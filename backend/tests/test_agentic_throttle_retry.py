"""
Tests for Bedrock-throttle detection and in-process retry/backoff on the
claude-sandbox call path (app/title_matching/agentic/runner.py).

THROTTLE SIGNATURE PROVENANCE (see runner.py's module-level docstring above
`_THROTTLE_RE` for the full writeup):

  * VERIFIED LIVE (2026-08-25): built and ran the real
    `entelligence-ai-claude-sandbox` docker image, pointed its
    ANTHROPIC_BEDROCK_BASE_URL (confirmed via `strings`/`grep` on the
    installed `claude` CLI binary to be the actual env var it reads,
    alongside CLAUDE_CODE_USE_BEDROCK=1 which docker-compose.yml already
    sets) at a local HTTP stub that unconditionally returns HTTP 429 with an
    AWS-shaped `{"__type":"ThrottlingException", ...}` body, then POSTed to
    the sandbox's real /run endpoint. Reproduced across two runs (25s and
    180s `timeout_seconds`): the CLI never surfaces the raw AWS exception
    text — it retries internally (observed up to attempt 10, with
    increasing delays) and emits one `{"type":"system","subtype":
    "api_retry","error_status":429,"error":"rate_limit",...}` stream-json
    line per attempt to stdout. In both captures the CLI's own retry budget
    outlasted the timeout before it gave up or succeeded, so the observed
    terminal shape was ALWAYS `exit_code: -1, timed_out: true, stderr: ""`
    with those api_retry lines sitting in stdout.
  * NOT verified live / INFERRED (from server.js's runClaude() plumbing and
    the CLI's documented stream-json event shapes already parsed elsewhere
    in this codebase — agentic_usage.py's `type:"result"` handling,
    result_parser.py's `type:"assistant"` handling): a throttle burst short
    enough to not hit the timeout could plausibly show as (a) a non-zero
    exit with throttle wording landing on stderr, or (b) a terminal
    `type:"result", is_error:true` event whose `result`/`error` text
    contains throttle wording, with `exit_code == 0`. The fixtures for these
    two shapes below are therefore inferred, not captured — flagged for a
    human reviewer to double check against real production throttle logs
    once AGENTIC_THROTTLE_MAX_RETRIES-driven retries start actually firing.

Also empirically demonstrated (and turned into a regression test below,
`test_looks_throttled_false_for_self_healed_internal_retry`): the CLI's
OWN internal retry sometimes succeeds transparently (a transient throttle
absorbed within its ~10-attempt budget, followed by exit_code 0 and a valid
final answer) while its stdout still contains one or more `api_retry`
lines with throttle wording. `_looks_throttled` must return False for that
case — the call genuinely succeeded — which is why `_error_text` explicitly
excludes `system`/`api_retry` events from what it scans.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.title_matching.agentic import (
    AgenticThrottleError,
    AgenticTimeoutError,
)
from app.title_matching.agentic import runner as runner_mod


# ---------------------------------------------------------------------------
# Fixture bodies (dicts shaped exactly like claude-sandbox's /run response)
# ---------------------------------------------------------------------------

def _api_retry_line(attempt: int) -> str:
    return json.dumps({
        "type": "system",
        "subtype": "api_retry",
        "attempt": attempt,
        "max_retries": 10,
        "retry_delay_ms": 600 * attempt,
        "error_status": 429,
        "error": "rate_limit",
        "session_id": "test-session",
    })


def _assistant_text_line(text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    })


def _result_line(*, is_error: bool, subtype: str, result: str, cost: float = 0.01) -> str:
    return json.dumps({
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result,
        "total_cost_usd": cost,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


# EMPIRICALLY CAPTURED shape: sustained throttle -> CLI's own retries outlast
# our timeout -> the sandbox wrapper SIGKILLs -> timed_out=True. Throttle
# wording (api_retry lines) is present in stdout, but timed_out must win.
TIMED_OUT_WITH_THROTTLE_WORDING_BODY = {
    "stdout": "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        _api_retry_line(1),
        _api_retry_line(2),
    ]),
    "stderr": "",
    "exit_code": -1,
    "timed_out": True,
    "serper_calls": [],
}

# INFERRED shape (a): non-zero exit, throttle wording lands on stderr.
THROTTLED_STDERR_BODY = {
    "stdout": "",
    "stderr": "Error: ThrottlingException: Too many requests, please wait before trying again.",
    "exit_code": 1,
    "timed_out": False,
    "serper_calls": [{"attempt": "throttled"}],
}

# INFERRED shape (b): exit_code 0, but a terminal stream-json result event
# reports the failure as throttling (is_error=true, error-ish subtype).
THROTTLED_TERMINAL_EVENT_BODY = {
    "stdout": "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        _api_retry_line(1),  # informational -- must NOT be what trips detection
        _result_line(
            is_error=True,
            subtype="error_during_execution",
            result="Request failed: rate limit exceeded (429).",
        ),
    ]),
    "stderr": "",
    "exit_code": 0,
    "timed_out": False,
    "serper_calls": [{"attempt": "throttled"}],
}

# A genuinely successful body whose ASSISTANT TEXT deliberately mentions
# "rate limit" and "429" in ordinary prose (e.g. discussing a franchise
# entry / release-date trivia) -- must NOT be misdetected as a throttle.
# This is the core regression proof that detection is structural (parses
# error-typed events), not a blind substring search over all of stdout.
SUCCESS_WITH_INNOCENT_WORDING_BODY = {
    "stdout": "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        _assistant_text_line(
            "The '429 Rate Limit' theory is just internet trivia about a "
            "different film entirely -- not relevant here."
        ),
        _assistant_text_line('{"suggested_movie_id": 42, "confidence": 0.9}'),
        _result_line(is_error=False, subtype="success", result='{"suggested_movie_id": 42}'),
    ]),
    "stderr": "",
    "exit_code": 0,
    "timed_out": False,
    "serper_calls": [{"attempt": "success"}],
}

# The CLI's own internal retry self-heals: transient api_retry lines
# (genuine throttle wording) followed by a clean success -- must NOT be
# misdetected as a throttle either, since the call actually succeeded.
SUCCESS_AFTER_SELF_HEALED_RETRY_BODY = {
    "stdout": "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        _api_retry_line(1),
        _api_retry_line(2),
        _result_line(is_error=False, subtype="success", result='{"suggested_movie_id": 42}'),
    ]),
    "stderr": "",
    "exit_code": 0,
    "timed_out": False,
    "serper_calls": [{"attempt": "self-healed"}],
}

PLAIN_SUCCESS_BODY = {
    "stdout": _result_line(is_error=False, subtype="success", result='{"suggested_movie_id": 1}'),
    "stderr": "",
    "exit_code": 0,
    "timed_out": False,
    "serper_calls": [{"attempt": "plain-success"}],
}


# ---------------------------------------------------------------------------
# 1 & 2: _looks_throttled — true positives, and the critical false-positive
#        regression (structural detection, not substring search)
# ---------------------------------------------------------------------------

def test_looks_throttled_true_for_stderr_wording():
    body = THROTTLED_STDERR_BODY
    assert runner_mod._looks_throttled(body["stdout"], body["stderr"]) is True


def test_looks_throttled_true_for_terminal_error_event():
    body = THROTTLED_TERMINAL_EVENT_BODY
    assert runner_mod._looks_throttled(body["stdout"], body["stderr"]) is True


def test_looks_throttled_false_for_innocent_prose_mentioning_429_and_rate_limit():
    """The regression test: assistant text containing "rate limit"/"429" in
    ordinary prose must NOT trip detection."""
    body = SUCCESS_WITH_INNOCENT_WORDING_BODY
    assert runner_mod._looks_throttled(body["stdout"], body["stderr"]) is False


def test_looks_throttled_false_for_self_healed_internal_retry():
    """A CLI-internal retry that ultimately succeeds must not be treated as
    a throttle, even though its stdout contains real throttle wording in
    the (informational, non-terminal) api_retry events."""
    body = SUCCESS_AFTER_SELF_HEALED_RETRY_BODY
    assert runner_mod._looks_throttled(body["stdout"], body["stderr"]) is False


def test_looks_throttled_false_for_plain_success():
    body = PLAIN_SUCCESS_BODY
    assert runner_mod._looks_throttled(body["stdout"], body["stderr"]) is False


# ---------------------------------------------------------------------------
# 6: timed_out must never be classified as throttle, even with throttle
#    wording present in stdout
# ---------------------------------------------------------------------------

def test_timed_out_raises_timeout_not_throttle_even_with_throttle_wording():
    with patch.object(runner_mod, "_post_sandbox", return_value=TIMED_OUT_WITH_THROTTLE_WORDING_BODY):
        with pytest.raises(AgenticTimeoutError):
            runner_mod._call_sandbox("prompt", "")


# ---------------------------------------------------------------------------
# 3: throttled(fast-fail) -> success respects the backoff sleep
# ---------------------------------------------------------------------------

def test_throttle_then_success_sleeps_expected_backoff_then_returns():
    sequence = [THROTTLED_STDERR_BODY, PLAIN_SUCCESS_BODY]
    # monotonic() is called twice per attempt (start, then elapsed-diff).
    # Attempt 0: elapsed=5s (well under the 45s fast-fail threshold at the
    # default AGENTIC_TIMEOUT_SECONDS=90). Attempt 1 succeeds; its elapsed
    # value is irrelevant.
    monotonic_values = [0.0, 5.0, 5.0, 6.0]
    sleep_calls = []

    with patch.object(runner_mod, "_post_sandbox", side_effect=sequence), \
         patch.object(runner_mod.time, "monotonic", side_effect=monotonic_values), \
         patch.object(runner_mod.time, "sleep", side_effect=lambda s: sleep_calls.append(s)), \
         patch.object(runner_mod.random, "uniform", return_value=1.0):
        stdout = runner_mod._call_sandbox("prompt", "")

    assert stdout == PLAIN_SUCCESS_BODY["stdout"]
    # backoff = AGENTIC_THROTTLE_BACKOFF_BASE_SECONDS * 2**0 * 1.0 (jitter
    # pinned to 1.0 above) = the configured base itself.
    from app.config import settings
    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(settings.AGENTIC_THROTTLE_BACKOFF_BASE_SECONDS)


# ---------------------------------------------------------------------------
# 4: retries exhausted -> raises AgenticThrottleError
# ---------------------------------------------------------------------------

def test_throttle_retries_exhausted_raises(monkeypatch):
    monkeypatch.setattr("app.config.settings.AGENTIC_THROTTLE_MAX_RETRIES", 1)
    sequence = [THROTTLED_STDERR_BODY, THROTTLED_STDERR_BODY]
    # Both attempts fast-fail (elapsed=5s each).
    monotonic_values = [0.0, 5.0, 5.0, 10.0]

    with patch.object(runner_mod, "_post_sandbox", side_effect=sequence), \
         patch.object(runner_mod.time, "monotonic", side_effect=monotonic_values), \
         patch.object(runner_mod.time, "sleep"):
        with pytest.raises(AgenticThrottleError):
            runner_mod._call_sandbox("prompt", "")


# ---------------------------------------------------------------------------
# 5: a slow failure (elapsed >= half the timeout) skips the in-process
#    retry entirely -- raises immediately, no sleep call
# ---------------------------------------------------------------------------

def test_slow_throttle_failure_skips_retry_no_sleep(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_THROTTLE_MAX_RETRIES", 1)
    # elapsed = 50s >= 90/2 = 45s -> NOT a fast fail.
    monotonic_values = [0.0, 50.0]
    sleep_calls = []

    with patch.object(runner_mod, "_post_sandbox", return_value=THROTTLED_STDERR_BODY), \
         patch.object(runner_mod.time, "monotonic", side_effect=monotonic_values), \
         patch.object(runner_mod.time, "sleep", side_effect=lambda s: sleep_calls.append(s)):
        with pytest.raises(AgenticThrottleError):
            runner_mod._call_sandbox("prompt", "")

    assert sleep_calls == []


# ---------------------------------------------------------------------------
# 7: last_serper_calls reflects only the successful attempt's data after a
#    throttle-then-success sequence -- no merge/leak across attempts
# ---------------------------------------------------------------------------

def test_last_serper_calls_reflects_only_successful_attempt():
    sequence = [THROTTLED_STDERR_BODY, PLAIN_SUCCESS_BODY]
    monotonic_values = [0.0, 5.0, 5.0, 6.0]

    with patch.object(runner_mod, "_post_sandbox", side_effect=sequence), \
         patch.object(runner_mod.time, "monotonic", side_effect=monotonic_values), \
         patch.object(runner_mod.time, "sleep"):
        runner_mod._call_sandbox("prompt", "")

    assert runner_mod._call_sandbox.last_serper_calls == PLAIN_SUCCESS_BODY["serper_calls"]
    assert runner_mod._call_sandbox.last_serper_calls != THROTTLED_STDERR_BODY["serper_calls"]

"""
Celery worker entrypoint used by tests/test_batch_chord_live.py AND
tests/test_batch_window_live.py.

It is launched as a subprocess:

    celery -A tests._batch_worker_bootstrap worker --pool solo -Q agentic ...
    (test_batch_window_live.py launches it with --pool=threads and a higher
    --concurrency instead, to exercise real overlapping row execution.)

Before exposing the Celery app, it patches
``app.title_matching.agentic.runner.run_agentic_match`` with a deterministic
fake so the worker never spawns the real claude-sandbox subprocess. The fake
simulates a genuine "retry then succeed" path for exactly one target row: it
raises ``AgenticTimeoutError`` on that row's FIRST invocation and returns a
successful match on the SECOND, using a Redis INCR counter (shared, survives
across the two task attempts in the worker process) keyed by row title.

It also tracks a concurrency high-water mark (Phase 5 —
test_batch_window_live.py's concurrency-cap check): every invocation
increments ``batchtest:concurrency:current``, atomically ratchets
``batchtest:concurrency:max`` up via a small Lua script if this is a new
high, sleeps ``BATCH_TEST_ROW_WORK_SECONDS`` to create a genuine overlap
window between concurrent calls, then decrements on the way out. This is
test-only instrumentation, not the mechanism under test — the actual
concurrency cap is enforced by ``sandbox_semaphore`` + Celery worker
``--concurrency``, both real and unpatched here.

DATABASE_URL / REDIS_URL are taken from the environment the test sets, so the
worker shares the same SQLite file DB and Redis broker/backend as the test.
"""

from __future__ import annotations

import os
import time

# Env (DATABASE_URL / REDIS_URL) is already set by the parent test process and
# inherited here; app.config.settings reads it at import time below.

from app.title_matching.types import TitleMatchResult  # noqa: E402
from app.title_matching.agentic import AgenticTimeoutError  # noqa: E402

# Row titles the test uses (kept in sync with test_batch_chord_live.py).
RETRY_TITLE = os.environ.get("BATCH_TEST_RETRY_TITLE", "Retry Once Then Ok")
RETRY_MOVIE_ID = int(os.environ.get("BATCH_TEST_RETRY_MOVIE_ID", "7777"))
MATCHED_MOVIE_ID = int(os.environ.get("BATCH_TEST_MATCHED_MOVIE_ID", "4242"))

# Phase 5 (test_batch_window_live.py) -- concurrency high-water mark tracking.
_CONCUR_CURRENT_KEY = "batchtest:concurrency:current"
_CONCUR_MAX_KEY = "batchtest:concurrency:max"
_ROW_WORK_SECONDS = float(os.environ.get("BATCH_TEST_ROW_WORK_SECONDS", "0.0"))

_RATCHET_MAX_LUA = """
local cur = tonumber(ARGV[1])
local max = tonumber(redis.call('GET', KEYS[1]) or '0')
if cur > max then
    redis.call('SET', KEYS[1], cur)
    return cur
end
return max
"""


def _counter_client():
    import redis

    from app.config import settings

    return redis.Redis.from_url(settings.REDIS_URL)


class _ConcurrencyTracker:
    """Context manager: bumps the current/high-water-mark Redis counters on
    enter, decrements on exit. See the module docstring's Phase 5 section."""

    def __enter__(self):
        client = _counter_client()
        self._client = client
        current = client.incr(_CONCUR_CURRENT_KEY)
        try:
            client.eval(_RATCHET_MAX_LUA, 1, _CONCUR_MAX_KEY, current)
        except Exception:  # noqa: BLE001 - instrumentation must never fail the row
            pass
        if _ROW_WORK_SECONDS > 0:
            time.sleep(_ROW_WORK_SECONDS)
        return self

    def __exit__(self, *exc_info):
        try:
            self._client.decr(_CONCUR_CURRENT_KEY)
        except Exception:  # noqa: BLE001
            pass
        return False


def _fake_run_agentic_match(title, show_date, theater, ticketing_url, use_poster_vision, market="domestic", country=None, usage_ctx=None):
    """Deterministic stand-in for the real sandbox runner.

    * ``RETRY_TITLE`` -> AgenticTimeoutError on first call, success on retry.
    * a title containing "nomatch" -> resolves to id 0 (no match).
    * anything else -> resolves to MATCHED_MOVIE_ID (present in MovieMaster).
    """
    assert theater is None  # batch path always passes None

    with _ConcurrencyTracker():
        if title == RETRY_TITLE:
            client = _counter_client()
            attempt = client.incr(f"batchtest:calls:{title}")
            if attempt == 1:
                raise AgenticTimeoutError("synthetic first-attempt timeout")
            return TitleMatchResult(
                suggested_movie_id=RETRY_MOVIE_ID,
                suggested_movie_title="Retried To Success",
                canonical_movie_id=RETRY_MOVIE_ID,
                confidence=0.91,
                decision="AUTO_ACCEPT",
                reasoning="succeeded on retry",
                evidence={},
            )

        if "nomatch" in title.lower():
            return TitleMatchResult(
                suggested_movie_id=0,
                suggested_movie_title="",
                canonical_movie_id=0,
                confidence=0.05,
                decision="REVIEW",
                reasoning="no candidate",
                evidence={},
            )

        return TitleMatchResult(
            suggested_movie_id=MATCHED_MOVIE_ID,
            suggested_movie_title="Matched Movie",
            canonical_movie_id=MATCHED_MOVIE_ID,
            confidence=0.95,
            decision="AUTO_ACCEPT",
            reasoning="clean match",
            evidence={},
        )


def _install_patch():
    import app.title_matching.agentic.runner as runner_mod

    runner_mod.run_agentic_match = _fake_run_agentic_match


# Patch at import time (solo pool => single process => this is enough).
_install_patch()

# Belt-and-suspenders: re-apply in each worker process if a prefork pool is
# ever used for this bootstrap.
from celery.signals import worker_process_init  # noqa: E402


@worker_process_init.connect
def _reapply_patch(**_kwargs):  # pragma: no cover - only fires under prefork
    _install_patch()


# The Celery app object celery's `-A` flag looks for.
from app.celery_app import celery  # noqa: E402

# The per-row task uses ``self.retry(exc=...)`` with no explicit countdown, so
# it would otherwise inherit Celery's 180s ``default_retry_delay`` and make the
# retry-then-succeed test hang. Collapse the delay to 0 for the test worker so
# the retried chord member re-runs immediately. (Test-only shim; does not touch
# the production task definition.)
from app.tasks.agentic_match_task import agentic_batch_row  # noqa: E402

agentic_batch_row.default_retry_delay = 0


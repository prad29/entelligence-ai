from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import random
import re
import time
import urllib.parse
import urllib.request
from typing import Optional
from urllib.error import URLError

from app.config import settings
from app.observability.agentic_usage import extract_agentic_usage
from app.observability.constants import (
    CALLER_PORTAL,
    PATH_AGENTIC_CLI,
    STATUS_FAILURE,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    TASK_DOMESTIC_MAPPING,
    TASK_INTL_MAPPING,
)
from app.observability.context import LlmCallContext
from app.observability.llm_logging import log_llm_call
from app.observability.serp_logging import log_serper_calls
from app.title_matching.types import TitleMatchResult
from app.title_matching.agentic import (
    AgenticConfigError,
    AgenticSubprocessError,
    AgenticThrottleError,
    AgenticTimeoutError,
)
from app.title_matching.agentic.prompt_builder import build_prompt
from app.title_matching.agentic.result_parser import parse_agent_output
from app.title_matching.normalizer import has_conflicting_ordinal, normalize_title
from app.title_matching.semantic_index import get_embedding

logger = logging.getLogger(__name__)


def _default_usage_ctx(market: str, country: Optional[str]) -> LlmCallContext:
    """Attribution for a caller that didn't supply one — the portal's
    single-match path (app/title_matching/engine.py), which has no job to
    attribute to."""
    return LlmCallContext(
        task_type=TASK_DOMESTIC_MAPPING if market == "domestic" else TASK_INTL_MAPPING,
        call_path=PATH_AGENTIC_CLI,
        caller_type=CALLER_PORTAL,
        market=market,
        country=country,
    )


def _log_sandbox_call(
    ctx: LlmCallContext,
    stdout: str,
    started: float,
    *,
    retry_count: int,
    decision: Optional[str] = None,
    status: str = STATUS_SUCCESS,
    error_type: Optional[str] = None,
) -> None:
    """Write one LlmCallLog row for one sandbox invocation. Never raises.

    Called once per _call_sandbox(), including the parse-failure retry: that
    retry is a second real CLI invocation billing real tokens, so it gets its
    own row (retry_count=1) rather than being folded into the first.
    """
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        parsed = extract_agentic_usage(stdout)
        log_llm_call(
            ctx,
            model_id=settings.AGENTIC_CLAUDE_MODEL,
            usage=parsed.usage,
            latency_ms=latency_ms,
            status=status,
            error_type=error_type,
            retry_count=retry_count,
            decision=decision,
            # The CLI's own total_cost_usd wins when present; None falls back
            # to litellm token pricing inside log_llm_call.
            cost_usd=parsed.cost_usd,
        )
        serper_calls = getattr(_call_sandbox, "last_serper_calls", None)
        if serper_calls:
            log_serper_calls(
                serper_calls,
                job_id=ctx.job_id,
                job_type=ctx.job_type,
                task_type=ctx.task_type,
                market=ctx.market,
            )
    except Exception as log_exc:  # noqa: BLE001 — spec §7
        logger.warning("agentic_usage_log_failed error=%s", log_exc)


def _status_for(exc: BaseException) -> str:
    return STATUS_TIMEOUT if isinstance(exc, AgenticTimeoutError) else STATUS_FAILURE


def run_agentic_match(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
    market: str = "domestic",
    country: Optional[str] = None,
    usage_ctx: Optional[LlmCallContext] = None,
) -> TitleMatchResult:
    _check_sandbox_reachable()

    # Pre-fetch DB candidates before calling the sandbox so the agent
    # never needs to call localhost (the sidecar can't reach compose services).
    db_candidates = _fetch_db_candidates(title, market=market, country=country)
    vespa_candidates = _fetch_vespa_candidates(title, market=market)

    if use_poster_vision:
        _annotate_poster_availability(db_candidates)

    prompt = build_prompt(
        title, show_date, theater, ticketing_url,
        db_candidates=db_candidates,
        vespa_candidates=vespa_candidates,
        use_poster_vision=use_poster_vision,
        market=market,
        country=country,
    )

    # Built-in WebSearch is unavailable under Bedrock (CLAUDE_CODE_USE_BEDROCK=1
    # drops it from the tool list entirely, regardless of --tools/--allowedTools).
    # web_search/web_fetch are provided instead by the movieweb MCP server that
    # claude-sandbox always connects (see server.js) — no tool name needed here
    # for those; only built-in WebFetch is requested, when a ticketing page or
    # poster image needs to be fetched directly.
    tools = "WebFetch" if (use_poster_vision or ticketing_url) else ""

    logger.info(
        "agentic_match_start title=%r model=%s bedrock=%s db_hits=%d vespa_hits=%d poster_vision=%s",
        title, settings.AGENTIC_CLAUDE_MODEL, settings.AGENTIC_USE_BEDROCK,
        len(db_candidates), len(vespa_candidates), use_poster_vision,
    )

    # Attribution for every sandbox call below. A caller-supplied context wins,
    # but market/country are backfilled from this function's own arguments —
    # the runner is the single source of truth for those, so a call site can
    # never drift them out of sync with the prompt it actually built.
    ctx = usage_ctx or _default_usage_ctx(market, country)
    if usage_ctx is not None:
        ctx = dataclasses.replace(
            ctx,
            market=ctx.market or market,
            country=ctx.country or country,
        )

    started = time.monotonic()
    try:
        stdout = _call_sandbox(prompt, tools)
    except BaseException as exc:
        _log_sandbox_call(
            ctx, "", started,
            retry_count=0,
            status=_status_for(exc),
            error_type=type(exc).__name__,
        )
        raise

    logger.debug("agentic_match_raw_output length=%d", len(stdout))
    result = parse_agent_output(stdout)
    _log_sandbox_call(ctx, stdout, started, retry_count=0, decision=result.decision)

    # Retry once if parse produced a fallback (model stopped before outputting JSON)
    if result.suggested_movie_id == 0 and result.evidence.get("parse_error"):
        logger.warning(
            "agentic_parse_failed_retrying title=%r parse_error=%r",
            title, result.evidence["parse_error"][:100],
        )
        retry_prompt = (
            f"{prompt}\n\n"
            "IMPORTANT: Your previous response did not contain valid JSON output. "
            "You MUST respond with ONLY the JSON object and nothing else. "
            "No explanations, no tool calls — just the raw JSON."
        )
        retry_started = time.monotonic()
        try:
            stdout2 = _call_sandbox(retry_prompt, tools)
            result = parse_agent_output(stdout2)
            _log_sandbox_call(
                ctx, stdout2, retry_started, retry_count=1, decision=result.decision
            )
            logger.info("agentic_retry_success title=%r id=%d", title, result.suggested_movie_id)
        except AgenticThrottleError as retry_exc:
            # A genuine throttle here has ALREADY been through _call_sandbox's
            # own in-process fast-fail retry (and its backoff sleep) before
            # raising — swallowing it into the generic `except Exception`
            # branch below would silently keep this function's ORIGINAL
            # parse-failure fallback result (a low-confidence REVIEW) and
            # hide the real cause from the caller entirely. The Celery row
            # task needs to see AgenticThrottleError specifically so it can
            # back off and re-queue the whole row (releasing the sandbox
            # semaphore slot) instead of accepting a degraded result — so
            # this must propagate, not be logged-and-ignored like an ordinary
            # parse-retry failure.
            _log_sandbox_call(
                ctx, "", retry_started,
                retry_count=1,
                status=_status_for(retry_exc),
                error_type=type(retry_exc).__name__,
            )
            logger.warning("agentic_retry_throttled title=%r error=%s", title, retry_exc)
            raise
        except Exception as retry_exc:
            _log_sandbox_call(
                ctx, "", retry_started,
                retry_count=1,
                status=_status_for(retry_exc),
                error_type=type(retry_exc).__name__,
            )
            logger.warning("agentic_retry_failed title=%r error=%s", title, retry_exc)

    # If Claude identified the movie but couldn't match a DB id (id=0),
    # do a second DB lookup using Claude's identified movie_title.
    # This handles cases like "Graveyard Shift: CANNIBAL HOLOCAUST (New Restoration)"
    # where the pre-fetch found nothing but Claude correctly identified the film.
    if result.suggested_movie_id == 0 and result.suggested_movie_title and result.suggested_movie_title != "Unknown":
        logger.info(
            "agentic_post_lookup title=%r claude_identified=%r",
            title, result.suggested_movie_title,
        )
        query_ordinal = (
            normalize_title(title).ordinal
            or normalize_title(result.suggested_movie_title).ordinal
        )
        post_hits = _post_lookup_search(
            result.suggested_movie_title, market, country, query_ordinal,
        )

        # International-only fallback: the agent may have guessed the "wrong"
        # one of the English/localized title pair for whichever string
        # MovieMasterIntl.movie_title actually stores for this row (see
        # INTL_SEMANTIC_REGRESSION_ANALYSIS.md — the intl prompt now asks for
        # the English title first, but the agent may only be confident in the
        # localized one, or vice versa). One bounded second attempt, not a loop.
        if not post_hits and result.alternate_movie_title:
            logger.info(
                "agentic_post_lookup_alternate title=%r claude_alternate=%r",
                title, result.alternate_movie_title,
            )
            post_hits = _post_lookup_search(
                result.alternate_movie_title, market, country, query_ordinal,
            )

        if post_hits:
            db_candidates = post_hits  # refresh for cover_image lookup below
            best = post_hits[0]
            result.suggested_movie_id = best["id"]
            result.canonical_movie_id = best["id"]
            # Always sync the displayed title to the row that actually got
            # matched — not just when it started empty. Without this, a
            # successful *alternate*-title fallback hit still displays
            # Claude's original (wrong) primary guess as suggested_movie_title,
            # even though suggested_movie_id/canonical_movie_id correctly
            # point at the alternate's row. Confirmed live during batch
            # testing: "Little Creatures" resolved to the correct DB row
            # (id 156949, "Pequenas Criaturas") via the alternate title, but
            # the batch output kept displaying "Little Creatures" as the
            # mapped title, making an otherwise-correct match look wrong in
            # any downstream title-string comparison.
            result.suggested_movie_title = best["movie_title"]
            logger.info(
                "agentic_post_lookup_hit id=%d title=%r",
                best["id"], best["movie_title"],
            )

    # Attach cover_image from DB candidates (original pre-fetch or post-lookup)
    if result.suggested_movie_id:
        cover_lookup = {
            c["id"]: c.get("cover_image", "")
            for c in db_candidates
            if c.get("id")
        }
        img = cover_lookup.get(result.suggested_movie_id, "")
        if img and "noimage" not in img.lower():
            result.cover_image = img

    return result


# ---------------------------------------------------------------------------
# Bedrock-throttle detection
# ---------------------------------------------------------------------------
#
# THROTTLE SIGNATURE — WHAT WAS VERIFIED EMPIRICALLY VS. INFERRED
# ================================================================
# Verified live (2026-08-25): built the actual `entelligence-ai-claude-sandbox`
# image, pointed it at a local HTTP stub bound at ANTHROPIC_BEDROCK_BASE_URL
# (confirmed via `grep` on the installed `claude` CLI binary to be the real
# env var name it reads, alongside CLAUDE_CODE_USE_BEDROCK=1, which
# docker-compose.yml already sets) that unconditionally returns HTTP 429 with
# an AWS-shaped body (`{"__type":"ThrottlingException","message":"..."}`),
# then POSTed to the sandbox's real /run endpoint.
#
# Result, reproduced across two separate runs (25s and 180s
# timeout_seconds): the `claude` CLI does NOT surface the raw AWS
# ThrottlingException text at all. It has its OWN internal retry loop
# (up to 10 attempts, exponential backoff observed at roughly
# 0.5s/1s/2s/4s/9s/17s/36s/36s/38s/34s between attempts) and emits one
# `stream-json` line per attempt to STDOUT shaped like:
#   {"type":"system","subtype":"api_retry","attempt":1,"max_retries":10,
#    "retry_delay_ms":612,"error_status":429,"error":"rate_limit",
#    "session_id":"...","uuid":"..."}
# In EVERY run captured, the CLI's own backoff schedule outlasted the
# sandbox wrapper's timeout (server.js's runClaude() SIGKILLs the subprocess
# once `timeoutMs` elapses) well before the CLI exhausted its 10-attempt
# budget or produced a final answer — so the observed outcome was always
# `exit_code: -1, timed_out: true, stderr: ""`, with the api_retry lines
# sitting in stdout. This directly confirms the plan's warning: a throttle
# that persists for the CLI's own retry window manifests as a TIMEOUT, not a
# distinguishable non-zero exit — and must never be reclassified as a
# throttle just because throttle wording is present (see the `timed_out`
# check below, which is checked and dispatched on BEFORE any throttle
# detection, unconditionally).
#
# NOT verified live (inferred from server.js's runClaude() + the CLI's
# documented stream-json event shapes, which this codebase already parses
# elsewhere — see agentic_usage.py's `type: "result"` handling and
# result_parser.py's `type: "assistant"` handling): a throttle burst SHORT
# enough for the CLI to either (a) give up on its own before our timeout and
# report a non-zero exit / a terminal `type: "result", is_error: true` event
# with throttle wording in `result`/`error` fields, or (b) succeed
# transparently after 1-2 internal retries (exit_code 0, valid final
# answer) while its stdout still contains one or more `api_retry` lines.
# Case (b) is NOT a throttle from this module's point of view — the call
# genuinely succeeded — which is why `_error_text` below explicitly excludes
# `system`/`api_retry` events (informational, not terminal) from what it
# scans: including them would misclassify a call that self-healed via the
# CLI's own retry as throttled, and (being the least "fast fail" case, since
# elapsed time includes the internal retry delay) potentially discard a
# perfectly good result. Case (a)'s terminal-error-event shape is inferred,
# not observed — a human reviewer should treat `_looks_throttled`'s
# stdout-side detection as best-effort/defense-in-depth, and the `stderr`
# side (checked unconditionally, raw substring) as the higher-confidence
# path for whatever non-timeout throttle shape shows up in production.
_THROTTLE_RE = re.compile(
    r"throttl|too\s+many\s+requests|rate[\s_-]?limit|\b429\b|"
    r"service\s*unavailable|overloaded",
    re.IGNORECASE,
)

# An event's own `subtype` counts as "error-ish" if it contains one of these
# substrings, or exactly matches one of these words. Deliberately excludes
# "retry" ("api_retry" is informational, not terminal — see docstring above).
_ERROR_ISH_SUBTYPE_MARKERS = ("error", "abort", "max_turns", "denied")


def _error_text(stdout: str, stderr: str) -> str:
    """Build the text surface `_looks_throttled` searches.

    stderr is always included verbatim — subprocess/CLI-crash-level failures
    land there as plain text, not JSON.

    stdout is scanned line-by-line for parseable stream-json events; ONLY
    events that are structurally error/terminal-failure-shaped are included
    (an `is_error` flag, `type == "error"`, or an error-ish `subtype`) —
    ordinary `type: "assistant"` text/reasoning content is never included,
    because a movie title or the model's own reasoning could innocently
    contain "429" or "rate limit" (e.g. discussing a theatrical re-release
    date) and must not trip throttle detection. `system`/`api_retry` events
    are explicitly excluded too — see the module-level docstring above:
    those are the CLI transparently handling a transient 429 internally and
    can precede a fully successful result.
    """
    parts = [stderr or ""]
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        subtype = event.get("subtype")

        if event_type == "system" and subtype == "api_retry":
            continue  # informational CLI-internal retry telemetry, not terminal

        is_error = bool(event.get("is_error"))
        error_ish_subtype = isinstance(subtype, str) and any(
            marker in subtype.lower() for marker in _ERROR_ISH_SUBTYPE_MARKERS
        )
        if is_error or event_type == "error" or error_ish_subtype:
            parts.append(line)

    return "\n".join(parts)


def _looks_throttled(stdout: str, stderr: str) -> bool:
    """True if the error-shaped surface of this response reads as a Bedrock
    throttle (429 / ThrottlingException / rate limit wording)."""
    return bool(_THROTTLE_RE.search(_error_text(stdout, stderr)))


def _throttle_backoff_seconds(attempt: int) -> float:
    """Exponential backoff (seconds) before an in-process retry of a
    throttled sandbox call, with +/-50% jitter so multiple rows throttled by
    the same Bedrock quota exhaustion don't all wake up and retry in the same
    instant — same thundering-herd rationale as sandbox_semaphore.py's own
    jittered acquire-retry loop."""
    base = settings.AGENTIC_THROTTLE_BACKOFF_BASE_SECONDS
    return base * (2**attempt) * random.uniform(0.5, 1.5)


def _post_sandbox(prompt: str, tools: str) -> dict:
    """POST to the claude-sandbox sidecar and return the parsed response
    body dict (stdout/stderr/exit_code/timed_out/serper_calls). Raw HTTP-call
    plumbing only — no throttle/error interpretation lives here; that's
    `_call_sandbox`'s job, so it can retry this call in a loop.
    """
    payload = json.dumps({
        "prompt": prompt,
        "model": settings.AGENTIC_CLAUDE_MODEL,
        "tools": tools,
        "timeout_seconds": settings.AGENTIC_TIMEOUT_SECONDS,
    }).encode()

    url = f"{settings.CLAUDE_SANDBOX_URL.rstrip('/')}/run"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=settings.AGENTIC_TIMEOUT_SECONDS + 10) as resp:
            return json.loads(resp.read())
    except URLError as exc:
        raise AgenticConfigError(
            f"Claude sandbox unreachable at {url}. "
            "Ensure the claude-sandbox service is running and healthy. "
            f"Error: {exc}"
        )


def _call_sandbox(prompt: str, tools: str) -> str:
    """POST to the claude-sandbox sidecar and return raw stdout, retrying
    in-process on a detected Bedrock throttle.

    Side channel: also stashes the response's `serper_calls` list (spec §7 —
    the movieweb MCP server's web_search/web_fetch call log for this
    invocation) onto `_call_sandbox.last_serper_calls`, read by the caller
    right after this returns. This keeps the return type unchanged (still
    `str`) so existing callers/mocks (e.g. tests that patch this function to
    return a plain string) are unaffected. Reset per ATTEMPT (not just once
    per call) so a retried attempt's serper_calls can never merge with or
    leak from a prior throttled attempt's partial data.

    Retry policy (up to `settings.AGENTIC_THROTTLE_MAX_RETRIES + 1` total
    attempts):
      - `timed_out: true` NEVER counts as a throttle, even if throttle
        wording happens to appear in stdout/stderr (see the module docstring
        above — in every empirical capture, sustained throttling manifested
        as exactly this: the CLI's own internal retry budget outlasting our
        timeout). Raises AgenticTimeoutError immediately, no retry here.
      - A throttled response gets ONE more in-process attempt only if the
        failed attempt was a "fast fail" (elapsed less than half of
        AGENTIC_TIMEOUT_SECONDS) — a slow failure means the CLI already
        burned its own internal retry budget on this attempt, so retrying
        immediately from here would just repeat that same expensive dance.
        Otherwise (or once retries are exhausted) raises
        AgenticThrottleError so the caller can back off at the Celery level
        instead (releasing the sandbox semaphore slot for the whole wait).
      - A non-throttled non-zero exit raises AgenticSubprocessError, as
        before.
      - Otherwise returns stdout, as before.
    """
    max_attempts = settings.AGENTIC_THROTTLE_MAX_RETRIES + 1

    for attempt in range(max_attempts):
        _call_sandbox.last_serper_calls = []

        attempt_started = time.monotonic()
        body = _post_sandbox(prompt, tools)
        elapsed = time.monotonic() - attempt_started

        exit_code = body.get("exit_code", -1)
        timed_out = body.get("timed_out", False)
        stderr = body.get("stderr", "")
        stdout = body.get("stdout", "")
        serper_calls = body.get("serper_calls", [])
        if isinstance(serper_calls, list):
            _call_sandbox.last_serper_calls = serper_calls

        if timed_out:
            raise AgenticTimeoutError(
                f"Agent timed out after {settings.AGENTIC_TIMEOUT_SECONDS}s for title. "
                "Increase AGENTIC_TIMEOUT_SECONDS or check claude-sandbox logs."
            )

        if _looks_throttled(stdout, stderr):
            fast_fail = elapsed < (settings.AGENTIC_TIMEOUT_SECONDS / 2)
            attempts_remaining = attempt < max_attempts - 1
            logger.warning(
                "agentic_sandbox_throttled attempt=%d/%d elapsed=%.1fs fast_fail=%s",
                attempt + 1, max_attempts, elapsed, fast_fail,
            )
            if attempts_remaining and fast_fail:
                time.sleep(_throttle_backoff_seconds(attempt))
                continue
            excerpt = stderr[:500] if stderr else (stdout[-500:] if stdout else "(no output)")
            raise AgenticThrottleError(
                f"Bedrock throttled the sandbox call (attempt {attempt + 1}/{max_attempts}, "
                f"elapsed {elapsed:.1f}s, fast_fail={fast_fail}). Excerpt: {excerpt}"
            )

        if exit_code != 0:
            excerpt = stderr[:500] if stderr else "(no stderr)"
            raise AgenticSubprocessError(
                f"Claude exited with code {exit_code}. "
                f"Check CLAUDE_CODE_USE_BEDROCK and AWS credentials. stderr: {excerpt}"
            )

        return stdout

    # Unreachable: the loop above always either returns or raises on every
    # iteration, including the last (attempts_remaining is False there).
    raise AgenticSubprocessError("agentic sandbox retry loop exited without a result")


def _check_sandbox_reachable() -> None:
    """Fail fast with a clear message if the sandbox sidecar isn't up."""
    url = f"{settings.CLAUDE_SANDBOX_URL.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
            if not data.get("claude_available"):
                raise AgenticConfigError(
                    "claude-sandbox is running but `claude` binary is not available inside it. "
                    "Rebuild the claude-sandbox image: docker compose build claude-sandbox"
                )
    except URLError as exc:
        raise AgenticConfigError(
            f"claude-sandbox not reachable at {settings.CLAUDE_SANDBOX_URL}. "
            "Start it with: docker compose up claude-sandbox. "
            f"Error: {exc}"
        )


def _post_lookup_search(
    claude_title: str,
    market: str,
    country: Optional[str],
    query_ordinal: Optional[int],
) -> list[dict]:
    """Re-search the DB using a title Claude identified after finding no
    pre-fetch candidate (movie_master_id=0). Shared by the primary
    suggested_movie_title attempt and the international alternate_movie_title
    fallback attempt in run_agentic_match — same query/filter logic either way.
    """
    # Strip parentheticals (e.g. "The Odyssey (L'Odyssée)" -> "The Odyssey")
    post_query = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", claude_title).strip(" -:")
    hits = _db_search(post_query or claude_title, market=market, country=country)

    # An ordinal is a hard constraint the agent may have already used to
    # reject a DB row (e.g. discarding a "Part 2" candidate for a "Part 5"
    # input). The trigram fallback in _db_search is permissive on spelling
    # but knows nothing about ordinals, so it can resurface exactly the row
    # the agent just rejected — filter those back out before trusting hits[0].
    if query_ordinal:
        hits = [h for h in hits if not has_conflicting_ordinal(h["movie_title"], query_ordinal)]

    return hits


def _fetch_db_candidates(
    title: str, market: str = "domestic", country: Optional[str] = None,
) -> list[dict]:
    """Best-effort keyword pre-fetch via direct DB query (avoids HTTP self-call deadlock).
    Claude does the real identification — this just gives it a head start."""
    try:
        bare = normalize_title(title).cleaned
        if ":" in bare:
            after = bare.split(":")[-1].strip()
            if after:
                bare = after
        bare = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", bare).strip(" -:")
        return _db_search(bare or title, market=market, country=country)
    except Exception as exc:
        logger.warning("db_candidate_fetch_failed title=%r error=%s", title, exc)
        return []


def _db_search(
    query: str, market: str = "domestic", country: Optional[str] = None,
) -> list[dict]:
    """Search Movie Master (or MovieMasterIntl, scoped by country) via direct DB query.

    Tries an ILIKE substring match first (fast, precise for exact/near-exact
    queries). Falls back to a pg_trgm similarity search (with unaccent) when
    ILIKE finds nothing — ILIKE containment is defeated by punctuation,
    accents, or word-order noise (e.g. "Oh Sukumari" vs the DB's "Oh..!
    Sukumari", or "DCI 2026 BIG LOUD AND LIVE" vs "DCI 2026: Big, Loud &
    Live"), which trigram similarity tolerates.

    For the international path, also tries `master_movie_title` (the English
    title MovieMasterIntl stores alongside the country-local `movie_title`)
    when the `movie_title` ILIKE finds nothing — a defense-in-depth net for
    the case where a ticketing page shows the English title for a market
    where the DB only indexes the country-local one.
    """
    try:
        from sqlmodel import Session, select
        from app.database import engine as db_engine

        with Session(db_engine) as session:
            if market == "international":
                from app.models import MovieMasterIntl as Model
                stmt = select(Model).where(Model.movie_title.ilike(f"%{query}%"))
                if country:
                    stmt = stmt.where(Model.country == country)
                stmt = stmt.limit(20)
            else:
                from app.models import MovieMaster as Model
                stmt = (
                    select(Model)
                    .where(Model.movie_title.ilike(f"%{query}%"))
                    .limit(20)
                )
            rows = session.exec(stmt).all()

            if not rows and market == "international":
                master_stmt = select(Model).where(Model.master_movie_title.ilike(f"%{query}%"))
                if country:
                    master_stmt = master_stmt.where(Model.country == country)
                rows = session.exec(master_stmt.limit(20)).all()

            if rows:
                # Exact (case-insensitive) title match first, then shortest title —
                # avoids picking an edition variant (e.g. "...: An IMAX 3D Experience")
                # over the plain canonical title when both match the ILIKE query.
                rows = sorted(
                    rows,
                    key=lambda r: (r.movie_title.lower() != query.lower(), len(r.movie_title)),
                )
            else:
                rows = _trigram_search(session, Model, query, country=country if market == "international" else None)

            if market == "international":
                return [
                    {
                        "id": r.id,
                        "movie_title": r.movie_title,
                        "release_date": str(r.release_date) if r.release_date else None,
                        "cover_image": "",
                        "country": r.country,
                    }
                    for r in rows
                ]

            return [
                {
                    "id": r.id,
                    "movie_title": r.movie_title,
                    "release_date": str(r.release_date) if r.release_date else None,
                    "cover_image": r.cover_image or "",
                }
                for r in rows
            ]
    except Exception as exc:
        logger.warning("db_search_failed query=%r error=%s", query, exc)
        return []


def _trigram_search(session, master_model, query: str, country: Optional[str] = None) -> list:
    """pg_trgm + unaccent similarity fallback, ranked by similarity descending.

    Requires the pg_trgm/unaccent extensions and the trigram index added by
    migration f6a1b2c3d4e5. Returns [] (never raises) if unavailable so a
    missing migration degrades to "no fallback candidates" rather than
    failing the whole pre-fetch. When country is given (international path),
    scopes the fallback to that country too.
    """
    from sqlmodel import select
    from sqlalchemy import func

    try:
        unaccented_title = func.unaccent(master_model.movie_title)
        unaccented_query = func.unaccent(query)
        similarity = func.similarity(unaccented_title, unaccented_query)
        stmt = select(master_model).where(unaccented_title.op("%")(unaccented_query))
        if country:
            stmt = stmt.where(master_model.country == country)
        stmt = stmt.order_by(similarity.desc()).limit(20)
        return list(session.exec(stmt).all())
    except Exception as exc:
        logger.debug("trigram_search_failed query=%r error=%s", query, exc)
        return []


def _fetch_vespa_candidates(title: str, market: str = "domestic") -> list[dict]:
    """Hybrid semantic (ANN) + BM25 search against Vespa, scoped to the market's
    document type. Embeds the query title via Cohere Embed Multilingual v3 so
    cross-language titles (e.g. "Aguas Mortais" -> "Deep Water") can be found
    by vector similarity even when they share no keywords with the English
    master title. Falls back to BM25-only search if embedding is unavailable
    (Bedrock unreachable, etc.) rather than failing the whole pre-fetch."""
    schema = "movie_master_intl" if market == "international" else "movie_master"
    id_field = "movie_master_intl_id" if market == "international" else "movie_master_id"
    hits = 10
    try:
        embedding = get_embedding(title, settings)

        if embedding is not None:
            yql = (
                f"select * from sources {schema} "
                f"where ({{targetHits:{hits}}}nearestNeighbor(embedding,q_embedding)) "
                f"or userQuery()"
            )
            body_dict = {
                "yql": yql,
                "query": title,
                "ranking": "hybrid",
                "input.query(q_embedding)": embedding,
                "hits": hits,
            }
        else:
            body_dict = {
                "yql": f"select * from sources {schema} where userQuery()",
                "query": title,
                "ranking": "hybrid",
                "hits": hits,
            }

        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(
            "http://vespa:8080/search/",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            hits = data.get("root", {}).get("children", [])
            return [
                {
                    "id": h.get("fields", {}).get(id_field),
                    "movie_title": h.get("fields", {}).get("title"),
                    "release_date": h.get("fields", {}).get("release_date"),
                    "relevance": h.get("relevance"),
                }
                for h in hits
            ]
    except Exception as exc:
        logger.warning("vespa_candidate_fetch_failed title=%r error=%s", title, exc)
        return []


def _annotate_poster_availability(candidates: list[dict]) -> None:
    """Add has_poster=True/False to each candidate in-place."""
    for c in candidates:
        img = (c.get("cover_image") or "").strip()
        c["has_poster"] = bool(img) and "noimage" not in img.lower()


async def run_agentic_match_async(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
    market: str = "domestic",
    country: Optional[str] = None,
    usage_ctx: Optional[LlmCallContext] = None,
) -> TitleMatchResult:
    return await asyncio.to_thread(
        run_agentic_match, title, show_date, theater, ticketing_url, use_poster_vision,
        market, country, usage_ctx,
    )

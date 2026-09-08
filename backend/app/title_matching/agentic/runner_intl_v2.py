"""International v2's standalone orchestrator -- never calls
run_agentic_match, per PR #56's unfulfilled follow-up (b): "rebuild
international title matching as a fully separate module, not sharing
runner.py with domestic." Reuses runner.py's transport plumbing (sandbox
HTTP calls, the empirically-tuned throttle detection, usage-logging helpers)
directly, since duplicating ~150 lines of hard-won infra would be a bigger
risk than importing it -- but the *decision* logic (prompt, candidates,
guardrail, rerank, post-lookup confidence fix) is entirely local to this
module and the sibling *_intl_v2.py files it composes.

No `market` parameter -- domestic is unrepresentable through this function,
the same way runner_v2.py makes an international v2 call unrepresentable.
`country` is required in practice.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from app.config import settings
from app.observability.context import LlmCallContext
from app.title_matching.agentic import AgenticThrottleError
from app.title_matching.agentic.candidates_intl_v2 import (
    fetch_db_candidates_intl,
    fetch_vespa_candidates_intl,
    post_lookup_search_intl,
)
from app.title_matching.agentic.intl_guardrail_v2 import apply_country_guardrail, candidate_passes_intl
from app.title_matching.agentic.post_lookup_intl_v2 import apply_intl_v2_post_lookup_resolution
from app.title_matching.agentic.prompt_builder_intl_v2 import build_prompt_intl_v2
from app.title_matching.agentic.result_parser import parse_agent_output
from app.title_matching.agentic.runner import (
    _call_sandbox,
    _check_sandbox_reachable,
    _default_usage_ctx,
    _log_sandbox_call,
    _status_for,
)
from app.title_matching.agentic.sandbox_target import sandbox_url_for
from app.title_matching.normalizer import normalize_title
from app.title_matching.types import TitleMatchResult

logger = logging.getLogger(__name__)


def run_agentic_match_intl_v2(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    country: Optional[str] = None,
    use_poster_vision: bool = False,
    usage_ctx: Optional[LlmCallContext] = None,
) -> TitleMatchResult:
    if not country or not country.strip():
        raise ValueError("run_agentic_match_intl_v2 requires a non-blank country")

    sandbox_url = sandbox_url_for("international", variant="v2")
    _check_sandbox_reachable(sandbox_url=sandbox_url)

    db_candidates = fetch_db_candidates_intl(title, country=country)
    vespa_candidates = fetch_vespa_candidates_intl(title, country=country)

    prompt = build_prompt_intl_v2(
        title, show_date, theater, ticketing_url, country,
        db_candidates=db_candidates,
        vespa_candidates=vespa_candidates,
        use_poster_vision=use_poster_vision,
    )

    tools = "WebFetch" if (use_poster_vision or ticketing_url) else ""

    logger.info(
        "agentic_match_intl_v2_start title=%r country=%s model=%s db_hits=%d vespa_hits=%d",
        title, country, settings.AGENTIC_CLAUDE_MODEL, len(db_candidates), len(vespa_candidates),
    )

    ctx = usage_ctx or _default_usage_ctx("international", country, variant="v2")

    started = time.monotonic()
    try:
        stdout = _call_sandbox(prompt, tools, sandbox_url=sandbox_url)
    except BaseException as exc:
        _log_sandbox_call(
            ctx, "", started, retry_count=0, status=_status_for(exc), error_type=type(exc).__name__,
        )
        raise

    result = parse_agent_output(stdout)
    _log_sandbox_call(ctx, stdout, started, retry_count=0, decision=result.decision)

    # Retry once if parse produced a fallback (model stopped before outputting
    # JSON) -- mirrors run_agentic_match's identical retry block.
    if result.suggested_movie_id == 0 and result.evidence.get("parse_error"):
        logger.warning(
            "agentic_intl_v2_parse_failed_retrying title=%r parse_error=%r",
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
            stdout2 = _call_sandbox(retry_prompt, tools, sandbox_url=sandbox_url)
            result = parse_agent_output(stdout2)
            _log_sandbox_call(ctx, stdout2, retry_started, retry_count=1, decision=result.decision)
            logger.info("agentic_intl_v2_retry_success title=%r id=%d", title, result.suggested_movie_id)
        except AgenticThrottleError as retry_exc:
            # See run_agentic_match's identical branch: a Celery caller needs
            # to see this type specifically to back off/re-queue the row.
            _log_sandbox_call(
                ctx, "", retry_started, retry_count=1,
                status=_status_for(retry_exc), error_type=type(retry_exc).__name__,
            )
            logger.warning("agentic_intl_v2_retry_throttled title=%r error=%s", title, retry_exc)
            raise
        except Exception as retry_exc:
            _log_sandbox_call(
                ctx, "", retry_started, retry_count=1,
                status=_status_for(retry_exc), error_type=type(retry_exc).__name__,
            )
            logger.warning("agentic_intl_v2_retry_failed title=%r error=%s", title, retry_exc)

    query_ordinal = normalize_title(title).ordinal

    result = apply_country_guardrail(result, db_candidates, country, query_ordinal=query_ordinal)

    if settings.AGENTIC_INTL_V2_RERANK_ENABLED and (db_candidates or vespa_candidates):
        from app.title_matching.agentic.rerank_intl_v2 import verify_candidate_pick

        result = verify_candidate_pick(
            title, show_date, country, result, db_candidates, vespa_candidates, settings,
            job_id=ctx.job_id,
        )

    if result.suggested_movie_id == 0 and result.suggested_movie_title and result.suggested_movie_title != "Unknown":
        query_ordinal = query_ordinal or normalize_title(result.suggested_movie_title).ordinal

        post_hits: list[dict] = []
        resolved_via = None

        # Most specific guess first: the anniversary rule's dated-re-release
        # form of the title, when Claude set it.
        if result.rerelease_lookup_title:
            post_hits = post_lookup_search_intl(result.rerelease_lookup_title, country, query_ordinal)
            if post_hits:
                resolved_via = "rerelease_lookup_title"

        if not post_hits:
            post_hits = post_lookup_search_intl(result.suggested_movie_title, country, query_ordinal)
            if post_hits:
                resolved_via = "suggested_movie_title"

        if not post_hits and result.alternate_movie_title:
            post_hits = post_lookup_search_intl(result.alternate_movie_title, country, query_ordinal)
            if post_hits:
                resolved_via = "alternate_movie_title"

        # Close the loop the guardrail opened: without this, the permissive
        # trigram post-lookup could resurface exactly the row the guardrail
        # just rejected for a country mismatch.
        post_hits = [h for h in post_hits if candidate_passes_intl(h, country)]

        if post_hits:
            db_candidates = post_hits
            best = post_hits[0]
            result.suggested_movie_id = best["id"]
            result.canonical_movie_id = best["id"]
            result.suggested_movie_title = best["movie_title"]
            result = apply_intl_v2_post_lookup_resolution(result, best, resolved_via=resolved_via)
            logger.info(
                "agentic_intl_v2_post_lookup_hit id=%d title=%r resolved_via=%s",
                best["id"], best["movie_title"], resolved_via,
            )

    return result


async def run_agentic_match_intl_v2_async(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    country: Optional[str] = None,
    use_poster_vision: bool = False,
    usage_ctx: Optional[LlmCallContext] = None,
) -> TitleMatchResult:
    return await asyncio.to_thread(
        run_agentic_match_intl_v2, title, show_date, theater, ticketing_url,
        country, use_poster_vision, usage_ctx,
    )

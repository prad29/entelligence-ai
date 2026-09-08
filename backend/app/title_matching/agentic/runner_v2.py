"""Domestic agentic title-match v2 — metadata-aware prompt (genre/cast/
director/synopsis) + deterministic incomplete-metadata guardrail, layered
on top of v1's orchestration (sandbox HTTP, throttle/timeout handling,
parse-retry, id=0 post-lookup, cover-image attach) via
runner.run_agentic_match(variant="v2"). v1 is frozen; this module is the
only way to reach the v2 behavior.

Deliberately domestic-only: no market/country parameters, so an
international v2 call is unrepresentable rather than merely discouraged.
"""

from __future__ import annotations

from typing import Optional

from app.observability.context import LlmCallContext
from app.title_matching.agentic.runner import run_agentic_match, run_agentic_match_async
from app.title_matching.types import TitleMatchResult


def run_agentic_match_v2(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
    usage_ctx: Optional[LlmCallContext] = None,
) -> TitleMatchResult:
    return run_agentic_match(
        title, show_date, theater, ticketing_url,
        use_poster_vision=use_poster_vision,
        market="domestic",
        country=None,
        usage_ctx=usage_ctx,
        variant="v2",
    )


async def run_agentic_match_v2_async(
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
    usage_ctx: Optional[LlmCallContext] = None,
) -> TitleMatchResult:
    return await run_agentic_match_async(
        title, show_date, theater, ticketing_url,
        use_poster_vision=use_poster_vision,
        market="domestic",
        country=None,
        usage_ctx=usage_ctx,
        variant="v2",
    )

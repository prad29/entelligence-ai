"""The only writers of LlmCallLog.

Both functions are total: they catch BaseException-minus-the-unrecoverables
and log instead of raising, because spec §7 makes observability strictly
subordinate to the pipeline it observes — a failed log write must not fail or
delay the underlying Bedrock/CLI call.

Each call opens and closes its own short-lived Session rather than joining a
caller's transaction. That is deliberate: sharing the caller's session would
let a log-write error poison the caller's transaction (and a caller rollback
silently discard the log row), which is exactly the coupling §7 forbids.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from app.config import settings
from app.observability.constants import STATUS_SUCCESS
from app.observability.context import LlmCallContext, TokenUsage
from app.observability.cost import estimate_cost_usd

logger = logging.getLogger(__name__)


def log_llm_call(
    ctx: LlmCallContext,
    *,
    model_id: str,
    usage: TokenUsage,
    latency_ms: int,
    status: str = STATUS_SUCCESS,
    error_type: Optional[str] = None,
    retry_count: int = 0,
    decision: Optional[str] = None,
    cost_usd: Optional[float] = None,
    cache_hit: bool = False,
) -> None:
    """Write one LlmCallLog row. Never raises.

    `cost_usd=None` means "price it from tokens via litellm". A float means
    "the provider already reported this cost, store it verbatim" — the
    agentic CLI path reports total_cost_usd in its stream-json result event
    and that is more authoritative than our own token arithmetic.
    """
    if not settings.USAGE_TRACKING_ENABLED:
        return

    try:
        from app.database import engine
        from app.models import LlmCallLog

        resolved_cost = (
            float(cost_usd)
            if cost_usd is not None
            else estimate_cost_usd(
                model_id,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
            )
        )

        with Session(engine) as session:
            session.add(
                LlmCallLog(
                    task_type=ctx.task_type,
                    call_path=ctx.call_path,
                    model_id=model_id,
                    caller_type=ctx.caller_type,
                    api_key_id=ctx.api_key_id,
                    job_id=ctx.job_id,
                    job_type=ctx.job_type,
                    market=ctx.market,
                    country=ctx.country,
                    decision=decision,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    cost_usd=resolved_cost,
                    latency_ms=int(latency_ms),
                    status=status,
                    error_type=error_type,
                    retry_count=int(retry_count),
                    cache_hit=cache_hit,
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — see module docstring / spec §7
        logger.warning(
            "llm_call_log_write_failed task_type=%s call_path=%s model_id=%r error=%s",
            ctx.task_type, ctx.call_path, model_id, exc,
        )


def log_cache_hit(ctx: LlmCallContext, *, model_id: str, count: int = 1) -> None:
    """Write `count` zero-cost cache_hit=True rows. Never raises.

    Cache hits are recorded as ordinary LlmCallLog rows (spec §6) so the
    dedupe rate is `cache_hit count / total attempted` — a plain count query
    over one table, with no separate counter to keep consistent.
    """
    if not settings.USAGE_TRACKING_ENABLED or count <= 0:
        return

    try:
        from app.database import engine
        from app.models import LlmCallLog

        with Session(engine) as session:
            for _ in range(count):
                session.add(
                    LlmCallLog(
                        task_type=ctx.task_type,
                        call_path=ctx.call_path,
                        model_id=model_id,
                        caller_type=ctx.caller_type,
                        api_key_id=ctx.api_key_id,
                        job_id=ctx.job_id,
                        job_type=ctx.job_type,
                        market=ctx.market,
                        country=ctx.country,
                        cost_usd=0.0,
                        latency_ms=0,
                        cache_hit=True,
                    )
                )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — see module docstring / spec §7
        logger.warning(
            "llm_call_log_write_failed(cache_hit) task_type=%s count=%d error=%s",
            ctx.task_type, count, exc,
        )

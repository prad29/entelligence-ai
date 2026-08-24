"""Writers for the SerpApi/Serper call logs (spec §6/§7).

Same contract as app/observability/llm_logging.py: total functions that log
and continue rather than raise, each opening its own short-lived Session so a
log-write error cannot poison the caller's transaction. The deleted-showtimes
pipeline runs 16-way concurrent and must never fail a theater batch because a
usage row wouldn't write.

log_serper_calls (for the movieweb MCP server's web_search/web_fetch calls
inside claude-sandbox) is added to this module by a later task; the sandbox
does not yet report those calls back to the runner.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session

from app.config import settings

logger = logging.getLogger(__name__)


def log_serpapi_call(
    *,
    job_id: Optional[str],
    slot: int,
    success: bool,
    calls_made: int,
    latency_ms: int,
    error_type: Optional[str] = None,
) -> None:
    """Write one SerpApiCallLog row for one search attempt. Never raises.

    `calls_made` is SerpClient's own counter — the number of requests SerpApi
    actually served for that attempt, which is what gets billed. It has been
    incremented on every call since the client was written and read by nothing
    until now.
    """
    if not settings.USAGE_TRACKING_ENABLED:
        return

    try:
        from app.database import engine
        from app.models import SerpApiCallLog

        with Session(engine) as session:
            session.add(
                SerpApiCallLog(
                    job_id=job_id,
                    slot=slot,
                    success=success,
                    calls_made=int(calls_made),
                    latency_ms=int(latency_ms),
                    error_type=error_type,
                )
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — see module docstring / spec §7
        logger.warning(
            "serpapi_call_log_write_failed slot=%s job_id=%r error=%s", slot, job_id, exc
        )

"""Writers for the SerpApi/Serper call logs (spec §6/§7).

Same contract as app/observability/llm_logging.py: total functions that log
and continue rather than raise, each opening its own short-lived Session so a
log-write error cannot poison the caller's transaction. The deleted-showtimes
pipeline runs 16-way concurrent and must never fail a theater batch because a
usage row wouldn't write.
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


def log_serper_calls(
    calls: list,
    *,
    job_id: Optional[str],
    job_type: Optional[str],
    task_type: Optional[str],
    market: Optional[str],
) -> None:
    """Write one SerperCallLog row per call the movieweb MCP server made
    during one agentic sandbox invocation. Never raises.

    `calls` is the `serper_calls` list the sandbox's /run response now
    carries — each item `{"call_type": "search"|"scrape", "success": bool,
    "latency_ms": int}`, written by movieweb/server.js into a per-run JSONL
    file and read back by claude-sandbox/server.js before it deletes the
    ephemeral $HOME. A malformed or missing field on any one item degrades
    that item to safe defaults rather than dropping the whole batch.
    """
    if not settings.USAGE_TRACKING_ENABLED or not calls:
        return

    try:
        from app.database import engine
        from app.models import SerperCallLog

        with Session(engine) as session:
            for call in calls:
                if not isinstance(call, dict):
                    continue
                call_type = call.get("call_type") or "search"
                try:
                    latency_ms = int(call.get("latency_ms") or 0)
                except (TypeError, ValueError):
                    latency_ms = 0
                session.add(
                    SerperCallLog(
                        job_id=job_id,
                        job_type=job_type,
                        task_type=task_type,
                        market=market,
                        call_type=call_type,
                        success=bool(call.get("success", True)),
                        latency_ms=latency_ms,
                    )
                )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — see module docstring / spec §7
        logger.warning(
            "serper_call_log_write_failed job_id=%r count=%d error=%s",
            job_id, len(calls) if isinstance(calls, list) else 0, exc,
        )

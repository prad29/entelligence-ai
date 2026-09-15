"""Manual dqscan trigger — one endpoint, fire-and-forget.

No auth — matches this codebase's no-auth internal-tool convention (see
calendar_extract.py's module docstring). Results are delivered the same way
the daily cron delivers them (SES/SNS + S3 report upload), not through this
API — there is deliberately no job-status polling here, just "did the Celery
task get enqueued."
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.tasks.dqscan_task import run_dqscan_scan

router = APIRouter(prefix="/api/v1/dqscan", tags=["dqscan"])


class TriggerScanRequest(BaseModel):
    # Both optional — omit both for "last 24 hours", the same default window
    # run_daily.py itself falls back to when no state file exists yet.
    from_date: date | None = None
    to_date: date | None = None


@router.post("/trigger")
async def trigger_scan(payload: TriggerScanRequest):
    to_date = payload.to_date or date.today()
    from_date = payload.from_date or (to_date - timedelta(days=1))

    task = run_dqscan_scan.delay(
        settings.DQSCAN_DEFAULT_ENV,
        from_date.isoformat(),
        to_date.isoformat(),
    )
    return {"task_id": task.id, "env": settings.DQSCAN_DEFAULT_ENV, "from": from_date.isoformat(), "to": to_date.isoformat()}

"""
External API for the singletitle/batchtitle title matching contract.

A parallel surface to movie_title_match.py, not a rename or replacement of
it — the existing Excel/Tableau flow and its /single sync endpoint stay
exactly as they are. This router delegates row processing to the same
run_agentic_match core via app.tasks.external_match_task, so matching logic
never forks even though job orchestration (durable per-row Postgres storage
vs. xlsx + ephemeral Redis) is deliberately different.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies.api_auth import require_api_key, require_db_update_permission
from app.models import ApiKey, ApiKeyMonthlyUsage, ApiTitleMatchJob, ApiTitleMatchRow
from app.title_matching.external_schemas import (
    ExternalBatchRequest,
    ExternalRowInput,
    serialize_job_status,
    serialize_row_result,
    validate_rows_for_market,
)

router = APIRouter(prefix="/api/v1", tags=["external-title-match"])

RESULTS_PAGE_SIZE = 100

# Job endpoints live under /api/v1/external/jobs, NOT /api/v1/jobs — the
# latter prefix is already owned by app/routers/jobs.py (DetectionJob
# polling, an unrelated screen-format-detection feature) and registered
# before this router in main.py. Reusing /api/v1/jobs/{job_id} here would
# silently shadow this router's routes rather than raise any error.
JOBS_PREFIX = "/external/jobs"


def _row_errors_response(row_errors: list) -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "error": "validation_failed",
            "row_errors": [e.model_dump() for e in row_errors],
        },
    )


def _submit_job(
    rows: list[ExternalRowInput],
    market: str,
    db_update: bool,
    api_key: ApiKey,
    session: Session,
) -> dict:
    max_rows = api_key.max_rows_per_batch
    if max_rows is None:
        from app.config import settings

        max_rows = settings.MAX_BATCH_ROWS
    if len(rows) > max_rows:
        raise HTTPException(
            status_code=422,
            detail=f"Submission exceeds this key's row limit of {max_rows}",
        )

    row_errors = validate_rows_for_market(rows, market)
    if row_errors:
        _row_errors_response(row_errors)

    job = ApiTitleMatchJob(
        api_key_id=api_key.id,
        market=market,
        db_update=db_update,
        rows_total=len(rows),
    )
    session.add(job)
    session.flush()  # obtain job.id before building rows

    for row in rows:
        session.add(
            ApiTitleMatchRow(
                job_id=job.id,
                row_uuid=row.row_uuid,
                input_json=row.model_dump_json(),
            )
        )
    session.commit()

    _bump_monthly_usage(session, api_key.id, len(rows))

    from app.tasks.external_match_task import external_dispatch_job_task

    external_dispatch_job_task.delay(job.id)

    return {
        "job_id": job.id,
        "status": "queued",
        "rows_total": job.rows_total,
        "submitted_at": job.created_at.isoformat() + "Z",
    }


def _bump_monthly_usage(session: Session, api_key_id: str, row_count: int) -> None:
    from sqlalchemy import update

    year_month = datetime.utcnow().strftime("%Y-%m")
    usage = session.exec(
        select(ApiKeyMonthlyUsage)
        .where(ApiKeyMonthlyUsage.api_key_id == api_key_id)
        .where(ApiKeyMonthlyUsage.year_month == year_month)
    ).first()

    if usage is None:
        session.add(ApiKeyMonthlyUsage(api_key_id=api_key_id, year_month=year_month, rows_used=row_count))
        session.commit()
        return

    session.execute(
        update(ApiKeyMonthlyUsage)
        .where(ApiKeyMonthlyUsage.id == usage.id)
        .values(rows_used=ApiKeyMonthlyUsage.rows_used + row_count)
    )
    session.commit()


@router.post("/singletitle", status_code=202)
async def submit_single_title(
    payload: ExternalRowInput,
    type: Literal["domestic", "international"] = Query(...),
    db_update: bool = Query(False),
    api_key: ApiKey = Depends(require_db_update_permission),
    session: Session = Depends(get_session),
):
    return _submit_job([payload], type, db_update, api_key, session)


@router.post("/batchtitle", status_code=202)
async def submit_batch_title(
    payload: ExternalBatchRequest,
    type: Literal["domestic", "international"] = Query(...),
    db_update: bool = Query(False),
    api_key: ApiKey = Depends(require_db_update_permission),
    session: Session = Depends(get_session),
):
    return _submit_job(payload.rows, type, db_update, api_key, session)


def _get_owned_job(job_id: str, api_key: ApiKey, session: Session) -> ApiTitleMatchJob:
    job = session.get(ApiTitleMatchJob, job_id)
    # Tenancy-safe 404 (not 403) for a job owned by another key — job
    # existence must not be disclosed across tenants, per the spec.
    if job is None or job.api_key_id != api_key.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get(JOBS_PREFIX + "/{job_id}")
async def get_job_status(
    job_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    job = _get_owned_job(job_id, api_key, session)
    return serialize_job_status(job)


@router.get(JOBS_PREFIX + "/{job_id}/results")
async def get_job_results(
    job_id: str,
    next_cursor: Optional[str] = Query(None),
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    job = _get_owned_job(job_id, api_key, session)

    query = (
        select(ApiTitleMatchRow)
        .where(ApiTitleMatchRow.job_id == job_id)
        .where(ApiTitleMatchRow.status.in_(["completed", "failed"]))
        .order_by(ApiTitleMatchRow.id)
    )
    if next_cursor:
        try:
            after_id = int(base64.urlsafe_b64decode(next_cursor.encode()).decode())
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid next_cursor")
        query = query.where(ApiTitleMatchRow.id > after_id)

    rows = session.exec(query.limit(RESULTS_PAGE_SIZE)).all()

    new_cursor = None
    if len(rows) == RESULTS_PAGE_SIZE:
        new_cursor = base64.urlsafe_b64encode(str(rows[-1].id).encode()).decode()

    payload = serialize_job_status(job)
    payload["results"] = [serialize_row_result(r) for r in rows]
    payload["next_cursor"] = new_cursor
    return payload


class RetryRequest(BaseModel):
    row_uuids: list[str]


@router.post(JOBS_PREFIX + "/{job_id}/retry")
async def retry_job_rows(
    job_id: str,
    payload: RetryRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    job = _get_owned_job(job_id, api_key, session)

    if job.phase in ("queued", "syncing", "processing"):
        raise HTTPException(status_code=409, detail="Job is still running; cannot retry yet")

    from app.config import settings

    owned_rows = session.exec(
        select(ApiTitleMatchRow)
        .where(ApiTitleMatchRow.job_id == job_id)
        .where(ApiTitleMatchRow.row_uuid.in_(payload.row_uuids))
    ).all()
    owned_by_uuid = {r.row_uuid: r for r in owned_rows}

    unknown = [u for u in payload.row_uuids if u not in owned_by_uuid]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown row_uuid(s) for this job: {unknown}")

    queued = [u for u, r in owned_by_uuid.items() if r.status == "failed" and r.attempts < settings.EXTERNAL_API_ROW_MAX_ATTEMPTS]
    skipped = [u for u in payload.row_uuids if u not in queued]

    if queued:
        from app.tasks.external_match_task import external_retry_rows_task

        external_retry_rows_task.delay(job_id, queued)

    return {"job_id": job_id, "queued": queued, "skipped": skipped}

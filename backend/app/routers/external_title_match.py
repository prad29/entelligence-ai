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
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.database import get_session
from app.dependencies.api_auth import require_api_key, require_db_update_permission
from app.models import ApiKey, ApiTitleMatchJob, ApiTitleMatchRow
from app.title_matching.external_schemas import (
    ExternalBatchRequest,
    ExternalRowInput,
    JobResultsResponse,
    JobStatusResponse,
    RetryRequestBody,
    RetryResponse,
    SubmitJobResponse,
    ValidationFailedResponse,
    serialize_job_status,
    serialize_row_result,
    validate_rows_for_market,
)

router = APIRouter(prefix="/api/v1", tags=["external-title-match"])

_AUTH_RESPONSES = {401: {"description": "Missing or unknown x-api-key"}}
_SUBMIT_RESPONSES = {
    **_AUTH_RESPONSES,
    403: {"description": "db_update=true requested without permission on this API key"},
    422: {"description": "Row validation failed", "model": ValidationFailedResponse},
    429: {"description": "Rate limit or concurrent-job limit exceeded"},
}
_JOB_RESPONSES = {**_AUTH_RESPONSES, 404: {"description": "Unknown job, or a job owned by another key"}}

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

    from app.tasks.external_match_task import external_dispatch_job_task

    external_dispatch_job_task.delay(job.id)

    return {
        "job_id": job.id,
        "status": "queued",
        "rows_total": job.rows_total,
        "submitted_at": job.created_at.isoformat() + "Z",
    }


@router.post(
    "/singletitle",
    status_code=202,
    response_model=SubmitJobResponse,
    summary="Submit one title for asynchronous matching",
    description=(
        "Submits a single row for AI title matching against Movie Master. Row processing "
        "takes on the order of minutes (candidate retrieval, an optional ticketing-page fetch, "
        "and a Claude Code sandbox call), so this endpoint never blocks — it returns 202 with a "
        "job_id immediately. Poll GET /external/jobs/{job_id} for status, or "
        "GET /external/jobs/{job_id}/results for the resolved match once available."
    ),
    responses=_SUBMIT_RESPONSES,
)
async def submit_single_title(
    payload: ExternalRowInput,
    type: Literal["domestic", "international"] = Query(
        ..., description="Selects MovieMaster (domestic) or MovieMasterIntl (international)."
    ),
    db_update: bool = Query(
        False,
        description="If true, refreshes the local Movie Master corpus from production before "
        "matching begins. Requires db_update_allowed on the calling API key.",
    ),
    api_key: ApiKey = Depends(require_db_update_permission),
    session: Session = Depends(get_session),
):
    return _submit_job([payload], type, db_update, api_key, session)


@router.post(
    "/batchtitle",
    status_code=202,
    response_model=SubmitJobResponse,
    summary="Submit a batch of titles for asynchronous matching",
    description=(
        "Submits N rows for AI title matching. A 100-row batch runs roughly "
        "(rows ÷ worker concurrency) × per-row match time, so this endpoint never blocks — it "
        "returns 202 with a job_id immediately. Row results are durable and individually "
        "addressable: poll GET /external/jobs/{job_id}/results for partial results while the "
        "job runs, and use POST /external/jobs/{job_id}/retry to re-run only the rows that failed."
    ),
    responses=_SUBMIT_RESPONSES,
)
async def submit_batch_title(
    payload: ExternalBatchRequest,
    type: Literal["domestic", "international"] = Query(
        ..., description="Selects MovieMaster (domestic) or MovieMasterIntl (international)."
    ),
    db_update: bool = Query(
        False,
        description="If true, refreshes the local Movie Master corpus from production before "
        "matching begins. Requires db_update_allowed on the calling API key.",
    ),
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


@router.get(
    JOBS_PREFIX + "/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll job status and progress",
    description=(
        "Returns the job's current phase and row-level progress counters. Poll no more "
        "frequently than every 60 seconds — a job may legitimately show no progress for "
        "several minutes given per-row runtime."
    ),
    responses=_JOB_RESPONSES,
)
async def get_job_status(
    job_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    job = _get_owned_job(job_id, api_key, session)
    return serialize_job_status(job)


@router.get(
    JOBS_PREFIX + "/{job_id}/results",
    response_model=JobResultsResponse,
    summary="Fetch completed row results (supports partial retrieval mid-run)",
    description=(
        "Returns completed rows (status completed or failed), whether or not the job has "
        "finished. Paginated at 100 rows per page via an opaque next_cursor. Rows complete in "
        "arbitrary order — key on row_uuid, not array position, since results shift between polls."
    ),
    responses=_JOB_RESPONSES,
)
async def get_job_results(
    job_id: str,
    next_cursor: Optional[str] = Query(None, description="Opaque cursor from a previous response's next_cursor field."),
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


_TERMINAL_PHASES = ("completed", "completed_with_errors", "failed")


@router.post(
    JOBS_PREFIX + "/{job_id}/retry",
    response_model=RetryResponse,
    summary="Re-run only the named failed rows within an existing job",
    description=(
        "Re-runs failed rows in place without a full 90-minute rerun over a handful of failures. "
        "Only rows currently status=failed and below the per-row attempt cap are retried; others "
        "are reported back in `skipped`. Requires the job to be in a terminal phase — a job still "
        "queued/syncing/processing returns 409."
    ),
    responses={**_JOB_RESPONSES, 409: {"description": "Job is still running; cannot retry yet"}},
)
async def retry_job_rows(
    job_id: str,
    payload: RetryRequestBody,
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
):
    from sqlalchemy import update

    job = _get_owned_job(job_id, api_key, session)

    from app.config import settings

    # Validate row_uuids before touching job.phase at all — a 404 here must
    # never leave the job stuck in "processing" with no dispatch to move it
    # back out.
    owned_rows = session.exec(
        select(ApiTitleMatchRow)
        .where(ApiTitleMatchRow.job_id == job_id)
        .where(ApiTitleMatchRow.row_uuid.in_(payload.row_uuids))
    ).all()
    owned_by_uuid = {r.row_uuid: r for r in owned_rows}

    unknown = [u for u in payload.row_uuids if u not in owned_by_uuid]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown row_uuid(s) for this job: {unknown}")

    # Atomic, conditional phase flip: only one concurrent /retry call for
    # this job can win the transition out of a terminal phase. A second
    # call racing the first sees rowcount==0 (phase no longer matches
    # terminal, since the first call already moved it) and gets a 409
    # instead of both dispatching a retry over the same rows.
    result = session.execute(
        update(ApiTitleMatchJob)
        .where(ApiTitleMatchJob.id == job_id)
        .where(ApiTitleMatchJob.phase.in_(_TERMINAL_PHASES))
        .values(phase="processing")
    )
    session.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Job is still running; cannot retry yet")

    queued = [u for u, r in owned_by_uuid.items() if r.status == "failed" and r.attempts < settings.EXTERNAL_API_ROW_MAX_ATTEMPTS]
    skipped = [u for u in payload.row_uuids if u not in queued]

    if queued:
        from app.tasks.external_match_task import external_retry_rows_task

        external_retry_rows_task.delay(job_id, queued)
    else:
        # No retryable rows found (e.g. all requested rows are already at
        # the attempt cap) — nothing will move the phase off 'processing'
        # via finalize, so put it back to what it was before this attempt.
        session.execute(
            update(ApiTitleMatchJob)
            .where(ApiTitleMatchJob.id == job_id)
            .values(phase="completed" if job.rows_failed == 0 else "completed_with_errors")
        )
        session.commit()

    return {"job_id": job_id, "queued": queued, "skipped": skipped}

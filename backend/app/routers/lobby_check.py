"""
Async-batch API for cinema lobby marketing-material image extraction
(Qwen 3-VL on Bedrock). Submit N S3 image links, poll for per-image results.
See docs/plans/2026-09-01-lobby-check-design.md.

Nothing else in this backend owns the /api/v1/lobby-check prefix, so
(unlike external_title_match.py, which is forced onto a bare /api/v1 +
a /external/jobs sub-prefix to avoid shadowing jobs.py) this router can use
a normal feature prefix throughout.
"""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.dependencies.api_auth import require_api_key_lobby_check
from app.lobby_check.schemas import (
    LobbyCheckJobStatusResponse,
    LobbyCheckRequest,
    LobbyCheckResultsResponse,
    SubmitLobbyCheckResponse,
    ValidationFailedResponse,
    serialize_job_status,
    serialize_row_result,
    validate_batch_size,
)
from app.models import ApiKey, LobbyCheckJob, LobbyCheckRow

router = APIRouter(prefix="/api/v1/lobby-check", tags=["lobby-check"])

_AUTH_RESPONSES = {401: {"description": "Missing or unknown x-api-key"}}
_SUBMIT_RESPONSES = {
    **_AUTH_RESPONSES,
    422: {"description": "Row validation failed", "model": ValidationFailedResponse},
    429: {"description": "Rate limit or concurrent-job limit exceeded"},
}
_JOB_RESPONSES = {**_AUTH_RESPONSES, 404: {"description": "Unknown job, or a job owned by another key"}}

RESULTS_PAGE_SIZE = 100


def _row_errors_response(row_errors: list) -> None:
    raise HTTPException(
        status_code=422,
        detail={
            "error": "validation_failed",
            "row_errors": [e.model_dump() for e in row_errors],
        },
    )


@router.post(
    "",
    status_code=202,
    response_model=SubmitLobbyCheckResponse,
    summary="Submit a batch of S3 image links for extraction",
    description=(
        "Submits N images for AI extraction. A batch runs roughly "
        "(images ÷ worker concurrency) × per-image extraction time, so this endpoint never "
        "blocks — it returns 202 with a job_id immediately. Poll GET .../jobs/{job_id} for "
        "status, or GET .../jobs/{job_id}/results for per-image results while the job runs."
    ),
    responses=_SUBMIT_RESPONSES,
)
async def submit_lobby_check(
    payload: LobbyCheckRequest,
    api_key: ApiKey = Depends(require_api_key_lobby_check),
    session: Session = Depends(get_session),
):
    max_rows = api_key.max_rows_per_batch or settings.LOBBY_CHECK_MAX_BATCH_ROWS
    row_errors = validate_batch_size(payload.images, max_rows)
    if row_errors:
        _row_errors_response(row_errors)

    job = LobbyCheckJob(api_key_id=api_key.id, rows_total=len(payload.images))
    session.add(job)
    session.flush()  # obtain job.id before building rows

    for image in payload.images:
        session.add(
            LobbyCheckRow(
                job_id=job.id,
                photo_id=image.photo_id,
                image_url=image.image_url,
                input_json=image.model_dump_json(),
            )
        )
    session.commit()

    from app.tasks.lobby_check_task import lobby_check_dispatch_job_task

    lobby_check_dispatch_job_task.delay(job.id)

    return {
        "job_id": job.id,
        "status": "queued",
        "rows_total": job.rows_total,
        "poll_url": f"/api/v1/lobby-check/jobs/{job.id}",
    }


def _get_owned_job(session: Session, job_id: str, api_key: ApiKey) -> LobbyCheckJob:
    job = session.get(LobbyCheckJob, job_id)
    # Tenancy-safe 404 (not 403) for a job owned by another key — job
    # existence must not be disclosed across tenants.
    if job is None or job.api_key_id != api_key.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get(
    "/jobs/{job_id}",
    response_model=LobbyCheckJobStatusResponse,
    summary="Poll job status and progress",
    responses=_JOB_RESPONSES,
)
async def get_job_status(
    job_id: str,
    api_key: ApiKey = Depends(require_api_key_lobby_check),
    session: Session = Depends(get_session),
):
    job = _get_owned_job(session, job_id, api_key)
    payload = serialize_job_status(job)
    payload["results_url"] = f"/api/v1/lobby-check/jobs/{job_id}/results"
    return payload


@router.get(
    "/jobs/{job_id}/results",
    response_model=LobbyCheckResultsResponse,
    summary="Fetch per-image results (supports partial retrieval mid-run)",
    description=(
        "Returns rows in any status, sorted by submission order — whether or not the job "
        "has finished. Paginated at 100 rows per page via an opaque cursor. Diagnostic "
        "fields (tokens, cost, latency, framing, model_id) are never included here — "
        "see /api/v1/usage/* for cost/spend reporting."
    ),
    responses=_JOB_RESPONSES,
)
async def get_job_results(
    job_id: str,
    cursor: Optional[str] = Query(None, description="Opaque cursor from a previous response's next_cursor field."),
    status: Optional[str] = Query(None, description="Filter to one row status: pending|dispatched|completed|failed."),
    api_key: ApiKey = Depends(require_api_key_lobby_check),
    session: Session = Depends(get_session),
):
    job = _get_owned_job(session, job_id, api_key)

    query = select(LobbyCheckRow).where(LobbyCheckRow.job_id == job_id)
    if status:
        query = query.where(LobbyCheckRow.status == status)
    query = query.order_by(LobbyCheckRow.id)

    if cursor:
        try:
            after_id = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        query = query.where(LobbyCheckRow.id > after_id)

    rows = session.exec(query.limit(RESULTS_PAGE_SIZE)).all()

    has_more = len(rows) == RESULTS_PAGE_SIZE
    next_cursor = (
        base64.urlsafe_b64encode(str(rows[-1].id).encode()).decode() if has_more else None
    )

    return {
        "job_id": job_id,
        "status": job.phase,
        "results": [
            serialize_row_result(r, review_threshold=settings.LOBBY_CHECK_REVIEW_CONFIDENCE_THRESHOLD)
            for r in rows
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }

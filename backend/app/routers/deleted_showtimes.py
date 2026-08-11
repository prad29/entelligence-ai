"""
Deleted Showtimes Check — upload/status/download/history endpoints.

Mirrors the movie_title_match.py /batch upload -> job+poll -> download
pattern: the upload endpoint validates the file, caps rows, persists the
upload to S3, creates a DeletedShowtimeJob row, and enqueues dispatch as its
own Celery task (dispatch_job_task) rather than building the chord inline —
re-parsing the file and publishing one Celery message per batch is the
expensive part of dispatch and can alone exceed an ALB/nginx idle timeout for
large files if done inside the request.

No auth — matches every other internal batch router in this codebase
(movie_title_match.py, movie_jobs.py); this is an internal ops tool, not a
public API surface.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session

router = APIRouter(prefix="/api/v1/deleted-showtimes", tags=["deleted-showtimes"])

TheaterVerifyMode = Literal["off", "warn", "strict"]
FallbackMode = Literal["off", "auto", "plain", "movie"]


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


@router.post("/batch")
async def upload_batch(
    file: UploadFile = File(...),
    title_missing_is_deleted: str = Form("false"),
    strict_screen_count: str = Form("false"),
    theater_verify: TheaterVerifyMode = Form("warn"),
    fallback: FallbackMode = Form("auto"),
    workers: int = Form(4),
    session: Session = Depends(get_session),
):
    """Upload a .csv/.xlsx of showtimes to check against Google via SerpApi.

    Required columns (case-insensitive): Theater Name, Title, Show date,
    Show time. The file must NOT already contain a DELETED_SHOWTIME column —
    that's the output this job produces. Enforces DELETED_SHOWTIME_MAX_ROWS
    as a cost guardrail against runaway SerpApi credit spend.
    """
    from app.deleted_showtimes import batch_io, storage
    from app.tasks.deleted_showtime_task import dispatch_job_task

    if not settings.SERPAPI_API_KEY:
        raise HTTPException(status_code=400, detail="SERPAPI_API_KEY is not configured")

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".csv", ".xlsx"):
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported")

    contents = await file.read()

    try:
        _headers, rows = batch_io.parse_upload(contents, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row_count = len(rows)
    if row_count > settings.DELETED_SHOWTIME_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.DELETED_SHOWTIME_MAX_ROWS} row limit",
        )
    if row_count == 0:
        raise HTTPException(status_code=400, detail="File has no data rows")

    if workers < 1 or workers > 16:
        raise HTTPException(status_code=400, detail="workers must be between 1 and 16")

    from app.models import DeletedShowtimeJob

    job_id = str(uuid.uuid4())
    upload_key = storage.upload_key(job_id, ext)
    storage.put_bytes(upload_key, contents)

    job = DeletedShowtimeJob(
        id=job_id,
        status="queued",
        total=row_count,
        title_missing_is_deleted=_parse_bool(title_missing_is_deleted),
        strict_screen_count=_parse_bool(strict_screen_count),
        theater_verify=theater_verify,
        fallback=fallback,
        workers=workers,
        file_path=upload_key,
        original_filename=filename,
    )
    session.add(job)
    session.commit()

    dispatch_job_task.delay(job_id)

    return {"job_id": job_id}


@router.post("/preflight")
async def preflight_batch(file: UploadFile = File(...)):
    """Parse an upload WITHOUT creating a job, returning row count + how many
    rows have showtimes already started in US Eastern time (Google drops
    those from its panel, so they'd come back UNABLE_TO_DETERMINE rather than
    a real deletion signal). The frontend calls this first to show a warning
    banner the user can confirm past, before /batch actually spends credits.
    """
    from zoneinfo import ZoneInfo

    from app.deleted_showtimes import batch_io
    from app.deleted_showtimes.core import preflight_late_rows

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".csv", ".xlsx"):
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported")

    contents = await file.read()
    try:
        headers, rows = batch_io.parse_upload(contents, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row_count = len(rows)
    if row_count > settings.DELETED_SHOWTIME_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.DELETED_SHOWTIME_MAX_ROWS} row limit",
        )

    showtime_rows = batch_io.rows_to_showtime_rows(headers, rows)
    now_et = datetime.now(ZoneInfo("America/New_York"))
    at_risk = preflight_late_rows(showtime_rows, now_et)

    return {
        "row_count": row_count,
        "rows_already_started": at_risk,
        "now_et": now_et.isoformat(),
    }


def _serialize_job(job) -> dict:
    progress = (job.processed / job.total) if job.total > 0 else 0
    return {
        "job_id": job.id,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "progress": progress,
        "true_count": job.true_count,
        "false_count": job.false_count,
        "unknown_count": job.unknown_count,
        "original_filename": job.original_filename,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "output_url": (
            f"/api/v1/deleted-showtimes/batch/{job.id}/download"
            if job.status == "completed" and job.output_path
            else None
        ),
        "audit_url": (
            f"/api/v1/deleted-showtimes/batch/{job.id}/audit"
            if job.status == "completed" and job.audit_output_path
            else None
        ),
        "error": job.error,
    }


@router.get("/batch/{job_id}")
async def get_batch_job(job_id: str, session: Session = Depends(get_session)):
    from app.models import DeletedShowtimeJob

    job = session.get(DeletedShowtimeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(job)


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    """Global history of past runs (no per-user scoping — matches this
    codebase's no-auth internal-tool convention)."""
    from app.models import DeletedShowtimeJob

    jobs = session.exec(
        select(DeletedShowtimeJob)
        .order_by(DeletedShowtimeJob.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {"jobs": [_serialize_job(j) for j in jobs]}


@router.get("/batch/{job_id}/download")
async def download_batch_job(job_id: str, session: Session = Depends(get_session)) -> Response:
    from app.deleted_showtimes import storage
    from app.models import DeletedShowtimeJob

    job = session.get(DeletedShowtimeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job not completed")
    if job.ttl and datetime.utcnow() > job.ttl:
        raise HTTPException(status_code=410, detail="Download expired")
    if not job.output_path or not storage.exists(job.output_path):
        raise HTTPException(status_code=404, detail="Output file not found")

    contents = storage.get_bytes(job.output_path)
    return Response(
        content=contents,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="deleted_showtimes_results_{job_id[:8]}.xlsx"'
        },
    )


@router.get("/batch/{job_id}/audit")
async def download_audit_json(job_id: str, session: Session = Depends(get_session)) -> Response:
    from app.deleted_showtimes import storage
    from app.models import DeletedShowtimeJob

    job = session.get(DeletedShowtimeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job not completed")
    if job.ttl and datetime.utcnow() > job.ttl:
        raise HTTPException(status_code=410, detail="Download expired")
    if not job.audit_output_path or not storage.exists(job.audit_output_path):
        raise HTTPException(status_code=404, detail="Audit file not found")

    contents = storage.get_bytes(job.audit_output_path)
    return Response(
        content=contents,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="deleted_showtimes_audit_{job_id[:8]}.json"'
        },
    )

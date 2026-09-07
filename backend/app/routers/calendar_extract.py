"""
Competitive Calendar Extraction — upload/status/download/history endpoints.

Single `jobs` resource, not `/batch` — this is one-job-per-upload (classify
then extract one PDF), not a fan-out batch pipeline like deleted-showtimes.
No auth — matches this codebase's no-auth internal-tool convention.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, select

from app.database import get_session

router = APIRouter(prefix="/api/v1/calendar-extract", tags=["calendar-extract"])


@router.post("/jobs")
async def upload_job(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Every upload always creates a new job and reprocesses — no dedup.
    (Product decision 2026-09-07: an earlier content-hash dedup was removed
    because it surprised users re-running the same file on purpose. `file_hash`
    is still recorded on the job row for observability/debugging, just no
    longer used to skip processing.)"""
    from app.calendar_extract import storage
    from app.models import CalendarExtractJob
    from app.tasks.calendar_extract_task import process_calendar_job

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="Only .pdf files are supported")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="File is empty")

    file_hash = hashlib.sha256(contents).hexdigest()

    job_id = str(uuid.uuid4())
    upload_key = storage.upload_key(job_id)
    storage.put_bytes(upload_key, contents)

    job = CalendarExtractJob(
        id=job_id,
        status="queued",
        original_filename=filename,
        file_hash=file_hash,
        file_path=upload_key,
    )
    session.add(job)
    session.commit()

    process_calendar_job.delay(job_id)

    return {"job_id": job_id}


def _serialize_job(job) -> dict:
    return {
        "job_id": job.id,
        "status": job.status,
        "original_filename": job.original_filename,
        "is_release_calendar": job.is_release_calendar,
        "classification_reason": job.classification_reason,
        "rows_extracted": job.rows_extracted,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "output_url": (
            f"/api/v1/calendar-extract/jobs/{job.id}/download"
            if job.status == "completed" and job.output_path
            else None
        ),
        "error": job.error,
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, session: Session = Depends(get_session)):
    from app.models import CalendarExtractJob

    job = session.get(CalendarExtractJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(job)


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    from app.models import CalendarExtractJob

    jobs = session.exec(
        select(CalendarExtractJob)
        .order_by(CalendarExtractJob.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {"jobs": [_serialize_job(j) for j in jobs]}


@router.get("/jobs/{job_id}/download")
async def download_job(job_id: str, session: Session = Depends(get_session)) -> Response:
    from app.calendar_extract import storage
    from app.models import CalendarExtractJob

    job = session.get(CalendarExtractJob, job_id)
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
            "Content-Disposition": f'attachment; filename="competitive_calendar_{job_id[:8]}.xlsx"'
        },
    )

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session

from app.config import settings
from app.database import engine as db_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/movie-title-match", tags=["movie-title-match-v2"])


class TitleMatchV2Request(BaseModel):
    title: str
    theater: Optional[str] = None
    show_date: Optional[str] = None       # YYYY-MM-DD
    ticketing_url: Optional[str] = None
    use_poster_vision: bool = False


@router.post(
    "/single",
    summary="Submit one domestic title for v2 (metadata-aware) matching",
    description=(
        "Domestic-only. Same synchronous contract as /api/v1/movie-title-match/single, but the "
        "match additionally weighs genre/cast/director/synopsis against the listing, not just "
        "title text, and deterministically rejects a pick whose director AND synopsis are both "
        "empty (unless genre is Sports or Concert/Special Events). On an agentic-pipeline "
        "failure this still returns 200 with an honest suggested_movie_id=0 / REVIEW result "
        "rather than an error status."
    ),
)
async def match_single_title_v2(payload: TitleMatchV2Request):
    if not settings.AGENTIC_TITLE_MATCH_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="v2 title matching requires Mode B (agentic) to be enabled",
        )

    from app.title_matching.agentic import AgenticError
    from app.title_matching.agentic.runner_v2 import run_agentic_match_v2_async
    from app.title_matching.evidence_fetcher import attach_to_result
    from app.title_matching.types import TitleMatchResult

    try:
        result = await run_agentic_match_v2_async(
            payload.title,
            payload.show_date,
            payload.theater,
            payload.ticketing_url,
            use_poster_vision=payload.use_poster_vision,
        )
    except AgenticError as exc:
        # Same honest-no-match-at-200 contract as v1's /single -- see that
        # route for the full rationale. pipeline_variant marks this as a v2
        # failure so it's distinguishable in the response body.
        logger.warning(
            "single_match_v2_agentic_error title=%r error_type=%s error=%s",
            payload.title, type(exc).__name__, exc,
        )
        result = TitleMatchResult(
            suggested_movie_id=0,
            suggested_movie_title="Unknown",
            canonical_movie_id=0,
            confidence=0.0,
            decision="REVIEW",
            reasoning=(
                f"No match returned: the agentic matching pipeline failed with "
                f"{type(exc).__name__}: {exc}. Manual review required."
            ),
            evidence={
                "agentic": True,
                "agentic_error": True,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "pipeline_variant": "v2",
            },
            fired_ai=False,
        )

    attach_to_result(result, payload.ticketing_url)
    return result.__dict__


@router.post(
    "/batch",
    summary="Upload a batch of domestic titles for v2 matching",
    description=(
        "Domestic-only. Mirrors /api/v1/movie-title-match/batch's multipart upload contract "
        "(same columns, same async Celery-job-plus-polling flow) but every row is dispatched "
        "through the v2 (metadata-aware) pipeline via the shared movietitlebatchjob table's "
        "pipeline_variant column, not a separate table. Poll GET /batch/{job_id} for status."
    ),
)
async def upload_batch_v2(
    file: UploadFile = File(...),
    use_poster_vision: str = Form("false"),
):
    if not settings.AGENTIC_TITLE_MATCH_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Batch title matching requires Mode B (agentic) to be enabled",
        )

    from app.title_matching import batch_io, batch_storage
    from app.models import MovieTitleBatchJob
    from app.tasks.agentic_match_task import dispatch_batch_task

    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".csv", ".xlsx"):
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported")

    contents = await file.read()

    try:
        _headers, rows = batch_io.parse_upload(contents, ext, market="domestic")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row_count = len(rows)
    if row_count > settings.MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.MAX_BATCH_ROWS} row limit",
        )

    use_poster_vision_bool = use_poster_vision.strip().lower() in ("true", "1", "yes")

    job_id = str(uuid.uuid4())
    upload_key = batch_storage.upload_key(job_id, ext)
    batch_storage.put_bytes(upload_key, contents)

    with Session(db_engine) as db_session:
        job = MovieTitleBatchJob(
            id=job_id,
            status="queued",
            total=row_count,
            use_poster_vision=use_poster_vision_bool,
            file_path=upload_key,
            pipeline_variant="v2",
        )
        db_session.add(job)
        db_session.commit()

    dispatch_batch_task.delay(job_id)

    return {"job_id": job_id}


def _get_v2_job(job_id: str):
    from app.models import MovieTitleBatchJob
    from app.tasks.agentic_match_task import _variant_of

    with Session(db_engine) as session:
        job = session.get(MovieTitleBatchJob, job_id)
        if job is None or _variant_of(job) != "v2":
            raise HTTPException(status_code=404, detail="Job not found")
        # Detach values we need after the session closes.
        return {
            "id": job.id,
            "status": job.status,
            "total": job.total,
            "processed": job.processed,
            "matched": job.matched,
            "no_match": job.no_match,
            "failed": job.failed,
            "error": job.error,
            "output_path": job.output_path,
            "ttl": job.ttl,
        }


@router.get(
    "/batch/{job_id}",
    summary="Poll v2 batch job status and progress",
    description="Same status/progress shape as v1's GET /batch/{job_id}, scoped to v2 jobs only.",
)
async def get_batch_job_v2(job_id: str):
    job = _get_v2_job(job_id)
    progress = (job["processed"] / job["total"]) if job["total"] > 0 else 0

    return {
        "job_id": job["id"],
        "status": job["status"],
        "total": job["total"],
        "processed": job["processed"],
        "progress": progress,
        "matched": job["matched"],
        "no_match": job["no_match"],
        "failed": job["failed"],
        "output_url": (
            f"/api/v2/movie-title-match/batch/{job['id']}/download"
            if job["status"] == "completed" and job["output_path"]
            else None
        ),
        "error": job["error"],
    }


@router.get(
    "/batch/{job_id}/download",
    summary="Download completed v2 batch results as XLSX",
    description=(
        "Same XLSX-download contract as v1's /batch/{job_id}/download. 400 if the job isn't "
        "completed yet, 410 if the job's TTL has expired, 404 if the output file is missing."
    ),
)
async def download_batch_job_v2(job_id: str) -> Response:
    from app.title_matching import batch_storage

    job = _get_v2_job(job_id)

    if job["status"] != "completed":
        raise HTTPException(status_code=400, detail="Job not completed")

    if job["ttl"] and datetime.utcnow() > job["ttl"]:
        raise HTTPException(status_code=410, detail="Download expired")

    if not job["output_path"] or not batch_storage.exists(job["output_path"]):
        raise HTTPException(status_code=404, detail="Output file not found")

    contents = batch_storage.get_bytes(job["output_path"])

    return Response(
        content=contents,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="movie_title_match_v2_results_{job_id[:8]}.xlsx"'
        },
    )

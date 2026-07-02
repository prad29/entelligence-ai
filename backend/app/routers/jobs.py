"""
Jobs router — Phase 4.

Exposes:
  GET /api/v1/jobs/{job_id}           — poll status and progress
  GET /api/v1/jobs/{job_id}/download  — stream completed output xlsx
"""

import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.database import get_session
from app.models import DetectionJob

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict:
    """Return current status, progress fraction, and aggregate stats for a job."""
    job = session.get(DetectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    import json as _json
    progress = min(1.0, job.processed / job.total) if job.total > 0 else 0.0
    stats = _json.loads(job.stats) if job.stats else {}

    return {
        "job_id": job.id,
        "status": job.status,
        "total": job.total,
        "processed": job.processed,
        "progress": round(progress, 3),
        "matched": stats.get("matched", 0),
        "ai_suggestions": stats.get("ai_suggestions", 0),
        "output_url": f"/api/v1/jobs/{job.id}/download" if job.status == "completed" and job.output_path else None,
    }


@router.get("/{job_id}/download")
def download_job(job_id: str, session: Session = Depends(get_session)) -> FileResponse:
    """Stream the completed output xlsx.  Returns 400 if not done, 410 if TTL expired."""
    job = session.get(DetectionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job not completed")

    if job.ttl and datetime.utcnow() > job.ttl:
        raise HTTPException(status_code=410, detail="Download expired")

    if not job.output_path or not os.path.exists(job.output_path):
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        job.output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"screen_format_results_{job_id[:8]}.xlsx",
    )

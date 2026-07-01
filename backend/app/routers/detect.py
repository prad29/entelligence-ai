import io
import os
import uuid

import openpyxl
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session
from typing import Optional

from app.database import get_session
from app.models import DetectionJob

router = APIRouter(prefix="/api/v1/detect", tags=["detect"])


class DetectSingleRequest(BaseModel):
    amenity: str
    circuit_name: Optional[str] = ""


@router.post("/single")
async def detect_single(payload: DetectSingleRequest, request: Request):
    engine = request.app.state.engine
    result = engine.detect(payload.amenity, payload.circuit_name or "")
    return result.__dict__ if hasattr(result, "__dict__") else result


@router.post("/batch", status_code=202)
async def detect_batch(
    request: Request,
    file: UploadFile = File(...),
    include_diagnostics: bool = False,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    session: Session = Depends(get_session),
) -> JSONResponse:
    """
    Accept an .xlsx file with columns circuit_name and amenities, validate it,
    enqueue an async background job, and return 202 with the job_id.
    """
    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active
        headers = [
            str(ws.cell(1, c).value or "").strip().lower()
            for c in range(1, ws.max_column + 1)
        ]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid xlsx file")

    if "amenities" not in headers:
        raise HTTPException(status_code=400, detail="Column 'amenities' not found")
    if "circuit_name" not in headers:
        raise HTTPException(status_code=400, detail="Column 'circuit_name' not found")

    total = max(0, ws.max_row - 1)

    from app.config import settings

    if total > settings.MAX_BATCH_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds MAX_BATCH_ROWS={settings.MAX_BATCH_ROWS}",
        )

    job_id = str(uuid.uuid4())
    os.makedirs("/tmp/amenity_uploads", exist_ok=True)
    upload_path = f"/tmp/amenity_uploads/{job_id}_input.xlsx"
    with open(upload_path, "wb") as f:
        f.write(content)

    job = DetectionJob(
        id=job_id,
        total=total,
        status="queued",
        include_diagnostics=include_diagnostics,
    )
    session.add(job)
    session.commit()

    from app.workers.batch_worker import run_batch_job

    engine = request.app.state.engine
    background_tasks.add_task(run_batch_job, job_id, upload_path, include_diagnostics, engine)

    return JSONResponse({"job_id": job_id}, status_code=202)

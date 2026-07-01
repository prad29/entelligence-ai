"""
Async batch worker for Phase 4.

Processes an uploaded .xlsx row-by-row in chunks of 50, runs Layer 1
in-memory detection, writes an output .xlsx, and updates DetectionJob
progress in the database.

Layer 2 (Bedrock AI) is stubbed: no-match rows are logged to ReviewItem
and the official screen_format stays 'Standard'.  Phase 5 will wire the
actual Bedrock client.
"""

import json
import logging
import os
from datetime import datetime, timedelta

import openpyxl
from sqlmodel import Session

from app.database import engine as db_engine
from app.models import DetectionJob, ReviewItem

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 50


def run_batch_job(
    job_id: str,
    upload_path: str,
    include_diagnostics: bool,
    detection_engine,
) -> None:
    """
    Background task entry-point.  Called by FastAPI BackgroundTasks.

    Parameters
    ----------
    job_id:
        UUID string — primary key of the DetectionJob row.
    upload_path:
        Absolute path to the uploaded .xlsx saved by the endpoint.
    include_diagnostics:
        When True, extra columns (detected_keyword, match_source,
        match_track, confidence, ai_suggested_format, ai_reasoning)
        are appended to every output row.
    detection_engine:
        A ScreenFormatEngine instance loaded from app.state.engine.
    """
    from app.config import settings

    with Session(db_engine) as session:
        job = session.get(DetectionJob, job_id)
        if not job:
            logger.error("run_batch_job: job %s not found", job_id)
            return

        job.status = "processing"
        session.commit()

        try:
            _process_job(job_id, upload_path, include_diagnostics, detection_engine, session, settings)
        except Exception:
            # Reload job inside the same session to avoid stale state.
            job = session.get(DetectionJob, job_id)
            if job:
                job.status = "failed"
                session.commit()
            logger.exception("run_batch_job: job %s failed", job_id)
            raise


def _process_job(
    job_id: str,
    upload_path: str,
    include_diagnostics: bool,
    detection_engine,
    session: Session,
    settings,
) -> None:
    wb_in = openpyxl.load_workbook(upload_path, data_only=True)
    ws = wb_in.active

    headers = [
        str(ws.cell(1, c).value or "").strip().lower()
        for c in range(1, ws.max_column + 1)
    ]
    amenities_idx = headers.index("amenities")
    circuit_idx = headers.index("circuit_name")

    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active

    out_headers = ["circuit_name", "amenities", "screen_format"]
    if include_diagnostics:
        out_headers += [
            "detected_keyword",
            "match_source",
            "match_track",
            "confidence",
            "ai_suggested_format",
            "ai_reasoning",
        ]
    ws_out.append(out_headers)

    stats: dict = {"matched": 0, "standard": 0, "ai_suggestions": 0, "no_match": 0}
    pending_review: list[ReviewItem] = []

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=1):
        amenity = str(row[amenities_idx] or "").strip()
        circuit = str(row[circuit_idx] or "").strip()

        result = detection_engine.detect(amenity, circuit)

        if result.fired_ai:
            # Layer 2 stub: AI classification deferred to Phase 5.
            # Official output = Standard; log for human review.
            stats["no_match"] += 1
            stats["standard"] += 1
            stats["ai_suggestions"] += 1
            pending_review.append(
                ReviewItem(
                    type="ai_suggestion",
                    source_string=amenity,
                    circuit=circuit,
                    suggested_format="Standard",
                    confidence=0.0,
                    reasoning="No keyword match — pending AI classification (Phase 5)",
                )
            )
        else:
            if result.screen_format != "Standard":
                stats["matched"] += 1
            else:
                stats["standard"] += 1

        out_row = [circuit, amenity, result.screen_format]
        if include_diagnostics:
            out_row += [
                result.detected_keyword or "",
                result.match_source,
                result.match_track or "",
                result.confidence,
                result.ai_suggested_format or "",
                result.ai_reasoning or "",
            ]
        ws_out.append(out_row)

        if row_idx % _CHUNK_SIZE == 0:
            job = session.get(DetectionJob, job_id)
            if job:
                job.processed = row_idx
            for ri in pending_review:
                session.add(ri)
            pending_review = []
            session.commit()

    # Final flush of remaining review items and progress.
    total_rows = ws.max_row - 1
    job = session.get(DetectionJob, job_id)
    if job:
        job.processed = total_rows

    for ri in pending_review:
        session.add(ri)

    os.makedirs("/tmp/amenity_outputs", exist_ok=True)
    output_path = f"/tmp/amenity_outputs/{job_id}_output.xlsx"
    wb_out.save(output_path)

    if job:
        job.output_path = output_path
        job.status = "completed"
        job.stats = json.dumps(stats)
        job.ttl = datetime.utcnow() + timedelta(hours=settings.JOB_TTL_HOURS)

    session.commit()

    # Remove the uploaded input file to free space.
    try:
        os.remove(upload_path)
    except OSError:
        pass

    logger.info(
        "run_batch_job: job %s completed — %d rows, stats=%s",
        job_id,
        total_rows,
        stats,
    )

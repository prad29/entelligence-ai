"""
Async batch worker for Phase 5.

Processes an uploaded .xlsx or .csv row-by-row, runs Layer 1 in-memory
detection, calls AWS Bedrock (Layer 2) for no-match rows when AI_TRIGGER_MODE
is enabled, writes an output .xlsx, and updates DetectionJob progress in the
database.
"""

import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any

import openpyxl
from openpyxl.styles import PatternFill
from sqlmodel import Session

from app.database import engine as db_engine
from app.models import DetectionJob, ReviewItem

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 50


def _read_rows(upload_path: str) -> tuple[list[str], list[tuple]]:
    """
    Read amenity rows from either .xlsx or .csv.

    Returns (headers_lower, data_rows) where data_rows is a list of tuples
    with values in header order.
    """
    if upload_path.lower().endswith(".csv"):
        with open(upload_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            raw_headers = next(reader)
            headers = [h.strip().lower() for h in raw_headers]
            rows = [tuple(row) for row in reader]
        return headers, rows

    # xlsx
    wb = openpyxl.load_workbook(upload_path, data_only=True)
    ws = wb.active
    headers = [
        str(ws.cell(1, c).value or "").strip().lower()
        for c in range(1, ws.max_column + 1)
    ]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    return headers, rows


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
    headers, rows = _read_rows(upload_path)
    amenities_idx = headers.index("amenities")
    circuit_idx = headers.index("circuit_name")

    # Update total to the actual row count (estimate may be off by ±1 for CSV)
    actual_total = len(rows)
    job = session.get(DetectionJob, job_id)
    if job and job.total != actual_total:
        job.total = actual_total
        session.commit()

    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active
    _AI_FILL = PatternFill(start_color="FFFFE0", end_color="FFFFE0", fill_type="solid")

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

    stats: dict[str, int] = {"matched": 0, "standard": 0, "ai_suggestions": 0, "no_match": 0}

    # Single pass: Layer 1 for all rows, Layer 2 AI only for no-match rows.
    # Progress is updated every _CHUNK_SIZE rows so the UI stays live.
    ai_enabled = settings.AI_TRIGGER_MODE not in ("off", "")
    pending_review: list[ReviewItem] = []
    all_results: list[Any] = []

    if ai_enabled:
        from app.detection.bedrock_client import bedrock_client
        known_formats = detection_engine.get_all_formats()
    else:
        known_formats = []

    for row_idx, row in enumerate(rows, start=1):
        amenity = str(row[amenities_idx] or "").strip()
        circuit = str(row[circuit_idx] or "").strip()
        result = detection_engine.detect(amenity, circuit)

        if result.fired_ai:
            stats["no_match"] += 1
            stats["standard"] += 1
            if ai_enabled:
                suggestion = bedrock_client.classify_single(amenity, circuit, known_formats)
                if suggestion:
                    result.ai_suggested_format = suggestion.suggested_screen_format
                    result.ai_reasoning = suggestion.reasoning
                    stats["ai_suggestions"] += 1
                    pending_review.append(ReviewItem(
                        type="ai_suggestion",
                        source_string=amenity,
                        circuit=circuit,
                        suggested_format=suggestion.suggested_screen_format,
                        confidence=suggestion.confidence,
                        reasoning=suggestion.reasoning,
                    ))
                else:
                    pending_review.append(ReviewItem(
                        type="ai_suggestion",
                        source_string=amenity,
                        circuit=circuit,
                        suggested_format="Standard",
                        confidence=0.0,
                        reasoning="Bedrock call failed — no match, Standard applied",
                    ))
            else:
                pending_review.append(ReviewItem(
                    type="ai_suggestion",
                    source_string=amenity,
                    circuit=circuit,
                    suggested_format="Standard",
                    confidence=0.0,
                    reasoning="No keyword match — AI trigger disabled",
                ))
        else:
            if result.screen_format != "Standard":
                stats["matched"] += 1
            else:
                stats["standard"] += 1

        all_results.append((amenity, circuit, result))

        # Write output row immediately
        out_row: list = [circuit, amenity, result.screen_format]
        if include_diagnostics:
            out_row += [
                result.detected_keyword or "",
                result.match_source or "",
                result.match_track or "",
                result.confidence,
                result.ai_suggested_format or "",
                result.ai_reasoning or "",
            ]
        ws_out.append(out_row)

        # Highlight AI rows in light yellow
        if result.fired_ai:
            row_num = ws_out.max_row
            for col in range(1, len(out_row) + 1):
                ws_out.cell(row=row_num, column=col).fill = _AI_FILL

        # Update progress every row when AI fired (slow path), else every chunk
        if result.fired_ai or row_idx % _CHUNK_SIZE == 0:
            job = session.get(DetectionJob, job_id)
            if job:
                job.processed = row_idx
            session.commit()

    # Final: flush review items and mark job complete
    total_rows = len(all_results)
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

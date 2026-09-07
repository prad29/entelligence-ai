"""Competitive Calendar Extraction — single Celery task per job.

Unlike deleted_showtime_task's chord-based per-theater fan-out, one calendar
PDF is small enough (a handful of `extractor._chunk_text` calls at most) to
classify + extract + build the xlsx in one task, matching lobby_check_task's
simpler single-job shape rather than the heavier dispatch-window machinery.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlmodel import Session

from app.calendar_extract import batch_io, storage
from app.calendar_extract.classify import classify
from app.calendar_extract.extractor import extract_calendar_rows
from app.calendar_extract.pdf_text import extract_calendar_text
from app.celery_app import celery
from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)

QUEUE = "calendar-extract"


@celery.task(name="app.tasks.calendar_extract_task.process_calendar_job", queue=QUEUE)
def process_calendar_job(job_id: str) -> None:
    from app.models import CalendarExtractJob

    with Session(engine) as session:
        job = session.get(CalendarExtractJob, job_id)
        if job is None:
            logger.warning("calendar_extract_job_not_found job_id=%s", job_id)
            return

        try:
            job.status = "classifying"
            session.add(job)
            session.commit()

            pdf_bytes = storage.get_bytes(job.file_path)
            text = extract_calendar_text(pdf_bytes)

            result = classify(text)
            job.is_release_calendar = result.is_release_calendar
            job.classification_reason = result.reasoning

            if not result.is_release_calendar:
                job.status = "rejected"
                session.add(job)
                session.commit()
                return

            job.status = "processing"
            session.add(job)
            session.commit()

            outcome = extract_calendar_rows(text)
            if not outcome.rows:
                job.status = "failed"
                job.error = "No rows extracted" + (
                    f" ({'; '.join(outcome.errors[:3])})" if outcome.errors else ""
                )
                session.add(job)
                session.commit()
                return

            xlsx_bytes = batch_io.build_output_xlsx(outcome.rows)
            out_key = storage.output_key(job_id)
            storage.put_bytes(out_key, xlsx_bytes)

            job.output_path = out_key
            job.rows_extracted = len(outcome.rows)
            job.model_id = settings.CALENDAR_EXTRACT_MODEL_ID
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            job.ttl = job.completed_at + timedelta(hours=settings.CALENDAR_EXTRACT_JOB_TTL_HOURS)
            if outcome.chunks_failed:
                job.error = (
                    f"{outcome.chunks_failed}/{outcome.chunks_total} chunk(s) failed and were "
                    f"skipped: {'; '.join(outcome.errors[:3])}"
                )
            session.add(job)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("calendar_extract_job_failed job_id=%s", job_id)
            job.status = "failed"
            job.error = str(exc)
            session.add(job)
            session.commit()

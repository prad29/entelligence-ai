"""
Celery tasks for the Mode B agentic *international* batch title-matching
pipeline. Mirrors app.tasks.agentic_match_task exactly, but:

* operates on MovieTitleIntlBatchJob (not MovieTitleBatchJob)
* passes market="international" and the row's own "country" column into
  run_agentic_match, instead of a fixed market/no country
* resolves present_in_db against MovieMasterIntl (not MovieMaster)

Kept as a separate module (not a market branch inside agentic_match_task.py)
to match this codebase's one-artifact-per-feature convention and so a bug or
load spike in international batch processing can never affect the domestic
job model, counters, or queue.

Reuses the same batch_storage (S3), Redis results-hash, atomic counter-bump,
and async .delay() chord-dispatch pattern as the domestic pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from sqlalchemy import update
from sqlmodel import Session, select

from app.celery_app import celery
from app.config import settings

logger = logging.getLogger(__name__)

AGENTIC_QUEUE = "agentic"


def _results_key(job_id: str) -> str:
    return f"batch-intl:{job_id}:results"


def _get_redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL)


def _movie_exists(session: Session, movie_id: int) -> bool:
    from app.models import MovieMasterIntl

    if not movie_id or movie_id <= 0:
        return False
    row = session.exec(select(MovieMasterIntl.id).where(MovieMasterIntl.id == movie_id)).first()
    return row is not None


def _bump_counters(session: Session, job_id: str, **increments: int) -> None:
    from app.models import MovieTitleIntlBatchJob

    values = {
        col: getattr(MovieTitleIntlBatchJob, col) + delta
        for col, delta in increments.items()
    }
    session.execute(
        update(MovieTitleIntlBatchJob)
        .where(MovieTitleIntlBatchJob.id == job_id)
        .values(**values)
    )
    session.commit()


@celery.task(
    bind=True,
    name="app.tasks.agentic_intl_match_task.agentic_intl_batch_row",
    queue=AGENTIC_QUEUE,
    max_retries=2,
    soft_time_limit=settings.AGENTIC_TIMEOUT_SECONDS + 30,
    time_limit=settings.AGENTIC_TIMEOUT_SECONDS + 90,
)
def agentic_intl_batch_row(
    self,
    job_id: str,
    row_index: int,
    title: str,
    show_date: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    country: Optional[str] = None,
    use_poster_vision: bool = False,
) -> None:
    """Process a single international batch row. theater is ALWAYS None,
    matching the domestic batch path (the upload schema has no theater
    column). A single row failing only marks that row failed."""
    from celery.exceptions import Retry

    from app.database import engine
    from app.title_matching import batch_io
    from app.title_matching.agentic import AgenticError
    from app.title_matching.agentic.runner import run_agentic_match
    from app.title_matching import sandbox_semaphore

    holder = None
    try:
        holder = sandbox_semaphore.acquire(timeout=settings.AGENTIC_TIMEOUT_SECONDS + 30)
        try:
            result = run_agentic_match(
                title,
                show_date,
                None,  # theater: always None in the batch path
                ticketing_url,
                use_poster_vision,
                market="international",
                country=country,
            )
        except AgenticError as exc:
            if self.request.retries < self.max_retries:
                logger.warning(
                    "agentic_intl_batch_row retrying job=%s row=%s title=%r err=%s",
                    job_id, row_index, title, exc,
                )
                raise self.retry(exc=exc)
            logger.error(
                "agentic_intl_batch_row exhausted job=%s row=%s title=%r err=%s",
                job_id, row_index, title, exc,
            )
            _record_failed_row(job_id, row_index, str(exc))
            return

        with Session(engine) as session:
            mapped_title, present = batch_io.resolve_present_in_db(
                result, lambda mid: _movie_exists(session, mid)
            )
            row_result = {
                "mapped_title": mapped_title,
                "confidence_score": getattr(result, "confidence", 0) or 0,
                "reasoning": getattr(result, "reasoning", "") or "",
                "present_in_db": present,
            }
            _store_row_result(job_id, row_index, row_result)
            outcome_col = "matched" if present == "Yes" else "no_match"
            _bump_counters(session, job_id, processed=1, **{outcome_col: 1})
    except Retry:
        raise
    except BaseException as exc:  # noqa: BLE001
        logger.exception(
            "agentic_intl_batch_row failed (non-agentic) job=%s row=%s title=%r",
            job_id, row_index, title,
        )
        try:
            _record_failed_row(job_id, row_index, _failure_message(exc))
        except Exception:  # noqa: BLE001 - last-resort: never re-raise from here
            logger.exception(
                "agentic_intl_batch_row: could not even record failed row job=%s row=%s",
                job_id, row_index,
            )
    finally:
        sandbox_semaphore.release(holder)


def _failure_message(exc: BaseException) -> str:
    from celery.exceptions import SoftTimeLimitExceeded

    if isinstance(exc, SoftTimeLimitExceeded):
        return "row timed out (soft time limit exceeded)"
    if isinstance(exc, TimeoutError):
        return f"timed out acquiring a sandbox slot: {exc}"
    text = str(exc).strip()
    return text or f"{type(exc).__name__}"


def _store_row_result(job_id: str, row_index: int, row_result: dict) -> None:
    r = _get_redis()
    r.hset(_results_key(job_id), str(row_index), json.dumps(row_result))


def _record_failed_row(job_id: str, row_index: int, message: str) -> None:
    from app.database import engine
    from app.title_matching import batch_io

    _store_row_result(job_id, row_index, batch_io.failed_row_result(message))
    with Session(engine) as session:
        _bump_counters(session, job_id, processed=1, failed=1)


@celery.task(
    name="app.tasks.agentic_intl_match_task.finalize_intl_batch",
    queue=AGENTIC_QUEUE,
)
def finalize_intl_batch(_row_results, job_id: str) -> None:
    """Chord callback: assemble results, write xlsx, complete the job, clean up.

    Idempotent — a no-op if the job is already completed."""
    from datetime import datetime, timedelta

    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching import batch_io, batch_storage

    with Session(engine) as session:
        job = session.get(MovieTitleIntlBatchJob, job_id)
        if job is None:
            logger.error("finalize_intl_batch: job %s not found", job_id)
            return
        if job.status == "completed":
            logger.info("finalize_intl_batch: job %s already completed, no-op", job_id)
            return

        total = job.total or 0
        upload_key = job.file_path

    contents = batch_storage.get_bytes(upload_key)
    ext = os.path.splitext(upload_key)[1]
    original_headers, rows = batch_io.parse_upload(contents, ext, market="international")

    r = _get_redis()
    raw = r.hgetall(_results_key(job_id))
    stored = {int(k.decode() if isinstance(k, bytes) else k): v for k, v in raw.items()}
    results = []
    for i in range(total):
        val = stored.get(i)
        if val is None:
            results.append(
                batch_io.failed_row_result(
                    "row result missing - task may have crashed without reporting"
                )
            )
        else:
            results.append(json.loads(val.decode() if isinstance(val, bytes) else val))

    xlsx_bytes = batch_io.build_output_xlsx(original_headers, rows, results)

    output_key = batch_storage.output_key(job_id)
    batch_storage.put_bytes(output_key, xlsx_bytes)

    with Session(engine) as session:
        job = session.get(MovieTitleIntlBatchJob, job_id)
        job.status = "completed"
        job.output_path = output_key
        job.ttl = datetime.utcnow() + timedelta(hours=settings.JOB_TTL_HOURS)
        session.add(job)
        session.commit()

    try:
        batch_storage.delete(upload_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_intl_batch: could not remove upload %s: %s", upload_key, exc)
    try:
        r.delete(_results_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_intl_batch: could not delete redis hash for %s: %s", job_id, exc)


@celery.task(
    name="app.tasks.agentic_intl_match_task.dispatch_intl_batch_task",
    queue=AGENTIC_QUEUE,
)
def dispatch_intl_batch_task(job_id: str) -> None:
    """Celery task wrapper around :func:`dispatch_intl_batch`, enqueued by the
    upload endpoint instead of calling it inline (same ALB-timeout rationale
    as dispatch_batch_task in the domestic pipeline)."""
    dispatch_intl_batch(job_id)


def dispatch_intl_batch(job_id: str) -> None:
    """Build and apply the chord of per-row international tasks + finalize callback."""
    from celery import chord, group

    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching import batch_io, batch_storage

    try:
        with Session(engine) as session:
            job = session.get(MovieTitleIntlBatchJob, job_id)
            if job is None:
                raise ValueError(f"dispatch_intl_batch: job {job_id} not found")
            upload_key = job.file_path
            use_poster_vision = job.use_poster_vision

        contents = batch_storage.get_bytes(upload_key)
        ext = os.path.splitext(upload_key)[1]
        _headers, rows = batch_io.parse_upload(contents, ext, market="international")

        with Session(engine) as session:
            session.execute(
                update(MovieTitleIntlBatchJob)
                .where(MovieTitleIntlBatchJob.id == job_id)
                .values(status="processing", total=len(rows))
            )
            session.commit()

        header_map = {h.strip().lower(): h for h in _headers}
        title_key = next(
            header_map[alias] for alias in batch_io.TITLE_COLUMN_ALIASES if alias in header_map
        )
        date_key = header_map.get("show_date")
        url_key = header_map.get("ticketing_url")
        country_key = header_map.get("country")

        row_sigs = [
            agentic_intl_batch_row.s(
                job_id,
                idx,
                row.get(title_key, "") or "",
                row.get(date_key, "") if date_key else None,
                row.get(url_key, "") if url_key else None,
                row.get(country_key, "") if country_key else None,
                use_poster_vision,
            )
            for idx, row in enumerate(rows)
        ]

        chord(group(row_sigs))(finalize_intl_batch.s(job_id))
    except Exception as exc:
        logger.exception("dispatch_intl_batch failed for job %s", job_id)
        try:
            with Session(engine) as session:
                session.execute(
                    update(MovieTitleIntlBatchJob)
                    .where(MovieTitleIntlBatchJob.id == job_id)
                    .values(status="failed", error=str(exc))
                )
                session.commit()
        except Exception:  # noqa: BLE001 - best effort to record failure
            logger.exception("dispatch_intl_batch: could not mark job %s failed", job_id)
        raise

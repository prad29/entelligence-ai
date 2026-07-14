"""
Celery tasks for the Mode B agentic *batch* title-matching pipeline.

Three moving parts:

* :func:`agentic_batch_row` — one Celery task per input row. Runs the sandbox
  match behind a TTL semaphore, resolves ``present_in_db``, stashes the row
  result in a Redis hash, and atomically bumps the job counters. Retries once
  on an agentic error, then falls into the failed-row path so a single bad row
  never aborts the whole batch.
* :func:`finalize_batch` — chord callback. Assembles every row result (filling
  gaps for tasks that crashed without reporting), writes the xlsx output, marks
  the job completed, then cleans up the upload + Redis hash. Idempotent.
* :func:`dispatch_batch` — builds and applies the chord (group of rows +
  finalize callback). Marks the job failed if it can't even dispatch.

Counter updates use server-side ``column = column + 1`` SQL expressions
(NEVER a Python read-modify-write) so concurrent workers can't lose an
increment (see LOCKED product decision #10).
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
OUTPUT_DIR = "/tmp/movie_title_batch_outputs"  # noqa: S108 - matches existing job convention


def _results_key(job_id: str) -> str:
    return f"batch:{job_id}:results"


def _get_redis():
    """Return a redis client (raises if unavailable — callers decide how to handle)."""
    import redis

    return redis.Redis.from_url(settings.REDIS_URL)


def _movie_exists(session: Session, movie_id: int) -> bool:
    from app.models import MovieMaster

    if not movie_id or movie_id <= 0:
        return False
    row = session.exec(select(MovieMaster.id).where(MovieMaster.id == movie_id)).first()
    return row is not None


def _bump_counters(session: Session, job_id: str, **increments: int) -> None:
    """
    Atomically increment one or more MovieTitleBatchJob counter columns using a
    server-side ``col = col + N`` expression. Never a Python read-modify-write.
    """
    from app.models import MovieTitleBatchJob

    values = {
        col: getattr(MovieTitleBatchJob, col) + delta
        for col, delta in increments.items()
    }
    session.execute(
        update(MovieTitleBatchJob)
        .where(MovieTitleBatchJob.id == job_id)
        .values(**values)
    )
    session.commit()


@celery.task(
    bind=True,
    name="app.tasks.agentic_match_task.agentic_batch_row",
    queue=AGENTIC_QUEUE,
    max_retries=2,
    soft_time_limit=settings.AGENTIC_TIMEOUT_SECONDS + 30,
    time_limit=settings.AGENTIC_TIMEOUT_SECONDS + 90,
)
def agentic_batch_row(
    self,
    job_id: str,
    row_index: int,
    title: str,
    show_date: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    use_poster_vision: bool = False,
) -> None:
    """Process a single batch row. theater is ALWAYS None in the batch path.

    The upload schema has no theater column (a deliberate, documented difference
    from the single-match UI, which does pass a theater). A single row failing
    only marks that row failed; it never aborts the batch.
    """
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
            )
        except AgenticError as exc:
            # Retry once; on the final attempt fall through to the failed-row path.
            if self.request.retries < self.max_retries:
                logger.warning(
                    "agentic_batch_row retrying job=%s row=%s title=%r err=%s",
                    job_id, row_index, title, exc,
                )
                raise self.retry(exc=exc)
            logger.error(
                "agentic_batch_row exhausted job=%s row=%s title=%r err=%s",
                job_id, row_index, title, exc,
            )
            _record_failed_row(job_id, row_index, str(exc))
            return

        # Success path: resolve present_in_db against MovieMaster, store, count.
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
    finally:
        sandbox_semaphore.release(holder)


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
    name="app.tasks.agentic_match_task.finalize_batch",
    queue=AGENTIC_QUEUE,
)
def finalize_batch(_row_results, job_id: str) -> None:
    """Chord callback: assemble results, write xlsx, complete the job, clean up.

    Idempotent — a no-op if the job is already completed. Cleanup (deleting the
    upload file + Redis hash) happens ONLY after the output write + commit
    succeed, so a retry after a partial failure (e.g. disk full) still finds its
    inputs intact.

    The first positional arg is the chord's collected group results, which we
    ignore — the authoritative per-row output lives in the Redis hash.
    """
    from datetime import datetime, timedelta

    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching import batch_io

    with Session(engine) as session:
        job = session.get(MovieTitleBatchJob, job_id)
        if job is None:
            logger.error("finalize_batch: job %s not found", job_id)
            return
        if job.status == "completed":
            logger.info("finalize_batch: job %s already completed, no-op", job_id)
            return

        total = job.total or 0
        file_path = job.file_path

    # Recover original headers + rows from the source upload.
    with open(file_path, "rb") as fh:
        contents = fh.read()
    ext = os.path.splitext(file_path)[1]
    original_headers, rows = batch_io.parse_upload(contents, ext)

    # Assemble every row result by index, filling any gap (a task that crashed
    # without reporting) with a failed-row so rows never misalign.
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

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, f"{job_id}_output.xlsx")
    with open(output_path, "wb") as fh:
        fh.write(xlsx_bytes)

    # Mark completed BEFORE any cleanup so a crash after this leaves a retryable
    # (but already-completed -> no-op) job rather than a wedged one.
    with Session(engine) as session:
        job = session.get(MovieTitleBatchJob, job_id)
        job.status = "completed"
        job.output_path = output_path
        job.ttl = datetime.utcnow() + timedelta(hours=settings.JOB_TTL_HOURS)
        session.add(job)
        session.commit()

    # Cleanup only after the output is durably written and the job committed.
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except OSError as exc:
        logger.warning("finalize_batch: could not remove upload %s: %s", file_path, exc)
    try:
        r.delete(_results_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_batch: could not delete redis hash for %s: %s", job_id, exc)


def dispatch_batch(job_id: str) -> None:
    """Build and apply the chord of per-row tasks + finalize callback.

    On any failure before the chord is dispatched (e.g. parse_upload raising),
    mark the job failed so polling clients see it, then re-raise.
    """
    from celery import chord, group

    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching import batch_io

    try:
        with Session(engine) as session:
            job = session.get(MovieTitleBatchJob, job_id)
            if job is None:
                raise ValueError(f"dispatch_batch: job {job_id} not found")
            file_path = job.file_path
            use_poster_vision = job.use_poster_vision

        with open(file_path, "rb") as fh:
            contents = fh.read()
        ext = os.path.splitext(file_path)[1]
        _headers, rows = batch_io.parse_upload(contents, ext)

        with Session(engine) as session:
            session.execute(
                update(MovieTitleBatchJob)
                .where(MovieTitleBatchJob.id == job_id)
                .values(status="processing", total=len(rows))
            )
            session.commit()

        header_map = {h.strip().lower(): h for h in _headers}
        title_key = header_map["movie_title"]
        date_key = header_map.get("show_date")
        url_key = header_map.get("ticketing_url")

        row_sigs = [
            agentic_batch_row.s(
                job_id,
                idx,
                row.get(title_key, "") or "",
                row.get(date_key, "") if date_key else None,
                row.get(url_key, "") if url_key else None,
                use_poster_vision,
            )
            for idx, row in enumerate(rows)
        ]

        chord(group(row_sigs))(finalize_batch.s(job_id))
    except Exception as exc:
        logger.exception("dispatch_batch failed for job %s", job_id)
        try:
            with Session(engine) as session:
                session.execute(
                    update(MovieTitleBatchJob)
                    .where(MovieTitleBatchJob.id == job_id)
                    .values(status="failed", error=str(exc))
                )
                session.commit()
        except Exception:  # noqa: BLE001 - best effort to record failure
            logger.exception("dispatch_batch: could not mark job %s failed", job_id)
        raise

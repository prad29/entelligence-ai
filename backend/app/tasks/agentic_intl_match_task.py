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
and windowed-dispatch/self-refill pattern (Phase 5 — see
enqueue_next_window/scheduler_state below) as the domestic pipeline.
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
from app.title_matching.agentic import limits

logger = logging.getLogger(__name__)

AGENTIC_QUEUE = "agentic"


def _results_key(job_id: str) -> str:
    return f"batch-intl:{job_id}:results"


def _row_args_key(job_id: str) -> str:
    return f"batch-intl:{job_id}:rowargs"


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
    # Raised from 2 — see agentic_match_task.agentic_batch_row's identical
    # comment: a throttle storm now shares this retry budget via the
    # AgenticThrottleError branch below, and shouldn't be able to exhaust
    # what genuine errors also need.
    max_retries=4,
    soft_time_limit=limits.row_soft_time_limit(),
    time_limit=limits.row_time_limit(),
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
    from app.observability.constants import (
        CALLER_PORTAL,
        PATH_AGENTIC_CLI,
        TASK_INTL_MAPPING,
    )
    from app.observability.context import LlmCallContext
    from app.title_matching import batch_io
    from app.title_matching.agentic import AgenticError, AgenticThrottleError
    from app.title_matching.agentic.runner import run_agentic_match
    from app.title_matching import sandbox_semaphore

    holder = None
    try:
        holder = sandbox_semaphore.acquire(timeout=limits.slot_wait_timeout())
        try:
            result = run_agentic_match(
                title,
                show_date,
                None,  # theater: always None in the batch path
                ticketing_url,
                use_poster_vision,
                market="international",
                country=country,
                usage_ctx=LlmCallContext(
                    task_type=TASK_INTL_MAPPING,
                    call_path=PATH_AGENTIC_CLI,
                    caller_type=CALLER_PORTAL,
                    job_id=job_id,
                    job_type="MovieTitleIntlBatchJob",
                ),
            )
        except AgenticThrottleError as exc:
            # MUST be checked before the generic `except AgenticError` below
            # — see agentic_match_task.agentic_batch_row's identical branch.
            if self.request.retries < self.max_retries:
                countdown = limits.throttle_retry_countdown(self.request.retries)
                logger.warning(
                    "agentic_intl_batch_row throttled, retrying job=%s row=%s title=%r "
                    "countdown=%s err=%s",
                    job_id, row_index, title, countdown, exc,
                )
                raise self.retry(exc=exc, countdown=countdown)
            logger.error(
                "agentic_intl_batch_row throttle retries exhausted job=%s row=%s "
                "title=%r err=%s",
                job_id, row_index, title, exc,
            )
            _record_failed_row(job_id, row_index, str(exc))
            return
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
            if _store_row_result(job_id, row_index, row_result):
                outcome_col = "matched" if present == "Yes" else "no_match"
                _bump_counters(session, job_id, processed=1, **{outcome_col: 1})
            else:
                logger.warning(
                    "agentic_intl_batch_row: duplicate row execution ignored, "
                    "not re-bumping counters job=%s row=%s", job_id, row_index,
                )
        _after_row_terminal(job_id)
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


def _store_row_result(job_id: str, row_index: int, row_result: dict) -> bool:
    """Store this row's result under its index, once.

    See agentic_match_task._store_row_result's identical docstring — uses
    Redis ``HSETNX`` so a row executed twice only ever writes its result
    the first time; callers must only bump counters when this returns True.
    """
    r = _get_redis()
    return bool(r.hsetnx(_results_key(job_id), str(row_index), json.dumps(row_result)))


def _record_failed_row(job_id: str, row_index: int, message: str) -> None:
    from app.database import engine
    from app.title_matching import batch_io

    with Session(engine) as session:
        if _store_row_result(job_id, row_index, batch_io.failed_row_result(message)):
            _bump_counters(session, job_id, processed=1, failed=1)
        else:
            logger.warning(
                "agentic_intl_batch_row: duplicate row execution ignored, "
                "not re-bumping counters job=%s row=%s", job_id, row_index,
            )
    _after_row_terminal(job_id)


def _after_row_terminal(job_id: str) -> None:
    """Counter-based completion trigger — see agentic_match_task's identical
    docstring. Call at the end of every TERMINAL row outcome, never on the
    Celery-retry path; never raises. If this row didn't complete the job,
    self-refill this job's dispatch window rather than waiting for the next
    beat tick."""
    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching.dispatch_window import claim_finalize

    try:
        with Session(engine) as session:
            won = claim_finalize(
                session,
                MovieTitleIntlBatchJob,
                job_id,
                completion_predicate=(
                    MovieTitleIntlBatchJob.processed >= MovieTitleIntlBatchJob.total
                ),
            )
        if won:
            finalize_intl_batch.apply_async(args=[None, job_id])
            return
        enqueue_next_window(job_id, settings.AGENTIC_ROUNDROBIN_CHUNK)
    except Exception:  # noqa: BLE001 - must never escape into the row task
        logger.exception("_after_row_terminal failed job=%s", job_id)


@celery.task(
    name="app.tasks.agentic_intl_match_task.finalize_intl_batch",
    queue=AGENTIC_QUEUE,
)
def finalize_intl_batch(_row_results, job_id: str) -> None:
    """Completion callback -- see agentic_match_task.finalize_batch's
    identical docstring (no longer a Celery chord callback; enqueued
    directly by whichever caller wins the counter-based finalize claim).

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
    try:
        r.delete(_row_args_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_intl_batch: could not delete row-args hash for %s: %s", job_id, exc)


def _row_task_args(headers: list[str], rows: list[dict], use_poster_vision: bool) -> list[list]:
    """Build each row's ``agentic_intl_batch_row`` positional args (excluding
    ``job_id``/``row_index``) -- see agentic_match_task's identical helper.
    Includes the international-only ``country`` column."""
    from app.title_matching import batch_io

    header_map = {h.strip().lower(): h for h in headers}
    title_key = next(
        header_map[alias] for alias in batch_io.TITLE_COLUMN_ALIASES if alias in header_map
    )
    date_key = header_map.get("show_date")
    url_key = header_map.get("ticketing_url")
    country_key = header_map.get("country")

    return [
        [
            row.get(title_key, "") or "",
            row.get(date_key, "") if date_key else None,
            row.get(url_key, "") if url_key else None,
            row.get(country_key, "") if country_key else None,
            use_poster_vision,
        ]
        for row in rows
    ]


def _cache_row_args(job_id: str, headers: list[str], rows: list[dict], use_poster_vision: bool) -> None:
    """See agentic_match_task._cache_row_args's identical docstring."""
    args_list = _row_task_args(headers, rows, use_poster_vision)
    if not args_list:
        return
    mapping = {str(idx): json.dumps(args) for idx, args in enumerate(args_list)}
    try:
        r = _get_redis()
        r.hset(_row_args_key(job_id), mapping=mapping)
        r.expire(_row_args_key(job_id), settings.JOB_TTL_HOURS * 3600)
    except Exception as exc:  # noqa: BLE001 - best effort; _row_args_for falls back to S3
        logger.warning("_cache_row_args: failed to cache row args for job=%s: %s", job_id, exc)


def _row_args_for(job_id: str, indices: list[int]) -> dict[int, list]:
    """See agentic_match_task._row_args_for's identical docstring."""
    if not indices:
        return {}

    result: dict[int, list] = {}
    missing = list(indices)
    try:
        r = _get_redis()
        raw = r.hmget(_row_args_key(job_id), [str(i) for i in indices])
        for idx, val in zip(indices, raw):
            if val is not None:
                result[idx] = json.loads(val.decode() if isinstance(val, bytes) else val)
        missing = [i for i in indices if i not in result]
    except Exception as exc:  # noqa: BLE001
        logger.warning("_row_args_for: redis unavailable for job=%s: %s", job_id, exc)
        missing = list(indices)

    if not missing:
        return result

    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching import batch_io, batch_storage

    try:
        with Session(engine) as session:
            job = session.get(MovieTitleIntlBatchJob, job_id)
            if job is None:
                return result
            upload_key = job.file_path
            use_poster_vision = job.use_poster_vision

        contents = batch_storage.get_bytes(upload_key)
        ext = os.path.splitext(upload_key)[1]
        headers, rows = batch_io.parse_upload(contents, ext, market="international")
        args_list = _row_task_args(headers, rows, use_poster_vision)
        _cache_row_args(job_id, headers, rows, use_poster_vision)
        for i in missing:
            if 0 <= i < len(args_list):
                result[i] = args_list[i]
    except Exception as exc:  # noqa: BLE001 - upload gone or unreadable
        logger.warning(
            "_row_args_for: could not recover args for job=%s indices=%s "
            "(upload no longer available?): %s", job_id, missing, exc,
        )
    return result


def enqueue_next_window(job_id: str, limit: int) -> int:
    """See agentic_match_task.enqueue_next_window's identical docstring."""
    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching.dispatch_window import claim_row_window

    if limit <= 0:
        return 0

    with Session(engine) as session:
        frm, to = claim_row_window(session, MovieTitleIntlBatchJob, job_id, limit)
    if to <= frm:
        return 0

    indices = list(range(frm, to))
    args_by_index = _row_args_for(job_id, indices)

    published = 0
    for idx in indices:
        args = args_by_index.get(idx)
        if args is None:
            logger.error(
                "enqueue_next_window: could not recover row args job=%s row=%s "
                "(cache miss + upload gone) -- recording as failed", job_id, idx,
            )
            try:
                _record_failed_row(
                    job_id, idx, "row arguments unrecoverable (upload no longer available)"
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "enqueue_next_window: failed to record unrecoverable row job=%s row=%s",
                    job_id, idx,
                )
            continue
        title, show_date, ticketing_url, country, use_poster_vision = args
        agentic_intl_batch_row.apply_async(
            args=[job_id, idx, title, show_date, ticketing_url, country, use_poster_vision],
            queue=AGENTIC_QUEUE,
        )
        published += 1
    return published


def scheduler_state() -> list:
    """One :class:`app.title_matching.dispatch_window.JobDispatchState` per
    international job currently ``processing`` with outstanding/remaining
    rows -- see agentic_match_task.scheduler_state's identical docstring."""
    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching.dispatch_window import JobDispatchState

    with Session(engine) as session:
        jobs = session.exec(
            select(MovieTitleIntlBatchJob).where(MovieTitleIntlBatchJob.status == "processing")
        ).all()

    states = []
    for job in jobs:
        outstanding = max(job.dispatched - job.processed, 0)
        remaining = max(job.total - job.dispatched, 0)
        if outstanding == 0 and remaining == 0:
            continue
        states.append(
            JobDispatchState(
                kind="international", job_id=job.id, outstanding=outstanding, remaining=remaining
            )
        )
    return states


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
    """Parse the upload, cache row args, and publish an initial bounded
    window of rows -- see agentic_match_task.dispatch_batch's identical
    docstring for the Phase 5 rationale."""
    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.title_matching import batch_io, batch_storage, dispatch_window

    try:
        with Session(engine) as session:
            job = session.get(MovieTitleIntlBatchJob, job_id)
            if job is None:
                raise ValueError(f"dispatch_intl_batch: job {job_id} not found")
            upload_key = job.file_path
            use_poster_vision = job.use_poster_vision

        contents = batch_storage.get_bytes(upload_key)
        ext = os.path.splitext(upload_key)[1]
        headers, rows = batch_io.parse_upload(contents, ext, market="international")

        with Session(engine) as session:
            session.execute(
                update(MovieTitleIntlBatchJob)
                .where(MovieTitleIntlBatchJob.id == job_id)
                .values(status="processing", total=len(rows), dispatched=0)
            )
            session.commit()

        if not rows:
            # Finding #2 -- see agentic_match_task.dispatch_batch's identical
            # empty-job branch.
            finalize_intl_batch.apply_async(args=[None, job_id])
            return

        _cache_row_args(job_id, headers, rows, use_poster_vision)

        limit = len(rows) if celery.conf.task_always_eager else dispatch_window.read_job_window()
        enqueue_next_window(job_id, limit)
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

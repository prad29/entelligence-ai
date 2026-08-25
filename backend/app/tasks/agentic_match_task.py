"""
Celery tasks for the Mode B agentic *batch* title-matching pipeline.

Three moving parts:

* :func:`agentic_batch_row` — one Celery task per input row. Runs the sandbox
  match behind a TTL semaphore, resolves ``present_in_db``, stashes the row
  result in a Redis hash, and atomically bumps the job counters. Retries once
  on an agentic error, then falls into the failed-row path so a single bad row
  never aborts the whole batch.
* :func:`finalize_batch` — completion callback (formerly a chord callback;
  now invoked directly via ``apply_async`` by whichever caller wins the
  counter-based finalize claim). Assembles every row result (filling gaps for
  tasks that crashed without reporting), writes the xlsx output, marks the
  job completed, then cleans up the upload + Redis hashes. Idempotent.
* :func:`dispatch_batch` — parses the upload, caches each row's task args,
  and calls :func:`enqueue_next_window` for an initial bounded window of rows
  rather than publishing the whole job at once (Phase 5 — see
  ``app.title_matching.dispatch_window`` and
  ``app.tasks.agentic_scheduler_task.topup_agentic_queue``). Marks the job
  failed if it can't even dispatch.
* :func:`dispatch_batch_task` — Celery task wrapper the upload endpoint
  enqueues instead of calling ``dispatch_batch`` inline, so the HTTP request
  returns immediately regardless of file size (see its docstring).
* :func:`enqueue_next_window` / :func:`scheduler_state` — the windowed-
  dispatch primitives ``topup_agentic_queue`` (and each row task's own
  self-refill, via ``_after_row_terminal``) drive: claim the next slice of
  this job's undispatched rows and publish them, or report this job's
  current outstanding/remaining counts.

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
from app.title_matching.agentic import limits

logger = logging.getLogger(__name__)

AGENTIC_QUEUE = "agentic"


def _results_key(job_id: str) -> str:
    return f"batch:{job_id}:results"


def _row_args_key(job_id: str) -> str:
    return f"batch:{job_id}:rowargs"


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
    # Raised from 2: a Bedrock throttle storm now consumes retries via the
    # SAME self.request.retries counter as genuine AgenticError retries (see
    # the AgenticThrottleError branch below), and a throttle-heavy stretch
    # shouldn't be able to exhaust the retry budget that real errors also
    # depend on. 4 gives real errors their original 2 retries' worth of
    # headroom even if 1-2 throttle retries also land on this row.
    max_retries=4,
    soft_time_limit=limits.row_soft_time_limit(),
    time_limit=limits.row_time_limit(),
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
    from celery.exceptions import Retry

    from app.database import engine
    from app.observability.constants import (
        CALLER_PORTAL,
        PATH_AGENTIC_CLI,
        TASK_DOMESTIC_MAPPING,
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
                usage_ctx=LlmCallContext(
                    task_type=TASK_DOMESTIC_MAPPING,
                    call_path=PATH_AGENTIC_CLI,
                    caller_type=CALLER_PORTAL,
                    job_id=job_id,
                    job_type="MovieTitleBatchJob",
                ),
            )
        except AgenticThrottleError as exc:
            # MUST be checked before the generic `except AgenticError` below —
            # AgenticThrottleError is a subclass, so ordering matters. A
            # throttle backs off and re-queues the WHOLE row via Celery's own
            # countdown (releasing the sandbox semaphore slot for the entire
            # wait) instead of the tight in-process retry a generic
            # AgenticError gets, since the fix for throttling is time, not a
            # fast resubmission.
            if self.request.retries < self.max_retries:
                countdown = limits.throttle_retry_countdown(self.request.retries)
                logger.warning(
                    "agentic_batch_row throttled, retrying job=%s row=%s title=%r "
                    "countdown=%s err=%s",
                    job_id, row_index, title, countdown, exc,
                )
                raise self.retry(exc=exc, countdown=countdown)
            logger.error(
                "agentic_batch_row throttle retries exhausted job=%s row=%s title=%r err=%s",
                job_id, row_index, title, exc,
            )
            _record_failed_row(job_id, row_index, str(exc))
            return
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
            if _store_row_result(job_id, row_index, row_result):
                outcome_col = "matched" if present == "Yes" else "no_match"
                _bump_counters(session, job_id, processed=1, **{outcome_col: 1})
            else:
                logger.warning(
                    "agentic_batch_row: duplicate row execution ignored, not "
                    "re-bumping counters job=%s row=%s", job_id, row_index,
                )
        _after_row_terminal(job_id)
    except Retry:
        # celery's self.retry() control-flow signal — MUST propagate so the row
        # is rescheduled. Not a failure of this row.
        raise
    except BaseException as exc:  # noqa: BLE001
        # Any other failure (semaphore acquire TimeoutError, SoftTimeLimitExceeded,
        # a DB/Redis error in the success path, or any unexpected exception) must
        # NOT escape the task: this row would never record a terminal outcome,
        # so `processed` would never reach `total` and finalize's counter-based
        # trigger would never fire, wedging the whole job at 'processing'
        # forever. Instead we record the row as failed and return normally,
        # guaranteeing the exit criterion that a single row failure never
        # aborts the batch. (SoftTimeLimitExceeded/KeyboardInterrupt derive
        # from BaseException, hence the broad base catch.)
        logger.exception(
            "agentic_batch_row failed (non-agentic) job=%s row=%s title=%r",
            job_id, row_index, title,
        )
        try:
            _record_failed_row(job_id, row_index, _failure_message(exc))
        except Exception:  # noqa: BLE001 - last-resort: never re-raise from here
            logger.exception(
                "agentic_batch_row: could not even record failed row job=%s row=%s",
                job_id, row_index,
            )
    finally:
        sandbox_semaphore.release(holder)


def _failure_message(exc: BaseException) -> str:
    """Human-readable message for a non-agentic row failure."""
    from celery.exceptions import SoftTimeLimitExceeded

    if isinstance(exc, SoftTimeLimitExceeded):
        return "row timed out (soft time limit exceeded)"
    if isinstance(exc, TimeoutError):
        return f"timed out acquiring a sandbox slot: {exc}"
    text = str(exc).strip()
    return text or f"{type(exc).__name__}"


def _store_row_result(job_id: str, row_index: int, row_result: dict) -> bool:
    """Store this row's result under its index, once.

    Uses Redis ``HSETNX`` so a row executed twice (Celery retry racing a
    redelivery, at-least-once redelivery, a future repair sweep) only ever
    writes its result the first time. Returns True if THIS call was the
    first writer -- callers MUST only bump counters when this is True, or a
    duplicate execution double-counts processed/matched/no_match/failed and
    could double-trigger finalize. Returns False if a result for this index
    already existed.
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
                "agentic_batch_row: duplicate row execution ignored, not "
                "re-bumping counters job=%s row=%s", job_id, row_index,
            )
    _after_row_terminal(job_id)


def _after_row_terminal(job_id: str) -> None:
    """Counter-based completion trigger.

    Call this at the end of every TERMINAL row outcome (success or a
    recorded failure), after the counter bump for that outcome has
    committed -- NEVER on the Celery-retry path (a retried row hasn't
    finished and hasn't incremented ``processed``).

    Never raises: a failure here must not turn a completed row into a
    failed one. If this row didn't complete the job, keep this job's
    dispatch window full via a self-refill rather than waiting for the next
    beat tick (``topup_agentic_queue``) -- see the plan's "Core mechanism"
    section: self-refill is why celery-beat is not a single point of
    failure, since a job still runs to completion at whatever window it had
    even if beat is down.
    """
    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching.dispatch_window import claim_finalize

    try:
        with Session(engine) as session:
            won = claim_finalize(
                session,
                MovieTitleBatchJob,
                job_id,
                completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
            )
        if won:
            finalize_batch.apply_async(args=[None, job_id])
            return
        enqueue_next_window(job_id, settings.AGENTIC_ROUNDROBIN_CHUNK)
    except Exception:  # noqa: BLE001 - must never escape into the row task
        logger.exception("_after_row_terminal failed job=%s", job_id)


@celery.task(
    name="app.tasks.agentic_match_task.finalize_batch",
    queue=AGENTIC_QUEUE,
)
def finalize_batch(_row_results, job_id: str) -> None:
    """Completion callback: assemble results, write xlsx, complete the job,
    clean up. Enqueued directly via ``apply_async(args=[None, job_id])`` by
    whichever caller wins the counter-based finalize claim
    (``_after_row_terminal`` / the beat-scheduled finalize sweep) — no longer
    a Celery chord callback, but the signature is kept ``(_row_results,
    job_id)`` for continuity; ``_row_results`` is always ``None`` now and
    ignored either way — the authoritative per-row output lives in the Redis
    hash.

    Idempotent — a no-op if the job is already completed. Cleanup (deleting the
    upload file + Redis hash) happens ONLY after the output write + commit
    succeed, so a retry after a partial failure (e.g. disk full) still finds its
    inputs intact.
    """
    from datetime import datetime, timedelta

    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching import batch_io, batch_storage

    with Session(engine) as session:
        job = session.get(MovieTitleBatchJob, job_id)
        if job is None:
            logger.error("finalize_batch: job %s not found", job_id)
            return
        if job.status == "completed":
            logger.info("finalize_batch: job %s already completed, no-op", job_id)
            return

        total = job.total or 0
        upload_key = job.file_path

    # Recover original headers + rows from the source upload.
    contents = batch_storage.get_bytes(upload_key)
    ext = os.path.splitext(upload_key)[1]
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

    output_key = batch_storage.output_key(job_id)
    batch_storage.put_bytes(output_key, xlsx_bytes)

    # Mark completed BEFORE any cleanup so a crash after this leaves a retryable
    # (but already-completed -> no-op) job rather than a wedged one.
    with Session(engine) as session:
        job = session.get(MovieTitleBatchJob, job_id)
        job.status = "completed"
        job.output_path = output_key
        job.ttl = datetime.utcnow() + timedelta(hours=settings.JOB_TTL_HOURS)
        session.add(job)
        session.commit()

    # Cleanup only after the output is durably written and the job committed.
    try:
        batch_storage.delete(upload_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_batch: could not remove upload %s: %s", upload_key, exc)
    try:
        r.delete(_results_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_batch: could not delete redis hash for %s: %s", job_id, exc)
    try:
        r.delete(_row_args_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_batch: could not delete row-args hash for %s: %s", job_id, exc)


def _row_task_args(headers: list[str], rows: list[dict], use_poster_vision: bool) -> list[list]:
    """Build each row's ``agentic_batch_row`` positional args (excluding
    ``job_id``/``row_index``, which the caller supplies) from parsed upload
    headers + rows. Raises the same way ``dispatch_batch`` always has if no
    title-column alias is present in the upload -- unchanged validation
    surface, just factored out so both :func:`_cache_row_args` and the
    S3-reparse fallback in :func:`_row_args_for` share one implementation.
    """
    from app.title_matching import batch_io

    header_map = {h.strip().lower(): h for h in headers}
    title_key = next(
        header_map[alias] for alias in batch_io.TITLE_COLUMN_ALIASES if alias in header_map
    )
    date_key = header_map.get("show_date")
    url_key = header_map.get("ticketing_url")

    return [
        [
            row.get(title_key, "") or "",
            row.get(date_key, "") if date_key else None,
            row.get(url_key, "") if url_key else None,
            use_poster_vision,
        ]
        for row in rows
    ]


def _cache_row_args(job_id: str, headers: list[str], rows: list[dict], use_poster_vision: bool) -> None:
    """Cache every row's Celery-task positional args in a Redis hash, once,
    at dispatch time (``batch:{job_id}:rowargs``, TTL ``JOB_TTL_HOURS``).

    Windowed/incremental dispatch (Phase 5) publishes rows one window at a
    time instead of all at once, so re-parsing the uploaded file from S3 on
    every self-refill/beat top-up would be wasteful -- this cache is what
    :func:`enqueue_next_window` reads from instead. Best-effort: a failure
    here (e.g. Redis down) is logged and swallowed, since
    :func:`_row_args_for` has an S3-reparse fallback for exactly this case.
    """
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
    """HMGET the cached row args for these indices.

    On a miss (Redis restarted, hash expired, or simply unreachable),
    re-parses the original upload from S3 and repopulates the cache. If the
    upload itself is gone (job already finalized and cleaned up), returns an
    entry-less mapping for the unrecoverable indices -- callers must fail
    those rows rather than wait forever for args that will never arrive.
    """
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
    except Exception as exc:  # noqa: BLE001 - fall through to the S3 fallback below
        logger.warning("_row_args_for: redis unavailable for job=%s: %s", job_id, exc)
        missing = list(indices)

    if not missing:
        return result

    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching import batch_io, batch_storage

    try:
        with Session(engine) as session:
            job = session.get(MovieTitleBatchJob, job_id)
            if job is None:
                return result
            upload_key = job.file_path
            use_poster_vision = job.use_poster_vision

        contents = batch_storage.get_bytes(upload_key)
        ext = os.path.splitext(upload_key)[1]
        headers, rows = batch_io.parse_upload(contents, ext)
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
    """Claim up to ``limit`` of this job's undispatched rows
    (:func:`app.title_matching.dispatch_window.claim_row_window`) and publish
    them to :func:`agentic_batch_row`. Returns how many were actually
    published.

    A row whose cached args can't be recovered (see :func:`_row_args_for`)
    is recorded as a failed row immediately via the existing failed-row path
    rather than silently never dispatched -- so ``processed`` still
    converges to ``total`` and the job can finalize.
    """
    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching.dispatch_window import claim_row_window

    if limit <= 0:
        return 0

    with Session(engine) as session:
        frm, to = claim_row_window(session, MovieTitleBatchJob, job_id, limit)
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
            except Exception:  # noqa: BLE001 - never let one bad row abort the window
                logger.exception(
                    "enqueue_next_window: failed to record unrecoverable row job=%s row=%s",
                    job_id, idx,
                )
            continue
        title, show_date, ticketing_url, use_poster_vision = args
        agentic_batch_row.apply_async(
            args=[job_id, idx, title, show_date, ticketing_url, use_poster_vision],
            queue=AGENTIC_QUEUE,
        )
        published += 1
    return published


def scheduler_state() -> list:
    """One :class:`app.title_matching.dispatch_window.JobDispatchState` per
    domestic job currently ``processing`` that still has outstanding
    (dispatched-not-yet-processed) or remaining (never-dispatched) rows --
    what :func:`app.tasks.agentic_scheduler_task.topup_agentic_queue` needs
    to fairly top up the shared queue across all three pipelines.
    """
    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching.dispatch_window import JobDispatchState

    with Session(engine) as session:
        jobs = session.exec(
            select(MovieTitleBatchJob).where(MovieTitleBatchJob.status == "processing")
        ).all()

    states = []
    for job in jobs:
        outstanding = max(job.dispatched - job.processed, 0)
        remaining = max(job.total - job.dispatched, 0)
        if outstanding == 0 and remaining == 0:
            continue
        states.append(
            JobDispatchState(kind="domestic", job_id=job.id, outstanding=outstanding, remaining=remaining)
        )
    return states


@celery.task(
    name="app.tasks.agentic_match_task.dispatch_batch_task",
    queue=AGENTIC_QUEUE,
)
def dispatch_batch_task(job_id: str) -> None:
    """Celery task wrapper around :func:`dispatch_batch`.

    The upload endpoint enqueues this instead of calling ``dispatch_batch``
    inline. Re-parsing the file and caching per-row args (plus publishing the
    initial window) is the expensive part of dispatch (network round-trips
    to Redis) — for large files this alone can take longer than the
    ALB/nginx idle timeout if done inside the request. Moving it here means
    the HTTP response returns as soon as the job row + upload are persisted,
    regardless of file size.
    """
    dispatch_batch(job_id)


def dispatch_batch(job_id: str) -> None:
    """Parse the upload, cache row args, and publish an initial bounded
    window of rows (Phase 5 — see ``app.title_matching.dispatch_window``).

    Replaces the old "push the whole job as one chord" dispatch: only an
    initial window is published here; the rest arrive via self-refill
    (``_after_row_terminal``) and the beat-scheduled round-robin top-up
    (``topup_agentic_queue``), which is what makes a big job's rows never
    fully starve a smaller concurrently-active job.

    On any failure before dispatch completes (e.g. parse_upload raising),
    mark the job failed so polling clients see it, then re-raise.
    """
    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.title_matching import batch_io, batch_storage, dispatch_window

    try:
        with Session(engine) as session:
            job = session.get(MovieTitleBatchJob, job_id)
            if job is None:
                raise ValueError(f"dispatch_batch: job {job_id} not found")
            upload_key = job.file_path
            use_poster_vision = job.use_poster_vision

        contents = batch_storage.get_bytes(upload_key)
        ext = os.path.splitext(upload_key)[1]
        headers, rows = batch_io.parse_upload(contents, ext)

        with Session(engine) as session:
            session.execute(
                update(MovieTitleBatchJob)
                .where(MovieTitleBatchJob.id == job_id)
                .values(status="processing", total=len(rows), dispatched=0)
            )
            session.commit()

        if not rows:
            # Finding #2: an empty upload used to complete instantly via
            # chord(group([])) firing its callback with no members. Counter-
            # based finalize never fires on its own for zero rows (nothing
            # ever increments `processed`), so this needs an explicit branch.
            finalize_batch.apply_async(args=[None, job_id])
            return

        _cache_row_args(job_id, headers, rows, use_poster_vision)

        # Eager mode (Celery ALWAYS_EAGER, used by test_batch_e2e.py) has no
        # beat/self-refill loop actually running, so dispatch everything up
        # front -- matches pre-Phase-5 behavior and avoids unbounded
        # self-refill recursion depth (each row would otherwise recursively
        # dispatch the next one inline).
        limit = len(rows) if celery.conf.task_always_eager else dispatch_window.read_job_window()
        enqueue_next_window(job_id, limit)
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

"""
Celery tasks for the external singletitle/batchtitle API
(app/routers/external_title_match.py).

Mirrors the existing agentic_match_task.py windowed-dispatch/row/finalize
pattern (Phase 5 — the chord is gone; see enqueue_next_window/scheduler_state
below), but with durable Postgres row storage (ApiTitleMatchRow, keyed by
client-supplied row_uuid) instead of xlsx output + an ephemeral Redis hash —
this surface needs individually addressable rows for partial retrieval and
row-scoped retry across a job that can run for over an hour. Both this
module and agentic_match_task.py call the same run_agentic_match core, so
matching logic itself never forks.

Unlike domestic/international, ApiTitleMatchRow has no integer `dispatched`
cursor -- its per-row `status` column IS the dispatch state, with a
`dispatched` value (distinct from `pending`/`completed`/`failed`) added in
Phase 5 for exactly this purpose. `dispatched` never leaks into the public
API: the /results endpoint only ever returns rows with status in
(completed, failed), so a row sitting at `pending` or `dispatched` is simply
omitted, same as today.

Runs on the SAME "agentic" queue and sandbox_semaphore as the internal batch
pipeline (a deliberate choice — no dedicated capacity pool for external
traffic; see the plan's Context section).

Counter updates use server-side ``column = column + 1`` SQL expressions
(NEVER a Python read-modify-write), same convention as agentic_match_task.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import update
from sqlmodel import Session, func, select

from app.celery_app import celery
from app.config import settings
from app.title_matching.agentic import limits

logger = logging.getLogger(__name__)

AGENTIC_QUEUE = "agentic"

# How often to poll the Vespa reindex ready-key while db_update=true is
# waiting for the index to catch up, in seconds.
_SYNC_POLL_INTERVAL_SECONDS = 5


def _movie_exists(session: Session, market: str, movie_id: int) -> bool:
    from app.models import MovieMaster, MovieMasterIntl

    if not movie_id or movie_id <= 0:
        return False

    model = MovieMasterIntl if market == "international" else MovieMaster
    row = session.exec(select(model.id).where(model.id == movie_id)).first()
    return row is not None


def _bump_job_counters(session: Session, job_id: str, **increments: int) -> None:
    from app.models import ApiTitleMatchJob

    values = {
        col: getattr(ApiTitleMatchJob, col) + delta
        for col, delta in increments.items()
    }
    session.execute(
        update(ApiTitleMatchJob)
        .where(ApiTitleMatchJob.id == job_id)
        .values(**values)
    )
    session.commit()


@celery.task(
    name="app.tasks.external_match_task.external_dispatch_job_task",
    queue=AGENTIC_QUEUE,
)
def external_dispatch_job_task(job_id: str) -> None:
    """Celery task wrapper around :func:`external_dispatch_job`, enqueued by
    the submission endpoint so the HTTP response returns immediately —
    same rationale as dispatch_batch_task in agentic_match_task.py.
    """
    external_dispatch_job(job_id)


def _wait_for_index_ready(market: str) -> None:
    """Poll the existing Vespa reindex ready-key (set by
    app.tasks.semantic_tasks) up to EXTERNAL_API_SYNC_WAIT_CEILING_SECONDS.

    Replaces a blind fixed-duration sleep: the sync job itself marks
    'completed' before the async Vespa reindex task finishes, so waiting on
    a timer risks matching against a stale/partial index. On ceiling
    breach, logs a warning and proceeds anyway rather than failing the
    job — sync is meant to precede fan-out on a best-effort basis, not gate
    it indefinitely.
    """
    import time

    from app.tasks.semantic_tasks import _READY_KEY, _READY_KEY_INTL

    ready_key = _READY_KEY_INTL if market == "international" else _READY_KEY

    try:
        import redis

        r = redis.Redis.from_url(settings.REDIS_URL)
    except Exception as exc:
        logger.warning("external_dispatch: cannot reach redis to poll %s: %s", ready_key, exc)
        return

    deadline = time.monotonic() + settings.EXTERNAL_API_SYNC_WAIT_CEILING_SECONDS
    while time.monotonic() < deadline:
        try:
            if r.get(ready_key):
                return
        except Exception as exc:
            logger.warning("external_dispatch: error polling %s: %s", ready_key, exc)
            return
        time.sleep(_SYNC_POLL_INTERVAL_SECONDS)

    logger.warning(
        "external_dispatch: ready-key %s not set within %ss ceiling — proceeding anyway",
        ready_key, settings.EXTERNAL_API_SYNC_WAIT_CEILING_SECONDS,
    )


def _run_sync_inline(market: str) -> None:
    """Run the existing master-DB sync task inline (blocking), reusing its
    logic unchanged rather than duplicating the MySQL fetch/upsert path.

    Celery's `.apply()` (not `.delay()`/`.apply_async()`) executes the task
    body synchronously in the calling worker process and returns only once
    it's done — exactly what's needed here, since matching must not start
    until the sync (and, best-effort, the reindex) has caught up.
    """
    from app.database import engine
    from app.models import MovieMasterSyncJob

    with Session(engine) as session:
        sync_job = MovieMasterSyncJob(market=market)
        session.add(sync_job)
        session.commit()
        sync_job_id = sync_job.id

    if market == "international":
        from app.tasks.prod_db_sync_task import sync_movie_master_intl_task
        sync_movie_master_intl_task.apply(args=[sync_job_id])
    else:
        from app.tasks.prod_db_sync_task import sync_movie_master_task
        sync_movie_master_task.apply(args=[sync_job_id])

    with Session(engine) as session:
        sync_job = session.get(MovieMasterSyncJob, sync_job_id)
        if sync_job is not None and sync_job.status == "failed":
            raise RuntimeError(f"master DB sync failed: {sync_job.error}")


def enqueue_next_window(job_id: str, limit: int) -> int:
    """Claim up to ``limit`` of this job's ``pending`` rows and publish them
    to :func:`external_match_row`. Returns how many were actually published.

    External has no dispatch cursor (Phase 4 deliberately didn't add one --
    ``ApiTitleMatchRow.status`` already tracks dispatch state per row), so
    this claims via a per-row guarded ``UPDATE ... WHERE id=:row_id AND
    status='pending'`` -- one statement per candidate row, keeping only
    rowcount==1 winners -- rather than a single cursor CAS. ``limit`` is
    single digits and tests run on SQLite, so per-row guards are correct on
    both without needing ``FOR UPDATE SKIP LOCKED``.

    Only dispatches while the job is in the ``processing`` phase -- rows
    exist (status='pending') from submission time onward, but must NOT be
    published while a ``db_update=true`` job is still ``syncing``.
    """
    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow

    if limit <= 0:
        return 0

    with Session(engine) as session:
        job = session.get(ApiTitleMatchJob, job_id)
        if job is None or job.phase != "processing":
            return 0

        candidate_ids = session.exec(
            select(ApiTitleMatchRow.id)
            .where(ApiTitleMatchRow.job_id == job_id)
            .where(ApiTitleMatchRow.status == "pending")
            .limit(limit)
        ).all()

        won_ids = []
        for row_id in candidate_ids:
            result = session.execute(
                update(ApiTitleMatchRow)
                .where(ApiTitleMatchRow.id == row_id)
                .where(ApiTitleMatchRow.status == "pending")
                .values(status="dispatched", updated_at=datetime.utcnow())
            )
            if result.rowcount == 1:
                won_ids.append(row_id)
        session.commit()

    for row_id in won_ids:
        external_match_row.apply_async(args=[job_id, row_id], queue=AGENTIC_QUEUE)
    return len(won_ids)


def scheduler_state() -> list:
    """One :class:`app.title_matching.dispatch_window.JobDispatchState` per
    external job currently ``processing`` with outstanding/remaining rows.

    ``outstanding`` = count of rows with ``status='dispatched'``;
    ``remaining`` = count of rows with ``status='pending'`` -- external's
    per-row ``status`` IS its dispatch state, unlike domestic/international's
    integer cursor.
    """
    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow
    from app.title_matching.dispatch_window import JobDispatchState

    states = []
    with Session(engine) as session:
        jobs = session.exec(
            select(ApiTitleMatchJob).where(ApiTitleMatchJob.phase == "processing")
        ).all()
        for job in jobs:
            outstanding = session.exec(
                select(func.count())
                .select_from(ApiTitleMatchRow)
                .where(ApiTitleMatchRow.job_id == job.id)
                .where(ApiTitleMatchRow.status == "dispatched")
            ).one()
            remaining = session.exec(
                select(func.count())
                .select_from(ApiTitleMatchRow)
                .where(ApiTitleMatchRow.job_id == job.id)
                .where(ApiTitleMatchRow.status == "pending")
            ).one()
            if outstanding == 0 and remaining == 0:
                continue
            states.append(
                JobDispatchState(
                    kind="external", job_id=job.id, outstanding=int(outstanding), remaining=int(remaining)
                )
            )
    return states


def external_dispatch_job(job_id: str) -> None:
    """Parse/sync the job, then publish an initial bounded window of rows
    (Phase 5 — see ``app.title_matching.dispatch_window``), optionally
    preceded by a blocking master-DB sync.

    Replaces the old "build the whole chord" dispatch: only an initial
    window of rows is published here; the rest arrive via self-refill
    (``_after_row_terminal``) and the beat-scheduled round-robin top-up
    (``topup_agentic_queue``).

    On any failure before dispatch completes, marks the job failed so
    polling clients see it — mirrors dispatch_batch's except block.
    """
    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow
    from app.title_matching import dispatch_window

    try:
        with Session(engine) as session:
            job = session.get(ApiTitleMatchJob, job_id)
            if job is None:
                raise ValueError(f"external_dispatch_job: job {job_id} not found")
            market = job.market
            db_update = job.db_update

        if db_update:
            with Session(engine) as session:
                session.execute(
                    update(ApiTitleMatchJob).where(ApiTitleMatchJob.id == job_id).values(phase="syncing")
                )
                session.commit()

            _run_sync_inline(market)
            _wait_for_index_ready(market)

        with Session(engine) as session:
            total_pending = session.exec(
                select(func.count())
                .select_from(ApiTitleMatchRow)
                .where(ApiTitleMatchRow.job_id == job_id)
                .where(ApiTitleMatchRow.status == "pending")
            ).one()

            session.execute(
                update(ApiTitleMatchJob)
                .where(ApiTitleMatchJob.id == job_id)
                .values(phase="processing", started_at=datetime.utcnow())
            )
            session.commit()

        if total_pending == 0:
            # Finding #2's external-shaped equivalent: a submission with no
            # pending rows never lands a terminal row for
            # _after_row_terminal's NOT EXISTS predicate to notice
            # organically -- finalize directly.
            external_finalize_job.apply_async(args=[None, job_id])
            return

        limit = (
            int(total_pending)
            if celery.conf.task_always_eager
            else dispatch_window.read_job_window()
        )
        enqueue_next_window(job_id, limit)

    except Exception as exc:
        logger.exception("external_dispatch_job failed for job %s", job_id)
        try:
            with Session(engine) as session:
                session.execute(
                    update(ApiTitleMatchJob)
                    .where(ApiTitleMatchJob.id == job_id)
                    .values(phase="failed", error=str(exc))
                )
                session.commit()
        except Exception:  # noqa: BLE001 - best effort to record failure
            logger.exception("external_dispatch_job: could not mark job %s failed", job_id)
        raise


@celery.task(
    bind=True,
    name="app.tasks.external_match_task.external_match_row",
    queue=AGENTIC_QUEUE,
    # NOT raised in Phase 1 (unlike the two internal-batch row tasks) — this
    # is deliberately still tied to EXTERNAL_API_ROW_MAX_ATTEMPTS, which also
    # governs the /retry endpoint's own attempt-cap predicate
    # (external_retry_rows_task's `attempts < EXTERNAL_API_ROW_MAX_ATTEMPTS`
    # filter) and the is_retry/attempts counter bookkeeping below (finding
    # #3 in the plan) — decoupling this number without touching that whole
    # bookkeeping scheme is explicitly out of scope for this phase. A
    # throttle retry (see the AgenticThrottleError branch below) does NOT
    # touch row.attempts either way, so it doesn't change is_retry semantics
    # even while sharing this smaller retry budget.
    max_retries=settings.EXTERNAL_API_ROW_MAX_ATTEMPTS - 1,
    soft_time_limit=limits.row_soft_time_limit(),
    time_limit=limits.row_time_limit(),
)
def external_match_row(self, job_id: str, row_id: int) -> None:
    """Process a single ApiTitleMatchRow. theater is always None — the
    external contract has no theater field, matching the internal batch
    path's convention.
    """
    from celery.exceptions import Retry

    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow
    from app.observability.constants import (
        CALLER_EXTERNAL_API,
        PATH_AGENTIC_CLI,
        TASK_DOMESTIC_MAPPING,
        TASK_INTL_MAPPING,
    )
    from app.observability.context import LlmCallContext
    from app.title_matching import batch_io
    from app.title_matching.agentic import AgenticError, AgenticThrottleError
    from app.title_matching.agentic.runner import run_agentic_match
    from app.title_matching import sandbox_semaphore

    with Session(engine) as session:
        row = session.get(ApiTitleMatchRow, row_id)
        if row is None:
            logger.error("external_match_row: row %s not found for job %s", row_id, job_id)
            return
        job = session.get(ApiTitleMatchJob, job_id)
        market = job.market if job is not None else "domestic"
        # Read inside the existing session block — the only place this job row
        # is loaded, so per-key cost attribution (spec §3) costs no extra query.
        api_key_id = job.api_key_id if job is not None else None
        input_data = json.loads(row.input_json)
        # attempts > 0 means a prior attempt already counted this row into
        # rows_processed (and, on failure, rows_failed) — this run must
        # adjust those counters instead of blindly re-incrementing them,
        # or a retried row inflates rows_processed past rows_total and
        # rows_failed never clears, permanently stranding the job at
        # completed_with_errors even after every row eventually succeeds.
        is_retry = row.attempts > 0

    title = input_data.get("movie_title", "") or ""
    show_date = input_data.get("show_date")
    ticketing_url = input_data.get("ticketing_url")
    country = input_data.get("country")

    holder = None
    try:
        holder = sandbox_semaphore.acquire(timeout=limits.slot_wait_timeout())
        try:
            result = run_agentic_match(
                title,
                show_date,
                None,  # theater: not part of the external contract
                ticketing_url,
                market=market,
                country=country,
                usage_ctx=LlmCallContext(
                    task_type=(
                        TASK_DOMESTIC_MAPPING if market == "domestic" else TASK_INTL_MAPPING
                    ),
                    call_path=PATH_AGENTIC_CLI,
                    caller_type=CALLER_EXTERNAL_API,
                    api_key_id=api_key_id,
                    job_id=job_id,
                    job_type="ApiTitleMatchJob",
                ),
            )
        except AgenticThrottleError as exc:
            # MUST be checked before the generic `except AgenticError` below
            # — same ordering rationale as the internal batch row tasks.
            # Does NOT touch row.attempts/is_retry bookkeeping (see the
            # decorator comment above) — self.retry() here is pure Celery
            # control flow, not a recorded row attempt.
            if self.request.retries < self.max_retries:
                countdown = limits.throttle_retry_countdown(self.request.retries)
                logger.warning(
                    "external_match_row throttled, retrying job=%s row=%s title=%r "
                    "countdown=%s err=%s",
                    job_id, row_id, title, countdown, exc,
                )
                raise self.retry(exc=exc, countdown=countdown)
            logger.error(
                "external_match_row throttle retries exhausted job=%s row=%s title=%r err=%s",
                job_id, row_id, title, exc,
            )
            _record_failed_row(job_id, row_id, str(exc), is_retry=is_retry)
            return
        except AgenticError as exc:
            if self.request.retries < self.max_retries:
                logger.warning(
                    "external_match_row retrying job=%s row=%s title=%r err=%s",
                    job_id, row_id, title, exc,
                )
                raise self.retry(exc=exc)
            logger.error(
                "external_match_row exhausted job=%s row=%s title=%r err=%s",
                job_id, row_id, title, exc,
            )
            _record_failed_row(job_id, row_id, str(exc), is_retry=is_retry)
            return

        with Session(engine) as session:
            mapped_title, present = batch_io.resolve_present_in_db(
                result, lambda mid: _movie_exists(session, market, mid)
            )
            db_row = session.get(ApiTitleMatchRow, row_id)
            db_row.status = "completed"
            db_row.mapped_title = mapped_title
            db_row.confidence = getattr(result, "confidence", 0) or 0
            db_row.reasoning = getattr(result, "reasoning", "") or ""
            db_row.present_in_db = present == "Yes"
            db_row.attempts += 1
            db_row.updated_at = datetime.utcnow()
            session.add(db_row)
            session.commit()

            outcome_col = "rows_matched" if present == "Yes" else "rows_no_match"
            if is_retry:
                # This row was already counted into rows_processed (and
                # rows_failed) on its prior failed attempt — only clear the
                # failure, don't re-count rows_processed.
                _bump_job_counters(session, job_id, rows_failed=-1, **{outcome_col: 1})
            else:
                _bump_job_counters(session, job_id, rows_processed=1, **{outcome_col: 1})
        _after_row_terminal(job_id)
    except Retry:
        # celery's self.retry() control-flow signal — MUST propagate so the
        # row is rescheduled. Not a failure of this row.
        raise
    except BaseException as exc:  # noqa: BLE001
        # Any other failure must NOT escape the task: this row would never
        # reach a terminal status, so _after_row_terminal's "zero rows left
        # non-terminal" predicate would never be satisfied and
        # external_finalize_job would never be claimed, wedging the whole
        # job at 'processing' forever. Same rationale as agentic_batch_row.
        logger.exception(
            "external_match_row failed (non-agentic) job=%s row=%s title=%r",
            job_id, row_id, title,
        )
        try:
            _record_failed_row(job_id, row_id, _failure_message(exc), is_retry=is_retry)
        except Exception:  # noqa: BLE001 - last-resort: never re-raise from here
            logger.exception(
                "external_match_row: could not even record failed row job=%s row=%s",
                job_id, row_id,
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


def _record_failed_row(job_id: str, row_id: int, message: str, *, is_retry: bool = False) -> None:
    from app.database import engine
    from app.models import ApiTitleMatchRow

    with Session(engine) as session:
        row = session.get(ApiTitleMatchRow, row_id)
        if row is not None:
            row.status = "failed"
            row.error = message
            row.attempts += 1
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
        # A retried row was already counted into rows_processed/rows_failed
        # on its prior failed attempt — failing again must not double-count
        # either counter.
        if not is_retry:
            _bump_job_counters(session, job_id, rows_processed=1, rows_failed=1)
    _after_row_terminal(job_id)


def _remaining_row_count(session: Session, job_id: str) -> int:
    """Count of this job's rows not yet in a terminal state.

    External's completion predicate is genuinely NOT "processed == total"
    (finding #3 in the plan): ``external_match_row`` deliberately does not
    re-increment ``rows_processed`` on a retried row, so on a ``/retry`` run
    ``rows_processed == rows_total`` can already be true before the retried
    rows even finish. Completion here means "no row left non-terminal",
    checked directly against ``ApiTitleMatchRow.status`` rather than any
    counter.
    """
    from sqlalchemy import func

    from app.models import ApiTitleMatchRow

    return session.exec(
        select(func.count())
        .select_from(ApiTitleMatchRow)
        .where(ApiTitleMatchRow.job_id == job_id)
        .where(ApiTitleMatchRow.status.notin_(("completed", "failed")))
    ).one()


def _after_row_terminal(job_id: str) -> None:
    """Counter-based completion trigger.

    Call this at the end of every TERMINAL row outcome (success or a
    recorded failure) — NEVER on the Celery-retry path.

    Unlike the domestic/international pipelines, this is NOT a counter
    equality check (finding #3) — it claims finalize only when zero rows
    remain in a non-terminal state, re-checked atomically inside the same
    conditional UPDATE that performs the claim (never a separate
    read-then-decide step, which could race a concurrently-finishing row).

    Never raises. If this row didn't complete the job (or the claim lost a
    race to a concurrently-finishing row), self-refill this job's dispatch
    window rather than waiting for the next beat tick.
    """
    from sqlalchemy import exists

    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow
    from app.title_matching.dispatch_window import claim_finalize

    try:
        won = False
        with Session(engine) as session:
            # Cheap pre-check to skip the UPDATE attempt when rows are
            # obviously still outstanding. The atomicity guarantee itself
            # comes from the NOT EXISTS predicate embedded in the UPDATE
            # below (re-evaluated by the DB at claim time), not from this
            # check.
            if _remaining_row_count(session, job_id) == 0:
                no_rows_outstanding = ~exists().where(
                    ApiTitleMatchRow.job_id == job_id,
                    ApiTitleMatchRow.status.notin_(("completed", "failed")),
                )
                won = claim_finalize(
                    session,
                    ApiTitleMatchJob,
                    job_id,
                    completion_predicate=no_rows_outstanding,
                )
        if won:
            external_finalize_job.apply_async(args=[None, job_id])
            return
        enqueue_next_window(job_id, settings.AGENTIC_ROUNDROBIN_CHUNK)
    except Exception:  # noqa: BLE001 - must never escape into the row task
        logger.exception("_after_row_terminal failed job=%s", job_id)


@celery.task(
    name="app.tasks.external_match_task.external_finalize_job",
    queue=AGENTIC_QUEUE,
)
def external_finalize_job(_row_results, job_id: str) -> None:
    """Completion callback -- see agentic_match_task.finalize_batch's
    identical docstring re: no longer a Celery chord callback. Recomputes
    the job's terminal phase from current row counts and stamps
    completed_at/ttl. Idempotent — a no-op if the job is already in a
    terminal phase.

    Per product decision: a row that exhausts its retries is marked failed
    and the job moves on — job phase reflects current row state
    (COMPLETED once rows_failed == 0, else COMPLETED_WITH_ERRORS), not
    history, so a later retry that clears all failures flips the phase back
    to COMPLETED.
    """
    from app.database import engine
    from app.models import ApiTitleMatchJob

    with Session(engine) as session:
        job = session.get(ApiTitleMatchJob, job_id)
        if job is None:
            logger.error("external_finalize_job: job %s not found", job_id)
            return
        if job.phase in ("completed", "completed_with_errors", "failed"):
            logger.info("external_finalize_job: job %s already terminal, no-op", job_id)
            return

        job.phase = "completed" if job.rows_failed == 0 else "completed_with_errors"
        job.completed_at = datetime.utcnow()
        job.ttl = datetime.utcnow() + timedelta(hours=settings.EXTERNAL_API_JOB_TTL_HOURS)
        session.add(job)
        session.commit()


@celery.task(
    name="app.tasks.external_match_task.external_retry_rows_task",
    queue=AGENTIC_QUEUE,
)
def external_retry_rows_task(job_id: str, row_uuids: list[str]) -> None:
    """Re-run only the named failed rows within an existing job.

    Rows already at the attempt cap (EXTERNAL_API_ROW_MAX_ATTEMPTS) are left
    'failed' and skipped rather than retried again. Publishes the flipped
    rows via :func:`enqueue_next_window` (Phase 5) instead of building a new
    chord.
    """
    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow
    from app.title_matching import dispatch_window

    with Session(engine) as session:
        candidates = session.exec(
            select(ApiTitleMatchRow)
            .where(ApiTitleMatchRow.job_id == job_id)
            .where(ApiTitleMatchRow.row_uuid.in_(row_uuids))
            .where(ApiTitleMatchRow.status == "failed")
            .where(ApiTitleMatchRow.attempts < settings.EXTERNAL_API_ROW_MAX_ATTEMPTS)
        ).all()

        if not candidates:
            logger.info("external_retry_rows_task: no retryable rows for job %s", job_id)
            return

        for row in candidates:
            row.status = "pending"
            row.updated_at = datetime.utcnow()
            session.add(row)

        # Clear finalize_claimed_at in the SAME transaction that flips these
        # rows back to 'pending': the job may have already finalized once
        # (claiming finalize_claimed_at) before this retry ran, and without
        # clearing it here, _after_row_terminal's conditional-UPDATE claim
        # would find finalize_claimed_at already set and refuse to ever
        # finalize this job again, even once the retried rows complete.
        session.execute(
            update(ApiTitleMatchJob)
            .where(ApiTitleMatchJob.id == job_id)
            .values(phase="processing", finalize_claimed_at=None)
        )
        session.commit()

    limit = (
        len(candidates)
        if celery.conf.task_always_eager
        else dispatch_window.read_job_window()
    )
    enqueue_next_window(job_id, limit)

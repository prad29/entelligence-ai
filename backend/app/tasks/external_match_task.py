"""
Celery tasks for the external singletitle/batchtitle API
(app/routers/external_title_match.py).

Mirrors the existing agentic_match_task.py dispatch/row/finalize chord
pattern, but with durable Postgres row storage (ApiTitleMatchRow, keyed by
client-supplied row_uuid) instead of xlsx output + an ephemeral Redis hash —
this surface needs individually addressable rows for partial retrieval and
row-scoped retry across a job that can run for over an hour. Both this
module and agentic_match_task.py call the same run_agentic_match core, so
matching logic itself never forks.

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
from sqlmodel import Session, select

from app.celery_app import celery
from app.config import settings

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


def external_dispatch_job(job_id: str) -> None:
    """Build and apply the chord of per-row tasks + finalize callback for an
    ApiTitleMatchJob, optionally preceded by a blocking master-DB sync.

    On any failure before the chord is dispatched, marks the job failed so
    polling clients see it — mirrors dispatch_batch's except block.
    """
    from celery import chord, group

    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow

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
            rows = session.exec(
                select(ApiTitleMatchRow)
                .where(ApiTitleMatchRow.job_id == job_id)
                .where(ApiTitleMatchRow.status == "pending")
            ).all()
            row_ids = [r.id for r in rows]

            session.execute(
                update(ApiTitleMatchJob)
                .where(ApiTitleMatchJob.id == job_id)
                .values(phase="processing", started_at=datetime.utcnow())
            )
            session.commit()

        row_sigs = [external_match_row.s(job_id, row_id) for row_id in row_ids]
        chord(group(row_sigs))(external_finalize_job.s(job_id))

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
    max_retries=settings.EXTERNAL_API_ROW_MAX_ATTEMPTS - 1,
    soft_time_limit=settings.AGENTIC_TIMEOUT_SECONDS + 30,
    time_limit=settings.AGENTIC_TIMEOUT_SECONDS + 90,
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
    from app.title_matching.agentic import AgenticError
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
        holder = sandbox_semaphore.acquire(timeout=settings.AGENTIC_TIMEOUT_SECONDS + 30)
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
    except Retry:
        # celery's self.retry() control-flow signal — MUST propagate so the
        # row is rescheduled. Not a failure of this row.
        raise
    except BaseException as exc:  # noqa: BLE001
        # Any other failure must NOT escape the task: an uncaught exception
        # fails this chord header task, and the chord's callback only fires
        # when ALL header tasks succeed — so a single escaping error would
        # leave external_finalize_job un-run and wedge the whole job at
        # 'processing' forever. Same rationale as agentic_batch_row.
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
        if is_retry:
            return
        _bump_job_counters(session, job_id, rows_processed=1, rows_failed=1)


@celery.task(
    name="app.tasks.external_match_task.external_finalize_job",
    queue=AGENTIC_QUEUE,
)
def external_finalize_job(_row_results, job_id: str) -> None:
    """Chord callback: recompute the job's terminal phase from current row
    counts and stamp completed_at/ttl. Idempotent — a no-op if the job is
    already in a terminal phase.

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
    'failed' and skipped rather than retried again.
    """
    from celery import chord, group

    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow

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

        row_ids = []
        for row in candidates:
            row.status = "pending"
            row.updated_at = datetime.utcnow()
            session.add(row)
            row_ids.append(row.id)

        session.execute(
            update(ApiTitleMatchJob).where(ApiTitleMatchJob.id == job_id).values(phase="processing")
        )
        session.commit()

    row_sigs = [external_match_row.s(job_id, row_id) for row_id in row_ids]
    chord(group(row_sigs))(external_finalize_job.s(job_id))

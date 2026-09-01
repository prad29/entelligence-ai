"""Celery tasks for /api/v1/lobby-check (cinema-lobby marketing-material
image extraction, Qwen 3-VL on Bedrock).

Structurally mirrors external_match_task.py's windowed-dispatch/row/finalize
pattern, but on lobby-check's OWN dedicated queue/pool — deliberately NOT
joined to the shared "agentic" queue/topup_agentic_queue fair-dispatch pool
(see docs/plans/2026-09-01-lobby-check-design.md §4.1): that machinery is
sized for the domestic/international/external title-matching pipelines, and
joining it would shrink their fair-share windows to fund a pipeline that
consumes none of their worker slots.

Fair-share window sizing below reimplements dispatch_window.py's
target_queue_depth/compute_job_window FORMULAS locally (LOBBY_CHECK_*
settings, a live DB count of active jobs instead of a Redis-cached value)
rather than generalizing that module — at the confirmed ~600 images/day
volume, a COUNT query per dispatch/self-refill call is negligible; Redis
caching only earns its complexity at the agentic pipelines' much higher call
volume. See the design doc's §4.7/phase 7 for the generalization that would
be worth doing if lobby-check's volume ever grows to warrant it.

Counter updates use server-side ``column = column + 1`` SQL expressions
(NEVER a Python read-modify-write), same convention as external_match_task.py.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlmodel import Session, func, select

from app.celery_app import celery
from app.config import settings
from app.lobby_check import limits

logger = logging.getLogger(__name__)

LOBBY_CHECK_QUEUE = "lobby-check"


def _bump_job_counters(session: Session, job_id: str, **increments: int) -> None:
    from app.models import LobbyCheckJob

    values = {
        col: getattr(LobbyCheckJob, col) + delta
        for col, delta in increments.items()
    }
    session.execute(
        update(LobbyCheckJob).where(LobbyCheckJob.id == job_id).values(**values)
    )
    session.commit()


def _row_needs_review(rec: dict) -> bool:
    fields = (
        "confidence_movie_title", "confidence_material_type",
        "confidence_material_quantity", "confidence_material_condition",
    )
    try:
        values = [float(rec[f]) for f in fields if rec.get(f) is not None]
    except (TypeError, ValueError):
        return True
    return bool(values) and min(values) < settings.LOBBY_CHECK_REVIEW_CONFIDENCE_THRESHOLD


def _target_queue_depth() -> int:
    if settings.LOBBY_CHECK_QUEUE_TARGET_DEPTH:
        return settings.LOBBY_CHECK_QUEUE_TARGET_DEPTH
    return 2 * settings.LOBBY_CHECK_MAX_CONCURRENCY


def _compute_job_window(active_jobs: int) -> int:
    """Per-job standing dispatch window: target depth divided evenly across
    active jobs, floored at LOBBY_CHECK_JOB_WINDOW_MIN. Mirrors
    dispatch_window.compute_job_window's formula (see module docstring for
    why this isn't a call to that function)."""
    depth = _target_queue_depth()
    if active_jobs <= 1:
        return depth
    return max(settings.LOBBY_CHECK_JOB_WINDOW_MIN, depth // active_jobs)


def _current_window(session: Session) -> int:
    from app.models import LobbyCheckJob

    active = session.exec(
        select(func.count())
        .select_from(LobbyCheckJob)
        .where(LobbyCheckJob.phase == "processing")
    ).one()
    return _compute_job_window(int(active) or 1)


@celery.task(
    name="app.tasks.lobby_check_task.lobby_check_dispatch_job_task",
    queue=LOBBY_CHECK_QUEUE,
)
def lobby_check_dispatch_job_task(job_id: str) -> None:
    """Celery task wrapper around :func:`lobby_check_dispatch_job`, enqueued
    by the submission endpoint so the HTTP response returns immediately."""
    lobby_check_dispatch_job(job_id)


def enqueue_next_window(job_id: str, limit: int) -> int:
    """Claim up to ``limit`` of this job's ``pending`` rows and publish them
    to :func:`lobby_check_row`. Returns how many were actually published.

    Per-row guarded ``UPDATE ... WHERE id=:row_id AND status='pending'``
    (mirrors external_match_task.enqueue_next_window) rather than a cursor
    CAS — LobbyCheckRow.status IS the dispatch state, and ``limit`` is
    single digits, so per-row guards are correct without SKIP LOCKED.

    Only dispatches while the job is ``processing`` — a defensive no-op
    otherwise (e.g. a self-refill racing a job that just finalized).
    """
    from app.database import engine
    from app.models import LobbyCheckJob, LobbyCheckRow

    if limit <= 0:
        return 0

    with Session(engine) as session:
        job = session.get(LobbyCheckJob, job_id)
        if job is None or job.phase != "processing":
            return 0

        candidate_ids = session.exec(
            select(LobbyCheckRow.id)
            .where(LobbyCheckRow.job_id == job_id)
            .where(LobbyCheckRow.status == "pending")
            .limit(limit)
        ).all()

        won_ids = []
        for row_id in candidate_ids:
            result = session.execute(
                update(LobbyCheckRow)
                .where(LobbyCheckRow.id == row_id)
                .where(LobbyCheckRow.status == "pending")
                .values(status="dispatched", updated_at=datetime.utcnow())
            )
            if result.rowcount == 1:
                won_ids.append(row_id)
        session.commit()

    for row_id in won_ids:
        lobby_check_row.apply_async(args=[job_id, row_id], queue=LOBBY_CHECK_QUEUE)
    return len(won_ids)


def scheduler_state() -> list:
    """One title_matching.dispatch_window.JobDispatchState per lobby-check
    job currently ``processing`` with outstanding/remaining rows. Not
    consumed anywhere yet (no beat top-up task in this phase — see design
    doc phase 7); kept so a follow-up can wire it in without touching this
    module again."""
    from app.database import engine
    from app.models import LobbyCheckJob, LobbyCheckRow
    from app.title_matching.dispatch_window import JobDispatchState

    states = []
    with Session(engine) as session:
        jobs = session.exec(
            select(LobbyCheckJob).where(LobbyCheckJob.phase == "processing")
        ).all()
        for job in jobs:
            outstanding = session.exec(
                select(func.count())
                .select_from(LobbyCheckRow)
                .where(LobbyCheckRow.job_id == job.id)
                .where(LobbyCheckRow.status == "dispatched")
            ).one()
            remaining = session.exec(
                select(func.count())
                .select_from(LobbyCheckRow)
                .where(LobbyCheckRow.job_id == job.id)
                .where(LobbyCheckRow.status == "pending")
            ).one()
            if outstanding == 0 and remaining == 0:
                continue
            states.append(
                JobDispatchState(
                    kind="lobby_check", job_id=job.id,
                    outstanding=int(outstanding), remaining=int(remaining),
                )
            )
    return states


def lobby_check_dispatch_job(job_id: str) -> None:
    """Publish an initial fair-share window of rows. The rest arrive via
    self-refill (:func:`_after_row_terminal`) as rows complete — no beat
    top-up in this phase (see module docstring).

    On any failure before dispatch completes, marks the job failed so
    polling clients see it.
    """
    from app.database import engine
    from app.models import LobbyCheckJob, LobbyCheckRow

    try:
        with Session(engine) as session:
            job = session.get(LobbyCheckJob, job_id)
            if job is None:
                raise ValueError(f"lobby_check_dispatch_job: job {job_id} not found")

            total_pending = session.exec(
                select(func.count())
                .select_from(LobbyCheckRow)
                .where(LobbyCheckRow.job_id == job_id)
                .where(LobbyCheckRow.status == "pending")
            ).one()

            session.execute(
                update(LobbyCheckJob)
                .where(LobbyCheckJob.id == job_id)
                .values(phase="processing", started_at=datetime.utcnow())
            )
            session.commit()

            if total_pending == 0:
                # A submission with zero rows never lands a terminal row for
                # _after_row_terminal's NOT EXISTS predicate to notice
                # organically -- finalize directly.
                lobby_check_finalize_job.apply_async(args=[None, job_id])
                return

            limit = (
                int(total_pending)
                if celery.conf.task_always_eager
                else _current_window(session)
            )

        enqueue_next_window(job_id, limit)

    except Exception as exc:
        logger.exception("lobby_check_dispatch_job failed for job %s", job_id)
        try:
            with Session(engine) as session:
                session.execute(
                    update(LobbyCheckJob)
                    .where(LobbyCheckJob.id == job_id)
                    .values(phase="failed", error=str(exc))
                )
                session.commit()
        except Exception:  # noqa: BLE001 - best effort to record failure
            logger.exception("lobby_check_dispatch_job: could not mark job %s failed", job_id)
        raise


@celery.task(
    bind=True,
    name="app.tasks.lobby_check_task.lobby_check_row",
    queue=LOBBY_CHECK_QUEUE,
    max_retries=settings.LOBBY_CHECK_ROW_MAX_ATTEMPTS - 1,
    soft_time_limit=limits.row_soft_time_limit(),
    time_limit=limits.row_time_limit(),
)
def lobby_check_row(self, job_id: str, row_id: int) -> None:
    """Process a single LobbyCheckRow: fetch its image, extract, persist.

    Image-fetch failures and schema failures are deterministic (fail
    immediately, no Celery retry); throttle/transient Bedrock failures are
    retried via self.retry up to LOBBY_CHECK_ROW_MAX_ATTEMPTS — see
    lobby_check/errors.py.
    """
    from celery.exceptions import Retry

    from app.database import engine
    from app.lobby_check import images
    from app.lobby_check.errors import (
        LobbyCheckImageError,
        LobbyCheckSchemaError,
        LobbyCheckThrottleError,
        LobbyCheckTransientError,
    )
    from app.lobby_check.extractor import extract_material_record
    from app.models import LobbyCheckJob, LobbyCheckRow
    from app.observability.constants import (
        CALLER_EXTERNAL_API,
        PATH_BEDROCK_CONVERSE,
        TASK_LOBBY_CHECK,
    )
    from app.observability.context import LlmCallContext

    with Session(engine) as session:
        row = session.get(LobbyCheckRow, row_id)
        if row is None:
            logger.error("lobby_check_row: row %s not found for job %s", row_id, job_id)
            return
        job = session.get(LobbyCheckJob, job_id)
        api_key_id = job.api_key_id if job is not None else None
        image_url = row.image_url
        # attempts > 0 means a prior attempt already counted this row into
        # rows_processed/rows_failed -- this run must adjust those counters
        # instead of blindly re-incrementing them (mirrors
        # external_match_task's is_retry bookkeeping exactly).
        is_retry = row.attempts > 0

    try:
        try:
            image_bytes = images.fetch_image(image_url)
            framing, _, _ = images.image_framing(image_bytes)
        except LobbyCheckImageError as exc:
            _record_failed_row(job_id, row_id, str(exc), is_retry=is_retry)
            return

        try:
            result = extract_material_record(
                image_bytes,
                framing,
                usage_ctx=LlmCallContext(
                    task_type=TASK_LOBBY_CHECK,
                    call_path=PATH_BEDROCK_CONVERSE,
                    caller_type=CALLER_EXTERNAL_API,
                    api_key_id=api_key_id,
                    job_id=job_id,
                    job_type="LobbyCheckJob",
                ),
            )
        except LobbyCheckThrottleError as exc:
            if self.request.retries < self.max_retries:
                countdown = settings.LOBBY_CHECK_THROTTLE_BACKOFF_SECONDS * (2 ** self.request.retries)
                logger.warning(
                    "lobby_check_row throttled, retrying job=%s row=%s countdown=%s err=%s",
                    job_id, row_id, countdown, exc,
                )
                raise self.retry(exc=exc, countdown=countdown)
            logger.error(
                "lobby_check_row throttle retries exhausted job=%s row=%s err=%s",
                job_id, row_id, exc,
            )
            _record_failed_row(job_id, row_id, str(exc), is_retry=is_retry)
            return
        except LobbyCheckTransientError as exc:
            if self.request.retries < self.max_retries:
                logger.warning(
                    "lobby_check_row transient failure, retrying job=%s row=%s err=%s",
                    job_id, row_id, exc,
                )
                raise self.retry(exc=exc)
            logger.error(
                "lobby_check_row transient retries exhausted job=%s row=%s err=%s",
                job_id, row_id, exc,
            )
            _record_failed_row(job_id, row_id, str(exc), is_retry=is_retry)
            return
        except LobbyCheckSchemaError as exc:
            # Deterministic -- never retried, same rationale as the image
            # error branch above.
            _record_failed_row(job_id, row_id, str(exc), is_retry=is_retry)
            return

        with Session(engine) as session:
            rec = result.record
            db_row = session.get(LobbyCheckRow, row_id)
            db_row.status = "completed"
            db_row.movie_title = (rec.get("movie_title") or "").strip()
            db_row.confidence_movie_title = rec.get("confidence_movie_title")
            db_row.material_type = rec.get("material_type")
            db_row.confidence_material_type = rec.get("confidence_material_type")
            db_row.material_quantity = rec.get("material_quantity")
            db_row.confidence_material_quantity = rec.get("confidence_material_quantity")
            db_row.material_condition = rec.get("material_condition")
            db_row.confidence_material_condition = rec.get("confidence_material_condition")
            db_row.visual_notes = (rec.get("visual_notes") or "").strip()
            db_row.defects_json = json.dumps(rec.get("defects") or [])
            db_row.defect_evidence = (rec.get("defect_evidence") or "").strip()
            db_row.condition_conflict = result.condition_conflict
            db_row.framing = result.framing
            db_row.model_id = settings.LOBBY_CHECK_MODEL_ID
            db_row.input_tokens = result.input_tokens
            db_row.output_tokens = result.output_tokens
            db_row.cost_usd = result.cost_usd
            db_row.latency_ms = result.latency_ms
            db_row.parse_retries = result.parse_retries
            db_row.attempts += 1
            db_row.updated_at = datetime.utcnow()
            session.add(db_row)
            session.commit()

            increments = (
                {"rows_failed": -1, "rows_succeeded": 1} if is_retry
                else {"rows_processed": 1, "rows_succeeded": 1}
            )
            if _row_needs_review(rec):
                increments["rows_needs_review"] = 1
            _bump_job_counters(session, job_id, **increments)
        _after_row_terminal(job_id)
    except Retry:
        # celery's self.retry() control-flow signal -- MUST propagate so the
        # row is rescheduled. Not a failure of this row.
        raise
    except BaseException as exc:  # noqa: BLE001
        # Any other failure must NOT escape the task: this row would never
        # reach a terminal status, so _after_row_terminal's "zero rows left
        # non-terminal" predicate would never be satisfied and the job would
        # wedge at 'processing' forever.
        logger.exception("lobby_check_row failed (unexpected) job=%s row=%s", job_id, row_id)
        try:
            _record_failed_row(job_id, row_id, _failure_message(exc), is_retry=is_retry)
        except Exception:  # noqa: BLE001 - last-resort: never re-raise from here
            logger.exception(
                "lobby_check_row: could not even record failed row job=%s row=%s", job_id, row_id
            )


def _failure_message(exc: BaseException) -> str:
    from celery.exceptions import SoftTimeLimitExceeded

    if isinstance(exc, SoftTimeLimitExceeded):
        return "row timed out (soft time limit exceeded)"
    text = str(exc).strip()
    return text or f"{type(exc).__name__}"


def _record_failed_row(job_id: str, row_id: int, message: str, *, is_retry: bool = False) -> None:
    from app.database import engine
    from app.models import LobbyCheckRow

    with Session(engine) as session:
        row = session.get(LobbyCheckRow, row_id)
        if row is not None:
            row.status = "failed"
            row.error = message
            row.attempts += 1
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
        # A retried row was already counted into rows_processed/rows_failed
        # on its prior failed attempt -- failing again must not double-count
        # either counter.
        if not is_retry:
            _bump_job_counters(session, job_id, rows_processed=1, rows_failed=1)
    _after_row_terminal(job_id)


def _remaining_row_count(session: Session, job_id: str) -> int:
    """Count of this job's rows not yet in a terminal state. Completion here
    means "no row left non-terminal", checked directly against
    LobbyCheckRow.status rather than any counter -- same rationale as
    external_match_task._remaining_row_count (a retried row does not
    re-increment rows_processed, so a counter-equality check can already be
    satisfied before the retried row even finishes)."""
    from app.models import LobbyCheckRow

    return session.exec(
        select(func.count())
        .select_from(LobbyCheckRow)
        .where(LobbyCheckRow.job_id == job_id)
        .where(LobbyCheckRow.status.notin_(("completed", "failed")))
    ).one()


def _after_row_terminal(job_id: str) -> None:
    """Counter-based completion trigger. Call this at the end of every
    TERMINAL row outcome (success or a recorded failure) -- NEVER on the
    Celery-retry path.

    Never raises. If this row didn't complete the job (or the claim lost a
    race to a concurrently-finishing row), self-refill this job's dispatch
    window rather than waiting for a beat tick that doesn't exist in this
    phase.
    """
    from sqlalchemy import exists

    from app.database import engine
    from app.models import LobbyCheckJob, LobbyCheckRow
    from app.title_matching.dispatch_window import claim_finalize

    try:
        won = False
        with Session(engine) as session:
            if _remaining_row_count(session, job_id) == 0:
                no_rows_outstanding = ~exists().where(
                    LobbyCheckRow.job_id == job_id,
                    LobbyCheckRow.status.notin_(("completed", "failed")),
                )
                won = claim_finalize(
                    session, LobbyCheckJob, job_id, completion_predicate=no_rows_outstanding
                )
        if won:
            lobby_check_finalize_job.apply_async(args=[None, job_id])
            return
        enqueue_next_window(job_id, settings.LOBBY_CHECK_ROUNDROBIN_CHUNK)
    except Exception:  # noqa: BLE001 - must never escape into the row task
        logger.exception("_after_row_terminal failed job=%s", job_id)


@celery.task(
    name="app.tasks.lobby_check_task.lobby_check_finalize_job",
    queue=LOBBY_CHECK_QUEUE,
)
def lobby_check_finalize_job(_row_results, job_id: str) -> None:
    """Completion callback -- recomputes the job's terminal phase from
    current row counts and stamps completed_at/ttl. Idempotent -- a no-op
    if the job is already in a terminal phase."""
    from app.database import engine
    from app.models import LobbyCheckJob

    with Session(engine) as session:
        job = session.get(LobbyCheckJob, job_id)
        if job is None:
            logger.error("lobby_check_finalize_job: job %s not found", job_id)
            return
        if job.phase in ("completed", "completed_with_errors", "failed"):
            logger.info("lobby_check_finalize_job: job %s already terminal, no-op", job_id)
            return

        job.phase = "completed" if job.rows_failed == 0 else "completed_with_errors"
        job.completed_at = datetime.utcnow()
        job.ttl = datetime.utcnow() + timedelta(hours=settings.LOBBY_CHECK_JOB_TTL_HOURS)
        session.add(job)
        session.commit()

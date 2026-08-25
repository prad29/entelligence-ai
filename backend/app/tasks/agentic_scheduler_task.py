"""
Celery scheduler task(s) spanning the shared agentic pool — domestic
(``agentic_match_task.py``), international (``agentic_intl_match_task.py``),
and external-API (``external_match_task.py``) title matching all route to the
same ``"agentic"`` Celery queue and the same ``sandbox_semaphore`` Redis
semaphore. This module is the one component that looks across all three.

Phase 2 (pool observability) is everything currently in this file:
``queue_depth()`` and the beat-scheduled ``sample_agentic_pool()``, which log
one structured-ish line per tick covering broker queue depth, live
semaphore-holder count, the configured concurrency cap, and how many jobs
(across all three pipelines) are currently ``processing``. This is pure
read-only sampling — no dispatch, retry, or concurrency behavior changes here.

Phase 5 adds ``topup_agentic_queue``, the round-robin top-up beat task:
fairness across all three pipelines is exactly what it needs too — hence the
module name, chosen ahead of that work.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlmodel import Session, func, select

from app.celery_app import celery
from app.config import settings
from app.tasks.agentic_match_task import AGENTIC_QUEUE
from app.title_matching import dispatch_window
from app.title_matching.sandbox_semaphore import holder_count

logger = logging.getLogger(__name__)

# Redis keys used only by topup_agentic_queue (Phase 5).
_TICK_LOCK_KEY = "agentic:sched:tick"
_ROTATION_KEY = "agentic:sched:rotation"


def _get_redis():
    """Return a redis client reachable at settings.REDIS_URL, or None if the
    library/connection is unavailable. Mirrors sandbox_semaphore._get_redis's
    fail-open pattern — every caller in this module must tolerate a Redis
    outage without raising."""
    try:
        import redis  # local import so importing this module never hard-requires redis

        client = redis.Redis.from_url(settings.REDIS_URL)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 - any failure means "unknown", not "zero"
        logger.warning("agentic_scheduler redis unavailable: %s", exc)
        return None


def queue_depth(queue: str = AGENTIC_QUEUE, *, redis_client=None) -> Optional[int]:
    """Length of the Celery/Redis broker list backing ``queue``. Returns
    ``None`` (never raises) if Redis is unreachable.

    This repo's Celery is configured with the plain ``redis://`` transport
    (``broker=settings.REDIS_URL`` in celery_app.py) and no queue-name prefix
    or custom routing key — so the broker list backing a queue is a plain
    Redis LIST whose key is the queue name itself, and ``LLEN queue`` is
    exactly that list's length, no translation needed.

    This is an APPROXIMATION: it counts only messages still sitting in the
    broker list. Messages already reserved by a worker (with
    worker_prefetch_multiplier=1, roughly one per live worker process) are not
    included, and the list may also carry non-row messages (dispatch/finalize
    tasks). Treat this as a soak-observation signal only — never as an
    admission-control input.
    """
    client = redis_client if redis_client is not None else _get_redis()
    if client is None:
        return None
    try:
        return int(client.llen(queue))
    except Exception as exc:  # noqa: BLE001 - sampler must never raise
        logger.warning("agentic_scheduler queue_depth failed queue=%s error=%s", queue, exc)
        return None


def _active_job_count() -> int:
    """Sum of jobs currently ``processing`` across all three pipelines that
    share this pool. Fairness in later phases spans domestic, international,
    and external-API together, so this metric does too, rather than only
    covering one kind.

    Raises on a DB error — the caller (``sample_agentic_pool``) decides how
    to degrade, same as it does for the Redis-backed samples.
    """
    from app.database import engine
    from app.models import ApiTitleMatchJob, MovieTitleBatchJob, MovieTitleIntlBatchJob

    with Session(engine) as session:
        domestic = session.exec(
            select(func.count())
            .select_from(MovieTitleBatchJob)
            .where(MovieTitleBatchJob.status == "processing")
        ).one()
        intl = session.exec(
            select(func.count())
            .select_from(MovieTitleIntlBatchJob)
            .where(MovieTitleIntlBatchJob.status == "processing")
        ).one()
        external = session.exec(
            select(func.count())
            .select_from(ApiTitleMatchJob)
            .where(ApiTitleMatchJob.phase == "processing")
        ).one()
    return int(domestic) + int(intl) + int(external)


@celery.task(name="app.tasks.agentic_scheduler_task.sample_agentic_pool", ignore_result=True)
def sample_agentic_pool() -> dict:
    """Beat-scheduled sampler for pool utilization: queue depth, live
    semaphore-holder count, and how many jobs are currently active. Fills the
    one gap the cost-observability platform doesn't cover. Never raises —
    logging failures here must not affect the beat schedule or any real job.
    """
    try:
        depth = queue_depth()
        holders = holder_count()
        try:
            active = _active_job_count()
        except Exception as exc:  # noqa: BLE001 - a DB hiccup must not break the sample
            logger.warning("agentic_pool_sample active_jobs query failed: %s", exc)
            active = None

        # NOTE on extra= vs. plain %-formatting into the message string:
        # app.logging_config.configure_logging() (which installs
        # StructuredFormatter, the only thing in this repo that reads a
        # LogRecord's `extra` allow-list) is called ONLY from app/main.py —
        # the FastAPI process. celery_app.py never calls it, and there is no
        # celeryd_init/worker_process_init (or any other) signal handler
        # anywhere in this repo that installs it for celery-worker/
        # celery-beat processes either. So Celery worker/beat logs go through
        # Celery's own default logging setup, not StructuredFormatter — any
        # `extra={...}` kwarg passed to logger.info() here would sit on the
        # LogRecord unused and never render in the actual log output.
        # usage_rollup_task.py — the existing beat-scheduled task from this
        # same cost-observability platform — confirms this is the real,
        # working convention already in use: it logs via %s-interpolation
        # into the message string, never `extra=`. This line follows that
        # same convention rather than introducing a new, silently-broken one.
        logger.info(
            "agentic_pool_sample queue_depth=%s semaphore_holders=%s max_concurrency=%s active_jobs=%s",
            depth, holders, settings.AGENTIC_BATCH_MAX_CONCURRENCY, active,
        )
        return {
            "queue_depth": depth,
            "semaphore_holders": holders,
            "max_concurrency": settings.AGENTIC_BATCH_MAX_CONCURRENCY,
            "active_jobs": active,
            "ok": True,
        }
    except Exception as exc:  # noqa: BLE001 - beat schedule must never see an exception
        logger.warning("agentic_pool_sample failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Phase 5 — round-robin top-up (the fairness fix)
# ---------------------------------------------------------------------------

_KIND_ORDER = ("domestic", "international", "external")


def _pipeline_modules():
    """Local import so importing this module at Celery app build time never
    creates an import cycle with the three task modules (which themselves
    import ``app.celery_app``)."""
    from app.tasks import agentic_intl_match_task, agentic_match_task, external_match_task

    return {
        "domestic": agentic_match_task,
        "international": agentic_intl_match_task,
        "external": external_match_task,
    }


def _acquire_tick_lock(*, redis_client=None) -> bool:
    """Best-effort ``SET NX EX`` lock so overlapping beat ticks skip rather
    than double-work. PURELY an efficiency optimization: correctness against
    double-dispatch comes from ``claim_row_window``'s CAS (domestic/intl) and
    the per-row guarded UPDATE (external), never from this lock. A bug here,
    or Redis simply being unreachable, fails OPEN (returns True) so a lock
    outage can never block the real fairness mechanism -- it can only cost a
    little duplicate log-line/query work on an overlapping tick.
    """
    client = redis_client if redis_client is not None else _get_redis()
    if client is None:
        return True
    try:
        ttl = max(int(settings.AGENTIC_SCHED_TICK_SECONDS * 3), 5)
        return bool(client.set(_TICK_LOCK_KEY, uuid.uuid4().hex, nx=True, ex=ttl))
    except Exception as exc:  # noqa: BLE001 - fail open
        logger.warning("topup_agentic_queue: tick lock unavailable, proceeding: %s", exc)
        return True


def _next_rotation_offset(n: int, *, redis_client=None) -> int:
    """Rotate which job starts each round-robin pass, tick over tick, so
    fairness holds even when the total per-tick budget is tighter than total
    deficit (otherwise the same job(s) at the front of a fixed ordering
    would always get served first). Falls back to 0 (no rotation) if Redis
    is unreachable -- degrades to "always start from the same job", which is
    still correct, just less fair under a tight budget.
    """
    if n <= 0:
        return 0
    client = redis_client if redis_client is not None else _get_redis()
    if client is None:
        return 0
    try:
        val = client.incr(_ROTATION_KEY)
        client.expire(_ROTATION_KEY, 3600)
        return int(val) % n
    except Exception as exc:  # noqa: BLE001
        logger.warning("topup_agentic_queue: rotation counter unavailable: %s", exc)
        return 0


def _gather_states() -> list:
    """``scheduler_state()`` from all three pipelines. A failure in one
    pipeline's query must not block topping up the other two."""
    states = []
    for kind, module in _pipeline_modules().items():
        try:
            states.extend(module.scheduler_state())
        except Exception as exc:  # noqa: BLE001
            logger.warning("topup_agentic_queue: scheduler_state failed for %s: %s", kind, exc)
    return states


def _sweep_stuck_domestic() -> int:
    """Repair path: a domestic job fully dispatched AND fully processed but
    never claimed for finalize (e.g. a worker died between the counter bump
    and the claim attempt). Not the primary finalize trigger -- that's
    ``_after_row_terminal`` -- this only catches what it missed."""
    from app.database import engine
    from app.models import MovieTitleBatchJob
    from app.tasks.agentic_match_task import finalize_batch
    from app.title_matching.dispatch_window import claim_finalize

    claimed = 0
    with Session(engine) as session:
        stuck = session.exec(
            select(MovieTitleBatchJob)
            .where(MovieTitleBatchJob.status == "processing")
            .where(MovieTitleBatchJob.finalize_claimed_at.is_(None))
            .where(MovieTitleBatchJob.dispatched >= MovieTitleBatchJob.total)
            .where(MovieTitleBatchJob.processed >= MovieTitleBatchJob.total)
        ).all()
        for job in stuck:
            won = claim_finalize(
                session, MovieTitleBatchJob, job.id,
                completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
            )
            if won:
                claimed += 1
                finalize_batch.apply_async(args=[None, job.id])
    return claimed


def _sweep_stuck_intl() -> int:
    """See ``_sweep_stuck_domestic``'s identical docstring, international."""
    from app.database import engine
    from app.models import MovieTitleIntlBatchJob
    from app.tasks.agentic_intl_match_task import finalize_intl_batch
    from app.title_matching.dispatch_window import claim_finalize

    claimed = 0
    with Session(engine) as session:
        stuck = session.exec(
            select(MovieTitleIntlBatchJob)
            .where(MovieTitleIntlBatchJob.status == "processing")
            .where(MovieTitleIntlBatchJob.finalize_claimed_at.is_(None))
            .where(MovieTitleIntlBatchJob.dispatched >= MovieTitleIntlBatchJob.total)
            .where(MovieTitleIntlBatchJob.processed >= MovieTitleIntlBatchJob.total)
        ).all()
        for job in stuck:
            won = claim_finalize(
                session, MovieTitleIntlBatchJob, job.id,
                completion_predicate=(
                    MovieTitleIntlBatchJob.processed >= MovieTitleIntlBatchJob.total
                ),
            )
            if won:
                claimed += 1
                finalize_intl_batch.apply_async(args=[None, job.id])
    return claimed


def _sweep_stuck_external() -> int:
    """See ``_sweep_stuck_domestic``'s docstring -- external's shape is "zero
    rows left in a non-terminal state" rather than a counter comparison
    (finding #3), matching ``external_match_task._after_row_terminal``."""
    from sqlalchemy import exists

    from app.database import engine
    from app.models import ApiTitleMatchJob, ApiTitleMatchRow
    from app.tasks.external_match_task import external_finalize_job
    from app.title_matching.dispatch_window import claim_finalize

    claimed = 0
    with Session(engine) as session:
        candidates = session.exec(
            select(ApiTitleMatchJob)
            .where(ApiTitleMatchJob.phase == "processing")
            .where(ApiTitleMatchJob.finalize_claimed_at.is_(None))
        ).all()
        for job in candidates:
            remaining = session.exec(
                select(func.count())
                .select_from(ApiTitleMatchRow)
                .where(ApiTitleMatchRow.job_id == job.id)
                .where(ApiTitleMatchRow.status.notin_(("completed", "failed")))
            ).one()
            if remaining > 0:
                continue
            no_rows_outstanding = ~exists().where(
                ApiTitleMatchRow.job_id == job.id,
                ApiTitleMatchRow.status.notin_(("completed", "failed")),
            )
            won = claim_finalize(
                session, ApiTitleMatchJob, job.id, completion_predicate=no_rows_outstanding
            )
            if won:
                claimed += 1
                external_finalize_job.apply_async(args=[None, job.id])
    return claimed


def _log_stalls(states: list) -> None:
    """Log-only stall detection (no alerting — explicitly out of scope): a
    job fully dispatched (``remaining == 0``) but still with rows in flight
    (``outstanding > 0``) for longer than ``AGENTIC_STALL_WARN_SECONDS``.

    None of the job models carry a per-state "when did this become fully
    dispatched" timestamp, so this uses the closest existing proxy for each
    kind (documented as an approximation, not exact stall duration) rather
    than adding new columns just for a log line: domestic/international's
    ``created_at`` (job creation), external's ``started_at`` (set when the
    job actually enters ``processing``, closer to the truth) falling back to
    ``created_at``.
    """
    from datetime import datetime

    from app.database import engine
    from app.models import ApiTitleMatchJob, MovieTitleBatchJob, MovieTitleIntlBatchJob

    threshold = settings.AGENTIC_STALL_WARN_SECONDS
    now = datetime.utcnow()

    stalled_ids = {(s.kind, s.job_id) for s in states if s.remaining == 0 and s.outstanding > 0}
    if not stalled_ids:
        return

    try:
        with Session(engine) as session:
            for kind, job_id in stalled_ids:
                if kind == "domestic":
                    job = session.get(MovieTitleBatchJob, job_id)
                    since = job.created_at if job else None
                elif kind == "international":
                    job = session.get(MovieTitleIntlBatchJob, job_id)
                    since = job.created_at if job else None
                else:
                    job = session.get(ApiTitleMatchJob, job_id)
                    since = (job.started_at or job.created_at) if job else None
                if since is None:
                    continue
                age = (now - since).total_seconds()
                if age > threshold:
                    logger.warning(
                        "agentic_topup stall_warning kind=%s job=%s age_seconds=%.0f "
                        "threshold=%s (fully dispatched, still processing)",
                        kind, job_id, age, threshold,
                    )
    except Exception as exc:  # noqa: BLE001 - stall logging must never break the tick
        logger.warning("topup_agentic_queue: stall check failed: %s", exc)


def _round_robin_topup(states: list, window: int) -> dict:
    """Publish one ``AGENTIC_ROUNDROBIN_CHUNK``-row chunk per job per
    rotation pass, cycling through every job with remaining deficit
    (``window - outstanding``, capped at that job's own ``remaining``) until
    no job has deficit left or a full pass makes zero progress.

    CRITICAL: this does NOT give one job its whole deficit before moving to
    the next. The broker is strict FIFO, so publish order is execution
    order -- a large per-job chunk before rotating would reconstruct exactly
    the head-of-line blocking this phase exists to remove.
    """
    modules = _pipeline_modules()

    deficits: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for st in states:
        if st.remaining <= 0:
            continue
        deficit = min(max(window - st.outstanding, 0), st.remaining)
        if deficit <= 0:
            continue
        deficits[(st.kind, st.job_id)] = deficit
        order.append((st.kind, st.job_id))

    pushed = {kind: 0 for kind in modules}
    if not order:
        return pushed

    offset = _next_rotation_offset(len(order))
    order = order[offset:] + order[:offset]

    chunk = max(settings.AGENTIC_ROUNDROBIN_CHUNK, 1)
    progress = True
    while progress and any(v > 0 for v in deficits.values()):
        progress = False
        for kind, job_id in order:
            remaining_deficit = deficits.get((kind, job_id), 0)
            if remaining_deficit <= 0:
                continue
            n = modules[kind].enqueue_next_window(job_id, min(chunk, remaining_deficit))
            if n > 0:
                pushed[kind] += n
                deficits[(kind, job_id)] -= n
                progress = True
            else:
                # Nothing left to claim for this job right now (raced by
                # something else, or already fully dispatched) -- stop
                # retrying it this tick rather than spinning.
                deficits[(kind, job_id)] = 0
    return pushed


@celery.task(name="app.tasks.agentic_scheduler_task.topup_agentic_queue", ignore_result=True)
def topup_agentic_queue() -> dict:
    """Beat-scheduled round-robin top-up across every active agentic job —
    domestic, international, and external together, since they share one
    queue/worker/semaphore (see ``sample_agentic_pool``'s ``_active_job_count``
    for the same cross-pipeline precedent).

    1. Gather ``scheduler_state()`` from all three pipelines.
    2. Compute the current fair-share job window from the total active-job
       count and cache it (``dispatch_window.write_job_window``) for every
       pipeline's self-refill to read.
    3. Sweep for jobs that are fully dispatched + fully processed but whose
       finalize was somehow never triggered (repair path, not the primary
       trigger).
    4. Round-robin top-up: publish one chunk per job per rotation pass across
       every job with remaining deficit.
    5. Log one summary line. Never raises — a beat-task exception must not
       break the schedule.
    """
    try:
        if not _acquire_tick_lock():
            logger.info("topup_agentic_queue: tick lock held by another tick, skipping")
            return {"ok": True, "locked": True}

        states = _gather_states()

        try:
            active_jobs = _active_job_count()
        except Exception as exc:  # noqa: BLE001
            logger.warning("topup_agentic_queue: active_job_count failed: %s", exc)
            active_jobs = max(len({(s.kind, s.job_id) for s in states}), 1)

        window = dispatch_window.compute_job_window(max(active_jobs, 1))
        dispatch_window.write_job_window(window)

        swept = 0
        for sweep_fn in (_sweep_stuck_domestic, _sweep_stuck_intl, _sweep_stuck_external):
            try:
                swept += sweep_fn()
            except Exception as exc:  # noqa: BLE001 - one pipeline's sweep must not block the others
                logger.warning("topup_agentic_queue: %s failed: %s", sweep_fn.__name__, exc)

        try:
            _log_stalls(states)
        except Exception as exc:  # noqa: BLE001
            logger.warning("topup_agentic_queue: stall logging failed: %s", exc)

        pushed = _round_robin_topup(states, window)

        depth = queue_depth()
        holders = holder_count()
        logger.info(
            "agentic_topup window=%s active_jobs=%s pushed_domestic=%s "
            "pushed_international=%s pushed_external=%s finalize_swept=%s "
            "queue_depth=%s semaphore_holders=%s",
            window, active_jobs, pushed.get("domestic", 0), pushed.get("international", 0),
            pushed.get("external", 0), swept, depth, holders,
        )
        return {
            "ok": True,
            "locked": False,
            "window": window,
            "active_jobs": active_jobs,
            "pushed": pushed,
            "finalize_swept": swept,
            "queue_depth": depth,
            "semaphore_holders": holders,
        }
    except Exception as exc:  # noqa: BLE001 - beat schedule must never see an exception
        logger.warning("topup_agentic_queue failed: %s", exc)
        return {"ok": False, "error": str(exc)}

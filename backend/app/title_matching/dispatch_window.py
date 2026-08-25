"""
Shared atomic-claim primitives for the agentic batch pipelines: domestic
(``app.tasks.agentic_match_task``), international
(``app.tasks.agentic_intl_match_task``), and external-API
(``app.tasks.external_match_task``). All three share one Celery queue/worker
pool/sandbox semaphore (see
``local-docs/2026-08-25-agentic-batch-concurrency-design.md``); this module is
the ONE place their per-job coordination logic lives instead of three
copy-pasted implementations of the same race-free patterns.

Phase 4 (schema + counter-based finalize, chord still active) wired in
``claim_finalize`` alongside the still-active Celery chord as a
belt-and-suspenders completion trigger: whichever caller's row brings a job
to completion attempts the claim; if it wins, it enqueues that pipeline's
finalize task directly.

Phase 5 (windowed dispatch + round-robin top-up) removes the chord and adds
the rest of this module: ``claim_row_window`` (the atomic per-job dispatch
cursor claim), ``compute_job_window``/``target_queue_depth`` (the fair-share
formula), and ``write_job_window``/``read_job_window`` (the Redis cache the
beat-scheduled ``topup_agentic_queue`` and the per-pipeline self-refill agree
on). ``JobDispatchState`` was defined in Phase 4 but only consumed starting
now, by each pipeline's ``scheduler_state()``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session

logger = logging.getLogger(__name__)

# Redis key the beat-scheduled topup_agentic_queue writes the current
# fair-share per-job window to, and every pipeline's self-refill
# (enqueue_next_window callers) reads it from -- self-refill runs from row
# tasks, which don't want to recompute active-job counts (a DB query across
# three tables) on every single row completion.
_JOB_WINDOW_KEY = "agentic:sched:job_window"


def claim_finalize(
    session: Session,
    model: Any,
    job_id: str,
    completion_predicate: Optional[ColumnElement] = None,
) -> bool:
    """Atomically claim the right to finalize ``job_id`` via ONE conditional
    UPDATE -- never a read-then-write.

    ``model`` must expose an ``id`` primary key and a nullable
    ``finalize_claimed_at`` datetime column (``MovieTitleBatchJob``,
    ``MovieTitleIntlBatchJob``, ``ApiTitleMatchJob`` all do).
    ``completion_predicate`` is the pipeline's own "this job is actually
    done" condition, ANDed into the SAME UPDATE's WHERE clause so the
    completion check and the claim happen atomically together -- never a
    separate read-then-decide step that could race a concurrent caller:

    * domestic/international: ``model.processed >= model.total`` (``>=``,
      not ``==``, so a race that (harmlessly) double-bumps a counter before
      this claim still claims correctly rather than getting stuck).
    * external: NOT a counter-equality predicate (finding #3 in the plan --
      ``external_match_row`` deliberately does not re-increment
      ``rows_processed`` on a retried row, so ``rows_processed ==
      rows_total`` can already be true before a retry's rows finish).
      Callers pass a ``NOT EXISTS`` predicate over ``ApiTitleMatchRow``
      instead -- see ``external_match_task._after_row_terminal``.

    Returns True iff THIS call won the claim (``rowcount == 1``) -- i.e. the
    job was actually complete AND no one had claimed it yet. Ten callers can
    race this concurrently; at most one gets True. Commits the claim itself
    (callers do not need to commit separately).
    """
    stmt = (
        update(model)
        .where(model.id == job_id)
        .where(model.finalize_claimed_at.is_(None))
    )
    if completion_predicate is not None:
        stmt = stmt.where(completion_predicate)
    stmt = stmt.values(finalize_claimed_at=datetime.utcnow())

    result = session.execute(stmt)
    session.commit()
    return result.rowcount == 1


def _get_redis():
    """Local import so importing this module never hard-requires redis;
    raises if unreachable -- callers here decide how to degrade."""
    import redis

    from app.config import settings

    return redis.Redis.from_url(settings.REDIS_URL)


def target_queue_depth() -> int:
    """Total standing depth of the shared ``agentic`` queue across every
    active job, combined. ``settings.AGENTIC_QUEUE_TARGET_DEPTH`` if
    explicitly set (nonzero); otherwise auto-derived as
    ``2 * AGENTIC_BATCH_MAX_CONCURRENCY`` so the queue stays proportionally
    short as concurrency is raised in later phases without a manual bump.
    """
    from app.config import settings

    if settings.AGENTIC_QUEUE_TARGET_DEPTH:
        return settings.AGENTIC_QUEUE_TARGET_DEPTH
    return 2 * settings.AGENTIC_BATCH_MAX_CONCURRENCY


def compute_job_window(active_jobs: int) -> int:
    """Per-job standing dispatch window: ``target_queue_depth()`` divided
    evenly across ``active_jobs``, floored at ``AGENTIC_JOB_WINDOW_MIN`` so a
    job never gets starved down to zero just because many jobs are active.

    With ``active_jobs <= 1`` this returns the full target depth unmodified
    -- a lone active job saturates the shared pool exactly as it did before
    windowed dispatch existed.
    """
    from app.config import settings

    depth = target_queue_depth()
    if active_jobs <= 1:
        return depth
    return max(settings.AGENTIC_JOB_WINDOW_MIN, depth // active_jobs)


def write_job_window(window: int, *, redis_client=None) -> None:
    """Cache the current computed per-job window in Redis
    (``agentic:sched:job_window``, TTL a few multiples of the beat tick) so
    self-refill (``enqueue_next_window`` callers, invoked from row tasks) can
    read the same number the scheduler last computed without re-querying
    active-job counts itself on every row completion. Never raises -- a
    Redis outage here degrades read_job_window's callers to its documented
    fallback, it must not break the row task calling this.
    """
    from app.config import settings

    client = redis_client
    if client is None:
        try:
            client = _get_redis()
        except Exception as exc:  # noqa: BLE001
            logger.warning("write_job_window: redis unavailable: %s", exc)
            return
    try:
        ttl = max(int(settings.AGENTIC_SCHED_TICK_SECONDS * 6), 30)
        client.set(_JOB_WINDOW_KEY, int(window), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.warning("write_job_window: failed to cache window=%s: %s", window, exc)


def read_job_window(*, redis_client=None) -> int:
    """The cached per-job window written by the most recent
    ``topup_agentic_queue`` beat tick, or a safe fallback
    (``max(AGENTIC_JOB_WINDOW_MIN, AGENTIC_BATCH_MAX_CONCURRENCY)``) if
    Redis is unreachable or beat hasn't run yet (e.g. right after a fresh
    deploy) -- enough for one job to keep the pool busy without flooding it
    if N jobs already exist but beat hasn't ticked. Never raises.
    """
    from app.config import settings

    fallback = max(settings.AGENTIC_JOB_WINDOW_MIN, settings.AGENTIC_BATCH_MAX_CONCURRENCY)
    client = redis_client
    if client is None:
        try:
            client = _get_redis()
        except Exception as exc:  # noqa: BLE001
            logger.warning("read_job_window: redis unavailable, using fallback=%s: %s", fallback, exc)
            return fallback
    try:
        raw = client.get(_JOB_WINDOW_KEY)
        if raw is None:
            return fallback
        return int(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("read_job_window: read failed, using fallback=%s: %s", fallback, exc)
        return fallback


def claim_row_window(session: Session, model: Any, job_id: str, limit: int, *, max_attempts: int = 5) -> tuple[int, int]:
    """Claim up to ``limit`` of this job's undispatched row indices via a
    compare-and-swap on the ``dispatched`` cursor column. Returns an
    exclusive ``[from_idx, to_idx)`` that THIS caller alone owns, or
    ``(0, 0)`` if nothing to claim (job missing, not ``status ==
    'processing'``, ``dispatched >= total``, or ``limit <= 0``).

    CAS (read ``dispatched``, then ``UPDATE ... WHERE dispatched == <read
    value>``), not ``SELECT ... FOR UPDATE`` -- this must behave identically
    on Postgres (prod) and SQLite (tests), and ``FOR UPDATE``/``SKIP LOCKED``
    are not portable across both. Retries the read + conditional-UPDATE up to
    ``max_attempts`` times on contention (another caller won the CAS between
    our read and write); logs a warning and returns ``(0, 0)`` if exhausted
    -- self-healing, since the next caller (self-refill or the next beat
    tick) simply retries the claim later.

    ``model`` must expose ``id``, ``status``, ``dispatched``, and ``total``
    columns -- ``MovieTitleBatchJob``/``MovieTitleIntlBatchJob`` both do,
    with identical field names, so this one function serves both pipelines.
    """
    if limit <= 0:
        return (0, 0)

    for _attempt in range(max_attempts):
        job = session.get(model, job_id, populate_existing=True)
        if job is None:
            return (0, 0)
        if job.status != "processing":
            return (0, 0)
        dispatched, total = job.dispatched, job.total
        if dispatched >= total:
            return (0, 0)

        to_idx = min(dispatched + limit, total)
        result = session.execute(
            update(model)
            .where(model.id == job_id)
            .where(model.dispatched == dispatched)
            .values(dispatched=to_idx)
        )
        session.commit()
        if result.rowcount == 1:
            return (dispatched, to_idx)
        # Someone else's claim landed between our read and write -- retry
        # against the now-current dispatched value.

    logger.warning(
        "claim_row_window: exhausted %d attempts under contention for job=%s "
        "(self-healing -- the next caller retries)", max_attempts, job_id,
    )
    return (0, 0)


@dataclass(frozen=True)
class JobDispatchState:
    """Per-job dispatch/outstanding-row snapshot.

    Produced by each pipeline's ``scheduler_state()`` (only for jobs that
    still have outstanding or remaining rows -- a job with both at zero is
    the finalize sweep's concern, not the round-robin top-up's) and consumed
    by ``app.tasks.agentic_scheduler_task.topup_agentic_queue``, the one
    place all three pipelines' fairness accounting comes together.
    """

    kind: str  # "domestic" | "international" | "external"
    job_id: str
    outstanding: int  # dispatched - processed (domestic/intl); count(status='dispatched') (external)
    remaining: int  # rows not yet dispatched to Celery at all

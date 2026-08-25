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

Phase 5's round-robin top-up task (``topup_agentic_queue``) will live in this
same module later, since fairness across all three pipelines is exactly what
that task needs too — hence the module name, chosen ahead of that work.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, func, select

from app.celery_app import celery
from app.config import settings
from app.tasks.agentic_match_task import AGENTIC_QUEUE
from app.title_matching.sandbox_semaphore import holder_count

logger = logging.getLogger(__name__)


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

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery(
    "entelligence",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.semantic_tasks",
        "app.tasks.agentic_match_task",
        "app.tasks.agentic_intl_match_task",
        "app.tasks.prod_db_sync_task",
        "app.tasks.external_match_task",
        "app.tasks.deleted_showtime_task",
        "app.tasks.serpapi_credit_task",
        "app.tasks.usage_rollup_task",
        "app.tasks.agentic_scheduler_task",
    ],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    # Route the agentic batch tasks to a dedicated "agentic" queue run at
    # worker concurrency 2 — the primary concurrency cap for sandbox calls.
    task_routes={
        "app.tasks.agentic_match_task.agentic_batch_row": {"queue": "agentic"},
        "app.tasks.agentic_match_task.finalize_batch": {"queue": "agentic"},
        "app.tasks.agentic_intl_match_task.agentic_intl_batch_row": {"queue": "agentic"},
        "app.tasks.agentic_intl_match_task.finalize_intl_batch": {"queue": "agentic"},
        "app.tasks.deleted_showtime_task.process_batch": {"queue": "deleted-showtimes"},
        "app.tasks.deleted_showtime_task.finalize_job": {"queue": "deleted-showtimes"},
        "app.tasks.deleted_showtime_task.dispatch_job_task": {"queue": "deleted-showtimes"},
    },
    # Periodic tasks, run by the single-replica `celery-beat` service in
    # docker-compose.
    #
    # No task_routes entry for any of them: beat tasks land on the default
    # "celery" queue, which celery-worker already consumes — so a new
    # scheduled task needs no new worker service.
    #
    # The SerpApi credit snapshot and usage rollup run every 30 seconds — a
    # further revision from the design doc's original "hourly is fine" call,
    # tightened first to 5 minutes and then to 30 seconds for fast feedback
    # while actively testing the observability platform locally.
    #
    # Sub-minute cadence needs a plain seconds-based schedule; crontab() is
    # minute-granularity and cannot express "every 30 seconds" at all.
    #
    # OPERATIONAL NOTE: 30s means ~2 polls/minute x 13 configured SerpApi keys
    # = ~26 real HTTP requests/minute to SerpApi's own /account endpoint,
    # continuously. That is aggressive purely for a credit-balance check and
    # risks SerpApi-side rate limiting on that endpoint. Dial this back
    # (5 minutes or hourly) before any shared/production deployment — see
    # local-docs/2026-08-24-observability-platform-design.md §3.
    beat_schedule={
        "serpapi-credit-snapshot": {
            "task": "app.tasks.serpapi_credit_task.snapshot_serpapi_credits",
            "schedule": 30.0,
        },
        "usage-rollup": {
            "task": "app.tasks.usage_rollup_task.rollup_llm_usage_hourly",
            "schedule": 30.0,
        },
        "usage-prune-daily": {
            "task": "app.tasks.usage_rollup_task.prune_llm_call_logs",
            "schedule": crontab(hour=3, minute=20),
        },
        # Phase 2 pool observability (see local-docs/2026-08-25-agentic-batch-
        # concurrency-design.md §4.3): samples "agentic" queue depth + live
        # semaphore-holder count + active-job count every 30s. Deliberately
        # NOT in task_routes above — it must land on the default "celery"
        # queue that celery-worker/celery-beat already consume, never on
        # "agentic", where it would consume a scarce sandbox-call worker
        # slot for a task that does no sandbox work at all.
        "agentic-pool-sample": {
            "task": "app.tasks.agentic_scheduler_task.sample_agentic_pool",
            "schedule": 30.0,
        },
    },
)

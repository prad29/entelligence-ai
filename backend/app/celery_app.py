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
    # docker-compose. Two conventions hold for every entry here:
    #
    #  1. No task_routes entry. Beat tasks land on the default "celery" queue,
    #     which celery-worker already consumes — so a new scheduled task needs
    #     no new worker service.
    #  2. Off-the-hour minutes. Each entry gets its own minute so the hourly
    #     jobs don't all contend for the same worker slot at :00.
    #
    # Further observability entries (the hourly usage rollup and the daily raw
    # log prune) are added to this same dict.
    beat_schedule={
        "serpapi-credit-snapshot-hourly": {
            "task": "app.tasks.serpapi_credit_task.snapshot_serpapi_credits",
            "schedule": crontab(minute=7),
        },
        "usage-rollup-hourly": {
            "task": "app.tasks.usage_rollup_task.rollup_llm_usage_hourly",
            "schedule": crontab(minute=10),
        },
        "usage-prune-daily": {
            "task": "app.tasks.usage_rollup_task.prune_llm_call_logs",
            "schedule": crontab(hour=3, minute=20),
        },
    },
)

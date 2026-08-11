from celery import Celery
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
)

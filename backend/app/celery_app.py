from celery import Celery
from app.config import settings

celery = Celery(
    "entelligence",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.semantic_tasks", "app.tasks.agentic_match_task"],
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
    },
)

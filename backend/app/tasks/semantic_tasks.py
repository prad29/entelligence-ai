"""
Celery task: build the Vespa semantic index in the background.

Triggered from the FastAPI startup event so the API is immediately
available while embeddings are generated and fed to Vespa.
Once complete the task signals via Redis so the web process can
attach the index to the running CandidateGenerator.
"""

import logging

from app.celery_app import celery
from app.config import settings

logger = logging.getLogger(__name__)

_READY_KEY = "semantic_index:ready"


@celery.task(
    bind=True,
    name="app.tasks.semantic_tasks.build_semantic_index",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def build_semantic_index_task(self):
    """
    Build (or resume) the Vespa semantic index.

    Idempotent — uses ID-based diffing so restarting the task only
    feeds rows that are not yet in Vespa.
    """
    try:
        from sqlmodel import Session, select
        from app.database import engine as db_engine
        from app.models import MovieMaster
        from app.title_matching.semantic_index import build_semantic_index

        logger.info("semantic_task: loading master rows from DB")
        with Session(db_engine) as session:
            rows = session.exec(select(MovieMaster)).all()

        master_rows = [
            {
                "id": r.id,
                "movie_title": r.movie_title,
                "release_date": r.release_date,
                "cover_image": r.cover_image,
                "parent_id": r.parent_id,
                "director": getattr(r, "director", None),
            }
            for r in rows
        ]

        logger.info("semantic_task: starting index build for %d rows", len(master_rows))
        index = build_semantic_index(master_rows, settings)

        if index is not None:
            # Signal readiness via Redis so web workers can pick it up
            import redis as redis_lib
            r = redis_lib.from_url(settings.REDIS_URL)
            r.set(_READY_KEY, "1")
            logger.info("semantic_task: index ready, signalled via Redis key '%s'", _READY_KEY)
        else:
            logger.warning("semantic_task: build_semantic_index returned None")

    except Exception as exc:
        logger.error("semantic_task: failed: %s", exc)
        raise self.retry(exc=exc)

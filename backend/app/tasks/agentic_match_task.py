from __future__ import annotations

import logging
from typing import Optional

from app.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(bind=True, max_retries=2, soft_time_limit=120, time_limit=150)
def agentic_title_match(
    self,
    title: str,
    show_date: Optional[str] = None,
    theater: Optional[str] = None,
    ticketing_url: Optional[str] = None,
    row_id: Optional[str] = None,
) -> dict:
    """Celery task that runs Mode B agentic match and stores the result."""
    from app.title_matching.agentic.runner import run_agentic_match
    from app.title_matching.agentic import AgenticError

    try:
        result = run_agentic_match(title, show_date, theater, ticketing_url)
    except AgenticError as exc:
        logger.error("agentic_match_failed row_id=%s title=%r error=%s", row_id, title, exc)
        raise self.retry(exc=exc)

    if row_id:
        _store_result(row_id, result)

    return vars(result)


def _store_result(row_id: str, result) -> None:
    """Persist the agentic match result to the review queue."""
    try:
        from app.database import SessionLocal
        from app.models import MovieTitleMatchReviewItem

        with SessionLocal() as session:
            item = MovieTitleMatchReviewItem(
                row_id=row_id,
                suggested_movie_id=result.suggested_movie_id,
                suggested_movie_title=result.suggested_movie_title,
                confidence=result.confidence,
                decision=result.decision,
                reasoning=result.reasoning,
                source="agentic",
            )
            session.add(item)
            session.commit()
    except Exception as exc:
        logger.warning("agentic_result_store_failed row_id=%s error=%s", row_id, exc)

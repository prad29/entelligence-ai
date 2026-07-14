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
    """Celery task that runs a single Mode B agentic match.

    NOTE: this is a minimal stub kept import-clean. The real batch-aware
    task (per-row/job bookkeeping, atomic counters, concurrency semaphore)
    is implemented in a later construction step. This feature does not use
    a review-queue model.
    """
    from app.title_matching.agentic.runner import run_agentic_match
    from app.title_matching.agentic import AgenticError

    try:
        result = run_agentic_match(title, show_date, theater, ticketing_url)
    except AgenticError as exc:
        logger.error("agentic_match_failed row_id=%s title=%r error=%s", row_id, title, exc)
        raise self.retry(exc=exc)

    return vars(result)

"""
Shared atomic-claim primitives for the agentic batch pipelines: domestic
(``app.tasks.agentic_match_task``), international
(``app.tasks.agentic_intl_match_task``), and external-API
(``app.tasks.external_match_task``). All three share one Celery queue/worker
pool/sandbox semaphore (see
``local-docs/2026-08-25-agentic-batch-concurrency-design.md``); this module is
the ONE place their per-job coordination logic lives instead of three
copy-pasted implementations of the same race-free patterns.

Phase 4 (schema + counter-based finalize, chord still active) wires in
``claim_finalize`` alongside the still-active Celery chord as a
belt-and-suspenders completion trigger: whichever caller's row brings a job
to completion attempts the claim; if it wins, it enqueues that pipeline's
finalize task directly. The chord remains the reliable backstop this phase --
a subtly wrong counter-trigger still lets jobs finish via the chord exactly
as before.

Phase 5 (windowed dispatch + round-robin top-up) is what actually removes the
chord and starts writing/reading ``JobDispatchState``/``dispatched`` -- both
are defined here now (shared infrastructure) but unused until then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session


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


@dataclass(frozen=True)
class JobDispatchState:
    """Per-job dispatch/outstanding-row snapshot.

    Unused until Phase 5's round-robin top-up task
    (``app.tasks.agentic_scheduler_task.topup_agentic_queue``) --
    defined now because ``dispatch_window.py`` is the shared infrastructure
    module all three pipelines build on, and Phase 5's scheduler consumes
    this shape identically across domestic/international/external.
    """

    kind: str  # "domestic" | "international" | "external"
    job_id: str
    outstanding: int  # dispatched - processed (domestic/intl); count(status='dispatched') (external)
    remaining: int  # rows not yet dispatched to Celery at all

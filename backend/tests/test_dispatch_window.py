"""
Tests for app.title_matching.dispatch_window.claim_finalize -- the shared,
race-free "one conditional UPDATE, never read-then-write" finalize claim used
by all three agentic batch pipelines (domestic, international, external-API).

DB: in-memory sqlite via SQLModel metadata (same convention as
test_agentic_batch_task.py).
"""

from __future__ import annotations

import pytest
from sqlalchemy import update
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.models import ApiTitleMatchJob, ApiTitleMatchRow, MovieTitleBatchJob
from app.title_matching.dispatch_window import JobDispatchState, claim_finalize


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _make_job(engine, total=3, processed=0, job_id="job-claim-1"):
    with Session(engine) as s:
        s.add(
            MovieTitleBatchJob(
                id=job_id, status="processing", total=total, processed=processed
            )
        )
        s.commit()
    return job_id


def _get_job(engine, job_id):
    with Session(engine) as s:
        return s.get(MovieTitleBatchJob, job_id)


# ---------------------------------------------------------------------------
# 1/2. Claimed exactly once whether the job reaches total via success or
#      failure counting -- claim_finalize itself doesn't care which counter
#      moved processed to total, only that processed >= total.
# ---------------------------------------------------------------------------
def test_claim_finalize_wins_when_processed_reaches_total(db_engine):
    job_id = _make_job(db_engine, total=3, processed=2)

    with Session(db_engine) as s:
        won = claim_finalize(
            s,
            MovieTitleBatchJob,
            job_id,
            completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
        )
    assert won is False  # processed(2) < total(3): not complete yet

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is None

    # Bump the last row's processed counter (simulating the last row landing).
    with Session(db_engine) as s:
        s.execute(
            update(MovieTitleBatchJob)
            .where(MovieTitleBatchJob.id == job_id)
            .values(processed=MovieTitleBatchJob.processed + 1)
        )
        s.commit()

    with Session(db_engine) as s:
        won = claim_finalize(
            s,
            MovieTitleBatchJob,
            job_id,
            completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
        )
    assert won is True

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is not None


# ---------------------------------------------------------------------------
# 3. Calling claim_finalize a second time after it already won -> False, no
#    double claim.
# ---------------------------------------------------------------------------
def test_claim_finalize_second_call_is_a_noop(db_engine):
    job_id = _make_job(db_engine, total=1, processed=1)

    with Session(db_engine) as s:
        first = claim_finalize(
            s,
            MovieTitleBatchJob,
            job_id,
            completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
        )
    assert first is True
    first_claimed_at = _get_job(db_engine, job_id).finalize_claimed_at

    with Session(db_engine) as s:
        second = claim_finalize(
            s,
            MovieTitleBatchJob,
            job_id,
            completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
        )
    assert second is False
    # The original claim timestamp is untouched by the losing second call.
    assert _get_job(db_engine, job_id).finalize_claimed_at == first_claimed_at


# ---------------------------------------------------------------------------
# 4. processed > total (defensive >= not ==) still claims correctly.
# ---------------------------------------------------------------------------
def test_claim_finalize_handles_processed_greater_than_total(db_engine):
    job_id = _make_job(db_engine, total=3, processed=5)

    with Session(db_engine) as s:
        won = claim_finalize(
            s,
            MovieTitleBatchJob,
            job_id,
            completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
        )
    assert won is True


# ---------------------------------------------------------------------------
# No completion_predicate -> claims unconditionally as long as unclaimed.
# ---------------------------------------------------------------------------
def test_claim_finalize_without_predicate_claims_unconditionally(db_engine):
    job_id = _make_job(db_engine, total=100, processed=0)

    with Session(db_engine) as s:
        won = claim_finalize(s, MovieTitleBatchJob, job_id)
    assert won is True


def test_claim_finalize_missing_job_returns_false(db_engine):
    with Session(db_engine) as s:
        won = claim_finalize(
            s,
            MovieTitleBatchJob,
            "does-not-exist",
            completion_predicate=MovieTitleBatchJob.processed >= MovieTitleBatchJob.total,
        )
    assert won is False


# ---------------------------------------------------------------------------
# External-shaped predicate: a NOT EXISTS subquery over a sibling row table
# (mirrors what external_match_task._after_row_terminal actually passes) --
# claim_finalize must be generic enough to accept ANY SQLAlchemy boolean
# expression, not just a same-table column comparison.
# ---------------------------------------------------------------------------
def test_claim_finalize_with_not_exists_predicate_over_sibling_table(db_engine):
    from sqlalchemy import exists

    job_id = "ext-job-1"
    with Session(db_engine) as s:
        s.add(ApiTitleMatchJob(id=job_id, api_key_id="k1", market="domestic"))
        s.add(
            ApiTitleMatchRow(
                job_id=job_id, row_uuid="r1", input_json="{}", status="pending"
            )
        )
        s.commit()

    def _predicate():
        return ~exists().where(
            ApiTitleMatchRow.job_id == job_id,
            ApiTitleMatchRow.status.notin_(("completed", "failed")),
        )

    with Session(db_engine) as s:
        won = claim_finalize(s, ApiTitleMatchJob, job_id, completion_predicate=_predicate())
    assert won is False  # the one row is still 'pending' -> not complete

    with Session(db_engine) as s:
        row = s.exec(
            select(ApiTitleMatchRow).where(ApiTitleMatchRow.job_id == job_id)
        ).first()
        row.status = "completed"
        s.add(row)
        s.commit()

    with Session(db_engine) as s:
        won = claim_finalize(s, ApiTitleMatchJob, job_id, completion_predicate=_predicate())
    assert won is True


def test_job_dispatch_state_is_a_plain_frozen_dataclass():
    """Sanity check only -- unused until Phase 5, but must exist with the
    documented shape now (shared infrastructure module)."""
    state = JobDispatchState(kind="domestic", job_id="j1", outstanding=2, remaining=5)
    assert state.kind == "domestic"
    assert state.job_id == "j1"
    assert state.outstanding == 2
    assert state.remaining == 5
    with pytest.raises(Exception):
        state.kind = "international"  # frozen -> AttributeError/FrozenInstanceError

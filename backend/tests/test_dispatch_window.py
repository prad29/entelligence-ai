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

from app.models import ApiTitleMatchJob, ApiTitleMatchRow, MovieTitleBatchJob, MovieTitleIntlBatchJob
from app.title_matching import dispatch_window
from app.title_matching.dispatch_window import (
    JobDispatchState,
    claim_finalize,
    claim_row_window,
    compute_job_window,
    read_job_window,
    target_queue_depth,
    write_job_window,
)


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


# ---------------------------------------------------------------------------
# Phase 5 -- claim_row_window
# ---------------------------------------------------------------------------
def _make_dispatch_job(engine, total=10, dispatched=0, status="processing", job_id="disp-job-1"):
    with Session(engine) as s:
        s.add(MovieTitleBatchJob(id=job_id, status=status, total=total, dispatched=dispatched))
        s.commit()
    return job_id


def test_claim_row_window_disjoint_sequential_claims(db_engine):
    job_id = _make_dispatch_job(db_engine, total=10, dispatched=0)

    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, 3) == (0, 3)
    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, 3) == (3, 6)
    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, 10) == (6, 10)
    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, 10) == (0, 0)


def test_claim_row_window_not_processing_returns_zero(db_engine):
    job_id = _make_dispatch_job(db_engine, total=10, dispatched=0, status="queued")

    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, 3) == (0, 0)


def test_claim_row_window_zero_or_negative_limit_returns_zero(db_engine):
    job_id = _make_dispatch_job(db_engine, total=10, dispatched=0)

    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, 0) == (0, 0)
    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, job_id, -5) == (0, 0)


def test_claim_row_window_missing_job_returns_zero(db_engine):
    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleBatchJob, "does-not-exist", 3) == (0, 0)


def test_claim_row_window_works_identically_for_intl_model(db_engine):
    job_id = "intl-disp-1"
    with Session(db_engine) as s:
        s.add(MovieTitleIntlBatchJob(id=job_id, status="processing", total=4, dispatched=0))
        s.commit()

    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleIntlBatchJob, job_id, 4) == (0, 4)
    with Session(db_engine) as s:
        assert claim_row_window(s, MovieTitleIntlBatchJob, job_id, 4) == (0, 0)


def test_claim_row_window_never_overlaps_under_contention(db_engine):
    """Simulate contention: repeatedly claiming small windows concurrently
    (sequential calls standing in for overlapping callers -- the CAS
    guarantee doesn't depend on true thread concurrency to prove disjointness)
    must never produce overlapping [from, to) ranges."""
    job_id = _make_dispatch_job(db_engine, total=50, dispatched=0)

    claims = []
    with Session(db_engine) as s:
        for _ in range(20):
            frm, to = claim_row_window(s, MovieTitleBatchJob, job_id, 3)
            if to > frm:
                claims.append((frm, to))

    total_claimed = sum(to - frm for frm, to in claims)
    assert total_claimed == 50
    covered = set()
    for frm, to in claims:
        rng = set(range(frm, to))
        assert not (rng & covered), f"overlap detected: {frm, to} vs already-covered {covered}"
        covered |= rng
    assert covered == set(range(50))


# ---------------------------------------------------------------------------
# Phase 5 -- compute_job_window / target_queue_depth
# ---------------------------------------------------------------------------
def test_target_queue_depth_auto_derives_from_max_concurrency(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 0)
    monkeypatch.setattr(settings, "AGENTIC_BATCH_MAX_CONCURRENCY", 4)
    assert target_queue_depth() == 8


def test_target_queue_depth_explicit_override(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 25)
    assert target_queue_depth() == 25


def test_compute_job_window_single_job_gets_full_depth(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 8)
    monkeypatch.setattr(settings, "AGENTIC_JOB_WINDOW_MIN", 2)
    assert compute_job_window(1) == 8
    assert compute_job_window(0) == 8


def test_compute_job_window_divides_across_active_jobs(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 8)
    monkeypatch.setattr(settings, "AGENTIC_JOB_WINDOW_MIN", 2)
    assert compute_job_window(4) == 2


def test_compute_job_window_floors_at_job_window_min(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 8)
    monkeypatch.setattr(settings, "AGENTIC_JOB_WINDOW_MIN", 2)
    assert compute_job_window(99) == 2


# ---------------------------------------------------------------------------
# Phase 5 -- write_job_window / read_job_window
# ---------------------------------------------------------------------------
def test_write_then_read_job_window_round_trips(monkeypatch):
    import redis

    try:
        client = redis.Redis.from_url("redis://localhost:6379/0")
        client.ping()
    except Exception:
        pytest.skip("local redis not reachable at localhost:6379")

    write_job_window(7, redis_client=client)
    try:
        assert read_job_window(redis_client=client) == 7
    finally:
        client.delete(dispatch_window._JOB_WINDOW_KEY)


def test_read_job_window_returns_fallback_when_redis_unavailable(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_JOB_WINDOW_MIN", 2)
    monkeypatch.setattr(settings, "AGENTIC_BATCH_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(dispatch_window, "_get_redis", lambda: (_ for _ in ()).throw(ConnectionError("down")))

    assert read_job_window() == 4  # max(2, 4)


def test_read_job_window_returns_fallback_when_key_missing():
    from app.config import settings

    class _EmptyRedis:
        def get(self, key):
            return None

    expected = max(settings.AGENTIC_JOB_WINDOW_MIN, settings.AGENTIC_BATCH_MAX_CONCURRENCY)
    assert read_job_window(redis_client=_EmptyRedis()) == expected


def test_write_job_window_never_raises_when_redis_unavailable():
    class _RaisingRedis:
        def set(self, *a, **k):
            raise ConnectionError("down")

    write_job_window(5, redis_client=_RaisingRedis())  # must not raise


def test_read_job_window_never_raises_when_client_raises():
    class _RaisingRedis:
        def get(self, key):
            raise ConnectionError("down")

    assert isinstance(read_job_window(redis_client=_RaisingRedis()), int)

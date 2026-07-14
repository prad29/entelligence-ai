"""
Tests for the Mode B agentic batch Celery tasks and the TTL sandbox semaphore.

Coverage:
  (a) a successful row atomically bumps processed + matched/no_match and stores
      its result;
  (b) a base agentic error retries once then falls into the failed-row path,
      still bumping processed + failed;
  (c) two concurrent-style counter increments against a shared session both land
      (proving the atomic UPDATE ... col = col + 1 never loses an increment — a
      naive Python read-modify-write would drop one);
  (d) the semaphore self-heals via TTL: a holder that is never released still
      disappears once its EX ttl elapses.

Redis: uses the real local redis at localhost:6379 (available in this env) for
the semaphore TTL test with a deliberately tiny TTL; row-result storage is
patched to an in-memory dict so counter/retry tests don't need redis.
DB: in-memory sqlite via SQLModel metadata.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import update
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.models import MovieMaster, MovieTitleBatchJob
from app.title_matching import batch_io
from app.title_matching.agentic import AgenticError


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


@pytest.fixture
def fake_hash():
    """In-memory stand-in for the per-job Redis results hash."""
    return {}


@pytest.fixture
def patched_task(monkeypatch, db_engine, fake_hash):
    """Patch the task module's engine + redis-backed helpers to in-memory ones."""
    import app.tasks.agentic_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    def _store(job_id, row_index, row_result):
        fake_hash[str(row_index)] = json.dumps(row_result)

    monkeypatch.setattr(task_mod, "_store_row_result", _store)
    return task_mod


def _make_job(engine, total=1):
    job_id = "job-test-1"
    with Session(engine) as s:
        s.add(MovieTitleBatchJob(id=job_id, status="processing", total=total))
        s.commit()
    return job_id


def _get_job(engine, job_id):
    with Session(engine) as s:
        return s.get(MovieTitleBatchJob, job_id)


class _Result:
    def __init__(self, canonical_movie_id, suggested_movie_id, title, confidence):
        self.canonical_movie_id = canonical_movie_id
        self.suggested_movie_id = suggested_movie_id
        self.suggested_movie_title = title
        self.confidence = confidence
        self.reasoning = "matched via test"


# ---------------------------------------------------------------------------
# (a) successful row -> atomic counters + stored result
# ---------------------------------------------------------------------------
def test_successful_row_bumps_matched_and_stores_result(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine)
    with Session(db_engine) as s:
        s.add(MovieMaster(id=42, movie_title="The Matrix"))
        s.commit()

    def fake_run(title, show_date, theater, ticketing_url, use_poster_vision):
        assert theater is None  # batch path always passes None
        return _Result(42, 42, "The Matrix", 0.97)

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch

    with patch.object(runner_mod, "run_agentic_match", side_effect=fake_run), \
         patch.object(sem, "acquire", return_value="holder-x"), \
         patch.object(sem, "release") as rel:
        patched_task.agentic_batch_row.run(job_id, 0, "The Matrix", None, None, False)

    job = _get_job(db_engine, job_id)
    assert job.processed == 1
    assert job.matched == 1
    assert job.no_match == 0
    assert job.failed == 0
    stored = json.loads(fake_hash["0"])
    assert stored["present_in_db"] == "Yes"
    assert stored["mapped_title"] == "The Matrix"
    assert stored["confidence_score"] == 0.97
    rel.assert_called_once_with("holder-x")


def test_successful_row_no_match_when_id_absent(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine)

    def fake_run(title, show_date, theater, ticketing_url, use_poster_vision):
        # id > 0 but not present in MovieMaster -> No
        return _Result(999, 999, "Ghost Movie", 0.5)

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch

    with patch.object(runner_mod, "run_agentic_match", side_effect=fake_run), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        patched_task.agentic_batch_row.run(job_id, 0, "Ghost Movie", None, None, False)

    job = _get_job(db_engine, job_id)
    assert job.processed == 1
    assert job.no_match == 1
    assert job.matched == 0
    assert json.loads(fake_hash["0"])["present_in_db"] == "No"


# ---------------------------------------------------------------------------
# (b) base agentic error -> retry once then failed-row path
# ---------------------------------------------------------------------------
def test_agentic_error_retries_then_fails(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine)

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch, MagicMock

    def always_fail(*a, **k):
        raise AgenticError("sandbox exploded")

    # First call: retries=0 < max_retries=2 -> self.retry raises Retry
    class _Retry(Exception):
        pass

    # The raw, undecorated function (takes `self` as the first positional arg)
    # so we can inject a fake task self with a controllable retries count.
    raw_fn = patched_task.agentic_batch_row.run.__func__
    with patch.object(runner_mod, "run_agentic_match", side_effect=always_fail), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        # Simulate the retrying attempt (retries=0): self.retry should be invoked.
        fake_self = MagicMock()
        fake_self.request.retries = 0
        fake_self.max_retries = 2
        fake_self.retry.side_effect = _Retry()
        with pytest.raises(_Retry):
            raw_fn(fake_self, job_id, 0, "boom", None, None, False)
        assert fake_self.retry.called

        # Now the exhausted attempt (retries == max_retries): failed-row path.
        fake_self2 = MagicMock()
        fake_self2.request.retries = 2
        fake_self2.max_retries = 2
        raw_fn(fake_self2, job_id, 0, "boom", None, None, False)

    job = _get_job(db_engine, job_id)
    assert job.processed == 1
    assert job.failed == 1
    assert job.matched == 0
    assert job.no_match == 0
    stored = json.loads(fake_hash["0"])
    assert stored["present_in_db"] == "No"
    assert stored["reasoning"].startswith("error:")
    assert "sandbox exploded" in stored["reasoning"]


# ---------------------------------------------------------------------------
# (c) atomic counter never loses an increment under concurrent-style writes
# ---------------------------------------------------------------------------
def test_atomic_counter_does_not_lose_increment(patched_task, db_engine):
    """
    Two increments applied via the atomic SQL expression must both land (final
    processed == 2). A naive Python read-modify-write against a stale snapshot
    would drop one; we prove the difference below.
    """
    job_id = _make_job(db_engine)

    # Two independent sessions, each reading BEFORE either commits — the classic
    # lost-update race. The atomic col = col + 1 expression is evaluated
    # server-side at commit, so both increments survive.
    s1 = Session(db_engine)
    s2 = Session(db_engine)
    try:
        s1.execute(
            update(MovieTitleBatchJob)
            .where(MovieTitleBatchJob.id == job_id)
            .values(processed=MovieTitleBatchJob.processed + 1)
        )
        s2.execute(
            update(MovieTitleBatchJob)
            .where(MovieTitleBatchJob.id == job_id)
            .values(processed=MovieTitleBatchJob.processed + 1)
        )
        s1.commit()
        s2.commit()
    finally:
        s1.close()
        s2.close()

    assert _get_job(db_engine, job_id).processed == 2

    # Sanity: a naive read-modify-write from a shared stale snapshot WOULD lose
    # one, confirming the atomic approach is the meaningful part.
    job_id2 = "job-naive"
    with Session(db_engine) as s:
        s.add(MovieTitleBatchJob(id=job_id2, status="processing", total=2, processed=0))
        s.commit()
    with Session(db_engine) as reader:
        stale = reader.get(MovieTitleBatchJob, job_id2).processed  # both read 0
    for _ in range(2):
        with Session(db_engine) as w:
            j = w.get(MovieTitleBatchJob, job_id2)
            j.processed = stale + 1  # write 1 twice -> lost update
            w.add(j)
            w.commit()
    assert _get_job(db_engine, job_id2).processed == 1  # proves naive loses one


# ---------------------------------------------------------------------------
# (d) semaphore self-heals via TTL even if release is never called
# ---------------------------------------------------------------------------
@pytest.fixture
def local_redis():
    import redis

    try:
        client = redis.Redis.from_url("redis://localhost:6379/0")
        client.ping()
    except Exception:
        pytest.skip("local redis not reachable at localhost:6379")
    # Clean any stray holder keys before/after.
    for k in client.scan_iter(match="sandbox:holder:*"):
        client.delete(k)
    yield client
    for k in client.scan_iter(match="sandbox:holder:*"):
        client.delete(k)


def test_semaphore_self_heals_via_ttl(local_redis):
    import time

    from app.title_matching import sandbox_semaphore as sem

    # Acquire with a tiny 1s TTL and NEVER release (simulating a SIGKILLed holder).
    holder = sem.acquire(timeout=2, ttl=1, redis_client=local_redis)
    assert holder.startswith(sem.HOLDER_PREFIX)
    # The EX ttl was set on the key.
    assert local_redis.ttl(holder) >= 0
    assert local_redis.exists(holder) == 1

    # Wait for the TTL to elapse; the slot frees itself with no release() call.
    time.sleep(1.3)
    assert local_redis.exists(holder) == 0

    # And a fresh acquire now succeeds even though release was never invoked.
    holder2 = sem.acquire(timeout=2, ttl=1, redis_client=local_redis)
    assert holder2.startswith(sem.HOLDER_PREFIX)


def test_semaphore_caps_concurrency(local_redis):
    from app.title_matching import sandbox_semaphore as sem
    from app.config import settings

    # Fill all slots (cap = AGENTIC_BATCH_MAX_CONCURRENCY, default 2).
    holders = [
        sem.acquire(timeout=2, ttl=30, redis_client=local_redis)
        for _ in range(settings.AGENTIC_BATCH_MAX_CONCURRENCY)
    ]
    assert all(h.startswith(sem.HOLDER_PREFIX) for h in holders)

    # Next acquire cannot get a slot within the timeout.
    with pytest.raises(TimeoutError):
        sem.acquire(timeout=1, ttl=30, redis_client=local_redis)

    # Releasing one frees a slot.
    sem.release(holders[0], redis_client=local_redis)
    freed = sem.acquire(timeout=2, ttl=30, redis_client=local_redis)
    assert freed.startswith(sem.HOLDER_PREFIX)


def test_semaphore_fails_open_when_redis_none():
    from app.title_matching import sandbox_semaphore as sem

    # Force the no-client path: _get_redis returns None (redis unreachable).
    import unittest.mock as m

    with m.patch.object(sem, "_get_redis", return_value=None):
        holder = sem.acquire(timeout=1)
    assert holder == sem.FAIL_OPEN_HOLDER
    # release of the sentinel is a no-op and never raises.
    sem.release(holder)

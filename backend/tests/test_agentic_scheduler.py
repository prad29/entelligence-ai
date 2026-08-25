"""
Tests for Phase 2 pool observability: app.tasks.agentic_scheduler_task.

Coverage:
  (a) queue_depth() against real local Redis with a known number of items
      pushed onto the queue key -> returns the exact count.
  (b) queue_depth() with a client that raises -> returns None, never raises.
  (c) sample_agentic_pool() with queue_depth/holder_count mocked and a DB
      seeded with `processing` jobs across all three kinds -> logged/returned
      values match, all three kinds counted.
  (d) sample_agentic_pool() with the Redis-backed calls raising -> still
      returns a dict, never raises (proves the outer try/except works).
  (e) the new task is registered under the right name, on the beat schedule,
      and NOT routed onto the "agentic" queue.

Redis: uses the real local redis at localhost:6379 (same convention as
test_agentic_batch_task.py's `local_redis` fixture).
DB: in-memory sqlite via SQLModel metadata (same convention as
test_agentic_batch_task.py's `db_engine` fixture).
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import ApiTitleMatchJob, MovieTitleBatchJob, MovieTitleIntlBatchJob


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def local_redis():
    import redis

    try:
        client = redis.Redis.from_url("redis://localhost:6379/0")
        client.ping()
    except Exception:
        pytest.skip("local redis not reachable at localhost:6379")
    client.delete("agentic")
    yield client
    client.delete("agentic")


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


# ---------------------------------------------------------------------------
# (a)/(b) queue_depth
# ---------------------------------------------------------------------------
def test_queue_depth_counts_real_pushed_items(local_redis):
    from app.tasks import agentic_scheduler_task as sched

    for i in range(5):
        local_redis.rpush("agentic", f"msg-{i}")

    assert sched.queue_depth("agentic", redis_client=local_redis) == 5


def test_queue_depth_empty_queue_is_zero(local_redis):
    from app.tasks import agentic_scheduler_task as sched

    assert sched.queue_depth("agentic", redis_client=local_redis) == 0


def test_queue_depth_returns_none_when_redis_unreachable():
    from app.tasks import agentic_scheduler_task as sched

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sched, "_get_redis", lambda: None)
        assert sched.queue_depth("agentic") is None


def test_queue_depth_returns_none_when_client_raises():
    from app.tasks import agentic_scheduler_task as sched

    class _RaisingClient:
        def llen(self, queue):
            raise ConnectionError("boom")

    assert sched.queue_depth("agentic", redis_client=_RaisingClient()) is None


# ---------------------------------------------------------------------------
# (c) sample_agentic_pool — happy path, across all three job kinds
# ---------------------------------------------------------------------------
def test_sample_agentic_pool_reports_active_jobs_across_all_three_kinds(monkeypatch, db_engine):
    from app.tasks import agentic_scheduler_task as sched

    with Session(db_engine) as s:
        # Two domestic processing, one domestic completed (should not count).
        s.add(MovieTitleBatchJob(id="d1", status="processing", total=10))
        s.add(MovieTitleBatchJob(id="d2", status="processing", total=10))
        s.add(MovieTitleBatchJob(id="d3", status="completed", total=10))
        # One intl processing.
        s.add(MovieTitleIntlBatchJob(id="i1", status="processing", total=5))
        # One external processing, one external queued (should not count).
        s.add(
            ApiTitleMatchJob(
                id="e1", api_key_id="k1", market="domestic", phase="processing", rows_total=3
            )
        )
        s.add(
            ApiTitleMatchJob(
                id="e2", api_key_id="k1", market="domestic", phase="queued", rows_total=3
            )
        )
        s.commit()

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(sched, "queue_depth", lambda *a, **k: 7)
    monkeypatch.setattr(sched, "holder_count", lambda *a, **k: 2)

    from app.config import settings

    result = sched.sample_agentic_pool()

    assert result["ok"] is True
    assert result["queue_depth"] == 7
    assert result["semaphore_holders"] == 2
    # Read from settings rather than hardcoding — this must track whatever
    # AGENTIC_BATCH_MAX_CONCURRENCY is currently configured to, not a
    # particular historical default.
    assert result["max_concurrency"] == settings.AGENTIC_BATCH_MAX_CONCURRENCY
    # 2 domestic + 1 intl + 1 external = 4 processing jobs.
    assert result["active_jobs"] == 4


def test_sample_agentic_pool_logs_one_line(monkeypatch, db_engine, caplog):
    import logging

    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(sched, "queue_depth", lambda *a, **k: 0)
    monkeypatch.setattr(sched, "holder_count", lambda *a, **k: 0)

    with caplog.at_level(logging.INFO, logger=sched.logger.name):
        sched.sample_agentic_pool()

    matching = [r for r in caplog.records if r.message.startswith("agentic_pool_sample")]
    assert len(matching) == 1
    assert "queue_depth=0" in matching[0].message
    assert "semaphore_holders=0" in matching[0].message


# ---------------------------------------------------------------------------
# (d) sample_agentic_pool — degraded path, never raises
# ---------------------------------------------------------------------------
def test_sample_agentic_pool_never_raises_when_redis_calls_fail(monkeypatch, db_engine):
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    def _raise(*a, **k):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(sched, "queue_depth", _raise)
    monkeypatch.setattr(sched, "holder_count", lambda *a, **k: None)

    result = sched.sample_agentic_pool()

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert "error" in result


def test_sample_agentic_pool_never_raises_when_db_query_fails(monkeypatch):
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr(sched, "queue_depth", lambda *a, **k: 3)
    monkeypatch.setattr(sched, "holder_count", lambda *a, **k: 1)

    def _raise():
        raise RuntimeError("db is down")

    monkeypatch.setattr(sched, "_active_job_count", _raise)

    result = sched.sample_agentic_pool()

    assert isinstance(result, dict)
    # DB failure is caught by the inner guard, degrading active_jobs to None
    # rather than failing the whole sample.
    assert result["ok"] is True
    assert result["active_jobs"] is None
    assert result["queue_depth"] == 3
    assert result["semaphore_holders"] == 1


# ---------------------------------------------------------------------------
# (e) registration — task name, beat schedule, and queue routing
# ---------------------------------------------------------------------------
def test_sample_agentic_pool_registered_on_beat_schedule_not_agentic_queue():
    from app.celery_app import celery

    entry = celery.conf.beat_schedule.get("agentic-pool-sample")
    assert entry is not None
    assert entry["task"] == "app.tasks.agentic_scheduler_task.sample_agentic_pool"

    # No task_routes entry for this task name -> it lands on the default
    # "celery" queue, never on "agentic" (which would steal a scarce
    # sandbox-call worker slot for a task that does no sandbox work).
    routes = celery.conf.task_routes or {}
    assert "app.tasks.agentic_scheduler_task.sample_agentic_pool" not in routes


def test_sample_agentic_pool_task_registered_in_celery_app():
    from app.celery_app import celery
    import app.tasks.agentic_scheduler_task  # noqa: F401 - ensure module is imported/registered

    assert "app.tasks.agentic_scheduler_task.sample_agentic_pool" in celery.tasks

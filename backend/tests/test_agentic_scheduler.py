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

import uuid

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import ApiTitleMatchJob, ApiTitleMatchRow, MovieTitleBatchJob, MovieTitleIntlBatchJob


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


# ---------------------------------------------------------------------------
# Phase 5 -- topup_agentic_queue (the round-robin fairness fix)
#
# Redis: uses the SAME client the real code reaches Redis through
# (dispatch_window._get_redis(), i.e. settings.REDIS_URL) rather than a
# hardcoded localhost URL, since this suite may run inside a container where
# only the Redis service hostname resolves. Never flushdb() -- this may be a
# shared Redis also used as the live Celery broker; only the specific keys
# this module writes are cleaned up.
# ---------------------------------------------------------------------------
@pytest.fixture
def sched_redis():
    from app.title_matching.dispatch_window import _get_redis as dw_get_redis

    try:
        client = dw_get_redis()
        client.ping()
    except Exception:
        pytest.skip("redis not reachable via settings.REDIS_URL")
    yield client


def _flush_sched_keys(client, *job_ids):
    keys = ["agentic:sched:job_window", "agentic:sched:tick", "agentic:sched:rotation"]
    for jid in job_ids:
        keys += [f"batch:{jid}:rowargs", f"batch:{jid}:results",
                 f"batch-intl:{jid}:rowargs", f"batch-intl:{jid}:results"]
    for k in keys:
        try:
            client.delete(k)
        except Exception:
            pass


@pytest.fixture
def sched_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 0)
    monkeypatch.setattr(settings, "AGENTIC_BATCH_MAX_CONCURRENCY", 4)
    monkeypatch.setattr(settings, "AGENTIC_JOB_WINDOW_MIN", 2)
    monkeypatch.setattr(settings, "AGENTIC_ROUNDROBIN_CHUNK", 1)
    return settings


def _seed_three_jobs(db_engine):
    """2 domestic + 1 international job, all `processing`, 10 rows each,
    with row args pre-cached (real Redis) so enqueue_next_window never hits
    the S3-reparse fallback (no real upload exists for these synthetic
    jobs)."""
    from app.tasks import agentic_intl_match_task, agentic_match_task

    job_d1 = f"sched-d1-{uuid.uuid4().hex[:8]}"
    job_d2 = f"sched-d2-{uuid.uuid4().hex[:8]}"
    job_i1 = f"sched-i1-{uuid.uuid4().hex[:8]}"

    with Session(db_engine) as s:
        s.add(MovieTitleBatchJob(id=job_d1, status="processing", total=10, dispatched=0))
        s.add(MovieTitleBatchJob(id=job_d2, status="processing", total=10, dispatched=0))
        s.add(MovieTitleIntlBatchJob(id=job_i1, status="processing", total=10, dispatched=0))
        s.commit()

    headers = ["movie_title", "show_date", "ticketing_url"]
    rows = [{"movie_title": f"Title {i}"} for i in range(10)]
    agentic_match_task._cache_row_args(job_d1, headers, rows, False)
    agentic_match_task._cache_row_args(job_d2, headers, rows, False)

    intl_headers = ["movie_title", "show_date", "ticketing_url", "country"]
    intl_rows = [{"movie_title": f"Title {i}", "country": "GB"} for i in range(10)]
    agentic_intl_match_task._cache_row_args(job_i1, intl_headers, intl_rows, False)

    return job_d1, job_d2, job_i1


def test_topup_agentic_queue_round_robins_across_three_jobs(
    monkeypatch, db_engine, sched_redis, sched_settings
):
    """The core fairness proof: 3 concurrent jobs (2 domestic, 1 intl) ->
    published rows interleave (no run of the same job longer than
    AGENTIC_ROUNDROBIN_CHUNK) and total published == 3 * window."""
    from app.tasks import agentic_intl_match_task, agentic_match_task
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    # Stub the tick lock to always succeed -- this test is about the
    # round-robin logic, not the lock (see the overlap-safety test below for
    # the lock-stubbed-out double-tick proof).
    monkeypatch.setattr(sched, "_acquire_tick_lock", lambda **k: True)

    job_d1, job_d2, job_i1 = _seed_three_jobs(db_engine)

    published = []

    def _record(kind):
        def _fake_apply_async(args, queue=None):
            published.append((kind, args[0], args[1]))
        return _fake_apply_async

    monkeypatch.setattr(agentic_match_task.agentic_batch_row, "apply_async", _record("domestic"))
    monkeypatch.setattr(
        agentic_intl_match_task.agentic_intl_batch_row, "apply_async", _record("international")
    )

    try:
        result = sched.topup_agentic_queue()
        assert result["ok"] is True
        assert result["window"] == 2  # target_depth(8, from 2*4) // 3 active jobs, floor 2 -> 2

        assert len(published) == 3 * 2  # 3 jobs * window(2)

        # Fairness: no run of the same job_id longer than AGENTIC_ROUNDROBIN_CHUNK (1).
        run_len = 1
        for i in range(1, len(published)):
            if published[i][1] == published[i - 1][1]:
                run_len += 1
                assert run_len <= 1
            else:
                run_len = 1

        # Every job got exactly `window` rows.
        from collections import Counter

        per_job = Counter(job_id for _, job_id, _ in published)
        assert per_job == {job_d1: 2, job_d2: 2, job_i1: 2}

        # Second tick immediately after: every job already at its window ->
        # nothing more to push.
        published.clear()
        result2 = sched.topup_agentic_queue()
        assert published == []
        assert sum(result2["pushed"].values()) == 0
    finally:
        _flush_sched_keys(sched_redis, job_d1, job_d2, job_i1)


def test_topup_agentic_queue_overlap_safety_no_duplicate_publishes(
    monkeypatch, db_engine, sched_redis, sched_settings
):
    """Overlap safety: with the tick lock stubbed to ALWAYS succeed
    (simulating two ticks racing each other), running it twice back-to-back
    must never publish the same (job_id, row_index) pair twice -- proving the
    atomic CLAIM (claim_row_window's CAS), not the lock, is what prevents
    double-dispatch."""
    from app.tasks import agentic_intl_match_task, agentic_match_task
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(sched, "_acquire_tick_lock", lambda **k: True)

    job_d1, job_d2, job_i1 = _seed_three_jobs(db_engine)

    published = []

    def _record(kind):
        def _fake_apply_async(args, queue=None):
            published.append((kind, args[0], args[1]))
        return _fake_apply_async

    monkeypatch.setattr(agentic_match_task.agentic_batch_row, "apply_async", _record("domestic"))
    monkeypatch.setattr(
        agentic_intl_match_task.agentic_intl_batch_row, "apply_async", _record("international")
    )

    try:
        sched.topup_agentic_queue()
        sched.topup_agentic_queue()

        pairs = [(kind, job_id, idx) for kind, job_id, idx in published]
        assert len(pairs) == len(set(pairs)), "duplicate (job_id, row_index) publish detected"
    finally:
        _flush_sched_keys(sched_redis, job_d1, job_d2, job_i1)


def test_topup_agentic_queue_tick_lock_skips_second_immediate_call(
    monkeypatch, db_engine, sched_redis, sched_settings
):
    """With the REAL tick lock (not stubbed), a second immediate call must
    return locked=True and do no work at all -- proving the lock itself
    (an efficiency optimization) actually skips overlapping ticks."""
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    job_d1, job_d2, job_i1 = _seed_three_jobs(db_engine)

    from unittest.mock import MagicMock

    try:
        result1 = sched.topup_agentic_queue()
        assert result1.get("locked") is not True

        gather_spy = MagicMock(wraps=sched._gather_states)
        monkeypatch.setattr(sched, "_gather_states", gather_spy)

        result2 = sched.topup_agentic_queue()
        assert result2 == {"ok": True, "locked": True}
        gather_spy.assert_not_called()
    finally:
        _flush_sched_keys(sched_redis, job_d1, job_d2, job_i1)


def test_topup_agentic_queue_finalize_sweep_claims_stuck_domestic_job(
    monkeypatch, db_engine, sched_redis, sched_settings
):
    """A job fully dispatched + fully processed but never claimed for
    finalize (e.g. a worker died between the counter bump and the claim) is
    picked up by the sweep and finalize is enqueued exactly once."""
    from unittest.mock import MagicMock

    from app.tasks import agentic_match_task
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(sched, "_acquire_tick_lock", lambda **k: True)
    monkeypatch.setattr(agentic_match_task.finalize_batch, "apply_async", MagicMock())

    job_id = f"sched-stuck-{uuid.uuid4().hex[:8]}"
    with Session(db_engine) as s:
        s.add(
            MovieTitleBatchJob(
                id=job_id, status="processing", total=3, dispatched=3, processed=3
            )
        )
        s.commit()

    try:
        result = sched.topup_agentic_queue()
        assert result["finalize_swept"] == 1
        agentic_match_task.finalize_batch.apply_async.assert_called_once_with(args=[None, job_id])

        job = db_engine and Session(db_engine).get(MovieTitleBatchJob, job_id)
        assert job.finalize_claimed_at is not None

        # A second tick must not claim/enqueue it again.
        agentic_match_task.finalize_batch.apply_async.reset_mock()
        result2 = sched.topup_agentic_queue()
        assert result2["finalize_swept"] == 0
        agentic_match_task.finalize_batch.apply_async.assert_not_called()
    finally:
        _flush_sched_keys(sched_redis, job_id)


def test_topup_agentic_queue_one_pipeline_failing_does_not_block_others(
    monkeypatch, db_engine, sched_redis, sched_settings
):
    """external's scheduler_state() raising must not prevent domestic/intl
    from being topped up."""
    from app.tasks import agentic_match_task, external_match_task
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(sched, "_acquire_tick_lock", lambda **k: True)

    job_d1 = f"sched-resilient-{uuid.uuid4().hex[:8]}"
    with Session(db_engine) as s:
        s.add(MovieTitleBatchJob(id=job_d1, status="processing", total=5, dispatched=0))
        s.commit()

    headers = ["movie_title"]
    rows = [{"movie_title": f"Title {i}"} for i in range(5)]
    agentic_match_task._cache_row_args(job_d1, headers, rows, False)

    def _boom():
        raise RuntimeError("external DB is down")

    monkeypatch.setattr(external_match_task, "scheduler_state", _boom)

    published = []
    monkeypatch.setattr(
        agentic_match_task.agentic_batch_row,
        "apply_async",
        lambda args, queue=None: published.append(args[1]),
    )

    try:
        result = sched.topup_agentic_queue()
        assert result["ok"] is True
        assert len(published) > 0
        assert result["pushed"]["domestic"] > 0
    finally:
        _flush_sched_keys(sched_redis, job_d1)

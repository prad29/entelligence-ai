"""Tests for app.deleted_showtimes.job_semaphore — the per-job Redis
semaphore capping concurrent SerpApi calls at `job.workers`.

Uses the real local redis at localhost:6379 (available in this env), mirroring
test_agentic_batch_task.py's semaphore TTL test. No SerpApi/network calls.
"""

from __future__ import annotations

import uuid

import pytest

from app.deleted_showtimes import job_semaphore


@pytest.fixture
def job_id():
    return f"test-job-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def _local_redis(monkeypatch):
    """`settings.REDIS_URL` defaults to the docker-compose hostname
    'redis', which doesn't resolve in a bare local test run — point
    _get_redis at localhost instead, skipping if unreachable (mirrors
    test_agentic_batch_task.py's local_redis fixture)."""
    import redis

    try:
        client = redis.Redis.from_url("redis://localhost:6379/0")
        client.ping()
    except Exception:
        pytest.skip("local redis not reachable at localhost:6379")

    monkeypatch.setattr(job_semaphore, "_get_redis", lambda: client)
    for k in client.scan_iter(match="deleted-showtimes:sem:*"):
        client.delete(k)
    yield
    for k in client.scan_iter(match="deleted-showtimes:sem:*"):
        client.delete(k)


def test_acquire_and_release_within_cap(job_id):
    holder = job_semaphore.acquire(job_id, max_concurrency=2, timeout=5)
    assert holder != job_semaphore.FAIL_OPEN_HOLDER
    job_semaphore.release(holder)


def test_acquire_blocks_and_times_out_at_capacity(job_id):
    h1 = job_semaphore.acquire(job_id, max_concurrency=1, timeout=5)
    try:
        with pytest.raises(TimeoutError):
            job_semaphore.acquire(job_id, max_concurrency=1, timeout=0.5)
    finally:
        job_semaphore.release(h1)


def test_release_frees_a_slot_for_the_next_acquire(job_id):
    h1 = job_semaphore.acquire(job_id, max_concurrency=1, timeout=5)
    job_semaphore.release(h1)
    h2 = job_semaphore.acquire(job_id, max_concurrency=1, timeout=5)
    assert h2 != job_semaphore.FAIL_OPEN_HOLDER
    job_semaphore.release(h2)


def test_different_jobs_do_not_share_a_cap(job_id):
    other_job_id = f"test-job-{uuid.uuid4()}"
    h1 = job_semaphore.acquire(job_id, max_concurrency=1, timeout=5)
    try:
        h2 = job_semaphore.acquire(other_job_id, max_concurrency=1, timeout=5)
        job_semaphore.release(h2)
    finally:
        job_semaphore.release(h1)


def test_release_is_a_noop_for_fail_open_sentinel():
    job_semaphore.release(job_semaphore.FAIL_OPEN_HOLDER)  # must not raise


def test_release_is_a_noop_for_none():
    job_semaphore.release(None)  # must not raise


def test_acquire_fails_open_when_redis_unreachable(monkeypatch, job_id):
    monkeypatch.setattr(job_semaphore, "_get_redis", lambda: None)
    holder = job_semaphore.acquire(job_id, max_concurrency=1, timeout=5)
    assert holder == job_semaphore.FAIL_OPEN_HOLDER

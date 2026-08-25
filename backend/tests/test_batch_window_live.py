"""
REAL-BROKER fairness + concurrency-cap integration test for Phase 5
(windowed dispatch + round-robin top-up — the fairness fix).

This is THE regression test for the actual problem being solved: on the
pre-Phase-5 chord-based dispatcher, a big Job A's entire row list landed in
the broker queue at once, so a smaller Job B submitted moments later sat
behind nearly all of A's rows regardless of concurrency — B could never show
``0 < processed < total`` at the same time A did; B simply waited. This test
proves that's no longer true.

Clones tests/test_batch_chord_live.py's real-broker harness (throwaway
SQLite file DB shared with a real ``celery worker`` subprocess over a
dedicated Redis logical db), but:

  * launches the worker with ``--pool=threads`` and
    ``--concurrency=AGENTIC_BATCH_MAX_CONCURRENCY`` instead of ``--pool=solo``
    -- solo has no real concurrency at all, which would make both the
    fairness observation and the concurrency-cap check meaningless;
  * the fake runner (tests/_batch_worker_bootstrap.py) sleeps
    ``BATCH_TEST_ROW_WORK_SECONDS`` per call and tracks a concurrency
    high-water mark via Redis, so overlap is real and measurable;
  * drives ``topup_agentic_queue()`` in a loop from THIS process (no beat
    subprocess needed) instead of dispatching a chord.

Marked ``@pytest.mark.integration``; skips (never downgrades to eager mode)
if Redis or the S3 bucket used by batch_storage is unreachable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import pytest

_REDIS_URL = os.environ.get("BATCH_TEST_REDIS_URL", "redis://localhost:6379/15")
_DB_FILE = f"/tmp/batch_window_live_{uuid.uuid4().hex}.db"  # noqa: S108
_DB_URL = f"sqlite:///{_DB_FILE}"

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CONCURRENCY = 4
_ROW_WORK_SECONDS = 0.25

pytestmark = pytest.mark.integration


def _redis_reachable() -> bool:
    try:
        import redis

        redis.Redis.from_url(_REDIS_URL).ping()
        return True
    except Exception:
        return False


def _s3_reachable() -> bool:
    from app.config import settings

    if not settings.AGENTIC_BATCH_S3_BUCKET:
        return False
    try:
        import boto3

        boto3.client("s3", region_name=settings.AGENTIC_BATCH_S3_REGION).head_bucket(
            Bucket=settings.AGENTIC_BATCH_S3_BUCKET
        )
        return True
    except Exception:
        return False


@pytest.fixture
def live_env(monkeypatch):
    if not _redis_reachable():
        pytest.skip("redis not reachable — real-broker test cannot run")
    if not _s3_reachable():
        pytest.skip("AGENTIC_BATCH_S3_BUCKET not configured/reachable — real-broker test cannot run")

    import redis

    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", _REDIS_URL)
    monkeypatch.setattr(settings, "AGENTIC_BATCH_MAX_CONCURRENCY", _CONCURRENCY)
    monkeypatch.setattr(settings, "AGENTIC_JOB_WINDOW_MIN", 2)
    monkeypatch.setattr(settings, "AGENTIC_ROUNDROBIN_CHUNK", 1)
    monkeypatch.setattr(settings, "AGENTIC_QUEUE_TARGET_DEPTH", 0)

    from app.celery_app import celery

    monkeypatch.setattr(celery.conf, "broker_url", _REDIS_URL, raising=False)
    monkeypatch.setattr(celery.conf, "result_backend", _REDIS_URL, raising=False)
    celery.conf.task_always_eager = False

    from sqlmodel import SQLModel, create_engine

    import app.models  # noqa: F401

    engine = create_engine(_DB_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    import app.database as _db_module

    monkeypatch.setattr(_db_module, "engine", engine, raising=False)

    # The round-robin top-up's tick lock is stubbed out for this test: we
    # WANT every driven tick to actually do work (this test is driving
    # topup_agentic_queue() directly from the test process in a tight loop,
    # standing in for celery-beat) rather than skip because a previous tick's
    # lock TTL hasn't expired yet. Lock semantics themselves are covered by
    # test_agentic_scheduler.py's dedicated tick-lock tests; correctness here
    # (no double-dispatch) comes from claim_row_window's CAS regardless.
    from app.tasks import agentic_scheduler_task as sched

    monkeypatch.setattr(sched, "_acquire_tick_lock", lambda **k: True)

    rc = redis.Redis.from_url(_REDIS_URL)
    rc.flushdb()

    yield {"engine": engine, "redis": rc}

    rc.flushdb()
    engine.dispose()
    try:
        os.remove(_DB_FILE)
    except OSError:
        pass


def _write_upload(rows: list[str], prefix: str) -> str:
    from app.title_matching import batch_storage

    text = "movie_title,show_date,ticketing_url\n" + "\n".join(rows) + "\n"
    key = batch_storage.upload_key(f"{prefix}_{uuid.uuid4().hex}", ".csv")
    batch_storage.put_bytes(key, text.encode("utf-8-sig"))
    return key


def _start_worker() -> subprocess.Popen:
    from app.config import settings

    env = dict(os.environ)
    env["DATABASE_URL"] = _DB_URL
    env["REDIS_URL"] = _REDIS_URL
    env["BATCH_TEST_ROW_WORK_SECONDS"] = str(_ROW_WORK_SECONDS)
    env["AGENTIC_BATCH_S3_BUCKET"] = settings.AGENTIC_BATCH_S3_BUCKET
    env["AGENTIC_BATCH_S3_REGION"] = settings.AGENTIC_BATCH_S3_REGION
    env["AGENTIC_BATCH_MAX_CONCURRENCY"] = str(_CONCURRENCY)
    env["PYTHONPATH"] = _BACKEND_DIR + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "tests._batch_worker_bootstrap",
        "worker",
        "--loglevel=info",
        f"--concurrency={_CONCURRENCY}",
        "--pool=threads",
        "-Q",
        "agentic",
    ]
    return subprocess.Popen(
        cmd,
        cwd=_BACKEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_worker_ready(proc: subprocess.Popen, timeout: float = 40.0) -> list[str]:
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    import select

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            lines.extend(proc.stdout.readlines())
            raise RuntimeError("celery worker exited during boot:\n" + "".join(lines))
        ready, _, _ = select.select([proc.stdout], [], [], 1.0)
        if ready:
            line = proc.stdout.readline()
            if not line:
                continue
            lines.append(line)
            if "ready." in line or ("celery@" in line and "ready" in line):
                return lines
    raise RuntimeError("celery worker did not report ready in time:\n" + "".join(lines))


def _drain(proc: subprocess.Popen) -> str:
    try:
        return proc.stdout.read() or ""
    except Exception:
        return ""


@pytest.mark.integration
def test_small_job_progresses_concurrently_with_large_job_via_topup(live_env):
    """Dispatch a 30-row job, then ~2s later a 6-row job; drive
    topup_agentic_queue() in a loop while sampling both jobs' `processed`.
    Assert a sample exists where BOTH jobs simultaneously have
    `0 < processed < total` (impossible on the pre-Phase-5 chord-based code)
    AND the small job finishes well before the large one. Also asserts the
    concurrency high-water mark never exceeded AGENTIC_BATCH_MAX_CONCURRENCY.
    """
    engine = live_env["engine"]

    from sqlmodel import Session

    from app.models import MovieMaster, MovieTitleBatchJob
    from app.tasks.agentic_match_task import dispatch_batch
    from app.tasks.agentic_scheduler_task import topup_agentic_queue

    with Session(engine) as s:
        s.add(MovieMaster(id=4242, movie_title="Matched Movie"))
        s.commit()

    big_total = 60
    small_total = 8
    big_upload = _write_upload(
        [f"Big Title {i},2024-01-0{1 + i % 9},https://example.com/big/{i}" for i in range(big_total)],
        "windowlive_big",
    )
    # Pre-write the small job's upload BEFORE the head-start delay below, so
    # its real S3 round-trip latency (observed ~2s in this environment) does
    # NOT eat into the deliberately short head-start window and let the big
    # job finish before the small one even starts.
    small_upload = _write_upload(
        [f"Small Title {i},2024-02-0{1 + i},https://example.com/small/{i}" for i in range(small_total)],
        "windowlive_small",
    )

    big_job_id = str(uuid.uuid4())
    with Session(engine) as s:
        s.add(
            MovieTitleBatchJob(
                id=big_job_id, status="queued", total=big_total, use_poster_vision=False, file_path=big_upload
            )
        )
        s.commit()

    worker = _start_worker()
    small_job_id = None
    try:
        _wait_worker_ready(worker)

        dispatch_batch(big_job_id)

        # Give the big job a real head start before the small one arrives —
        # this is the exact scenario from the original ask.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            topup_agentic_queue()
            time.sleep(0.1)

        small_job_id = str(uuid.uuid4())
        with Session(engine) as s:
            s.add(
                MovieTitleBatchJob(
                    id=small_job_id, status="queued", total=small_total, use_poster_vision=False, file_path=small_upload
                )
            )
            s.commit()
        dispatch_batch(small_job_id)

        both_mid_flight = False
        small_completed_at = None
        big_completed_at = None
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            topup_agentic_queue()
            with Session(engine) as s:
                big = s.get(MovieTitleBatchJob, big_job_id)
                small = s.get(MovieTitleBatchJob, small_job_id)

            if (
                big is not None and small is not None
                and 0 < big.processed < big.total
                and 0 < small.processed < small.total
            ):
                both_mid_flight = True

            if small is not None and small.status == "completed" and small_completed_at is None:
                small_completed_at = time.monotonic()
            if big is not None and big.status == "completed" and big_completed_at is None:
                big_completed_at = time.monotonic()

            if big_completed_at is not None and small_completed_at is not None:
                break
            if worker.poll() is not None:
                raise RuntimeError("worker died mid-run:\n" + _drain(worker))
            time.sleep(0.1)

        assert both_mid_flight, (
            "never observed both jobs simultaneously mid-flight (0 < processed < total) "
            "-- the fairness fix is not taking effect"
        )
        assert small_completed_at is not None, "small job never completed"
        assert big_completed_at is not None, "big job never completed"
        assert small_completed_at < big_completed_at, (
            "small job did not finish before the large one -- fairness regression"
        )

        with Session(engine) as s:
            big = s.get(MovieTitleBatchJob, big_job_id)
            small = s.get(MovieTitleBatchJob, small_job_id)
        assert big.status == "completed" and big.processed == big.total
        assert small.status == "completed" and small.processed == small.total

        # Concurrency-cap check (item 13): the fake runner's high-water mark
        # must never have exceeded AGENTIC_BATCH_MAX_CONCURRENCY.
        rc = live_env["redis"]
        max_seen = int(rc.get("batchtest:concurrency:max") or 0)
        assert 0 < max_seen <= _CONCURRENCY, f"concurrency high-water mark {max_seen} exceeded cap {_CONCURRENCY}"
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)
        from app.title_matching import batch_storage

        for key in (big_upload, small_upload):
            if key:
                try:
                    batch_storage.delete(key)
                except Exception:
                    pass


@pytest.mark.integration
def test_jobs_complete_via_self_refill_alone_with_zero_topup_ticks(live_env):
    """Beat-independence: rerun a two-job scenario calling
    topup_agentic_queue() ZERO times. Both jobs must still complete via
    self-refill alone (_after_row_terminal), slower/less interleaved but
    correct -- proving celery-beat is not a single point of failure.
    """
    engine = live_env["engine"]

    from sqlmodel import Session

    from app.models import MovieMaster, MovieTitleBatchJob
    from app.tasks.agentic_match_task import dispatch_batch

    with Session(engine) as s:
        s.add(MovieMaster(id=4242, movie_title="Matched Movie"))
        s.commit()

    big_total = 12
    small_total = 4
    big_upload = _write_upload(
        [f"NoTopup Big {i},2024-03-0{1 + i % 9},https://example.com/ntbig/{i}" for i in range(big_total)],
        "windowlive_notopup_big",
    )
    small_upload = None
    big_job_id = str(uuid.uuid4())
    with Session(engine) as s:
        s.add(
            MovieTitleBatchJob(
                id=big_job_id, status="queued", total=big_total, use_poster_vision=False, file_path=big_upload
            )
        )
        s.commit()

    worker = _start_worker()
    small_job_id = None
    try:
        _wait_worker_ready(worker)

        dispatch_batch(big_job_id)
        time.sleep(1.0)

        small_upload = _write_upload(
            [f"NoTopup Small {i},2024-04-0{1 + i},https://example.com/ntsmall/{i}" for i in range(small_total)],
            "windowlive_notopup_small",
        )
        small_job_id = str(uuid.uuid4())
        with Session(engine) as s:
            s.add(
                MovieTitleBatchJob(
                    id=small_job_id, status="queued", total=small_total, use_poster_vision=False, file_path=small_upload
                )
            )
            s.commit()
        dispatch_batch(small_job_id)

        # No topup_agentic_queue() calls at all -- just poll and wait.
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            with Session(engine) as s:
                big = s.get(MovieTitleBatchJob, big_job_id)
                small = s.get(MovieTitleBatchJob, small_job_id)
            if big and small and big.status == "completed" and small.status == "completed":
                break
            if worker.poll() is not None:
                raise RuntimeError("worker died mid-run:\n" + _drain(worker))
            time.sleep(0.3)

        with Session(engine) as s:
            big = s.get(MovieTitleBatchJob, big_job_id)
            small = s.get(MovieTitleBatchJob, small_job_id)
        assert big is not None and big.status == "completed", f"big job status={big.status if big else None}"
        assert small is not None and small.status == "completed", f"small job status={small.status if small else None}"
        assert big.processed == big.total
        assert small.processed == small.total
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)
        from app.title_matching import batch_storage

        for key in (big_upload, small_upload):
            if key:
                try:
                    batch_storage.delete(key)
                except Exception:
                    pass

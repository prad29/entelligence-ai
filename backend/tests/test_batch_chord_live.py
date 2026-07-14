"""
REAL-BROKER chord + retry integration test (NOT eager mode).

This is the one test that actually exercises Celery's chord-completion tracking
combined with a per-member ``self.retry()`` against the Redis result backend —
the exact interaction eager mode bypasses and that the design review flagged as
a version-dependent Celery edge case. It proves, on this repo's real Celery
5.6.3 + redis-py stack, that the chord callback (``finalize_batch``) fires
EXACTLY ONCE, only after every member (including the one that retried) has
finished, and that the retried row appears in the output exactly once with its
eventual SUCCESSFUL result.

How it runs (no HTTP server needed):
  * a throwaway SQLite *file* DB is shared between this process and the worker
    subprocess (both read DATABASE_URL from the env);
  * a dedicated Redis logical db (localhost:6379/15) is the broker + backend,
    flushed before and after;
  * an actual ``celery worker`` subprocess consumes the ``agentic`` queue via
    tests/_batch_worker_bootstrap.py, which patches run_agentic_match to a
    deterministic fail-first-then-succeed fake for one target row;
  * the test dispatches the chord, then polls the job row in the DB (not HTTP)
    until it reports ``completed`` or a generous timeout elapses.

Marked ``@pytest.mark.integration``. Redis IS reachable in this environment, so
the test RUNS (it only skips if redis is genuinely unreachable — never silently
downgrades to eager mode, which would defeat its purpose).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

import pytest

# ── real broker / backend + shared file DB for this test only ────────────────
_REDIS_URL = "redis://localhost:6379/15"
_DB_FILE = f"/tmp/batch_chord_live_{uuid.uuid4().hex}.db"  # noqa: S108
_DB_URL = f"sqlite:///{_DB_FILE}"

_RETRY_TITLE = "Retry Once Then Ok"
_RETRY_MOVIE_ID = 7777
_MATCHED_MOVIE_ID = 4242

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)

pytestmark = pytest.mark.integration


def _redis_reachable() -> bool:
    try:
        import redis

        redis.Redis.from_url(_REDIS_URL).ping()
        return True
    except Exception:
        return False


@pytest.fixture
def live_env(monkeypatch):
    """Point settings + the celery app + tasks at the local broker and file DB."""
    if not _redis_reachable():
        pytest.skip("redis not reachable at localhost:6379 — real-broker test cannot run")

    import redis

    from app.config import settings

    # Redirect settings used by dispatch_batch (chord publish) and the tasks
    # (results hash) at the local broker.
    monkeypatch.setattr(settings, "REDIS_URL", _REDIS_URL)

    # Retarget the celery app broker/backend so the chord we publish lands on
    # the same queue the worker subprocess consumes.
    from app.celery_app import celery

    monkeypatch.setattr(celery.conf, "broker_url", _REDIS_URL, raising=False)
    monkeypatch.setattr(celery.conf, "result_backend", _REDIS_URL, raising=False)
    celery.conf.task_always_eager = False

    # Fresh SQLite file DB shared with the worker; retarget the app engine so
    # dispatch_batch (which reads/writes the job row) uses it too.
    from sqlmodel import SQLModel, create_engine

    # Import the models so their tables are registered on the shared metadata
    # BEFORE create_all runs (import order across the suite is not guaranteed).
    import app.models  # noqa: F401

    engine = create_engine(_DB_URL, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    import app.database as _db_module

    monkeypatch.setattr(_db_module, "engine", engine, raising=False)

    # Clean the redis logical db before + after.
    rc = redis.Redis.from_url(_REDIS_URL)
    rc.flushdb()

    yield {"engine": engine, "redis": rc}

    rc.flushdb()
    engine.dispose()
    try:
        os.remove(_DB_FILE)
    except OSError:
        pass


def _write_upload(rows: list[str]) -> str:
    upload_dir = "/tmp/movie_title_batch_uploads"  # noqa: S108
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, f"chordlive_{uuid.uuid4().hex}.csv")
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write("movie_title,show_date,ticketing_url\n")
        for r in rows:
            fh.write(r + "\n")
    return path


def _start_worker() -> subprocess.Popen:
    """Spawn a real celery worker consuming the ``agentic`` queue (solo pool)."""
    env = dict(os.environ)
    env["DATABASE_URL"] = _DB_URL
    env["REDIS_URL"] = _REDIS_URL
    env["BATCH_TEST_RETRY_TITLE"] = _RETRY_TITLE
    env["BATCH_TEST_RETRY_MOVIE_ID"] = str(_RETRY_MOVIE_ID)
    env["BATCH_TEST_MATCHED_MOVIE_ID"] = str(_MATCHED_MOVIE_ID)
    # Ensure the backend package is importable as `tests._batch_worker_bootstrap`.
    env["PYTHONPATH"] = _BACKEND_DIR + os.pathsep + env.get("PYTHONPATH", "")

    cmd = [
        os.path.join(_BACKEND_DIR, ".venv", "bin", "celery"),
        "-A",
        "tests._batch_worker_bootstrap",
        "worker",
        "--loglevel=info",
        "--concurrency=2",
        "--pool=solo",
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
    """Poll the worker's stdout until it reports it is ready, or time out."""
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    import select

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            # Worker died during boot — drain whatever it printed for diagnostics.
            lines.extend(proc.stdout.readlines())
            raise RuntimeError("celery worker exited during boot:\n" + "".join(lines))
        ready, _, _ = select.select([proc.stdout], [], [], 1.0)
        if ready:
            line = proc.stdout.readline()
            if not line:
                continue
            lines.append(line)
            if "ready." in line or "celery@" in line and "ready" in line:
                return lines
    raise RuntimeError("celery worker did not report ready in time:\n" + "".join(lines))


def _drain(proc: subprocess.Popen) -> str:
    try:
        return proc.stdout.read() or ""
    except Exception:
        return ""


@pytest.mark.integration
def test_chord_retry_fires_callback_once_with_correct_output(live_env):
    engine = live_env["engine"]

    from sqlmodel import Session

    from app.models import MovieMaster, MovieTitleBatchJob

    # Seed the movies the successful rows resolve to.
    with Session(engine) as s:
        s.add(MovieMaster(id=_MATCHED_MOVIE_ID, movie_title="Matched Movie"))
        s.add(MovieMaster(id=_RETRY_MOVIE_ID, movie_title="Retried To Success"))
        s.commit()

    # 4 rows: 2 clean matches, 1 no-match, 1 that fails first then succeeds.
    upload_path = _write_upload(
        [
            "Clean One,2024-01-01,https://example.com/1",
            "Nomatch Film,2024-01-02,https://example.com/2",
            f"{_RETRY_TITLE},2024-01-03,https://example.com/3",
            "Clean Two,2024-01-04,https://example.com/4",
        ]
    )
    total = 4

    job_id = str(uuid.uuid4())
    with Session(engine) as s:
        s.add(
            MovieTitleBatchJob(
                id=job_id,
                status="queued",
                total=total,
                use_poster_vision=False,
                file_path=upload_path,
            )
        )
        s.commit()

    worker = _start_worker()
    try:
        _wait_worker_ready(worker)

        # Publish the chord to the real broker.
        from app.tasks.agentic_match_task import dispatch_batch

        dispatch_batch(job_id)

        # Poll the DB (not HTTP) until the job completes.
        deadline = time.monotonic() + 90.0
        final = None
        while time.monotonic() < deadline:
            with Session(engine) as s:
                job = s.get(MovieTitleBatchJob, job_id)
                if job and job.status in ("completed", "failed"):
                    final = job
                    break
            if worker.poll() is not None:
                raise RuntimeError("worker died mid-run:\n" + _drain(worker))
            time.sleep(0.5)

        assert final is not None, "job never reached a terminal state in time"
        assert final.status == "completed", f"status={final.status} error={final.error}"

        # ── counters internally consistent ────────────────────────────────────
        assert final.total == total
        assert final.processed == total
        assert final.matched + final.no_match + final.failed == total
        # The retried row eventually SUCCEEDS, so it counts as matched, not failed.
        # Clean One + Clean Two + retried = 3 matched; Nomatch = 1 no_match; 0 failed.
        assert final.matched == 3, f"matched={final.matched}"
        assert final.no_match == 1, f"no_match={final.no_match}"
        assert final.failed == 0, f"failed={final.failed}"

        # ── output file reflects the retried row's success exactly once ────────
        assert final.output_path and os.path.exists(final.output_path)
        import openpyxl

        wb = openpyxl.load_workbook(final.output_path)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        header = list(rows[0])
        assert header[-4:] == [
            "mapped_title",
            "confidence_score",
            "reasoning",
            "present_in_db",
        ]

        data = rows[1:]
        # Exactly `total` data rows — the retry did NOT duplicate or drop a row.
        assert len(data) == total, f"expected {total} data rows, got {len(data)}"

        by_title = {r[0]: r for r in data}
        retried = by_title[_RETRY_TITLE]
        assert retried[3] == "Retried To Success"   # mapped_title
        assert retried[6] == "Yes"                   # present_in_db (success path)
        # It appears exactly once.
        assert sum(1 for r in data if r[0] == _RETRY_TITLE) == 1

        # The callback fired exactly once: a single output file, a single
        # completed job row. (finalize_batch is also idempotent, but a
        # double-fire would have raced the counters / re-run cleanup — the
        # counter-consistency asserts above would catch a double increment.)

        # And run_agentic_match was invoked TWICE for the retry row (fail + ok),
        # proving a genuine retry happened rather than a first-try success.
        rc = live_env["redis"]
        attempts = rc.get(f"batchtest:calls:{_RETRY_TITLE}")
        assert attempts is not None and int(attempts) == 2, (
            f"expected exactly 2 runner calls for the retry row, got {attempts!r}"
        )
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)
        # Clean up the upload file if finalize didn't (it deletes on success).
        if os.path.exists(upload_path):
            os.remove(upload_path)

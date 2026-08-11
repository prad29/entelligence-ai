"""
Tests for the Deleted Showtimes Check Celery tasks (process_batch,
finalize_job, dispatch_job) and the abort guardrail.

Coverage:
  (a) a successful batch bumps processed + true/false/unknown counters and
      stores every row's result;
  (b) a SerpAuthError (401/403) marks the job aborted and records every row
      in the batch as RUN_ABORTED, without raising;
  (c) an unexpected exception anywhere in the SerpApi/parsing path (e.g. a
      malformed response) is caught by the broad guard — the task returns
      normally instead of raising, so a chord header task failing here can
      never wedge finalize_job (this is the exact regression this test
      suite exists to catch — see the CRITICAL review finding it fixes);
  (d) once job.aborted is set, a batch that hasn't run yet short-circuits
      without calling SerpApi at all;
  (e) consecutive_failures crosses the abort threshold and sets job.aborted;
  (f) finalize_job is idempotent and no-ops on an already-terminal job.

DB: in-memory sqlite via SQLModel metadata (mirrors test_agentic_batch_task.py).
Row-result storage and the per-job semaphore are patched to avoid any real
Redis/SerpApi dependency — no network calls, no SerpApi credits spent.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.deleted_showtimes.core import FALSE_, TRUE_, UNKNOWN_, Listing
from app.deleted_showtimes.serp_client import SerpAuthError
from app.models import DeletedShowtimeJob


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
    import app.tasks.deleted_showtime_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    def _store(job_id, row_index, row):
        payload = {
            "verdict": row.verdict,
            "reason": row.reason,
            "published": row.published,
            "nearest": row.nearest,
            "source_query": row.source_query,
            "theater_verified": row.theater_verified,
            "google_theater": row.google_theater,
            "google_address": row.google_address,
        }
        fake_hash[str(row_index)] = json.dumps(payload)

    monkeypatch.setattr(task_mod, "_store_row_result", _store)
    monkeypatch.setattr(task_mod.job_semaphore, "acquire", lambda *a, **k: "holder-x")
    monkeypatch.setattr(task_mod.job_semaphore, "release", lambda *a, **k: None)
    return task_mod


def _make_job(engine, total=2, **overrides):
    job_id = "job-test-1"
    with Session(engine) as s:
        s.add(DeletedShowtimeJob(id=job_id, status="processing", total=total, **overrides))
        s.commit()
    return job_id


def _get_job(engine, job_id):
    with Session(engine) as s:
        return s.get(DeletedShowtimeJob, job_id)


def _row_payloads():
    return [
        {"title": "Dune", "show_time_raw": "7:00 PM", "show_min": 19 * 60},
        {"title": "Barbie", "show_time_raw": "8:00 PM", "show_min": 20 * 60},
    ]


# ---------------------------------------------------------------------------
# (a) successful batch -> atomic counters + stored results
# ---------------------------------------------------------------------------
def test_successful_batch_bumps_counters_and_stores_results(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine)

    ok_listing = Listing(ok=True, query="AMC Wayne 14", theater_verified=True,
                          by_title={"dune": [(19 * 60, "7:00 PM", "Standard")]},
                          titles_seen=["Dune"], total_times=1)

    import app.tasks.deleted_showtime_task as task_mod
    from unittest.mock import patch

    with patch.object(task_mod, "_attempt", return_value=ok_listing):
        task_mod.process_batch.run(job_id, "AMC Wayne 14", "2026-08-06", [0, 1], _row_payloads())

    job = _get_job(db_engine, job_id)
    assert job.processed == 2
    assert job.false_count == 1  # Dune published at the exact minute
    assert job.unknown_count == 1  # Barbie not listed -> UNABLE_TO_DETERMINE
    assert job.consecutive_failures == 0
    assert not job.aborted

    stored_0 = json.loads(fake_hash["0"])
    assert stored_0["verdict"] == FALSE_


# ---------------------------------------------------------------------------
# (b) SerpAuthError -> job aborted, batch recorded, task never raises
# ---------------------------------------------------------------------------
def test_serp_auth_error_aborts_job_without_raising(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine)

    import app.tasks.deleted_showtime_task as task_mod
    from unittest.mock import patch

    with patch.object(task_mod, "_attempt", side_effect=SerpAuthError("bad key")):
        task_mod.process_batch.run(job_id, "AMC Wayne 14", "2026-08-06", [0, 1], _row_payloads())

    job = _get_job(db_engine, job_id)
    assert job.aborted
    assert "bad key" in job.error
    assert job.processed == 2
    assert job.unknown_count == 2
    assert json.loads(fake_hash["0"])["verdict"] == UNKNOWN_
    assert json.loads(fake_hash["0"])["reason"] == "RUN_ABORTED"


# ---------------------------------------------------------------------------
# (c) unexpected exception is swallowed, not raised — the chord-wedge guard
# ---------------------------------------------------------------------------
def test_unexpected_exception_is_caught_and_recorded_not_raised(patched_task, db_engine, fake_hash):
    """Regression test for the CRITICAL review finding: process_batch's
    docstring claims it never raises, but had no broad exception guard.
    A malformed SerpApi response (or any other unexpected error) must be
    swallowed here — otherwise the chord header task fails and finalize_job
    (no link_error registered) never runs, wedging the job forever."""
    job_id = _make_job(db_engine)

    import app.tasks.deleted_showtime_task as task_mod
    from unittest.mock import patch

    with patch.object(task_mod, "_attempt", side_effect=KeyError("unexpected shape")):
        # Must not raise.
        task_mod.process_batch.run(job_id, "AMC Wayne 14", "2026-08-06", [0, 1], _row_payloads())

    job = _get_job(db_engine, job_id)
    assert job.processed == 2
    assert job.unknown_count == 2
    assert job.consecutive_failures == 1
    stored_0 = json.loads(fake_hash["0"])
    assert stored_0["verdict"] == UNKNOWN_
    assert "BATCH_TASK_ERROR" in stored_0["reason"]


def test_semaphore_timeout_is_caught_and_recorded_not_raised(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine)

    import app.tasks.deleted_showtime_task as task_mod

    task_mod.job_semaphore.acquire = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("no slot"))

    # Must not raise.
    task_mod.process_batch.run(job_id, "AMC Wayne 14", "2026-08-06", [0, 1], _row_payloads())

    job = _get_job(db_engine, job_id)
    assert job.processed == 2
    assert job.unknown_count == 2


# ---------------------------------------------------------------------------
# (d) once aborted, a not-yet-run batch short-circuits without calling SerpApi
# ---------------------------------------------------------------------------
def test_already_aborted_job_skips_serpapi_call(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine, aborted=True, error="prior abort")

    import app.tasks.deleted_showtime_task as task_mod
    from unittest.mock import patch

    with patch.object(task_mod, "_attempt") as mock_attempt:
        task_mod.process_batch.run(job_id, "AMC Wayne 14", "2026-08-06", [0, 1], _row_payloads())
        mock_attempt.assert_not_called()

    job = _get_job(db_engine, job_id)
    assert job.processed == 2
    assert job.unknown_count == 2
    for idx in ("0", "1"):
        assert json.loads(fake_hash[idx])["reason"] == "RUN_ABORTED"


# ---------------------------------------------------------------------------
# (e) consecutive_failures crosses the threshold -> job.aborted
# ---------------------------------------------------------------------------
def test_consecutive_failures_crossing_threshold_aborts_job(patched_task, db_engine, fake_hash, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DELETED_SHOWTIME_ABORT_AFTER", 2)
    job_id = _make_job(db_engine, total=4)

    import app.tasks.deleted_showtime_task as task_mod
    from unittest.mock import patch

    failing_listing = Listing(ok=False, reason="NO_SHOWTIMES_PANEL")

    with patch.object(task_mod, "_attempt", return_value=failing_listing):
        task_mod.process_batch.run(job_id, "Theater A", "2026-08-06", [0], _row_payloads()[:1])
        job = _get_job(db_engine, job_id)
        assert job.consecutive_failures == 1
        assert not job.aborted

        task_mod.process_batch.run(job_id, "Theater B", "2026-08-06", [1], _row_payloads()[1:])
        job = _get_job(db_engine, job_id)
        assert job.consecutive_failures == 2
        assert job.aborted
        assert "Aborted" in job.error


def test_success_resets_consecutive_failures(patched_task, db_engine, fake_hash, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DELETED_SHOWTIME_ABORT_AFTER", 5)
    job_id = _make_job(db_engine, total=4, consecutive_failures=3)

    import app.tasks.deleted_showtime_task as task_mod
    from unittest.mock import patch

    ok_listing = Listing(ok=True, query="q", by_title={}, titles_seen=[], total_times=1)

    with patch.object(task_mod, "_attempt", return_value=ok_listing):
        task_mod.process_batch.run(job_id, "Theater A", "2026-08-06", [0], _row_payloads()[:1])

    job = _get_job(db_engine, job_id)
    assert job.consecutive_failures == 0


# ---------------------------------------------------------------------------
# (f) finalize_job is idempotent
# ---------------------------------------------------------------------------
def test_finalize_job_noops_on_already_terminal_job(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine, total=1)
    with Session(db_engine) as s:
        job = s.get(DeletedShowtimeJob, job_id)
        job.status = "completed"
        job.output_path = "deleted-showtimes/outputs/already-done.xlsx"
        s.add(job)
        s.commit()

    import app.tasks.deleted_showtime_task as task_mod

    called = {"put_bytes": False}
    monkeypatch.setattr(task_mod.storage, "put_bytes", lambda *a, **k: called.__setitem__("put_bytes", True))

    task_mod.finalize_job(None, job_id)

    assert called["put_bytes"] is False
    job = _get_job(db_engine, job_id)
    assert job.status == "completed"
    assert job.output_path == "deleted-showtimes/outputs/already-done.xlsx"


def test_finalize_job_marks_failed_when_aborted(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine, total=1, aborted=True, error="Aborted: too many failures",
                        file_path="deleted-showtimes/uploads/job-test-1.csv")

    import app.tasks.deleted_showtime_task as task_mod

    monkeypatch.setattr(task_mod.storage, "delete", lambda *a, **k: None)
    monkeypatch.setattr(task_mod, "_get_redis", lambda: type("R", (), {"delete": lambda self, *a: None})())

    task_mod.finalize_job(None, job_id)

    job = _get_job(db_engine, job_id)
    assert job.status == "failed"
    assert job.error == "Aborted: too many failures"

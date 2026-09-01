"""Phase 4 tests for app.tasks.lobby_check_task: dispatch/claim/finalize
mechanics and the row task's success/failure/retry branches. In-memory
SQLite via SQLModel metadata (same convention as test_external_match_task.py)
-- no real Bedrock calls; extract_material_record and images.fetch_image are
monkeypatched.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import update
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.lobby_check.errors import (
    LobbyCheckImageError,
    LobbyCheckSchemaError,
    LobbyCheckThrottleError,
    LobbyCheckTransientError,
)
from app.lobby_check.types import ExtractionResult
from app.models import ApiKey, LobbyCheckJob, LobbyCheckRow

# Captured at import time, BEFORE the patched_task fixture replaces
# task_mod.enqueue_next_window with a no-op mock.
import app.tasks.lobby_check_task as _real_task_mod  # noqa: E402
_REAL_ENQUEUE_NEXT_WINDOW = _real_task_mod.enqueue_next_window


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
def patched_task(monkeypatch, db_engine):
    import app.tasks.lobby_check_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(task_mod.lobby_check_finalize_job, "apply_async", MagicMock())
    # Row-level unit tests invoke lobby_check_row.run(...) directly, not
    # through lobby_check_dispatch_job -- self-refill's real claim/publish
    # mechanics aren't under test here (see the enqueue_next_window tests
    # below for that).
    monkeypatch.setattr(task_mod, "enqueue_next_window", MagicMock(return_value=0))
    return task_mod


def _make_key(engine, key_id="k1"):
    with Session(engine) as s:
        s.add(ApiKey(id=key_id, key_hash=f"hash-{key_id}", key_prefix="lc-"))
        s.commit()
    return key_id


def _make_job(engine, job_id="lc-job-1", api_key_id="k1"):
    _make_key(engine, api_key_id)
    with Session(engine) as s:
        s.add(LobbyCheckJob(id=job_id, api_key_id=api_key_id, phase="processing"))
        s.commit()
    return job_id


def _add_row(engine, job_id, photo_id, status="pending"):
    with Session(engine) as s:
        row = LobbyCheckRow(
            job_id=job_id,
            photo_id=photo_id,
            image_url="https://mm-intelligence.s3.amazonaws.com/lobby/x.jpg",
            input_json=json.dumps({"photo_id": photo_id}),
            status=status,
        )
        s.add(row)
        s.commit()
        return row.id


def _get_job(engine, job_id):
    with Session(engine) as s:
        return s.get(LobbyCheckJob, job_id)


def _get_row(engine, row_id):
    with Session(engine) as s:
        return s.get(LobbyCheckRow, row_id)


def _valid_record(**overrides) -> dict:
    rec = {
        "visual_notes": "wall-mounted glass case",
        "material_type": "One Sheet",
        "confidence_material_type": 0.95,
        "movie_title": "Inception",
        "confidence_movie_title": 0.9,
        "material_quantity": 1,
        "confidence_material_quantity": 0.85,
        "defects": [],
        "defect_evidence": "",
        "material_condition": "good",
        "confidence_material_condition": 0.9,
    }
    rec.update(overrides)
    return rec


def _fake_extraction_ok(**overrides) -> ExtractionResult:
    return ExtractionResult(
        record=_valid_record(**overrides), framing="wide",
        input_tokens=1500, output_tokens=400, cost_usd=0.0018, latency_ms=1200,
    )


def _patched_extract(monkeypatch, result_or_exc):
    import app.lobby_check.extractor as extractor_mod

    if isinstance(result_or_exc, Exception):
        monkeypatch.setattr(
            extractor_mod, "extract_material_record",
            MagicMock(side_effect=result_or_exc),
        )
    else:
        monkeypatch.setattr(
            extractor_mod, "extract_material_record",
            MagicMock(return_value=result_or_exc),
        )


def _patched_fetch_ok(monkeypatch):
    import app.lobby_check.images as images_mod

    monkeypatch.setattr(images_mod, "fetch_image", MagicMock(return_value=b"jpegbytes"))
    monkeypatch.setattr(images_mod, "image_framing", MagicMock(return_value=("wide", 768, 512)))


# ---------------------------------------------------------------------------
# fair-share window formula
# ---------------------------------------------------------------------------

def test_compute_job_window_single_job_gets_full_depth(patched_task):
    assert patched_task._compute_job_window(1) == patched_task._target_queue_depth()


def test_compute_job_window_divides_across_active_jobs(patched_task):
    depth = patched_task._target_queue_depth()
    assert patched_task._compute_job_window(4) == max(2, depth // 4)


def test_compute_job_window_floors_at_job_window_min(patched_task):
    from app.config import settings

    assert patched_task._compute_job_window(1000) == settings.LOBBY_CHECK_JOB_WINDOW_MIN


# ---------------------------------------------------------------------------
# _remaining_row_count / _after_row_terminal claim mechanics
# ---------------------------------------------------------------------------

def test_remaining_row_count_ignores_terminal_rows(patched_task, db_engine):
    job_id = _make_job(db_engine)
    _add_row(db_engine, job_id, 1, status="pending")
    _add_row(db_engine, job_id, 2, status="completed")
    _add_row(db_engine, job_id, 3, status="failed")

    with Session(db_engine) as s:
        assert patched_task._remaining_row_count(s, job_id) == 1


def test_after_row_terminal_claims_once_when_last_row_succeeds(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")
    r2 = _add_row(db_engine, job_id, 2, status="pending")

    _patched_fetch_ok(monkeypatch)
    _patched_extract(monkeypatch, _fake_extraction_ok())

    patched_task.lobby_check_row.run(job_id, r1)
    patched_task.lobby_check_finalize_job.apply_async.assert_not_called()

    patched_task.lobby_check_row.run(job_id, r2)

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is not None
    patched_task.lobby_check_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])

    # A second trigger attempt is a no-op.
    patched_task._after_row_terminal(job_id)
    patched_task.lobby_check_finalize_job.apply_async.assert_called_once()


def test_after_row_terminal_claims_once_when_last_row_fails(patched_task, db_engine):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    patched_task._record_failed_row(job_id, r1, "boom")

    job = _get_job(db_engine, job_id)
    assert job.rows_processed == 1
    assert job.rows_failed == 1
    assert job.finalize_claimed_at is not None
    patched_task.lobby_check_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])


def test_retry_regression_finalizes_a_second_time_after_retry_completes(patched_task, db_engine, monkeypatch):
    """A naive rows_processed==rows_total predicate would already be
    satisfied before the retried row finishes -- the direct regression test
    for the NOT EXISTS-based completion predicate."""
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")
    r2 = _add_row(db_engine, job_id, 2, status="pending")

    _patched_fetch_ok(monkeypatch)

    _patched_extract(monkeypatch, _fake_extraction_ok())
    patched_task.lobby_check_row.run(job_id, r1)

    _patched_extract(monkeypatch, LobbyCheckSchemaError("boom"))
    patched_task.lobby_check_row.run(job_id, r2)

    job = _get_job(db_engine, job_id)
    assert job.rows_processed == 2
    assert job.rows_failed == 1
    assert job.finalize_claimed_at is not None
    patched_task.lobby_check_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])

    # Simulate a caller-driven retry flipping r2 back to pending and clearing
    # finalize_claimed_at, WITHOUT re-invoking any real retry endpoint.
    with Session(db_engine) as s:
        row = s.get(LobbyCheckRow, r2)
        row.status = "pending"
        s.add(row)
        s.execute(
            update(LobbyCheckJob).where(LobbyCheckJob.id == job_id)
            .values(phase="processing", finalize_claimed_at=None)
        )
        s.commit()

    with Session(db_engine) as s:
        assert patched_task._remaining_row_count(s, job_id) == 1

    patched_task.lobby_check_finalize_job.apply_async.reset_mock()

    _patched_extract(monkeypatch, _fake_extraction_ok())
    patched_task.lobby_check_row.run(job_id, r2)

    job = _get_job(db_engine, job_id)
    assert job.rows_failed == 0  # cleared by the is_retry=-1 adjustment
    assert job.rows_succeeded == 2
    assert job.finalize_claimed_at is not None
    patched_task.lobby_check_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])


def test_unexpected_exception_never_wedges_the_job(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    import app.lobby_check.images as images_mod
    monkeypatch.setattr(images_mod, "fetch_image", MagicMock(side_effect=RuntimeError("boom")))

    patched_task.lobby_check_row.run(job_id, r1)

    row = _get_row(db_engine, r1)
    assert row.status == "failed"
    assert "boom" in row.error
    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is not None
    patched_task.lobby_check_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])


# ---------------------------------------------------------------------------
# lobby_check_row: outcome-specific persistence + counters
# ---------------------------------------------------------------------------

def test_row_success_persists_all_fields_and_bumps_counters(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    _patched_fetch_ok(monkeypatch)
    _patched_extract(monkeypatch, _fake_extraction_ok())

    patched_task.lobby_check_row.run(job_id, r1)

    row = _get_row(db_engine, r1)
    assert row.status == "completed"
    assert row.movie_title == "Inception"
    assert row.material_type == "One Sheet"
    assert row.material_quantity == 1
    assert row.material_condition == "good"
    assert json.loads(row.defects_json) == []
    assert row.condition_conflict is False

    job = _get_job(db_engine, job_id)
    assert job.rows_processed == 1
    assert job.rows_succeeded == 1
    assert job.rows_failed == 0
    assert job.rows_needs_review == 0


def test_row_low_confidence_bumps_needs_review(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    _patched_fetch_ok(monkeypatch)
    _patched_extract(monkeypatch, _fake_extraction_ok(confidence_material_condition=0.2))

    patched_task.lobby_check_row.run(job_id, r1)

    job = _get_job(db_engine, job_id)
    assert job.rows_needs_review == 1


def test_row_image_fetch_failure_records_failed_no_retry_attempted(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    import app.lobby_check.images as images_mod
    monkeypatch.setattr(
        images_mod, "fetch_image",
        MagicMock(side_effect=LobbyCheckImageError("404 for image")),
    )

    patched_task.lobby_check_row.run(job_id, r1)

    row = _get_row(db_engine, r1)
    assert row.status == "failed"
    assert "404" in row.error
    job = _get_job(db_engine, job_id)
    assert job.rows_failed == 1


def test_row_schema_error_records_failed_no_retry_attempted(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    _patched_fetch_ok(monkeypatch)
    _patched_extract(monkeypatch, LobbyCheckSchemaError("still invalid after repair"))

    patched_task.lobby_check_row.run(job_id, r1)

    row = _get_row(db_engine, r1)
    assert row.status == "failed"
    assert "still invalid" in row.error


def test_row_throttle_exhausted_retries_records_failed(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    _patched_fetch_ok(monkeypatch)
    _patched_extract(monkeypatch, LobbyCheckThrottleError("throttled"))

    raw_fn = patched_task.lobby_check_row.run.__func__
    fake_self = MagicMock()
    fake_self.request.retries = 99
    fake_self.max_retries = 2

    raw_fn(fake_self, job_id, r1)

    fake_self.retry.assert_not_called()
    row = _get_row(db_engine, r1)
    assert row.status == "failed"
    assert "throttled" in row.error


def test_row_transient_exhausted_retries_records_failed(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")

    _patched_fetch_ok(monkeypatch)
    _patched_extract(monkeypatch, LobbyCheckTransientError("model not ready"))

    raw_fn = patched_task.lobby_check_row.run.__func__
    fake_self = MagicMock()
    fake_self.request.retries = 99
    fake_self.max_retries = 2

    raw_fn(fake_self, job_id, r1)

    fake_self.retry.assert_not_called()
    row = _get_row(db_engine, r1)
    assert row.status == "failed"


# ---------------------------------------------------------------------------
# enqueue_next_window: real claim/publish mechanics (unmocked)
# ---------------------------------------------------------------------------

def test_enqueue_next_window_claims_each_pending_row_exactly_once(monkeypatch, db_engine):
    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(_real_task_mod.lobby_check_row, "apply_async", MagicMock())

    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, 1, status="pending")
    r2 = _add_row(db_engine, job_id, 2, status="pending")

    won_first = _REAL_ENQUEUE_NEXT_WINDOW(job_id, 10)
    won_second = _REAL_ENQUEUE_NEXT_WINDOW(job_id, 10)  # doubled call -- nothing left to claim

    assert won_first == 2
    assert won_second == 0
    assert _real_task_mod.lobby_check_row.apply_async.call_count == 2

    with Session(db_engine) as s:
        rows = s.exec(select(LobbyCheckRow).where(LobbyCheckRow.job_id == job_id)).all()
        assert all(r.status == "dispatched" for r in rows)


def test_enqueue_next_window_noop_when_job_not_processing(monkeypatch, db_engine):
    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(_real_task_mod.lobby_check_row, "apply_async", MagicMock())

    job_id = _make_job(db_engine)
    with Session(db_engine) as s:
        job = s.get(LobbyCheckJob, job_id)
        job.phase = "completed"
        s.add(job)
        s.commit()
    _add_row(db_engine, job_id, 1, status="pending")

    assert _REAL_ENQUEUE_NEXT_WINDOW(job_id, 10) == 0
    _real_task_mod.lobby_check_row.apply_async.assert_not_called()

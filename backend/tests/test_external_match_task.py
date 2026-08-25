"""
Tests for app.tasks.external_match_task's Phase 4 additions:

  (a) _remaining_row_count counts only non-terminal (not completed/failed)
      rows for a job.
  (b) _after_row_terminal claims finalize exactly once when the last
      outstanding row completes (success), and a second attempt is a no-op.
  (c) the same when the last row goes through the failed-row path.
  (d) the external retry regression (finding #3): completing a job, then
      simulating external_retry_rows_task putting one row back to
      'pending' + clearing finalize_claimed_at, then completing that row
      again must claim finalize a SECOND time. A naive "rows_processed ==
      rows_total" predicate would already have been satisfied before the
      retry even ran, so this is the direct regression test for finding #3.
  (e) external_retry_rows_task clears finalize_claimed_at in the same
      transaction that flips rows back to pending.

DB: in-memory sqlite via SQLModel metadata (same convention as
test_agentic_batch_task.py).
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import ApiTitleMatchJob, ApiTitleMatchRow


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
    import app.tasks.external_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    from unittest.mock import MagicMock

    monkeypatch.setattr(task_mod.external_finalize_job, "apply_async", MagicMock())
    return task_mod


def _make_job(engine, job_id="ext-job-1", market="domestic"):
    with Session(engine) as s:
        s.add(ApiTitleMatchJob(id=job_id, api_key_id="k1", market=market, phase="processing"))
        s.commit()
    return job_id


def _add_row(engine, job_id, row_uuid, status="pending"):
    with Session(engine) as s:
        row = ApiTitleMatchRow(
            job_id=job_id,
            row_uuid=row_uuid,
            input_json=json.dumps({"movie_title": row_uuid}),
            status=status,
        )
        s.add(row)
        s.commit()
        return row.id


def _get_job(engine, job_id):
    with Session(engine) as s:
        return s.get(ApiTitleMatchJob, job_id)


def _get_row(engine, row_id):
    with Session(engine) as s:
        return s.get(ApiTitleMatchRow, row_id)


class _Result:
    def __init__(self, canonical_movie_id, suggested_movie_id, title, confidence):
        self.canonical_movie_id = canonical_movie_id
        self.suggested_movie_id = suggested_movie_id
        self.suggested_movie_title = title
        self.confidence = confidence
        self.reasoning = "matched via test"


def _fake_run_ok(title, show_date, theater, ticketing_url, market="domestic", country=None, usage_ctx=None):
    return _Result(0, 0, title, 0.9)  # id 0 -> present_in_db "No", irrelevant here


# ---------------------------------------------------------------------------
# (a) _remaining_row_count
# ---------------------------------------------------------------------------
def test_remaining_row_count_ignores_terminal_rows(patched_task, db_engine):
    job_id = _make_job(db_engine)
    _add_row(db_engine, job_id, "r1", status="pending")
    _add_row(db_engine, job_id, "r2", status="completed")
    _add_row(db_engine, job_id, "r3", status="failed")

    with Session(db_engine) as s:
        assert patched_task._remaining_row_count(s, job_id) == 1


def test_remaining_row_count_zero_when_all_terminal(patched_task, db_engine):
    job_id = _make_job(db_engine)
    _add_row(db_engine, job_id, "r1", status="completed")
    _add_row(db_engine, job_id, "r2", status="failed")

    with Session(db_engine) as s:
        assert patched_task._remaining_row_count(s, job_id) == 0


# ---------------------------------------------------------------------------
# (b)/(c) _after_row_terminal claims exactly once, success or failure path
# ---------------------------------------------------------------------------
def test_after_row_terminal_claims_once_when_last_row_succeeds(patched_task, db_engine):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, "r1", status="pending")
    r2 = _add_row(db_engine, job_id, "r2", status="pending")

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch

    with patch.object(runner_mod, "run_agentic_match", side_effect=_fake_run_ok), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        patched_task.external_match_row.run(job_id, r1)
        patched_task.external_finalize_job.apply_async.assert_not_called()

        patched_task.external_match_row.run(job_id, r2)

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is not None
    patched_task.external_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])

    # A second trigger attempt is a no-op.
    patched_task._after_row_terminal(job_id)
    patched_task.external_finalize_job.apply_async.assert_called_once()


def test_after_row_terminal_claims_once_when_last_row_fails(patched_task, db_engine):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, "r1", status="pending")

    patched_task._record_failed_row(job_id, r1, "boom")

    job = _get_job(db_engine, job_id)
    assert job.rows_processed == 1
    assert job.rows_failed == 1
    assert job.finalize_claimed_at is not None
    patched_task.external_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])


# ---------------------------------------------------------------------------
# (d) THE regression test for finding #3: a naive rows_processed==rows_total
#     predicate would already be satisfied before the retried row finishes.
# ---------------------------------------------------------------------------
def test_external_retry_finalizes_a_second_time_after_retry_completes(patched_task, db_engine):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, "r1", status="pending")
    r2 = _add_row(db_engine, job_id, "r2", status="pending")

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch

    with patch.object(sem, "acquire", return_value="h"), patch.object(sem, "release"):
        # r1 succeeds, r2 fails -> job completes (with an error) the first time.
        with patch.object(runner_mod, "run_agentic_match", side_effect=_fake_run_ok):
            patched_task.external_match_row.run(job_id, r1)

        def always_fail(*a, **k):
            from app.title_matching.agentic import AgenticError

            raise AgenticError("boom")

        raw_fn = patched_task.external_match_row.run.__func__
        from unittest.mock import MagicMock

        fake_self = MagicMock()
        fake_self.request.retries = 99
        fake_self.max_retries = 99
        with patch.object(runner_mod, "run_agentic_match", side_effect=always_fail):
            raw_fn(fake_self, job_id, r2)

    job = _get_job(db_engine, job_id)
    assert job.rows_processed == 2
    assert job.rows_failed == 1
    assert job.finalize_claimed_at is not None
    patched_task.external_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])

    # --- simulate external_retry_rows_task's row-flip + finalize_claimed_at
    #     clear, WITHOUT re-invoking Celery's chord/group machinery (that
    #     part of external_retry_rows_task is exercised by test_
    #     external_retry_clears_finalize_claimed_at below).
    from sqlalchemy import update

    with Session(db_engine) as s:
        row = s.get(ApiTitleMatchRow, r2)
        row.status = "pending"
        s.add(row)
        s.execute(
            update(ApiTitleMatchJob)
            .where(ApiTitleMatchJob.id == job_id)
            .values(phase="processing", finalize_claimed_at=None)
        )
        s.commit()

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is None  # cleared -> claimable again

    # Naive-predicate sanity check: rows_processed(2) == rows_total is
    # irrelevant here since ApiTitleMatchJob doesn't even track rows_total
    # as a completion gate for external -- the row.status is what matters,
    # and r2 is back to 'pending' (non-terminal) even though rows_processed
    # was never decremented. A naive counter-equality predicate would think
    # this job is already done; ours correctly sees 1 outstanding row.
    with Session(db_engine) as s:
        assert patched_task._remaining_row_count(s, job_id) == 1

    patched_task.external_finalize_job.apply_async.reset_mock()

    # Re-run r2 (the retried row), this time succeeding.
    with patch.object(runner_mod, "run_agentic_match", side_effect=_fake_run_ok), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        patched_task.external_match_row.run(job_id, r2)

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is not None
    patched_task.external_finalize_job.apply_async.assert_called_once_with(args=[None, job_id])


# ---------------------------------------------------------------------------
# (e) external_retry_rows_task clears finalize_claimed_at in the SAME
#     transaction that flips rows back to pending.
# ---------------------------------------------------------------------------
def test_external_retry_rows_task_clears_finalize_claimed_at(patched_task, db_engine, monkeypatch):
    job_id = _make_job(db_engine)
    r1 = _add_row(db_engine, job_id, "r1", status="failed")

    # Pretend the job already finalized once.
    from datetime import datetime

    with Session(db_engine) as s:
        job = s.get(ApiTitleMatchJob, job_id)
        job.finalize_claimed_at = datetime.utcnow()
        job.phase = "completed_with_errors"
        s.add(job)
        s.commit()

    # external_retry_rows_task does `from celery import chord, group` INSIDE
    # the function body, re-imported fresh on every call -- so patching the
    # `celery` package's own attributes (looked up at call time) is what
    # actually takes effect, not patching this module's namespace. This
    # avoids touching the real broker for the chord the task builds; `.s()`
    # itself never touches the broker (it only builds a signature), so it
    # needs no patching.
    import celery as celery_pkg

    captured = {}

    class _FakeChordApplier:
        def __init__(self, header):
            captured["header"] = header

        def __call__(self, callback):
            captured["callback"] = callback
            return None

    monkeypatch.setattr(celery_pkg, "chord", _FakeChordApplier)
    monkeypatch.setattr(celery_pkg, "group", lambda sigs: sigs)

    patched_task.external_retry_rows_task(job_id, ["r1"])

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is None
    assert job.phase == "processing"
    row = _get_row(db_engine, r1)
    assert row.status == "pending"

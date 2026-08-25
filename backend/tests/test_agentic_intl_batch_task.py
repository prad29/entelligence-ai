"""
Tests for the Mode B agentic *international* batch Celery tasks
(app.tasks.agentic_intl_match_task) -- mirrors test_agentic_batch_task.py's
conventions for the domestic pipeline, focused on the Phase 4 additions:

  (a) a successful row atomically bumps processed + matched/no_match and
      stores its result, only when it is the first writer for that row
      index (HSETNX-based _store_row_result);
  (b) the counter-based finalize trigger (_after_row_terminal) claims
      exactly once when the last row completes (success or failure), and a
      second claim attempt is a no-op;
  (c) a duplicate row execution against real Redis does not double-bump
      counters.

DB: in-memory sqlite via SQLModel metadata. Redis: real local redis for the
HSETNX test; row-result storage is patched to an in-memory dict for the
counter/trigger tests so they don't need Redis.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import MovieMasterIntl, MovieTitleIntlBatchJob
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
    return {}


@pytest.fixture
def patched_task(monkeypatch, db_engine, fake_hash):
    import app.tasks.agentic_intl_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    def _store(job_id, row_index, row_result):
        fake_hash[str(row_index)] = json.dumps(row_result)
        return True

    monkeypatch.setattr(task_mod, "_store_row_result", _store)

    from unittest.mock import MagicMock

    monkeypatch.setattr(task_mod.finalize_intl_batch, "apply_async", MagicMock())
    return task_mod


def _make_job(engine, total=1, job_id="intl-job-1"):
    with Session(engine) as s:
        s.add(MovieTitleIntlBatchJob(id=job_id, status="processing", total=total))
        s.commit()
    return job_id


def _get_job(engine, job_id):
    with Session(engine) as s:
        return s.get(MovieTitleIntlBatchJob, job_id)


class _Result:
    def __init__(self, canonical_movie_id, suggested_movie_id, title, confidence):
        self.canonical_movie_id = canonical_movie_id
        self.suggested_movie_id = suggested_movie_id
        self.suggested_movie_title = title
        self.confidence = confidence
        self.reasoning = "matched via test"


def _fake_run_ok(title, show_date, theater, ticketing_url, use_poster_vision, market="international", country=None, usage_ctx=None):
    return _Result(42, 42, title, 0.9)


def test_successful_row_bumps_counters_and_stores_result(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine, total=1)
    with Session(db_engine) as s:
        s.add(MovieMasterIntl(id=42, movie_id=42, movie_title="The Matrix", country="GB"))
        s.commit()

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch

    with patch.object(runner_mod, "run_agentic_match", side_effect=_fake_run_ok), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        patched_task.agentic_intl_batch_row.run(job_id, 0, "The Matrix", None, None, "GB", False)

    job = _get_job(db_engine, job_id)
    assert job.processed == 1
    assert job.matched == 1
    stored = json.loads(fake_hash["0"])
    assert stored["present_in_db"] == "Yes"


def test_finalize_claimed_exactly_once_when_last_row_succeeds(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine, total=2)
    with Session(db_engine) as s:
        s.add(MovieMasterIntl(id=42, movie_id=42, movie_title="Whatever", country="GB"))
        s.commit()

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch

    with patch.object(runner_mod, "run_agentic_match", side_effect=_fake_run_ok), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        patched_task.agentic_intl_batch_row.run(job_id, 0, "Row0", None, None, "GB", False)
        patched_task.finalize_intl_batch.apply_async.assert_not_called()

        patched_task.agentic_intl_batch_row.run(job_id, 1, "Row1", None, None, "GB", False)

    job = _get_job(db_engine, job_id)
    assert job.finalize_claimed_at is not None
    patched_task.finalize_intl_batch.apply_async.assert_called_once_with(args=[None, job_id])

    # A second trigger attempt (e.g. a stray re-invocation) is a no-op.
    patched_task._after_row_terminal(job_id)
    patched_task.finalize_intl_batch.apply_async.assert_called_once()


def test_finalize_claimed_exactly_once_when_last_row_fails(patched_task, db_engine, fake_hash):
    job_id = _make_job(db_engine, total=1)

    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.sandbox_semaphore as sem
    from unittest.mock import patch, MagicMock

    def always_fail(*a, **k):
        raise AgenticError("boom")

    raw_fn = patched_task.agentic_intl_batch_row.run.__func__
    with patch.object(runner_mod, "run_agentic_match", side_effect=always_fail), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        fake_self = MagicMock()
        fake_self.request.retries = 4
        fake_self.max_retries = 4
        raw_fn(fake_self, job_id, 0, "Row0", None, None, "GB", False)

    job = _get_job(db_engine, job_id)
    assert job.processed == 1
    assert job.failed == 1
    assert job.finalize_claimed_at is not None
    patched_task.finalize_intl_batch.apply_async.assert_called_once_with(args=[None, job_id])


def test_hsetnx_duplicate_row_execution_does_not_double_bump():
    """Real local Redis: two identical writes for the same job/row index
    must only land once."""
    import redis

    try:
        r = redis.Redis.from_url("redis://localhost:6379/0")
        r.ping()
    except Exception:
        pytest.skip("local redis not reachable at localhost:6379 for HSETNX test")

    import app.tasks.agentic_intl_match_task as task_mod

    job_id = "intl-job-hsetnx-1"
    r.delete(task_mod._results_key(job_id))
    try:
        first = task_mod._store_row_result(job_id, 0, {"present_in_db": "Yes"})
        second = task_mod._store_row_result(job_id, 0, {"present_in_db": "Yes"})
        assert first is True
        assert second is False
        assert r.hlen(task_mod._results_key(job_id)) == 1
    finally:
        r.delete(task_mod._results_key(job_id))

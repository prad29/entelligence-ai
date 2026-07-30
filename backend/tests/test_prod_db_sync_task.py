"""
Unit tests for the "Sync from Production DB" Celery tasks
(app/tasks/prod_db_sync_task.py). Mocks the prod_db fetch/count functions and
seed_loader upsert functions, and runs against an in-memory SQLite DB —
matching the pattern in test_agentic_batch_task.py. Tasks are called directly
as plain functions (not through Celery's broker).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import MovieMasterSyncJob


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


def _make_job(engine, market: str) -> str:
    job_id = f"job-{market}-1"
    with Session(engine) as s:
        s.add(MovieMasterSyncJob(id=job_id, market=market, status="queued"))
        s.commit()
    return job_id


def _get_job(engine, job_id: str) -> MovieMasterSyncJob:
    with Session(engine) as s:
        return s.get(MovieMasterSyncJob, job_id)


# ── Domestic ─────────────────────────────────────────────────────────────────

class TestSyncMovieMasterTask:
    def test_success_sets_total_from_count_and_records_counts(self, monkeypatch, db_engine):
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)
        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_count", lambda: 42)
        monkeypatch.setattr(
            task_mod.prod_db, "fetch_fq_movie_master_rows",
            lambda: iter([{"id": 1, "movie_title": "A"}, {"id": 2, "movie_title": "B"}]),
        )
        monkeypatch.setattr(
            task_mod, "seed_from_rows",
            lambda session, rows: {"inserted": len(rows), "updated": 0, "skipped": 0},
        )
        fake_redis = MagicMock()
        monkeypatch.setattr(task_mod, "_get_redis", lambda: fake_redis)
        fake_reindex = MagicMock()
        monkeypatch.setattr(
            "app.tasks.semantic_tasks.build_semantic_index_task", MagicMock(delay=fake_reindex)
        )

        job_id = _make_job(db_engine, "domestic")
        task_mod.sync_movie_master_task(job_id)

        job = _get_job(db_engine, job_id)
        assert job.status == "completed"
        # total must come from the count helper, not stay at the model default of 0
        assert job.total == 42
        assert job.inserted == 2
        assert job.processed == 2
        fake_redis.set.assert_called_once_with(task_mod.MOVIE_MASTER_SYNC_DIRTY_KEY, "1")
        fake_reindex.assert_called_once()

    def test_no_upserts_does_not_trigger_reindex_or_redis_signal(self, monkeypatch, db_engine):
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)
        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_count", lambda: 0)
        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_rows", lambda: iter([]))
        fake_redis = MagicMock()
        monkeypatch.setattr(task_mod, "_get_redis", lambda: fake_redis)
        fake_reindex = MagicMock()
        monkeypatch.setattr(
            "app.tasks.semantic_tasks.build_semantic_index_task", MagicMock(delay=fake_reindex)
        )

        job_id = _make_job(db_engine, "domestic")
        task_mod.sync_movie_master_task(job_id)

        job = _get_job(db_engine, job_id)
        assert job.status == "completed"
        fake_redis.set.assert_not_called()
        fake_reindex.assert_not_called()

    def test_failure_sets_scrubbed_error_not_raw_exception_text(self, monkeypatch, db_engine):
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)
        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_count", lambda: 10)

        def _fetch_rows():
            raise Exception(
                "Can't connect to MySQL server on 'mmproddb.critf4jd3ef7.us-east-1.rds.amazonaws.com' "
                "Access denied for user 'AIuser'@'10.0.0.5'"
            )
            yield  # pragma: no cover - unreachable, keeps this a generator

        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_rows", _fetch_rows)
        fake_reindex = MagicMock()
        monkeypatch.setattr(
            "app.tasks.semantic_tasks.build_semantic_index_task", MagicMock(delay=fake_reindex)
        )

        job_id = _make_job(db_engine, "domestic")
        task_mod.sync_movie_master_task(job_id)

        job = _get_job(db_engine, job_id)
        assert job.status == "failed"
        assert "mmproddb" not in job.error
        assert "AIuser" not in job.error
        assert job_id in job.error
        fake_reindex.assert_not_called()

    def test_unknown_job_id_does_not_raise(self, monkeypatch, db_engine):
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)
        task_mod.sync_movie_master_task("does-not-exist")  # must not raise

    def test_crash_loading_job_still_marks_job_failed(self, monkeypatch, db_engine):
        """Regression test: a crash on the very first DB call (_load_job
        itself, e.g. a dropped connection) must not leave the job stuck at
        "queued" forever — that permanently blocks the in-flight-job guard
        in movie_title_match.py from ever starting a new sync for this
        market, since it only treats "queued"/"processing" jobs as busy."""
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)

        real_load_job = task_mod._load_job
        calls = {"n": 0}

        def _flaky_load_job(session, job_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("SSL SYSCALL error: EOF detected")
            return real_load_job(session, job_id)

        monkeypatch.setattr(task_mod, "_load_job", _flaky_load_job)

        job_id = _make_job(db_engine, "domestic")
        task_mod.sync_movie_master_task(job_id)  # must not raise

        job = _get_job(db_engine, job_id)
        assert job.status == "failed"
        assert job.error is not None


# ── International ────────────────────────────────────────────────────────────

class TestSyncMovieMasterIntlTask:
    def test_success_records_skipped_undefined_country(self, monkeypatch, db_engine):
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)
        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_intl_count", lambda: 7)
        monkeypatch.setattr(
            task_mod.prod_db, "fetch_fq_movie_master_intl_rows",
            lambda: iter([{"movie_id": 1, "movie_title": "A", "country": "France"}]),
        )
        monkeypatch.setattr(
            task_mod, "seed_intl_from_rows",
            lambda session, rows: {
                "inserted": 1, "updated": 0, "skipped": 0, "skipped_undefined_country": 3,
            },
        )
        fake_reindex = MagicMock()
        monkeypatch.setattr(
            "app.tasks.semantic_tasks.build_semantic_index_intl_task", MagicMock(delay=fake_reindex)
        )

        job_id = _make_job(db_engine, "international")
        task_mod.sync_movie_master_intl_task(job_id)

        job = _get_job(db_engine, job_id)
        assert job.status == "completed"
        assert job.total == 7
        assert job.inserted == 1
        assert job.skipped_undefined_country == 3
        fake_reindex.assert_called_once()

    def test_failure_sets_scrubbed_error(self, monkeypatch, db_engine):
        import app.tasks.prod_db_sync_task as task_mod

        monkeypatch.setattr("app.database.engine", db_engine, raising=False)
        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_intl_count", lambda: 5)

        def _fetch_rows():
            raise Exception("Access denied for user 'AIuser'@'10.0.0.5' (using password: YES)")
            yield  # pragma: no cover

        monkeypatch.setattr(task_mod.prod_db, "fetch_fq_movie_master_intl_rows", _fetch_rows)
        fake_reindex = MagicMock()
        monkeypatch.setattr(
            "app.tasks.semantic_tasks.build_semantic_index_intl_task", MagicMock(delay=fake_reindex)
        )

        job_id = _make_job(db_engine, "international")
        task_mod.sync_movie_master_intl_task(job_id)

        job = _get_job(db_engine, job_id)
        assert job.status == "failed"
        assert "AIuser" not in job.error
        fake_reindex.assert_not_called()

"""
API tests for the Movie Master production DB sync endpoints:

    POST /api/v1/movie-title-match/master/sync/{market}
    GET  /api/v1/movie-title-match/master/sync/{market}/{job_id}

sync_movie_master_task/sync_movie_master_intl_task are monkeypatched to
no-op stubs so these tests never touch Celery, Redis, or the production
MySQL DB — pure HTTP + DB behavior.

Uses an in-memory SQLite DB. Unlike test_batch_title_match_api.py (which
sets app.dependency_overrides[get_session] once at module-import time),
this file sets/restores the override inside an autouse fixture — pytest
collects (imports) every test module before running any test, so a
module-level assignment here would silently overwrite the override for
every other already-collected test file's tests too, purely based on
alphabetical/collection order. Scoping it to a fixture keeps this file's
DB isolated without that cross-file side effect.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import MovieMasterSyncJob

_sqlite_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SQLModel.metadata.create_all(_sqlite_engine)

client = TestClient(app, raise_server_exceptions=False)


def _override_get_session():
    with Session(_sqlite_engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _isolated_session_override():
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = _override_get_session
    try:
        yield
    finally:
        if previous is not None:
            app.dependency_overrides[get_session] = previous
        else:
            app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def _clean_table():
    with Session(_sqlite_engine) as session:
        for row in session.exec(select(MovieMasterSyncJob)).all():
            session.delete(row)
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _stub_sync_tasks(monkeypatch):
    """Never touch real Celery/Redis/prod-DB — sync_*.delay is a no-op."""
    domestic_calls = []
    intl_calls = []

    monkeypatch.setattr(
        "app.tasks.prod_db_sync_task.sync_movie_master_task.delay",
        lambda job_id: domestic_calls.append(job_id),
    )
    monkeypatch.setattr(
        "app.tasks.prod_db_sync_task.sync_movie_master_intl_task.delay",
        lambda job_id: intl_calls.append(job_id),
    )
    return {"domestic": domestic_calls, "international": intl_calls}


def _make_job(market: str, status: str = "queued", **overrides) -> str:
    with Session(_sqlite_engine) as session:
        job = MovieMasterSyncJob(market=market, status=status, **overrides)
        session.add(job)
        session.commit()
        return job.id


class TestPostSync:
    def test_creates_job_and_dispatches_domestic_task(self, _stub_sync_tasks):
        resp = client.post("/api/v1/movie-title-match/master/sync/domestic")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert _stub_sync_tasks["domestic"] == [body["job_id"]]
        assert _stub_sync_tasks["international"] == []

    def test_creates_job_and_dispatches_intl_task(self, _stub_sync_tasks):
        resp = client.post("/api/v1/movie-title-match/master/sync/international")
        assert resp.status_code == 200
        body = resp.json()
        assert _stub_sync_tasks["international"] == [body["job_id"]]
        assert _stub_sync_tasks["domestic"] == []

    def test_in_flight_guard_returns_existing_job_instead_of_starting_second(self, _stub_sync_tasks):
        first = client.post("/api/v1/movie-title-match/master/sync/domestic").json()

        second = client.post("/api/v1/movie-title-match/master/sync/domestic").json()

        assert second["job_id"] == first["job_id"]
        # Only the first POST actually dispatched a Celery task
        assert _stub_sync_tasks["domestic"] == [first["job_id"]]

    def test_in_flight_guard_does_not_cross_markets(self, _stub_sync_tasks):
        domestic_job = client.post("/api/v1/movie-title-match/master/sync/domestic").json()
        intl_job = client.post("/api/v1/movie-title-match/master/sync/international").json()

        assert domestic_job["job_id"] != intl_job["job_id"]
        assert _stub_sync_tasks["domestic"] == [domestic_job["job_id"]]
        assert _stub_sync_tasks["international"] == [intl_job["job_id"]]

    def test_completed_job_does_not_block_a_new_sync(self, _stub_sync_tasks):
        _make_job("domestic", status="completed")

        resp = client.post("/api/v1/movie-title-match/master/sync/domestic")

        assert resp.status_code == 200
        assert len(_stub_sync_tasks["domestic"]) == 1


class TestGetSync:
    def test_returns_current_job_state(self):
        job_id = _make_job(
            "domestic", status="processing", total=100, processed=40,
            inserted=30, updated=10, skipped=0,
        )

        resp = client.get(f"/api/v1/movie-title-match/master/sync/domestic/{job_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["market"] == "domestic"
        assert body["status"] == "processing"
        assert body["total"] == 100
        assert body["processed"] == 40
        assert body["progress"] == pytest.approx(0.4)
        assert body["inserted"] == 30
        assert body["updated"] == 10

    def test_progress_zero_when_total_zero(self):
        job_id = _make_job("domestic", status="queued", total=0, processed=0)

        resp = client.get(f"/api/v1/movie-title-match/master/sync/domestic/{job_id}")

        assert resp.json()["progress"] == 0

    def test_404_on_unknown_job_id(self):
        resp = client.get("/api/v1/movie-title-match/master/sync/domestic/does-not-exist")
        assert resp.status_code == 404

    def test_404_on_market_job_id_mismatch(self):
        job_id = _make_job("domestic")

        resp = client.get(f"/api/v1/movie-title-match/master/sync/international/{job_id}")

        assert resp.status_code == 404

    def test_includes_skipped_undefined_country_for_intl(self):
        job_id = _make_job(
            "international", status="completed", total=10, processed=10,
            inserted=5, updated=2, skipped=1, skipped_undefined_country=2,
        )

        resp = client.get(f"/api/v1/movie-title-match/master/sync/international/{job_id}")

        assert resp.json()["skipped_undefined_country"] == 2

    def test_error_field_surfaced_when_failed(self):
        job_id = _make_job(
            "domestic", status="failed",
            error="Production DB connection or upsert failed for job_id=abc — check server logs.",
        )

        resp = client.get(f"/api/v1/movie-title-match/master/sync/domestic/{job_id}")

        body = resp.json()
        assert body["status"] == "failed"
        assert "check server logs" in body["error"]

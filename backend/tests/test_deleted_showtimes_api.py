"""
API tests for the Deleted Showtimes Check endpoints:

    POST /api/v1/deleted-showtimes/batch
    POST /api/v1/deleted-showtimes/preflight
    GET  /api/v1/deleted-showtimes/batch/{job_id}
    GET  /api/v1/deleted-showtimes/batch/{job_id}/download
    GET  /api/v1/deleted-showtimes/batch/{job_id}/audit
    GET  /api/v1/deleted-showtimes/jobs

dispatch_job_task.delay is monkeypatched to a no-op stub so these tests never
touch Celery or Redis. storage (S3) is monkeypatched to an in-memory dict so
these tests never touch real S3. No SerpApi calls happen anywhere in this
file — pure HTTP + DB behavior, mirroring test_batch_title_match_api.py.

Unlike test_batch_title_match_api.py (which sets
app.dependency_overrides[get_session] once at module-import time), this file
sets/restores the override inside an autouse fixture — pytest collects
(imports) every test module before running any test, so a module-level
assignment here would silently overwrite the override for every other
already-collected test file's tests too, purely based on
alphabetical/collection order. Scoping it to a fixture (the same pattern
test_movie_master_sync_api.py uses) keeps this file's DB isolated without
that cross-file side effect. This router never reads app.database.engine
directly (unlike dispatch_job/finalize_job, which do and are tested
separately in test_deleted_showtime_task.py with an explicit monkeypatch), so
no engine swap is needed here at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select, create_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import get_session
from app.main import app
from app.models import DeletedShowtimeJob

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
def _clean_tables():
    with Session(_sqlite_engine) as session:
        for row in session.exec(select(DeletedShowtimeJob)).all():
            session.delete(row)
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _serpapi_key_configured(monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", "test-key-not-real")
    yield


@pytest.fixture(autouse=True)
def _stub_dispatch_job(monkeypatch):
    calls = []

    def _fake_delay(job_id):
        calls.append(job_id)

    monkeypatch.setattr("app.tasks.deleted_showtime_task.dispatch_job_task.delay", _fake_delay)
    return calls


@pytest.fixture(autouse=True)
def _stub_storage(monkeypatch):
    import app.deleted_showtimes.storage as storage

    store: dict[str, bytes] = {}

    monkeypatch.setattr(storage, "put_bytes", lambda key, data: store.__setitem__(key, data))
    monkeypatch.setattr(storage, "get_bytes", lambda key: store[key])
    monkeypatch.setattr(storage, "delete", lambda key: store.pop(key, None))
    monkeypatch.setattr(storage, "exists", lambda key: key in store)
    return store


def _csv_bytes(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _valid_csv() -> bytes:
    return _csv_bytes(
        "Theater Name,Title,Show date,Show time",
        "AMC Wayne 14,Dune,2026-08-06,7:00 PM",
        "AMC Wayne 14,Barbie,2026-08-06,8:00 PM",
    )


def _get_job(job_id: str) -> DeletedShowtimeJob:
    with Session(_sqlite_engine) as session:
        return session.get(DeletedShowtimeJob, job_id)


# ─────────────────────────────────────────────────────────────────────────────
# POST /batch
# ─────────────────────────────────────────────────────────────────────────────

def test_valid_csv_upload_returns_200_with_job_id():
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.csv", _valid_csv(), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body and body["job_id"]

    job = _get_job(body["job_id"])
    assert job is not None
    assert job.total == 2
    assert job.theater_verify == "warn"
    assert job.fallback == "auto"
    assert job.workers == 4


def test_upload_with_advanced_options_persists_them():
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.csv", _valid_csv(), "text/csv")},
        data={
            "title_missing_is_deleted": "true",
            "strict_screen_count": "true",
            "theater_verify": "strict",
            "fallback": "off",
            "workers": "8",
        },
    )
    assert resp.status_code == 200
    job = _get_job(resp.json()["job_id"])
    assert job.title_missing_is_deleted is True
    assert job.strict_screen_count is True
    assert job.theater_verify == "strict"
    assert job.fallback == "off"
    assert job.workers == 8


def test_upload_missing_required_column_returns_400():
    bad_csv = _csv_bytes("Theater Name,Title,Show date", "AMC Wayne 14,Dune,2026-08-06")
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.csv", bad_csv, "text/csv")},
    )
    assert resp.status_code == 400
    assert "Show time" in resp.json()["detail"]


def test_upload_with_existing_deleted_showtime_column_returns_400():
    """A previous output workbook re-uploaded must be rejected, not silently
    given a second DELETED_SHOWTIME column."""
    bad_csv = _csv_bytes(
        "Theater Name,Title,Show date,Show time,DELETED_SHOWTIME",
        "AMC Wayne 14,Dune,2026-08-06,7:00 PM,FALSE",
    )
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("results.csv", bad_csv, "text/csv")},
    )
    assert resp.status_code == 400
    assert "DELETED_SHOWTIME" in resp.json()["detail"]


def test_upload_bad_extension_returns_400():
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.txt", b"Theater Name,Title,Show date,Show time\n", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_without_serpapi_key_configured_returns_400(monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", "")
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.csv", _valid_csv(), "text/csv")},
    )
    assert resp.status_code == 400
    assert "SERPAPI_API_KEY" in resp.json()["detail"]


def test_upload_exceeding_row_cap_returns_400(monkeypatch):
    monkeypatch.setattr(settings, "DELETED_SHOWTIME_MAX_ROWS", 1)
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.csv", _valid_csv(), "text/csv")},
    )
    assert resp.status_code == 400
    assert "row limit" in resp.json()["detail"]


def test_upload_with_invalid_workers_returns_400():
    resp = client.post(
        "/api/v1/deleted-showtimes/batch",
        files={"file": ("showtimes.csv", _valid_csv(), "text/csv")},
        data={"workers": "99"},
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# POST /preflight
# ─────────────────────────────────────────────────────────────────────────────

def test_preflight_reports_row_count_without_creating_a_job():
    resp = client.post(
        "/api/v1/deleted-showtimes/preflight",
        files={"file": ("showtimes.csv", _valid_csv(), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 2
    with Session(_sqlite_engine) as session:
        assert session.exec(select(DeletedShowtimeJob)).all() == []


def test_preflight_flags_rows_already_started_in_et():
    past_csv = _csv_bytes(
        "Theater Name,Title,Show date,Show time",
        "AMC Wayne 14,Dune,2020-01-01,7:00 PM",
    )
    resp = client.post(
        "/api/v1/deleted-showtimes/preflight",
        files={"file": ("showtimes.csv", past_csv, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["rows_already_started"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# GET /batch/{job_id}
# ─────────────────────────────────────────────────────────────────────────────

def test_status_on_fresh_job_has_zero_progress_not_nan():
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(id="job-fresh", status="queued", total=0))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/batch/job-fresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed"] == 0
    assert body["progress"] == 0


def test_status_unknown_job_returns_404():
    resp = client.get("/api/v1/deleted-showtimes/batch/does-not-exist")
    assert resp.status_code == 404


def test_status_completed_job_exposes_output_and_audit_urls():
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(
            id="job-done", status="completed", total=1, processed=1,
            output_path="deleted-showtimes/outputs/job-done_output.xlsx",
            audit_output_path="deleted-showtimes/audit/job-done_audit.json",
        ))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/batch/job-done")
    body = resp.json()
    assert body["output_url"] == "/api/v1/deleted-showtimes/batch/job-done/download"
    assert body["audit_url"] == "/api/v1/deleted-showtimes/batch/job-done/audit"


# ─────────────────────────────────────────────────────────────────────────────
# GET /batch/{job_id}/download and /audit
# ─────────────────────────────────────────────────────────────────────────────

def test_download_after_completion_returns_200_xlsx(_stub_storage):
    _stub_storage["deleted-showtimes/outputs/job-dl.xlsx"] = b"fake xlsx bytes"
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(
            id="job-dl", status="completed", total=1,
            output_path="deleted-showtimes/outputs/job-dl.xlsx",
            ttl=datetime.utcnow() + timedelta(hours=1),
        ))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/batch/job-dl/download")
    assert resp.status_code == 200
    assert resp.content == b"fake xlsx bytes"


def test_download_expired_job_returns_410():
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(
            id="job-expired", status="completed", total=1,
            output_path="deleted-showtimes/outputs/job-expired.xlsx",
            ttl=datetime.utcnow() - timedelta(hours=1),
        ))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/batch/job-expired/download")
    assert resp.status_code == 410


def test_download_incomplete_job_returns_400():
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(id="job-running", status="processing", total=1))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/batch/job-running/download")
    assert resp.status_code == 400


def test_audit_download_after_completion_returns_200_json(_stub_storage):
    _stub_storage["deleted-showtimes/audit/job-audit.json"] = b'{"job_id": "job-audit"}'
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(
            id="job-audit", status="completed", total=1,
            audit_output_path="deleted-showtimes/audit/job-audit.json",
            ttl=datetime.utcnow() + timedelta(hours=1),
        ))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/batch/job-audit/audit")
    assert resp.status_code == 200
    assert resp.json() == {"job_id": "job-audit"}


# ─────────────────────────────────────────────────────────────────────────────
# GET /jobs
# ─────────────────────────────────────────────────────────────────────────────

def test_jobs_history_lists_most_recent_first():
    with Session(_sqlite_engine) as session:
        session.add(DeletedShowtimeJob(id="job-old", status="completed", total=1,
                                        created_at=datetime(2026, 1, 1)))
        session.add(DeletedShowtimeJob(id="job-new", status="completed", total=1,
                                        created_at=datetime(2026, 2, 1)))
        session.commit()

    resp = client.get("/api/v1/deleted-showtimes/jobs")
    assert resp.status_code == 200
    ids = [j["job_id"] for j in resp.json()["jobs"]]
    assert ids == ["job-new", "job-old"]

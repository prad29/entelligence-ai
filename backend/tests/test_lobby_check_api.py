"""Phase 5 API tests for POST /api/v1/lobby-check and its jobs/{id}[/results]
endpoints, via a standalone FastAPI app mounting just lobby_check.router
(see test_deleted_showtimes_api.py's docstring for why: main.py only
includes this router when LOBBY_CHECK_ENABLED is true at import time, which
is a process-wide, import-order-sensitive flag no test file should depend
on). lobby_check_dispatch_job_task.delay is monkeypatched to a no-op so
these tests never touch Celery/Redis/Bedrock.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, delete, select
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.dependencies.api_auth import hash_api_key
from app.models import ApiKey, LobbyCheckJob, LobbyCheckRow
from app.routers import lobby_check

_sqlite_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
SQLModel.metadata.create_all(_sqlite_engine)

app = FastAPI()
app.include_router(lobby_check.router)


def _override_get_session():
    with Session(_sqlite_engine) as session:
        yield session


app.dependency_overrides[get_session] = _override_get_session

client = TestClient(app, raise_server_exceptions=False)

RAW_KEY = "lobby-test-key"
HEADERS = {"x-api-key": RAW_KEY}
VALID_URL = "https://mm-intelligence.s3.amazonaws.com/lobby/1787248984204.jpg"


@pytest.fixture(autouse=True)
def _seed_key_and_stub_celery(monkeypatch):
    monkeypatch.setattr(
        "app.tasks.lobby_check_task.lobby_check_dispatch_job_task.delay",
        lambda job_id: None,
    )
    with Session(_sqlite_engine) as s:
        s.add(ApiKey(
            id="test-key-1", key_hash=hash_api_key(RAW_KEY), key_prefix=RAW_KEY[:8],
            active=True, max_rows_per_batch=3, max_concurrent_jobs=5, requests_per_minute=1000,
        ))
        s.commit()
    yield
    _clear_tables()


def _clear_tables():
    with Session(_sqlite_engine) as s:
        s.exec(delete(LobbyCheckRow))
        s.exec(delete(LobbyCheckJob))
        s.exec(delete(ApiKey))
        s.commit()


# --- POST /api/v1/lobby-check ------------------------------------------------

def test_submit_returns_202_and_persists_pending_rows():
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [{"photo_id": 1, "image_url": VALID_URL}]},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["rows_total"] == 1

    with Session(_sqlite_engine) as s:
        rows = s.exec(
            select(LobbyCheckRow).where(LobbyCheckRow.job_id == body["job_id"])
        ).all()
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].image_url == VALID_URL


def test_submit_missing_api_key_returns_401():
    resp = client.post(
        "/api/v1/lobby-check",
        json={"images": [{"photo_id": 1, "image_url": VALID_URL}]},
    )
    assert resp.status_code == 401


def test_submit_duplicate_photo_id_returns_422():
    dup = 1
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [
            {"photo_id": dup, "image_url": VALID_URL},
            {"photo_id": dup, "image_url": VALID_URL},
        ]},
    )
    assert resp.status_code == 422


def test_submit_malformed_url_returns_422():
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [{"photo_id": 1, "image_url": "not-a-url"}]},
    )
    assert resp.status_code == 422


def test_submit_disallowed_host_returns_422():
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [{"photo_id": 1, "image_url": "https://evil.example.com/x.jpg"}]},
    )
    assert resp.status_code == 422


def test_submit_over_batch_cap_returns_422():
    # ApiKey.max_rows_per_batch=3 in the fixture above
    images = [{"photo_id": i, "image_url": VALID_URL} for i in range(4)]
    resp = client.post("/api/v1/lobby-check", headers=HEADERS, json={"images": images})
    assert resp.status_code == 422
    assert "exceeding this key's limit of 3" in resp.text


def test_submit_empty_images_returns_422():
    resp = client.post("/api/v1/lobby-check", headers=HEADERS, json={"images": []})
    assert resp.status_code == 422


# --- GET /jobs/{job_id} and /jobs/{job_id}/results ---------------------------

def _submit_one() -> str:
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [{"photo_id": 1, "image_url": VALID_URL}]},
    )
    return resp.json()["job_id"]


def test_get_job_status():
    job_id = _submit_one()
    resp = client.get(f"/api/v1/lobby-check/jobs/{job_id}", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["rows_total"] == 1
    assert body["results_url"] == f"/api/v1/lobby-check/jobs/{job_id}/results"


def test_get_job_status_unknown_job_returns_404():
    resp = client.get("/api/v1/lobby-check/jobs/does-not-exist", headers=HEADERS)
    assert resp.status_code == 404


def test_get_job_status_wrong_owner_returns_404():
    job_id = _submit_one()
    with Session(_sqlite_engine) as s:
        s.add(ApiKey(
            id="other-key", key_hash=hash_api_key("other-raw"), key_prefix="other-ra",
            active=True, requests_per_minute=1000,
        ))
        s.commit()
    resp = client.get(f"/api/v1/lobby-check/jobs/{job_id}", headers={"x-api-key": "other-raw"})
    assert resp.status_code == 404


def test_get_job_results_includes_pending_rows_and_no_diagnostics():
    job_id = _submit_one()
    resp = client.get(f"/api/v1/lobby-check/jobs/{job_id}/results", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    result = body["results"][0]
    assert result["status"] == "pending"
    # never exposed via the API -- dashboard-only (design doc §6.6)
    for forbidden in ("cost_usd", "input_tokens", "output_tokens", "latency_ms", "model_id", "framing", "parse_retries"):
        assert forbidden not in result


def test_get_job_results_single_page_when_under_page_size():
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [{"photo_id": i, "image_url": VALID_URL} for i in range(3)]},
    )
    job_id = resp.json()["job_id"]

    # RESULTS_PAGE_SIZE is 100, so all 3 rows fit on one page.
    page1 = client.get(f"/api/v1/lobby-check/jobs/{job_id}/results", headers=HEADERS)
    body = page1.json()
    assert len(body["results"]) == 3
    assert body["next_cursor"] is None
    assert body["has_more"] is False


def test_get_job_results_cursor_excludes_already_seen_rows():
    resp = client.post(
        "/api/v1/lobby-check",
        headers=HEADERS,
        json={"images": [{"photo_id": i, "image_url": VALID_URL} for i in range(3)]},
    )
    job_id = resp.json()["job_id"]

    all_rows = client.get(f"/api/v1/lobby-check/jobs/{job_id}/results", headers=HEADERS).json()["results"]
    first_photo_id = all_rows[0]["photo_id"]
    with Session(_sqlite_engine) as s:
        first_id = s.exec(
            select(LobbyCheckRow.id).where(LobbyCheckRow.photo_id == first_photo_id)
        ).one()
    cursor = base64.urlsafe_b64encode(str(first_id).encode()).decode()

    resp = client.get(f"/api/v1/lobby-check/jobs/{job_id}/results?cursor={cursor}", headers=HEADERS)
    remaining_ids = {r["photo_id"] for r in resp.json()["results"]}
    assert first_photo_id not in remaining_ids
    assert len(remaining_ids) == 2


def test_get_job_results_invalid_cursor_returns_400():
    job_id = _submit_one()
    resp = client.get(
        f"/api/v1/lobby-check/jobs/{job_id}/results?cursor=not-valid-base64!!!",
        headers=HEADERS,
    )
    assert resp.status_code == 400


def test_get_job_results_status_filter():
    job_id = _submit_one()
    resp = client.get(
        f"/api/v1/lobby-check/jobs/{job_id}/results?status=completed", headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["results"] == []

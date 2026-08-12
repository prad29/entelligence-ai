"""
API tests for the International Amenity Detection endpoints:

    POST /api/v1/intl-detect/single
    POST /api/v1/intl-detect/batch
    GET  /api/v1/intl-jobs/{job_id}
    GET  /api/v1/intl-jobs/{job_id}/download
    GET  /api/v1/intl-amenities
    POST /api/v1/intl-amenities
    PATCH /api/v1/intl-amenities/{id}
    DELETE /api/v1/intl-amenities/{id}
    POST /api/v1/intl-amenities/{id}/approve
    POST /api/v1/intl-amenities/{id}/reject
    POST /api/v1/intl-amenities/import
    GET  /api/v1/intl-amenities/export

Follows the pattern documented in test_deleted_showtimes_api.py's header, NOT
test_api_integration.py's: pytest collects (imports) every test module before
running any test, so a module-level `app.dependency_overrides[get_session] =
...` assignment would silently clobber the override for every other
already-collected test file. This file scopes the override to an autouse
fixture with teardown instead.

Unlike test_deleted_showtimes_api.py's router (which never touches
app.database.engine directly), the intl batch worker DOES open its own
`Session(db_engine)` from a background thread (threading.Thread, not
Celery — see docs/international-amenity-screen-format-plan.md Phase 3
architecture note), so DATABASE_URL is patched to sqlite:///:memory: before
any app module import, and app.database.engine is monkeypatched to a shared
StaticPool sqlite engine, mirroring test_api_integration.py's approach for
that reason alone (not for the dependency_overrides pattern, which stays
autouse-fixture-scoped as directed above).

app.state.intl_engine is injected explicitly per test — startup() does not
run under this TestClient construction pattern.
"""

from __future__ import annotations

import io
import os
import time
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

import app.database as _db_module  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import IntlAmenityMapping, IntlDetectionJob  # noqa: E402
from app.intl_detection.loader import build_intl_engine_from_db  # noqa: E402
from app.detection.engine import MappingIndex, ScreenFormatEngine  # noqa: E402

_sqlite_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
SQLModel.metadata.create_all(_sqlite_engine)

# The intl batch worker thread opens Session(db_engine) directly (not via the
# get_session dependency), so app.database.engine itself must point at our
# shared in-memory sqlite engine.
_db_module.engine = _sqlite_engine

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
        for row in session.exec(select(IntlAmenityMapping)).all():
            session.delete(row)
        for row in session.exec(select(IntlDetectionJob)).all():
            session.delete(row)
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _no_ai_trigger(monkeypatch):
    # Neither the intl engine nor the intl batch worker call Bedrock (there
    # is no AI fallback in this build), but the domestic /detect/single
    # endpoint used in test_domestic_endpoints_are_unaffected does, unless
    # AI_TRIGGER_MODE is off. Force it off so no test ever attempts a real
    # network call.
    monkeypatch.setattr(settings, "AI_TRIGGER_MODE", "off")
    yield


@pytest.fixture(autouse=True)
def _inject_engines():
    # Fresh, empty engines by default — individual tests seed rows and
    # rebuild as needed through the real endpoints (the thing under test).
    with Session(_sqlite_engine) as session:
        app.state.intl_engine = build_intl_engine_from_db(session)
    app.state.engine = ScreenFormatEngine(MappingIndex(mappings=[], aliases={}))
    yield


def _seed_approved_mapping(keyword="4DX", fmt="4DX", tier=1) -> int:
    with Session(_sqlite_engine) as session:
        m = IntlAmenityMapping(
            amenity_keyword=keyword,
            screen_format=fmt,
            priority_tier=tier,
            status="approved",
        )
        session.add(m)
        session.commit()
        session.refresh(m)
        mapping_id = m.id
        app.state.intl_engine = build_intl_engine_from_db(session)
    return mapping_id


def _xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _poll_job_completed(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/intl-jobs/{job_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    return body


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/intl-detect/single
# ─────────────────────────────────────────────────────────────────────────────

def test_single_detect_matched():
    _seed_approved_mapping("4DX", "4DX", 1)
    r = client.post("/api/v1/intl-detect/single", json={"amenity": "4DX"})
    assert r.status_code == 200
    body = r.json()
    assert body["screen_format"] == "4DX"
    assert body["match_source"] == "Keyword Match"
    assert body["fired_ai"] is False


def test_single_detect_unmatched():
    r = client.post("/api/v1/intl-detect/single", json={"amenity": "Comfy Recliners"})
    assert r.status_code == 200
    body = r.json()
    assert body["screen_format"] == "Standard"
    assert body["match_source"] == "No Match"
    assert body["fired_ai"] is False


def test_single_detect_empty_amenity_returns_no_match():
    r = client.post("/api/v1/intl-detect/single", json={"amenity": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["screen_format"] == "Standard"
    assert body["confidence"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CRUD /api/v1/intl-amenities
# ─────────────────────────────────────────────────────────────────────────────

def test_list_amenities_empty_table():
    r = client.get("/api/v1/intl-amenities")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_create_amenity_defaults_to_pending():
    r = client.post(
        "/api/v1/intl-amenities",
        json={"amenity_keyword": "Xplus", "screen_format": "Xplus", "priority_tier": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["amenity_keyword"] == "Xplus"


def test_pending_mapping_is_not_detectable_until_approved():
    create_resp = client.post(
        "/api/v1/intl-amenities",
        json={"amenity_keyword": "Xplus", "screen_format": "Xplus", "priority_tier": 3},
    )
    mapping_id = create_resp.json()["id"]

    # Not yet approved — engine has not been rebuilt with this row.
    detect_before = client.post("/api/v1/intl-detect/single", json={"amenity": "Xplus"})
    assert detect_before.json()["match_source"] == "No Match"

    approve_resp = client.post(f"/api/v1/intl-amenities/{mapping_id}/approve")
    assert approve_resp.status_code == 200

    # This is the test that pins the app.state.intl_engine rebuild contract.
    detect_after = client.post("/api/v1/intl-detect/single", json={"amenity": "Xplus"})
    assert detect_after.json()["screen_format"] == "Xplus"
    assert detect_after.json()["match_source"] == "Keyword Match"


def test_patch_updates_a_field():
    mapping_id = _seed_approved_mapping("Onyx", "Onyx", 3)
    r = client.patch(f"/api/v1/intl-amenities/{mapping_id}", json={"screen_format": "Onyx Renamed"})
    assert r.status_code == 200
    assert r.json()["screen_format"] == "Onyx Renamed"


def test_delete_removes_and_stops_matching():
    mapping_id = _seed_approved_mapping("Onyx", "Onyx", 3)
    detect_before = client.post("/api/v1/intl-detect/single", json={"amenity": "Onyx"})
    assert detect_before.json()["screen_format"] == "Onyx"

    r = client.delete(f"/api/v1/intl-amenities/{mapping_id}")
    assert r.status_code == 200

    detect_after = client.post("/api/v1/intl-detect/single", json={"amenity": "Onyx"})
    assert detect_after.json()["match_source"] == "No Match"


def test_list_filters_search_status_tier_and_total_pages():
    with Session(_sqlite_engine) as session:
        session.add(IntlAmenityMapping(amenity_keyword="4DX", screen_format="4DX", priority_tier=1, status="approved"))
        session.add(IntlAmenityMapping(amenity_keyword="ScreenX", screen_format="ScreenX", priority_tier=3, status="pending"))
        session.add(IntlAmenityMapping(amenity_keyword="4DX 3D", screen_format="4DX", priority_tier=1, status="approved"))
        session.commit()

    r = client.get("/api/v1/intl-amenities", params={"search": "4DX", "status": "approved", "tier": "P1"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(item["amenity_keyword"].startswith("4DX") for item in body["items"])
    assert all(item["status"] == "approved" for item in body["items"])
    assert all(item["priority_tier"] == 1 for item in body["items"])

    r2 = client.get("/api/v1/intl-amenities", params={"page": 1, "page_size": 1})
    assert r2.json()["total_pages"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/intl-detect/batch
# ─────────────────────────────────────────────────────────────────────────────

def test_batch_rejects_bad_extension():
    r = client.post(
        "/api/v1/intl-detect/batch",
        files={"file": ("amenities.txt", b"amenities\n4DX\n", "text/plain")},
    )
    assert r.status_code == 400


def test_batch_rejects_missing_amenity_column():
    content = _xlsx_bytes(["not_amenities"], [["4DX"]])
    r = client.post(
        "/api/v1/intl-detect/batch",
        files={"file": ("bad.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 400
    assert "amenities" in r.json()["detail"]


def test_batch_rejects_audit_mode_without_screen_format_column():
    content = _xlsx_bytes(["amenities"], [["4DX"]])
    r = client.post(
        "/api/v1/intl-detect/batch",
        params={"audit_mode": "true"},
        files={"file": ("audit.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 400
    assert "screen_format" in r.json()["detail"]


def test_valid_batch_upload_returns_job_id_and_is_pollable():
    _seed_approved_mapping("4DX", "4DX", 1)
    content = _xlsx_bytes(["amenities"], [["4DX"], ["Comfy Recliners"], ["ScreenX"]])
    r = client.post(
        "/api/v1/intl-detect/batch",
        files={"file": ("batch.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id

    body = _poll_job_completed(job_id)
    assert body["status"] == "completed"
    assert body["total"] == 3
    assert body["output_url"] == f"/api/v1/intl-jobs/{job_id}/download"


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/intl-jobs/{job_id}[/download]
# ─────────────────────────────────────────────────────────────────────────────

def test_job_not_found_returns_404():
    r = client.get("/api/v1/intl-jobs/does-not-exist")
    assert r.status_code == 404


def test_download_not_completed_returns_400():
    with Session(_sqlite_engine) as session:
        job = IntlDetectionJob(id="queued-job", status="queued", total=1)
        session.add(job)
        session.commit()

    r = client.get("/api/v1/intl-jobs/queued-job/download")
    assert r.status_code == 400


def test_download_expired_ttl_returns_410():
    with Session(_sqlite_engine) as session:
        job = IntlDetectionJob(
            id="expired-job",
            status="completed",
            total=1,
            output_path="/tmp/does-not-matter.xlsx",
            ttl=datetime.utcnow() - timedelta(hours=1),
        )
        session.add(job)
        session.commit()

    r = client.get("/api/v1/intl-jobs/expired-job/download")
    assert r.status_code == 410


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/intl-amenities/import, GET .../export
# ─────────────────────────────────────────────────────────────────────────────

def test_import_valid_file():
    content = _xlsx_bytes(
        ["amenity_keyword", "screen_format", "priority_tier"],
        [["4DX", "4DX", 1], ["ScreenX", "ScreenX", 3]],
    )
    r = client.post(
        "/api/v1/intl-amenities/import",
        files={"file": ("import.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 2


def test_import_missing_required_header_returns_400():
    content = _xlsx_bytes(["amenity_keyword", "screen_format"], [["4DX", "4DX"]])
    r = client.post(
        "/api/v1/intl-amenities/import",
        files={"file": ("bad_import.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 400


def test_export_returns_xlsx_content_type():
    _seed_approved_mapping("4DX", "4DX", 1)
    r = client.get("/api/v1/intl-amenities/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ─────────────────────────────────────────────────────────────────────────────
# Domestic-routers-unaffected guard
# ─────────────────────────────────────────────────────────────────────────────

def test_domestic_endpoints_are_unaffected():
    r = client.get("/api/v1/amenities")
    assert r.status_code == 200

    r2 = client.post(
        "/api/v1/detect/single",
        json={"amenity": "IMAX", "circuit_name": "AMC Entertainment Inc"},
    )
    assert r2.status_code == 200
    assert "screen_format" in r2.json()

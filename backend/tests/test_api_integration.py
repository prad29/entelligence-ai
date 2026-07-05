"""
API integration tests using FastAPI TestClient.

These tests hit the live app routes and exercise the full request/response cycle.
A SQLite in-memory database is used for isolation (no PostgreSQL required).
The app's startup event is bypassed: we inject the detection engine and DB session
directly so tests run without Docker or any external services.
"""

import io
import os

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool


# ── Patch DATABASE_URL before any app module imports it ──────────────────────
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

# Now safe to import app modules
import app.database as _db_module  # noqa: E402
from app.main import app  # noqa: E402
from app.database import get_session  # noqa: E402


# ── SQLite in-memory engine shared across the module ─────────────────────────
_SQLITE_URL = "sqlite:///:memory:"
_sqlite_engine = create_engine(
    _SQLITE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Monkey-patch the module-level engine used by database.py
_db_module.engine = _sqlite_engine


def _override_get_session():
    with Session(_sqlite_engine) as session:
        yield session


app.dependency_overrides[get_session] = _override_get_session

# Create tables once
SQLModel.metadata.create_all(_sqlite_engine)

# ── Build and inject a seeded detection engine ────────────────────────────────
from tests.conftest import ALL_MAPPINGS, CIRCUIT_OVERRIDES, CIRCUIT_ALIASES  # noqa: E402
from app.detection.engine import MappingIndex, ScreenFormatEngine  # noqa: E402

_idx = MappingIndex(
    mappings=ALL_MAPPINGS,
    overrides=CIRCUIT_OVERRIDES,
    aliases=CIRCUIT_ALIASES,
)
_seeded_engine = ScreenFormatEngine(_idx)
app.state.engine = _seeded_engine

# ── TestClient (no lifespan — engine already injected) ───────────────────────
client = TestClient(app, raise_server_exceptions=False)


# ── Integration tests ─────────────────────────────────────────────────────────

def test_detect_single_vip():
    r = client.post(
        "/api/v1/detect/single",
        json={"amenity": "IMAX | VIP 19+", "circuit_name": "Cineplex Entertainment"},
    )
    assert r.status_code == 200
    assert r.json()["screen_format"] == "VIP Cineplex"


def test_detect_single_4dx():
    r = client.post(
        "/api/v1/detect/single",
        json={"amenity": "4DX | IMAX | BTX"},
    )
    assert r.status_code == 200
    assert r.json()["screen_format"] == "4DX"


def test_detect_single_empty():
    r = client.post("/api/v1/detect/single", json={"amenity": ""})
    assert r.status_code == 200
    assert r.json()["match_source"] == "Empty Input"


def test_batch_missing_column():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["wrong_col", "another_col"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    r = client.post(
        "/api/v1/detect/batch",
        files={
            "file": (
                "test.xlsx",
                buf,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert r.status_code == 400
    assert "amenities" in r.json()["detail"]


def test_amenities_list():
    r = client.get("/api/v1/amenities")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "items" in body
    assert isinstance(body["items"], list)


def test_settings_bedrock_status():
    r = client.get("/api/v1/settings/bedrock/status")
    assert r.status_code == 200
    assert "connected" in r.json()

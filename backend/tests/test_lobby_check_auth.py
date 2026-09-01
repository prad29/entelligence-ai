"""Phase 5 tests for the lobby-check auth dependency
(app.dependencies.api_auth.require_api_key_lobby_check), exercised as plain
function calls (no HTTP layer) against an in-memory SQLite session —
mirrors the concurrent-jobs regression the design doc calls out: an
in-flight ApiTitleMatchJob must not consume a lobby-check slot, and vice
versa.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool
from starlette.requests import Request

from app.dependencies.api_auth import (
    hash_api_key,
    require_api_key,
    require_api_key_lobby_check,
)
from app.models import ApiKey, ApiTitleMatchJob, LobbyCheckJob


@pytest.fixture
def db_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _make_request(raw_key: str | None) -> Request:
    headers = [(b"x-api-key", raw_key.encode())] if raw_key else []
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/"}
    return Request(scope)


def _make_key(engine, raw_key="secret123", **overrides) -> ApiKey:
    defaults = dict(
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:8],
        active=True,
        max_concurrent_jobs=2,
        requests_per_minute=60,
    )
    defaults.update(overrides)
    with Session(engine) as s:
        key = ApiKey(**defaults)
        s.add(key)
        s.commit()
        s.refresh(key)
        return key


def test_missing_header_raises_401(db_engine):
    with Session(db_engine) as session:
        with pytest.raises(HTTPException) as exc:
            require_api_key_lobby_check(_make_request(None), session)
    assert exc.value.status_code == 401


def test_unknown_key_raises_401(db_engine):
    with Session(db_engine) as session:
        with pytest.raises(HTTPException) as exc:
            require_api_key_lobby_check(_make_request("nope"), session)
    assert exc.value.status_code == 401


def test_inactive_key_raises_401(db_engine):
    _make_key(db_engine, raw_key="deadkey", active=False)
    with Session(db_engine) as session:
        with pytest.raises(HTTPException) as exc:
            require_api_key_lobby_check(_make_request("deadkey"), session)
    assert exc.value.status_code == 401


def test_valid_key_returns_api_key(db_engine):
    _make_key(db_engine, raw_key="goodkey")
    with Session(db_engine) as session:
        api_key = require_api_key_lobby_check(_make_request("goodkey"), session)
    assert api_key.key_prefix == "goodkey"[:8]


def test_lobby_check_cap_ignores_in_flight_external_job(db_engine):
    """An ApiTitleMatchJob in-flight for this key must NOT consume its
    lobby-check concurrency slot."""
    key = _make_key(db_engine, raw_key="k1", max_concurrent_jobs=1)
    with Session(db_engine) as s:
        s.add(ApiTitleMatchJob(id="ext-1", api_key_id=key.id, market="domestic", phase="processing"))
        s.commit()

    with Session(db_engine) as session:
        # must NOT raise -- zero LobbyCheckJob rows are in-flight for this key
        require_api_key_lobby_check(_make_request("k1"), session)


def test_external_cap_ignores_in_flight_lobby_check_job(db_engine):
    """Symmetric regression: a LobbyCheckJob in-flight must NOT consume the
    external title-match surface's concurrency slot."""
    key = _make_key(db_engine, raw_key="k2", max_concurrent_jobs=1)
    with Session(db_engine) as s:
        s.add(LobbyCheckJob(id="lc-1", api_key_id=key.id, phase="processing"))
        s.commit()

    with Session(db_engine) as session:
        require_api_key(_make_request("k2"), session)  # must NOT raise


def test_lobby_check_cap_enforced_against_lobby_check_jobs(db_engine):
    key = _make_key(db_engine, raw_key="k3", max_concurrent_jobs=1)
    with Session(db_engine) as s:
        s.add(LobbyCheckJob(id="lc-2", api_key_id=key.id, phase="processing"))
        s.commit()

    with Session(db_engine) as session:
        with pytest.raises(HTTPException) as exc:
            require_api_key_lobby_check(_make_request("k3"), session)
    assert exc.value.status_code == 429


def test_lobby_check_cap_allows_when_under_limit(db_engine):
    key = _make_key(db_engine, raw_key="k4", max_concurrent_jobs=2)
    with Session(db_engine) as s:
        s.add(LobbyCheckJob(id="lc-3", api_key_id=key.id, phase="processing"))
        s.commit()

    with Session(db_engine) as session:
        require_api_key_lobby_check(_make_request("k4"), session)  # 1 < 2, must NOT raise


def test_lobby_check_cap_ignores_terminal_jobs(db_engine):
    key = _make_key(db_engine, raw_key="k5", max_concurrent_jobs=1)
    with Session(db_engine) as s:
        s.add(LobbyCheckJob(id="lc-4", api_key_id=key.id, phase="completed"))
        s.commit()

    with Session(db_engine) as session:
        require_api_key_lobby_check(_make_request("k5"), session)  # completed doesn't count

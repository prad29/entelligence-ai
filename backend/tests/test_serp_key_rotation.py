"""
Tests for app.deleted_showtimes.serp_key_rotation (RotatingSerpClient,
mark_exhausted, AllKeysExhaustedError).

Coverage:
  (a) slot 1 exhausted (SerpQuotaError) -> rotates to slot 2 -> succeeds.
  (b) every configured key exhausted -> AllKeysExhaustedError, exactly one
      attempt per configured key (no infinite loop).
  (c) a key marked exhausted more than SERPAPI_KEY_COOLDOWN_HOURS ago is
      eligible again.
  (d) repeat-hit: mark_exhausted() called twice while the first exhaustion is
      still within its cooldown window must NOT push exhausted_at forward —
      only last_error/failure_count change. This is the critical fix the
      design review called out (naive "always overwrite" would keep the key
      in cooldown forever).
  (e) a fingerprint mismatch (slot's configured key changed) makes that slot
      available again, ignoring the stale exhaustion row.
  (f) a non-quota/non-auth SerpError propagates unchanged, with no rotation
      and no DB write.
  (g) fail-open: if the SerpApiKeySlot table isn't queryable (e.g.
      un-migrated DB), RotatingSerpClient still works using slot 1 only.

DB: in-memory sqlite via SQLModel metadata (mirrors test_deleted_showtime_task.py).
SerpClient itself is replaced by a fake so no network calls are made.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.config import settings
from app.deleted_showtimes.serp_client import SerpAuthError, SerpError, SerpQuotaError
from app.models import SerpApiKeySlot

import app.deleted_showtimes.serp_key_rotation as rotation_mod
from app.deleted_showtimes.serp_key_rotation import (
    AllKeysExhaustedError,
    RotatingSerpClient,
    _fingerprint,
    mark_exhausted,
)


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


@pytest.fixture(autouse=True)
def _patch_engine_and_keys(monkeypatch, db_engine):
    """Point the module at the in-memory DB and give every test a clean,
    known key configuration (slots 1-2 set, 3-11 blank)."""
    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", "key-1")
    monkeypatch.setattr(settings, "SERPAPI_API_KEY_2", "key-2")
    for i in range(3, 12):
        monkeypatch.setattr(settings, f"SERPAPI_API_KEY_{i}", "")
    monkeypatch.setattr(settings, "SERPAPI_KEY_COOLDOWN_HOURS", 24)
    # Reset the module's one-shot fail-open warning flag between tests.
    monkeypatch.setattr(rotation_mod, "_fail_open_warned", False)
    return db_engine


class FakeSerpClient:
    """Stand-in for SerpClient: `behaviors[key]` is either a dict (returned
    as-is) or an Exception instance/class (raised)."""

    behaviors: dict = {}
    calls: list = []

    def __init__(self, key, retries=3, timeout=45):
        self.key = key

    def search(self, params):
        FakeSerpClient.calls.append(self.key)
        behavior = FakeSerpClient.behaviors[self.key]
        if isinstance(behavior, Exception):
            raise behavior
        if isinstance(behavior, type) and issubclass(behavior, Exception):
            raise behavior("fake failure")
        return behavior


@pytest.fixture(autouse=True)
def _patch_serp_client(monkeypatch):
    FakeSerpClient.behaviors = {}
    FakeSerpClient.calls = []
    monkeypatch.setattr(rotation_mod, "SerpClient", FakeSerpClient)
    return FakeSerpClient


def _row(engine, slot):
    with Session(engine) as s:
        return s.get(SerpApiKeySlot, slot)


# ---------------------------------------------------------------------------
# (a) slot 1 exhausted -> rotate to slot 2 -> success
# ---------------------------------------------------------------------------
def test_rotates_to_next_slot_on_quota_error(db_engine):
    FakeSerpClient.behaviors = {
        "key-1": SerpQuotaError("account has run out of searches"),
        "key-2": {"showtimes": []},
    }

    result = RotatingSerpClient().search({"q": "AMC Wayne 14"})

    assert result == {"showtimes": []}
    assert FakeSerpClient.calls == ["key-1", "key-2"]

    row1 = _row(db_engine, 1)
    assert row1 is not None
    assert row1.exhausted_at is not None
    assert row1.key_fingerprint == _fingerprint("key-1")
    row2 = _row(db_engine, 2)
    assert row2 is None  # never failed, no row written


# ---------------------------------------------------------------------------
# (b) every configured key exhausted -> AllKeysExhaustedError, no infinite loop
# ---------------------------------------------------------------------------
def test_all_keys_exhausted_raises_with_no_infinite_loop():
    FakeSerpClient.behaviors = {
        "key-1": SerpQuotaError("no searches left"),
        "key-2": SerpAuthError("account has run out of credits"),
    }

    with pytest.raises(AllKeysExhaustedError) as exc_info:
        RotatingSerpClient().search({"q": "x"})

    assert FakeSerpClient.calls == ["key-1", "key-2"]  # each key tried exactly once
    msg = str(exc_info.value)
    assert "tried 2 of 2" in msg
    assert "credits" in msg  # most recent failure reason surfaces


# ---------------------------------------------------------------------------
# (c) a key past cooldown is eligible again
# ---------------------------------------------------------------------------
def test_key_past_cooldown_is_eligible_again(db_engine, monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_KEY_COOLDOWN_HOURS", 24)
    with Session(db_engine) as s:
        s.add(SerpApiKeySlot(
            slot=1,
            key_fingerprint=_fingerprint("key-1"),
            exhausted_at=datetime.utcnow() - timedelta(hours=25),
            last_error="ran out of searches",
            failure_count=3,
        ))
        s.commit()

    FakeSerpClient.behaviors = {"key-1": {"ok": True}}

    result = RotatingSerpClient().search({"q": "x"})

    assert result == {"ok": True}
    assert FakeSerpClient.calls == ["key-1"]


def test_key_still_within_cooldown_is_skipped(db_engine, monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_KEY_COOLDOWN_HOURS", 24)
    with Session(db_engine) as s:
        s.add(SerpApiKeySlot(
            slot=1,
            key_fingerprint=_fingerprint("key-1"),
            exhausted_at=datetime.utcnow() - timedelta(hours=1),
            last_error="ran out of searches",
            failure_count=3,
        ))
        s.commit()

    FakeSerpClient.behaviors = {"key-1": {"unused": True}, "key-2": {"ok": True}}

    result = RotatingSerpClient().search({"q": "x"})

    assert result == {"ok": True}
    assert FakeSerpClient.calls == ["key-2"]  # slot 1 never even attempted


# ---------------------------------------------------------------------------
# (d) repeat-hit within an active cooldown must not push exhausted_at forward
# ---------------------------------------------------------------------------
def test_repeat_hit_within_cooldown_preserves_original_exhausted_at(db_engine, monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_KEY_COOLDOWN_HOURS", 24)

    mark_exhausted(1, "key-1", "no searches left")
    row_after_first = _row(db_engine, 1)
    original_exhausted_at = row_after_first.exhausted_at
    assert row_after_first.failure_count == 1

    # Second probe hits the same dead key well within the cooldown window —
    # the naive "always overwrite exhausted_at = now()" implementation would
    # move the timestamp forward here and this assertion would fail.
    mark_exhausted(1, "key-1", "no searches left (again)")
    row_after_second = _row(db_engine, 1)

    assert row_after_second.exhausted_at == original_exhausted_at
    assert row_after_second.failure_count == 2
    assert row_after_second.last_error == "no searches left (again)"


def test_fresh_exhaustion_after_cooldown_resets_timestamp_and_count(db_engine, monkeypatch):
    monkeypatch.setattr(settings, "SERPAPI_KEY_COOLDOWN_HOURS", 24)
    with Session(db_engine) as s:
        s.add(SerpApiKeySlot(
            slot=1,
            key_fingerprint=_fingerprint("key-1"),
            exhausted_at=datetime.utcnow() - timedelta(hours=25),
            last_error="stale failure",
            failure_count=7,
        ))
        s.commit()

    before = datetime.utcnow()
    mark_exhausted(1, "key-1", "fresh failure")
    row = _row(db_engine, 1)

    assert row.exhausted_at >= before
    assert row.failure_count == 1
    assert row.last_error == "fresh failure"


# ---------------------------------------------------------------------------
# (e) fingerprint mismatch -> treated as available, ignoring stale exhaustion
# ---------------------------------------------------------------------------
def test_fingerprint_mismatch_treated_as_available(db_engine):
    with Session(db_engine) as s:
        s.add(SerpApiKeySlot(
            slot=1,
            key_fingerprint=_fingerprint("old-rotated-out-key"),
            exhausted_at=datetime.utcnow(),  # "just" exhausted, well within cooldown
            last_error="no searches left",
            failure_count=5,
        ))
        s.commit()

    # settings.SERPAPI_API_KEY (from the autouse fixture) is "key-1", which
    # doesn't match the fingerprint stored above -> slot 1 must be usable.
    FakeSerpClient.behaviors = {"key-1": {"ok": True}}

    result = RotatingSerpClient().search({"q": "x"})

    assert result == {"ok": True}
    assert FakeSerpClient.calls == ["key-1"]


# ---------------------------------------------------------------------------
# (f) non-quota/non-auth SerpError propagates unchanged, no rotation, no DB write
# ---------------------------------------------------------------------------
def test_non_quota_serp_error_propagates_without_rotation_or_db_write(db_engine):
    FakeSerpClient.behaviors = {
        "key-1": SerpError("malformed response body"),
        "key-2": {"ok": True},
    }

    with pytest.raises(SerpError, match="malformed response body"):
        RotatingSerpClient().search({"q": "x"})

    assert FakeSerpClient.calls == ["key-1"]  # never rotated to key-2
    with Session(db_engine) as s:
        assert s.exec(select(SerpApiKeySlot)).all() == []  # DB untouched


# ---------------------------------------------------------------------------
# (g) fail-open: SerpApiKeySlot table missing -> slot 1 only, no crash
# ---------------------------------------------------------------------------
def test_fail_open_when_table_missing(monkeypatch):
    # An engine with NO tables created at all -> any query against
    # SerpApiKeySlot raises sqlalchemy.exc.OperationalError ("no such table").
    bare_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    monkeypatch.setattr("app.database.engine", bare_engine, raising=False)

    FakeSerpClient.behaviors = {"key-1": {"ok": True}}

    result = RotatingSerpClient().search({"q": "x"})

    assert result == {"ok": True}
    assert FakeSerpClient.calls == ["key-1"]
    bare_engine.dispose()


def test_fail_open_all_keys_configured_but_only_slot1_tried_on_failure(monkeypatch):
    """Under fail-open, a slot-1 failure must not crash trying to record it
    in a table that doesn't exist, and rotation to slot 2 never happens
    (slot 1 is the only fail-open candidate by design)."""
    bare_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    monkeypatch.setattr("app.database.engine", bare_engine, raising=False)

    FakeSerpClient.behaviors = {
        "key-1": SerpQuotaError("no searches left"),
        "key-2": {"ok": True},
    }

    with pytest.raises(AllKeysExhaustedError):
        RotatingSerpClient().search({"q": "x"})

    assert FakeSerpClient.calls == ["key-1"]
    bare_engine.dispose()


# ---------------------------------------------------------------------------
# zero keys configured
# ---------------------------------------------------------------------------
def test_zero_keys_configured_raises_all_exhausted(monkeypatch):
    for i in range(2, 12):
        monkeypatch.setattr(settings, f"SERPAPI_API_KEY_{i}", "")
    monkeypatch.setattr(settings, "SERPAPI_API_KEY", "")

    with pytest.raises(AllKeysExhaustedError, match="tried 0 of 0"):
        RotatingSerpClient().search({"q": "x"})

    assert FakeSerpClient.calls == []

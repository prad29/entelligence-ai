"""
SerpApi key rotation for the Deleted Showtimes Check feature.

`RotatingSerpClient` is a drop-in replacement for `SerpClient` at call
sites — same `.search(params) -> Dict[str, Any]` signature — that
transparently fails over to another configured key when the current one
reports quota exhaustion or an outright-bad key (`SerpQuotaError` /
`SerpAuthError`), then keeps its own DB-backed cooldown state so a bad key
doesn't get retried on every single call.

Only that one `search()` call is retried against the next key. Any
higher-level fallback/query-ladder logic (e.g. `_attempt` in
deleted_showtime_task.py) is the caller's business and is untouched here —
this module never re-runs a caller's retry loop, it only decides which raw
SerpApi key backs the next `SerpClient(...).search(params)` call.

Rotation state lives in `SerpApiKeySlot` (see app.models): one row per
configured key slot, written only on exhaustion. Absence of a row, or a
`key_fingerprint` mismatch (the value behind that slot's env var changed),
means "available" — see the model's own docstring for why rows are never
lazily seeded (insert race across Celery's 16 concurrent workers).
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import case, or_
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlmodel import Session, select

from app.config import settings
from app.deleted_showtimes.serp_client import SerpAuthError, SerpClient, SerpQuotaError
from app.observability.serp_logging import log_serpapi_call

logger = logging.getLogger(__name__)

# Exceptions raised while probing whether the SerpApiKeySlot table itself is
# usable (e.g. a mid-deploy window where this code is live against a
# not-yet-migrated DB). Anything else propagates unchanged — we only fail
# open for "the table/relation isn't there".
_TABLE_MISSING_ERRORS = (ProgrammingError, OperationalError)


class AllKeysExhaustedError(RuntimeError):
    """Raised when every configured SerpApi key is currently in cooldown
    (or no key is configured at all)."""


def _fingerprint(key: str) -> str:
    """Short, non-reversible fingerprint used to detect a key being swapped
    into an existing slot (env var value changed under an unchanged slot
    number)."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# Logged once per process, not once per call — the underlying condition
# (table missing) doesn't change between calls within the same mid-deploy
# window, so repeating the warning on every search() would just be noise.
_fail_open_warned = False


def _warn_fail_open_once(exc: Exception) -> None:
    global _fail_open_warned
    if _fail_open_warned:
        return
    _fail_open_warned = True
    logger.warning(
        "serp_key_rotation: SerpApiKeySlot table unavailable (%s: %s) — failing open to "
        "SERPAPI_API_KEY (slot 1) only, no rotation state will be read or written until "
        "the table exists",
        type(exc).__name__, exc,
    )


def _insert_stmt(session: Session):
    """Dialect-appropriate `INSERT ... ON CONFLICT` builder. Production runs
    Postgres; the test suite (and any other non-Postgres consumer) uses
    SQLite, whose `ON CONFLICT DO UPDATE` support has the identical
    `excluded`-alias API, so picking the module by the bound dialect is all
    that's needed to keep this portable."""
    from app.models import SerpApiKeySlot

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return postgresql.insert(SerpApiKeySlot)
    return sqlite.insert(SerpApiKeySlot)


def mark_exhausted(slot: int, key: str, reason: str) -> None:
    """Upsert `SerpApiKeySlot` for `slot` to record that `key` just failed
    with `reason` (a `SerpQuotaError`/`SerpAuthError` message).

    Conflict resolution is the critical bit: a key is re-probed on every
    `search()` call (this rotation scheme tries every *available* key each
    time), so naively overwriting `exhausted_at = now()` on every hit would
    push the cooldown timer forward forever and the key would never self-heal.
    Instead:

    - Fresh exhaustion (no existing row, OR existing row's fingerprint
      doesn't match `key`, OR the existing `exhausted_at` is already past the
      cooldown window) -> overwrite `exhausted_at` to now, reset
      `failure_count` to 1.
    - Repeat hit (same key, existing `exhausted_at` still within the cooldown
      window) -> keep the original `exhausted_at`, just bump `failure_count`
      and refresh `last_error`.

    Fails open (logs once, no-op) if the table itself isn't there yet.
    """
    from app.models import SerpApiKeySlot

    fp = _fingerprint(key)
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=settings.SERPAPI_KEY_COOLDOWN_HOURS)

    try:
        from app.database import engine

        with Session(engine) as session:
            stmt = _insert_stmt(session).values(
                slot=slot,
                key_fingerprint=fp,
                exhausted_at=now,
                last_error=reason,
                failure_count=1,
            )
            is_fresh = or_(
                SerpApiKeySlot.key_fingerprint != stmt.excluded.key_fingerprint,
                SerpApiKeySlot.exhausted_at.is_(None),
                SerpApiKeySlot.exhausted_at <= cutoff,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["slot"],
                set_={
                    "key_fingerprint": stmt.excluded.key_fingerprint,
                    "last_error": stmt.excluded.last_error,
                    "exhausted_at": case(
                        (is_fresh, stmt.excluded.exhausted_at), else_=SerpApiKeySlot.exhausted_at
                    ),
                    "failure_count": case(
                        (is_fresh, stmt.excluded.failure_count),
                        else_=SerpApiKeySlot.failure_count + 1,
                    ),
                },
            )
            session.execute(stmt)
            session.commit()
    except _TABLE_MISSING_ERRORS as exc:
        _warn_fail_open_once(exc)


def _select_candidates(keys: List[Tuple[int, str]]) -> Tuple[List[Tuple[int, str]], Optional[str]]:
    """Returns `(candidates, reason_if_none_available)` — `candidates` is the
    slot-ordered subset of `keys` currently usable; `reason_if_none_available`
    is a best-effort description of why (the most recently recorded failure
    among the unavailable slots), used only when `candidates` ends up empty.

    Fails open to `[(1, settings.SERPAPI_API_KEY)]` (or `[]` if that slot
    isn't configured) if the SerpApiKeySlot table isn't queryable yet.
    """
    from app.models import SerpApiKeySlot

    try:
        from app.database import engine

        with Session(engine) as session:
            rows = {row.slot: row for row in session.exec(select(SerpApiKeySlot)).all()}
    except _TABLE_MISSING_ERRORS as exc:
        _warn_fail_open_once(exc)
        slot1 = next(((slot, key) for slot, key in keys if slot == 1), None)
        return ([slot1] if slot1 else []), None

    now = datetime.utcnow()
    cutoff_delta = timedelta(hours=settings.SERPAPI_KEY_COOLDOWN_HOURS)

    candidates: List[Tuple[int, str]] = []
    cooling_down: List[Tuple[datetime, Optional[str]]] = []

    for slot, key in keys:
        row = rows.get(slot)
        if row is None or row.key_fingerprint != _fingerprint(key) or row.exhausted_at is None:
            candidates.append((slot, key))
            continue
        if now - row.exhausted_at >= cutoff_delta:
            candidates.append((slot, key))
            continue
        cooling_down.append((row.exhausted_at, row.last_error))

    reason = None
    if not candidates and cooling_down:
        cooling_down.sort(key=lambda t: t[0])
        reason = cooling_down[-1][1] or "in cooldown"
    return candidates, reason


class RotatingSerpClient:
    """Drop-in replacement for `SerpClient`: same `.search(params)` API, but
    rotates across `settings.SERPAPI_API_KEYS` on quota/auth failure instead
    of surfacing the error immediately.

    Deliberately holds no state from `settings.SERPAPI_API_KEYS` at
    construction time — that property is re-read on every `search()` call
    (cheap: it's just reading already-loaded `Settings` attributes) so the
    rotation always reflects current config.
    """

    def __init__(self, retries: int = 3, timeout: int = 45, job_id: Optional[str] = None):
        self.retries = retries
        self.timeout = timeout
        # Optional trailing kwarg so existing zero-arg construction still
        # works; supplied by deleted_showtime_task.process_batch so usage is
        # attributable per job (spec §3).
        self.job_id = job_id

    def _log_attempt(
        self,
        slot: int,
        client: Any,
        started: float,
        *,
        success: bool,
        error_type: Optional[str] = None,
    ) -> None:
        """Record one attempt. Never raises — log_serpapi_call is already
        total, and this second guard keeps a future change to it from ever
        being able to break a search (spec §7)."""
        try:
            log_serpapi_call(
                job_id=self.job_id,
                slot=slot,
                success=success,
                # getattr, not client.calls_made: a future substitute client
                # missing the counter must degrade to 0 rather than raise.
                calls_made=getattr(client, "calls_made", 0),
                latency_ms=int((time.monotonic() - started) * 1000),
                error_type=error_type,
            )
        except Exception as log_exc:  # noqa: BLE001 — spec §7
            logger.warning("serpapi_call_log_failed slot=%d error=%s", slot, log_exc)

    def search(self, params: Dict[str, str]) -> Dict[str, Any]:
        keys = settings.SERPAPI_API_KEYS
        total = len(keys)
        if total == 0:
            raise AllKeysExhaustedError(
                "tried 0 of 0 configured SerpApi key(s); no SERPAPI_API_KEY* env var is set"
            )

        candidates, cooldown_reason = _select_candidates(keys)
        if not candidates:
            reason = cooldown_reason or "no SerpApi keys are currently available"
            logger.warning(
                "serp_key_rotation: all keys unavailable before any attempt (0 of %d "
                "configured); most recent failure: %s", total, reason,
            )
            raise AllKeysExhaustedError(
                f"tried 0 of {total} configured key(s); most recent failure: {reason}"
            )

        attempted = 0
        last_reason: Optional[str] = None
        for slot, key in candidates:
            attempted += 1
            client = SerpClient(key, retries=self.retries, timeout=self.timeout)
            started = time.monotonic()
            try:
                data = client.search(params)
            except (SerpQuotaError, SerpAuthError) as exc:
                last_reason = str(exc)
                # A rejected attempt still consumed a request against that key,
                # so it gets its own row — per-slot failure visibility is the
                # reason a 13-key pool exists (spec §6).
                self._log_attempt(slot, client, started, success=False,
                                  error_type=type(exc).__name__)
                logger.warning(
                    "serp_key_rotation: slot %d rejected (%s: %s) — rotating to next key",
                    slot, type(exc).__name__, exc,
                )
                mark_exhausted(slot, key, last_reason)
                continue
            except BaseException as exc:
                # Everything else (plain SerpError, timeouts) propagates
                # unchanged, exactly as before — but the attempt is recorded
                # first so a slot failing this way is still visible.
                self._log_attempt(slot, client, started, success=False,
                                  error_type=type(exc).__name__)
                raise
            self._log_attempt(slot, client, started, success=True)
            return data

        logger.warning(
            "serp_key_rotation: all %d available key(s) exhausted this call (%d of %d "
            "configured); most recent failure: %s", attempted, attempted, total, last_reason,
        )
        raise AllKeysExhaustedError(
            f"tried {attempted} of {total} configured key(s); most recent failure: {last_reason}"
        )

"""
scrape.do token rotation for the Deleted Showtimes site-verification step.

`RotatingScrapeDoClient` is a drop-in replacement for `ScrapeDoClient` at call
sites — same `.fetch(url, **kwargs) -> ScrapeResponse` signature — that
transparently fails over to another configured token when the current one
reports credit exhaustion or an outright-bad token (`ScrapeDoQuotaError` /
`ScrapeDoAuthError`), then keeps its own DB-backed cooldown state so a bad
token doesn't get retried on every single call.

Direct structural copy of serp_key_rotation.py — see that module's docstring
for the full rationale (fingerprint-based swap detection, never-lazily-seeded
rows, fail-open on a not-yet-migrated table). Only the model/exception/
settings names differ.
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
from app.deleted_showtimes.scrapedo_client import ScrapeDoAuthError, ScrapeDoClient, ScrapeDoQuotaError
from app.observability.serp_logging import log_scrapedo_call

logger = logging.getLogger(__name__)

_TABLE_MISSING_ERRORS = (ProgrammingError, OperationalError)


class AllTokensExhaustedError(RuntimeError):
    """Raised when every configured scrape.do token is currently in cooldown
    (or no token is configured at all)."""


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


_fail_open_warned = False


def _warn_fail_open_once(exc: Exception) -> None:
    global _fail_open_warned
    if _fail_open_warned:
        return
    _fail_open_warned = True
    logger.warning(
        "scrapedo_key_rotation: ScrapeDoTokenSlot table unavailable (%s: %s) — failing open to "
        "SCRAPE_DO_TOKEN (slot 1) only, no rotation state will be read or written until "
        "the table exists",
        type(exc).__name__, exc,
    )


def _insert_stmt(session: Session):
    from app.models import ScrapeDoTokenSlot

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return postgresql.insert(ScrapeDoTokenSlot)
    return sqlite.insert(ScrapeDoTokenSlot)


def mark_exhausted(slot: int, token: str, reason: str) -> None:
    """Upsert `ScrapeDoTokenSlot` for `slot` — see serp_key_rotation.mark_exhausted
    for the full conflict-resolution rationale (fresh exhaustion vs. repeat
    hit within the same cooldown window). Fails open if the table isn't there."""
    from app.models import ScrapeDoTokenSlot

    fp = _fingerprint(token)
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=settings.SCRAPE_DO_TOKEN_COOLDOWN_HOURS)

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
                ScrapeDoTokenSlot.key_fingerprint != stmt.excluded.key_fingerprint,
                ScrapeDoTokenSlot.exhausted_at.is_(None),
                ScrapeDoTokenSlot.exhausted_at <= cutoff,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["slot"],
                set_={
                    "key_fingerprint": stmt.excluded.key_fingerprint,
                    "last_error": stmt.excluded.last_error,
                    "exhausted_at": case(
                        (is_fresh, stmt.excluded.exhausted_at), else_=ScrapeDoTokenSlot.exhausted_at
                    ),
                    "failure_count": case(
                        (is_fresh, stmt.excluded.failure_count),
                        else_=ScrapeDoTokenSlot.failure_count + 1,
                    ),
                },
            )
            session.execute(stmt)
            session.commit()
    except _TABLE_MISSING_ERRORS as exc:
        _warn_fail_open_once(exc)


def _select_candidates(tokens: List[Tuple[int, str]]) -> Tuple[List[Tuple[int, str]], Optional[str]]:
    from app.models import ScrapeDoTokenSlot

    try:
        from app.database import engine

        with Session(engine) as session:
            rows = {row.slot: row for row in session.exec(select(ScrapeDoTokenSlot)).all()}
    except _TABLE_MISSING_ERRORS as exc:
        _warn_fail_open_once(exc)
        slot1 = next(((slot, token) for slot, token in tokens if slot == 1), None)
        return ([slot1] if slot1 else []), None

    now = datetime.utcnow()
    cutoff_delta = timedelta(hours=settings.SCRAPE_DO_TOKEN_COOLDOWN_HOURS)

    candidates: List[Tuple[int, str]] = []
    cooling_down: List[Tuple[datetime, Optional[str]]] = []

    for slot, token in tokens:
        row = rows.get(slot)
        if row is None or row.key_fingerprint != _fingerprint(token) or row.exhausted_at is None:
            candidates.append((slot, token))
            continue
        if now - row.exhausted_at >= cutoff_delta:
            candidates.append((slot, token))
            continue
        cooling_down.append((row.exhausted_at, row.last_error))

    reason = None
    if not candidates and cooling_down:
        cooling_down.sort(key=lambda t: t[0])
        reason = cooling_down[-1][1] or "in cooldown"
    return candidates, reason


class RotatingScrapeDoClient:
    """Drop-in replacement for `ScrapeDoClient`: same `.fetch(url, **kwargs)`
    API, but rotates across `settings.SCRAPE_DO_TOKENS` on quota/auth failure
    instead of surfacing the error immediately."""

    def __init__(self, timeout: int = 90, job_id: Optional[str] = None):
        self.timeout = timeout
        self.job_id = job_id
        # Accumulated across every .fetch() call made through this instance
        # (one instance is shared across an adapter's whole multi-fetch call,
        # e.g. AMC's multi-candidate/multi-attempt loop) — read by the caller
        # afterwards to bump DeletedShowtimeJob.scrape_credits_used.
        self.calls_made = 0
        self.total_credits = 0

    def _log_attempt(
        self,
        slot: int,
        client: Any,
        started: float,
        *,
        success: bool,
        error_type: Optional[str] = None,
    ) -> None:
        try:
            log_scrapedo_call(
                job_id=self.job_id,
                slot=slot,
                success=success,
                credits_used=getattr(client, "total_credits", 0),
                latency_ms=int((time.monotonic() - started) * 1000),
                error_type=error_type,
            )
        except Exception as log_exc:  # noqa: BLE001
            logger.warning("scrapedo_call_log_failed slot=%d error=%s", slot, log_exc)

    def fetch(self, url: str, **kwargs: Any) -> Any:
        tokens = settings.SCRAPE_DO_TOKENS
        total = len(tokens)
        if total == 0:
            raise AllTokensExhaustedError(
                "tried 0 of 0 configured scrape.do token(s); no SCRAPE_DO_TOKEN* env var is set"
            )

        candidates, cooldown_reason = _select_candidates(tokens)
        if not candidates:
            reason = cooldown_reason or "no scrape.do tokens are currently available"
            logger.warning(
                "scrapedo_key_rotation: all tokens unavailable before any attempt (0 of %d "
                "configured); most recent failure: %s", total, reason,
            )
            raise AllTokensExhaustedError(
                f"tried 0 of {total} configured token(s); most recent failure: {reason}"
            )

        attempted = 0
        last_reason: Optional[str] = None
        for slot, token in candidates:
            attempted += 1
            client = ScrapeDoClient(token, timeout=self.timeout)
            started = time.monotonic()
            try:
                data = client.fetch(url, **kwargs)
            except (ScrapeDoQuotaError, ScrapeDoAuthError) as exc:
                last_reason = str(exc)
                self._log_attempt(slot, client, started, success=False,
                                  error_type=type(exc).__name__)
                logger.warning(
                    "scrapedo_key_rotation: slot %d rejected (%s: %s) — rotating to next token",
                    slot, type(exc).__name__, exc,
                )
                mark_exhausted(slot, token, last_reason)
                continue
            except BaseException as exc:
                self._log_attempt(slot, client, started, success=False,
                                  error_type=type(exc).__name__)
                raise
            self._log_attempt(slot, client, started, success=True)
            self.calls_made += client.calls_made
            self.total_credits += client.total_credits
            return data

        logger.warning(
            "scrapedo_key_rotation: all %d available token(s) exhausted this call (%d of %d "
            "configured); most recent failure: %s", attempted, attempted, total, last_reason,
        )
        raise AllTokensExhaustedError(
            f"tried {attempted} of {total} configured token(s); most recent failure: {last_reason}"
        )

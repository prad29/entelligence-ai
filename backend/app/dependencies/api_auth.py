"""
Auth, rate-limit, and concurrency dependencies for the API-key-authenticated
surfaces: external title-matching (app/routers/external_title_match.py) and
lobby-check (app/routers/lobby_check.py).

Plain FastAPI `Depends()` callables — no new middleware. This mirrors the
codebase's existing convention: every route in movie_title_match.py only
ever uses `Depends(get_session)`; there is no `middleware/` directory and no
prior auth dependency to extend.

Rate limiting uses a Redis fixed-window counter (INCR + EXPIRE), the same
"Redis as coordination primitive" pattern sandbox_semaphore.py already
establishes for a different concern (bounding concurrent sandbox calls).

The two surfaces share auth/rate-limit (_authenticate) but have INDEPENDENT
concurrent-jobs budgets, each counted against its own job table
(ApiTitleMatchJob vs. LobbyCheckJob) — a title-match backlog must never 429
a lobby-check submission, and vice versa, since the two surfaces have very
different per-job runtimes (design doc §5.3).

The x-api-key header is declared via fastapi.security.APIKeyHeader, wired in
as a sub-dependency (Depends(_authenticate)) rather than read off the raw
Request — that's what makes FastAPI register it as a security scheme and
show the "Authorize" lock + an actual input field in Swagger UI. Reading
request.headers.get(...) directly (the previous approach) is invisible to
FastAPI's OpenAPI generation entirely, which is why Swagger never offered a
way to set it.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import ApiKey, ApiTitleMatchJob

IN_FLIGHT_PHASES = ("queued", "syncing", "processing")
LOBBY_CHECK_IN_FLIGHT_PHASES = ("queued", "processing")

_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL)


def _authenticate(
    session: Session = Depends(get_session),
    raw_key: Optional[str] = Depends(_api_key_header),
) -> ApiKey:
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    api_key = session.exec(
        select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_key))
    ).first()
    if api_key is None or not api_key.active:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    _check_rate_limit(api_key)
    return api_key


def require_api_key(
    session: Session = Depends(get_session),
    api_key: ApiKey = Depends(_authenticate),
) -> ApiKey:
    _check_concurrent_jobs(session, api_key, ApiTitleMatchJob, IN_FLIGHT_PHASES)
    return api_key


def require_api_key_lobby_check(
    session: Session = Depends(get_session),
    api_key: ApiKey = Depends(_authenticate),
) -> ApiKey:
    from app.models import LobbyCheckJob

    _check_concurrent_jobs(session, api_key, LobbyCheckJob, LOBBY_CHECK_IN_FLIGHT_PHASES)
    return api_key


def _check_rate_limit(api_key: ApiKey) -> None:
    try:
        r = _get_redis()
    except Exception:
        # Fail open — same rationale as sandbox_semaphore.py: a Redis outage
        # should not take down the API surface it's merely throttling.
        return

    minute_bucket = datetime.utcnow().strftime("%Y%m%d%H%M")
    key = f"ratelimit:{api_key.id}:{minute_bucket}"
    try:
        count = r.incr(key)
        if count == 1:
            r.expire(key, 60)
    except Exception:
        return

    if count > api_key.requests_per_minute:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": "60"},
        )


def _check_concurrent_jobs(session: Session, api_key: ApiKey, job_model, in_flight_phases) -> None:
    in_flight = session.exec(
        select(job_model)
        .where(job_model.api_key_id == api_key.id)
        .where(job_model.phase.in_(in_flight_phases))
    ).all()
    if len(in_flight) >= api_key.max_concurrent_jobs:
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent jobs for this API key",
            headers={"Retry-After": "60"},
        )


def require_db_update_permission(
    db_update: bool = False,
    api_key: ApiKey = Depends(require_api_key),
) -> ApiKey:
    if db_update and not api_key.db_update_allowed:
        raise HTTPException(
            status_code=403,
            detail="This API key is not permitted to trigger db_update=true",
        )
    return api_key

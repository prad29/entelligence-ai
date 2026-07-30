"""
Auth, rate-limit, and quota dependencies for the external title-matching API
(app/routers/external_title_match.py).

Plain FastAPI `Depends()` callables — no new middleware. This mirrors the
codebase's existing convention: every route in movie_title_match.py only
ever uses `Depends(get_session)`; there is no `middleware/` directory and no
prior auth dependency to extend.

Rate limiting uses a Redis fixed-window counter (INCR + EXPIRE), the same
"Redis as coordination primitive" pattern sandbox_semaphore.py already
establishes for a different concern (bounding concurrent sandbox calls).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import ApiKey, ApiKeyMonthlyUsage, ApiTitleMatchJob

IN_FLIGHT_PHASES = ("queued", "syncing", "processing")


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL)


def _current_year_month() -> str:
    return datetime.utcnow().strftime("%Y-%m")


def require_api_key(
    request: Request,
    session: Session = Depends(get_session),
) -> ApiKey:
    raw_key = request.headers.get("x-api-key")
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    api_key = session.exec(
        select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_key))
    ).first()
    if api_key is None or not api_key.active:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    _check_rate_limit(api_key)
    _check_monthly_quota(session, api_key)
    _check_concurrent_jobs(session, api_key)

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


def _check_monthly_quota(session: Session, api_key: ApiKey) -> None:
    if api_key.monthly_row_quota is None:
        return

    year_month = _current_year_month()
    usage = session.exec(
        select(ApiKeyMonthlyUsage)
        .where(ApiKeyMonthlyUsage.api_key_id == api_key.id)
        .where(ApiKeyMonthlyUsage.year_month == year_month)
    ).first()
    rows_used = usage.rows_used if usage is not None else 0

    if rows_used >= api_key.monthly_row_quota:
        raise HTTPException(
            status_code=429,
            detail="Monthly row quota exceeded",
            headers={"Retry-After": "3600"},
        )


def _check_concurrent_jobs(session: Session, api_key: ApiKey) -> None:
    in_flight = session.exec(
        select(ApiTitleMatchJob)
        .where(ApiTitleMatchJob.api_key_id == api_key.id)
        .where(ApiTitleMatchJob.phase.in_(IN_FLIGHT_PHASES))
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

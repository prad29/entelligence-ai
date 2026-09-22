"""
TTL-based, self-expiring Redis semaphore that caps concurrent scrape.do calls
GLOBALLY (across every job/batch at once) at `settings.SCRAPE_DO_MAX_CONCURRENCY`.

Deliberately global, not per-job like job_semaphore.py's SerpApi cap: SerpApi
key rotation gives each key its own quota, so per-job caps make sense there,
but scrape.do's concurrency limit is a property of the account/plan as a
whole — many jobs' batches can run concurrently on the "deleted-showtimes"
Celery queue (worker concurrency 16), and each one now also calls scrape.do,
so the cap has to apply across all of them at once or a handful of busy jobs
could still blow past the plan's real concurrency ceiling.

Same "one Redis key per holder with SET ... EX NX" design as job_semaphore.py
— see that module for the acquire-loop rationale. Fails open (returns a
sentinel holder) if Redis is unreachable.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

FAIL_OPEN_HOLDER = "fail-open"

_ACQUIRE_LUA = """
local pattern = ARGV[4]
local max = tonumber(ARGV[3])
local count = 0
local cursor = "0"
repeat
    local res = redis.call("SCAN", cursor, "MATCH", pattern, "COUNT", 100)
    cursor = res[1]
    count = count + #res[2]
    if count >= max then
        return 0
    end
until cursor == "0"
redis.call("SET", ARGV[1], "1", "EX", tonumber(ARGV[2]), "NX")
return 1
"""

_RETRY_BASE_SLEEP = 0.25
_RETRY_JITTER = 0.25
_HOLDER_TTL_SECONDS = 120  # comfortably longer than a single scrape.do fetch+retry
_PREFIX = "deleted-showtimes:scrapedo-sem:"
_PATTERN = f"{_PREFIX}*"


def _get_redis():
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 - any failure means fail-open
        logger.warning("scrapedo_semaphore redis unavailable, failing open: %s", exc)
        return None


def acquire(timeout: float, max_concurrency: Optional[int] = None) -> str:
    client = _get_redis()
    if client is None:
        return FAIL_OPEN_HOLDER

    cap = max_concurrency if max_concurrency is not None else settings.SCRAPE_DO_MAX_CONCURRENCY
    deadline = time.monotonic() + timeout

    while True:
        holder_id = f"{_PREFIX}{uuid.uuid4()}"
        try:
            acquired = client.eval(
                _ACQUIRE_LUA, 0, holder_id, str(_HOLDER_TTL_SECONDS), str(cap), _PATTERN
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("scrapedo_semaphore eval failed, failing open: %s", exc)
            return FAIL_OPEN_HOLDER

        if acquired in (1, b"1", "1", 1.0):
            return holder_id

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"scrapedo_semaphore: could not acquire a global slot within {timeout}s (cap={cap})"
            )
        time.sleep(_RETRY_BASE_SLEEP + random.random() * _RETRY_JITTER)


def release(holder_id: Optional[str]) -> None:
    if not holder_id or holder_id == FAIL_OPEN_HOLDER:
        return
    try:
        client = _get_redis()
        if client is None:
            return
        client.delete(holder_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("scrapedo_semaphore release failed for %s: %s", holder_id, exc)

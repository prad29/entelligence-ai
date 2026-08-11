"""
TTL-based, self-expiring Redis semaphore that caps concurrent SerpApi calls
for a SINGLE job at `job.workers` (the script's old --workers flag, now a
per-run advanced option instead of a process-wide ThreadPoolExecutor size).

Same "one Redis key per holder with SET ... EX NX" design as
app/title_matching/sandbox_semaphore.py, keyed per job_id so concurrent jobs
never contend with each other's caps. Fails open (returns a sentinel holder)
if Redis is unreachable — batch tasks then run at whatever concurrency the
Celery queue itself allows.
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
_HOLDER_TTL_SECONDS = 120  # comfortably longer than a single SerpApi call+retries


def _holder_prefix(job_id: str) -> str:
    return f"deleted-showtimes:sem:{job_id}:"


def _get_redis():
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 - any failure means fail-open
        logger.warning("job_semaphore redis unavailable, failing open: %s", exc)
        return None


def acquire(job_id: str, max_concurrency: int, timeout: float) -> str:
    client = _get_redis()
    if client is None:
        return FAIL_OPEN_HOLDER

    prefix = _holder_prefix(job_id)
    pattern = f"{prefix}*"
    deadline = time.monotonic() + timeout

    while True:
        holder_id = f"{prefix}{uuid.uuid4()}"
        try:
            acquired = client.eval(
                _ACQUIRE_LUA, 0, holder_id, str(_HOLDER_TTL_SECONDS), str(max_concurrency), pattern
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("job_semaphore eval failed, failing open: %s", exc)
            return FAIL_OPEN_HOLDER

        if acquired in (1, b"1", "1", 1.0):
            return holder_id

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"job_semaphore: could not acquire a slot for job {job_id} within "
                f"{timeout}s (cap={max_concurrency})"
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
        logger.warning("job_semaphore release failed for %s: %s", holder_id, exc)

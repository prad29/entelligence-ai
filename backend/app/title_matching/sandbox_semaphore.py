"""
TTL-based, self-expiring Redis semaphore that caps concurrent claude-sandbox
calls at ``settings.AGENTIC_BATCH_MAX_CONCURRENCY``.

WHY NOT A BARE INCR/DECR COUNTER
================================
A naive counter (``INCR`` on acquire, ``DECR`` on release) leaks permanently
if a holder process is SIGKILLed: the ``finally`` / release ``DECR`` never
runs, so the counter drifts upward and never recovers. Over time the cap
wedges toward 0 and the whole batch pipeline deadlocks with no self-healing.

This implementation instead uses ONE Redis key PER HOLDER, written with
``SET ... EX <ttl> NX``. The TTL (slightly longer than the per-row timeout)
is what actually guarantees no permanent leak: if a holder crashes without
releasing, its key simply expires and the slot frees itself. ``release`` is a
best-effort early ``DEL`` for the happy path; correctness does not depend on
it ever running.

The Redis semaphore is only a *soft* backstop. The primary concurrency bound
is the dedicated ``agentic`` Celery queue running at worker concurrency
``settings.AGENTIC_BATCH_MAX_CONCURRENCY``. If Redis is unreachable we
therefore FAIL OPEN (return a sentinel holder id and log a warning): the
queue-level concurrency still bounds in-flight sandbox calls.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Optional

from app.config import settings
from app.title_matching.agentic import limits

logger = logging.getLogger(__name__)

HOLDER_PREFIX = "sandbox:holder:"
# Sentinel returned when Redis is unreachable (fail-open path). release() treats
# it as a no-op so callers never need to special-case it.
FAIL_OPEN_HOLDER = "fail-open"

# Atomic check-and-set: count live holder keys, and only create a new one if we
# are still under the cap. Doing the count and the SET in a single Lua script
# eliminates the race between "count" and "setnx" that a plain scan-then-set
# loop would have.
#   KEYS: none (SCAN over a prefix pattern instead)
#   ARGV[1] = holder key to create
#   ARGV[2] = ttl seconds
#   ARGV[3] = max concurrency
#   ARGV[4] = scan match pattern (prefix*)
# Returns 1 if acquired, 0 if at capacity.
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

# Time between acquire attempts while blocking, in seconds. A little jitter is
# added so concurrent workers don't retry in lockstep.
_RETRY_BASE_SLEEP = 0.25
_RETRY_JITTER = 0.25


def _get_redis():
    """Return a redis client, or None if the library/connection is unavailable."""
    try:
        import redis  # local import so importing this module never hard-requires redis

        client = redis.Redis.from_url(settings.REDIS_URL)
        client.ping()
        return client
    except Exception as exc:  # noqa: BLE001 - any failure means fail-open
        logger.warning("sandbox_semaphore redis unavailable, failing open: %s", exc)
        return None


def _ttl_seconds() -> int:
    """Delegates to limits.holder_ttl_seconds() — the single source of truth
    for every timeout/TTL derived from AGENTIC_TIMEOUT_SECONDS, so this value
    can never drift out of lockstep with the row task's own soft/hard time
    limits (see title_matching/agentic/limits.py's module docstring for why
    that drift was a real bug)."""
    return limits.holder_ttl_seconds()


def acquire(timeout: float, *, ttl: Optional[int] = None, redis_client=None) -> str:
    """
    Acquire a semaphore slot, blocking up to ``timeout`` seconds.

    Returns an opaque holder id string to pass to :func:`release`. Raises
    :class:`TimeoutError` if a slot never frees within ``timeout``.

    If Redis is unreachable, logs a warning and immediately returns
    ``FAIL_OPEN_HOLDER`` (the ``agentic`` queue's worker concurrency, i.e.
    ``settings.AGENTIC_BATCH_MAX_CONCURRENCY``, still bounds real
    concurrency in that case).

    ``ttl`` / ``redis_client`` are injection points for tests.
    """
    client = redis_client if redis_client is not None else _get_redis()
    if client is None:
        return FAIL_OPEN_HOLDER

    effective_ttl = ttl if ttl is not None else _ttl_seconds()
    max_conc = settings.AGENTIC_BATCH_MAX_CONCURRENCY
    pattern = f"{HOLDER_PREFIX}*"
    deadline = time.monotonic() + timeout

    while True:
        holder_id = f"{HOLDER_PREFIX}{uuid.uuid4()}"
        try:
            acquired = client.eval(
                _ACQUIRE_LUA, 0, holder_id, str(effective_ttl), str(max_conc), pattern
            )
        except Exception as exc:  # noqa: BLE001 - redis died mid-run -> fail open
            logger.warning("sandbox_semaphore eval failed, failing open: %s", exc)
            return FAIL_OPEN_HOLDER

        if acquired == 1 or acquired == b"1" or acquired == "1" or acquired == 1.0:
            return holder_id

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"sandbox_semaphore: could not acquire a slot within {timeout}s "
                f"(cap={max_conc})"
            )
        time.sleep(_RETRY_BASE_SLEEP + random.random() * _RETRY_JITTER)


def release(holder_id: Optional[str], *, redis_client=None) -> None:
    """
    Best-effort early release of a holder slot by deleting its key.

    The TTL is the real guarantee against leaks; this just frees the slot
    sooner on the happy path. Never raises. A no-op for the fail-open sentinel
    or an empty holder id.
    """
    if not holder_id or holder_id == FAIL_OPEN_HOLDER:
        return
    try:
        client = redis_client if redis_client is not None else _get_redis()
        if client is None:
            return
        client.delete(holder_id)
    except Exception as exc:  # noqa: BLE001 - release must never break the caller
        logger.warning("sandbox_semaphore release failed for %s: %s", holder_id, exc)


def holder_count(*, redis_client=None) -> Optional[int]:
    """Read-only sample of the LIVE number of held semaphore slots right now.

    Not used by anything yet in Phase 1 — it's added now because it lives
    naturally next to the rest of the semaphore code, ahead of Phase 2's
    queue-depth/pool-utilization observability sampler, which is the first
    real caller.

    Deliberately does NOT reuse ``_ACQUIRE_LUA``: that script early-exits
    (``return 0``) the moment its running count reaches the cap, so it would
    under-report whenever the pool is fully saturated — exactly the moment an
    accurate holder count matters most for observability. This does a plain
    SCAN to completion instead.

    Returns ``None`` (never raises) if Redis is unreachable — "unknown", not
    "zero". A sampler that can't tell "no holders" apart from "couldn't
    check" would silently misreport a healthy-looking pool during a Redis
    outage.
    """
    client = redis_client if redis_client is not None else _get_redis()
    if client is None:
        return None

    pattern = f"{HOLDER_PREFIX}*"
    try:
        count = 0
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=100)
            count += len(keys)
            if cursor == 0:
                break
        return count
    except Exception as exc:  # noqa: BLE001 - sampler must never raise
        logger.warning("sandbox_semaphore holder_count scan failed: %s", exc)
        return None

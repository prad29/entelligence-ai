"""
Single source of truth for every timeout/TTL derived from
``settings.AGENTIC_TIMEOUT_SECONDS``, so the sandbox semaphore's slot-wait
timeout, its holder TTL, and the row Celery task's soft/hard time limits
cannot silently drift apart.

THE BUG THIS FIXES
===================
Before this module existed, ``agentic_batch_row``'s ``soft_time_limit`` and
the value passed to ``sandbox_semaphore.acquire(timeout=...)`` were the SAME
number (``AGENTIC_TIMEOUT_SECONDS + 30``). That is wrong: a row's wall-clock
budget is slot-wait time PLUS the sandbox call itself, not slot-wait time
duplicated as if it were the whole budget. Any row that waited any nonzero
time for a semaphore slot and then made a full-length sandbox call was
guaranteed to exceed the soft limit and get recorded as a spurious timeout —
this happens for real at the current concurrency (2) any time the queue is
non-empty when a row starts waiting.

THE BUDGET MODEL
=================
One row's real wall-clock budget, worst case, is:

    slot-wait (up to one sandbox attempt's worth of time, see
    ``slot_wait_timeout``)
  + one full sandbox attempt (``sandbox_attempt_seconds``)
  + possibly one in-process fast-fail throttle retry, itself up to one
    attempt's worth of time, plus its exponential backoff sleep (bounded,
    see ``AGENTIC_THROTTLE_BACKOFF_BASE_SECONDS`` / runner.py)

That's "up to 3 attempts" of real budget, not 1 — hence the 3x multiplier
below. Concrete numbers (documented, not just copied) at the default
``AGENTIC_TIMEOUT_SECONDS=90``:

    slot_wait_timeout()   =  90s   (one attempt)
    row_soft_time_limit() = 270s   (3x: slot-wait + attempt + retry-attempt)
    row_time_limit()      = 330s   (+60s: headroom for the task's own
                                     except/finally cleanup — recording the
                                     failed row, releasing the semaphore —
                                     to run to completion after
                                     SoftTimeLimitExceeded fires but before
                                     Celery's hard kill)
    holder_ttl_seconds()  = 390s   (+60s more: the semaphore holder key must
                                     outlive the worst-case row lifetime
                                     (row_time_limit) so a still-legitimately-
                                     running row is never treated as an
                                     abandoned holder by its own key expiring
                                     mid-flight; the extra 60s covers the gap
                                     between Celery's SIGKILL and the OS/
                                     Celery actually reaping the process)

This keeps a strict ordering (enforced by a regression test in
``test_agentic_throttle_retry.py``):

    holder_ttl_seconds() > row_time_limit() > row_soft_time_limit()
        > slot_wait_timeout()

If someone changes these derivations later and breaks that ordering, the
original bug (a soft limit smaller than the sum of what a row can legitimately
spend waiting + working) can reappear.
"""

from __future__ import annotations

import random

from app.config import settings

# Row soft/hard time-limit multiplier of one sandbox attempt (see module
# docstring: slot-wait + attempt + one fast-fail retry-attempt).
_ROW_SOFT_LIMIT_MULTIPLIER = 3
# Headroom (seconds) added on top of the soft limit for the row task's own
# except/finally cleanup to finish before Celery's hard kill.
_ROW_HARD_LIMIT_HEADROOM_SECONDS = 60
# Headroom (seconds) added on top of the row hard time limit for the
# semaphore holder TTL, so a holder key never expires out from under a row
# that is still legitimately running.
_HOLDER_TTL_HEADROOM_SECONDS = 60


def sandbox_attempt_seconds() -> int:
    """Wall-clock budget for exactly one sandbox call attempt."""
    return settings.AGENTIC_TIMEOUT_SECONDS


def slot_wait_timeout() -> int:
    """How long a row should wait for a semaphore slot before giving up.

    Deliberately NOT the full row time budget: if the pool is backed up
    enough that a slot doesn't free within one sandbox attempt's worth of
    time, waiting even longer just delays the inevitable — the row instead
    fails fast (TimeoutError -> recorded as a failed row / retried by the
    task's normal retry path) rather than silently eating most of the row's
    total budget on nothing but queueing.
    """
    return sandbox_attempt_seconds()


def row_soft_time_limit() -> int:
    """Celery ``soft_time_limit`` for one row task.

    3x one sandbox attempt: slot-wait (<= 1 attempt) + the sandbox call
    itself (1 attempt) + budget for one in-process fast-fail throttle retry
    plus its backoff sleep (<= 1 attempt, generously bounded). See module
    docstring for the full worked example.
    """
    return _ROW_SOFT_LIMIT_MULTIPLIER * sandbox_attempt_seconds()


def row_time_limit() -> int:
    """Celery hard ``time_limit`` for one row task.

    Soft limit plus headroom for the row task's except/finally blocks
    (recording a failed row, releasing the sandbox semaphore) to actually
    run to completion after ``SoftTimeLimitExceeded`` fires, before Celery's
    SIGKILL-equivalent hard limit lands.
    """
    return row_soft_time_limit() + _ROW_HARD_LIMIT_HEADROOM_SECONDS


def holder_ttl_seconds() -> int:
    """TTL (seconds) for a sandbox_semaphore holder key.

    Must outlive the worst-case row lifetime (``row_time_limit``) so a row
    that is still legitimately running is never treated as an abandoned
    holder by its own semaphore key expiring mid-flight. The extra headroom
    covers the gap between Celery's hard kill and the process actually being
    reaped.
    """
    return row_time_limit() + _HOLDER_TTL_HEADROOM_SECONDS


def throttle_retry_countdown(retries: int) -> int:
    """Seconds to wait before Celery redelivers a row that raised
    ``AgenticThrottleError``, given ``retries`` PRIOR Celery-level retries of
    this row (i.e. ``self.request.retries`` at the time of the throttle).

    Exponential in ``retries`` with +/-50% jitter, mirroring
    ``sandbox_semaphore.py``'s own jittered acquire-retry loop: multiple rows
    throttled by the same Bedrock quota exhaustion (a whole batch hitting the
    ceiling at once) must not all wake up and retry in the same instant, or
    the thundering herd immediately re-trips the same throttle.
    """
    base = settings.AGENTIC_THROTTLE_CELERY_BACKOFF_SECONDS
    jittered = base * (2**max(retries, 0)) * random.uniform(0.5, 1.5)
    return max(1, int(round(jittered)))

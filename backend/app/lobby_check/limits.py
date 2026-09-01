"""Single source of truth for lobby_check_row's soft/hard Celery time
limits, derived from settings.LOBBY_CHECK_TIMEOUT_SECONDS — mirrors
title_matching/agentic/limits.py's rationale (one derivation module so the
task decorator's timeouts and the code's own budget reasoning cannot
silently drift apart), simplified for this pipeline: there is no semaphore
slot-wait here (extractor.py's client is only ever contended for by this
worker's own concurrency setting, which the queue itself already bounds —
no second process's parallelism needs protecting the way sandbox_semaphore
protects the claude-sandbox container). The budget is just the image fetch
plus up to two converse attempts (the primary call plus the one in-process
repair retry — see extractor.extract_material_record).

Concrete numbers at the defaults (LOBBY_CHECK_TIMEOUT_SECONDS=90,
LOBBY_CHECK_IMAGE_FETCH_TIMEOUT_SECONDS=30):

    row_soft_time_limit() = 210s  (fetch + 2 converse attempts)
    row_time_limit()      = 240s  (+30s headroom for the task's own
                                    except/finally cleanup — recording the
                                    failed row — to run to completion after
                                    SoftTimeLimitExceeded fires but before
                                    Celery's hard kill)

Ordering invariant (enforced by a regression test in
test_lobby_check_extractor.py):

    row_time_limit() > row_soft_time_limit() > LOBBY_CHECK_TIMEOUT_SECONDS
"""

from __future__ import annotations

from app.config import settings


def row_soft_time_limit() -> int:
    return (
        2 * settings.LOBBY_CHECK_TIMEOUT_SECONDS
        + settings.LOBBY_CHECK_IMAGE_FETCH_TIMEOUT_SECONDS
    )


def row_time_limit() -> int:
    return row_soft_time_limit() + 30

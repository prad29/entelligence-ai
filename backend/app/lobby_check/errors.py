"""Exception taxonomy driving lobby_check_row's three-layer retry policy
(see docs/plans/2026-09-01-lobby-check-design.md §4.3).

LobbyCheckThrottleError/LobbyCheckTransientError are Celery-retried;
LobbyCheckSchemaError/LobbyCheckImageError are deterministic and must fail
the row immediately — retrying them only burns Bedrock quota for a
guaranteed-identical outcome.
"""

from __future__ import annotations


class LobbyCheckError(Exception):
    """Base class for every lobby-check extraction failure."""


class LobbyCheckThrottleError(LobbyCheckError):
    """Bedrock throttled the request (ThrottlingException,
    TooManyRequestsException, ServiceQuotaExceededException, HTTP 429).
    Retry with exponential backoff."""


class LobbyCheckTransientError(LobbyCheckError):
    """Transient failure worth a short-countdown retry: 5xx, model-not-ready/
    timeout, connection/read timeouts on the Bedrock call or the image
    fetch."""


class LobbyCheckSchemaError(LobbyCheckError):
    """The model's response never validated against EXTRACTION_SCHEMA, even
    after the one in-process repair retry. Deterministic — do not retry at
    the Celery layer."""


class LobbyCheckImageError(LobbyCheckError):
    """The image itself is unusable: fetch failed (403/404), oversized,
    wrong content-type, or the host isn't allow-listed. Deterministic — do
    not retry at the Celery layer."""

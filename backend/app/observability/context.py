"""Value objects threaded through the instrumented call sites.

LlmCallContext carries the identity of the call (who/what/where) and is built
by the caller that actually knows those facts — a Celery task knows its
job_id and market, a router knows the caller_type. TokenUsage carries the
counts extracted from a provider response. Both are frozen so a context can
be built once per row and safely reused across the primary call and its
retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.observability.constants import CALLER_PORTAL


@dataclass(frozen=True)
class LlmCallContext:
    """Attribution for one LLM call (spec §5 atomic fields).

    `caller_type` defaults to "portal" because that is the only bucket
    available for portal-driven work — no portal auth exists and none is
    being added (spec §3). Only the external API surface sets
    caller_type="external_api" with a real api_key_id.
    """

    task_type: str
    call_path: str
    caller_type: str = CALLER_PORTAL
    api_key_id: Optional[str] = None
    job_id: Optional[str] = None
    job_type: Optional[str] = None
    market: Optional[str] = None
    country: Optional[str] = None


@dataclass(frozen=True)
class TokenUsage:
    """Token counts for one call. Cache fields are captured opportunistically
    wherever a response exposes them and stay 0 otherwise (spec §3) — their
    presence is never assumed."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

"""Which claude-sandbox container a given match request should call.

Pure, dependency-free resolution so the choice isn't scattered across
runner.py/runner_intl_v2.py. This is about which CONTAINER to call (baked-in
MCP tools/model/settings differ) -- it is NOT a concurrency-isolation
mechanism. The sandbox_semaphore and celery-agentic-worker pool stay fully
shared across every market and version, by deliberate choice: informal
round-robin fairness across whatever jobs are active, the same as domestic/
intl-v1/external API already share today.
"""

from __future__ import annotations

from typing import Optional

from app.config import settings


def sandbox_url_for(market: str, *, variant: str) -> Optional[str]:
    """Return the sandbox base URL to call, or None to use the default
    (settings.CLAUDE_SANDBOX_URL) -- today's behavior, unchanged.

    Only international v2 traffic uses the dedicated intl sandbox by
    default. Intl v1 traffic can opt in via AGENTIC_INTL_V1_USE_INTL_SANDBOX
    (default False, so intl v1 is provably untouched by this project unless
    explicitly flipped later). Falls back to the default sandbox if
    CLAUDE_SANDBOX_URL_INTL isn't configured (e.g. the service isn't
    deployed yet), so this seam degrades gracefully rather than breaking.
    """
    if market != "international":
        return None
    if variant == "v2" or settings.AGENTIC_INTL_V1_USE_INTL_SANDBOX:
        return settings.CLAUDE_SANDBOX_URL_INTL or None
    return None

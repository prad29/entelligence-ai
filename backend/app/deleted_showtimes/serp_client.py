"""
Plain SerpApi client, ported from showtime_serp_check.py.

No caching — every call is live, so verdicts always reflect what Google is
publishing right now. Showtimes change through the day (Google drops shows as
they start), which makes a reused response worse than no response.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"

# Substrings SerpApi uses (in the JSON "error" field or the raw HTTP body) to
# report that a key's plan/credits are exhausted, as opposed to a genuinely
# invalid key or a short-lived rate limit. Matched case-insensitively.
_QUOTA_PHRASES = [
    "run out of searches",
    "ran out of searches",
    "out of searches",
    "exceeded your monthly",
    "no searches left",
    "insufficient credits",
    "account has run out",
]


def _is_quota_message(message: Optional[str]) -> bool:
    """True if `message` looks like a SerpApi quota/credit-exhaustion message
    rather than a genuinely invalid key or a transient rate limit."""
    if not message:
        return False
    lowered = message.lower()
    return any(phrase in lowered for phrase in _QUOTA_PHRASES)


class SerpError(RuntimeError):
    pass


class SerpAuthError(SerpError):
    """Raised on HTTP 401/403 — invalid key or no credits left."""


class SerpQuotaError(SerpError):
    """Raised when the key's plan/credits are exhausted — safe to rotate to another key."""


class SerpClient:
    def __init__(self, api_key: str, retries: int = 3, timeout: int = 45):
        self.api_key = api_key
        self.retries = retries
        self.timeout = timeout
        self.calls_made = 0

    def search(self, params: Dict[str, str]) -> Dict[str, Any]:
        q = dict(params, api_key=self.api_key, output="json", no_cache="true")
        url = SERPAPI_ENDPOINT + "?" + urllib.parse.urlencode(q)
        last_err: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "entelligence-deleted-showtimes/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.calls_made += 1
                if data.get("error"):
                    error_message = str(data["error"])
                    if _is_quota_message(error_message):
                        raise SerpQuotaError(error_message)
                    raise SerpError(error_message)
                return data
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                last_err = SerpError(f"HTTP {e.code}: {body}")
                if e.code in (401, 403):
                    if _is_quota_message(body):
                        raise SerpQuotaError(str(last_err)) from e
                    raise SerpAuthError(str(last_err)) from e
                if e.code == 429 and _is_quota_message(body):
                    # SerpApi reports quota exhaustion as a 429 with a
                    # quota-specific message — raise immediately, no retry
                    # sleep, so the caller can rotate to another key right away.
                    raise SerpQuotaError(str(last_err)) from e
                if e.code not in (429, 500, 502, 503, 504):
                    raise last_err
            except SerpQuotaError:
                # Raised in-band from a 200 response body above — propagate
                # immediately, no retry sleep, same as the 401/403/429 cases.
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(min(2 ** attempt, 20))
        raise SerpError(f"exhausted retries: {last_err}")

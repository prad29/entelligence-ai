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


class SerpError(RuntimeError):
    pass


class SerpAuthError(SerpError):
    """Raised on HTTP 401/403 — invalid key or no credits left."""


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
                    raise SerpError(str(data["error"]))
                return data
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "ignore")[:300]
                last_err = SerpError(f"HTTP {e.code}: {body}")
                if e.code in (401, 403):
                    raise SerpAuthError(str(last_err)) from e
                if e.code not in (429, 500, 502, 503, 504):
                    raise last_err
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(min(2 ** attempt, 20))
        raise SerpError(f"exhausted retries: {last_err}")

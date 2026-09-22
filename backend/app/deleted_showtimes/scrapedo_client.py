"""
Plain scrape.do client for the Deleted Showtimes site-verification step,
ported from the research script (scripts/site_verify_poc.py's ScrapeDoClient).

No caching of fetched pages — a theater's live showtimes listing changes
through the day, same rationale as serp_client.py's own no-caching rule.
Only the resolved *URL* for a theater is ever cached (see site_url_cache.py).
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

SCRAPEDO_FETCH_ENDPOINT = "https://api.scrape.do/"


class ScrapeDoError(RuntimeError):
    pass


class ScrapeDoAuthError(ScrapeDoError):
    """Raised on HTTP 401/403 — invalid or revoked token."""


class ScrapeDoQuotaError(ScrapeDoError):
    """Raised on HTTP 402 — token's credit balance is exhausted."""


@dataclass
class ScrapeResponse:
    status_code: int
    text: str
    final_url: str
    credits_used: Optional[int]
    remaining_credits: Optional[int]


def _int_or_none(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class ScrapeDoClient:
    """One token, one call = one attempt — no internal retry. Adapters that
    need to retry (e.g. AMC trying multiple candidate URLs) do so explicitly
    at the call site, so a client-level retry loop can't silently multiply
    scrape.do credit spend on top of an adapter's own retry loop."""

    def __init__(self, token: str, timeout: int = 90):
        self.token = token
        self.timeout = timeout
        self.calls_made = 0
        self.total_credits = 0

    def fetch(self, url: str, *, render: bool = True, super_proxy: bool = False,
              geo: Optional[str] = "us") -> ScrapeResponse:
        params = {"token": self.token, "url": url}
        if render:
            params["render"] = "true"
        if super_proxy:
            params["super"] = "true"
        if geo:
            params["geoCode"] = geo
        full = SCRAPEDO_FETCH_ENDPOINT + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(full, headers={"User-Agent": "entelligence-deleted-showtimes/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
                self.calls_made += 1
                cost = _int_or_none(resp.headers.get("Scrape.do-Request-Cost"))
                if cost:
                    self.total_credits += cost
                return ScrapeResponse(
                    status_code=resp.status,
                    text=body,
                    final_url=resp.geturl(),
                    credits_used=cost,
                    remaining_credits=_int_or_none(resp.headers.get("Scrape.do-Remaining-Credits")),
                )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:300]
            msg = f"HTTP {e.code} fetching {url}: {body}"
            if e.code in (401, 403):
                raise ScrapeDoAuthError(msg) from e
            if e.code == 402:
                raise ScrapeDoQuotaError(msg) from e
            raise ScrapeDoError(msg) from e
        except urllib.error.URLError as e:
            raise ScrapeDoError(f"network error fetching {url}: {e}") from e
        except (socket.timeout, TimeoutError, ConnectionError) as e:
            # Raised by resp.read() itself (after the connection is already
            # open) — urllib does NOT wrap this in URLError. Confirmed live
            # during research: left uncaught, this crashed an entire batch
            # run instead of being one retryable failed fetch attempt.
            raise ScrapeDoError(f"timeout reading response for {url}: {e}") from e

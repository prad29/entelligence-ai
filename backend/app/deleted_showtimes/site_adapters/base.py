"""
Shared adapter interface for the Deleted Showtimes site-verification step.

Every adapter is a plain function `fetch(theater, circuit, ctx) -> SiteListing`
— resolve URL(s) (using site_url_cache.py where applicable) -> fetch (via
ctx.scrapedo) -> extract -> return a SiteListing. No adapter ever raises for
a normal "couldn't confirm" outcome (bot-wall, unresolved URL, empty page) —
those are all just a SiteListing with an empty by_title and a `method` code
describing why, exactly like today's Google-only path treats a failed SerpApi
lookup as UNABLE_TO_DETERMINE rather than an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class SiteListing:
    """One theater's per-title showtime minutes as extracted from its own
    website for `ctx.today`, plus (AMC-only) an exact sellable/soldout status
    per (norm_title, minute).

    `method` is a raw, internal reason/source code (e.g. "AMC_NEXT_F_RSC",
    "URL_NOT_RESOLVED", "PAGE_FETCHED_NO_STRUCTURED_DATA") — never shown to a
    user directly; core.plain_site_reason() maps it to plain English for the
    report, while this raw value stays available in the SITE_EVIDENCE sheet.
    """

    url: Optional[str] = None
    by_title: Dict[str, List[int]] = field(default_factory=dict)
    status_map: Dict[Tuple[str, int], str] = field(default_factory=dict)
    method: str = "URL_NOT_RESOLVED_OR_FETCH_FAILED"

    @property
    def ok(self) -> bool:
        return bool(self.by_title)


@dataclass
class SiteCheckContext:
    """Everything an adapter needs, bundled once per batch task so adapters
    never import Celery/DB/Redis plumbing themselves."""

    scrapedo: Any  # RotatingScrapeDoClient — .fetch(url, **kwargs) -> ScrapeResponse
    serp_client: Any  # RotatingSerpClient — .search(params) -> dict, reused for AMC URL resolution
    serp_data: Optional[dict]  # raw SerpApi response already fetched for this theater's Google check
    today: date


AdapterFn = Callable[[str, str, SiteCheckContext], SiteListing]

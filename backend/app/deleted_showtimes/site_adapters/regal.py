"""
Regal adapter — ported from scripts/site_verify_poc.py's Regal logic
(validated live against real Regal theaters during research).

`regmovies.com/theatres/<slug>` (slug derived directly from the theater name)
embeds a `__NEXT_DATA__` blob with the exact per-performance schedule —
structured, exact, no interaction needed, the cheapest possible extraction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

from app.deleted_showtimes.normalize import norm_title
from app.deleted_showtimes.scrapedo_client import ScrapeDoError
from app.deleted_showtimes.site_adapters.base import SiteCheckContext, SiteListing

logger = logging.getLogger(__name__)

CIRCUIT_NAME = "Regal Entertainment Group"


def regal_url(theater: str) -> str:
    slug = theater.lower().replace("&", " ")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return f"https://www.regmovies.com/theatres/{slug}"


def _extract_performances(html: str) -> Dict[str, List[int]]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}

    def find_movie_blocks(obj: Any) -> List[Dict[str, Any]]:
        found = []
        if isinstance(obj, dict):
            if "Performances" in obj and "Title" in obj:
                found.append(obj)
            for v in obj.values():
                found += find_movie_blocks(v)
        elif isinstance(obj, list):
            for v in obj:
                found += find_movie_blocks(v)
        return found

    by_title: Dict[str, List[int]] = {}
    for block in find_movie_blocks(data):
        title = str(block.get("Title") or "")
        if not title:
            continue
        mins = []
        for perf in block.get("Performances", []):
            cal = str(perf.get("CalendarShowTime") or "")
            m2 = re.search(r"T(\d{2}):(\d{2}):\d{2}$", cal)
            if m2:
                mins.append(int(m2.group(1)) * 60 + int(m2.group(2)))
        if mins:
            by_title.setdefault(norm_title(title), []).extend(mins)
    return by_title


def fetch(theater: str, circuit: str, ctx: SiteCheckContext) -> SiteListing:
    url = regal_url(theater)
    try:
        resp = ctx.scrapedo.fetch(url, render=True, geo="us")
    except ScrapeDoError as exc:
        logger.warning("regal adapter: fetch failed theater=%r url=%s: %s", theater, url, exc)
        return SiteListing(url=url, method="PAGE_FETCHED_NO_STRUCTURED_DATA")
    if resp.status_code != 200:
        return SiteListing(url=url, method="PAGE_FETCHED_NO_STRUCTURED_DATA")
    by_title = _extract_performances(resp.text)
    if by_title:
        return SiteListing(url=url, by_title=by_title, method="REGAL_NEXT_DATA")
    return SiteListing(url=url, method="PAGE_FETCHED_NO_STRUCTURED_DATA")

"""
Branson's IMAX Entertainment Complex adapter — EasyTixs-backed. Everything
needed (title, every showtime, and even the site's own "already passed
today" behavior) is on the single /movies page — no second hop, no proxy
tier needed. Matched by exact theater name in registry.py, not by circuit
(Circuit Name is shared "IMAX" with Science North).
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from app.deleted_showtimes.normalize import norm_title, parse_time_to_min
from app.deleted_showtimes.scrapedo_client import ScrapeDoError
from app.deleted_showtimes.site_adapters.base import SiteCheckContext, SiteListing

logger = logging.getLogger(__name__)

THEATER_NAME = "Branson's IMAX Entertainment Complex"

MOVIES_URL = "https://bransonimax.com/movies"
_BLOCK_RE = re.compile(
    r'<h3>([^<]+?)(?:\s+in\s+[^<]+)?</h3>.*?<div class="showtimes-container">(.*?)</div>', re.S)
_TIME_RE = re.compile(r'>(\d{1,2}:\d{2}(?:AM|PM))</a>')


def _extract_listing(html: str) -> Dict[str, List[int]]:
    """The homepage carousel repeats each film's card (once as a featured
    slide, once in the full grid below), producing duplicate matches — a
    set() dedupes without affecting presence/absence correctness either way."""
    by_title: Dict[str, List[int]] = {}
    for title, block in _BLOCK_RE.findall(html):
        minutes = {m for t in _TIME_RE.findall(block) if (m := parse_time_to_min(t)) is not None}
        if minutes:
            key = norm_title(title.strip())
            by_title[key] = sorted(set(by_title.get(key, [])) | minutes)
    return by_title


def fetch(theater: str, circuit: str, ctx: SiteCheckContext) -> SiteListing:
    try:
        resp = ctx.scrapedo.fetch(MOVIES_URL, render=True, geo="us")
    except ScrapeDoError as exc:
        logger.warning("bransons_imax adapter: fetch failed: %s", exc)
        return SiteListing(url=MOVIES_URL, method="PAGE_FETCHED_NO_STRUCTURED_DATA")
    if resp.status_code != 200:
        return SiteListing(url=MOVIES_URL, method="PAGE_FETCHED_NO_STRUCTURED_DATA")
    by_title = _extract_listing(resp.text)
    if by_title:
        return SiteListing(url=MOVIES_URL, by_title=by_title, method="BRANSON_EASYTIXS_HTML")
    return SiteListing(url=MOVIES_URL, method="PAGE_FETCHED_NO_STRUCTURED_DATA")

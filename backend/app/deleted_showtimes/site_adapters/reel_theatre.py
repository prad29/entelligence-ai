"""
Reel Theatre adapter — plain server-rendered HTML, no bot-wall. The one real
gotcha (confirmed live during research): the page embeds a film's FULL
multi-day calendar in one response — later days are merely CSS-hidden
(style="display: none"), not JS-loaded — so an extractor that doesn't scope
to one exact date silently merges tomorrow's schedule into today's and
manufactures a false "confirmed" match.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from app.deleted_showtimes.normalize import norm_title, parse_time_to_min
from app.deleted_showtimes.scrapedo_client import ScrapeDoError
from app.deleted_showtimes.site_adapters.base import SiteCheckContext, SiteListing

logger = logging.getLogger(__name__)

CIRCUIT_NAME = "Reel Theatres"

REEL_HOMEPAGE = "https://www.reeltheatre.com"
_LOCATION_LINK_RE = re.compile(r'href="(/showtimes-([a-z]+))"')
_FILM_BLOCK_RE = re.compile(r'<div id="film-\d+" class="showtime"[^>]*>(.*?)(?=<div id="film-|\Z)', re.S)
_TITLE_RE = re.compile(r'<h3 class="title">([^<]+)</h3>')
_TIME_RE = re.compile(r'>\s*(\d{1,2}:\d{2}(?:am|pm))')


def _resolve_url(ctx: SiteCheckContext, theater: str) -> Optional[str]:
    """Reel Theatre's location slugs (e.g. "eagle" for "Reel Theatre - Eagle
    Luxe") don't derive from the theater name by any fixed rule, but the
    chain is small — fetch the homepage nav and match by substring rather
    than guessing a pattern."""
    try:
        resp = ctx.scrapedo.fetch(REEL_HOMEPAGE, render=True, geo="us")
    except ScrapeDoError as exc:
        logger.warning("reel_theatre adapter: homepage fetch failed: %s", exc)
        return None
    if resp.status_code != 200:
        return None
    theater_lower = theater.lower()
    for path, loc in _LOCATION_LINK_RE.findall(resp.text):
        if loc in theater_lower:
            return REEL_HOMEPAGE + path
    return None


def _extract_listing(html: str, target_date_yyyymmdd: str) -> Dict[str, List[int]]:
    by_title: Dict[str, List[int]] = {}
    marker = f'data-date="{target_date_yyyymmdd}"'
    for block in _FILM_BLOCK_RE.findall(html):
        title_m = _TITLE_RE.search(block)
        if not title_m:
            continue
        idx = block.find(marker)
        if idx == -1:
            continue
        next_idx = block.find('data-date="', idx + len(marker))
        segment = block[idx: next_idx if next_idx != -1 else None]
        minutes = [m for t in _TIME_RE.findall(segment) if (m := parse_time_to_min(t)) is not None]
        if minutes:
            by_title.setdefault(norm_title(title_m.group(1).strip()), []).extend(minutes)
    return by_title


def fetch(theater: str, circuit: str, ctx: SiteCheckContext) -> SiteListing:
    url = _resolve_url(ctx, theater)
    if not url:
        return SiteListing(url=None, method="URL_NOT_RESOLVED")
    try:
        resp = ctx.scrapedo.fetch(url, render=True, geo="us")
    except ScrapeDoError as exc:
        logger.warning("reel_theatre adapter: fetch failed theater=%r url=%s: %s", theater, url, exc)
        return SiteListing(url=url, method="PAGE_FETCHED_NO_STRUCTURED_DATA")
    if resp.status_code != 200:
        return SiteListing(url=url, method="PAGE_FETCHED_NO_STRUCTURED_DATA")
    by_title = _extract_listing(resp.text, ctx.today.strftime("%Y%m%d"))
    if by_title:
        return SiteListing(url=url, by_title=by_title, method="REEL_DATE_SCOPED_HTML")
    return SiteListing(url=url, method="PAGE_FETCHED_NO_STRUCTURED_DATA")

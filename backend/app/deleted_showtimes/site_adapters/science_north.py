"""
IMAX - Science North adapter — a different ticketing vendor entirely
(TN-style widget) than Branson's, needing a real two-hop crawl: the venue's
own sciencenorth.ca/imax listing links to one page per film, and each film's
page links to its own order.sciencenorth.ca ticketing product — no shortcut
found. That ticketing subdomain also needs the residential proxy tier to
load at all (confirmed: consistent 502s on plain render, works with
super=true). Deliberately uses sciencenorth.ca's own pages, not the
third-party imax.com aggregator. Matched by exact theater name in
registry.py, not by circuit (Circuit Name is shared "IMAX" with Branson's).
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from app.deleted_showtimes.normalize import norm_title, parse_time_to_min
from app.deleted_showtimes.scrapedo_client import ScrapeDoError
from app.deleted_showtimes.site_adapters.base import SiteCheckContext, SiteListing

logger = logging.getLogger(__name__)

THEATER_NAME = "IMAX - Science North"

IMAX_LISTING_URL = "https://www.sciencenorth.ca/imax"
_CARD_RE = re.compile(r'<h3>\s*<a href="(/imax/[a-z0-9-]+)" rel="bookmark">\s*<span>([^<]+)</span>', re.S)
_ORDER_LINK_RE = re.compile(r'href="(https://order\.sciencenorth\.ca/overview/\d+)"')
_PERF_RE = re.compile(
    r'tn-prod-list-item__perf-date">([^<]+)</span>\s*'
    r'<span class="tn-prod-list-item__perf-time">([^<]+)</span>.*?'
    r'tn-performance-title">([^<]+)</span>', re.S)


def _resolve_film_pages(ctx: SiteCheckContext) -> Dict[str, str]:
    """Returns norm_title -> ticketing overview URL, for every film currently
    listed on the venue's own /imax page."""
    try:
        resp = ctx.scrapedo.fetch(IMAX_LISTING_URL, render=True, geo="us")
    except ScrapeDoError as exc:
        logger.warning("science_north adapter: listing fetch failed: %s", exc)
        return {}
    if resp.status_code != 200:
        return {}
    out: Dict[str, str] = {}
    for path, title in _CARD_RE.findall(resp.text):
        try:
            film_resp = ctx.scrapedo.fetch("https://www.sciencenorth.ca" + path, render=True, geo="us")
        except ScrapeDoError as exc:
            logger.warning("science_north adapter: film page fetch failed path=%s: %s", path, exc)
            continue
        if film_resp.status_code != 200:
            continue
        order_m = _ORDER_LINK_RE.search(film_resp.text)
        if order_m:
            out[norm_title(title.strip())] = order_m.group(1)
    return out


def _extract_listing(ctx: SiteCheckContext, ticket_urls: Dict[str, str]) -> Dict[str, List[int]]:
    """Matches on TN's own date-label string verbatim (e.g. "September 22,
    2026") rather than re-parsing it, to avoid a second, possibly-
    inconsistent date-formatting implementation."""
    target_date_label = ctx.today.strftime("%B %-d, %Y")
    by_title: Dict[str, List[int]] = {}
    for norm, url in ticket_urls.items():
        try:
            resp = ctx.scrapedo.fetch(url, render=True, super_proxy=True, geo="us")
        except ScrapeDoError as exc:
            logger.warning("science_north adapter: ticketing fetch failed url=%s: %s", url, exc)
            continue
        if resp.status_code != 200:
            continue
        minutes = []
        for date_str, time_str, _title in _PERF_RE.findall(resp.text):
            if date_str.strip() != target_date_label:
                continue
            m = parse_time_to_min(time_str.strip())
            if m is not None:
                minutes.append(m)
        if minutes:
            by_title[norm] = minutes
    return by_title


def fetch(theater: str, circuit: str, ctx: SiteCheckContext) -> SiteListing:
    ticket_urls = _resolve_film_pages(ctx)
    if not ticket_urls:
        return SiteListing(url=IMAX_LISTING_URL, method="URL_NOT_RESOLVED")
    by_title = _extract_listing(ctx, ticket_urls)
    if by_title:
        return SiteListing(url=IMAX_LISTING_URL, by_title=by_title, method="SCIENCENORTH_TN_TICKETING")
    return SiteListing(url=IMAX_LISTING_URL, method="PAGE_FETCHED_NO_STRUCTURED_DATA")

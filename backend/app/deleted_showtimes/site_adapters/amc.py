"""
AMC adapter — ported from scripts/site_verify_batch.py's AMC logic (validated
live against real AMC theaters during research).

The `/movie-theatres/<market>/<slug>/showtimes` route embeds the full day's
schedule as Next.js RSC stream chunks (self.__next_f.push([1, "..."])) even
on a plain render — no interaction/playWithBrowser needed — including an
exact "Sellable"/"Soldout" status per performance. The market/slug is
resolved for free from SerpApi's organic_results[].link, then cached in
TheaterSiteUrl so most theaters never need a second SerpApi call at all.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from app.deleted_showtimes import site_url_cache
from app.deleted_showtimes.core import build_query
from app.deleted_showtimes.normalize import norm_title, parse_time_to_min
from app.deleted_showtimes.scrapedo_client import ScrapeDoError
from app.deleted_showtimes.site_adapters.base import SiteCheckContext, SiteListing

logger = logging.getLogger(__name__)

CIRCUIT_NAME = "AMC Entertainment Inc"

_AMC_LINK_RE = re.compile(
    r"https?://(?:www\.)?amctheatres\.com/movie-theatres/([a-z0-9-]+)/(amc-[a-z0-9-]+)(?:/|\?|$)",
    re.I,
)

# Words that don't distinguish one AMC theatre from another — stripped before
# comparing a candidate slug against the theater name actually being looked
# for. Screen-count digits are excluded too: "16"/"18" is shared by dozens of
# unrelated theatres and can't help confirm a match.
_AMC_NOISE_TOKENS = {"amc", "with", "and", "the", "dine", "in", "theatres",
                     "theatre", "cinemas", "cinema", "at", "of"}

_NEXT_F_PUSH_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)


def _amc_core_tokens(name: str) -> set:
    tokens = re.split(r"[\s-]+", name.lower())
    return {t for t in tokens if t and t not in _AMC_NOISE_TOKENS and not t.isdigit()}


def _amc_slug_matches_theater(theater: str, slug: str) -> bool:
    """Confirmed necessary, not paranoia: a live run resolved "AMC Southcenter
    16" to amctheatres.com's real, unrelated "AMC South Bay Galleria 16" page
    — a well-formed link, correctly shaped by every regex check, for a
    completely different theatre. Require every distinguishing word in the
    theater's name to appear in the candidate slug — not just some overlap."""
    theater_tokens = _amc_core_tokens(theater)
    if not theater_tokens:
        return False  # nothing distinguishing to check — refuse rather than guess
    slug_tokens = _amc_core_tokens(slug)
    return theater_tokens.issubset(slug_tokens)


def _resolve_candidates(serp_data: Optional[dict], theater: str) -> List[str]:
    """Every plausible AMC theatre URL in the response that actually matches
    `theater` by name, in rank order, deduplicated — a single SerpApi call
    sometimes carries more than one candidate (a legitimate link plus AMC's
    own "undefined"-market decoy, or the same theatre linked twice at
    different ranks)."""
    if not serp_data:
        return []
    seen: set = set()
    out: List[str] = []
    for r in serp_data.get("organic_results") or []:
        m = _AMC_LINK_RE.search(str(r.get("link") or ""))
        if not m or m.group(1).lower() in ("undefined", "null", "unknown"):
            continue
        if not _amc_slug_matches_theater(theater, m.group(2)):
            continue
        url = f"https://www.amctheatres.com/movie-theatres/{m.group(1)}/{m.group(2)}/showtimes"
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _extract_showtimes(html: str) -> Tuple[Dict[str, List[int]], Dict[Tuple[str, int], str]]:
    """Movie identity comes from the slug in `aria-describedby`
    ("resident-evil-79697 ..." -> "resident evil"), not an exact title field —
    an approximation, not guaranteed to match every title's normalised form."""
    decoded_chunks = []
    for raw in _NEXT_F_PUSH_RE.findall(html):
        try:
            arr = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if len(arr) > 1 and isinstance(arr[1], str):
            decoded_chunks.append(arr[1])
    full = "\n".join(decoded_chunks)

    by_title: Dict[str, List[int]] = {}
    status_map: Dict[Tuple[str, int], str] = {}
    for m in re.finditer(r'"showtime":\{', full):
        start = full.rfind("{", 0, m.start())
        if start == -1:
            continue
        depth, i = 0, start
        while i < len(full):
            if full[i] == "{":
                depth += 1
            elif full[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        try:
            obj = json.loads(full[start:i + 1])
        except json.JSONDecodeError:
            continue
        st = obj.get("showtime") or {}
        aria = str(obj.get("aria-describedby") or "")
        slug = aria.split(" ")[0] if aria else ""
        disp = st.get("display") or {}
        if not (slug and disp.get("time") and disp.get("amPm")):
            continue
        minutes = parse_time_to_min(f"{disp['time']} {disp['amPm']}")
        if minutes is None:
            continue
        title_guess = re.sub(r"-\d+$", "", slug).replace("-", " ")
        norm = norm_title(title_guess)
        by_title.setdefault(norm, []).append(minutes)
        status_map[(norm, minutes)] = str(st.get("status") or "")
    return by_title, status_map


def _resolve_candidates_with_retry(ctx: SiteCheckContext, theater: str) -> List[str]:
    """Two independent misses observed in practice, both worth retrying for:
    (1) Google/SerpApi's own ranking is non-deterministic call-to-call — the
        same "bare" query sometimes surfaces amctheatres.com and sometimes
        doesn't. (2) A handful of theater names are too generic for Google to
        disambiguate on their own with the "bare" query alone; appending
        "showtimes" ("plain" mode) reliably disambiguates these."""
    candidates = _resolve_candidates(ctx.serp_data, theater)
    if candidates:
        return candidates
    for mode in ("bare", "bare", "plain"):
        q = build_query(theater, mode)
        try:
            data = ctx.serp_client.search({"engine": "google", "q": q, "hl": "en", "gl": "us", "device": "desktop"})
        except Exception as exc:  # noqa: BLE001 — a resolution-retry failure must not abort the whole check
            logger.warning("amc adapter: SerpApi retry (%s) failed for theater=%r: %s", mode, theater, exc)
            continue
        candidates = _resolve_candidates(data, theater)
        if candidates:
            return candidates
    return []


def fetch(theater: str, circuit: str, ctx: SiteCheckContext) -> SiteListing:
    cached = site_url_cache.get_cached_url(theater)
    candidates = [cached] if cached else _resolve_candidates_with_retry(ctx, theater)
    if not candidates:
        return SiteListing(url=None, method="URL_NOT_RESOLVED")

    # AMC's own /showtimes route is occasionally flaky even with a correct,
    # previously-working URL — confirmed live: re-fetching a theater that
    # failed once returned full data on a later attempt with no code change.
    # Never treat one empty result as a confirmed absence.
    for url in candidates:
        for attempt in range(3):
            try:
                resp = ctx.scrapedo.fetch(url, render=True, geo="us")
            except ScrapeDoError as exc:
                logger.warning("amc adapter: fetch failed theater=%r url=%s attempt=%d: %s",
                                theater, url, attempt, exc)
                continue
            if resp.status_code != 200:
                continue
            by_title, status_map = _extract_showtimes(resp.text)
            if by_title:
                if not cached:
                    site_url_cache.save_resolved_url(theater, circuit or CIRCUIT_NAME, url, source="serpapi")
                return SiteListing(url=url, by_title=by_title, status_map=status_map, method="AMC_NEXT_F_RSC")
    return SiteListing(url=candidates[0], method="PAGE_FETCHED_NO_STRUCTURED_DATA")

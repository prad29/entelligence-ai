"""
Core matching/decision logic, ported from showtime_serp_check.py.

Pure functions operating on plain dicts/dataclasses — no DB, Celery, or S3
imports here, so this module stays unit-testable in isolation (mirrors the
batch_io.py convention used elsewhere in this codebase).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from app.deleted_showtimes.normalize import (
    fmt_min,
    norm_title,
    resolve_block_dates,
    screen_drift,
    theaters_match,
    titles_match,
)

TRUE_ = "TRUE"
FALSE_ = "FALSE"
UNKNOWN_ = "UNABLE_TO_DETERMINE"

RETRYABLE_MISSES = {"NO_SHOWTIMES_PANEL", "DATE_NOT_IN_PANEL", "THEATER_NOT_IN_PANEL", "PANEL_EMPTY"}


@dataclass
class Listing:
    """Everything published for one theater on one date."""
    ok: bool
    reason: str = ""
    query: str = ""
    theater_verified: bool = False
    kg_title: str = ""
    google_name: str = ""
    google_address: str = ""
    screen_note: str = ""
    day_label: str = ""
    day_date: str = ""
    truncated: bool = False
    coverage_from: Optional[int] = None
    by_title: Dict[str, List[Tuple[int, str, str]]] = field(default_factory=dict)
    titles_seen: List[str] = field(default_factory=list)
    total_times: int = 0


@dataclass
class ShowtimeRow:
    """One input row. `key` is an opaque row identifier the caller supplies
    (e.g. the original row index in the uploaded file) — this module never
    interprets it, only echoes it back in the result."""
    key: Any
    theater: str
    title: str
    show_date: Optional[date]
    show_time_raw: str
    show_min: Optional[int]
    verdict: str = UNKNOWN_
    reason: str = ""
    published: str = ""
    nearest: str = ""
    source_query: str = ""
    theater_verified: bool = False
    google_theater: str = ""
    google_address: str = ""


def _iter_showtime_blocks(data: Dict[str, Any]):
    blocks = data.get("showtimes")
    if isinstance(blocks, list):
        yield from blocks
    ab = data.get("answer_box") or {}
    if isinstance(ab.get("showtimes"), list):
        yield from ab["showtimes"]


def _collect_showing(container: Dict[str, Any]) -> List[Tuple[int, str, str]]:
    from app.deleted_showtimes.normalize import parse_time_to_min

    out: List[Tuple[int, str, str]] = []
    for show in container.get("showing") or []:
        ftype = str(show.get("type") or "").strip()
        times = show.get("time")
        if isinstance(times, str):
            times = [times]
        for t in times or []:
            mins = parse_time_to_min(t)
            if mins is not None:
                out.append((mins, str(t), ftype))
    return out


def parse_theater_listing(data: Dict[str, Any], theater_name: str, target: date,
                           today: date, aliases: Dict[str, str],
                           require_theater_verify: bool,
                           strict_screens: bool = False) -> Listing:
    """Turn a SerpApi google response into a per-title time index for one date."""
    lst = Listing(ok=False, query=str((data.get("search_parameters") or {}).get("q") or ""))

    kg = data.get("knowledge_graph") or {}
    kg_title = str(kg.get("title") or "")
    lst.kg_title = kg_title
    if kg_title:
        lst.google_name = kg_title
        lst.google_address = str(kg.get("address") or kg.get("located_in") or "")
        if theaters_match(theater_name, kg_title, aliases, strict_screens):
            lst.theater_verified = True
            drift = screen_drift(theater_name, kg_title, aliases)
            if drift:
                lst.screen_note = f"SCREEN_COUNT_DRIFT {drift}"

    blocks = list(_iter_showtime_blocks(data))
    if not blocks:
        lst.reason = "NO_SHOWTIMES_PANEL"
        return lst

    block_dates = resolve_block_dates(blocks, target, today)
    idx = next((i for i, d in enumerate(block_dates) if d == target), None)
    if idx is None:
        lst.reason = "DATE_NOT_IN_PANEL"
        lst.day_label = ",".join(
            f"{b.get('day') or '?'}={d or '?'}" for b, d in zip(blocks, block_dates))[:160]
        return lst
    day_block = blocks[idx]
    lst.day_label = str(day_block.get("day") or "")
    lst.day_date = str(block_dates[idx])

    def _floor(b: Dict[str, Any]) -> Optional[int]:
        mins = []
        for cont in (b.get("movies") or []) + (b.get("theaters") or []):
            mins += [t[0] for t in _collect_showing(cont)]
        return min(mins) if mins else None

    if idx == 0 and len(blocks) > 1:
        f_today, f_next = _floor(day_block), _floor(blocks[1])
        if f_today is not None and f_next is not None and f_today - f_next >= 60:
            lst.truncated = True
            lst.coverage_from = f_today

    movies = day_block.get("movies")
    if isinstance(movies, list) and movies:
        for mv in movies:
            name = str(mv.get("name") or "").strip()
            if not name:
                continue
            times = _collect_showing(mv)
            if not times:
                continue
            lst.titles_seen.append(name)
            lst.by_title.setdefault(norm_title(name), []).extend(times)
            lst.total_times += len(times)
    else:
        theaters = day_block.get("theaters") or []
        matched = [t for t in theaters
                   if theaters_match(theater_name, str(t.get("name") or ""), aliases, strict_screens)]
        if not matched:
            lst.reason = "THEATER_NOT_IN_PANEL"
            return lst
        lst.theater_verified = True
        lst.google_name = str(matched[0].get("name") or "")
        lst.google_address = str(matched[0].get("address") or "")
        drift = screen_drift(theater_name, lst.google_name, aliases)
        if drift:
            lst.screen_note = f"SCREEN_COUNT_DRIFT {drift}"
        title_hint = str(((data.get("search_parameters") or {}).get("q") or ""))
        for th in matched:
            times = _collect_showing(th)
            if not times:
                continue
            name = str(th.get("movie") or th.get("title") or title_hint)
            lst.titles_seen.append(name)
            lst.by_title.setdefault(norm_title(name), []).extend(times)
            lst.total_times += len(times)

    if lst.total_times == 0:
        lst.reason = "PANEL_EMPTY"
        return lst
    if require_theater_verify and not lst.theater_verified:
        lst.reason = "THEATER_UNVERIFIED"
        return lst
    lst.ok = True
    return lst


def decide_rows(batch: List[ShowtimeRow], lst: Listing, title_missing_is_deleted: bool) -> None:
    for r in batch:
        r.source_query = lst.query
        r.theater_verified = lst.theater_verified
        r.google_theater = lst.google_name + ((" [" + lst.screen_note + "]") if lst.screen_note else "")
        r.google_address = lst.google_address

        if not lst.ok:
            r.verdict = UNKNOWN_
            r.reason = lst.reason or "NO_USABLE_LISTING"
            continue
        if r.show_min is None:
            r.verdict = UNKNOWN_
            r.reason = "UNPARSEABLE_SHOW_TIME"
            continue

        if lst.truncated and lst.coverage_from is not None and r.show_min < lst.coverage_from:
            r.verdict = UNKNOWN_
            r.reason = (f"SHOWTIME_ALREADY_PASSED — Google lists only remaining shows "
                        f"for today (from {fmt_min(lst.coverage_from)}); "
                        f"{r.show_time_raw} cannot be verified")
            continue

        entries = lst.by_title.get(norm_title(r.title))
        if entries is None:
            for k, v in lst.by_title.items():
                if titles_match(r.title, k):
                    entries = v
                    break
        if not entries:
            r.published = "; ".join(sorted(set(lst.titles_seen)))[:400]
            if title_missing_is_deleted:
                r.verdict = TRUE_
                r.reason = "TITLE_NOT_LISTED_AT_THEATER"
            else:
                r.verdict = UNKNOWN_
                r.reason = "TITLE_NOT_LISTED_AT_THEATER"
            continue

        times_sorted = sorted(set(entries))
        r.published = "; ".join(f"{fmt_min(m)}{('[' + f + ']') if f else ''}"
                                 for m, _, f in times_sorted)[:900]
        exact = [(m, raw, f) for m, raw, f in entries if m == r.show_min]
        nearest = min(entries, key=lambda e: abs(e[0] - r.show_min))
        r.nearest = f"{fmt_min(nearest[0])} ({nearest[0] - r.show_min:+d} min)"

        if exact:
            r.verdict = FALSE_
            fmts = ", ".join(sorted({f for _, _, f in exact if f})) or "n/a"
            r.reason = f"EXACT_MATCH ({fmt_min(r.show_min)} published as {fmts})"
        else:
            r.verdict = TRUE_
            r.reason = f"NO_EXACT_MATCH (nearest {r.nearest})"
            if not lst.theater_verified:
                r.reason += " [theater identity unverified]"


_MARKETING_TAIL = re.compile(
    r"\s+(?:with\s+.*$|&\s*(?:mx4d|screenx|imax|rpx|xd|4dx|grand screen|screenplay!?).*$)",
    re.I)


def short_theater_name(name: str) -> str:
    """
    Long marketing suffixes can suppress Google's showtimes panel even though
    Google knows the venue perfectly well. Trimming to the venue + screen
    count brings the panel back.
    """
    out = _MARKETING_TAIL.sub("", name).strip(" ,-&")
    out = re.sub(r",\s*$", "", out).strip()
    return out if out and out.lower() != name.lower() else ""


def build_query(theater: str, mode: str = "bare") -> str:
    """
    'bare' reproduces exactly what you'd type in a browser: just the theatre
    name. Quoting is an exact-phrase filter that can suppress the whole panel
    on any naming difference; adding 'showtimes' can shift the SERP and cost
    the panel its explicit date labels.
    """
    if mode == "quoted":
        return f'"{theater}" showtimes'
    if mode == "plain":
        return f"{theater} showtimes"
    return theater


def preflight_late_rows(rows: List[ShowtimeRow], now_et: datetime) -> int:
    """
    Google's today-panel drops showtimes that have already started, so a row
    can only be verified while its show is still in the future (US Eastern is
    the earliest US zone). Returns the count of rows already "at risk".
    """
    today_et = now_et.date()
    at_risk = 0
    for r in rows:
        if r.show_min is None or r.show_date is None:
            continue
        if r.show_date < today_et:
            at_risk += 1
        elif r.show_date == today_et and r.show_min <= now_et.hour * 60 + now_et.minute:
            at_risk += 1
    return at_risk

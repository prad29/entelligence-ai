"""
Deterministic normalisation helpers, ported from showtime_serp_check.py.

Design rules from the original script (do not loosen without a product
decision — see docs/plans for the Deleted Showtimes Check design doc):
  * EXACT-MINUTE MATCHING ONLY. 10:45 vs 10:50 is not a match.
  * NO FUZZY NAME MATCHING. Deterministic normalisation only, no aliases in v1.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Decorations Google/theatre chains bolt onto a title that do NOT change the film.
TITLE_DECORATIONS = [
    r"the imax experience", r"imax 3d", r"imax", r"dolby cinema", r"dolby atmos",
    r"screenx", r"4dx", r"rpx", r"xd", r"prime", r"big d", r"bigd", r"superscreen",
    r"3d", r"2d", r"open caption(?:ed)?", r"closed caption(?:ed)?", r"captioned",
    r"subtitled", r"sub(?:bed)?", r"dubbed", r"english dubbed", r"spanish dubbed",
    r"early access", r"fan event", r"sensory friendly", r"reserved seating",
    r"re-?release", r"anniversary edition", r"extended edition", r"special edition",
    r"in 70mm", r"70mm", r"35mm", r"laser at amc", r"laser",
]
_DECOR_RE = re.compile(r"\b(?:%s)\b" % "|".join(TITLE_DECORATIONS))
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def _squash(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("&", " and ")
    s = re.sub(r"[‘’“”]", "'", s)
    s = re.sub(r"[^a-z0-9']+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_title(title: str) -> str:
    """Normalise a film title for comparison. Keeps sequel numbers intact."""
    s = _squash(title)
    s = re.sub(r"\((?:[^)]*)\)", " ", s)          # already stripped by _squash, kept for safety
    s = _YEAR_RE.sub(" ", s)                      # 'Moana (2026)' -> 'moana'
    s = _DECOR_RE.sub(" ", s)                     # format decorations
    s = re.sub(r"\bthe\b", " ", s)                # leading/among 'the'
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _sequel_token(norm: str) -> Optional[int]:
    """Trailing sequel number, arabic or roman. 'moana 2' -> 2, 'rocky iii' -> 3."""
    if not norm:
        return None
    last = norm.split()[-1]
    if last.isdigit():
        return int(last)
    return _ROMAN.get(last)


def titles_match(a: str, b: str) -> bool:
    """Exact match after normalisation, with a hard sequel-number guard."""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    if _sequel_token(na) != _sequel_token(nb):
        return False                              # 'moana' vs 'moana 2' -> never
    return na == nb


_THEATER_NOISE = [
    r"^amc classic\b", r"^amc dine in\b", r"^amc dine-in\b", r"^amc\b", r"^regal\b",
    r"^cinemark\b", r"^marcus\b", r"^b and b theatres\b", r"^b and b\b",
    r"^alamo drafthouse cinema\b", r"^alamo drafthouse\b", r"^harkins\b",
    r"^cinepolis\b", r"^showcase\b", r"^mjr\b", r"^look cinemas\b", r"^paragon\b",
    r"^movie tavern\b", r"^studio movie grill\b", r"^emagine\b",
]
_THEATER_TAIL = [
    r"\bwith grand screen.*$", r"\bscreenplay\b.*$", r"\bmx4d\b", r"\band xd\b",
    r"\bxd\b", r"\brpx\b", r"\bimax\b", r"\bscreenx\b", r"\b4dx\b", r"\bdolby\b",
    r"\bdigital cinema\b", r"\bcinemas?\b", r"\btheatres?\b", r"\btheaters?\b",
    r"\bmovies?\b", r"\bstadium\b", r"\bua\b", r"\bthe\b", r"\band\b",
]


def norm_theater(name: str) -> Tuple[str, Optional[int]]:
    """Return (core name, screen-count) — screen count is compared separately."""
    s = _squash(name)
    s = s.replace("-", " ")
    for pat in _THEATER_NOISE:
        s = re.sub(pat, " ", s, count=1)
    for pat in _THEATER_TAIL:
        s = re.sub(pat, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    screens = None
    m = re.search(r"\b(\d{1,2})\b\s*$", s)
    if m:
        screens = int(m.group(1))
        s = s[: m.start()].strip()
    s = re.sub(r"\b\d{1,2}\b", " ", s)             # any other stray screen counts
    s = re.sub(r"\s+", " ", s).strip()
    return s, screens


def theaters_match(a: str, b: str, aliases: Optional[Dict[str, str]] = None,
                    strict_screens: bool = False) -> bool:
    """
    Deterministic theatre identity. Core name tokens must be IDENTICAL.
    Screen counts drift (renumbering) and are advisory unless strict_screens=True.
    """
    aliases = aliases or {}
    a = aliases.get(a, a)
    b = aliases.get(b, b)
    ca, sa = norm_theater(a)
    cb, sb = norm_theater(b)
    if not ca or not cb:
        return False
    if ca != cb:
        return False
    if strict_screens and sa is not None and sb is not None and sa != sb:
        return False
    return True


def screen_drift(a: str, b: str, aliases: Optional[Dict[str, str]] = None) -> str:
    """'14 -> 16' when the two names differ only by screen count, else ''."""
    aliases = aliases or {}
    _, sa = norm_theater(aliases.get(a, a))
    _, sb = norm_theater(aliases.get(b, b))
    if sa is not None and sb is not None and sa != sb:
        return f"{sa} -> {sb}"
    return ""


_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.]?(\d{2})?\s*([ap])\.?m\.?\s*$", re.I)


def parse_time_to_min(value: Any) -> Optional[int]:
    """'10:45 PM' / '10:45pm' / '9pm' / datetime.time -> minutes after midnight."""
    if value is None:
        return None
    if hasattr(value, "hour") and hasattr(value, "minute"):
        return value.hour * 60 + value.minute
    s = str(value).strip()
    m = _TIME_RE.match(s)
    if m:
        h = int(m.group(1)) % 12
        mi = int(m.group(2) or 0)
        if m.group(3).lower() == "p":
            h += 12
        return h * 60 + mi
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", s)   # 24h fallback
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


def fmt_min(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    ampm = "AM" if h < 12 else "PM"
    hh = h % 12 or 12
    return f"{hh}:{m:02d} {ampm}"


def parse_show_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_block_date(raw: str, target: date) -> Optional[date]:
    """'Aug 6' / '2026-08-06' -> date. Year is chosen as the one nearest the target."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^([A-Za-z]{3,4})\.?\s+(\d{1,2})$", raw)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    mon, dom = MONTHS[m.group(1).lower()], int(m.group(2))
    best = None
    for year in (target.year - 1, target.year, target.year + 1):
        try:
            cand = date(year, mon, dom)
        except ValueError:
            continue
        if best is None or abs((cand - target).days) < abs((best - target).days):
            best = cand
    return best


def resolve_block_dates(blocks: List[Dict[str, Any]], target: date,
                         today: date) -> List[Optional[date]]:
    """
    Map every day-block to a real date. Google labels the first two blocks
    "Today"/"Tomorrow" with no date field, and the rest "Sat"/"Aug 8". Blocks
    are consecutive days, so one explicitly dated block anchors the whole
    sequence — independent of the machine's clock/timezone. Falling back to
    the local date is a last resort.
    """
    out: List[Optional[date]] = [_parse_block_date(str(b.get("date") or ""), target)
                                  for b in blocks]
    anchor = next((i for i, d in enumerate(out) if d is not None), None)
    if anchor is not None:
        base = out[anchor]
        return [base + timedelta(days=i - anchor) for i in range(len(blocks))]
    for i, b in enumerate(blocks):
        day = str(b.get("day") or "").strip().lower()
        if day == "today":
            out[i] = today
        elif day == "tomorrow":
            out[i] = today + timedelta(days=1)
    anchor = next((i for i, d in enumerate(out) if d is not None), None)
    if anchor is not None:
        base = out[anchor]
        return [base + timedelta(days=i - anchor) for i in range(len(blocks))]
    return out

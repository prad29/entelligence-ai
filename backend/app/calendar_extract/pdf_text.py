"""Column-aware text extraction for multi-column release-calendar PDFs.

`pdfplumber`'s default `extract_text()` (with or without `layout=True`)
reads a page left-to-right by y-position, which interleaves side-by-side
table columns into nonsense on documents like this one (observed directly:
a 3-column "Domestic Release Calendar" comes out with column 1, 2 and 3
scrambled together line-by-line). Movie release calendars are consistently
laid out as N side-by-side date-sorted columns, so column boundaries are
detected from the x-position of the `M/D` date tokens themselves (the one
token guaranteed to start flush-left in every column), then each column is
extracted independently via `within_bbox` (which — unlike `crop` — drops
words that only partially overlap the box instead of splitting a character
in half at the boundary) and concatenated in reading order.

Falls back to plain `extract_text()` when a page doesn't yield a clean
N-way clustering of date tokens (e.g. a sparse page with too few dated
rows, or a genuinely single-column layout) — correctness degrades to
"whatever plain extraction gives you" rather than raising.
"""

from __future__ import annotations

import re

_DATE_TOKEN_RE = re.compile(r"^\d{1,2}/\d{1,2}$")
_MIN_CLUSTER_GAP = 25.0
_BOUNDARY_MARGIN = 8.0
_CANDIDATE_COLUMN_COUNTS = (3, 2)


def _cluster_x0(values: list[float], min_gap: float) -> list[list[float]]:
    clusters: list[list[float]] = [[values[0]]]
    for x in values[1:]:
        if x - clusters[-1][-1] <= min_gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return clusters


def _detect_column_bounds(page, n_cols: int) -> list[float] | None:
    words = page.extract_words()
    anchor_x0 = sorted(w["x0"] for w in words if _DATE_TOKEN_RE.match(w["text"]))
    if not anchor_x0:
        return None
    clusters = _cluster_x0(anchor_x0, _MIN_CLUSTER_GAP)
    if len(clusters) != n_cols:
        return None
    centers = [sum(c) / len(c) for c in clusters]
    return [0.0] + [c - _BOUNDARY_MARGIN for c in centers[1:]] + [page.width]


def _extract_page_text(page) -> str:
    for n_cols in _CANDIDATE_COLUMN_COUNTS:
        bounds = _detect_column_bounds(page, n_cols)
        if bounds is not None:
            columns = [
                page.within_bbox((bounds[i], 0, bounds[i + 1], page.height)).extract_text() or ""
                for i in range(len(bounds) - 1)
            ]
            return "\n".join(columns)
    return page.extract_text() or ""


def extract_calendar_text(pdf_bytes: bytes) -> str:
    """Return the calendar's text, one page's columns concatenated left-to-right,
    pages concatenated in order."""
    import pdfplumber

    with pdfplumber.open(__import__("io").BytesIO(pdf_bytes)) as pdf:
        return "\n".join(_extract_page_text(page) for page in pdf.pages)

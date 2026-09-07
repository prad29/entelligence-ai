"""Row extraction: calendar text -> validated list of row dicts.

One Converse call per chunk (see `_chunk_text`), each chunk holding as many
whole "MONTH - YEAR" sections as fit under `_TARGET_CHUNK_CHARS` — sized to
match scripts/calendar_eval.py's harness-validated single-call shape (the
harness fed one page, ~5-6k chars, in one call at 89% full-row accuracy). A
calendar spanning many months (like the multi-year sample) is therefore
split across a handful of calls rather than one call per month, keeping each
chunk's cross-references (e.g. the un-truncated "Studio Ghibli Fest" form
needed to fix truncated instances elsewhere) as complete as practical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.calendar_extract.prompt import (
    DAY_TAGS,
    FIELD_ORDER,
    PROMPT_ONLY_SUFFIX,
    SCHEMA,
    SYSTEM_PROMPT,
    USER_TEXT_PREFIX,
)

_TARGET_CHUNK_CHARS = 6000
_SECTION_RE = re.compile(
    r"(?m)^\s*(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|"
    r"AUG(?:UST)?|SEP(?:TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)\s*-\s*\d{4}\s*$"
)


def _chunk_text(text: str, target_chars: int = _TARGET_CHUNK_CHARS) -> list[str]:
    starts = [m.start() for m in _SECTION_RE.finditer(text)]
    if not starts:
        return [text]
    sections = [text[starts[i]:starts[i + 1] if i + 1 < len(starts) else len(text)]
                for i in range(len(starts))]
    chunks: list[str] = []
    current = ""
    for section in sections:
        if current and len(current) + len(section) > target_chars:
            chunks.append(current)
            current = section
        else:
            current += section
    if current:
        chunks.append(current)
    return chunks


def _validate_rows(rows: list) -> list[str]:
    errs = []
    if not isinstance(rows, list):
        return ["'rows' is not a list"]
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errs.append(f"row {i}: not an object")
            continue
        for k in FIELD_ORDER:
            if k not in row:
                errs.append(f"row {i}: missing key '{k}'")
        tag = row.get("release_day_tag")
        if tag is not None and tag not in DAY_TAGS:
            errs.append(f"row {i}: release_day_tag {tag!r} not one of {DAY_TAGS}")
    return errs


@dataclass
class ExtractionOutcome:
    rows: list[dict] = field(default_factory=list)
    chunks_total: int = 0
    chunks_failed: int = 0
    errors: list[str] = field(default_factory=list)


def extract_calendar_rows(text: str, job_id: str | None = None) -> ExtractionOutcome:
    from app.calendar_extract.bedrock import call_converse_json
    from app.observability.constants import TASK_CALENDAR_EXTRACT

    outcome = ExtractionOutcome()
    chunks = _chunk_text(text)
    outcome.chunks_total = len(chunks)

    for i, chunk in enumerate(chunks):
        try:
            rec, _usage = call_converse_json(
                system_prompt=SYSTEM_PROMPT,
                prompt_only_suffix=PROMPT_ONLY_SUFFIX,
                schema=SCHEMA,
                user_text=USER_TEXT_PREFIX + chunk,
                tool_name="emit_calendar_rows",
                task_type=TASK_CALENDAR_EXTRACT,
                job_id=job_id,
                # 6000-char chunks can hold 100+ dense entries in the later,
                # terser years of a multi-year calendar; 8000 truncated mid-
                # JSON on a real run (confirmed: that chunk needed 8133).
                max_tokens=16000,
            )
        except Exception as exc:  # noqa: BLE001
            outcome.chunks_failed += 1
            outcome.errors.append(f"chunk {i}: {exc}")
            continue

        rows = rec.get("rows") or []
        errs = _validate_rows(rows)
        if errs:
            outcome.chunks_failed += 1
            outcome.errors.append(f"chunk {i}: " + "; ".join(errs[:5]))
            continue
        outcome.rows.extend(rows)

    return outcome

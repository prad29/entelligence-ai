"""Two-stage "is this a release calendar" gate.

Stage A is a free, deterministic heuristic on the extracted text: no
document-classification precedent exists elsewhere in this codebase to
reuse (the repo's only prior art, title_matching's agentic prompt_builder,
classifies-and-routes-to-review rather than accept/reject — not applicable
here). Stage B (one LLM call) only runs when Stage A is ambiguous, since a
clear yes/no almost never needs it and every call costs real money.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADER_RE = re.compile(r"release\s+calendar", re.IGNORECASE)
_SECTION_RE = re.compile(
    r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s*-\s*\d{4}\b", re.IGNORECASE
)
_DATE_LINE_RE = re.compile(r"\b\d{1,2}/\d{1,2}\b")

_MIN_SECTIONS = 1
_MIN_DATE_LINES = 3


@dataclass
class ClassifyResult:
    is_release_calendar: bool
    reasoning: str
    ambiguous: bool = False


def classify_stage_a(text: str) -> ClassifyResult:
    """Deterministic pass. `ambiguous=True` means the caller should fall
    through to an LLM call (stage B) rather than trusting this verdict."""
    if not text or not text.strip():
        return ClassifyResult(False, "No extractable text (empty or scanned-image PDF).")

    has_header = bool(_HEADER_RE.search(text))
    n_sections = len(_SECTION_RE.findall(text))
    n_dates = len(_DATE_LINE_RE.findall(text))

    if has_header and n_sections >= _MIN_SECTIONS and n_dates >= _MIN_DATE_LINES:
        return ClassifyResult(
            True,
            f"Found a 'release calendar' header, {n_sections} month-year section(s), "
            f"and {n_dates} dated line(s).",
        )
    if not has_header and n_sections == 0 and n_dates == 0:
        return ClassifyResult(
            False, "No 'release calendar' header, month-year sections, or dated lines found."
        )
    return ClassifyResult(
        False,
        f"Ambiguous signal: header={has_header}, sections={n_sections}, dated_lines={n_dates}.",
        ambiguous=True,
    )


def classify_stage_b(text: str, job_id: str | None = None) -> ClassifyResult:
    """One LLM call for the ambiguous case."""
    from app.calendar_extract.bedrock import call_converse_json
    from app.calendar_extract.prompt import (
        CLASSIFY_PROMPT_ONLY_SUFFIX,
        CLASSIFY_SCHEMA,
        CLASSIFY_SYSTEM_PROMPT,
    )
    from app.observability.constants import TASK_CALENDAR_EXTRACT

    rec, _usage = call_converse_json(
        system_prompt=CLASSIFY_SYSTEM_PROMPT,
        prompt_only_suffix=CLASSIFY_PROMPT_ONLY_SUFFIX,
        schema=CLASSIFY_SCHEMA,
        user_text="Is this document a release calendar?\n\n" + text[:8000],
        tool_name="emit_classification",
        task_type=TASK_CALENDAR_EXTRACT,
        job_id=job_id,
    )
    return ClassifyResult(
        bool(rec.get("is_release_calendar")),
        str(rec.get("reasoning") or "No reasoning returned."),
    )


def classify(text: str, job_id: str | None = None) -> ClassifyResult:
    result = classify_stage_a(text)
    if result.ambiguous:
        return classify_stage_b(text, job_id)
    return result

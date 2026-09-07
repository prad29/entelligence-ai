"""Extraction schema + prompt for Competitive Calendar Extraction.

Validated by scripts/calendar_eval.py's 6-model harness against a 56-row
hand-labeled golden set (see out/calendar_eval/summary.md) — this is the
same prompt/schema that harness scored, kept here verbatim rather than
imported from the standalone script (mmvision.py's prompt/lobby_check's
prompt.py have the same split: the eval script is throwaway/self-contained,
this module is the versioned production copy).
"""

from __future__ import annotations

from typing import Any

DAY_TAGS = ["", "W", "TH", "TU", "M", "SA", "SU"]

ROW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "movie_title": {"type": "string"},
        "release_date": {"type": "string", "description": "YYYY-MM-DD"},
        "studio": {"type": "string"},
        "rating": {"type": "string"},
        "release_day_tag": {"type": "string", "enum": DAY_TAGS},
        "release_day_date": {"type": "string", "description": "YYYY-MM-DD, or empty string"},
        "miscellaneous": {"type": "string"},
    },
    "required": ["movie_title", "release_date", "studio", "rating",
                 "release_day_tag", "release_day_date", "miscellaneous"],
    "additionalProperties": False,
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"rows": {"type": "array", "items": ROW_SCHEMA}},
    "required": ["rows"],
    "additionalProperties": False,
}

FIELD_ORDER = ROW_SCHEMA["required"]

SYSTEM_PROMPT = """\
You extract every movie-release row from a studio "domestic release calendar" \
document into structured records. The text was extracted from a multi-column \
PDF table; minor OCR-style artifacts (a stray leading fragment, a word split \
across a line break, a dropped punctuation mark at a column edge) are expected \
— use context to read through them rather than reproducing them literally.

## Document structure

The text is organized into "MONTH - YEAR" section headers, each followed by \
"M/D" date lines (e.g. "7/3"), each followed by one or more movie entries \
belonging to that date. Every date is a Friday, the calendar's default \
release day. Entries continue until the next "M/D" line or section header.

## Per-entry shape

TITLE [STUDIO] - RATING (extra parenthetical info)

Studio bracket and rating are each optional — many entries have neither, \
especially "UNTITLED ..." placeholders.

## release_date

The full date (YYYY-MM-DD) of the "M/D" line the entry belongs to, resolved \
against the enclosing "MONTH - YEAR" header for the year.

## studio

The bracketed code exactly as printed, including multi-distributor forms \
like "SONY/CRUNC" and codes containing a space like "ROW K". Empty string if \
no bracket is present. Never expand to a full studio name.

## rating

Normalized to standard MPAA form (e.g. "PG-13", not "PG13"); if the rating \
was split across a line break (e.g. "PG-" then "13" on the next fragment), \
rejoin it. Empty string if no rating is printed — never invent one.

## release_day_tag / release_day_date — the day-of-week release exception

A parenthetical tag of the form "(XX-MM/DD)" (the dash is sometimes missing, \
e.g. "(M07/20)") marks that this title's ACTUAL release day is not the \
row's default Friday. The two-letter code is a day-of-week abbreviation: \
  W = Wednesday   TH = Thursday   TU = Tuesday
  M = Monday      SA = Saturday   SU = Sunday
Put the bare code in release_day_tag and the full resolved date (YYYY-MM-DD) \
in release_day_date. If no such tag is present, both are empty strings.

Do NOT confuse this with a date embedded inside an "UNTITLED" placeholder \
title for disambiguation, e.g. "UNTITLED A24 (9/18/26)" — that date is part \
of movie_title, not a release_day_tag/release_day_date pair, even though it \
looks similar. The giveaway: a release_day_tag always has a leading letter \
code; an embedded disambiguation date never does, and sits directly after \
the title text rather than after the studio/rating.

## movie_title — reconstruct line-wrap truncations

Some titles are cut short by the PDF's column width and continue as a \
recognizable franchise/series suffix, most commonly the Studio Ghibli Fest \
series: "- STUDIO", "- S", "- STU" appearing right before "[FTHM]" are all \
truncations of "- Studio Ghibli Fest" — confirm this by finding the fuller \
form written out elsewhere in the same document (e.g. "MY NEIGHBOR TOTORO - \
STUDIO GHIBLI FEST [FTHM]") and reconstruct every truncated instance the \
same way. The same applies to other truncated words wherever the intended \
word is unambiguous from context (e.g. "35TH ANNIVERS" -> "35th Anniversary", \
"20T" at the end of a title before a studio bracket -> "20th Anniversary", \
"ROTHER STOR" -> "Rother Story"). A year or event label already fully inside \
the title's own parentheses, e.g. "CARS (20TH ANNIVERSARY)" or "PASSION OF \
THE CHRIST, THE (2026 EVENT)", is part of the title as printed — do not move \
it anywhere else.

Render movie_title in title case (e.g. "Minions & Monsters"), preserving the \
document's own internal punctuation and ampersands.

## miscellaneous — everything else, concatenated

Rollout-stage markers ("(2)", "((3) WIDE)", "(LTD)", "(NY/LA)", "(MODERATE)", \
"(ALTERNATIVE ENGAGEMENT - WIDE)", "(IMAX EXCL)"), rerelease/anniversary \
annotations that are NOT part of the title itself, and anything else that \
doesn't belong in the other six fields. Join multiple such fragments with \
"; ". Empty string if there is nothing left over.

## Worked examples

"7/3 MINIONS & MONSTERS - PG (W-07/01)" (no studio bracket) ->
  movie_title="Minions & Monsters", release_date="2026-07-03", studio="", \
  rating="PG", release_day_tag="W", release_day_date="2026-07-01", \
  miscellaneous=""

"10/16 ... SPIRITED AWAY 25TH ANNIVERSARY - STUDIO [FTHM] - PG (SA-10/17)" ->
  movie_title="Spirited Away 25th Anniversary - Studio Ghibli Fest", \
  release_date="2026-10-16", studio="FTHM", rating="PG", \
  release_day_tag="SA", release_day_date="2026-10-17", miscellaneous=""

Extract every entry in the document. Return only the structured rows.
"""

USER_TEXT_PREFIX = "Extract every movie-release row from this calendar text:\n\n"

PROMPT_ONLY_SUFFIX = (
    "\n\nReturn ONLY a single JSON object, no prose and no markdown fences, of the "
    'form {"rows": [...]}, where each row object has exactly these keys in this '
    "order: " + ", ".join(FIELD_ORDER) + ".\n"
    "`release_day_tag` must be exactly one of: " + " | ".join(repr(t) for t in DAY_TAGS)
)

CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_release_calendar": {"type": "boolean"},
        "reasoning": {"type": "string"},
    },
    "required": ["is_release_calendar", "reasoning"],
    "additionalProperties": False,
}

CLASSIFY_SYSTEM_PROMPT = """\
You determine whether a document is a movie-studio theatrical release \
calendar: a dated list of upcoming film titles, typically organized by \
month with each date followed by one or more movie titles (often with a \
studio/distributor code in brackets and an MPAA rating). Judge the document \
text given to you and answer whether it is such a calendar, with one \
sentence of reasoning. If the text looks unrelated (a random document, a \
spreadsheet of something else entirely, empty/garbled text), say so.
"""

CLASSIFY_PROMPT_ONLY_SUFFIX = (
    '\n\nReturn ONLY a single JSON object of the form '
    '{"is_release_calendar": true|false, "reasoning": "..."}, no prose, no markdown fences.'
)

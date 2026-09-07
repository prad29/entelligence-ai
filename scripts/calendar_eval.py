#!/usr/bin/env python3
"""
calendar_eval — model-selection harness for Competitive Calendar Extraction.

Text-only sibling of mmvision.py: same MODELS-registry / cost / eval
architecture, applied to structured extraction of movie-release rows from a
studio release-calendar PDF instead of lobby-photo classification.

Usage
-----
    python calendar_eval.py preflight
    python calendar_eval.py run --model haiku45
    python calendar_eval.py eval --models nemotron,nova2lite,qwen,haiku45,sonnet5,mistral

Requires: boto3 pandas openpyxl (all already in the project venv)
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ENV_VAR = "AWS_BEARER_TOKEN_BEDROCK"
LEGACY_ENV_VAR = "BEDROCK_API_KEY"


def find_and_load_env(explicit: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        here = Path.cwd().resolve()
        candidates += [p / ".env" for p in [here, *here.parents]]
        script = Path(__file__).resolve().parent
        candidates += [p / ".env" for p in [script, *script.parents]]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            seen.add(path)
            continue
        seen.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)
        if not os.environ.get(ENV_VAR) and os.environ.get(LEGACY_ENV_VAR):
            os.environ[ENV_VAR] = os.environ[LEGACY_ENV_VAR]
        return path
    return None


# ----------------------------------------------------------------------------
# Model registry — the 5 already verified authorized/entitled in this AWS
# account (mmvision.py preflight, 2026-08-30) plus mistral (this repo's
# existing default general-purpose Bedrock model, config.py:9).
# ----------------------------------------------------------------------------

@dataclass
class Model:
    alias: str
    model_id: str
    backend: str  # "output_config" | "forced_tool" | "prompt_only"
    in_per_mtok: float
    out_per_mtok: float
    supports_temperature: bool = True


MODELS: dict[str, Model] = {
    "nemotron":  Model("nemotron",  "nvidia.nemotron-nano-12b-v2",                  "output_config", 0.20, 0.60),
    "nova2lite": Model("nova2lite", "us.amazon.nova-2-lite-v1:0",                   "forced_tool",   0.33, 2.75),
    "qwen":      Model("qwen",      "qwen.qwen3-vl-235b-a22b",                      "prompt_only",   0.53, 2.66),
    "haiku45":   Model("haiku45",   "us.anthropic.claude-haiku-4-5-20251001-v1:0",  "output_config", 1.10, 5.50),
    "sonnet5":   Model("sonnet5",   "us.anthropic.claude-sonnet-5",                 "forced_tool",   2.20, 11.00),
    "mistral":   Model("mistral",   "mistral.mistral-large-3-675b-instruct",        "prompt_only",   2.00, 6.00),
}

# ----------------------------------------------------------------------------
# Extraction schema — 7 output columns, per design doc §2.
# ----------------------------------------------------------------------------

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


def prompt_hash() -> str:
    blob = SYSTEM_PROMPT + json.dumps(SCHEMA, sort_keys=True) + PROMPT_ONLY_SUFFIX
    import hashlib
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------
# Harness input — page 1 (Jul-Dec 2026) of OneSheetCalendar.pdf, extracted via
# app.calendar_extract.pdf_text (the same column-aware extractor production
# uses), kept verbatim including its minor column-edge artifacts on purpose —
# the model has to read through the same noise production will feed it.
# ----------------------------------------------------------------------------

RAW_TEXT = """\
D
JULY - 2026
7/3 MINIONS & MONSTERS - PG (W-07/01
BACKROOMS [A24] - R ((2)
EXTENDED VERSION)
YOUNG WASHINGTON [ANGEL] - PG-
13
7/10 MOANA [DIS] - PG
EVIL DEAD BURN [WB] - R
GAIL DAUGHTRY AND THE
CELEBRITY SEX PASS [SPC] - R
MY NEIGHBOR TOTORO - STUDIO
GHIBLI FEST [FTHM] - G (SA-07/11)
7/17 ODYSSEY, THE [UNI]
MOB PSYCHO 100 CELEBRATES ITS
10TH ANNIV [SONY/CRUNC]
(ALTERNATIVE ENGAGEMENT)(M-
07/20)
7/24 BAD COUNSELORS [FTHM] - PG-13
(W-07/22)
MOTOR CITY [IFC]
7/31 SPIDER-MAN: BRAND NEW DAY
[SNY]
AUGUST - 2026
8/7 ONE NIGHT ONLY [UNI]
SUPER TROOPERS 3 [SL] - R
ONLY YESTERDAY 35TH
ANNIVERSARY - STUDIO [FTHM] - PG
(SU-08/09)
TALES FROM EARTHSEA 20TH
ANNIVERSARY - S [FTHM] - PG-13
(SA-08/08)
ICE CREAM MAN [ICOC] - NR
8/14 WET HOT AMERICAN SUMMER [UNI]
R((2) (25TH ANNIVERSARY))
SIX: THE MUSICAL LIVE! [FOC]
(MODERATE)
PAW PATROL: THE DINO MOVIE
[PAR] - PG
END OF OAK STREET, THE [WB]
NIMRODS [FFF]
TEXAS CHAINSAW DAY (2026)
[FTHM] - R(ALTERNATIVE
ENGAGEMENT - WIDE) (TU-08/18)
8/21 FAST AND THE FURIOUS, THE (2001)
[UNI] - PG-13((2) (RE: 25TH
ANNIVERSARY))
FAST AND THE FURIOUS: TOKYO
DRIFT (2006) [UNI] - PG-13((2) (RE:
20TH ANNIVERSARY))
FAST FIVE [UNI] - PG-13((2) (RE: 15T
ANNIVERSARY))
HOT SPOT [FOC] (MODERATE)
MUTINY [LION]
INSIDIOUS: OUT OF THE FURTHER
[SNY]
YOUR LETTER [SONY/CRUNC] - NR
(ALTERNATIVE ENGAGEMENT) (M-
08/17)
SPA WEEKEND [BEAR]
AMERICAN MARTYR: THE STANLEY
ROTHER STOR [FTHM](ALTERNATIV
ENGAGEMENT - WIDE)(TU-08/25)
CASTLE IN THE SKY 40TH
ANNIVERSARY - STU [FTHM] - PG
(SA-08/22)
MAGIC FARAWAY TREE, THE [VERT]
8/28 FINDING EMILY [FOC] - PG-13
(MODERATE)
DOG STARS, THE [20TH] - R
HARRY POTTER AND THE
SORCERER'S STONE [WB] - PG ((2)
(RE - 25TH ANNIVERSARY))
COYOTE VS. ACME [FFF]
CONFIDENTIAL: FOR INTERNAL USE ONLY
mestic Release
Date from Jul-03-2026 Sorted By D
8/28 TERMINATOR 2: JUDGMENT DAY
35TH ANNIVERS [FTHM] - R
IDIOTS [IFC]
BUDDY [RSA]
CLIFFHANGER [ROW K]
SEPTEMBER - 2026
9/4 NUTTY PROFESSOR, THE - PG-13((2
(RE: 30TH ANNIVERSARY)) (SU-09/0
CARS (20TH ANNIVERSARY) [DIS]
BY ANY MEANS [PAR]
ONSLAUGHT [A24]
HOW TO ROB A BANK [AMZMGM] -
FALL 2: DEADPOINT [LGPR] (W-
09/02)
9/11 UPRISING, THE [FOC]
UNTITLED OASIS DOCUMENTARY
[DIS]
PRACTICAL MAGIC FILM, A [WB]
RUNNER [ANGEL]
FIX, THE [BCLF]
MST3K: THE RIFFTRAX
EXPERIMENTS - STING [FTHM] - NR
(ALTERNATIVE ENGAGEMENT -
WIDE) (W-09/09)
PASSION OF THE CHRIST, THE (202
EVENT) [FTHM] - R (TH-09/10)
DON'T MOVE [INDEFILMS]
HOPE [NEON] (W-09/09)
UNTITLED HADES RELEASE (9/11/26
[VAR]
TOM AND JERRY: THE FORBIDDEN
COMPASS [VIVA] - PG (W-09/09)
9/18 RESIDENT EVIL [SNY]
UNTITLED A24 (9/18/26) [A24] (LTD)
HEAVEN IN STONE AND GLASS
[FTHM](ALTERNATIVE ENGAGEMEN
- WIDE)(SU-09/20)
SHAUN THE SHEEP: THE BEAST OF
MOSSY BOTT [GKIDS]
WEIGHT, THE [VERT]
9/25 FORGOTTEN ISLAND [UNI]
AVENGERS: ENDGAME ENCORE
[DIS]
HEART OF THE BEAST [PAR]
YOUR MOTHER YOUR MOTHER
YOUR MOTHER [AMZMGM] (LTD)
HA-CHAN, SHAKE YOUR BOOTY!
[SPC] (MODERATE)
VICTORIAN PSYCHO [BST]
PRINCESS MONONOKE - STUDIO
GHIBLI FEST 2 [FTHM] (SA-09/26)
CRAWLERS [RSA]
OCTOBER - 2026
10/2 DIGGER [WB]
VERITY [AMZMGM] - R
YOUR MOTHER YOUR MOTHER
YOUR MOTHER [AMZMGM] ((2)
EXPANSION)
ROLLING LOUD [FFF]
BEWARE BOIUNA [LGPR]
10/9 OTHER MOMMY [UNI]
SOCIAL RECKONING, THE [SNY]
UNTITLED A24 (10/9/2026) [A24] (LT
YOUR MOTHER YOUR MOTHER
YOUR MOTHER [AMZMGM] ((3) WID
ANGEL AND THE BADMAN [ANGEL]
GONE WITH THE WIND (2026 EVENT
[FTHM] - G(ALTERNATIVE
ENGAGEMENT - WIDE) (SA-10/10)
GUILLERMO DEL TORO'S PAN'S
LABYRINTH 20T [FTHM] - R
alendar
te
10/16 SENSE AND SENSIBILITY [FOC]
WHALEFALL [20TH]
STREET FIGHTER [PAR]
FLYWHEEL: IGNITION OF THE SOUL
[SNY] - PG (MODERATE)
ONLY LIVING PICKPOCKET IN NEW
YORK, THE [SPC] (NY/LA)
) SPIRITED AWAY 25TH ANNIVERSARY
- STUDIO [FTHM] - PG (SA-10/17)
POETIC LICENSE [ROW K]
10/23 KLARA AND THE SUN [SNY]
CLAYFACE [WB]
UNTITLED AMAZON MGM EVENT
FILM (10/23/26 [AMZMGM]
ONLY LIVING PICKPOCKET IN NEW
YORK, THE [SPC] ((2) LTD -
EXPANSION)
WIFE & DOG [BEAR]
WILDWOOD [FTHM]
10/30 CHRISTMAS AT THE KRINGLES
[BCLF]
UNTITLED ICONIC HORROR FILM
(10/30/26) [ICOC]
SIGHT & SOUND PRESENTS:
JOSHUA LIVE! [RSA]
MONGOOSE, THE [SGF]
NOVEMBER - 2026
11/6 ARCHANGEL [SNY]
CAT IN THE HAT, THE [WB]
UNTITLED A24 (11/6/2026) [A24] (LTD)
I PLAY ROCKY [AMZMGM] (LTD)
BOLA NEGRA, LA [NETFLIX] (LTD)
WILD HORSE NINE [SL] - R
DRUMMER BOY [ANGEL]
JIMMY [FTHM]
GODZILLA MINUS ZERO [GKIDS]
11/13 EBENEZER: A CHRISTMAS CAROL
[PAR]
GREAT BEYOND, THE [WB]
I PLAY ROCKY [AMZMGM] ((2)
EXPANSION)
UNTITLED VARIANCE THING
(11/13/26) [VAR]
11/20 BEAUTIFUL MIND, A (2001) - PG-13((2)
(RE: 25TH ANNIVERSARY)) (SU-11/22)
HUNGER GAMES: SUNRISE ON THE
REAPING [LION]
UNTITLED A24 (11/20/26) [A24]
I PLAY ROCKY [AMZMGM] ((3) WIDE)
NOVEMBER 1963 [FFF]
PAPER TIGER [NEON] ((2) WIDE -
EXPANSION)
11/27 FOCKER-IN-LAW - PG-13 (W-11/25)
DISNEY'S HEXED [DIS] (W-11/25)
UNTITLED FINCHER PROJECT
[NETFLIX] (IMAX EXCL) (W-11/25)
HERSHEY [ANGEL] (W-11/25)
DECEMBER - 2026
12/4 VIOLENT NIGHT 2 [UNI] - R
12/11 ZERO A.D. [ANGEL]
UNTITLED MUSIC EVENT MOVIE
(12/11/26) [BST]
) 12/18 AVENGERS: DOOMSDAY [DIS]
DUNE: PART THREE [WB]
)
IT'S A WONDERFUL LIFE 80TH
ANNIVERSARY [FTHM] - PG
12/25 WERWULF [FOC]
ANGRY BIRDS MOVIE 3, THE [PAR]
(W-12/23)
MR. IRRELEVANT [PAR]
JUMANJI: OPEN WORLD [SNY]
Page1 of 1
"""

# ----------------------------------------------------------------------------
# Golden set — 55 hand-labeled rows covering every edge case, drawn entirely
# from the RAW_TEXT above so the model always has the cross-referencing
# context it needs (e.g. the un-truncated "Studio Ghibli Fest" full form is
# in the same input as every truncated instance).
# ----------------------------------------------------------------------------

def _g(release_date, title, studio="", rating="", tag="", tag_date="", misc=""):
    return {
        "release_date": release_date, "movie_title": title, "studio": studio,
        "rating": rating, "release_day_tag": tag, "release_day_date": tag_date,
        "miscellaneous": misc,
    }


GOLDEN: list[dict] = [
    _g("2026-07-03", "Minions & Monsters", rating="PG", tag="W", tag_date="2026-07-01"),
    _g("2026-07-03", "Backrooms", studio="A24", rating="R", misc="(2) Extended Version"),
    _g("2026-07-03", "Young Washington", studio="ANGEL", rating="PG-13"),
    _g("2026-07-10", "Moana", studio="DIS", rating="PG"),
    _g("2026-07-10", "Gail Daughtry and the Celebrity Sex Pass", studio="SPC", rating="R"),
    _g("2026-07-10", "My Neighbor Totoro - Studio Ghibli Fest", studio="FTHM", rating="G",
       tag="SA", tag_date="2026-07-11"),
    _g("2026-07-17", "Odyssey, The", studio="UNI"),
    _g("2026-07-17", "Mob Psycho 100 Celebrates Its 10th Anniv", studio="SONY/CRUNC",
       tag="M", tag_date="2026-07-20", misc="Alternative Engagement"),
    _g("2026-07-24", "Bad Counselors", studio="FTHM", rating="PG-13", tag="W", tag_date="2026-07-22"),
    _g("2026-07-24", "Motor City", studio="IFC"),
    _g("2026-07-31", "Spider-Man: Brand New Day", studio="SNY"),
    _g("2026-08-07", "Super Troopers 3", studio="SL", rating="R"),
    _g("2026-08-07", "Only Yesterday 35th Anniversary - Studio Ghibli Fest", studio="FTHM",
       rating="PG", tag="SU", tag_date="2026-08-09"),
    _g("2026-08-07", "Tales From Earthsea 20th Anniversary - Studio Ghibli Fest", studio="FTHM",
       rating="PG-13", tag="SA", tag_date="2026-08-08"),
    _g("2026-08-07", "Ice Cream Man", studio="ICOC", rating="NR"),
    _g("2026-08-14", "Wet Hot American Summer", studio="UNI", rating="R", misc="(2) (25th Anniversary)"),
    _g("2026-08-14", "Six: The Musical Live!", studio="FOC", misc="Moderate"),
    _g("2026-08-14", "Paw Patrol: The Dino Movie", studio="PAR", rating="PG"),
    _g("2026-08-14", "Texas Chainsaw Day (2026)", studio="FTHM", rating="R", tag="TU",
       tag_date="2026-08-18", misc="Alternative Engagement - Wide"),
    _g("2026-08-21", "Fast and the Furious, The (2001)", studio="UNI", rating="PG-13",
       misc="(2) (RE: 25th Anniversary)"),
    _g("2026-08-21", "Fast Five", studio="UNI", rating="PG-13", misc="(2) (RE: 15th Anniversary)"),
    _g("2026-08-21", "Your Letter", studio="SONY/CRUNC", rating="NR", tag="M", tag_date="2026-08-17",
       misc="Alternative Engagement"),
    _g("2026-08-21", "American Martyr: The Stanley Rother Story", studio="FTHM", tag="TU",
       tag_date="2026-08-25", misc="Alternative Engagement - Wide"),
    _g("2026-08-21", "Castle in the Sky 40th Anniversary - Studio Ghibli Fest", studio="FTHM",
       rating="PG", tag="SA", tag_date="2026-08-22"),
    _g("2026-08-28", "Finding Emily", studio="FOC", rating="PG-13", misc="Moderate"),
    _g("2026-08-28", "Harry Potter and the Sorcerer's Stone", studio="WB", rating="PG",
       misc="(2) (RE - 25th Anniversary)"),
    _g("2026-08-28", "Terminator 2: Judgment Day 35th Anniversary", studio="FTHM", rating="R"),
    _g("2026-08-28", "Cliffhanger", studio="ROW K"),
    _g("2026-09-04", "Nutty Professor, The", rating="PG-13", tag="SU", tag_date="2026-09-06",
       misc="(2) (RE: 30th Anniversary)"),
    _g("2026-09-04", "Cars (20th Anniversary)", studio="DIS"),
    _g("2026-09-04", "Fall 2: Deadpoint", studio="LGPR", tag="W", tag_date="2026-09-02"),
    _g("2026-09-11", "MST3K: The Rifftrax Experiments - Sting", studio="FTHM", rating="NR",
       tag="W", tag_date="2026-09-09", misc="Alternative Engagement - Wide"),
    _g("2026-09-11", "Passion of the Christ, The (2026 Event)", studio="FTHM", rating="R",
       tag="TH", tag_date="2026-09-10"),
    _g("2026-09-11", "Hope", studio="NEON", tag="W", tag_date="2026-09-09"),
    _g("2026-09-11", "Untitled Hades Release (9/11/26)", studio="VAR"),
    _g("2026-09-11", "Tom and Jerry: The Forbidden Compass", studio="VIVA", rating="PG",
       tag="W", tag_date="2026-09-09"),
    _g("2026-09-18", "Untitled A24 (9/18/26)", studio="A24", misc="LTD"),
    _g("2026-09-18", "Heaven in Stone and Glass", studio="FTHM", tag="SU", tag_date="2026-09-20",
       misc="Alternative Engagement - Wide"),
    _g("2026-09-18", "Shaun the Sheep: The Beast of Mossy Bottom", studio="GKIDS"),
    _g("2026-09-25", "Your Mother Your Mother Your Mother", studio="AMZMGM", misc="LTD"),
    _g("2026-09-25", "Princess Mononoke - Studio Ghibli Fest 2", studio="FTHM", tag="SA",
       tag_date="2026-09-26"),
    _g("2026-10-02", "Verity", studio="AMZMGM", rating="R"),
    _g("2026-10-02", "Your Mother Your Mother Your Mother", studio="AMZMGM", misc="(2) Expansion"),
    _g("2026-10-09", "Untitled A24 (10/9/2026)", studio="A24", misc="LTD"),
    _g("2026-10-09", "Your Mother Your Mother Your Mother", studio="AMZMGM", misc="(3) Wide"),
    _g("2026-10-09", "Gone With the Wind (2026 Event)", studio="FTHM", rating="G", tag="SA",
       tag_date="2026-10-10", misc="Alternative Engagement - Wide"),
    _g("2026-10-09", "Guillermo del Toro's Pan's Labyrinth 20th Anniversary", studio="FTHM", rating="R"),
    _g("2026-10-16", "Only Living Pickpocket in New York, The", studio="SPC", misc="NY/LA"),
    _g("2026-10-16", "Spirited Away 25th Anniversary - Studio Ghibli Fest", studio="FTHM",
       rating="PG", tag="SA", tag_date="2026-10-17"),
    _g("2026-10-23", "Untitled Amazon MGM Event Film (10/23/26)", studio="AMZMGM"),
    _g("2026-10-23", "Only Living Pickpocket in New York, The", studio="SPC", misc="(2) LTD - Expansion"),
    _g("2026-11-13", "Untitled Variance Thing (11/13/26)", studio="VAR"),
    _g("2026-11-20", "Beautiful Mind, A (2001)", rating="PG-13", tag="SU", tag_date="2026-11-22",
       misc="(2) (RE: 25th Anniversary)"),
    _g("2026-11-27", "Focker-in-Law", rating="PG-13", tag="W", tag_date="2026-11-25"),
    _g("2026-11-27", "Untitled Fincher Project", studio="NETFLIX", tag="W", tag_date="2026-11-25",
       misc="IMAX Excl"),
    _g("2026-12-25", "Angry Birds Movie 3, The", studio="PAR", tag="W", tag_date="2026-12-23"),
]

# Sanity-check every hand-labeled release_day_tag against the actual weekday
# of release_day_date — catches a labeling mistake before it poisons scoring.
_WEEKDAY_FOR_TAG = {"M": 0, "TU": 1, "W": 2, "TH": 3, "SA": 5, "SU": 6}


def _validate_golden() -> None:
    for row in GOLDEN:
        tag, d = row["release_day_tag"], row["release_day_date"]
        if not tag:
            continue
        y, m, dd = (int(x) for x in d.split("-"))
        actual = date(y, m, dd).weekday()
        expected = _WEEKDAY_FOR_TAG[tag]
        if actual != expected:
            raise AssertionError(
                f"golden row {row['movie_title']!r}: tag {tag} implies weekday "
                f"{expected}, but {d} is weekday {actual}"
            )


_validate_golden()


# ----------------------------------------------------------------------------
# Bedrock call
# ----------------------------------------------------------------------------

TOOL_NAME = "emit_calendar_rows"


def _build_converse_kwargs(model: Model, use_temp: bool) -> dict[str, Any]:
    system = [{"text": SYSTEM_PROMPT + (PROMPT_ONLY_SUFFIX if model.backend == "prompt_only" else "")}]
    kwargs: dict[str, Any] = {
        "modelId": model.model_id,
        "messages": [{"role": "user", "content": [{"text": USER_TEXT_PREFIX + RAW_TEXT}]}],
        "system": system,
        "inferenceConfig": {"maxTokens": 16000},
    }
    if use_temp:
        kwargs["inferenceConfig"]["temperature"] = 0.0
    if model.backend == "output_config":
        kwargs["outputConfig"] = {
            "textFormat": {"type": "json_schema",
                           "structure": {"jsonSchema": {"name": "calendar_rows", "schema": json.dumps(SCHEMA)}}}
        }
    elif model.backend == "forced_tool":
        kwargs["toolConfig"] = {
            "tools": [{"toolSpec": {"name": TOOL_NAME, "description": "Emit the extracted calendar rows.",
                                    "inputSchema": {"json": SCHEMA}}}],
            "toolChoice": {"tool": {"name": TOOL_NAME}},
        }
    return kwargs


def call_bedrock(client, model: Model) -> tuple[dict, dict]:
    use_temp = model.supports_temperature
    kwargs = _build_converse_kwargs(model, use_temp)
    try:
        resp = client.converse(**kwargs)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "temperature" in msg.lower() and use_temp:
            model.supports_temperature = False
            kwargs = _build_converse_kwargs(model, False)
            try:
                resp = client.converse(**kwargs)
            except Exception as exc2:  # noqa: BLE001
                msg2 = str(exc2)
                if "toolChoice" in msg2 and kwargs.get("toolConfig"):
                    kwargs["toolConfig"]["toolChoice"] = {"any": {}}
                    resp = client.converse(**kwargs)
                else:
                    raise
        elif "toolChoice" in msg and kwargs.get("toolConfig"):
            kwargs["toolConfig"]["toolChoice"] = {"any": {}}
            resp = client.converse(**kwargs)
        else:
            raise
    return resp, resp.get("usage", {})


JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_response(resp: dict, backend: str) -> dict:
    blocks = resp["output"]["message"]["content"]
    if backend == "forced_tool":
        for b in blocks:
            if "toolUse" in b:
                return b["toolUse"]["input"]
        raise ValueError("no toolUse block in response")
    text = "".join(b.get("text", "") for b in blocks).strip()
    if not text:
        raise ValueError("empty text response")
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = JSON_RE.search(text)
        if not m:
            raise ValueError(f"no JSON object found in: {text[:200]}")
        return json.loads(m.group(0))


def validate(rec: dict) -> list[str]:
    errs = []
    rows = rec.get("rows")
    if not isinstance(rows, list):
        return ["'rows' is not a list"]
    for i, row in enumerate(rows):
        for k in FIELD_ORDER:
            if k not in row:
                errs.append(f"row {i}: missing key '{k}'")
        tag = row.get("release_day_tag")
        if tag is not None and tag not in DAY_TAGS:
            errs.append(f"row {i}: release_day_tag {tag!r} not one of {DAY_TAGS}")
    return errs


def cost_usd(model: Model, usage: dict) -> float:
    return (usage.get("inputTokens", 0) / 1e6 * model.in_per_mtok
            + usage.get("outputTokens", 0) / 1e6 * model.out_per_mtok)


# ----------------------------------------------------------------------------
# Matching predicted rows to golden rows, and scoring
# ----------------------------------------------------------------------------

def norm_title(s: str) -> str:
    s = str(s or "").casefold().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_predictions(golden: list[dict], predicted: list[dict]) -> list[dict | None]:
    """For each golden row, greedily pick the best unused predicted row with
    the same release_date, by title similarity. None if no candidate exists."""
    by_date: dict[str, list[int]] = {}
    for i, p in enumerate(predicted):
        by_date.setdefault(str(p.get("release_date", "")), []).append(i)
    used: set[int] = set()
    matches: list[dict | None] = []
    for g in golden:
        candidates = by_date.get(g["release_date"], [])
        best_i, best_score = None, -1.0
        for i in candidates:
            if i in used:
                continue
            score = difflib.SequenceMatcher(
                None, norm_title(g["movie_title"]), norm_title(predicted[i].get("movie_title", "")),
            ).ratio()
            if score > best_score:
                best_i, best_score = i, score
        if best_i is not None and best_score >= 0.4:
            used.add(best_i)
            matches.append(predicted[best_i])
        else:
            matches.append(None)
    return matches


SCORE_FIELDS = ["movie_title", "release_date", "studio", "rating",
                "release_day_tag", "release_day_date", "miscellaneous"]


def field_match(field_name: str, expected: str, actual: str | None) -> bool:
    if actual is None:
        return False
    if field_name == "movie_title":
        return norm_title(expected) == norm_title(actual)
    if field_name == "miscellaneous":
        return norm_title(expected) == norm_title(actual) or (not expected.strip() and not (actual or "").strip())
    return str(expected).strip().casefold() == str(actual).strip().casefold()


@dataclass
class EvalResult:
    alias: str
    field_acc: dict[str, float] = field(default_factory=dict)
    full_row_acc: float = 0.0
    rows_missed: int = 0
    cost: float = 0.0
    in_tok: int = 0
    out_tok: int = 0
    latency_ms: int = 0
    retries: int = 0
    error: str = ""
    mismatches: list[dict] = field(default_factory=list)


def run_and_score(client, model: Model) -> EvalResult:
    r = EvalResult(alias=model.alias)
    t0 = time.time()
    extra_note = ""
    rec: dict = {}
    for attempt in range(2):
        resp, usage = call_bedrock(client, model)
        r.in_tok += usage.get("inputTokens", 0)
        r.out_tok += usage.get("outputTokens", 0)
        r.cost += cost_usd(model, usage)
        try:
            rec = parse_response(resp, model.backend)
            errs = validate(rec)
        except Exception as exc:  # noqa: BLE001
            rec, errs = {}, [str(exc)]
        if not errs:
            break
        if attempt == 0:
            r.retries += 1
        else:
            r.error = "parse_failure: " + "; ".join(errs[:3])
    r.latency_ms = int((time.time() - t0) * 1000)

    predicted = rec.get("rows") or []
    matches = match_predictions(GOLDEN, predicted)
    r.rows_missed = sum(1 for m in matches if m is None)

    per_field_hits = {f: 0 for f in SCORE_FIELDS}
    full_row_hits = 0
    for g, m in zip(GOLDEN, matches):
        row_ok = True
        for f in SCORE_FIELDS:
            ok = field_match(f, g[f], m.get(f) if m else None)
            per_field_hits[f] += int(ok)
            row_ok = row_ok and ok
        if row_ok:
            full_row_hits += 1
        else:
            r.mismatches.append({
                "movie_title": g["movie_title"], "release_date": g["release_date"],
                "expected": g, "predicted": m,
            })
    n = len(GOLDEN)
    r.field_acc = {f: round(per_field_hits[f] / n, 4) for f in SCORE_FIELDS}
    r.full_row_acc = round(full_row_hits / n, 4)
    return r


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def make_client():
    import boto3
    from botocore.config import Config
    # sonnet5 timed out on boto3's default 60s read timeout for this bulk
    # extraction task (~100s+ observed) — give every model real headroom.
    return boto3.client(
        "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"),
        config=Config(read_timeout=240, connect_timeout=10),
    )


def cmd_preflight(args) -> int:
    print(f"prompt hash: {prompt_hash()}")
    print(f"golden set : {len(GOLDEN)} rows (self-validated weekday-vs-tag consistency: OK)")
    token = os.environ.get(ENV_VAR)
    if not token:
        print(f"FAIL {ENV_VAR} not set.")
        return 1
    print(f"auth       : {ENV_VAR} set ({len(token)} chars)")
    for alias, m in MODELS.items():
        print(f"  {alias:<10} {m.model_id:<50} backend={m.backend}")
    return 0


def cmd_run(args) -> int:
    if args.model not in MODELS:
        print(f"unknown model alias {args.model!r}. Known: {', '.join(MODELS)}")
        return 1
    client = make_client()
    r = run_and_score(client, MODELS[args.model])
    print(json.dumps({
        "alias": r.alias, "field_acc": r.field_acc, "full_row_acc": r.full_row_acc,
        "rows_missed": r.rows_missed, "cost_usd": round(r.cost, 4),
        "tokens": r.in_tok + r.out_tok, "latency_ms": r.latency_ms,
        "retries": r.retries, "error": r.error,
    }, indent=2))
    return 0


def cmd_eval(args) -> int:
    import pandas as pd

    aliases = [a.strip() for a in args.models.split(",") if a.strip()]
    unknown = [a for a in aliases if a not in MODELS]
    if unknown:
        print(f"unknown alias(es): {unknown}. Known: {', '.join(MODELS)}")
        return 1

    client = make_client()
    results: list[EvalResult] = []
    for alias in aliases:
        model = MODELS[alias]
        print(f"=== {alias} ({model.backend}) ===")
        r = run_and_score(client, model)
        results.append(r)
        print(f"  full_row_acc={r.full_row_acc:.3f} title_acc={r.field_acc['movie_title']:.3f} "
              f"missed={r.rows_missed}/{len(GOLDEN)} cost=${r.cost:.4f} "
              f"tokens={r.in_tok + r.out_tok} latency={r.latency_ms}ms "
              f"retries={r.retries} error={r.error or '-'}")

    summary_rows = []
    for r in results:
        row = {
            "model": r.alias, "full_row_acc": r.full_row_acc, "rows_missed": r.rows_missed,
            "cost_usd": round(r.cost, 4),
            "proj_cost_per_1000_calendars": round(r.cost * 1000, 2),
            "tokens": r.in_tok + r.out_tok, "latency_ms": r.latency_ms,
            "retries": r.retries, "error": r.error,
        }
        row.update({f"acc[{f}]": r.field_acc[f] for f in SCORE_FIELDS})
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows).sort_values("full_row_acc", ascending=False)

    mismatch_rows = []
    for r in results:
        for mm in r.mismatches:
            mismatch_rows.append({
                "model": r.alias, "movie_title": mm["movie_title"], "release_date": mm["release_date"],
                "expected": json.dumps(mm["expected"]), "predicted": json.dumps(mm["predicted"]),
            })
    mismatches = pd.DataFrame(mismatch_rows)

    out_dir = Path(args.out) if args.out else Path("out/calendar_eval")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "eval.xlsx"
    with pd.ExcelWriter(dest, engine="openpyxl") as xl:
        summary.to_excel(xl, sheet_name="Summary", index=False)
        (mismatches if not mismatches.empty else pd.DataFrame([{"note": "no mismatches"}])).to_excel(
            xl, sheet_name="Mismatches", index=False)
        for name, ws in xl.sheets.items():
            ws.freeze_panes = "A2"

    winner = summary.iloc[0]
    md = [
        "# calendar_eval summary", "",
        f"Golden set: {len(GOLDEN)} hand-labeled rows from `OneSheetCalendar.pdf` "
        "(Jul-Dec 2026), covering every edge case in the design doc.", "",
        summary.to_markdown(index=False), "",
        f"**Winner by full-row accuracy: `{winner['model']}`** "
        f"(full_row_acc={winner['full_row_acc']:.3f}, ${winner['cost_usd']:.4f}/doc, "
        f"${winner['proj_cost_per_1000_calendars']:.2f}/1000 calendars).",
    ]
    (out_dir / "summary.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\nwrote {dest} and {out_dir / 'summary.md'}")
    print(f"winner: {winner['model']} (full_row_acc={winner['full_row_acc']:.3f})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="calendar_eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to .env (default: search upward from cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight")
    p = sub.add_parser("run")
    p.add_argument("--model", required=True)
    p = sub.add_parser("eval")
    p.add_argument("--models", required=True)
    p.add_argument("--out")

    args = ap.parse_args()
    env_path = find_and_load_env(args.env)
    if env_path:
        print(f"loaded env: {env_path}")
    return {"preflight": cmd_preflight, "run": cmd_run, "eval": cmd_eval}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

"""Extraction prompt/schema for lobby-check.

Ported from the validated mmvision.py prototype (repo root) with two
additions: `material_condition` is promoted from a derived label to a
model-emitted field with its own confidence, and `confidence_material_quantity`
is added — see docs/plans/2026-09-01-lobby-check-design.md §3.4 for why. Every
other rule below is hard-won from the prototype's eval corpus; do not
paraphrase them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.lobby_check.taxonomy import DEFECTS, MATERIAL_CONDITIONS, MATERIAL_TYPES

# ----------------------------------------------------------------------------
# Extraction schema — visual_notes first (forces the model to describe what
# it sees before committing to any field), condition placed after its
# evidence (defects/defect_evidence).
# ----------------------------------------------------------------------------

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "visual_notes": {
            "type": "string",
            "description": "What is physically visible: how the item is mounted or "
                           "supported, and where on the poster you read the title from.",
        },
        "material_type": {"type": "string", "enum": MATERIAL_TYPES},
        "confidence_material_type": {"type": "number"},
        "movie_title": {
            "type": "string",
            "description": "The film title. Empty string if not legible or not present.",
        },
        "confidence_movie_title": {"type": "number"},
        "material_quantity": {"type": "integer"},
        "confidence_material_quantity": {"type": "number"},
        "defects": {"type": "array", "items": {"type": "string", "enum": DEFECTS}},
        "defect_evidence": {
            "type": "string",
            "description": "What is actually visible. Empty string if no defects.",
        },
        "material_condition": {"type": "string", "enum": MATERIAL_CONDITIONS},
        "confidence_material_condition": {"type": "number"},
    },
    "required": [
        "visual_notes", "material_type", "confidence_material_type",
        "movie_title", "confidence_movie_title",
        "material_quantity", "confidence_material_quantity",
        "defects", "defect_evidence",
        "material_condition", "confidence_material_condition",
    ],
    "additionalProperties": False,
}

FIELD_ORDER: list[str] = EXTRACTION_SCHEMA["required"]

# ----------------------------------------------------------------------------
# Prompt. Every rule here prevents a specific failure observed in the
# mmvision.py eval corpus.
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You audit cinema lobby marketing materials from photographs. For each photo you \
return one structured record describing the single material the photo is about.

## Classify on MOUNTING, never on artwork

The poster art is identical across material types. What distinguishes them is how \
the item is physically supported:

  Wall-mounted, rigid frame, glass-fronted and/or internally lit,
    usually a branded header strip above the artwork ......... One Sheet
  Wall-mounted, fabric or vinyl, landscape, no frame .......... Banner
  Floor-standing, portrait, slim metal frame, weighted base ... Banner Stand
  Floor-standing, flat printed board, no frame, leaning ....... Easel Back Standee
  Floor-standing, three-dimensional, multi-panel or plinth .... Spectacular Standee

A movie poster in a wall-mounted poster case — framed, glass-fronted, and/or \
internally illuminated, usually with a branded header strip reading "REGAL" or \
"REGAL Coming Soon" — is ALWAYS `One Sheet`. This holds whether or not the case \
appears lit, whether the trim is black or red, and whether it is indoors or on an \
exterior wall. Illumination is not a distinguishing feature: exterior cases \
photographed in daylight look unlit but are the same item.

## The title is the title treatment or billing block — NEVER the largest text

The title is the film's styled logotype, or the name in the billing block above the \
release date. It is NOT necessarily the largest text: taglines are frequently set \
much larger than titles. If the largest text reads like a sentence or a phrase, it \
is a tagline, not a title. Say in `visual_notes` where on the poster you read the \
title from. If no title is legible, return an empty string — do not guess.

## Quantity is subject-scoped

Identify the SUBJECT: the material the photo is composed around — centred, largest, \
most fully in frame, in focus. Anything cut off by the frame edge, or behind or \
beside the subject, is context and is EXCLUDED, whatever it advertises.

`material_quantity` is the number of discrete units of the subject's material type, \
advertising the same film, forming one contiguous placement.
  - one poster in one case ....................................... 1
  - six adjacent cases showing six different films ............... 1
  - six adjacent cases all showing the same film ................. 6
  - one fixture built from several panels for one film ........... 1
Count fixtures, not printed panels. Never count neighbouring fixtures.

## Defects: positive visual evidence only

Report a defect ONLY when you can point to it in the image. Specifically do NOT \
report a defect for:
  - A fixture that appears unlit. Exterior cases in daylight always look unlit, and \
    a failed lamp cannot be distinguished from sunlight washout.
  - A header strip reading only "REGAL" rather than "REGAL Coming Soon". These are \
    different printed inserts, not a partially dark strip.
  - A straight horizontal or vertical line across a standee — that is a panel join, \
    not a crease.
  - Ripple, slack or waviness in fabric banners. Fabric always ripples. Only a tear, \
    a detached corner, or a fold that obscures artwork is damage.
Return an empty `defects` array and an empty `defect_evidence` when the material is \
intact.

## Condition is a direct call, backed by defects

`material_condition` is `damaged` if and only if you listed at least one item in \
`defects` above; otherwise it is `good`. Never return `damaged` with an empty \
`defects` array, and never return `good` with a non-empty one — `defect_evidence` \
is what grounds the call, not a separate judgment made independently of it.

## Confidence

Each `confidence_*` field is your calibrated probability that the paired value is \
correct, 0.0 to 1.0. Be honest: a low score routes the record to a human, which is \
cheap. A confident wrong answer is expensive.
  - `confidence_material_quantity`: lower it when adjacent fixtures are cut off by \
    the frame edge, or you cannot tell whether neighbouring cases show the same film.
  - `confidence_material_condition`: your probability that a human auditor looking \
    at this same photo would agree with your good/damaged call. Lower it when the \
    item is partly out of frame, in poor light, at an oblique angle, or when you \
    considered and rejected a possible defect under the rules above — and say so in \
    `defect_evidence`.

Write `visual_notes` FIRST, describing what you actually see, before deciding any \
other field.
"""

FRAMING_HINT = {
    "close": (
        "\n\nFRAMING: this is a CLOSE crop. The poster fills the frame and the mounting "
        "hardware may be cut off. If you cannot actually see how the item is supported, "
        "return `Other` for material_type with low confidence rather than guessing — "
        "close crops of floor-standing materials look identical to wall-mounted ones."
    ),
    "wide": (
        "\n\nFRAMING: this is a WIDE shot. The mounting and surroundings should be "
        "visible. Use them to decide material_type."
    ),
    "unknown": "",
}

FRAMING_BY_WIDTH = {629: "close", 768: "wide"}

USER_TEXT = (
    "Extract the record for the material in this photograph. "
    "Return only the structured fields."
)

# Qwen 3-VL on Bedrock has no native structured-output/tool-calling support
# (the "prompt_only" backend in mmvision.py's model registry) — the schema is
# enforced via prompt instructions and JSON parsing, with one repair retry on
# validation failure (see extractor.py).
PROMPT_ONLY_SUFFIX = (
    "\n\nReturn ONLY a single JSON object, no prose and no markdown fences, with "
    "exactly these keys in this order: " + ", ".join(FIELD_ORDER) + ".\n"
    "`material_type` must be exactly one of: " + " | ".join(MATERIAL_TYPES) + "\n"
    "`defects` is an array whose members must each be one of: " + " | ".join(DEFECTS) + "\n"
    "`material_condition` must be exactly one of: " + " | ".join(MATERIAL_CONDITIONS)
)


def prompt_hash() -> str:
    """Stable identifier for the current prompt+schema combination. Not
    consumed by any cache in phase 1 (no dedup cache is ported from
    mmvision.py — see design doc §3.2/§4.7), but recorded per-call as a
    useful LlmCallLog correlate, and changes whenever the schema changes —
    exercised by test_lobby_check_schemas.py.
    """
    blob = SYSTEM_PROMPT + json.dumps(EXTRACTION_SCHEMA, sort_keys=True) + PROMPT_ONLY_SUFFIX
    return hashlib.sha256(blob.encode()).hexdigest()[:12]

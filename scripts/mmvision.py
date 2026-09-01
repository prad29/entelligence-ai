#!/usr/bin/env python3
"""
mmvision — cinema marketing-material extraction via Amazon Bedrock.

Single-file test harness. Reads a CSV of S3 image URLs, sends each image to a
Bedrock vision model, and writes the input CSV back out with 10 new columns.

Auth: Bedrock API key from AWS_BEARER_TOKEN_BEDROCK, loaded from a .env file
found in the project root (searched upward from cwd and from this script).

Usage
-----
    python mmvision.py preflight
    python mmvision.py process --csv image-data.csv --model qwen --limit 5
    python mmvision.py process --csv image-data.csv --model haiku45
    python mmvision.py eval    --csv image-data.csv --models qwen,haiku45,nemotron
    python mmvision.py validate --csv image-data.csv

Requires: boto3 pandas openpyxl pillow requests
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# .env loading — project root, no python-dotenv dependency
# ----------------------------------------------------------------------------

ENV_VAR = "AWS_BEARER_TOKEN_BEDROCK"
# This project's .env carries the same Bedrock API key under the legacy name
# BEDROCK_API_KEY (used by the existing agentic-queue Bedrock integration).
# boto3 only recognizes AWS_BEARER_TOKEN_BEDROCK, so alias it in-process.
LEGACY_ENV_VAR = "BEDROCK_API_KEY"

# boto3/botocore version that first supports AWS_BEARER_TOKEN_BEDROCK bearer
# auth. Below this, the variable is silently ignored and calls fail with a
# confusing credentials error instead of using the token.
MIN_BOTO3_VERSION = (1, 35, 60)


def find_and_load_env(explicit: str | None = None) -> Path | None:
    """Search for a .env file and load it into os.environ (without overriding
    variables already set in the real environment)."""
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


def _boto3_version_tuple() -> tuple[int, ...]:
    import boto3
    parts = re.findall(r"\d+", boto3.__version__)
    return tuple(int(p) for p in parts[:3])


# ----------------------------------------------------------------------------
# Taxonomy — 22 values. See handoff §6. Do not add the withheld 15.
# ----------------------------------------------------------------------------

MATERIAL_TYPES = [
    "One Sheet", "Banner", "Banner Stand", "Easel Back Standee", "Spectacular Standee",
    "Box Standee", "Wrap", "Floor Decal", "Counter Card", "Static Clings",
    "Billboards/Marquees", "Video Wall", "Costume Displays", "Bus Shelter",
    "Digital Bus Shelter", "Digital Displays",
    "Popcorn Tub", "Drink Cup", "Kids Tray", "Buttons (Staff Worn)", "T-Shirts (Staff Worn)",
    "Other",
]

OUT_OF_SCOPE = {
    "Popcorn Tub", "Drink Cup", "Kids Tray",
    "Buttons (Staff Worn)", "T-Shirts (Staff Worn)",
}

DEFECTS = [
    "tear", "crease_or_fold", "peeling_or_lifting", "fading", "water_damage",
    "graffiti", "obscured_by_sticker", "broken_glazing",
    "broken_or_missing_frame_part", "detached_or_sagging_mount",
    "poster_slipped_or_empty",
]

# ----------------------------------------------------------------------------
# Model registry.  IDs verified against AWS model cards, 30 Aug 2026.
# backend: "output_config" | "forced_tool" | "prompt_only"
# ----------------------------------------------------------------------------

@dataclass
class Model:
    alias: str
    model_id: str
    backend: str
    in_per_mtok: float
    out_per_mtok: float
    supports_temperature: bool = True


MODELS: dict[str, Model] = {
    "nemotron":  Model("nemotron",  "nvidia.nemotron-nano-12b-v2",                  "output_config", 0.20, 0.60),
    "nova2lite": Model("nova2lite", "us.amazon.nova-2-lite-v1:0",                   "forced_tool",   0.33, 2.75),
    "qwen":      Model("qwen",      "qwen.qwen3-vl-235b-a22b",                      "prompt_only",   0.53, 2.66),
    "haiku45":   Model("haiku45",   "us.anthropic.claude-haiku-4-5-20251001-v1:0",  "output_config", 1.10, 5.50),
    "sonnet5":   Model("sonnet5",   "us.anthropic.claude-sonnet-5",                 "forced_tool",   2.20, 11.00),
}

# ----------------------------------------------------------------------------
# Extraction schema — 8 fields, visual_notes first.
# movie_title is a plain string ("" when illegible) rather than a nullable
# union: strict schema modes across these models handle unions inconsistently.
# ----------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "visual_notes": {
            "type": "string",
            "description": "What is physically visible: how the item is mounted or "
                           "supported, and where on the poster you read the title from.",
        },
        "movie_title": {
            "type": "string",
            "description": "The film title. Empty string if not legible or not present.",
        },
        "material_type": {"type": "string", "enum": MATERIAL_TYPES},
        "defects": {"type": "array", "items": {"type": "string", "enum": DEFECTS}},
        "defect_evidence": {
            "type": "string",
            "description": "What is actually visible. Empty string if no defects.",
        },
        "quantity": {"type": "integer"},
        "confidence_material_type": {"type": "number"},
        "confidence_movie_title": {"type": "number"},
    },
    "required": [
        "visual_notes", "movie_title", "material_type", "defects",
        "defect_evidence", "quantity",
        "confidence_material_type", "confidence_movie_title",
    ],
    "additionalProperties": False,
}

FIELD_ORDER = SCHEMA["required"]

# ----------------------------------------------------------------------------
# Prompt.  Every rule here prevents a specific failure observed in the corpus.
# ----------------------------------------------------------------------------

SYSTEM_PROMPT = f"""\
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

`quantity` is the number of discrete units of the subject's material type, \
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

## Confidence

`confidence_material_type` and `confidence_movie_title` are your calibrated \
probability that each value is correct, 0.0 to 1.0. Be honest: a low score routes \
the record to a human, which is cheap. A confident wrong answer is expensive.

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

USER_TEXT = (
    "Extract the record for the material in this photograph. "
    "Return only the structured fields."
)

PROMPT_ONLY_SUFFIX = (
    "\n\nReturn ONLY a single JSON object, no prose and no markdown fences, with "
    "exactly these keys in this order: " + ", ".join(FIELD_ORDER) + ".\n"
    "`material_type` must be exactly one of: " + " | ".join(MATERIAL_TYPES) + "\n"
    "`defects` is an array whose members must each be one of: " + " | ".join(DEFECTS)
)


def prompt_hash() -> str:
    blob = SYSTEM_PROMPT + json.dumps(SCHEMA, sort_keys=True) + PROMPT_ONLY_SUFFIX
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ----------------------------------------------------------------------------
# Images
# ----------------------------------------------------------------------------

FRAMING_BY_WIDTH = {629: "close", 768: "wide"}


def fetch_image(url: str, photo_id: str, cache_dir: Path, local_dir: Path | None) -> bytes:
    """Local copy if we have one, else cache, else fetch from S3."""
    if local_dir:
        p = local_dir / f"{photo_id}.jpg"
        if p.is_file() and p.stat().st_size > 0:
            return p.read_bytes()

    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{photo_id}.jpg"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_bytes()

    import requests
    resp = requests.get(url, timeout=60, headers={"User-Agent": "mmvision/1.0"})
    resp.raise_for_status()
    if not resp.content:
        raise ValueError(f"empty body for {url}")
    cached.write_bytes(resp.content)
    return resp.content


def image_framing(data: bytes) -> tuple[str, int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
    return FRAMING_BY_WIDTH.get(w, "unknown"), w, h


# ----------------------------------------------------------------------------
# Bedrock call — three back-ends
# ----------------------------------------------------------------------------

TOOL_NAME = "emit_material_record"
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Bedrock cap is 5 MB base64


def _messages(img: bytes, framing: str, extra: str = "") -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"image": {"format": "jpeg", "source": {"bytes": img}}},
            {"text": USER_TEXT + FRAMING_HINT.get(framing, "") + extra},
        ],
    }]


def _build_converse_kwargs(model: Model, img: bytes, framing: str,
                           extra_user_text: str, use_temp: bool) -> dict[str, Any]:
    system = [{"text": SYSTEM_PROMPT + (
        PROMPT_ONLY_SUFFIX if model.backend == "prompt_only" else "")}]
    kwargs: dict[str, Any] = {
        "modelId": model.model_id,
        "messages": _messages(img, framing, extra_user_text),
        "system": system,
        "inferenceConfig": {"maxTokens": 1500},
    }
    if use_temp:
        kwargs["inferenceConfig"]["temperature"] = 0.0

    if model.backend == "output_config":
        kwargs["outputConfig"] = {
            "textFormat": {
                "type": "json_schema",
                "structure": {"jsonSchema": {
                    "name": "material_record",
                    "schema": json.dumps(SCHEMA),
                }},
            }
        }
    elif model.backend == "forced_tool":
        kwargs["toolConfig"] = {
            "tools": [{"toolSpec": {
                "name": TOOL_NAME,
                "description": "Emit the structured record for this material.",
                "inputSchema": {"json": SCHEMA},
            }}],
            "toolChoice": {"tool": {"name": TOOL_NAME}},
        }
    return kwargs


def call_bedrock(client, model: Model, img: bytes, framing: str,
                 extra_user_text: str = "") -> tuple[dict, dict]:
    """Returns (raw_response, usage). Raises on API error.

    Each call captures `use_temp` locally and never re-reads model.supports_temperature
    mid-call: under concurrent workers, a losing thread that reads the flag after a
    winning thread already flipped it would otherwise retry with a kwargs dict built
    from the *original* (temperature-included) state and fail a second time uncaught.
    The shared flag is still updated as a best-effort hint so later calls in this run
    skip the doomed first attempt, but correctness never depends on it.
    """
    use_temp = model.supports_temperature
    kwargs = _build_converse_kwargs(model, img, framing, extra_user_text, use_temp)
    try:
        resp = client.converse(**kwargs)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "temperature" in msg.lower() and use_temp:
            model.supports_temperature = False
            kwargs = _build_converse_kwargs(model, img, framing, extra_user_text, False)
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
    for k in FIELD_ORDER:
        if k not in rec:
            errs.append(f"missing key '{k}'")
    mt = rec.get("material_type")
    if mt is not None and mt not in MATERIAL_TYPES:
        errs.append(f"material_type '{mt}' is not one of the {len(MATERIAL_TYPES)} allowed values")
    d = rec.get("defects")
    if d is not None:
        if not isinstance(d, list):
            errs.append("defects must be an array")
        else:
            for x in d:
                if x not in DEFECTS:
                    errs.append(f"defect '{x}' is not an allowed value")
    q = rec.get("quantity")
    if q is not None and not isinstance(q, int):
        try:
            rec["quantity"] = int(q)
        except (TypeError, ValueError):
            errs.append(f"quantity '{q}' is not an integer")
    return errs


def cost_usd(model: Model, usage: dict) -> float:
    return (usage.get("inputTokens", 0) / 1e6 * model.in_per_mtok
            + usage.get("outputTokens", 0) / 1e6 * model.out_per_mtok)


# ----------------------------------------------------------------------------
# Per-row extraction, with one repair retry
# ----------------------------------------------------------------------------

@dataclass
class RowResult:
    photo_id: str
    record: dict = field(default_factory=dict)
    framing: str = "unknown"
    in_tok: int = 0
    out_tok: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    retries: int = 0
    error: str = ""
    cached: bool = False


def response_cache_path(resp_cache_dir: Path, alias: str, photo_id: str, phash: str) -> Path:
    return resp_cache_dir / alias / f"{photo_id}__{phash}.json"


def extract_one(client, model: Model, url: str, photo_id: str,
                cache_dir: Path, local_dir: Path | None,
                resp_cache_dir: Path | None = None, phash: str = "") -> RowResult:
    r = RowResult(photo_id=photo_id)
    t0 = time.time()

    cache_file = (response_cache_path(resp_cache_dir, model.alias, photo_id, phash)
                  if resp_cache_dir else None)
    if cache_file and cache_file.is_file():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            r.record = data["record"]
            r.framing = data["framing"]
            r.in_tok = data["in_tok"]
            r.out_tok = data["out_tok"]
            r.cost = data["cost"]
            r.retries = data.get("retries", 0)
            r.cached = True
            r.latency_ms = int((time.time() - t0) * 1000)
            return r
        except Exception:  # noqa: BLE001
            pass  # corrupt/partial cache entry — fall through and re-call

    try:
        img = fetch_image(url, photo_id, cache_dir, local_dir)
        if len(img) > MAX_IMAGE_BYTES:
            raise ValueError(f"image is {len(img)/1e6:.1f} MB, over the 5 MB Bedrock cap")
        r.framing, _, _ = image_framing(img)

        extra = ""
        for attempt in range(2):
            resp, usage = call_bedrock(client, model, img, r.framing, extra)
            r.in_tok += usage.get("inputTokens", 0)
            r.out_tok += usage.get("outputTokens", 0)
            r.cost += cost_usd(model, usage)
            try:
                rec = parse_response(resp, model.backend)
                errs = validate(rec)
            except Exception as exc:  # noqa: BLE001
                rec, errs = {}, [str(exc)]
            if not errs:
                r.record = rec
                break
            if attempt == 0:
                r.retries += 1
                extra = ("\n\nYour previous response was rejected: "
                         + "; ".join(errs)
                         + ". Return a corrected JSON object only.")
            else:
                r.record = rec
                r.error = "parse_failure: " + "; ".join(errs)
    except Exception as exc:  # noqa: BLE001
        r.error = f"{type(exc).__name__}: {exc}"
    r.latency_ms = int((time.time() - t0) * 1000)

    if cache_file and not r.error:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "record": r.record, "framing": r.framing,
            "in_tok": r.in_tok, "out_tok": r.out_tok,
            "cost": r.cost, "retries": r.retries,
        }), encoding="utf-8")
    return r


# ----------------------------------------------------------------------------
# Output assembly
# ----------------------------------------------------------------------------

PRED_COLS = [
    "pred_movie_title", "pred_material_type", "pred_material_condition",
    "pred_quantity", "pred_defects", "pred_defect_evidence",
    "pred_confidence_material_type", "pred_confidence_movie_title",
    "tokens_consumed", "cost_usd",
]
DIAG_COLS = [
    "diag_visual_notes", "diag_framing", "diag_model_id",
    "diag_input_tokens", "diag_output_tokens", "diag_latency_ms",
    "diag_parse_retries", "diag_error",
]


def result_row(r: RowResult, model: Model) -> dict:
    rec = r.record or {}
    defects = rec.get("defects") or []
    return {
        "pred_movie_title": (rec.get("movie_title") or "").strip(),
        "pred_material_type": rec.get("material_type", ""),
        "pred_material_condition": ("damaged" if defects else "good") if rec else "",
        "pred_quantity": rec.get("quantity", ""),
        "pred_defects": "|".join(defects),
        "pred_defect_evidence": (rec.get("defect_evidence") or "").strip(),
        "pred_confidence_material_type": rec.get("confidence_material_type", ""),
        "pred_confidence_movie_title": rec.get("confidence_movie_title", ""),
        "tokens_consumed": r.in_tok + r.out_tok,
        "cost_usd": round(r.cost, 6),
        "diag_visual_notes": (rec.get("visual_notes") or "").strip(),
        "diag_framing": r.framing,
        "diag_model_id": model.model_id,
        "diag_input_tokens": r.in_tok,
        "diag_output_tokens": r.out_tok,
        "diag_latency_ms": r.latency_ms,
        "diag_parse_retries": r.retries,
        "diag_error": r.error,
    }


def write_workbook(path: Path, sheets: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with __import__("pandas").ExcelWriter(path, engine="openpyxl") as xl:
        for name, df in sheets.items():
            safe = re.sub(r"[\[\]:*?/\\]", "-", str(name))[:31]
            df.to_excel(xl, sheet_name=safe, index=False)
            ws = xl.sheets[safe]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col[:200] if c.value), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 60)


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def make_client(service: str):
    import boto3
    return boto3.client(service, region_name=os.environ.get("AWS_REGION", "us-east-1"))


def cmd_preflight(args) -> int:
    """Resolves every alias, reports entitlement, prints a paste-ready config.
    Control-plane reads only — never calls converse/invoke, so this spends
    nothing regardless of IAM policy or entitlement state."""
    print(f"prompt hash : {prompt_hash()}")
    token = os.environ.get(ENV_VAR)
    if not token:
        print(f"\nFAIL  {ENV_VAR} is not set (and no {LEGACY_ENV_VAR} to alias from).")
        print(f"      Put it in your project-root .env:  {ENV_VAR}=<your bedrock api key>")
        return 1
    via_legacy = os.environ.get(LEGACY_ENV_VAR) == token
    print(f"auth mode   : Bedrock API key ({ENV_VAR}, {len(token)} chars"
          f"{', aliased from ' + LEGACY_ENV_VAR if via_legacy else ''})")
    if os.environ.get("AWS_PROFILE"):
        print(f"note        : AWS_PROFILE={os.environ['AWS_PROFILE']} is set but the "
              "bearer token takes precedence for Bedrock.")

    import boto3
    ver = _boto3_version_tuple()
    print(f"boto3       : {boto3.__version__}", end="")
    if ver < MIN_BOTO3_VERSION:
        print(f"  FAIL — below the {'.'.join(map(str, MIN_BOTO3_VERSION))} floor for "
              f"{ENV_VAR} support. Calls will silently ignore the token and fail with a "
              "confusing credentials error. Run: pip install -U boto3")
        return 1
    print("  OK")

    bedrock = boto3.client("bedrock", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    vision_ids: set[str] = set()
    try:
        summaries = bedrock.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]
        vision_ids = {m["modelId"] for m in summaries if "IMAGE" in m.get("inputModalities", [])}
        print(f"catalog     : {len(vision_ids)} vision-capable model(s) visible in {bedrock.meta.region_name}")
    except Exception as exc:  # noqa: BLE001
        print(f"catalog     : SKIPPED — list_foundation_models refused ({str(exc).splitlines()[0][:80]})")

    us_profiles: dict[str, str] = {}
    try:
        profs = bedrock.list_inference_profiles(typeEquals="SYSTEM_DEFINED", maxResults=1000)
        for p in profs.get("inferenceProfileSummaries", []):
            pid = p.get("inferenceProfileId", "")
            if not pid.startswith("us."):
                continue
            for m in p.get("models", []):
                arn = m.get("modelArn", "")
                us_profiles[arn.rsplit("/", 1)[-1]] = pid
        print(f"profiles    : {len(us_profiles)} 'us.' system-defined inference profile(s) found")
    except Exception as exc:  # noqa: BLE001
        print(f"profiles    : SKIPPED — list_inference_profiles refused ({str(exc).splitlines()[0][:80]})")

    print(f"\n{'alias':<10} {'model id':<50} {'region':<10} {'auth':<12} {'entitle':<12} {'agreement':<10}")
    print("-" * 100)
    resolvable = 0
    for alias, m in MODELS.items():
        if args.models and alias not in args.models.split(","):
            continue
        base_id = m.model_id[3:] if m.model_id.startswith("us.") else m.model_id
        cols = ["?", "?", "?", "?"]
        try:
            av = bedrock.get_foundation_model_availability(modelId=base_id)
            cols = [
                av.get("regionAvailability", "?"),
                av.get("authorizationStatus", "?"),
                av.get("entitlementAvailability", "?"),
                av.get("agreementAvailability", {}).get("status", "?"),
            ]
            if all(c in ("AVAILABLE", "AUTHORIZED") for c in cols):
                resolvable += 1
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "AccessDenied" in msg or "not authorized" in msg:
                cols = ["UNKNOWN"] * 4
                print(f"{alias:<10} {m.model_id:<50} "
                      "get_foundation_model_availability not authorized for this "
                      "principal — this is expected for a long-term API key scoped to "
                      "bedrock:InvokeModel only. Cannot confirm entitlement without spending; "
                      "verify in the console instead.")
                continue
            cols = ["ERROR"] * 4
        print(f"{alias:<10} {m.model_id:<50} {cols[0]:<10} {cols[1]:<12} {cols[2]:<12} {cols[3]:<10}")
        if "anthropic" in m.model_id and cols[1] not in ("AUTHORIZED",):
            print(f"           note: Claude models require the one-time Anthropic First Time "
                  "Use form (Bedrock console → Model access) before {alias} can be invoked. "
                  "This is account-level, not credential-level.")

    print(f"\n{resolvable}/{len(MODELS)} model(s) confirmed available + authorized + "
          "entitled + agreed. This check made zero converse/invoke calls — $0 spent.")

    print("\n--- paste-ready config -----------------------------------------")
    print("MODELS = {")
    for alias, m in MODELS.items():
        print(f'    "{alias}": "{m.model_id}",  # backend={m.backend}')
    print("}")
    print("------------------------------------------------------------------")
    return 0


def cmd_validate(args) -> int:
    import pandas as pd
    df = pd.read_csv(args.csv)
    if "custom_movie_title" not in df or "movie_title" not in df:
        print("CSV lacks movie_title / custom_movie_title — nothing to validate.")
        return 0
    a = df["movie_title"].fillna("").str.strip()
    b = df["custom_movie_title"].fillna("").str.strip()
    bad = df[a != b]
    if bad.empty:
        print(f"No ground-truth title mismatches in {len(df)} rows.")
        return 0
    print(f"{len(bad)} suspect row(s) — movie_title != custom_movie_title:\n")
    for _, r in bad.iterrows():
        print(f"  photo {r['photo_id']}: movie_title={r['movie_title']!r} "
              f"custom={r['custom_movie_title']!r}")
    print("\nThese are likely dropdown carry-over errors. Exclude from title scoring.")
    return 0


def run_model(df, model: Model, args) -> list[dict]:
    import pandas as pd  # noqa: F401
    if "photo_id" in df.columns:
        dupes = df["photo_id"][df["photo_id"].duplicated()].astype(str).unique().tolist()
        if dupes:
            print(f"warning: duplicate photo_id(s) in input CSV, processing both rows: {dupes}")

    client = make_client("bedrock-runtime")
    cache = Path(args.cache)
    resp_cache = Path(args.cache).parent / "responses"
    phash = prompt_hash()
    local = Path(args.local_images) if args.local_images else None
    if local and not local.is_dir():
        local = None

    rows: list[dict | None] = [None] * len(df)
    todo = list(df.itertuples())
    done = cache_hits = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(extract_one, client, model,
                      getattr(t, "image_path"), str(getattr(t, "photo_id")),
                      cache, local, resp_cache, phash): i
            for i, t in enumerate(todo)
        }
        for fut in cf.as_completed(futs):
            i = futs[fut]
            r = fut.result()
            rows[i] = result_row(r, model)
            done += 1
            cache_hits += int(r.cached)
            if r.error:
                print(f"  [{done}/{len(todo)}] {r.photo_id} ERROR {r.error[:80]}")
            elif done % 10 == 0 or done == len(todo):
                print(f"  [{done}/{len(todo)}] ... ({cache_hits} cache hit(s) so far)")
    return [r for r in rows if r is not None]


def cmd_process(args) -> int:
    import pandas as pd
    if args.model not in MODELS:
        print(f"unknown model alias {args.model!r}. Known: {', '.join(MODELS)}")
        return 1
    model = MODELS[args.model]

    df = pd.read_csv(args.csv)
    if args.limit:
        df = df.head(args.limit)
    print(f"{len(df)} rows | model={model.alias} ({model.model_id}) "
          f"| backend={model.backend}")

    t0 = time.time()
    results = run_model(df, model, args)
    out = pd.concat([df.reset_index(drop=True), pd.DataFrame(results)], axis=1)
    if args.no_diagnostics:
        out = out.drop(columns=[c for c in DIAG_COLS if c in out.columns])

    dest = Path(args.out) if args.out else Path(f"out/{model.alias}/results.xlsx")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest.with_suffix(".csv"), index=False)

    run_meta = pd.DataFrame([{
        "model_alias": model.alias, "model_id": model.model_id,
        "backend": model.backend, "prompt_hash": prompt_hash(),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rows": len(out),
        "total_tokens": int(out["tokens_consumed"].sum()),
        "total_cost_usd": round(float(out["cost_usd"].sum()), 4),
        "errors": int((out.get("diag_error", pd.Series([""] * len(out))) != "").sum()),
        "elapsed_s": round(time.time() - t0, 1),
    }])
    write_workbook(dest, {"Results": out, "Run": run_meta})

    print(f"\nwrote {dest} and {dest.with_suffix('.csv')}")
    print(f"tokens {int(out['tokens_consumed'].sum()):,} | "
          f"cost ${out['cost_usd'].sum():.4f} | {time.time()-t0:.0f}s")
    return 0


def norm_title(s: str) -> str:
    s = str(s or "").casefold().strip()
    s = re.sub(r"\(\d{4}\)", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load_known_issues(path: str = "known_issues.yaml") -> dict:
    p = Path(path)
    if not p.is_file():
        return {}
    import yaml
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def class_metrics(true_s, pred_s, labels) -> tuple[float, dict[str, tuple[float, float, float, int]]]:
    """Per-class precision/recall/F1/support, plus macro-F1 over classes with support > 0."""
    f1s, per_class = [], {}
    for lab in labels:
        tp = int(((true_s == lab) & (pred_s == lab)).sum())
        fp = int(((true_s != lab) & (pred_s == lab)).sum())
        fn = int(((true_s == lab) & (pred_s != lab)).sum())
        sup = int((true_s == lab).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if sup:
            f1s.append(f1)
            per_class[lab] = (prec, rec, f1, sup)
    macro_f1 = sum(f1s) / len(f1s) if f1s else 0.0
    return macro_f1, per_class


FRAMINGS = ["close", "wide", "unknown"]


def cmd_eval(args) -> int:
    import pandas as pd
    aliases = [a.strip() for a in args.models.split(",") if a.strip()]
    unknown = [a for a in aliases if a not in MODELS]
    if unknown:
        print(f"unknown alias(es): {unknown}. Known: {', '.join(MODELS)}")
        return 1

    df = pd.read_csv(args.csv)
    if args.limit:
        df = df.head(args.limit)
    if ("material_type" not in df or df["material_type"].isna().all()
            or "movie_title" not in df or df["movie_title"].isna().all()):
        print("CSV has no movie_title/material_type ground truth — use `process` instead.")
        return 1

    known = load_known_issues()
    for row in known.get("location_mismatch", []):
        pid = str(row["photo_id"])
        if pid in df["photo_id"].astype(str).values:
            print(f"known issue (location, not scored): photo {pid}: {row.get('reason', '')}")
    for row in known.get("download_failures", []):
        pid = str(row["photo_id"])
        if pid in df["photo_id"].astype(str).values:
            print(f"known issue (download): photo {pid}: {row.get('reason', '')}")

    # Ground-truth rows to exclude from title scoring (carry-over errors).
    # Detected generically (movie_title != custom_movie_title, works on any CSV)
    # and unioned with known_issues.yaml entries for this specific CSV.
    if "custom_movie_title" in df:
        a = df["movie_title"].fillna("").str.strip()
        b = df["custom_movie_title"].fillna("").str.strip()
        title_excluded = set(df.loc[a != b, "photo_id"].astype(str))
    else:
        title_excluded = set()
    title_excluded |= {str(r["photo_id"]) for r in known.get("title_mismatch", [])}
    if title_excluded:
        print(f"excluding {len(title_excluded)} row(s) from title scoring "
              f"(movie_title != custom_movie_title): {sorted(title_excluded)}")

    sheets: dict[str, Any] = {}
    summary_rows, confusion_rows = [], []

    for alias in aliases:
        model = MODELS[alias]
        print(f"\n=== {alias} ({model.backend}) ===")
        res = run_model(df, model, args)
        out = pd.concat([df.reset_index(drop=True), pd.DataFrame(res)], axis=1)
        sheets[alias] = out

        in_scope = out[~out["material_type"].isin(OUT_OF_SCOPE)]
        labels = sorted(set(in_scope["material_type"]) | set(in_scope["pred_material_type"]))
        macro_f1, per_class = class_metrics(in_scope["material_type"], in_scope["pred_material_type"], labels)

        for tl, pl in zip(in_scope["material_type"], in_scope["pred_material_type"]):
            confusion_rows.append({"model": alias, "true_label": tl, "pred_label": pl})

        # Framing breakdown — §4: expected to be the dominant error axis.
        framing_acc = {}
        for fr in FRAMINGS:
            sub = in_scope[in_scope["diag_framing"] == fr]
            if len(sub):
                framing_acc[fr] = (float((sub["material_type"] == sub["pred_material_type"]).mean()), len(sub))

        # Rejection accuracy — does the model honestly label Popcorn Tub/Drink
        # Cup/etc. rather than forcing a poster class? (§10.1)
        oos = out[out["material_type"].isin(OUT_OF_SCOPE)]
        rejection_acc = (float((oos["material_type"] == oos["pred_material_type"]).mean()), len(oos)) if len(oos) else None

        scored = out[~out["photo_id"].astype(str).isin(title_excluded)]
        exact = float((scored["movie_title"].fillna("").str.strip()
                       == scored["pred_movie_title"].fillna("").str.strip()).mean())
        fuzzy = float((scored["movie_title"].map(norm_title)
                       == scored["pred_movie_title"].map(norm_title)).mean())
        abstain = float((scored["pred_movie_title"].fillna("").str.strip() == "").mean())

        maj = in_scope["material_type"].value_counts()
        baseline = float(maj.iloc[0] / len(in_scope)) if len(in_scope) else 0.0

        summary_rows.append({
            "model": alias,
            "backend": model.backend,
            "material_macro_f1": round(macro_f1, 4),
            "material_accuracy": round(float((in_scope["material_type"] == in_scope["pred_material_type"]).mean()), 4),
            "MAJORITY_BASELINE": round(baseline, 4),
            "title_exact": round(exact, 4),
            "title_fuzzy": round(fuzzy, 4),
            "title_abstain": round(abstain, 4),
            "rejection_accuracy": (f"{rejection_acc[0]:.3f}(n={rejection_acc[1]})" if rejection_acc else "n/a"),
            "parse_failures": int(out["diag_error"].str.startswith("parse_failure").sum()),
            "errors": int((out["diag_error"] != "").sum()),
            "tokens": int(out["tokens_consumed"].sum()),
            "cost_usd": round(float(out["cost_usd"].sum()), 4),
            "proj_monthly_at_550_day": round(float(out["cost_usd"].sum()) / max(len(out), 1) * 550 * 30, 2),
            "mean_latency_ms": int(out["diag_latency_ms"].mean()),
            **{f"acc[{fr}]({n})": round(a, 3) for fr, (a, n) in framing_acc.items()},
            **{f"recall[{k}]({v[3]})": round(v[1], 3) for k, v in per_class.items()},
        })
        s = summary_rows[-1]
        print(f"  macro-F1 {s['material_macro_f1']:.3f} | acc {s['material_accuracy']:.3f} "
              f"(baseline {s['MAJORITY_BASELINE']:.3f}) | title exact {s['title_exact']:.3f} "
              f"| ${s['cost_usd']:.4f}")

    # Explicit majority-class baseline row — computed from this CSV's ground
    # truth, never hardcoded. §10.1: print beside every result so nobody
    # misreads accuracy as good without it.
    full_in_scope = df[~df["material_type"].isin(OUT_OF_SCOPE)]
    if len(full_in_scope):
        maj_label = full_in_scope["material_type"].value_counts().idxmax()
        baseline_labels = sorted(full_in_scope["material_type"].unique())
        baseline_pred = pd.Series([maj_label] * len(full_in_scope), index=full_in_scope.index)
        b_f1, b_per_class = class_metrics(full_in_scope["material_type"], baseline_pred, baseline_labels)
        b_acc = float((full_in_scope["material_type"] == maj_label).mean())
        summary_rows.insert(0, {
            "model": "BASELINE (always majority class)",
            "backend": "", "material_macro_f1": round(b_f1, 4), "material_accuracy": round(b_acc, 4),
            "MAJORITY_BASELINE": round(b_acc, 4), "title_exact": "", "title_fuzzy": "", "title_abstain": "",
            "rejection_accuracy": "", "parse_failures": 0, "errors": 0, "tokens": 0, "cost_usd": 0.0,
            "proj_monthly_at_550_day": 0.0, "mean_latency_ms": 0,
            **{f"recall[{k}]({v[3]})": round(v[1], 3) for k, v in b_per_class.items()},
        })

    conf = (pd.DataFrame(confusion_rows)
            .groupby(["model", "true_label", "pred_label"]).size()
            .reset_index(name="count"))

    def build_error_rows(err_ids: set[str]):
        base_cols = ["photo_id", "image_path", "movie_title", "material_type"]
        rows = df[df["photo_id"].astype(str).isin(err_ids)][base_cols].copy()
        for alias in aliases:
            d = sheets[alias].set_index(sheets[alias]["photo_id"].astype(str))
            idx = rows["photo_id"].astype(str)
            rows[f"{alias}_type"] = idx.map(d["pred_material_type"]).values
            rows[f"{alias}_title"] = idx.map(d["pred_movie_title"]).values
            rows[f"{alias}_notes"] = idx.map(d["diag_visual_notes"]).values
        return rows

    type_err_ids = set()
    for alias in aliases:
        d = sheets[alias]
        type_err_ids |= set(d.loc[d["material_type"] != d["pred_material_type"], "photo_id"].astype(str))
    type_errors = build_error_rows(type_err_ids)

    title_err_ids = set()
    for alias in aliases:
        d = sheets[alias]
        scored_d = d[~d["photo_id"].astype(str).isin(title_excluded)]
        mism = (scored_d["movie_title"].fillna("").str.strip()
                != scored_d["pred_movie_title"].fillna("").str.strip())
        title_err_ids |= set(scored_d.loc[mism, "photo_id"].astype(str))
    title_errors = build_error_rows(title_err_ids)

    summary = pd.DataFrame(summary_rows)
    cost = summary[["model", "tokens", "cost_usd", "proj_monthly_at_550_day", "mean_latency_ms"]]

    out_dir = Path(args.out) if args.out else Path("out")
    dest = out_dir / "eval.xlsx"
    write_workbook(dest, {"Summary": summary, **sheets, "Confusion": conf,
                          "Errors": type_errors, "Errors_Title": title_errors, "Cost": cost})
    write_summary_md(out_dir / "summary.md", summary, len(df), title_excluded)
    local_dir = Path(args.local_images) if args.local_images else None
    write_errors_html(out_dir / "errors_material_type.html", type_errors, aliases, local_dir,
                      Path(args.cache), "material_type miss")
    write_errors_html(out_dir / "errors_movie_title.html", title_errors, aliases, local_dir,
                      Path(args.cache), "movie_title miss")
    print(f"\nwrote {dest}, {out_dir / 'summary.md'}, "
          f"{out_dir / 'errors_material_type.html'}, {out_dir / 'errors_movie_title.html'}")
    print(f"total cost ${summary['cost_usd'].sum():.4f}")
    return 0


def write_summary_md(path: Path, summary, n_rows: int, title_excluded: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    core_cols = ["model", "backend", "material_macro_f1", "material_accuracy", "MAJORITY_BASELINE",
                 "title_exact", "title_fuzzy", "title_abstain", "rejection_accuracy",
                 "parse_failures", "cost_usd", "proj_monthly_at_550_day", "mean_latency_ms"]
    core_cols = [c for c in core_cols if c in summary.columns]
    framing_cols = sorted(c for c in summary.columns if c.startswith("acc["))
    recall_cols = sorted(c for c in summary.columns if c.startswith("recall["))

    lines = ["# mmvision eval summary", "", f"Reference set: {n_rows} rows.", ""]
    if title_excluded:
        lines.append(f"{len(title_excluded)} row(s) excluded from title scoring "
                      f"(known ground-truth title errors): {sorted(title_excluded)}.")
        lines.append("")
    lines.append("Condition and quantity have no ground truth in this reference set — "
                  "distributions only, no accuracy figure is computed for either.")
    lines.append("")
    lines.append("With as few as 2 examples for some material_type classes, one error swings "
                 "that class's recall from 100% to 50%. Support counts are shown in every "
                 "per-class column name; treat classes with support < ~30 as a sanity check, "
                 "not a validated result.")
    lines.append("")
    lines.append("## Core metrics (MAJORITY_BASELINE row shows what a model that always "
                 "guesses the majority class would score — never read accuracy without it)")
    lines.append("")
    lines.append(summary[core_cols].to_markdown(index=False))
    lines.append("")
    if framing_cols:
        lines.append("## Material-type accuracy by framing (§4 — expected dominant error axis)")
        lines.append("")
        lines.append(summary[["model"] + framing_cols].to_markdown(index=False))
        lines.append("")
    if recall_cols:
        lines.append("## Per-class recall, with support")
        lines.append("")
        lines.append(summary[["model"] + recall_cols].to_markdown(index=False))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _image_src(photo_id: str, local_dir: Path | None, cache_dir: Path, image_path: str) -> str:
    import base64
    for candidate in ([local_dir / f"{photo_id}.jpg"] if local_dir else []) + [cache_dir / f"{photo_id}.jpg"]:
        if candidate.is_file():
            return "data:image/jpeg;base64," + base64.b64encode(candidate.read_bytes()).decode("ascii")
    return image_path  # fall back to the remote URL


def write_errors_html(path: Path, errors, aliases: list[str], local_dir: Path | None,
                      cache_dir: Path, label: str = "miss") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_html = []
    for _, r in errors.iterrows():
        src = _image_src(str(r["photo_id"]), local_dir, cache_dir, r["image_path"])
        preds = "".join(
            f"<td><b>{alias}</b><br>type: {r.get(f'{alias}_type', '')}<br>"
            f"title: {r.get(f'{alias}_title', '')}<br>"
            f"<i>{r.get(f'{alias}_notes', '')}</i></td>"
            for alias in aliases
        )
        rows_html.append(
            f"<tr><td><img src='{src}' style='max-height:220px'><br>"
            f"photo {r['photo_id']}</td>"
            f"<td>truth<br>type: {r['material_type']}<br>title: {r['movie_title']}</td>"
            f"{preds}</tr>"
        )
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>mmvision — {label} errors</title>
<style>
table {{ border-collapse: collapse; width: 100%; }}
td {{ border: 1px solid #ccc; padding: 8px; vertical-align: top; font-family: sans-serif; font-size: 13px; }}
</style></head><body>
<h1>mmvision — every {label} ({len(errors)} photo(s))</h1>
<table>{''.join(rows_html)}</table>
</body></html>"""
    path.write_text(html, encoding="utf-8")


# ----------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(prog="mmvision", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to .env (default: search upward from cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--csv", required=True)
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--workers", type=int, default=8)
        p.add_argument("--cache", default=".mmvision-cache/images")
        p.add_argument("--local-images", default="downloads",
                       help="folder of <photo_id>.jpg to use instead of fetching")
        p.add_argument("--out")

    p = sub.add_parser("preflight"); p.add_argument("--models")
    p = sub.add_parser("validate"); p.add_argument("--csv", required=True)
    p = sub.add_parser("process"); common(p)
    p.add_argument("--model", required=True)
    p.add_argument("--no-diagnostics", action="store_true")
    p = sub.add_parser("eval"); common(p)
    p.add_argument("--models", required=True)

    args = ap.parse_args()

    env_path = find_and_load_env(args.env)
    if env_path:
        print(f"loaded env  : {env_path}")
    elif args.cmd in {"preflight", "process", "eval"}:
        print("warning: no .env found; relying on the ambient environment")

    return {"preflight": cmd_preflight, "validate": cmd_validate,
            "process": cmd_process, "eval": cmd_eval}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

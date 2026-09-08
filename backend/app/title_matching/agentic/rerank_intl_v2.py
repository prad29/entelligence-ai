"""International v2's independent verification pass: an independent re-check
of the first pass's candidate pick via a direct Bedrock Converse call.
Recovered from PR #53/#55 (commit b84eada)'s rerank.py, with three changes:

1. Trigger is intl-v2-only now (the original fired for every
   market="international" row, including v1 and the external API) -- see
   runner_intl_v2.py, the only call site.
2. Cap/model are settings (AGENTIC_INTL_V2_RERANK_MAX_CONFIDENCE /
   AGENTIC_INTL_V2_RERANK_MODEL), not hardcoded literals.
3. The call is now logged via log_llm_call (PATH_BEDROCK_CONVERSE) -- a real
   cost/latency blind spot in the original.

Deliberately NOT a second claude-sandbox subprocess call: this step only
needs to reason over evidence already gathered (first pass's identified
film + reasoning + the full candidate list) — no web tools, no MCP, no
filesystem. Calls Bedrock Converse directly, mirroring the client-creation
pattern already used for embeddings in semantic_index.py::_get_bedrock_client
and for structured extraction in calendar_extract/bedrock.py's forced_tool
backend (Sonnet 5's own backend there), but with a single forced tool call
for structured output.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import Optional

from app.observability.bedrock_usage import extract_converse_usage
from app.observability.constants import (
    CALLER_PORTAL,
    PATH_BEDROCK_CONVERSE,
    STATUS_FAILURE,
    STATUS_SUCCESS,
    TASK_INTL_MAPPING,
)
from app.observability.context import LlmCallContext
from app.observability.llm_logging import log_llm_call
from app.title_matching.types import TitleMatchResult

logger = logging.getLogger(__name__)

_VERDICT_TOOL_NAME = "report_verdict"

# Restated from prompt_builder_intl_v2.py's Section B -- this verification
# pass never touches claude-sandbox and so can't share that prompt directly.
_VERIFY_SYSTEM_PROMPT = """\
You are verifying another agent's international movie-title-match pick \
before it is finalized. You will be given: the original scraped listing \
(title/country/show_date), the first pass's identified film, its chosen \
candidate id/title, its reasoning, and the full list of DB + Vespa \
candidates that were available to it. Your job is to CONFIRM the pick, \
OVERRULE it with a better candidate from the SAME list, or declare \
NO_DB_MATCH if no candidate fits — and, when you can, supply the correct \
titles even for a NO_DB_MATCH verdict.

Apply these rules:
- movie_title in your output MUST be the literal stored title string of the \
candidate row whose id you emit — never a paraphrase, and never the first \
pass's prose description of the film.
- A placeholder or implausible release_date on a candidate (e.g. a \
suspiciously round YYYY-01-01 that doesn't match the film's real release \
pattern) weakens that candidate; it does not make the field neutral.
- Never break a tie between two or more indistinguishable candidates using \
relevance score alone. Prefer NO_DB_MATCH over a confident-sounding coin flip.
- When you are confident in a film's real-world identity but no candidate id \
fits, ALWAYS still populate movie_title (and alternate_movie_title / \
rerelease_lookup_title where relevant) so a downstream DB lookup has \
something concrete to search for.
- movie_master_id in your verdict MUST be an id that appears in the supplied \
candidate list, or 0 for NO_DB_MATCH. Never invent an id.

Call the report_verdict tool with your answer. Do not output anything else."""

_VERDICT_TOOL_SPEC = {
    "toolSpec": {
        "name": _VERDICT_TOOL_NAME,
        "description": "Report the verification verdict for a title match.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "verdict": {
                        "type": "string",
                        "enum": ["CONFIRM", "OVERRULE", "NO_DB_MATCH"],
                    },
                    "movie_master_id": {"type": "integer"},
                    "movie_title": {"type": "string"},
                    "alternate_movie_title": {"type": ["string", "null"]},
                    "rerelease_lookup_title": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
                "required": ["verdict", "movie_master_id", "movie_title", "confidence", "reasoning"],
            }
        },
    }
}

# Decision thresholds mirrored from result_parser._build_result — kept as a
# local copy rather than importing that function, since this module computes
# a decision from a verdict dict, not an agent JSON payload.
_AUTO_ACCEPT_THRESHOLD = 0.90


def _get_bedrock_client(settings):
    """Create a boto3 bedrock-runtime client using the ambient IAM role.
    Mirrors semantic_index.py's _get_bedrock_client (kept separate since that
    module's client is used for Cohere invoke_model, not Converse)."""
    try:
        import boto3
        return boto3.client("bedrock-runtime", region_name=settings.BEDROCK_REGION)
    except Exception as exc:
        logger.warning("rerank_intl_v2: boto3 client creation failed: %s", exc)
        return None


def _build_user_message(
    title: str,
    show_date: Optional[str],
    country: Optional[str],
    result: TitleMatchResult,
    db_candidates: list[dict],
    vespa_candidates: list[dict],
) -> str:
    parts = [
        f'Input title: "{title}"',
        f"Country: {country}" if country else "",
        f"Show date: {show_date}" if show_date else "",
        "",
        "## First pass identification",
        f"Chosen movie_master_id: {result.suggested_movie_id}",
        f"Chosen movie_title: {result.suggested_movie_title!r}",
        f"alternate_movie_title: {result.alternate_movie_title!r}",
        f"reasoning: {result.reasoning}",
        "",
        "## DB candidates",
        json.dumps(db_candidates, indent=2) if db_candidates else "(none)",
        "",
        "## Vespa candidates",
        json.dumps(vespa_candidates, indent=2) if vespa_candidates else "(none)",
    ]
    return "\n".join(p for p in parts if p is not None)


def _candidate_ids(db_candidates: list[dict], vespa_candidates: list[dict]) -> set[int]:
    ids: set[int] = {0}
    for c in db_candidates or []:
        if c.get("id"):
            ids.add(int(c["id"]))
    for c in vespa_candidates or []:
        if c.get("id"):
            ids.add(int(c["id"]))
    return ids


def _merge_rerank(
    first: TitleMatchResult,
    verdict: dict,
    valid_ids: set[int],
    max_confidence: float,
) -> TitleMatchResult:
    """Pure merge function — no network calls, fully unit-testable. Never
    mutates `first`; always returns a new TitleMatchResult."""
    verdict_id = int(verdict.get("movie_master_id") or 0)
    if verdict_id not in valid_ids:
        logger.warning(
            "rerank_intl_v2_verdict_id_not_in_candidates verdict_id=%d valid_ids=%s — discarding verdict",
            verdict_id, sorted(valid_ids),
        )
        merged = dataclasses.replace(first)
        merged.evidence = {
            **first.evidence,
            "rerank": {
                "verdict": verdict.get("verdict"),
                "discarded_reason": "verdict id not in candidate list",
                "verdict_id": verdict_id,
            },
        }
        return merged

    verdict_type = verdict.get("verdict")
    verdict_confidence = min(float(verdict.get("confidence", first.confidence)), max_confidence)
    verdict_reasoning = str(verdict.get("reasoning", ""))

    merged = dataclasses.replace(first)
    merged.reasoning = (
        f"[Verification pass — {verdict_type}] {verdict_reasoning}\n\n"
        f"[First pass reasoning] {first.reasoning}"
    )
    merged.evidence = {
        **first.evidence,
        "rerank": {
            "verdict": verdict_type,
            "first_pass_id": first.suggested_movie_id,
            "first_pass_title": first.suggested_movie_title,
            "verdict_reasoning": verdict_reasoning,
        },
    }

    if verdict_type == "CONFIRM":
        merged.confidence = verdict_confidence
        # decision is event-type-driven (REVIEW_NON_MOVIE/REVIEW_MULTI_FILM)
        # and must never be overwritten by a confidence change alone.
        if first.decision not in ("REVIEW_NON_MOVIE", "REVIEW_MULTI_FILM"):
            merged.decision = "AUTO_ACCEPT" if verdict_confidence >= _AUTO_ACCEPT_THRESHOLD else "REVIEW"
        return merged

    if verdict_type == "OVERRULE":
        merged.suggested_movie_id = verdict_id
        merged.canonical_movie_id = verdict_id
        merged.suggested_movie_title = str(verdict.get("movie_title") or first.suggested_movie_title)
        merged.confidence = verdict_confidence
        if first.decision not in ("REVIEW_NON_MOVIE", "REVIEW_MULTI_FILM"):
            merged.decision = "AUTO_ACCEPT" if verdict_confidence >= _AUTO_ACCEPT_THRESHOLD else "REVIEW"
        alt = verdict.get("alternate_movie_title")
        if alt:
            merged.alternate_movie_title = str(alt)
        rr = verdict.get("rerelease_lookup_title")
        if rr:
            merged.rerelease_lookup_title = str(rr)
        return merged

    # NO_DB_MATCH
    merged.suggested_movie_id = 0
    merged.canonical_movie_id = 0
    if verdict.get("movie_title"):
        merged.suggested_movie_title = str(verdict["movie_title"])
    alt = verdict.get("alternate_movie_title")
    if alt:
        merged.alternate_movie_title = str(alt)
    rr = verdict.get("rerelease_lookup_title")
    if rr:
        merged.rerelease_lookup_title = str(rr)
    merged.confidence = verdict_confidence
    return merged


def verify_candidate_pick(
    title: str,
    show_date: Optional[str],
    country: Optional[str],
    result: TitleMatchResult,
    db_candidates: list[dict],
    vespa_candidates: list[dict],
    settings,
    *,
    job_id: Optional[str] = None,
) -> TitleMatchResult:
    """Run the independent verification pass and merge its verdict into a new
    TitleMatchResult. Any failure (client creation, network, malformed
    response) logs a warning and returns `result` completely unchanged —
    this pass must never be able to make a row worse than skipping it."""
    client = _get_bedrock_client(settings)
    if client is None:
        return result

    model_id = settings.AGENTIC_INTL_V2_RERANK_MODEL or settings.AGENTIC_CLAUDE_MODEL
    started = time.monotonic()
    try:
        user_message = _build_user_message(
            title, show_date, country, result, db_candidates, vespa_candidates,
        )
        response = client.converse(
            modelId=model_id,
            system=[{"text": _VERIFY_SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            toolConfig={
                "tools": [_VERDICT_TOOL_SPEC],
                "toolChoice": {"tool": {"name": _VERDICT_TOOL_NAME}},
            },
        )

        content = response["output"]["message"]["content"]
        tool_use = next(b["toolUse"] for b in content if "toolUse" in b)
        verdict = tool_use["input"]

        valid_ids = _candidate_ids(db_candidates, vespa_candidates)
        merged = _merge_rerank(
            result, verdict, valid_ids, settings.AGENTIC_INTL_V2_RERANK_MAX_CONFIDENCE,
        )

        try:
            log_llm_call(
                LlmCallContext(
                    task_type=TASK_INTL_MAPPING,
                    call_path=PATH_BEDROCK_CONVERSE,
                    caller_type=CALLER_PORTAL,
                    job_id=job_id,
                    market="international",
                    country=country,
                ),
                model_id=model_id,
                usage=extract_converse_usage(response),
                latency_ms=int((time.monotonic() - started) * 1000),
                status=STATUS_SUCCESS,
                decision=merged.decision,
            )
        except Exception as log_exc:  # noqa: BLE001 - logging must never break the row
            logger.warning("rerank_intl_v2_usage_log_failed error=%s", log_exc)

        return merged
    except Exception as exc:  # noqa: BLE001 - verification must never break the row
        logger.warning("rerank_intl_v2_failed_falling_back_to_first_pass title=%r error=%s", title, exc)
        try:
            log_llm_call(
                LlmCallContext(
                    task_type=TASK_INTL_MAPPING,
                    call_path=PATH_BEDROCK_CONVERSE,
                    caller_type=CALLER_PORTAL,
                    job_id=job_id,
                    market="international",
                    country=country,
                ),
                model_id=model_id,
                usage=extract_converse_usage(None),
                latency_ms=int((time.monotonic() - started) * 1000),
                status=STATUS_FAILURE,
                error_type=type(exc).__name__,
            )
        except Exception as log_exc:  # noqa: BLE001
            logger.warning("rerank_intl_v2_usage_log_failed error=%s", log_exc)
        return result

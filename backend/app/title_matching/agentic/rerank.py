"""
Category-B fix: an independent verification pass over the first agentic
pass's candidate pick, for market="international" only.

Root cause being fixed: the first pass has been observed to correctly
identify the real film via web research, then still emit the wrong
movie_master_id/movie_title (e.g. reasoning concludes "The Odyssey" but the
emitted title is "Odyssey" pointing at a placeholder-dated candidate), or
emit movie_master_id=0 with a BLANK movie_title despite a confident
identification. This pass re-checks the first pass's own candidate list
against those same failure modes and can confirm, overrule, or flag "no DB
match — but here are the titles to look up" — see the intl-title-match
skill's Section B for the full rule set.

Deliberately NOT a second claude-sandbox subprocess call: this step only
needs to reason over evidence already gathered (first pass's identified
film + reasoning + the full candidate list) — no web tools, no MCP, no
filesystem. Paying for a fresh `claude --print` subprocess (new ephemeral
$HOME, MCP server startup, stream-json parsing) for that would be pure
overhead. Instead this calls the model directly via boto3 bedrock-runtime's
Converse API — same client-creation pattern already used for embeddings in
semantic_index.py::_get_bedrock_client, but Converse instead of Cohere
invoke_model, with a single forced tool call for structured output.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.title_matching.types import TitleMatchResult

logger = logging.getLogger(__name__)

_VERDICT_TOOL_NAME = "report_verdict"

# Kept in sync with the "B. Candidate hygiene notes" section of
# backend/app/title_matching/agentic/skills/intl-title-match/SKILL.md — this
# verification pass never touches claude-sandbox and so can't load that file
# via the prompt_builder inlining mechanism; the relevant rules are
# duplicated here as the system prompt instead.
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
        logger.warning("rerank: boto3 client creation failed: %s", exc)
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
) -> TitleMatchResult:
    """Pure merge function — no network calls, fully unit-testable.

    See rerank.py module docstring / SKILL.md Section B for the rules this
    encodes. Never mutates `first`; always returns a new TitleMatchResult.
    """
    verdict_id = int(verdict.get("movie_master_id") or 0)
    if verdict_id not in valid_ids:
        logger.warning(
            "rerank_verdict_id_not_in_candidates verdict_id=%d valid_ids=%s — discarding verdict",
            verdict_id, sorted(valid_ids),
        )
        merged = _copy_result(first)
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
    verdict_confidence = min(float(verdict.get("confidence", first.confidence)), 0.97)
    verdict_reasoning = str(verdict.get("reasoning", ""))

    merged = _copy_result(first)
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


def _copy_result(result: TitleMatchResult) -> TitleMatchResult:
    """Shallow copy — evidence dict is always replaced wholesale by callers,
    never mutated in place, so a shallow copy is safe here."""
    import dataclasses
    return dataclasses.replace(result)


def verify_candidate_pick(
    title: str,
    show_date: Optional[str],
    country: Optional[str],
    result: TitleMatchResult,
    db_candidates: list[dict],
    vespa_candidates: list[dict],
    settings,
) -> TitleMatchResult:
    """Run the independent verification pass and merge its verdict into a new
    TitleMatchResult. Any failure (client creation, network, malformed
    response) logs a warning and returns `result` completely unchanged —
    this pass must never be able to make a row worse than skipping it."""
    client = _get_bedrock_client(settings)
    if client is None:
        return result

    try:
        user_message = _build_user_message(
            title, show_date, country, result, db_candidates, vespa_candidates,
        )
        response = client.converse(
            modelId=settings.AGENTIC_CLAUDE_MODEL,
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
        return _merge_rerank(result, verdict, valid_ids)
    except Exception as exc:  # noqa: BLE001 - verification must never break the row
        logger.warning("rerank_failed_falling_back_to_first_pass title=%r error=%s", title, exc)
        return result

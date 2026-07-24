from __future__ import annotations

import json
import re
from typing import Any

from app.title_matching.types import TitleMatchResult
from app.title_matching.agentic import AgenticParseError


def parse_agent_output(stdout: str) -> TitleMatchResult:
    """Extract the JSON payload from claude --output-format stream-json output.

    Falls back to a low-confidence REVIEW result when the agent output cannot
    be parsed (e.g. all tool calls errored and no JSON was produced).
    """
    try:
        raw = _extract_text(stdout)
        payload = _extract_json(raw)
        return _build_result(payload, raw)
    except AgenticParseError as exc:
        tail = stdout[-300:] if len(stdout) > 300 else stdout
        return TitleMatchResult(
            suggested_movie_id=0,
            suggested_movie_title="Unknown",
            canonical_movie_id=0,
            confidence=0.0,
            decision="REVIEW",
            reasoning=f"Agent output could not be parsed: {exc}",
            evidence={"agentic": True, "parse_error": str(exc), "raw_tail": tail},
            fired_ai=True,
        )


# ── private helpers ───────────────────────────────────────────────────────────

_TOOL_USE_SUMMARY_RE = re.compile(r"^\*+\d+ tool use\*+$", re.IGNORECASE)
_TOOL_USE_SUFFIX_RE = re.compile(r"\n+\*+\d+ tool use\*+\s*$", re.IGNORECASE)


def _extract_text(stdout: str) -> str:
    """Pull the final JSON-bearing text block from stream-json output.

    Scans all assistant message text blocks, stripping trailing tool-use
    summary lines and skipping blocks that are entirely a summary.
    Prefers a text block containing JSON; falls back to result.result.
    """
    last_text = ""
    result_field = ""

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        if obj.get("type") == "assistant":
            content = obj.get("message", {}).get("content", [])
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block["text"].strip()
                    # Strip trailing "**N tool use**" suffix appended to real content
                    text = _TOOL_USE_SUFFIX_RE.sub("", text).strip()
                    # Skip blocks that are entirely a tool-use summary
                    if text and not _TOOL_USE_SUMMARY_RE.match(text):
                        last_text = text

        elif obj.get("type") == "result":
            r = obj.get("result", "")
            if isinstance(r, str) and r.strip() and not _TOOL_USE_SUMMARY_RE.match(r.strip()):
                result_field = r.strip()

    # Prefer whichever source actually contains JSON
    if last_text and "{" in last_text:
        return last_text
    if result_field and "{" in result_field:
        return result_field
    text = last_text or result_field
    if not text:
        tail = stdout[-400:] if len(stdout) > 400 else stdout
        raise AgenticParseError(
            f"No assistant text block found in agent output. "
            f"Raw tail: {tail!r}. Check model and system prompt."
        )
    return text


def _extract_json(text: str) -> dict[str, Any]:
    """Find the outermost valid JSON object in the agent's text response.

    Tries in order:
    1. Content inside a ```json ... ``` fence.
    2. The first complete {...} block scanning from the first '{'.
    """
    # Try fenced block first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass  # fall through to brace scan

    # Scan for the first '{' and find its matching '}' by brace counting
    start = text.find("{")
    if start == -1:
        raise AgenticParseError(
            f"No JSON object found in agent output. Raw tail: {text[-300:]!r}. "
            "Adjust system prompt or check model."
        )

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise AgenticParseError(
                        f"Agent returned unparseable JSON: {exc}. "
                        f"Raw snippet: {candidate[:300]!r}."
                    ) from exc

    raise AgenticParseError(
        f"Unbalanced JSON braces in agent output. Raw tail: {text[-300:]!r}."
    )


def _build_result(payload: dict[str, Any], raw_text: str) -> TitleMatchResult:
    candidates = payload.get("candidates", [])
    if not candidates:
        # Agent found no DB match at all — return a low-confidence REVIEW result
        return TitleMatchResult(
            suggested_movie_id=0,
            suggested_movie_title="Unknown",
            canonical_movie_id=0,
            confidence=0.0,
            decision="REVIEW",
            reasoning=(
                "Agent could not identify a match: no DB candidates were returned "
                f"and the agent produced no candidates. Raw: {raw_text[:200]}"
            ),
            evidence={"agentic": True, "event_type": payload.get("event_type", "MOVIE")},
            fired_ai=True,
        )

    idx = int(payload.get("best_match_index", 0))
    if idx >= len(candidates):
        idx = 0
    best = candidates[idx]

    confidence = float(best.get("confidence", 0.0))
    event_type = payload.get("event_type", "MOVIE")

    if event_type == "NON_MOVIE":
        decision = "REVIEW_NON_MOVIE"
    elif event_type == "MULTI_FILM":
        decision = "REVIEW_MULTI_FILM"
    elif confidence >= 0.90:
        decision = "AUTO_ACCEPT"
    else:
        decision = "REVIEW"

    raw_id = best.get("movie_master_id") or 0
    alternate_title = best.get("alternate_movie_title")
    return TitleMatchResult(
        suggested_movie_id=int(raw_id),
        suggested_movie_title=str(best.get("movie_title", "")),
        canonical_movie_id=int(raw_id),
        confidence=confidence,
        decision=decision,
        reasoning=str(best.get("reasoning", "")),
        evidence={
            "agentic": True,
            "all_candidates": candidates,
            "source_evidence": best.get("source_evidence", {}),
            "normalized_input": payload.get("normalized_input", ""),
            "event_type": event_type,
        },
        fired_ai=True,
        alternate_movie_title=str(alternate_title) if alternate_title else None,
    )

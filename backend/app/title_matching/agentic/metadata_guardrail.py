"""Deterministic incomplete-metadata guardrail for domestic agentic v2.

Enforces, in code, the rule the v2 prompt (prompt_builder_v2.py's Step 2b)
already asks Claude to follow: a candidate with BOTH director and synopsis
empty must never be the final pick unless its genre is exactly "Sports" or
"Concert/Special Events" (verified against production data: those two genres
are 100%/96% empty on both fields, whereas narrative genres are consistently
populated). This module is the belt to the prompt's suspenders -- it does not
trust the model to have applied the rule, it re-checks the authoritative
DB-sourced values itself.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.title_matching.normalizer import has_conflicting_ordinal
from app.title_matching.types import TitleMatchResult

logger = logging.getLogger(__name__)

# Whole-string, case/whitespace-insensitive genre comparison. Never split on
# "/" or "," -- "Concert/Special Events" itself contains a slash, and a
# multi-value genre field (if one ever appears) would lose the exemption if
# split naively.
EXEMPT_GENRES = frozenset({"sports", "concert/special events"})

_BLANK_PLACEHOLDERS = frozenset({
    "", "n/a", "na", "none", "null", "nil", "-", "--", "tbd",
    "unknown", "not available", "0",
})


def _is_blank(value: Optional[str]) -> bool:
    if value is None:
        return True
    return str(value).strip().casefold() in _BLANK_PLACEHOLDERS


def is_genre_exempt(genre: Optional[str]) -> bool:
    return (genre or "").strip().casefold() in EXEMPT_GENRES


def candidate_passes(candidate: dict) -> bool:
    """True unless the candidate has BOTH director and synopsis empty in a
    non-exempt genre. Missing keys are treated as blank."""
    director_blank = _is_blank(candidate.get("director"))
    synopsis_blank = _is_blank(candidate.get("synopsis"))
    if director_blank and synopsis_blank and not is_genre_exempt(candidate.get("genre")):
        return False
    return True


def _resolve_candidate(movie_id: int, by_id: dict[int, dict]) -> Optional[dict]:
    row = by_id.get(movie_id)
    if row is not None:
        return row
    try:
        from sqlmodel import Session
        from app.database import engine as db_engine
        from app.models import MovieMaster

        with Session(db_engine) as session:
            record = session.get(MovieMaster, movie_id)
            if record is None:
                return None
            return {
                "id": record.id,
                "movie_title": record.movie_title,
                "genre": record.genre or "",
                "director": record.director or "",
                "synopsis": record.synopsis or "",
            }
    except Exception as exc:  # noqa: BLE001 - fail open, never block on this lookup
        logger.debug("metadata_guardrail_resolve_failed id=%s error=%s", movie_id, exc)
        return None


def _fallthrough_candidates(
    result: TitleMatchResult, db_candidates: list[dict], rejected_id: int,
) -> list[dict]:
    """Tier 1: Claude's own runner-ups (result.evidence['all_candidates']),
    in order, skipping the rejected pick. Tier 2: the pre-fetched DB order,
    skipping ids already considered in tier 1."""
    considered_ids = {rejected_id}
    ordered: list[dict] = []

    all_candidates = (result.evidence or {}).get("all_candidates") or []
    for c in all_candidates:
        try:
            mid = int(c.get("movie_master_id") or 0)
        except (TypeError, ValueError):
            continue
        if mid and mid not in considered_ids:
            considered_ids.add(mid)
            ordered.append({"id": mid, "movie_title": c.get("movie_title", ""), "_source": "model_candidates"})

    for c in db_candidates:
        mid = c.get("id")
        if mid and mid not in considered_ids:
            considered_ids.add(mid)
            ordered.append({**c, "_source": "db_prefetch"})

    return ordered


def apply_metadata_guardrail(
    result: TitleMatchResult,
    db_candidates: list[dict],
    *,
    query_ordinal: Optional[int] = None,
) -> TitleMatchResult:
    """Reject a pick with incomplete metadata (per candidate_passes) and fall
    through to the next-closest candidate that passes. Never raises. Records
    its verdict in result.evidence["metadata_guardrail"] regardless of mode."""
    mode = settings.AGENTIC_V2_METADATA_GUARDRAIL_MODE
    if mode == "off":
        return result

    if not result.suggested_movie_id:
        result.evidence = {**(result.evidence or {}), "metadata_guardrail": {
            "mode": mode, "checked": False, "reason": "no_pick",
        }}
        return result

    by_id = {c["id"]: c for c in db_candidates if c.get("id")}
    chosen = _resolve_candidate(result.suggested_movie_id, by_id)
    if chosen is None:
        result.evidence = {**(result.evidence or {}), "metadata_guardrail": {
            "mode": mode, "checked": False, "reason": "candidate_not_resolvable",
        }}
        return result

    if candidate_passes(chosen):
        result.evidence = {**(result.evidence or {}), "metadata_guardrail": {
            "mode": mode, "checked": True, "rejected_id": None,
        }}
        return result

    rejected_id = result.suggested_movie_id
    rejected_title = chosen.get("movie_title", "")
    rejected_genre = chosen.get("genre", "")

    replacement = None
    replacement_source = None
    for candidate in _fallthrough_candidates(result, db_candidates, rejected_id):
        resolved = _resolve_candidate(candidate["id"], by_id)
        if resolved is None or not candidate_passes(resolved):
            continue
        if query_ordinal and has_conflicting_ordinal(resolved.get("movie_title", ""), query_ordinal):
            continue
        try:
            from rapidfuzz import fuzz
            similarity = fuzz.token_set_ratio(resolved.get("movie_title", ""), rejected_title)
        except Exception:
            similarity = 0
        if similarity < settings.AGENTIC_V2_FALLTHROUGH_MIN_TITLE_SIMILARITY:
            continue
        replacement = resolved
        replacement_source = candidate.get("_source")
        break

    guardrail_evidence = {
        "mode": mode,
        "checked": True,
        "rejected_id": rejected_id,
        "rejected_title": rejected_title,
        "rejected_genre": rejected_genre,
        "rejected_reason": "director_and_synopsis_empty_genre_not_exempt",
        "replacement_id": replacement["id"] if replacement else None,
        "replacement_source": replacement_source,
        "exempt_genres": sorted(EXEMPT_GENRES),
    }

    if mode == "log_only":
        guardrail_evidence["applied"] = False
        result.evidence = {**(result.evidence or {}), "metadata_guardrail": guardrail_evidence}
        return result

    guardrail_evidence["applied"] = True
    result.evidence = {**(result.evidence or {}), "metadata_guardrail": guardrail_evidence}

    non_movie_decisions = {"REVIEW_NON_MOVIE", "REVIEW_MULTI_FILM"}

    if replacement is not None:
        result.suggested_movie_id = replacement["id"]
        result.canonical_movie_id = replacement["id"]
        result.suggested_movie_title = replacement.get("movie_title", "")
        result.cover_image = None
        result.confidence = min(result.confidence, 0.89)
        if result.decision not in non_movie_decisions:
            result.decision = "REVIEW"
        result.reasoning = (
            f"{result.reasoning} [Metadata guardrail: disregarded id {rejected_id} "
            f"('{rejected_title}') -- director and synopsis both empty, genre "
            f"'{rejected_genre}' not exempt; substituted id {replacement['id']} "
            f"('{replacement.get('movie_title', '')}')."
        ).strip()
    else:
        result.suggested_movie_id = 0
        result.canonical_movie_id = 0
        result.suggested_movie_title = "Unknown"
        result.confidence = min(result.confidence, 0.49)
        if result.decision not in non_movie_decisions:
            result.decision = "REVIEW"
        result.reasoning = (
            f"{result.reasoning} [Metadata guardrail: disregarded id {rejected_id} "
            f"('{rejected_title}') -- director and synopsis both empty, genre "
            f"'{rejected_genre}' not exempt; no passing replacement found among "
            f"the pre-fetched candidates."
        ).strip()

    return result

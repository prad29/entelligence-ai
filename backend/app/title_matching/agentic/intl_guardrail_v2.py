"""Deterministic country-consistency guardrail for international v2.

Domestic v2's metadata_guardrail.py cannot be reused or parameterized here --
its rule reads `director`/`synopsis`, columns MovieMasterIntl does not have.
This module enforces the same *shape* (prompt rule + deterministic re-check +
fall-through + evidence record) on the one deterministic signal
international actually has: country consistency. No import edge to
metadata_guardrail.py -- deliberately separate, matching the same
no-shared-code-path lesson behind the two post-lookup fixes.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.title_matching.normalizer import has_conflicting_ordinal
from app.title_matching.types import TitleMatchResult

logger = logging.getLogger(__name__)


def _is_blank(value: Optional[str]) -> bool:
    return value is None or not str(value).strip()


def candidate_passes_intl(candidate: dict, country: Optional[str]) -> bool:
    """True unless the candidate's country contradicts the request's
    country. Fails OPEN when either side is blank -- an unknown country is
    missing data, not a mismatch (intl batch uploads may carry an empty
    country cell)."""
    cand_country = candidate.get("country")
    if _is_blank(country) or _is_blank(cand_country):
        return True
    return str(cand_country).strip().casefold() == str(country).strip().casefold()


def _resolve_candidate(movie_id: int, by_id: dict[int, dict]) -> Optional[dict]:
    row = by_id.get(movie_id)
    if row is not None:
        return row
    try:
        from sqlmodel import Session
        from app.database import engine as db_engine
        from app.models import MovieMasterIntl

        with Session(db_engine) as session:
            record = session.get(MovieMasterIntl, movie_id)
            if record is None:
                return None
            return {
                "id": record.id,
                "movie_title": record.movie_title,
                "country": record.country,
                "genre": record.genre or "",
                "genre2": record.genre2 or "",
            }
    except Exception as exc:  # noqa: BLE001 - fail open, never block on this lookup
        logger.debug("intl_guardrail_resolve_failed id=%s error=%s", movie_id, exc)
        return None


def _fallthrough_candidates(
    result: TitleMatchResult, db_candidates: list[dict], rejected_id: int,
) -> list[dict]:
    """Tier 1: Claude's own runner-ups. Tier 2: the pre-fetched DB order.
    Mirrors metadata_guardrail._fallthrough_candidates's identical shape."""
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


def apply_country_guardrail(
    result: TitleMatchResult,
    db_candidates: list[dict],
    country: Optional[str],
    *,
    query_ordinal: Optional[int] = None,
) -> TitleMatchResult:
    """Reject a pick whose country contradicts the request's country and
    fall through to the next-closest candidate that passes. Never raises.
    Records its verdict in result.evidence["intl_country_guardrail"]
    regardless of mode."""
    mode = settings.AGENTIC_INTL_V2_COUNTRY_GUARDRAIL_MODE
    if mode == "off":
        return result

    if not result.suggested_movie_id:
        result.evidence = {**(result.evidence or {}), "intl_country_guardrail": {
            "mode": mode, "checked": False, "reason": "no_pick",
        }}
        return result

    by_id = {c["id"]: c for c in db_candidates if c.get("id")}
    chosen = _resolve_candidate(result.suggested_movie_id, by_id)
    if chosen is None:
        result.evidence = {**(result.evidence or {}), "intl_country_guardrail": {
            "mode": mode, "checked": False, "reason": "candidate_not_resolvable",
        }}
        return result

    if candidate_passes_intl(chosen, country):
        result.evidence = {**(result.evidence or {}), "intl_country_guardrail": {
            "mode": mode, "checked": True, "rejected_id": None,
        }}
        return result

    rejected_id = result.suggested_movie_id
    rejected_title = chosen.get("movie_title", "")
    rejected_country = chosen.get("country", "")

    replacement = None
    replacement_source = None
    for candidate in _fallthrough_candidates(result, db_candidates, rejected_id):
        resolved = _resolve_candidate(candidate["id"], by_id)
        if resolved is None or not candidate_passes_intl(resolved, country):
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
        "rejected_country": rejected_country,
        "rejected_reason": "country_mismatch",
        "replacement_id": replacement["id"] if replacement else None,
        "replacement_source": replacement_source,
        "request_country": country,
    }

    if mode == "log_only":
        guardrail_evidence["applied"] = False
        result.evidence = {**(result.evidence or {}), "intl_country_guardrail": guardrail_evidence}
        return result

    guardrail_evidence["applied"] = True
    result.evidence = {**(result.evidence or {}), "intl_country_guardrail": guardrail_evidence}

    non_movie_decisions = {"REVIEW_NON_MOVIE", "REVIEW_MULTI_FILM"}

    if replacement is not None:
        result.suggested_movie_id = replacement["id"]
        result.canonical_movie_id = replacement["id"]
        result.suggested_movie_title = replacement.get("movie_title", "")
        result.confidence = min(result.confidence, 0.89)
        if result.decision not in non_movie_decisions:
            result.decision = "REVIEW"
        result.reasoning = (
            f"{result.reasoning} [Country guardrail: disregarded id {rejected_id} "
            f"('{rejected_title}', country={rejected_country!r}) -- does not match "
            f"the requested country {country!r}; substituted id {replacement['id']} "
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
            f"{result.reasoning} [Country guardrail: disregarded id {rejected_id} "
            f"('{rejected_title}', country={rejected_country!r}) -- does not match "
            f"the requested country {country!r}; no passing replacement found among "
            f"the pre-fetched candidates."
        ).strip()

    return result

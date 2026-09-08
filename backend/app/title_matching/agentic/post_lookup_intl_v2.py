"""International v2 ONLY: rewrite confidence/decision/reasoning after the
id=0 post-lookup resolves a real row.

Deliberately a separate module from post_lookup_v2.py (domestic v2's copy)
with NO shared helper, even though the two do a similar job: sharing exactly
this code path between domestic and international is what caused the
production regression reverted in PR #56. There is no import edge between
the two modules, and each is imported from exactly one call site.
"""

from __future__ import annotations

from app.title_matching.types import TitleMatchResult

INTL_V2_POST_LOOKUP_CONFIDENCE = 0.90


def apply_intl_v2_post_lookup_resolution(
    result: TitleMatchResult, matched_row: dict, *, resolved_via: str,
) -> TitleMatchResult:
    """Rewrite confidence/decision/reasoning after a country-scoped
    post-lookup DB search resolves a real MovieMasterIntl row.

    confidence=0.90 (AUTO_ACCEPT-eligible): the hit is a country-scoped
    exact/trigram title match on MovieMasterIntl, a materially stronger
    signal than domestic's title-only post-lookup hit (see post_lookup_v2.py).
    REVIEW_NON_MOVIE/REVIEW_MULTI_FILM (event-type classifications, not a
    confidence signal) are never touched.

    `resolved_via` names which of the three post-lookup attempts won
    (rerelease_lookup_title, suggested_movie_title, or alternate_movie_title)
    so the reasoning says which string actually matched.
    """
    non_movie_decisions = {"REVIEW_NON_MOVIE", "REVIEW_MULTI_FILM"}
    previous_confidence = result.confidence
    previous_decision = result.decision
    original_reasoning = result.reasoning

    result.confidence = INTL_V2_POST_LOOKUP_CONFIDENCE
    if result.decision == "REVIEW":
        result.decision = "AUTO_ACCEPT"

    if result.decision == "AUTO_ACCEPT":
        tier_phrase = "the result was updated to point at it and auto-accepted"
    elif result.decision in non_movie_decisions:
        tier_phrase = (
            f"the result was updated to point at it but kept in {result.decision} "
            f"for human review (event-type classification, not a confidence signal)"
        )
    else:
        tier_phrase = "the result was updated to point at it but kept in REVIEW"

    result.reasoning = (
        f"Resolved via country-scoped post-lookup on {resolved_via}: Claude identified "
        f"this as {result.suggested_movie_title!r} but found no plausible pre-fetched "
        f"DB candidate; a follow-up DB search found movie_master_intl_id="
        f"{matched_row['id']} in country {matched_row.get('country')!r} and "
        f"{tier_phrase}. Claude's original reasoning: {original_reasoning}"
    )
    result.evidence = {
        **(result.evidence or {}),
        "intl_v2_post_lookup": {
            "resolved_via": resolved_via,
            "matched_id": matched_row["id"],
            "matched_country": matched_row.get("country"),
            "previous_confidence": previous_confidence,
            "previous_decision": previous_decision,
        },
    }
    return result

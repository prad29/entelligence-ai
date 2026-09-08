"""Domestic agentic v2 ONLY: rewrite confidence/decision/reasoning after the
id=0 post-lookup resolves a real row.

Deliberately a separate module from post_lookup_intl_v2.py (international
v2's copy) with NO shared helper, even though the two do a similar job:
sharing exactly this code path between domestic and international is what
caused the production regression reverted in PR #56. There is no import
edge between the two modules, and each is imported from exactly one call
site (enforced by test_intl_v2.py).

Domestic v1 keeps its pre-existing stale-reasoning behavior after a
post-lookup hit -- this fix is v2-only, per the user's explicit instruction
to leave v1 untouched by this initiative.
"""

from __future__ import annotations

from app.title_matching.types import TitleMatchResult

# Inside the REVIEW band (0.60-0.90) and NEVER auto-accepted: MovieMaster has
# no country column to corroborate a domestic post-lookup hit the way
# MovieMasterIntl's country scoping does (see post_lookup_intl_v2.py, which
# uses 0.90 and can auto-accept) -- a domestic title-only match is a
# genuinely weaker signal. 0.75 specifically (not lower, e.g. 0.70): the
# review-queue UI colors confidence red below 0.75, the same red as a
# garbage match, which would defeat the point of distinguishing a real
# post-lookup hit from the stale low-confidence state it replaces.
DOMESTIC_V2_POST_LOOKUP_CONFIDENCE = 0.75


def apply_domestic_v2_post_lookup_resolution(
    result: TitleMatchResult, matched_row: dict,
) -> TitleMatchResult:
    """Rewrite confidence/decision/reasoning after an exact-title post-lookup
    resolves a real MovieMaster row."""
    non_movie_decisions = {"REVIEW_NON_MOVIE", "REVIEW_MULTI_FILM"}
    previous_confidence = result.confidence
    previous_decision = result.decision
    original_reasoning = result.reasoning

    result.confidence = DOMESTIC_V2_POST_LOOKUP_CONFIDENCE
    # Event-type-driven decisions are never touched -- they aren't a
    # confidence signal. But an AUTO_ACCEPT the first pass already assigned
    # (confidence >= 0.90 alone triggers this in result_parser._build_result,
    # independent of movie_master_id) must be downgraded back to REVIEW here
    # -- otherwise a domestic post-lookup hit could silently keep AUTO_ACCEPT
    # while confidence drops to 0.75, which is exactly the "confidence/
    # decision say something the row doesn't back up" bug this fix removes.
    if result.decision == "AUTO_ACCEPT":
        result.decision = "REVIEW"

    if result.decision in non_movie_decisions:
        tier_phrase = (
            f"the result was updated to point at it but kept in {result.decision} "
            f"for human review (event-type classification, not a confidence signal)"
        )
    else:
        tier_phrase = (
            "the result was updated to point at it but kept in REVIEW, since a "
            "domestic title-only DB match has no country field to corroborate it"
        )

    result.reasoning = (
        f"Resolved via exact-title post-lookup: Claude identified this as "
        f"{result.suggested_movie_title!r} but found no plausible pre-fetched "
        f"DB candidate; a follow-up exact DB search on that title found "
        f"movie_master_id={matched_row['id']} and {tier_phrase}. "
        f"Claude's original reasoning: {original_reasoning}"
    )
    result.evidence = {
        **(result.evidence or {}),
        "domestic_v2_post_lookup": {
            "matched_id": matched_row["id"],
            "previous_confidence": previous_confidence,
            "previous_decision": previous_decision,
        },
    }
    return result

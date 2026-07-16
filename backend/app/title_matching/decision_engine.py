from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from app.title_matching.types import CandidateResult, NormalizedTitle, TitleMatchResult

logger = logging.getLogger(__name__)

# Sentinel release years that indicate "no real date" in the data
_SENTINEL_YEARS = frozenset({'0000', '9999'})
_FUTURE_YEAR_THRESHOLD = 2030

# Keywords suggesting animation in a title (simple heuristic)
_ANIMATION_HINTS = frozenset(['animated', '2d animated', 'animation', 'cartoon'])


def _parse_release_date(date_str: Optional[str]) -> Optional[date]:
    """Parse a YYYY-MM-DD release date string, returning None for sentinels."""
    if not date_str:
        return None
    year_part = date_str[:4]
    if year_part in _SENTINEL_YEARS:
        return None
    try:
        parsed = datetime.strptime(date_str, '%Y-%m-%d').date()
        if parsed.year >= _FUTURE_YEAR_THRESHOLD:
            return None
        return parsed
    except ValueError:
        return None


def _date_boost(show_date: Optional[str], release_date: Optional[str]) -> tuple[float, str]:
    """Return (boost, label) based on proximity of show_date to release_date."""
    if not show_date or not release_date:
        return 0.0, 'NONE'

    show = _parse_release_date(show_date)
    release = _parse_release_date(release_date)

    if show is None or release is None:
        return 0.0, 'NONE'

    delta_days = abs((show - release).days)

    if delta_days == 0:
        return 0.30, 'EXACT'
    if delta_days <= 30:
        return 0.15, 'NEAR'
    if delta_days <= 365:
        return 0.05, 'YEAR'
    return 0.0, 'NONE'


def _edition_penalty(normalized: NormalizedTitle, candidate_title: str) -> float:
    """Penalize if query has 'Live Action' but candidate looks like an animation."""
    if 'Live Action' not in normalized.edition_markers:
        return 0.0
    title_lower = candidate_title.lower()
    if any(hint in title_lower for hint in _ANIMATION_HINTS):
        return -0.20
    return 0.0


def _recency_boost(candidates: list[CandidateResult], winner_idx: int) -> float:
    """Give a small boost to the most recently released candidate among tied scores."""
    if len(candidates) <= 1:
        return 0.0
    winner = candidates[winner_idx]
    winner_date = _parse_release_date(winner.release_date)
    if winner_date is None:
        return 0.0
    # Check if winner has the most recent date among all candidates
    for i, c in enumerate(candidates):
        if i == winner_idx:
            continue
        other_date = _parse_release_date(c.release_date)
        if other_date and other_date > winner_date:
            return 0.0  # someone else is more recent
    return 0.05


def _runtime_check(
    page_runtime_min: Optional[int],
    master_runtime_min: Optional[int],
) -> tuple[float, str]:
    """Return (boost, label). +0.10 if runtimes within 5 min of each other."""
    if page_runtime_min is None or master_runtime_min is None:
        return 0.0, 'RUNTIME_UNKNOWN'
    delta = abs(page_runtime_min - master_runtime_min)
    if delta <= 5:
        return 0.10, 'RUNTIME_MATCH'
    return 0.0, f'RUNTIME_MISMATCH_{delta}min'


def _director_check(
    page_director: Optional[str],
    master_director: Optional[str],
) -> tuple[float, str]:
    """Return (boost, label). +0.10 if directors match (rapidfuzz ratio >= 80)."""
    if not page_director or not master_director:
        return 0.0, 'DIRECTOR_UNKNOWN'
    try:
        from rapidfuzz import fuzz
        ratio = fuzz.ratio(page_director.lower().strip(), master_director.lower().strip())
        if ratio >= 80:
            return 0.10, 'DIRECTOR_MATCH'
        return 0.0, f'DIRECTOR_MISMATCH_{int(ratio)}'
    except Exception:
        return 0.0, 'DIRECTOR_UNKNOWN'


def _cast_check(
    page_cast: Optional[str],
    master_cast_list: Optional[str],
) -> tuple[float, str]:
    """Return (boost, label). +0.05 if any cast overlap between page and master."""
    if not page_cast or not master_cast_list:
        return 0.0, 'CAST_UNKNOWN'
    page_names = {n.strip().lower() for n in page_cast.replace(',', ';').split(';') if n.strip()}
    master_names = {n.strip().lower() for n in master_cast_list.replace(',', ';').split(';') if n.strip()}
    overlap = page_names & master_names
    if overlap:
        return 0.05, f'CAST_MATCH_{len(overlap)}'
    return 0.0, 'CAST_NO_OVERLAP'


def _compute_composite_score(
    candidate: CandidateResult,
    normalized: NormalizedTitle,
    show_date: Optional[str],
) -> tuple[float, str]:
    """Compute composite score and return (score, date_boost_label)."""
    base = candidate.score
    date_boost, date_label = _date_boost(show_date, candidate.release_date)
    edition_pen = _edition_penalty(normalized, candidate.movie_title)
    raw = base + date_boost + edition_pen
    return max(0.0, min(1.0, raw)), date_label


def _resolve_parent(
    movie_id: int,
    id_to_row: dict[int, dict],
) -> int:
    """Return canonical_movie_id after resolving parent_id chain (one level)."""
    row = id_to_row.get(movie_id)
    if row is None:
        return movie_id
    parent_id = row.get('parent_id')
    if parent_id is not None and parent_id != movie_id and parent_id in id_to_row:
        return parent_id
    return movie_id


def _generate_reasoning(
    normalized: NormalizedTitle,
    winner: CandidateResult,
    score: float,
    date_label: str,
    fired_ai: bool,
) -> str:
    """Generate a template-based reasoning string."""
    date_explanation = {
        'EXACT': f"Release date matches show date exactly.",
        'NEAR': f"Release date is within 30 days of show date.",
        'YEAR': f"Release date is within one year of show date.",
        'NONE': "No date proximity signal available.",
    }.get(date_label, '')

    return (
        f"The title '{normalized.cleaned}' most closely matches "
        f"'{winner.movie_title}' (id {winner.movie_master_id}) "
        f"with confidence {score:.0%}. {date_explanation}"
    )


def _call_bedrock_reasoning(
    normalized: NormalizedTitle,
    winner: CandidateResult,
    score: float,
    candidates: list[CandidateResult],
) -> Optional[str]:
    """Attempt to generate reasoning via Bedrock. Returns None on failure."""
    try:
        from app.config import settings
        if settings.AI_TRIGGER_MODE == 'off':
            return None

        from app.detection.bedrock_client import bedrock_client

        top_titles = [f"{c.movie_title} (id {c.movie_master_id})" for c in candidates[:5]]
        prompt = (
            f'Movie title to match: "{normalized.cleaned}"\n'
            f'Top candidates:\n'
            + '\n'.join(f'  - {t}' for t in top_titles)
            + f'\n\nBest match selected: "{winner.movie_title}" (id {winner.movie_master_id}), '
            f'confidence {score:.0%}.\n\n'
            'In 1-2 sentences, explain why this is the best match. '
            'Return ONLY the explanation as plain text.'
        )
        # Use bedrock_client with a single-turn message
        import httpx

        def _base_url() -> str:
            return f"https://bedrock-runtime.{settings.BEDROCK_REGION}.amazonaws.com"

        body = {
            "messages": [{"role": "user", "content": prompt}],
            "system": "You are a movie title matching assistant. Provide concise, factual reasoning.",
            "max_tokens": 128,
            "temperature": 0,
        }
        url = _base_url() + f"/model/{settings.BEDROCK_MODEL_ID}/invoke"
        headers = {
            "Authorization": f"Bearer {settings.BEDROCK_API_KEY}",
            "Content-Type": "application/json",
        }

        resp = httpx.post(url, headers=headers, json=body, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        text = (raw.get("outputs") or [{}])[0].get("text") or (
            raw.get("choices") or [{}]
        )[0].get("message", {}).get("content", "")
        return text.strip() if text.strip() else None
    except Exception as e:
        logger.info("bedrock_reasoning_unavailable", extra={"error": str(e)})
        return None


def score_and_decide(
    normalized: NormalizedTitle,
    candidates: list[CandidateResult],
    show_date: Optional[str],
    theater: Optional[str],
    id_to_row: Optional[dict[int, dict]] = None,
    page_metadata: Optional[dict] = None,
) -> TitleMatchResult:
    """Score all candidates, pick a winner, resolve parent_id, and determine decision."""
    if not candidates:
        return TitleMatchResult(
            suggested_movie_id=0,
            suggested_movie_title="Unknown",
            canonical_movie_id=0,
            confidence=0.0,
            decision="REVIEW",
            reasoning="No candidates available for scoring.",
            evidence={"fuzzy_top": [], "date_window": "NONE"},
            fired_ai=False,
        )

    # Compute composite score for each candidate
    scored: list[tuple[float, str, CandidateResult, int]] = []
    for idx, candidate in enumerate(candidates):
        composite, date_label = _compute_composite_score(candidate, normalized, show_date)
        scored.append((composite, date_label, candidate, idx))

    # Sort by composite score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best_date_label, winner, winner_idx = scored[0]

    # Apply recency boost if winner is for a MOVIE event type
    if normalized.event_type == 'MOVIE':
        recency = _recency_boost(candidates, winner_idx)
        best_score = min(1.0, best_score + recency)

    # Metadata checks require id_to_row to look up master-side runtime/director/cast fields.
    # Stage 4: apply page metadata checks if available
    pre_metadata_score = best_score
    page_runtime = None
    page_director = None
    page_cast = None
    runtime_label = 'RUNTIME_UNKNOWN'
    director_label = 'DIRECTOR_UNKNOWN'
    cast_label = 'CAST_UNKNOWN'

    if page_metadata and id_to_row is not None:
        page_runtime = page_metadata.get('extracted_runtime_min')
        page_director = page_metadata.get('extracted_director')
        page_cast = page_metadata.get('extracted_cast')
        winner_row = id_to_row.get(winner.movie_master_id, {})
        master_runtime = winner_row.get('running_time')
        master_director = winner_row.get('director')
        master_cast = winner_row.get('cast_list')

        rt_boost, runtime_label = _runtime_check(page_runtime, master_runtime)
        dir_boost, director_label = _director_check(page_director, master_director)
        cast_boost, cast_label = _cast_check(page_cast, master_cast)
        best_score = min(1.0, best_score + rt_boost + dir_boost + cast_boost)

    # Resolve parent_id
    if id_to_row is not None:
        canonical_movie_id = _resolve_parent(winner.movie_master_id, id_to_row)
    else:
        canonical_movie_id = winner.movie_master_id

    # Determine decision
    if normalized.event_type in ('MULTI_FILM', 'NON_MOVIE'):
        decision = f"REVIEW_{normalized.event_type}"
    elif best_score >= 0.90:
        decision = "AUTO_ACCEPT"
    else:
        decision = "REVIEW"

    # Build evidence dict
    eliminated_list = [
        {
            "id": c.movie_master_id,
            "title": c.movie_title,
            "score": round(s, 4),
            "why": f"score={round(s, 4):.2f} (winner scored {round(pre_metadata_score, 4):.2f} pre-metadata, {round(best_score, 4):.2f} final)",
        }
        for s, _, c, _ in scored[1:]
    ]
    evidence = {
        "fuzzy_top": [
            {"id": c.movie_master_id, "title": c.movie_title, "score": round(c.score, 4)}
            for c in candidates[:5]
        ],
        "date_window": best_date_label,
        "edition_check": (
            "live_action_vs_animation_check"
            if 'Live Action' in normalized.edition_markers
            else "none"
        ),
        "eliminated": eliminated_list[:4],
        "runtime_check": {
            "page": page_runtime,
            "master": id_to_row.get(winner.movie_master_id, {}).get('running_time') if id_to_row else None,
            "label": runtime_label,
        },
        "director_check": {
            "page": page_director,
            "master": id_to_row.get(winner.movie_master_id, {}).get('director') if id_to_row else None,
            "label": director_label,
        },
    }

    # Generate reasoning — try Bedrock first, fall back to template
    ai_reasoning = _call_bedrock_reasoning(normalized, winner, best_score, candidates)
    fired_ai = ai_reasoning is not None
    reasoning = ai_reasoning or _generate_reasoning(
        normalized, winner, best_score, best_date_label, fired_ai=False
    )

    return TitleMatchResult(
        suggested_movie_id=winner.movie_master_id,
        suggested_movie_title=winner.movie_title,
        canonical_movie_id=canonical_movie_id,
        confidence=round(best_score, 4),
        decision=decision,
        reasoning=reasoning,
        evidence=evidence,
        fired_ai=fired_ai,
    )

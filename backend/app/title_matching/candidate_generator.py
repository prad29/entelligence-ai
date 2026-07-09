from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

from rapidfuzz import process, fuzz

from app.title_matching.types import NormalizedTitle, CandidateResult

if TYPE_CHECKING:
    from app.title_matching.semantic_index import VespaSemanticIndex

logger = logging.getLogger(__name__)

_JUNK_TITLES = frozenset(['3', '4', 'la', 'prince', 'phoenix', 'the order', 'night', 'king', 'nix'])

# Franchise map: (franchise_hint, ordinal) → movie_master_id
# These are the known IDs from Movie Master; expand as needed
FRANCHISE_MAP: dict[tuple[str, int], int] = {
    ('harry_potter', 1): 14039,
    ('harry_potter', 2): 14038,
    ('harry_potter', 3): 14061,
    ('harry_potter', 4): 14062,
    ('harry_potter', 5): 14063,
    ('harry_potter', 6): 14561,
    ('harry_potter', 7): 11626,   # 7/1 = part 1
    ('harry_potter', 8): 13837,   # 7/2 = part 2 (ordinal 8)
    ('toy_story', 1): 13868,
    ('toy_story', 2): 14046,
    ('toy_story', 3): 10731,
    ('toy_story', 4): 105988,
}


class CandidateGenerator:
    def __init__(
        self,
        master_rows: list[dict],
        semantic_index: Optional["VespaSemanticIndex"] = None,
    ) -> None:
        self._rows = master_rows
        self._titles = [r['movie_title'] for r in master_rows]
        self._id_to_row: dict[int, dict] = {r['id']: r for r in master_rows}
        self._junk = _JUNK_TITLES | {t.lower() for t in self._titles if len(t) <= 3}
        self._semantic_index = semantic_index

    def _semantic_search(
        self,
        query: str,
        exclude_ids: set[int],
        k: int,
    ) -> list[CandidateResult]:
        """Semantic hybrid search via Vespa. Returns [] if index unavailable."""
        if self._semantic_index is None or k <= 0:
            return []
        try:
            from app.config import settings
            from app.title_matching.semantic_index import get_embedding

            query_embedding = get_embedding(query, settings)
            if query_embedding is None:
                return []

            hits = self._semantic_index.search(
                query_embedding=query_embedding,
                query_text=query,
                k=k,
                exclude_ids=exclude_ids,
            )

            results: list[CandidateResult] = []
            for mid, score in hits:
                row = self._id_to_row.get(mid)
                if row is None or mid in exclude_ids:
                    continue
                results.append(CandidateResult(
                    movie_master_id=mid,
                    movie_title=row['movie_title'],
                    release_date=row.get('release_date'),
                    cover_image=row.get('cover_image'),
                    score=score,
                    source='semantic',
                ))
                exclude_ids.add(mid)
            return results
        except Exception as exc:
            logger.debug("semantic_search failed: %s", exc)
            return []

    def generate(
        self,
        normalized: NormalizedTitle,
        aliases: dict[str, int],    # normalized_alias.lower() → movie_master_id
        k: int = 10,
    ) -> list[CandidateResult]:
        candidates: list[CandidateResult] = []

        # 1. Alias / franchise map hits (strong prior)
        alias_key = normalized.cleaned.lower()
        if alias_key in aliases:
            mid = aliases[alias_key]
            row = self._id_to_row.get(mid)
            if row:
                candidates.append(CandidateResult(
                    movie_master_id=mid,
                    movie_title=row['movie_title'],
                    release_date=row.get('release_date'),
                    cover_image=row.get('cover_image'),
                    score=0.95,
                    source='alias',
                ))

        if normalized.franchise_hint and normalized.ordinal:
            fmap_id = FRANCHISE_MAP.get((normalized.franchise_hint, normalized.ordinal))
            if fmap_id:
                row = self._id_to_row.get(fmap_id)
                if row:
                    candidates.append(CandidateResult(
                        movie_master_id=fmap_id,
                        movie_title=row['movie_title'],
                        release_date=row.get('release_date'),
                        cover_image=row.get('cover_image'),
                        score=0.95,
                        source='franchise_map',
                    ))

        # 2. Guarded fuzzy matching
        query = normalized.cleaned
        if self._titles:
            fuzzy_hits = process.extract(query, self._titles, scorer=fuzz.WRatio, limit=k + 5)
        else:
            fuzzy_hits = []

        existing_ids = {c.movie_master_id for c in candidates}
        for title, score, idx in fuzzy_hits:
            row = self._rows[idx]
            mid = row['id']
            if mid in existing_ids:
                continue
            if title.lower() in self._junk:
                continue
            # Token coverage guard: candidate must cover ≥60% of query tokens
            query_tokens = set(query.lower().split())
            cand_tokens = set(title.lower().split())
            if query_tokens and len(query_tokens & cand_tokens) / len(query_tokens) < 0.60:
                if score < 90:    # only skip on low score; high score (≥90) overrides coverage
                    continue
            # Ordinal hard constraint
            if normalized.ordinal and _has_conflicting_ordinal(title, normalized.ordinal):
                continue
            candidates.append(CandidateResult(
                movie_master_id=mid,
                movie_title=title,
                release_date=row.get('release_date'),
                cover_image=row.get('cover_image'),
                score=score / 100.0 * 0.5,   # scale fuzzy 0–100 to 0–0.5 weight range
                source='fuzzy',
                raw_fuzzy_score=score,
            ))
            existing_ids.add(mid)
            if len(candidates) >= k:
                break

        # 3. Semantic hybrid search (fills remaining slots after fuzzy)
        if len(candidates) < k and self._semantic_index is not None:
            semantic_hits = self._semantic_search(
                query=normalized.cleaned,
                exclude_ids={c.movie_master_id for c in candidates},
                k=k - len(candidates),
            )
            candidates.extend(semantic_hits)

        return candidates[:k]


def _has_conflicting_ordinal(title: str, query_ordinal: int) -> bool:
    nums = re.findall(r'\b(\d+)\b', title)
    roman_map = {'i': 1, 'ii': 2, 'iii': 3, 'iv': 4, 'v': 5, 'vi': 6, 'vii': 7, 'viii': 8, 'ix': 9}
    romans = re.findall(r'\b(I{1,3}|IV|VI{0,3}|IX)\b', title, re.IGNORECASE)
    found = (
        [int(n) for n in nums if 1 <= int(n) <= 10]
        + [roman_map[r.lower()] for r in romans if r.lower() in roman_map]
    )
    if not found:
        return False
    return all(f != query_ordinal for f in found)

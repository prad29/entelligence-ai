from __future__ import annotations

import logging
from typing import Optional

from app.title_matching.normalizer import normalize_title
from app.title_matching.candidate_generator import CandidateGenerator
from app.title_matching.decision_engine import score_and_decide
from app.title_matching.types import TitleMatchResult

logger = logging.getLogger(__name__)


class TitleMatchEngine:
    def __init__(self, candidate_gen: CandidateGenerator, aliases: dict[str, int]) -> None:
        self._gen = candidate_gen
        self._aliases = aliases
        self._id_to_row = candidate_gen._id_to_row

    def match(
        self,
        title: str,
        show_date: Optional[str] = None,
        theater: Optional[str] = None,
        ticketing_url: Optional[str] = None,
    ) -> TitleMatchResult:
        normalized = normalize_title(title)
        candidates = self._gen.generate(normalized, self._aliases)

        if not candidates:
            return TitleMatchResult(
                suggested_movie_id=0,
                suggested_movie_title="Unknown",
                canonical_movie_id=0,
                confidence=0.0,
                decision="REVIEW",
                reasoning=f"No candidates found for '{title}'. Manual review required.",
                evidence={"fuzzy_top": [], "date_window": "NONE"},
                fired_ai=False,
            )

        result = score_and_decide(
            normalized,
            candidates,
            show_date,
            theater,
            id_to_row=self._id_to_row,
        )

        # Stage 2: Evidence fetcher (best-effort — never raises)
        if ticketing_url:
            try:
                from app.title_matching.evidence_fetcher import fetch_evidence
                evidence = fetch_evidence(ticketing_url)
                if evidence.ticketing_poster_url:
                    result.ticketing_poster_url = evidence.ticketing_poster_url
                import dataclasses
                result.page_metadata = dataclasses.asdict(evidence)
            except Exception as exc:
                logger.debug("evidence_fetch_skipped url=%s error=%s", ticketing_url, exc)

        # Attach cover_image from winner
        winner_row = self._id_to_row.get(result.suggested_movie_id)
        if winner_row:
            img = winner_row.get('cover_image')
            if img and 'noimage' not in (img or '').lower():
                result.cover_image = img

        return result

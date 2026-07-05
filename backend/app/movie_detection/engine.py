import logging
import re
from typing import Optional

from app.movie_detection.types import MovieFormatApprovedMapping, MovieFormatDetectionResult
from app.detection.normalizer import (
    track_a_clean,
    track_b_clean,
    track_c_tokens,
)

logger = logging.getLogger(__name__)

_IGNORE_TOKENS: frozenset[str] = frozenset(
    {
        "cc",
        "closed caption",
        "audio description",
        "reserved seating",
        "stadium",
        "no passes",
        "laser",
        "amc signature recliners",
        "prime at amc",
        "undefined",
        "•",
        "",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _concat_form(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


class MovieFormatMappingIndex:
    def __init__(self, mappings: list[MovieFormatApprovedMapping]) -> None:
        self.mappings = sorted(mappings, key=lambda m: m.priority_tier)
        self._track_a: dict[str, MovieFormatApprovedMapping] = {m.norm_track_a: m for m in self.mappings}
        self._track_b: dict[str, MovieFormatApprovedMapping] = {m.norm_track_b: m for m in self.mappings}


class MovieFormatEngine:
    def __init__(self, index: MovieFormatMappingIndex) -> None:
        self.index = index

    def get_all_formats(self) -> list[str]:
        return sorted({m.format for m in self.index.mappings})

    def _split_segments(self, amenity: str) -> list[str]:
        raw_segments = amenity.split("|")
        clean: list[str] = []
        for seg in raw_segments:
            s = seg.strip()
            if s.lower() in _IGNORE_TOKENS or s == "•":
                continue
            clean.append(s)
        return clean

    def _match_segment(
        self, segment: str, position: int
    ) -> Optional[tuple[MovieFormatApprovedMapping, str, str]]:
        norm_a = track_a_clean(segment)
        if norm_a in self.index._track_a:
            m = self.index._track_a[norm_a]
            return (m, "A", f"Bucket Priority {m.priority_tier}")

        norm_b = track_b_clean(segment)
        if norm_b in self.index._track_b:
            m = self.index._track_b[norm_b]
            return (m, "B", f"Bucket Priority {m.priority_tier}")

        try:
            from app.config import settings
            min_len = settings.TRACK_C_MIN_LEN
        except Exception:
            min_len = 4

        query_tokens = track_c_tokens(segment)
        concat = _concat_form(segment)

        if query_tokens or (concat and len(concat) >= min_len):
            best_score = 0.0
            best_mapping: Optional[MovieFormatApprovedMapping] = None

            for m in self.index.mappings:
                if not m.norm_track_c:
                    continue

                kw_concat = _concat_form(m.keyword)
                if not m.norm_track_c and not kw_concat:
                    continue

                token_match = bool(m.norm_track_c) and all(t in query_tokens for t in m.norm_track_c)
                concat_match = (
                    len(kw_concat) >= min_len
                    and len(concat) >= len(kw_concat)
                    and concat.startswith(kw_concat)
                )

                score = 1.0 if (concat_match or token_match) else 0.0

                if score > best_score:
                    best_score = score
                    best_mapping = m

            if best_mapping and best_score >= 0.5:
                return (best_mapping, "C", f"Bucket Priority {best_mapping.priority_tier}")

        return None

    def detect(self, amenity: str) -> MovieFormatDetectionResult:
        stripped = amenity.strip() if amenity else ""
        if not stripped:
            return MovieFormatDetectionResult(
                movie_format="2D",
                match_track="none",
                confidence=1.0,
                match_source="Empty Input",
                fired_ai=False,
            )

        segments = self._split_segments(amenity)
        best_hit: Optional[tuple[MovieFormatApprovedMapping, str, str, int]] = None

        for pos, seg in enumerate(segments):
            result = self._match_segment(seg, pos)
            if result is None:
                continue
            mapping, track, source = result
            if best_hit is None:
                best_hit = (mapping, track, source, pos)
            else:
                prev_mapping, prev_track, prev_source, prev_pos = best_hit
                if mapping.priority_tier < prev_mapping.priority_tier:
                    best_hit = (mapping, track, source, pos)
                elif mapping.priority_tier == prev_mapping.priority_tier:
                    if pos < prev_pos:
                        best_hit = (mapping, track, source, pos)
                    elif pos == prev_pos:
                        kw_len = len(mapping.keyword)
                        prev_kw_len = len(prev_mapping.keyword)
                        if kw_len > prev_kw_len:
                            best_hit = (mapping, track, source, pos)
                        elif kw_len == prev_kw_len:
                            track_order = {"A": 0, "B": 1, "C": 2}
                            if track_order.get(track, 9) < track_order.get(prev_track, 9):
                                best_hit = (mapping, track, source, pos)

        if best_hit is not None:
            mapping, track, source, pos = best_hit
            return MovieFormatDetectionResult(
                movie_format=mapping.format,
                match_track=track,
                confidence=1.0 if track == "A" else (0.9 if track == "B" else 0.75),
                matched_keyword=mapping.keyword,
                detected_keyword=mapping.keyword,
                match_source=source,
                fired_ai=False,
            )

        logger.info("movie_format_ai_invocation", extra={"amenity": amenity, "ai_invocation": True})
        return MovieFormatDetectionResult(
            movie_format="2D",
            match_track="none",
            confidence=0.0,
            match_source="No Match",
            fired_ai=True,
        )

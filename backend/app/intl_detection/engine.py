import logging
import re
from typing import Optional

from app.intl_detection.types import IntlApprovedMapping, IntlDetectionResult
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


class IntlMappingIndex:
    def __init__(self, mappings: list[IntlApprovedMapping]) -> None:
        self.mappings = sorted(mappings, key=lambda m: m.priority_tier)

        # First-writer-wins for every exact-form index, not just _concat_exact:
        # mappings are sorted by priority_tier ascending, so when two keywords
        # collide on a normalized form (e.g. the real spreadsheet's
        # "KINOEVOLUTION" appearing verbatim in both P1 and P3), the
        # higher-priority (lower-numbered tier) mapping must win the slot.
        self._exact: dict[str, IntlApprovedMapping] = {}
        self._track_a: dict[str, IntlApprovedMapping] = {}
        self._track_b: dict[str, IntlApprovedMapping] = {}
        for m in self.mappings:
            self._exact.setdefault(m.norm_exact, m)
            self._track_a.setdefault(m.norm_track_a, m)
            self._track_b.setdefault(m.norm_track_b, m)
        self._track_c: list[IntlApprovedMapping] = [m for m in self.mappings if m.norm_track_c]

        # Pre-built concat-form index for O(1) exact match, below the TRACK_C_MIN_LEN
        # guard. Intl has short keywords (XD, XL, LED, 70MM) shorter than 4 chars,
        # so an unguarded exact concat lookup is required for them to ever match.
        # Mappings are already sorted by priority_tier asc, so first writer wins
        # (highest-priority format) when two keywords share a concat form.
        self._concat_exact: dict[str, IntlApprovedMapping] = {}
        for m in self.mappings:
            cf = _concat_form(m.amenity_keyword)
            if cf and cf not in self._concat_exact:
                self._concat_exact[cf] = m


class IntlScreenFormatEngine:
    def __init__(self, index: IntlMappingIndex) -> None:
        self.index = index

    def get_all_formats(self) -> list[str]:
        return sorted({m.screen_format for m in self.index.mappings})

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
        self, segment: str
    ) -> Optional[tuple[IntlApprovedMapping, str]]:
        norm_a = track_a_clean(segment)
        if norm_a in self.index._track_a:
            m = self.index._track_a[norm_a]
            return (m, "A")

        norm_b = track_b_clean(segment)
        if norm_b in self.index._track_b:
            m = self.index._track_b[norm_b]
            return (m, "B")

        try:
            from app.config import settings
            min_len = settings.TRACK_C_MIN_LEN
        except Exception:
            min_len = 4

        query_tokens = track_c_tokens(segment)
        concat = _concat_form(segment)

        # Track C — sub-check 1: exact concat equality (no min_len guard). Covers
        # short keywords like "XD", "XL", "70MM" that would otherwise never clear
        # the min_len threshold below.
        if concat and concat in self.index._concat_exact:
            m = self.index._concat_exact[concat]
            return (m, "C")

        # Track C — sub-check 2: token set match + prefix match (min_len guard kept).
        if query_tokens or (concat and len(concat) >= min_len):
            best_score = 0.0
            best_mapping: Optional[IntlApprovedMapping] = None

            for m in self.index._track_c:
                kw_concat = _concat_form(m.amenity_keyword)

                token_match = all(t in query_tokens for t in m.norm_track_c)
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
                return (best_mapping, "C")

        return None

    def detect(self, amenity: str) -> IntlDetectionResult:
        stripped = amenity.strip() if amenity else ""
        if not stripped:
            return IntlDetectionResult(
                screen_format="Standard",
                match_track="none",
                confidence=0.0,
                match_source="No Match",
                fired_ai=False,
            )

        segments = self._split_segments(amenity)
        best_hit: Optional[tuple[IntlApprovedMapping, str, int]] = None

        for pos, seg in enumerate(segments):
            result = self._match_segment(seg)
            if result is None:
                continue
            mapping, track = result
            if best_hit is None:
                best_hit = (mapping, track, pos)
            else:
                prev_mapping, prev_track, prev_pos = best_hit
                if mapping.priority_tier < prev_mapping.priority_tier:
                    best_hit = (mapping, track, pos)
                elif mapping.priority_tier == prev_mapping.priority_tier:
                    if pos < prev_pos:
                        best_hit = (mapping, track, pos)
                    elif pos == prev_pos:
                        kw_len = len(mapping.amenity_keyword)
                        prev_kw_len = len(prev_mapping.amenity_keyword)
                        if kw_len > prev_kw_len:
                            best_hit = (mapping, track, pos)
                        elif kw_len == prev_kw_len:
                            track_order = {"A": 0, "B": 1, "C": 2}
                            if track_order.get(track, 9) < track_order.get(prev_track, 9):
                                best_hit = (mapping, track, pos)

        if best_hit is not None:
            mapping, track, _pos = best_hit
            return IntlDetectionResult(
                screen_format=mapping.screen_format,
                match_track=track,
                confidence=1.0 if track == "A" else (0.9 if track == "B" else 0.75),
                matched_keyword=mapping.amenity_keyword,
                detected_keyword=mapping.amenity_keyword,
                priority_tier=mapping.priority_tier,
                match_source="Keyword Match",
                fired_ai=False,
            )

        return IntlDetectionResult(
            screen_format="Standard",
            match_track="none",
            confidence=0.0,
            match_source="No Match",
            fired_ai=False,
        )

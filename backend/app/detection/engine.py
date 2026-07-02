import logging
import re
from typing import Optional

from app.detection.types import ApprovedMapping, CircuitOverrideEntry, DetectionResult
from app.detection.normalizer import (
    normalize_string,
    track_a_clean,
    track_b_clean,
    track_c_tokens,
)

logger = logging.getLogger(__name__)

# Circuits that trigger Layer-0 VIP override
_VIP_CIRCUITS: dict[str, str] = {
    "caribbean cinemas - us territory": "Caribbean VIP",
    "cineplex entertainment": "VIP Cineplex",
}

# Tokens that should be ignored when they appear as standalone pipe segments
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
        "undefined",
        "•",  # bullet •
        "",
    }
)

# P6 keywords map to Standard deliberately (no AI)
_P6_TIER = 6

# Strip all non-alphanumeric for the concatenated Track C form
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _concat_form(text: str) -> str:
    """
    Produce a single alnum-only concatenated string for Track C corruption matching.

    Example: 'I--Maxentral' → 'imaxentral'
    """
    return _NON_ALNUM.sub("", text.lower())


class MappingIndex:
    def __init__(
        self,
        mappings: list[ApprovedMapping],
        overrides: list[CircuitOverrideEntry],
        aliases: dict[str, str] | None = None,
        # Accept circuit_aliases as an alias for backward-compat with existing tests
        circuit_aliases: dict[str, str] | None = None,
    ) -> None:
        self.mappings = sorted(mappings, key=lambda m: m.priority_tier)
        self.overrides = overrides
        # Accept either kwarg name; aliases takes precedence
        self.aliases = aliases if aliases is not None else (circuit_aliases or {})

        # Build lookup dicts for fast access
        self._exact: dict[str, ApprovedMapping] = {m.norm_exact: m for m in self.mappings}
        self._track_a: dict[str, ApprovedMapping] = {m.norm_track_a: m for m in self.mappings}
        self._track_b: dict[str, ApprovedMapping] = {m.norm_track_b: m for m in self.mappings}
        # Override index uses all-lowercase keys: (keyword_lower, circuit_lower)
        self._override_index: dict[tuple[str, str], str] = {
            (o.keyword.lower(), o.circuit_name.lower()): o.screen_format
            for o in self.overrides
        }


class ScreenFormatEngine:
    def __init__(self, index: MappingIndex) -> None:
        self.index = index

    def _resolve_circuit(self, circuit: str) -> str:
        """
        Normalize circuit name via alias map.

        Always returns a lowercase string so it can be used directly as:
        - A key into _override_index (which uses lowercase keys)
        - A membership check against _VIP_CIRCUITS (lowercase keys)
        """
        normalized = circuit.strip().lower()
        # Alias may map to a mixed-case canonical — lowercase the result too
        resolved = self.index.aliases.get(normalized, normalized)
        return resolved.lower()

    def _split_segments(self, amenity: str) -> list[str]:
        """Split on pipe, trim, drop noise tokens."""
        raw_segments = amenity.split("|")
        clean: list[str] = []
        for seg in raw_segments:
            s = seg.strip()
            if s.lower() in _IGNORE_TOKENS or s == "•":
                continue
            clean.append(s)
        return clean

    def _match_segment(
        self, segment: str, norm_circuit_lower: str, position: int
    ) -> Optional[tuple[ApprovedMapping, str, str]]:
        """
        Try to match a single segment. Returns (mapping, match_track, match_source_label)
        or None.

        norm_circuit_lower must already be lowercased for override_index lookups.
        """
        # Track A: exact normalized
        norm_a = track_a_clean(segment)
        if norm_a in self.index._track_a:
            m = self.index._track_a[norm_a]
            return (m, "A", f"Bucket Priority {m.priority_tier}")

        # Track B: stopword-cleaned
        norm_b = track_b_clean(segment)
        if norm_b in self.index._track_b:
            m = self.index._track_b[norm_b]
            return (m, "B", f"Bucket Priority {m.priority_tier}")

        # Track C: prefix-anchored token matching (handles corruption/concatenation)
        # Strategy: build both the regular token set AND the concatenated alnum form,
        # then check if any keyword token is a prefix of any query form.
        try:
            from app.config import settings
            min_len = settings.TRACK_C_MIN_LEN
        except Exception:
            min_len = 4

        query_tokens = track_c_tokens(segment)
        concat = _concat_form(segment)

        if query_tokens or (concat and len(concat) >= min_len):
            best_score = 0.0
            best_mapping: Optional[ApprovedMapping] = None

            for m in self.index.mappings:
                if not m.norm_track_c:
                    continue

                eligible_kw_tokens = [t for t in m.norm_track_c if len(t) >= min_len]
                if not eligible_kw_tokens:
                    continue

                token_matched = 0
                concat_matched = 0
                for kw_tok in eligible_kw_tokens:
                    # Regular token match: kw_tok must be a prefix of a query whitespace token
                    if any(q_tok.startswith(kw_tok) for q_tok in query_tokens):
                        token_matched += 1
                    # Concat form match: kw_tok must be a prefix of the concatenated segment
                    if len(concat) >= len(kw_tok) and concat.startswith(kw_tok):
                        concat_matched += 1

                total_kw = len(eligible_kw_tokens)

                # For concat matches: if the concat form starts with the full keyword prefix,
                # that is a corruption/concatenation signal — use this with high confidence.
                if concat_matched == total_kw and total_kw > 0:
                    score = concat_matched / max(total_kw, 1)
                elif token_matched == total_kw and total_kw > 0:
                    # ALL keyword tokens matched in query tokens — full token overlap
                    score = 1.0
                else:
                    # Partial matches are not enough for Track C
                    score = 0.0

                if score > best_score:
                    best_score = score
                    best_mapping = m

            if best_mapping and best_score >= 0.5:
                return (best_mapping, "C", f"Bucket Priority {best_mapping.priority_tier}")

        return None

    def _resolve_circuit_format(
        self, mapping: ApprovedMapping, norm_circuit_lower: str
    ) -> str:
        """
        Apply circuit-sensitive resolution (§5 rules).

        norm_circuit_lower must be lowercased to match override_index keys.
        """
        if not mapping.circuit_name:
            # Not circuit-sensitive → return generic
            return mapping.screen_format

        # Rule 1: exact circuit match in override index (both keys are lowercase)
        key = (mapping.amenity_keyword.lower(), norm_circuit_lower)
        if key in self.index._override_index:
            return self.index._override_index[key]

        # Rule 2: NA default if circuit doesn't match
        if mapping.na_default:
            return mapping.na_default

        # Rule 3: foreign brand → Standard
        return "Standard"

    def detect(self, amenity: str, circuit_name: str = "") -> DetectionResult:
        # Handle empty / whitespace-only input
        stripped = amenity.strip() if amenity else ""
        if not stripped:
            return DetectionResult(
                screen_format="Standard",
                match_track="none",
                confidence=1.0,
                match_source="Empty Input",
                fired_ai=False,
            )

        # Resolve circuit: returns lowercase alias canonical or lowercase input
        norm_circuit_lower = self._resolve_circuit(circuit_name) if circuit_name else ""

        # Log unknown circuits for the aliases screen
        if circuit_name and circuit_name.strip():
            known = circuit_name.strip().lower() in self.index.aliases
            if not known:
                logger.warning(
                    "unknown_circuit",
                    extra={"unknown_circuit": circuit_name.strip()},
                )

        # Layer 0 — VIP Override (absolute highest priority)
        if norm_circuit_lower in _VIP_CIRCUITS:
            for seg in self._split_segments(amenity):
                if "vip" in seg.lower():
                    vip_format = _VIP_CIRCUITS[norm_circuit_lower]
                    return DetectionResult(
                        screen_format=vip_format,
                        match_track="A",
                        confidence=1.0,
                        matched_keyword="VIP",
                        detected_keyword="VIP",
                        circuit_name=circuit_name,
                        match_source="VIP Override",
                        fired_ai=False,
                    )

        # Layer 1 — Priority Bucket
        segments = self._split_segments(amenity)
        best_hit: Optional[tuple[ApprovedMapping, str, str, int]] = None  # (mapping, track, src, pos)

        for pos, seg in enumerate(segments):
            result = self._match_segment(seg, norm_circuit_lower, pos)
            if result is None:
                continue
            mapping, track, source = result
            if best_hit is None:
                best_hit = (mapping, track, source, pos)
            else:
                prev_mapping, prev_track, prev_source, prev_pos = best_hit
                # Tie-break: priority ASC → circuit-match → circuit-scoped → position ASC → specificity DESC → track A<B<C
                if mapping.priority_tier < prev_mapping.priority_tier:
                    best_hit = (mapping, track, source, pos)
                elif mapping.priority_tier == prev_mapping.priority_tier:
                    new_circuit_match = mapping.circuit_name is not None and mapping.circuit_name.lower() == norm_circuit_lower
                    prev_circuit_match = prev_mapping.circuit_name is not None and prev_mapping.circuit_name.lower() == norm_circuit_lower
                    new_is_circuit = mapping.circuit_name is not None
                    prev_is_circuit = prev_mapping.circuit_name is not None

                    if new_circuit_match and not prev_circuit_match:
                        best_hit = (mapping, track, source, pos)
                    elif not new_circuit_match and prev_circuit_match:
                        pass  # keep prev
                    elif new_is_circuit and not prev_is_circuit:
                        best_hit = (mapping, track, source, pos)
                    elif not new_is_circuit and prev_is_circuit:
                        pass  # keep prev
                    else:
                        # Both same circuit-specificity → fall back to existing position/specificity/track rules
                        if pos < prev_pos:
                            best_hit = (mapping, track, source, pos)
                        elif pos == prev_pos:
                            kw_len = len(mapping.amenity_keyword)
                            prev_kw_len = len(prev_mapping.amenity_keyword)
                            if kw_len > prev_kw_len:
                                best_hit = (mapping, track, source, pos)
                            elif kw_len == prev_kw_len:
                                track_order = {"A": 0, "B": 1, "C": 2}
                                if track_order.get(track, 9) < track_order.get(prev_track, 9):
                                    best_hit = (mapping, track, source, pos)

        if best_hit is not None:
            mapping, track, source, pos = best_hit
            # Resolve circuit-sensitive format (norm_circuit_lower already lowercased)
            resolved_format = self._resolve_circuit_format(mapping, norm_circuit_lower)

            # P6 deliberate Standard → no AI
            is_deliberate_standard = mapping.priority_tier == _P6_TIER

            match_source_label = source
            if resolved_format == "Standard" and not is_deliberate_standard:
                match_source_label = f"{source} (Circuit Unscoped → Standard)"
            elif is_deliberate_standard:
                match_source_label = f"{source} (Standard)"

            return DetectionResult(
                screen_format=resolved_format,
                match_track=track,
                confidence=1.0 if track == "A" else (0.9 if track == "B" else 0.75),
                matched_keyword=mapping.amenity_keyword,
                detected_keyword=mapping.amenity_keyword,
                circuit_name=circuit_name or None,
                na_default=mapping.na_default,
                match_source=match_source_label,
                fired_ai=False,
            )

        # Layer 2 — True no-match → mark as AI territory
        # Actual Bedrock call is handled by the router/worker; engine just signals it
        logger.info(
            "ai_invocation",
            extra={
                "amenity": amenity,
                "circuit": circuit_name,
                "ai_invocation": True,
            },
        )
        return DetectionResult(
            screen_format="Standard",
            match_track="none",
            confidence=0.0,
            match_source="No Match",
            fired_ai=True,
        )

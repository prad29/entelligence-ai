"""
ScreenFormatEngine — Layer 0 VIP override + Layer 1 priority bucket matching
with tie-break and circuit resolution.

Layer 2 (Bedrock AI) is stubbed via fired_ai=True; actual client wired in Phase 5.
"""

import re
import logging
from typing import Optional, Dict, List, Set, Tuple

from .types import ApprovedMapping, CircuitOverrideEntry, DetectionResult
from .normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens

logger = logging.getLogger(__name__)

# Tokens that must never be matched (case-insensitive, whole-token)
IGNORE_TOKENS: frozenset = frozenset([
    "cc",
    "closed caption",
    "audio description",
    "reserved seating",
    "stadium",
    "no passes",
    "laser",
    "amc signature recliners",
])

# VIP circuits: canonical circuit name lower -> branded VIP format
VIP_CIRCUITS: Dict[str, str] = {
    "caribbean cinemas - us territory": "Caribbean VIP",
    "cineplex entertainment": "VIP Cineplex",
}


class MappingIndex:
    """
    Pre-compiled index over a list of ApprovedMapping entries.

    Builds:
    - mappings: sorted by priority ASC, then len(keyword) DESC (most-specific first)
    - circuit_overrides: (keyword_lower, circuit_lower) -> screen_format
    - na_defaults: keyword_lower -> screen_format (when circuit unknown)
    - circuit_branded: set of formats that are exclusive to a specific circuit
    - circuit_aliases: raw_lower -> canonical_name
    - _overrides_list: raw CircuitOverrideEntry list for keyword membership checks
    """

    def __init__(
        self,
        mappings: List[ApprovedMapping],
        overrides: List[CircuitOverrideEntry],
        circuit_aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        # Sort: priority ASC, then keyword length DESC (more specific wins ties)
        self.mappings: List[ApprovedMapping] = sorted(
            mappings,
            key=lambda m: (m.priority_tier, -len(m.amenity_keyword)),
        )

        self._overrides_list: List[CircuitOverrideEntry] = overrides

        # (keyword_lower, circuit_lower) -> screen_format
        self.circuit_overrides: Dict[Tuple[str, str], str] = {}
        for entry in overrides:
            key = (entry.keyword.lower(), entry.circuit_name.lower())
            self.circuit_overrides[key] = entry.screen_format

        # keyword_lower -> na_default format
        self.na_defaults: Dict[str, str] = {}
        for m in mappings:
            if m.na_default:
                self.na_defaults[m.amenity_keyword.lower()] = m.na_default

        # All formats that exist exclusively as circuit-branded entries
        all_generic_formats: Set[str] = {m.screen_format for m in mappings if not m.circuit_name}
        all_override_formats: Set[str] = {e.screen_format for e in overrides}
        # circuit_branded = formats in overrides but NOT in generic mappings
        self.circuit_branded: Set[str] = all_override_formats - all_generic_formats

        # alias map: raw_lower -> canonical
        self.circuit_aliases: Dict[str, str] = {
            k.lower(): v for k, v in (circuit_aliases or {}).items()
        }


class ScreenFormatEngine:
    """
    Core detection engine.

    detect(amenity, circuit_name) runs:
      Layer 0 — empty-input guard + VIP override
      Layer 1 — priority bucket matching (exact / track A / B / C)
      Layer 2 flag — sets fired_ai=True when no match found
    """

    def __init__(self, index: MappingIndex) -> None:
        self.index = index

    # ------------------------------------------------------------------
    # Circuit normalisation
    # ------------------------------------------------------------------

    def normalize_circuit(self, circuit: str) -> Optional[str]:
        """
        1. Direct alias lookup (case-insensitive).
        2. Jaccard token-overlap fallback against alias keys (threshold 0.5).
        3. Returns None if no match found; logs a warning.
        """
        if not circuit or not circuit.strip():
            return None

        raw_lower = circuit.strip().lower()

        # Exact alias hit
        if raw_lower in self.index.circuit_aliases:
            return self.index.circuit_aliases[raw_lower]

        # Jaccard fallback
        query_tokens = set(re.split(r'\s+', raw_lower))
        best_score = 0.0
        best_canonical: Optional[str] = None

        for alias_lower, canonical in self.index.circuit_aliases.items():
            alias_tokens = set(re.split(r'\s+', alias_lower))
            intersection = len(query_tokens & alias_tokens)
            union = len(query_tokens | alias_tokens)
            if union == 0:
                continue
            score = intersection / union
            if score > best_score:
                best_score = score
                best_canonical = canonical

        if best_score >= 0.5:
            return best_canonical

        logger.warning("Unknown circuit name: %r (no alias match)", circuit)
        return None

    # ------------------------------------------------------------------
    # Main detection
    # ------------------------------------------------------------------

    def detect(self, amenity: str, circuit_name: str = "") -> DetectionResult:
        # --- Layer 0: empty input ---
        if not amenity or not amenity.strip():
            return DetectionResult(
                screen_format="Standard",
                detected_keyword=None,
                match_source="Empty Input",
                match_track=None,
                priority=None,
                confidence=1.0,
                fired_ai=False,
            )

        norm_circuit = self.normalize_circuit(circuit_name)
        circuit_lower = norm_circuit.lower() if norm_circuit else ""

        # --- Layer 0: VIP circuit override ---
        if circuit_lower in VIP_CIRCUITS and re.search(r'\bvip\b', amenity, re.IGNORECASE):
            fmt = VIP_CIRCUITS[circuit_lower]
            return DetectionResult(
                screen_format=fmt,
                detected_keyword="VIP",
                match_source="VIP Override",
                match_track="exact",
                priority=0,
                confidence=1.0,
                fired_ai=False,
            )

        # --- Layer 1: split amenity on pipe, filter noise tokens ---
        raw_tokens = [t.strip() for t in amenity.split('|') if t.strip()]
        filtered_tokens = []
        for t in raw_tokens:
            t_lower = t.lower()
            if t_lower in IGNORE_TOKENS:
                continue
            if t_lower in ('undefined', '•', ''):
                continue
            filtered_tokens.append(t)

        # hits: (priority, position, neg_specificity, track_rank, mapping, track)
        hits: List[Tuple] = []

        for pos, token in enumerate(filtered_tokens):
            tok_exact = normalize_string(token).lower()
            tok_a = track_a_clean(token)
            tok_b = track_b_clean(token)
            tok_c_list = track_c_tokens(token)

            for m in self.index.mappings:
                track: Optional[str] = None

                # Track exact
                if tok_exact == m.norm_exact:
                    track = "exact"
                # Track A: word-boundary match of keyword in cleaned token
                elif m.norm_track_a and re.search(
                    r'\b' + re.escape(m.norm_track_a) + r'\b', tok_a
                ):
                    track = "A"
                # Track B
                elif m.norm_track_b and re.search(
                    r'\b' + re.escape(m.norm_track_b) + r'\b', tok_b
                ):
                    track = "B"
                # Track C: prefix match of any token >= min_len
                elif m.norm_track_c:
                    for kw_c in m.norm_track_c:
                        if len(kw_c) >= 4:
                            for tc in tok_c_list:
                                if tc.startswith(kw_c):
                                    track = "C"
                                    break
                        if track:
                            break

                if track is not None:
                    track_rank = {"exact": 0, "A": 1, "B": 2, "C": 3}[track]
                    hits.append(
                        (m.priority_tier, pos, -len(m.amenity_keyword), track_rank, m, track)
                    )

        # --- No match → fire AI ---
        if not hits:
            return DetectionResult(
                screen_format="Standard",
                detected_keyword=None,
                match_source="No Match",
                match_track=None,
                priority=None,
                confidence=0.3,
                fired_ai=True,
            )

        # Tie-break:
        #   1. track_rank ASC (exact=0 beats A=1 beats B=2 beats C=3)
        #   2. priority ASC (P1 beats P2 etc.)
        #   3. specificity DESC (longer keyword wins at same priority+track)
        #   4. position ASC (earlier pipe token wins)
        #
        # Placing track_rank first ensures that an exact match of a longer
        # keyword (even at a higher priority number like P6) beats a partial
        # match on a shorter keyword at a lower priority number (P4).
        # e.g. "ACX Infinity 2D" exact (P6) beats "ACX Infinity" Track-A (P4).
        hits.sort(key=lambda h: (h[3], h[0], h[2], h[1]))
        best = hits[0]
        winner_mapping: ApprovedMapping = best[4]
        winner_track: str = best[5]

        # --- Circuit resolution (§5 4-step) ---
        kw_lower = winner_mapping.amenity_keyword.lower()

        # Determine if this keyword is circuit-sensitive
        override_keywords: Set[str] = {e.keyword.lower() for e in self.index._overrides_list}
        is_circuit_sensitive = (
            kw_lower in override_keywords or kw_lower in self.index.na_defaults
        )

        resolved_format = winner_mapping.screen_format
        match_qualifier = ""

        if is_circuit_sensitive:
            override_key = (kw_lower, circuit_lower)
            if override_key in self.index.circuit_overrides:
                # Step 1: confirmed circuit override
                resolved_format = self.index.circuit_overrides[override_key]
                match_qualifier = " (Circuit)"
            elif kw_lower in self.index.na_defaults:
                # Step 2: NA default (handles independent/unknown circuits)
                resolved_format = self.index.na_defaults[kw_lower]
                match_qualifier = ""
            elif kw_lower in override_keywords:
                # Step 3: keyword exists only as a circuit-specific override
                # and no NA default is defined — foreign/unknown circuit → Standard
                resolved_format = "Standard"
                match_qualifier = " (Circuit Unscoped -> Standard)"
            else:
                # Step 4: generic format, keep as-is
                resolved_format = winner_mapping.screen_format

        tier = winner_mapping.priority_tier
        # fired_ai is only True when there are NO hits at all (handled above)
        fired_ai = False

        # Build match_source label
        source = f"Bucket Priority {tier}{match_qualifier}"

        # Confidence by track quality
        confidence_map = {"exact": 1.0, "A": 0.9, "B": 0.8, "C": 0.7}
        confidence = confidence_map[winner_track]

        return DetectionResult(
            screen_format=resolved_format,
            detected_keyword=winner_mapping.amenity_keyword,
            match_source=source,
            match_track=winner_track,
            priority=tier,
            confidence=confidence,
            fired_ai=fired_ai,
        )

    def get_all_formats(self) -> List[str]:
        return list({m.screen_format for m in self.index.mappings})

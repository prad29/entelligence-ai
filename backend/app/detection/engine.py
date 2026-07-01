from typing import Optional
from app.detection.types import ApprovedMapping, CircuitOverrideEntry, DetectionResult
from app.detection.normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens


class MappingIndex:
    def __init__(
        self,
        mappings: list[ApprovedMapping],
        overrides: list[CircuitOverrideEntry],
        aliases: dict[str, str],
    ) -> None:
        self.mappings = sorted(mappings, key=lambda m: m.priority_tier)
        self.overrides = overrides
        self.aliases = aliases

        # Build lookup dicts for fast access
        self._exact: dict[str, ApprovedMapping] = {m.norm_exact: m for m in self.mappings}
        self._track_a: dict[str, ApprovedMapping] = {m.norm_track_a: m for m in self.mappings}
        self._track_b: dict[str, ApprovedMapping] = {m.norm_track_b: m for m in self.mappings}
        self._override_index: dict[tuple[str, str], str] = {
            (o.keyword.lower(), o.circuit_name.lower()): o.screen_format
            for o in self.overrides
        }


class ScreenFormatEngine:
    def __init__(self, index: MappingIndex) -> None:
        self.index = index

    def _resolve_circuit(self, circuit: str) -> str:
        normalized = circuit.strip().lower()
        return self.index.aliases.get(normalized, normalized)

    def get_all_formats(self) -> list[str]:
        """Return the deduplicated list of known screen format values."""
        seen: set[str] = set()
        result: list[str] = []
        for m in self.index.mappings:
            if m.screen_format not in seen:
                seen.add(m.screen_format)
                result.append(m.screen_format)
        return result

    def detect(self, amenity: str, circuit_name: str = "") -> DetectionResult:
        if not amenity:
            return DetectionResult(
                screen_format="UNKNOWN",
                match_track="none",
                confidence=0.0,
            )

        resolved_circuit = self._resolve_circuit(circuit_name) if circuit_name else ""

        # Check circuit override first
        if resolved_circuit:
            norm_a = track_a_clean(amenity)
            key = (norm_a, resolved_circuit)
            if key in self.index._override_index:
                return DetectionResult(
                    screen_format=self.index._override_index[key],
                    match_track="circuit_override",
                    confidence=1.0,
                    matched_keyword=amenity,
                    detected_keyword=amenity,
                    circuit_name=resolved_circuit,
                    match_source="circuit_override",
                )

        # Track A: exact normalized
        norm_a = track_a_clean(amenity)
        if norm_a in self.index._track_a:
            m = self.index._track_a[norm_a]
            return DetectionResult(
                screen_format=m.screen_format,
                match_track="A",
                confidence=1.0,
                matched_keyword=m.amenity_keyword,
                detected_keyword=m.amenity_keyword,
                circuit_name=m.circuit_name,
                na_default=m.na_default,
                match_source="layer1",
            )

        # Track B: stopword-cleaned
        norm_b = track_b_clean(amenity)
        if norm_b in self.index._track_b:
            m = self.index._track_b[norm_b]
            return DetectionResult(
                screen_format=m.screen_format,
                match_track="B",
                confidence=0.9,
                matched_keyword=m.amenity_keyword,
                detected_keyword=m.amenity_keyword,
                circuit_name=m.circuit_name,
                na_default=m.na_default,
                match_source="layer1",
            )

        # Track C: token intersection (Jaccard)
        query_tokens = track_c_tokens(amenity)
        if query_tokens:
            best_score = 0.0
            best_mapping: Optional[ApprovedMapping] = None
            for m in self.index.mappings:
                if not m.norm_track_c:
                    continue
                intersection = len(query_tokens & m.norm_track_c)
                union = len(query_tokens | m.norm_track_c)
                score = intersection / union if union > 0 else 0.0
                if score > best_score:
                    best_score = score
                    best_mapping = m
            if best_mapping and best_score >= 0.5:
                return DetectionResult(
                    screen_format=best_mapping.screen_format,
                    match_track="C",
                    confidence=best_score,
                    matched_keyword=best_mapping.amenity_keyword,
                    detected_keyword=best_mapping.amenity_keyword,
                    circuit_name=best_mapping.circuit_name,
                    na_default=best_mapping.na_default,
                    match_source="layer1",
                )

        # No Layer 1 match — signal Layer 2 AI
        return DetectionResult(
            screen_format="Standard",
            match_track="none",
            confidence=0.0,
            fired_ai=True,
            match_source="no_match",
        )

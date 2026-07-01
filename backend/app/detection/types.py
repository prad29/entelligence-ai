from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ApprovedMapping:
    amenity_keyword: str
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str]
    na_default: Optional[str]
    norm_exact: str
    norm_track_a: str
    norm_track_b: str
    norm_track_c: frozenset


@dataclass(frozen=True)
class CircuitOverrideEntry:
    keyword: str
    circuit_name: str
    screen_format: str


@dataclass
class BedrockSuggestion:
    suggested_screen_format: str
    confidence: float
    reasoning: str
    detected_keyword: Optional[str] = None


@dataclass
class DetectionResult:
    screen_format: str
    match_track: str
    confidence: float
    matched_keyword: Optional[str] = None
    circuit_name: Optional[str] = None
    na_default: Optional[str] = None
    diagnostics: Optional[dict] = field(default=None)
    # Layer 1 signal — True when no keyword matched and AI should be consulted
    fired_ai: bool = False
    match_source: str = "layer1"
    detected_keyword: Optional[str] = None
    # Layer 2 AI fields — populated after Bedrock classification
    ai_suggested_format: Optional[str] = None
    ai_reasoning: Optional[str] = None

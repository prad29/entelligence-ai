from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ApprovedMapping:
    amenity_keyword: str
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str] = None
    na_default: Optional[str] = None
    norm_exact: str = ""
    norm_track_a: str = ""
    norm_track_b: str = ""
    norm_track_c: list = field(default_factory=list)


@dataclass
class CircuitOverrideEntry:
    keyword: str
    circuit_name: str
    screen_format: str


@dataclass
class DetectionResult:
    screen_format: str
    detected_keyword: Optional[str]
    match_source: str
    match_track: Optional[str]
    priority: Optional[int]
    confidence: float
    fired_ai: bool
    ai_suggested_format: Optional[str] = None
    ai_reasoning: Optional[str] = None


@dataclass
class BedrockSuggestion:
    suggested_screen_format: str
    confidence: float
    reasoning: str
    detected_keyword: Optional[str] = None

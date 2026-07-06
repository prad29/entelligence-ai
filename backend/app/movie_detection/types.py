from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MovieFormatApprovedMapping:
    keyword: str
    format: str           # "70MM" | "35MM" | "3D" | "2D"
    priority_tier: int
    norm_exact: str
    norm_track_a: str
    norm_track_b: str
    norm_track_c: frozenset


@dataclass
class MovieFormatDetectionResult:
    movie_format: str
    match_track: str
    confidence: float
    matched_keyword: Optional[str] = None
    detected_keyword: Optional[str] = None
    match_source: Optional[str] = None
    fired_ai: bool = False
    ai_suggested_format: Optional[str] = None
    ai_reasoning: Optional[str] = None
    diagnostics: Optional[dict] = field(default=None)


@dataclass
class MovieFormatBedrockSuggestion:
    suggested_screen_format: str
    confidence: float
    reasoning: str
    detected_keyword: Optional[str] = None

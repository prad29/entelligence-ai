from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class IntlApprovedMapping:
    amenity_keyword: str
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str]
    na_default: Optional[str]
    norm_exact: str
    norm_track_a: str
    norm_track_b: str
    norm_track_c: frozenset


@dataclass
class IntlDetectionResult:
    screen_format: str
    match_track: str
    confidence: float
    matched_keyword: Optional[str] = None
    detected_keyword: Optional[str] = None
    priority_tier: Optional[int] = None
    match_source: Optional[str] = None
    fired_ai: bool = False
    diagnostics: Optional[dict] = field(default=None)

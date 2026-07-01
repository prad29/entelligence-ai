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
class DetectionResult:
    screen_format: str
    match_track: str
    confidence: float
    matched_keyword: Optional[str] = None
    circuit_name: Optional[str] = None
    na_default: Optional[str] = None
    diagnostics: Optional[dict] = field(default=None)

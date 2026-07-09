from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class ExtractionTier:
    T1_SIMPLE_HTTP = "T1_SIMPLE_HTTP"
    T2_HEADLESS = "T2_HEADLESS"
    T3_GEO_PROXY = "T3_GEO_PROXY"


class ExtractionPlatform:
    GENERIC = "GENERIC"
    INDY_SYSTEMS = "INDY_SYSTEMS"
    EVENT_CINEMAS = "EVENT_CINEMAS"
    VISTA = "VISTA"


@dataclass
class EvidenceResult:
    extraction_tier: str = ""
    extraction_platform: str = ""
    extraction_outcome: str = ""
    og_image: Optional[str] = None
    page_title: Optional[str] = None
    h1_text: Optional[str] = None
    runtime: Optional[str] = None
    director: Optional[str] = None
    cast: Optional[str] = None
    extracted_at: Optional[str] = None
    extra: dict = field(default_factory=dict)

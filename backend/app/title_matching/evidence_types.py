from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


class ExtractionTier:
    """String constants for extraction tiers."""

    T1_HTTP: str = "T1_HTTP"
    T2_HEADLESS: str = "T2_HEADLESS"
    T3_GEO_PROXY: str = "T3_GEO_PROXY"
    NONE: str = "NONE"


class ExtractionPlatform:
    """String constants for known ticketing platforms."""

    CINEMAPLUS: str = "CINEMAPLUS"
    AGILE_TICKETING: str = "AGILE_TICKETING"
    VIFF: str = "VIFF"
    INDY_SYSTEMS: str = "INDY_SYSTEMS"
    CINEPLEX_DE: str = "CINEPLEX_DE"
    EVENT_CINEMAS: str = "EVENT_CINEMAS"
    GENERIC: str = "GENERIC"


@dataclass
class EvidenceResult:
    """
    Holds extracted evidence gathered from a ticketing URL.

    extraction_outcome values:
        SUCCESS        — data was successfully extracted
        FAILED_T1      — T1_HTTP extraction failed
        FAILED_T2      — T2_HEADLESS extraction failed
        UNAVAILABLE    — the resource was unreachable or blocked
        NOT_ATTEMPTED  — extraction has not been started yet
    """

    ticketing_poster_url: Optional[str] = None
    page_title: Optional[str] = None
    extracted_runtime_min: Optional[int] = None
    extracted_director: Optional[str] = None
    extracted_cast: Optional[str] = None
    extracted_rating: Optional[str] = None
    extraction_platform: str = "UNKNOWN"
    extraction_tier: str = "NONE"
    extraction_outcome: str = "NOT_ATTEMPTED"
    extracted_at: Optional[str] = None

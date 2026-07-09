from __future__ import annotations

import logging

from app.title_matching.evidence_types import EvidenceResult
from .base import AbstractExtractor

logger = logging.getLogger(__name__)


class T2HeadlessExtractor(AbstractExtractor):
    """
    Tier-2 headless browser extractor.

    Stub implementation — full headless extraction will be added in a
    subsequent unit.  Returns FAILED_T2 so the orchestrator can handle
    escalation gracefully.
    """

    def extract(self, url: str, platform: str) -> EvidenceResult:
        logger.debug("t2_headless_extract url=%s platform=%s (stub)", url, platform)
        return EvidenceResult(
            extraction_tier="T2_HEADLESS",
            extraction_outcome="FAILED_T2",
            extraction_platform=platform,
        )


class T3GeoProxyExtractor(AbstractExtractor):
    """
    Tier-3 geo-proxy extractor.

    Stub implementation — full geo-proxy extraction will be added in a
    subsequent unit.  Returns FAILED_T2 (maps to a generic failure) so
    the orchestrator can handle the result gracefully.
    """

    def extract(self, url: str, platform: str) -> EvidenceResult:
        logger.debug("t3_geo_proxy_extract url=%s platform=%s (stub)", url, platform)
        return EvidenceResult(
            extraction_tier="T3_GEO_PROXY",
            extraction_outcome="FAILED_T2",
            extraction_platform=platform,
        )

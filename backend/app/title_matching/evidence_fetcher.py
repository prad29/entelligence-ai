from __future__ import annotations

import logging
from typing import Optional

from app.title_matching import evidence_cache, platform_router
from app.title_matching.evidence_types import EvidenceResult, ExtractionTier
from app.title_matching.extractors.t1_http import T1HttpExtractor
from app.title_matching.extractors.t2_headless import T2HeadlessExtractor, T3GeoProxyExtractor

logger = logging.getLogger(__name__)


def fetch_evidence(url: str, country_code: Optional[str] = None) -> EvidenceResult:
    """
    Fetch evidence for a ticketing URL.

    Steps:
    1. Check cache — return immediately on hit.
    2. Route the URL to a platform + extraction tier.
    3. Dispatch to the appropriate extractor.
    4. If T1 fails, escalate to T2 automatically.
    5. Store the result in cache before returning.

    Never raises — on any unexpected error returns a NOT_ATTEMPTED result.
    """
    try:
        # 1. Cache check
        cached = evidence_cache.get(url)
        if cached is not None:
            logger.debug("evidence_cache_hit url=%s", url)
            return cached

        # 2. Route
        platform, tier, repaired_url = platform_router.route(url)

        # 3. Dispatch
        if tier == ExtractionTier.T1_HTTP:
            result = T1HttpExtractor().extract(repaired_url, platform)

            # 4. T1 → T2 escalation
            if result.extraction_outcome == "FAILED_T1":
                logger.info("t1_failed_escalating_to_t2 url=%s", repaired_url)
                t2_result = T2HeadlessExtractor().extract(repaired_url, platform)
                if t2_result.extraction_outcome != "FAILED_T2":
                    result = t2_result

        elif tier == ExtractionTier.T2_HEADLESS:
            result = T2HeadlessExtractor().extract(repaired_url, platform)

        elif tier == ExtractionTier.T3_GEO_PROXY:
            result = T3GeoProxyExtractor().extract(repaired_url, platform)

        else:
            return EvidenceResult(extraction_outcome="NOT_ATTEMPTED")

        # 5. Store in cache
        evidence_cache.set(url, result)
        return result

    except Exception:
        logger.exception("evidence_fetch_error url=%s", url)
        return EvidenceResult(extraction_outcome="NOT_ATTEMPTED")

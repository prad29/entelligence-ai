from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from app.title_matching.evidence_types import EvidenceResult, ExtractionTier, ExtractionPlatform
from .base import AbstractExtractor

logger = logging.getLogger(__name__)

_RUNTIME_SELECTORS = [".runtime", '[class*="runtime"]', "[data-runtime]"]
_DIRECTOR_SELECTORS = [".director", '[class*="director"]']
_CAST_SELECTORS = [".cast", '[class*="cast"]', ".starring"]


class T2HeadlessExtractor(AbstractExtractor):
    """Tier-2 extractor that drives a headless Chromium browser via Playwright."""

    def extract(self, url: str, platform: str) -> EvidenceResult:  # noqa: PLR0912
        from playwright.sync_api import sync_playwright

        try:
            url = self._prepare_url(url, platform)

            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.goto(url, timeout=15000, wait_until="networkidle")

                    if platform == ExtractionPlatform.INDY_SYSTEMS:
                        # _handle_indy_systems may navigate the page itself and return a
                        # film-specific URL.  If it returns a URL that differs from the
                        # current page, navigate there now to land on the correct page.
                        repaired_url = self._handle_indy_systems(page, url)
                        if repaired_url and repaired_url != page.url:
                            page.goto(repaired_url, timeout=15000, wait_until="networkidle")
                        url = repaired_url or url

                    og_image = page.get_attribute('meta[property="og:image"]', "content")
                    page_title = page.title() or None

                    h1_text: Optional[str] = None
                    try:
                        h1_text = page.inner_text("h1") or None
                    except Exception:
                        pass

                    runtime: Optional[str] = None
                    for sel in _RUNTIME_SELECTORS:
                        try:
                            runtime = page.inner_text(sel) or None
                            if runtime:
                                break
                        except Exception:
                            continue

                    director: Optional[str] = None
                    for sel in _DIRECTOR_SELECTORS:
                        try:
                            director = page.inner_text(sel) or None
                            if director:
                                break
                        except Exception:
                            continue

                    cast: Optional[str] = None
                    for sel in _CAST_SELECTORS:
                        try:
                            cast = page.inner_text(sel) or None
                            if cast:
                                break
                        except Exception:
                            continue

                finally:
                    browser.close()

            outcome = "SUCCESS" if (og_image or page_title) else "FAILED_T2"
            return EvidenceResult(
                extraction_tier=ExtractionTier.T2_HEADLESS,
                extraction_platform=platform,
                extraction_outcome=outcome,
                og_image=og_image,
                page_title=page_title,
                h1_text=h1_text,
                runtime=runtime,
                director=director,
                cast=cast,
                extracted_at=datetime.now(timezone.utc).isoformat(),
            )

        except Exception:
            logger.exception("t2_headless extraction failed url=%s platform=%s", url, platform)
            return EvidenceResult(
                extraction_tier=ExtractionTier.T2_HEADLESS,
                extraction_outcome="FAILED_T2",
                extraction_platform=platform,
            )

    # ------------------------------------------------------------------
    # Platform-specific helpers
    # ------------------------------------------------------------------

    def _prepare_url(self, url: str, platform: str) -> str:
        """Apply pre-navigation URL repairs for known platforms."""
        if platform == ExtractionPlatform.EVENT_CINEMAS:
            url = url.split("#")[0]
        return url

    def _handle_indy_systems(self, page: object, url: str) -> str:
        """For INDY_SYSTEMS, if the URL contains /checkout or /cart, redirect to /movies/."""
        parsed = urlparse(url)
        path = parsed.path

        if "/checkout" in path or "/cart" in path:
            base_url = f"{parsed.scheme}://{parsed.netloc}/movies/"
            logger.info("indy_systems_checkout_strip url=%s -> %s", url, base_url)
            try:
                page.goto(base_url, timeout=15000, wait_until="networkidle")  # type: ignore[attr-defined]
                # Attempt to find a film link that matches the current page title
                film_link = page.query_selector('a[href*="/movies/"]')  # type: ignore[attr-defined]
                if film_link:
                    href = film_link.get_attribute("href")
                    if href:
                        return href
                return base_url
            except Exception:
                logger.warning("indy_systems_redirect_failed original_url=%s", url)
                return url

        return url


class T3GeoProxyExtractor(AbstractExtractor):
    """Tier-3 extractor stub — requires geo-proxy pool provisioning before use."""

    def extract(self, url: str, platform: str) -> EvidenceResult:
        # TODO: implement when geo-proxy pool is provisioned
        logger.info("t3_geo_proxy_unavailable url=%s", url)
        return EvidenceResult(
            extraction_tier=ExtractionTier.T3_GEO_PROXY,
            extraction_platform=platform,
            extraction_outcome="UNAVAILABLE",
        )

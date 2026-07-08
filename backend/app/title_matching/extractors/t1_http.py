from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

import httpx

from app.title_matching.evidence_types import EvidenceResult, ExtractionPlatform
from .base import AbstractExtractor

logger = logging.getLogger(__name__)


class _HTMLExtractor(HTMLParser):
    """Minimal stdlib HTML parser that collects og:image, page title, and first h1."""

    def __init__(self) -> None:
        super().__init__()
        self.og_image: Optional[str] = None
        self.page_title: Optional[str] = None
        self.h1: Optional[str] = None

        self._in_title: bool = False
        self._in_h1: bool = False
        self._title_buf: list[str] = []
        self._h1_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()

        if tag_lower == "meta":
            attr_dict = {k.lower(): v for k, v in attrs}
            if attr_dict.get("property", "").lower() == "og:image":
                self.og_image = attr_dict.get("content")
        elif tag_lower == "title":
            self._in_title = True
            self._title_buf = []
        elif tag_lower == "h1" and self.h1 is None:
            self._in_h1 = True
            self._h1_buf = []

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._in_title = False
            text = "".join(self._title_buf).strip()
            if text:
                self.page_title = text
        elif tag_lower == "h1" and self._in_h1:
            self._in_h1 = False
            text = "".join(self._h1_buf).strip()
            if text:
                self.h1 = text

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_buf.append(data)
        elif self._in_h1:
            self._h1_buf.append(data)


class T1HttpExtractor(AbstractExtractor):
    """
    Tier-1 HTTP extractor: fetches a ticketing URL with a plain HTTP GET
    and extracts evidence from the raw HTML using stdlib only.
    """

    _USER_AGENT = "Mozilla/5.0 (compatible; EntelligenceBot/1.0)"
    _TIMEOUT = 8
    _MAX_HTML_BYTES = 50_000

    def extract(self, url: str, platform: str) -> EvidenceResult:
        failed = EvidenceResult(
            extraction_tier="T1_HTTP",
            extraction_outcome="FAILED_T1",
            extraction_platform=platform,
        )

        try:
            resp = httpx.get(
                url,
                timeout=self._TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": self._USER_AGENT},
            )

            if resp.status_code != 200:
                logger.debug(
                    "t1_extract url=%s platform=%s outcome=%s",
                    url,
                    platform,
                    "FAILED_T1",
                )
                return failed

            html = resp.text[: self._MAX_HTML_BYTES]

            # --- Parse HTML structure ---
            parser = _HTMLExtractor()
            parser.feed(html)

            og_image: Optional[str] = parser.og_image
            page_title: Optional[str] = parser.page_title

            # --- Platform-specific regex extraction ---
            extracted_runtime_min: Optional[int] = None
            extracted_director: Optional[str] = None
            extracted_cast: Optional[str] = None
            ticketing_poster_url: Optional[str] = og_image

            if platform == ExtractionPlatform.AGILE_TICKETING:
                # Runtime: try HhMm pattern first, then bare-minutes patterns
                m = re.search(r"(\d+)h\s*(\d+)m", html)
                if m:
                    extracted_runtime_min = int(m.group(1)) * 60 + int(m.group(2))
                else:
                    m = re.search(r"(\d+)\s*(?:minutes|min)", html, re.IGNORECASE)
                    if m:
                        extracted_runtime_min = int(m.group(1))

                # Director
                m = re.search(r"director[:\s]+([A-Z][a-zA-Z ]{1,50})", html, re.IGNORECASE)
                if m:
                    extracted_director = m.group(1).strip()

                # Cast
                m = re.search(r"starring[:\s]+([^\n<]{10,80})", html, re.IGNORECASE)
                if m:
                    extracted_cast = m.group(1).strip()

            elif platform == ExtractionPlatform.CINEMAPLUS:
                if og_image is None:
                    m = re.search(r'src="([^"]*images\.cinemaplus\.com[^"]*)"', html)
                    if m:
                        ticketing_poster_url = m.group(1)

            # Determine outcome: SUCCESS if we have at least og_image or page_title
            outcome = "SUCCESS" if (og_image or page_title) else "FAILED_T1"
            extracted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

            result = EvidenceResult(
                ticketing_poster_url=ticketing_poster_url,
                page_title=page_title,
                extracted_runtime_min=extracted_runtime_min,
                extracted_director=extracted_director,
                extracted_cast=extracted_cast,
                extraction_platform=platform,
                extraction_tier="T1_HTTP",
                extraction_outcome=outcome,
                extracted_at=extracted_at,
            )

            logger.debug(
                "t1_extract url=%s platform=%s outcome=%s",
                url,
                platform,
                result.extraction_outcome,
            )
            return result

        except Exception:
            logger.debug(
                "t1_extract url=%s platform=%s outcome=%s",
                url,
                platform,
                "FAILED_T1",
            )
            return failed

"""
Comprehensive test suite for Stage 2 Evidence Fetcher components.

Covers:
  - PlatformRouter: URL routing to platform/tier
  - EvidenceCache: get/set with Redis + memory fallback
  - T1HttpExtractor: OG image and metadata extraction via plain HTTP
  - T2HeadlessExtractor: Playwright-based headless extraction
  - EvidenceFetcher: orchestration with caching and tier escalation
  - Stage 4 metadata checks: runtime, director, cast boosts in decision engine
  - eliminated[].why field on score_and_decide

Run with:
    pytest backend/tests/test_evidence_fetcher.py -v -s
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

# ─── logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger("evidence_fetcher_tests")


def _section(name: str) -> None:
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"  {name}")
    logger.info("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# TestPlatformRouter
# ─────────────────────────────────────────────────────────────────────────────

class TestPlatformRouter:
    """Tests for app.title_matching.platform_router.route()."""

    def test_cinemaplus_gqtmovies(self):
        _section("TestPlatformRouter.test_cinemaplus_gqtmovies")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

        platform, tier, repaired = route("https://www.gqtmovies.com/movies/film-123")

        assert platform == ExtractionPlatform.CINEMAPLUS, f"Expected CINEMAPLUS, got {platform}"
        assert tier == ExtractionTier.T1_HTTP, f"Expected T1_HTTP, got {tier}"
        logger.info("[PASS] gqtmovies.com routes to CINEMAPLUS/T1")

    def test_agile_ticketing_ifccenter(self):
        _section("TestPlatformRouter.test_agile_ticketing_ifccenter")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

        platform, tier, repaired = route("https://tickets.ifccenter.com/film/some-movie")

        assert platform == ExtractionPlatform.AGILE_TICKETING, f"Expected AGILE_TICKETING, got {platform}"
        assert tier == ExtractionTier.T1_HTTP, f"Expected T1_HTTP, got {tier}"
        logger.info("[PASS] tickets.ifccenter.com routes to AGILE_TICKETING/T1")

    def test_viff_org(self):
        _section("TestPlatformRouter.test_viff_org")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

        platform, tier, repaired = route("https://viff.org/films/some-movie")

        assert platform == ExtractionPlatform.VIFF, f"Expected VIFF, got {platform}"
        assert tier == ExtractionTier.T1_HTTP, f"Expected T1_HTTP, got {tier}"
        logger.info("[PASS] viff.org routes to VIFF/T1")

    def test_indy_systems_afi(self):
        _section("TestPlatformRouter.test_indy_systems_afi")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

        platform, tier, repaired = route("https://silver.afi.com/Browsing/Movies/Details.aspx?id=123")

        assert platform == ExtractionPlatform.INDY_SYSTEMS, f"Expected INDY_SYSTEMS, got {platform}"
        assert tier == ExtractionTier.T2_HEADLESS, f"Expected T2_HEADLESS, got {tier}"
        logger.info("[PASS] silver.afi.com routes to INDY_SYSTEMS/T2")

    def test_event_cinemas_au(self):
        _section("TestPlatformRouter.test_event_cinemas_au")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

        platform, tier, repaired = route("https://www.eventcinemas.com.au/movies/some-film")

        assert platform == ExtractionPlatform.EVENT_CINEMAS, f"Expected EVENT_CINEMAS, got {platform}"
        assert tier == ExtractionTier.T2_HEADLESS, f"Expected T2_HEADLESS, got {tier}"
        logger.info("[PASS] eventcinemas.com.au routes to EVENT_CINEMAS/T2")

    def test_kinepolis_fr(self):
        _section("TestPlatformRouter.test_kinepolis_fr")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionTier

        _platform, tier, _repaired = route("https://kinepolis.fr/films/some-movie")

        assert tier == ExtractionTier.T3_GEO_PROXY, f"Expected T3_GEO_PROXY, got {tier}"
        logger.info("[PASS] kinepolis.fr routes to T3_GEO_PROXY")

    def test_unknown_domain_defaults_to_generic_t1(self):
        _section("TestPlatformRouter.test_unknown_domain_defaults_to_generic_t1")
        from app.title_matching.platform_router import route
        from app.title_matching.evidence_types import ExtractionPlatform, ExtractionTier

        platform, tier, repaired = route("https://example.com/some-film")

        assert platform == ExtractionPlatform.GENERIC, f"Expected GENERIC, got {platform}"
        assert tier == ExtractionTier.T1_HTTP, f"Expected T1_HTTP, got {tier}"
        logger.info("[PASS] example.com defaults to GENERIC/T1")

    def test_eventcinemas_fragment_stripped(self):
        _section("TestPlatformRouter.test_eventcinemas_fragment_stripped")
        from app.title_matching.platform_router import route

        _platform, _tier, repaired = route("https://www.eventcinemas.com.au/movies/film#session123")

        assert "#" not in repaired, f"Fragment not stripped from repaired URL: {repaired}"
        assert "session123" not in repaired
        logger.info(f"[PASS] fragment stripped → {repaired}")

    def test_indy_systems_checkout_stripped(self):
        _section("TestPlatformRouter.test_indy_systems_checkout_stripped")
        from app.title_matching.platform_router import route

        _platform, _tier, repaired = route("https://silver.afi.com/checkout/movie/456")

        assert "/checkout" not in repaired, f"Checkout path not stripped: {repaired}"
        logger.info(f"[PASS] /checkout stripped → {repaired}")

    def test_viff_checkout_repair(self):
        _section("TestPlatformRouter.test_viff_checkout_repair")
        from app.title_matching.platform_router import route

        original = "https://viff.org/checkout/some-film/"
        _platform, _tier, repaired = route(original)

        # repaired should either strip checkout or return unchanged — must not crash
        assert isinstance(repaired, str) and len(repaired) > 0
        logger.info(f"[PASS] viff checkout repair attempted → {repaired}")


# ─────────────────────────────────────────────────────────────────────────────
# TestEvidenceCache
# ─────────────────────────────────────────────────────────────────────────────

class TestEvidenceCache:
    """Tests for app.title_matching.evidence_cache get/set."""

    def setup_method(self):
        _section("TestEvidenceCache")
        # Clear memory cache before each test
        try:
            from app.title_matching import evidence_cache
            evidence_cache._memory_cache.clear()
        except Exception:
            pass

    def test_cache_miss_returns_none(self):
        from app.title_matching.evidence_cache import get

        result = get("https://never-cached.example.com/film-xyz")

        assert result is None, f"Expected None on cache miss, got {result}"
        logger.info("[PASS] cache miss returns None")

    def test_cache_set_and_get_roundtrip(self):
        from app.title_matching.evidence_cache import get, set as cache_set
        from app.title_matching.evidence_types import EvidenceResult

        url = "https://example.com/film-roundtrip"
        evidence = EvidenceResult(
            ticketing_poster_url="https://cdn.example.com/poster.jpg",
            extraction_outcome="SUCCESS",
            extraction_tier="T1_HTTP",
            extracted_runtime_min=120,
            extracted_director="Christopher Nolan",
            extracted_cast="Tom Hardy, Christian Bale",
        )

        cache_set(url, evidence)
        retrieved = get(url)

        assert retrieved is not None, "Expected non-None after cache_set"
        assert retrieved.ticketing_poster_url == "https://cdn.example.com/poster.jpg"
        assert retrieved.extraction_outcome == "SUCCESS"
        assert retrieved.extracted_runtime_min == 120
        logger.info("[PASS] cache set/get roundtrip works")

    def test_cache_key_is_url_hash(self):
        from app.title_matching.evidence_cache import _cache_key

        key1 = _cache_key("https://example.com/film-a")
        key2 = _cache_key("https://example.com/film-b")

        assert key1 != key2, "Different URLs must produce different cache keys"
        assert isinstance(key1, str) and len(key1) > 0
        logger.info(f"[PASS] different URLs produce different keys: {key1[:12]}… vs {key2[:12]}…")

    def test_redis_unavailable_falls_back_to_memory(self, monkeypatch):
        from app.title_matching import evidence_cache
        from app.title_matching.evidence_types import EvidenceResult

        def _fail_redis():
            raise ConnectionError("Redis not available")

        monkeypatch.setattr(evidence_cache, "_get_redis", _fail_redis)

        url = "https://example.com/film-fallback"
        evidence = EvidenceResult(
            ticketing_poster_url=None,
            extraction_outcome="FAILED_T1",
            extraction_tier="T1_HTTP",
        )

        # Should not raise even when Redis is down
        evidence_cache.set(url, evidence)
        retrieved = evidence_cache.get(url)

        assert retrieved is not None
        assert retrieved.extraction_outcome == "FAILED_T1"
        logger.info("[PASS] Redis unavailable falls back to memory cache")

    def test_evidence_result_serialization(self):
        from app.title_matching.evidence_cache import get, set as cache_set
        from app.title_matching.evidence_types import EvidenceResult

        url = "https://example.com/film-serialization"
        evidence = EvidenceResult(
            ticketing_poster_url="https://cdn.example.com/nolan.jpg",
            extraction_outcome="SUCCESS",
            extraction_tier="T1_HTTP",
            extracted_runtime_min=148,
            extracted_director="Christopher Nolan",
            extracted_cast="Tom Hardy, Marion Cotillard",
        )

        cache_set(url, evidence)
        result = get(url)

        assert result is not None
        assert result.extracted_director == "Christopher Nolan"
        assert result.extracted_cast == "Tom Hardy, Marion Cotillard"
        assert result.extracted_runtime_min == 148
        logger.info("[PASS] EvidenceResult with all fields serializes/deserializes correctly")


# ─────────────────────────────────────────────────────────────────────────────
# TestT1HttpExtractor
# ─────────────────────────────────────────────────────────────────────────────

class TestT1HttpExtractor:
    """Tests for T1HttpExtractor with mocked httpx.get."""

    def setup_method(self):
        _section("TestT1HttpExtractor")

    def _mock_response(self, html: str, status: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.text = html
        return resp

    def test_og_image_extracted(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        html = '<html><head><meta property="og:image" content="https://example.com/poster.jpg"></head></html>'
        extractor = T1HttpExtractor()

        with patch("httpx.get", return_value=self._mock_response(html)):
            result = extractor.extract("https://example.com/film-123", ExtractionPlatform.GENERIC)

        assert result.ticketing_poster_url == "https://example.com/poster.jpg"
        assert result.extraction_outcome == "SUCCESS"
        logger.info(f"[PASS] og:image extracted → {result.ticketing_poster_url}")

    def test_no_og_image_returns_failed(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        html = "<html><head></head><body>no og tags</body></html>"
        extractor = T1HttpExtractor()

        with patch("httpx.get", return_value=self._mock_response(html)):
            result = extractor.extract("https://example.com/film-123", ExtractionPlatform.GENERIC)

        assert result.extraction_outcome == "FAILED_T1"
        assert result.ticketing_poster_url is None
        logger.info("[PASS] missing og:image → FAILED_T1")

    def test_http_error_returns_failed(self):
        import httpx
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        extractor = T1HttpExtractor()

        with patch("httpx.get", side_effect=httpx.RequestError("connection refused")):
            result = extractor.extract("https://unreachable.example.com/film", ExtractionPlatform.GENERIC)

        assert result.extraction_outcome == "FAILED_T1"
        logger.info("[PASS] httpx.RequestError → FAILED_T1 (no exception raised)")

    def test_non_200_returns_failed(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        extractor = T1HttpExtractor()

        with patch("httpx.get", return_value=self._mock_response("Not Found", status=404)):
            result = extractor.extract("https://example.com/film-404", ExtractionPlatform.GENERIC)

        assert result.extraction_outcome == "FAILED_T1"
        logger.info("[PASS] HTTP 404 → FAILED_T1")

    def test_agile_ticketing_runtime_extracted(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        html = (
            '<html><head><meta property="og:image" content="https://example.com/poster.jpg"></head>'
            "<body><p>Runtime: 120 min</p></body></html>"
        )
        extractor = T1HttpExtractor()

        with patch("httpx.get", return_value=self._mock_response(html)):
            result = extractor.extract(
                "https://tickets.ifccenter.com/film/test",
                ExtractionPlatform.AGILE_TICKETING,
            )

        assert result.extracted_runtime_min == 120, f"Expected 120, got {result.extracted_runtime_min}"
        logger.info(f"[PASS] runtime extracted → {result.extracted_runtime_min} min")

    def test_cinemaplus_platform_image_fallback(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        html = (
            '<html><head></head>'
            '<body><img src="https://images.cinemaplus.com/poster.jpg"></body></html>'
        )
        extractor = T1HttpExtractor()

        with patch("httpx.get", return_value=self._mock_response(html)):
            result = extractor.extract(
                "https://www.gqtmovies.com/film/123",
                ExtractionPlatform.CINEMAPLUS,
            )

        assert result.ticketing_poster_url is not None, "Expected fallback image URL"
        assert "cinemaplus.com" in result.ticketing_poster_url
        logger.info(f"[PASS] CINEMAPLUS image fallback → {result.ticketing_poster_url}")

    def test_extraction_tier_is_t1_http(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        html = '<html><head><meta property="og:image" content="https://example.com/p.jpg"></head></html>'
        extractor = T1HttpExtractor()

        with patch("httpx.get", return_value=self._mock_response(html)):
            result = extractor.extract("https://example.com/film", ExtractionPlatform.GENERIC)

        assert result.extraction_tier == "T1_HTTP", f"Expected T1_HTTP, got {result.extraction_tier}"
        logger.info(f"[PASS] extraction_tier = {result.extraction_tier}")


# ─────────────────────────────────────────────────────────────────────────────
# TestT2HeadlessExtractor
# ─────────────────────────────────────────────────────────────────────────────

class TestT2HeadlessExtractor:
    """Tests for T2HeadlessExtractor and T3GeoProxyExtractor."""

    def setup_method(self):
        _section("TestT2HeadlessExtractor")

    def test_playwright_unavailable_returns_failed(self):
        from app.title_matching.extractors.t2_headless import T2HeadlessExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        extractor = T2HeadlessExtractor()

        with patch(
            "app.title_matching.extractors.t2_headless.sync_playwright",
            side_effect=ImportError("playwright not installed"),
        ):
            result = extractor.extract(
                "https://silver.afi.com/movie/123",
                ExtractionPlatform.INDY_SYSTEMS,
            )

        assert result.extraction_outcome == "FAILED_T2"
        logger.info("[PASS] playwright ImportError → FAILED_T2 (no exception raised)")

    def test_t2_extraction_tier(self):
        from app.title_matching.extractors.t2_headless import T2HeadlessExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        extractor = T2HeadlessExtractor()

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_page.content.return_value = (
            '<html><head><meta property="og:image" content="https://cdn.afi.com/poster.jpg"></head></html>'
        )
        mock_browser.new_page.return_value = mock_page
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser

        with patch(
            "app.title_matching.extractors.t2_headless.sync_playwright",
            return_value=mock_pw,
        ):
            result = extractor.extract(
                "https://silver.afi.com/movie/123",
                ExtractionPlatform.INDY_SYSTEMS,
            )

        assert result.extraction_tier == "T2_HEADLESS", f"Expected T2_HEADLESS, got {result.extraction_tier}"
        logger.info(f"[PASS] T2 extraction_tier = {result.extraction_tier}")

    def test_t3_stub_returns_unavailable(self):
        from app.title_matching.extractors.t2_headless import T3GeoProxyExtractor
        from app.title_matching.evidence_types import ExtractionPlatform

        extractor = T3GeoProxyExtractor()
        result = extractor.extract("https://kinepolis.fr/films/some-movie", ExtractionPlatform.GENERIC)

        assert result.extraction_outcome == "UNAVAILABLE"
        assert result.extraction_tier == "T3_GEO_PROXY"
        logger.info(f"[PASS] T3 stub → outcome={result.extraction_outcome} tier={result.extraction_tier}")


# ─────────────────────────────────────────────────────────────────────────────
# TestEvidenceFetcher
# ─────────────────────────────────────────────────────────────────────────────

class TestEvidenceFetcher:
    """Tests for fetch_evidence() orchestration."""

    def setup_method(self):
        _section("TestEvidenceFetcher")
        try:
            from app.title_matching import evidence_cache
            evidence_cache._memory_cache.clear()
        except Exception:
            pass

    def _make_success_result(self, tier: str = "T1_HTTP") -> "EvidenceResult":  # type: ignore[name-defined]
        from app.title_matching.evidence_types import EvidenceResult
        return EvidenceResult(
            ticketing_poster_url="https://cdn.example.com/poster.jpg",
            extraction_outcome="SUCCESS",
            extraction_tier=tier,
        )

    def test_returns_evidence_result(self):
        from app.title_matching.evidence_fetcher import fetch_evidence
        from app.title_matching.evidence_types import EvidenceResult

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = self._make_success_result()

        with patch(
            "app.title_matching.evidence_fetcher.T1HttpExtractor",
            return_value=mock_extractor,
        ):
            result = fetch_evidence("https://example.com/film-1")

        assert isinstance(result, EvidenceResult)
        logger.info(f"[PASS] fetch_evidence returns EvidenceResult, outcome={result.extraction_outcome}")

    def test_cache_hit_skips_extraction(self):
        from app.title_matching.evidence_fetcher import fetch_evidence
        from app.title_matching.evidence_cache import set as cache_set

        url = "https://example.com/film-cached"
        cached = self._make_success_result()
        cache_set(url, cached)

        mock_extractor = MagicMock()

        with patch(
            "app.title_matching.evidence_fetcher.T1HttpExtractor",
            return_value=mock_extractor,
        ):
            result = fetch_evidence(url)

        assert mock_extractor.extract.call_count == 0, "Extractor should not be called on cache hit"
        assert result.extraction_outcome == "SUCCESS"
        logger.info("[PASS] cache hit skips extractor (call_count=0)")

    def test_t1_failure_escalates_to_t2(self):
        # Use a generic T1 domain so the router assigns T1_HTTP tier.
        # T1 returns FAILED_T1, verifying that fetch_evidence escalates to T2.
        from app.title_matching.evidence_fetcher import fetch_evidence
        from app.title_matching.evidence_types import EvidenceResult

        t1_result = EvidenceResult(
            ticketing_poster_url=None,
            extraction_outcome="FAILED_T1",
            extraction_tier="T1_HTTP",
        )
        t2_result = EvidenceResult(
            ticketing_poster_url="https://cdn.example.com/poster.jpg",
            extraction_outcome="SUCCESS",
            extraction_tier="T2_HEADLESS",
        )

        mock_t1 = MagicMock()
        mock_t1.extract.return_value = t1_result
        mock_t2 = MagicMock()
        mock_t2.extract.return_value = t2_result

        with (
            patch("app.title_matching.evidence_fetcher.T1HttpExtractor", return_value=mock_t1),
            patch("app.title_matching.evidence_fetcher.T2HeadlessExtractor", return_value=mock_t2),
        ):
            result = fetch_evidence("https://example.com/film-t1-fail")

        assert result.extraction_outcome == "SUCCESS"
        assert result.extraction_tier == "T2_HEADLESS"
        logger.info(f"[PASS] T1 FAILED_T1 escalated to T2 → outcome={result.extraction_outcome}")

    def test_t3_domain_returns_unavailable(self):
        from app.title_matching.evidence_fetcher import fetch_evidence

        result = fetch_evidence("https://kinepolis.fr/films/some-film")

        assert result.extraction_outcome == "UNAVAILABLE"
        logger.info(f"[PASS] T3 domain returns UNAVAILABLE → {result.extraction_outcome}")

    def test_never_raises(self):
        from app.title_matching.evidence_fetcher import fetch_evidence
        from app.title_matching.evidence_types import EvidenceResult

        mock_extractor = MagicMock()
        mock_extractor.extract.side_effect = Exception("Something exploded")

        result = None
        with patch(
            "app.title_matching.evidence_fetcher.T1HttpExtractor",
            return_value=mock_extractor,
        ):
            try:
                result = fetch_evidence("https://example.com/film-crash")
            except Exception as exc:
                pytest.fail(f"fetch_evidence raised an exception: {exc}")

        assert result is not None, "fetch_evidence returned None"
        assert isinstance(result, EvidenceResult)
        assert result.extraction_outcome in ("NOT_ATTEMPTED", "FAILED_T1", "FAILED_T2", "UNAVAILABLE")
        logger.info(f"[PASS] fetch_evidence never raises — outcome={result.extraction_outcome}")


# ─────────────────────────────────────────────────────────────────────────────
# TestStage4MetadataChecks
# ─────────────────────────────────────────────────────────────────────────────

class TestStage4MetadataChecks:
    """Tests for new Stage 4 metadata-check helpers in decision_engine.

    Requires Stage 2 additions to decision_engine.py:
      - _runtime_check(query_runtime, candidate_runtime) -> float
      - _director_check(query_director, candidate_director) -> float
      - _cast_check(query_cast, candidate_cast) -> float
      - 'why' key added to each item in the 'eliminated' evidence list
    These are not present in the baseline decision_engine.py — they will be
    added by the Stage 2 decision_engine unit before these tests run in Docker.
    """

    def setup_method(self):
        _section("TestStage4MetadataChecks")

    def test_runtime_check_match_within_5min(self):
        from app.title_matching.decision_engine import _runtime_check

        boost = _runtime_check(120, 122)

        assert boost > 0, f"Expected positive boost for runtime within 5 min, got {boost}"
        logger.info(f"[PASS] runtime within 5 min → boost={boost}")

    def test_runtime_check_mismatch(self):
        from app.title_matching.decision_engine import _runtime_check

        boost = _runtime_check(120, 135)

        assert boost == 0, f"Expected 0 boost for runtime mismatch > 5 min, got {boost}"
        logger.info(f"[PASS] runtime mismatch (15 min gap) → boost={boost}")

    def test_runtime_check_unknown(self):
        from app.title_matching.decision_engine import _runtime_check

        boost = _runtime_check(None, 120)

        assert boost == 0, f"Expected 0 when runtime is unknown (None), got {boost}"
        logger.info(f"[PASS] runtime=None → boost={boost}")

    def test_director_check_match(self):
        from app.title_matching.decision_engine import _director_check

        boost = _director_check("Christopher Nolan", "christopher nolan")

        assert boost > 0, f"Expected positive boost for matching director, got {boost}"
        logger.info(f"[PASS] director match (case-insensitive) → boost={boost}")

    def test_director_check_mismatch(self):
        from app.title_matching.decision_engine import _director_check

        boost = _director_check("James Cameron", "Christopher Nolan")

        assert boost == 0, f"Expected 0 boost for director mismatch, got {boost}"
        logger.info(f"[PASS] director mismatch → boost={boost}")

    def test_cast_check_overlap(self):
        from app.title_matching.decision_engine import _cast_check

        boost = _cast_check("Tom Hanks, Meg Ryan", "Meg Ryan, Gary Sinise")

        assert boost > 0, f"Expected positive boost for overlapping cast, got {boost}"
        logger.info(f"[PASS] cast overlap (Meg Ryan) → boost={boost}")

    def test_cast_check_no_overlap(self):
        from app.title_matching.decision_engine import _cast_check

        boost = _cast_check("Tom Hanks", "Meryl Streep")

        assert boost == 0, f"Expected 0 boost for no cast overlap, got {boost}"
        logger.info(f"[PASS] no cast overlap → boost={boost}")

    def test_eliminated_why_field_populated(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide
        from app.title_matching.types import CandidateResult

        norm = normalize_title("Inception")
        candidates = [
            CandidateResult(1, "Inception", "2010-07-16", None, 0.95, "fuzzy"),
            CandidateResult(2, "Interstellar", "2014-11-07", None, 0.40, "fuzzy"),
            CandidateResult(3, "Tenet", "2020-08-26", None, 0.20, "fuzzy"),
        ]

        result = score_and_decide(norm, candidates, show_date=None, theater=None)

        eliminated = result.evidence.get("eliminated", [])
        assert len(eliminated) > 0, "Expected at least one eliminated candidate"

        first_eliminated = eliminated[0]
        assert "why" in first_eliminated, f"'why' key missing from eliminated[0]: {first_eliminated}"
        why_text = first_eliminated["why"]
        assert isinstance(why_text, str) and len(why_text) > 0, (
            f"eliminated[0]['why'] should be a non-empty string, got: {why_text!r}"
        )
        logger.info(f"[PASS] eliminated[0].why = '{why_text[:60]}'")

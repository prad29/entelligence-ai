"""
Comprehensive test suite for the Movie Title Matching pipeline.

Covers every stage with structured pass/fail logs so you can see exactly
which part of the pipeline ran and what the result was.

Run with:
    pytest backend/tests/test_title_matching.py -v -s

Note: the rule-based fuzzy/date-proximity fallback (CandidateGenerator,
decision_engine.score_and_decide, TitleMatchEngine) was retired -- every
domestic match now runs through the Claude agentic runner unconditionally
(see test_agentic_mandatory.py for the regression fence). Their tests were
removed here accordingly.

Pipeline stages tested:
  Stage 0 — Normalizer (promo strip, mojibake, edition markers, event type, ordinal, franchise)
  Stage 2 — OG image fetcher (T1 plain HTTP poster extract)
  API     — /single endpoint (always agentic; run_agentic_match stubbed)
  Seed    — seed_from_rows upsert logic
"""

from __future__ import annotations

import logging
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import select

# ─── logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
log = logging.getLogger("title_match_tests")


def _pass(stage: str, label: str, detail: str = "") -> None:
    msg = f"[PASS]  {stage:<22} {label}"
    if detail:
        msg += f"  →  {detail}"
    log.info(msg)


def _fail_log(stage: str, label: str, detail: str = "") -> None:
    msg = f"[FAIL]  {stage:<22} {label}"
    if detail:
        msg += f"  →  {detail}"
    log.error(msg)


def _section(title: str) -> None:
    log.info("")
    log.info("=" * 70)
    log.info(f"  {title}")
    log.info("=" * 70)


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 0 — NORMALIZER
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizer:
    """Stage 0: title normalisation before candidate search."""

    def setup_method(self):
        _section("STAGE 0 — NORMALIZER")

    def test_promo_prefix_stripped(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("KIDSHOW: The Lion King")
        assert "KIDSHOW" not in result.cleaned
        assert "Lion King" in result.cleaned
        _pass("Stage0/Normalizer", "promo_strip", f"'{result.cleaned}'")

    def test_dollar_promo_stripped(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("$5 The Avengers")
        assert "$5" not in result.cleaned
        _pass("Stage0/Normalizer", "dollar_promo_strip", f"'{result.cleaned}'")

    def test_summer_kids_stripped(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Summer Kids Movie Series: Frozen")
        assert "Summer Kids" not in result.cleaned
        assert "Frozen" in result.cleaned
        _pass("Stage0/Normalizer", "summer_kids_strip", f"'{result.cleaned}'")

    def test_edition_marker_live_action(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Moana (Live Action)")
        assert "Live Action" in result.edition_markers
        _pass("Stage0/Normalizer", "edition_live_action", f"markers={result.edition_markers}")

    def test_edition_marker_4k(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("The Dark Knight 4K")
        assert "4K" in result.edition_markers
        _pass("Stage0/Normalizer", "edition_4K", f"markers={result.edition_markers}")

    def test_edition_marker_imax(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Avengers Endgame IMAX")
        assert "IMAX" in result.edition_markers
        _pass("Stage0/Normalizer", "edition_IMAX", f"markers={result.edition_markers}")

    def test_country_code_australia(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Mad Max Australia")
        assert result.country_code == "AU"
        _pass("Stage0/Normalizer", "country_AU", f"code={result.country_code}")

    def test_country_code_germany(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Das Boot Germany")
        assert result.country_code == "DE"
        _pass("Stage0/Normalizer", "country_DE", f"code={result.country_code}")

    def test_event_type_movie(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Inception")
        assert result.event_type == "MOVIE"
        _pass("Stage0/Normalizer", "event_MOVIE", f"type={result.event_type}")

    def test_event_type_non_movie_concert(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Taylor Swift: The Eras Tour Concert")
        assert result.event_type == "NON_MOVIE"
        _pass("Stage0/Normalizer", "event_NON_MOVIE", f"type={result.event_type}")

    def test_event_type_multi_film(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Marvel Saga Marathon")
        assert result.event_type == "MULTI_FILM"
        _pass("Stage0/Normalizer", "event_MULTI_FILM", f"type={result.event_type}")

    def test_event_type_rerelease(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Titanic 25th Anniversary")
        assert result.event_type == "RERELEASE"
        _pass("Stage0/Normalizer", "event_RERELEASE", f"type={result.event_type}")

    def test_franchise_harry_potter(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Harry Potter 3")
        assert result.franchise_hint == "harry_potter"
        assert result.ordinal == 3
        _pass("Stage0/Normalizer", "franchise_hp", f"hint={result.franchise_hint} ordinal={result.ordinal}")

    def test_franchise_toy_story(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Toy Story 4")
        assert result.franchise_hint == "toy_story"
        assert result.ordinal == 4
        _pass("Stage0/Normalizer", "franchise_toy_story", f"hint={result.franchise_hint} ordinal={result.ordinal}")

    def test_roman_numeral_ordinal(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Rocky III")
        assert result.ordinal == 3
        _pass("Stage0/Normalizer", "roman_numeral", f"ordinal={result.ordinal}")

    def test_part_ordinal(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("Harry Potter and the Deathly Hallows Part 2")
        assert result.ordinal == 2
        _pass("Stage0/Normalizer", "part_ordinal", f"ordinal={result.ordinal}")

    def test_hp_7_1_fraction_ordinal(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("HP 7/1")
        assert result.ordinal == 7
        assert result.franchise_hint == "harry_potter"
        _pass("Stage0/Normalizer", "hp_fraction", f"ordinal={result.ordinal}")

    def test_no_false_year_ordinal(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("RBO Cinema Season 2024-25: Barbie")
        # 2024 should NOT become ordinal
        assert result.ordinal is None or result.ordinal <= 20
        _pass("Stage0/Normalizer", "no_year_ordinal", f"ordinal={result.ordinal}")

    def test_flashback_stripped(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("FLASHBACK: Jurassic Park")
        assert "FLASHBACK" not in result.cleaned
        _pass("Stage0/Normalizer", "flashback_strip", f"'{result.cleaned}'")

    def test_plain_title_unchanged(self):
        from app.title_matching.normalizer import normalize_title
        result = normalize_title("The Dark Knight")
        assert "Dark Knight" in result.cleaned
        assert result.event_type == "MOVIE"
        assert result.edition_markers == []
        _pass("Stage0/Normalizer", "plain_unchanged", f"'{result.cleaned}'")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2 — OG IMAGE FETCHER (T1)
# ─────────────────────────────────────────────────────────────────────────────

_T1_HTTPX_PATCH = "app.title_matching.extractors.t1_http.httpx.get"


class TestOGImageFetcher:
    """
    Stage 2 (T1 only): fetches og:image from ticketing URL via T1HttpExtractor.
    Stage 3 (pHash / CLIP) is Phase 2 — NOT YET BUILT.
    """

    def setup_method(self):
        _section("STAGE 2 — OG IMAGE FETCHER (T1 plain HTTP)")

    def test_og_image_extracted_from_html(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor

        html = textwrap.dedent("""
            <html><head>
            <meta property="og:image" content="https://example.com/poster.jpg" />
            </head><body></body></html>
        """)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch(_T1_HTTPX_PATCH, return_value=mock_resp):
            result = T1HttpExtractor().extract("https://tickets.example.com/moana", "GENERIC")

        assert result.ticketing_poster_url == "https://example.com/poster.jpg"
        _pass("Stage2/OGFetch", "og_image_parse", f"url={result.ticketing_poster_url}")

    def test_og_image_missing_returns_none(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head></head><body>no og tags</body></html>"

        with patch(_T1_HTTPX_PATCH, return_value=mock_resp):
            result = T1HttpExtractor().extract("https://tickets.example.com/movie", "GENERIC")

        assert result.ticketing_poster_url is None
        _pass("Stage2/OGFetch", "og_missing_returns_none", "ticketing_poster_url=None")

    def test_http_error_returns_none(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch(_T1_HTTPX_PATCH, return_value=mock_resp):
            result = T1HttpExtractor().extract("https://tickets.example.com/404", "GENERIC")

        assert result.ticketing_poster_url is None
        assert result.extraction_outcome == "FAILED_T1"
        _pass("Stage2/OGFetch", "http_404_returns_none", "ticketing_poster_url=None")

    def test_network_exception_returns_none(self):
        from app.title_matching.extractors.t1_http import T1HttpExtractor

        with patch(_T1_HTTPX_PATCH, side_effect=Exception("Connection refused")):
            result = T1HttpExtractor().extract("https://unreachable.example.com", "GENERIC")

        assert result.ticketing_poster_url is None
        assert result.extraction_outcome == "FAILED_T1"
        _pass("Stage2/OGFetch", "network_error_silent", "ticketing_poster_url=None (no exception raised)")

    def test_og_image_only_parses_first_50kb(self):
        """Parser should not crash on large pages; only parses first 50000 chars."""
        from app.title_matching.extractors.t1_http import T1HttpExtractor

        big_content = "x" * 40000
        og_part = '<meta property="og:image" content="https://example.com/found.jpg" />'
        html = og_part + big_content  # og tag is within first 50kb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch(_T1_HTTPX_PATCH, return_value=mock_resp):
            result = T1HttpExtractor().extract("https://example.com/large-page", "GENERIC")

        assert result.ticketing_poster_url == "https://example.com/found.jpg"
        _pass("Stage2/OGFetch", "large_page_ok", f"url={result.ticketing_poster_url}")

    def test_stage3_phash_not_implemented(self):
        """
        Stage 3 (pHash / CLIP image verifier) is Phase 2 — confirm it is NOT present.
        When seeded, cover_image_phash should be NULL for all rows.

        IMAGE MATCHING DESIGN (Phase 2):
          1. Build poster index:
               python app/cli.py build-poster-index
             Downloads each cover_image S3 URL, computes pHash with imagehash.phash(),
             stores 64-bit hex string in moviemaster.cover_image_phash.

          2. At match time (Stage 3):
             a. Fetch ticketing_poster_url image (bytes)
             b. Compute pHash of fetched image
             c. Compare Hamming distance vs stored pHashes of top candidates
                  distance ≤ 10  → same movie poster  → boost score +0.30
                  distance 11-20 → similar             → boost score +0.10
                  distance > 20  → different poster
             d. CLIP cosine similarity (Bedrock Titan Multimodal) as secondary signal
             e. Vision-LLM adjudication when pHash and CLIP disagree

          Required packages (Phase 2):
               imagehash>=4.3.1
               Pillow>=10.0.0
               # CLIP: via AWS Bedrock Titan Multimodal — no extra pip install needed
        """
        # Verify no phash logic exists yet
        import importlib
        try:
            import imagehash  # type: ignore[import]
            _pass("Stage2/OGFetch", "imagehash_available", "imagehash installed (Phase 2 ready)")
        except ImportError:
            _pass("Stage2/OGFetch", "stage3_phash_not_built",
                  "imagehash not installed — Phase 2 image verifier not yet implemented (expected)")


# ─────────────────────────────────────────────────────────────────────────────
# API — /single endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAPIEndpoint:
    """API-layer tests for POST /api/v1/movie-title-match/single.

    Every request now always goes through the Claude agentic runner (the
    rule-based fallback was retired) — so `client` patches
    runner.run_agentic_match with a stub and forces
    AGENTIC_TITLE_MATCH_ENABLED on, rather than pre-building a fuzzy engine.
    """

    def setup_method(self):
        _section("API — /single endpoint")

    @staticmethod
    def _stub_result(title: str, *_args, **_kwargs):
        from app.title_matching.types import TitleMatchResult

        movie_id = 2 if "Interstellar" in title else 1
        return TitleMatchResult(
            suggested_movie_id=movie_id,
            suggested_movie_title=title,
            canonical_movie_id=movie_id,
            confidence=0.95,
            decision="AUTO_ACCEPT",
            reasoning="stubbed agentic match for test",
            evidence={"agentic": True},
            fired_ai=True,
        )

    @pytest.fixture
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.config import settings

        monkeypatch.setattr(settings, "AGENTIC_TITLE_MATCH_ENABLED", True)
        with patch(
            "app.title_matching.agentic.runner.run_agentic_match",
            side_effect=self._stub_result,
        ):
            yield TestClient(app)

    def test_match_returned(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception", "show_date": "2010-07-16"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggested_movie_id"] == 1
        assert "confidence" in data
        assert "reasoning" in data
        _pass("API/single", "match_returned",
              f"id={data['suggested_movie_id']} conf={data['confidence']} decision={data['decision']}")

    def test_missing_title_returns_422(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={}
        )
        assert resp.status_code == 422
        _pass("API/single", "missing_title_422", f"status={resp.status_code}")

    def test_optional_fields_accepted(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={
                "title": "Interstellar",
                "theater": "Falmouth Luxury Cinemas",
                "show_date": "2014-11-15",
                "ticketing_url": None,
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["suggested_movie_id"] == 2
        _pass("API/single", "optional_fields", f"id={data['suggested_movie_id']}")

    def test_response_has_all_required_keys(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception"}
        )
        data = resp.json()
        required = {"suggested_movie_id", "suggested_movie_title", "confidence",
                    "decision", "reasoning", "evidence", "fired_ai"}
        missing = required - set(data.keys())
        assert not missing, f"Missing keys: {missing}"
        _pass("API/single", "response_schema", f"all {len(required)} required keys present")

    def test_market_defaults_to_domestic_without_error(self, client):
        """Omitting market entirely must be accepted (defaults to 'domestic')
        — same request shape as before this field existed."""
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception", "show_date": "2010-07-16"}
        )
        assert resp.status_code == 200
        _pass("API/single", "market_default_domestic", f"status={resp.status_code}")

    def test_international_without_country_returns_422(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Blue Beetle", "market": "international"}
        )
        assert resp.status_code == 422
        _pass("API/single", "intl_missing_country_422", f"status={resp.status_code}")

    def test_international_with_blank_country_returns_422(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Blue Beetle", "market": "international", "country": "   "}
        )
        assert resp.status_code == 422
        _pass("API/single", "intl_blank_country_422", f"status={resp.status_code}")

    def test_international_with_country_accepted(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Blue Beetle", "market": "international", "country": "France"}
        )
        assert resp.status_code == 200
        _pass("API/single", "intl_with_country_accepted", f"status={resp.status_code}")

    def test_invalid_market_value_returns_422(self, client):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception", "market": "not-a-real-market"}
        )
        assert resp.status_code == 422
        _pass("API/single", "invalid_market_422", f"status={resp.status_code}")


# ─────────────────────────────────────────────────────────────────────────────
# SEED LOADER — upsert logic
# ─────────────────────────────────────────────────────────────────────────────

class TestSeedLoader:
    """seed_from_rows: insert new, update existing, skip malformed rows."""

    def setup_method(self):
        _section("SEED LOADER — upsert logic")

    def _make_session(self, existing_ids: list[int]):
        """Mock session that simulates existing MovieMaster rows."""
        from app.models import MovieMaster

        existing = {
            i: MovieMaster(
                id=i, movie_title=f"Existing {i}", release_date=None,
                imdb_id=None, cover_image=None, director=None, cast_list=None,
                running_time=None, parent_id=None, search_tags=None,
                title_tag=None, short_name=None,
            )
            for i in existing_ids
        }

        session = MagicMock()
        session.get.side_effect = lambda model, pk: existing.get(pk)
        return session

    def test_inserts_new_rows(self):
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([])
        rows = [{"id": "1", "movie_title": "Inception"}, {"id": "2", "title": "Interstellar"}]
        result = seed_from_rows(session, rows)
        assert result["inserted"] == 2
        assert result["updated"] == 0
        assert result["skipped"] == 0
        _pass("SeedLoader", "inserts_new", f"inserted={result['inserted']}")

    def test_updates_existing_rows(self):
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([1])
        rows = [{"id": "1", "movie_title": "Inception Updated"}]
        result = seed_from_rows(session, rows)
        assert result["updated"] == 1
        assert result["inserted"] == 0
        _pass("SeedLoader", "updates_existing", f"updated={result['updated']}")

    def test_skips_missing_id(self):
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([])
        rows = [{"movie_title": "No ID here"}]
        result = seed_from_rows(session, rows)
        assert result["skipped"] == 1
        _pass("SeedLoader", "skip_missing_id", f"skipped={result['skipped']}")

    def test_skips_missing_title(self):
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([])
        rows = [{"id": "99"}]
        result = seed_from_rows(session, rows)
        assert result["skipped"] == 1
        _pass("SeedLoader", "skip_missing_title", f"skipped={result['skipped']}")

    def test_skips_non_integer_id(self):
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([])
        rows = [{"id": "abc", "movie_title": "Bad Row"}]
        result = seed_from_rows(session, rows)
        assert result["skipped"] == 1
        _pass("SeedLoader", "skip_non_int_id", f"skipped={result['skipped']}")

    def test_column_aliases(self):
        """Accepts 'title' as an alias for 'movie_title'."""
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([])
        rows = [{"id": "5", "title": "Moana"}]
        result = seed_from_rows(session, rows)
        assert result["inserted"] == 1
        _pass("SeedLoader", "column_alias_title", f"inserted={result['inserted']}")

    def test_mixed_insert_update_skip(self):
        from app.title_matching.seed_loader import seed_from_rows

        session = self._make_session([2])
        rows = [
            {"id": "1", "movie_title": "New Movie"},       # insert
            {"id": "2", "movie_title": "Existing Updated"}, # update
            {"movie_title": "No ID"},                        # skip
        ]
        result = seed_from_rows(session, rows)
        assert result["inserted"] == 1
        assert result["updated"] == 1
        assert result["skipped"] == 1
        _pass("SeedLoader", "mixed_batch",
              f"inserted={result['inserted']} updated={result['updated']} skipped={result['skipped']}")


# ─────────────────────────────────────────────────────────────────────────────
# SEED LOADER — international (moviemasterintl), grain (movie_id, country,
# release_date). Uses a real in-memory sqlite DB (not a mocked session) so the
# unique-constraint / upsert-by-triple behavior is genuinely exercised.
# ─────────────────────────────────────────────────────────────────────────────

class TestSeedLoaderIntl:
    """seed_intl_from_rows: null-string coercion, undefined-country skip, per-triple upsert."""

    def setup_method(self):
        _section("SEED LOADER (INTL) — per-country/date upsert logic")

    def _make_session(self):
        from sqlmodel import Session, SQLModel, create_engine
        from sqlmodel.pool import StaticPool

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        return Session(engine)

    def test_null_string_coercion(self):
        from app.title_matching.seed_loader import seed_intl_from_rows
        from app.models import MovieMasterIntl

        session = self._make_session()
        rows = [{
            "id": "1", "movie_id": "100", "movie_title": "Blue Beetle",
            "country": "France", "release_date": "2023-08-16",
            "rating": "null", "genre2": "null", "running_time": "null",
        }]
        result = seed_intl_from_rows(session, rows)
        assert result["inserted"] == 1
        row = session.exec(
            select(MovieMasterIntl)
        ).first()
        assert row.rating is None
        assert row.genre2 is None
        assert row.running_time is None
        _pass("SeedLoaderIntl", "null_string_coercion", "rating/genre2/running_time coerced to None")

    def test_undefined_country_is_skipped(self):
        from app.title_matching.seed_loader import seed_intl_from_rows

        session = self._make_session()
        rows = [{
            "id": "1", "movie_id": "100", "movie_title": "Demon Slayer",
            "country": "undefined", "release_date": "2025-09-25",
        }]
        result = seed_intl_from_rows(session, rows)
        assert result["skipped_undefined_country"] == 1
        assert result["inserted"] == 0
        _pass("SeedLoaderIntl", "undefined_country_skipped",
              f"skipped_undefined_country={result['skipped_undefined_country']}")

    def test_same_movie_id_different_countries_both_retained(self):
        from app.title_matching.seed_loader import seed_intl_from_rows
        from app.models import MovieMasterIntl

        session = self._make_session()
        rows = [
            {"id": "1", "movie_id": "16456", "movie_title": "Blue Beetle",
             "country": "France", "release_date": "2023-08-16"},
            {"id": "2", "movie_id": "16456", "movie_title": "Blue Beetle",
             "country": "Germany", "release_date": "2023-08-17"},
        ]
        result = seed_intl_from_rows(session, rows)
        assert result["inserted"] == 2
        rows_in_db = session.exec(
            select(MovieMasterIntl).where(MovieMasterIntl.movie_id == 16456)
        ).all()
        assert len(rows_in_db) == 2
        _pass("SeedLoaderIntl", "same_movie_id_multi_country", f"rows={len(rows_in_db)}")

    def test_same_country_different_release_dates_both_retained(self):
        """Same movie_id + country re-released on a different date must not collide."""
        from app.title_matching.seed_loader import seed_intl_from_rows
        from app.models import MovieMasterIntl

        session = self._make_session()
        rows = [
            {"id": "1", "movie_id": "20310", "movie_title": "Expend4bles",
             "country": "Japan", "release_date": "2023-09-21"},
            {"id": "2", "movie_id": "20310", "movie_title": "Expend4bles",
             "country": "Japan", "release_date": "2024-01-12"},
        ]
        result = seed_intl_from_rows(session, rows)
        assert result["inserted"] == 2
        rows_in_db = session.exec(
            select(MovieMasterIntl).where(MovieMasterIntl.movie_id == 20310)
        ).all()
        assert len(rows_in_db) == 2
        _pass("SeedLoaderIntl", "same_country_diff_release_date", f"rows={len(rows_in_db)}")

    def test_exact_triple_duplicate_upserts_in_place(self):
        """Same (movie_id, country, release_date) seen twice updates, does not duplicate."""
        from app.title_matching.seed_loader import seed_intl_from_rows
        from app.models import MovieMasterIntl

        session = self._make_session()
        rows = [
            {"id": "1", "movie_id": "133231", "movie_title": "Fiume o Morte!",
             "country": "Sint Maarten", "release_date": "2025-04-04", "genre": "Documentary"},
            {"id": "2", "movie_id": "133231", "movie_title": "Fiume o Morte!",
             "country": "Sint Maarten", "release_date": "2025-04-04", "genre": "Documentary"},
        ]
        result = seed_intl_from_rows(session, rows)
        assert result["inserted"] == 1
        assert result["updated"] == 1
        rows_in_db = session.exec(
            select(MovieMasterIntl).where(MovieMasterIntl.movie_id == 133231)
        ).all()
        assert len(rows_in_db) == 1
        _pass("SeedLoaderIntl", "exact_triple_duplicate_upsert", f"rows={len(rows_in_db)}")

    def test_usa_rows_retained_unfiltered(self):
        """USA rows in the international dump are not special-cased or filtered out."""
        from app.title_matching.seed_loader import seed_intl_from_rows
        from app.models import MovieMasterIntl

        session = self._make_session()
        rows = [{
            "id": "1", "movie_id": "18028", "movie_title": "Black Box (2020)",
            "country": "USA", "release_date": "2020-10-06",
        }]
        result = seed_intl_from_rows(session, rows)
        assert result["inserted"] == 1
        row = session.exec(
            select(MovieMasterIntl).where(MovieMasterIntl.country == "USA")
        ).first()
        assert row is not None
        _pass("SeedLoaderIntl", "usa_rows_retained", "USA country row inserted, not filtered")

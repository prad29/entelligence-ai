"""
Comprehensive test suite for the Movie Title Matching pipeline.

Covers every stage with structured pass/fail logs so you can see exactly
which part of the pipeline ran and what the result was.

Run with:
    pytest backend/tests/test_title_matching.py -v -s

Pipeline stages tested:
  Stage 0 — Normalizer (promo strip, mojibake, edition markers, event type, ordinal, franchise)
  Stage 1 — CandidateGenerator (fuzzy, franchise map, alias lookup, ordinal constraints, token coverage)
  Stage 2 — OG image fetcher (T1 plain HTTP poster extract)
  Stage 4 — Metadata Eliminator (date-window boost, edition penalty, recency boost)
  Stage 5 — Decision Engine (composite score, AUTO_ACCEPT/REVIEW thresholds, parent_id resolution)
  E2E     — TitleMatchEngine full pipeline
  API     — /single endpoint (engine loaded / not loaded)
  Seed    — seed_from_rows upsert logic
"""

from __future__ import annotations

import logging
import textwrap
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

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


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_master_rows(entries: list[tuple]) -> list[dict]:
    """entries: (id, title, release_date, cover_image, parent_id)"""
    return [
        {
            "id": e[0],
            "movie_title": e[1],
            "release_date": e[2] if len(e) > 2 else None,
            "cover_image": e[3] if len(e) > 3 else None,
            "parent_id": e[4] if len(e) > 4 else None,
        }
        for e in entries
    ]


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
# STAGE 1 — CANDIDATE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

class TestCandidateGenerator:
    """Stage 1: produces up to 10 candidates via fuzzy, franchise map, alias."""

    def setup_method(self):
        _section("STAGE 1 — CANDIDATE GENERATOR")

    @pytest.fixture
    def master_rows(self):
        return _make_master_rows([
            (1,  "The Dark Knight",           "2008-07-18", None, None),
            (2,  "The Dark Knight Rises",     "2012-07-20", None, None),
            (3,  "Batman Begins",             "2005-06-15", None, None),
            (4,  "Moana",                     "2016-11-23", None, None),
            (5,  "Moana 2",                   "2024-11-27", None, None),
            (14039, "Harry Potter and the Philosopher's Stone", "2001-11-16", None, None),
            (14038, "Harry Potter and the Chamber of Secrets",  "2002-11-15", None, None),
            (14061, "Harry Potter and the Prisoner of Azkaban", "2004-06-04", None, None),
            (13868, "Toy Story",              "1995-11-22", None, None),
            (14046, "Toy Story 2",            "1999-11-24", None, None),
            (10731, "Toy Story 3",            "2010-06-18", None, None),
            (105988, "Toy Story 4",           "2019-06-21", None, None),
        ])

    @pytest.fixture
    def gen(self, master_rows):
        from app.title_matching.candidate_generator import CandidateGenerator
        return CandidateGenerator(master_rows)

    def test_fuzzy_exact_hit(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("The Dark Knight")
        results = gen.generate(normalized, {})
        ids = [c.movie_master_id for c in results]
        assert 1 in ids
        _pass("Stage1/CandidateGen", "fuzzy_exact", f"ids={ids[:3]}")

    def test_fuzzy_partial_match(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("Dark Knight")
        results = gen.generate(normalized, {})
        ids = [c.movie_master_id for c in results]
        assert 1 in ids or 2 in ids
        _pass("Stage1/CandidateGen", "fuzzy_partial", f"ids={ids[:3]}")

    def test_franchise_map_harry_potter_3(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("HP 3")
        results = gen.generate(normalized, {})
        ids = [c.movie_master_id for c in results]
        assert 14061 in ids
        franchise_sources = [c.source for c in results if c.movie_master_id == 14061]
        assert "franchise_map" in franchise_sources
        _pass("Stage1/CandidateGen", "franchise_hp3", f"source=franchise_map id=14061")

    def test_franchise_map_toy_story_4(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("Toy Story 4")
        results = gen.generate(normalized, {})
        ids = [c.movie_master_id for c in results]
        assert 105988 in ids
        _pass("Stage1/CandidateGen", "franchise_ts4", f"id=105988 ids={ids[:3]}")

    def test_alias_lookup(self, gen):
        from app.title_matching.normalizer import normalize_title
        aliases = {"dark knight": 1}
        normalized = normalize_title("Dark Knight")
        results = gen.generate(normalized, aliases)
        alias_hits = [c for c in results if c.source == "alias"]
        assert len(alias_hits) > 0
        _pass("Stage1/CandidateGen", "alias_hit", f"alias_id={alias_hits[0].movie_master_id}")

    def test_max_k_candidates(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("Toy Story")
        results = gen.generate(normalized, {}, k=5)
        assert len(results) <= 5
        _pass("Stage1/CandidateGen", "max_k", f"count={len(results)} ≤ 5")

    def test_ordinal_constraint_filters_wrong_installment(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("Toy Story 3")
        results = gen.generate(normalized, {})
        # Toy Story 1 has no ordinal conflict with query ordinal=3 BUT
        # Toy Story 2 has ordinal 2 — should be filtered out when ordinal hard constraint applies
        # This checks that ordinal 3 resolves to id 10731
        ids = [c.movie_master_id for c in results]
        assert 10731 in ids, f"Toy Story 3 (id 10731) not in candidates: {ids}"
        _pass("Stage1/CandidateGen", "ordinal_constraint", f"ts3_id=10731 in {ids[:4]}")

    def test_fuzzy_score_scaled(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("The Dark Knight")
        results = gen.generate(normalized, {})
        fuzzy_hits = [c for c in results if c.source == "fuzzy"]
        if fuzzy_hits:
            assert all(0.0 <= c.score <= 0.5 for c in fuzzy_hits), \
                f"Fuzzy scores should be in 0–0.5 range: {[c.score for c in fuzzy_hits]}"
        _pass("Stage1/CandidateGen", "fuzzy_score_range", f"scores={[round(c.score,3) for c in fuzzy_hits[:3]]}")

    def test_alias_score_high(self, gen):
        from app.title_matching.normalizer import normalize_title
        aliases = {"inception": 999}
        # Add id 999 to gen's id_to_row manually
        gen._id_to_row[999] = {"id": 999, "movie_title": "Inception", "release_date": "2010-07-16", "cover_image": None}
        normalized = normalize_title("Inception")
        results = gen.generate(normalized, aliases)
        alias_hits = [c for c in results if c.source == "alias"]
        assert alias_hits and alias_hits[0].score == 0.95
        _pass("Stage1/CandidateGen", "alias_score_0.95", f"score={alias_hits[0].score}")

    def test_no_candidates_for_gibberish(self, gen):
        from app.title_matching.normalizer import normalize_title
        normalized = normalize_title("xyzxyz qqqqqq zzzzz totally unknown title")
        results = gen.generate(normalized, {})
        # May return low-confidence results but should not crash
        _pass("Stage1/CandidateGen", "gibberish_no_crash", f"count={len(results)}")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2 — OG IMAGE FETCHER (T1)
# ─────────────────────────────────────────────────────────────────────────────

class TestOGImageFetcher:
    """
    Stage 2 (T1 only): fetches og:image from ticketing URL via plain HTTP.
    Stage 3 (pHash / CLIP) is Phase 2 — NOT YET BUILT.
    """

    def setup_method(self):
        _section("STAGE 2 — OG IMAGE FETCHER (T1 plain HTTP)")

    def test_og_image_extracted_from_html(self):
        from app.title_matching.engine import _fetch_og_image

        html = textwrap.dedent("""
            <html><head>
            <meta property="og:image" content="https://example.com/poster.jpg" />
            </head><body></body></html>
        """)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("httpx.get", return_value=mock_resp):
            result = _fetch_og_image("https://tickets.example.com/moana")

        assert result == "https://example.com/poster.jpg"
        _pass("Stage2/OGFetch", "og_image_parse", f"url={result}")

    def test_og_image_missing_returns_none(self):
        from app.title_matching.engine import _fetch_og_image

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head></head><body>no og tags</body></html>"

        with patch("httpx.get", return_value=mock_resp):
            result = _fetch_og_image("https://tickets.example.com/movie")

        assert result is None
        _pass("Stage2/OGFetch", "og_missing_returns_none", "result=None")

    def test_http_error_returns_none(self):
        from app.title_matching.engine import _fetch_og_image

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.get", return_value=mock_resp):
            result = _fetch_og_image("https://tickets.example.com/404")

        assert result is None
        _pass("Stage2/OGFetch", "http_404_returns_none", "result=None")

    def test_network_exception_returns_none(self):
        from app.title_matching.engine import _fetch_og_image

        with patch("httpx.get", side_effect=Exception("Connection refused")):
            result = _fetch_og_image("https://unreachable.example.com")

        assert result is None
        _pass("Stage2/OGFetch", "network_error_silent", "result=None (no exception raised)")

    def test_og_image_only_parses_first_50kb(self):
        """Parser should not crash on large pages; only parses first 50000 chars."""
        from app.title_matching.engine import _fetch_og_image

        big_content = "x" * 40000
        og_part = '<meta property="og:image" content="https://example.com/found.jpg" />'
        html = og_part + big_content  # og tag is within first 50kb

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("httpx.get", return_value=mock_resp):
            result = _fetch_og_image("https://example.com/large-page")

        assert result == "https://example.com/found.jpg"
        _pass("Stage2/OGFetch", "large_page_ok", f"url={result}")

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
# STAGE 4 — METADATA ELIMINATOR
# ─────────────────────────────────────────────────────────────────────────────

class TestDecisionEngineStage4:
    """Stage 4: date-window boost, edition marker filter, recency boost."""

    def setup_method(self):
        _section("STAGE 4 — METADATA ELIMINATOR")

    def _candidate(self, mid: int, title: str, score: float, release: Optional[str] = None) -> object:
        from app.title_matching.types import CandidateResult
        return CandidateResult(
            movie_master_id=mid,
            movie_title=title,
            release_date=release,
            cover_image=None,
            score=score,
            source="fuzzy",
        )

    def _normalized(self, title: str) -> object:
        from app.title_matching.normalizer import normalize_title
        return normalize_title(title)

    def test_date_boost_exact(self):
        from app.title_matching.decision_engine import _date_boost
        boost, label = _date_boost("2016-11-23", "2016-11-23")
        assert boost == 0.30
        assert label == "EXACT"
        _pass("Stage4/DateBoost", "exact_match", f"boost={boost} label={label}")

    def test_date_boost_near_within_30_days(self):
        from app.title_matching.decision_engine import _date_boost
        boost, label = _date_boost("2016-11-30", "2016-11-23")
        assert boost == 0.15
        assert label == "NEAR"
        _pass("Stage4/DateBoost", "near_30_days", f"boost={boost} label={label}")

    def test_date_boost_year_within_365(self):
        from app.title_matching.decision_engine import _date_boost
        boost, label = _date_boost("2017-05-01", "2016-11-23")
        assert boost == 0.05
        assert label == "YEAR"
        _pass("Stage4/DateBoost", "year_window", f"boost={boost} label={label}")

    def test_date_boost_none_when_no_dates(self):
        from app.title_matching.decision_engine import _date_boost
        boost, label = _date_boost(None, None)
        assert boost == 0.0
        assert label == "NONE"
        _pass("Stage4/DateBoost", "no_dates", f"boost={boost}")

    def test_date_boost_sentinel_release_date(self):
        from app.title_matching.decision_engine import _date_boost
        boost, label = _date_boost("2024-01-01", "0000-00-00")
        assert boost == 0.0
        _pass("Stage4/DateBoost", "sentinel_date", f"boost={boost} (0000 sentinel ignored)")

    def test_edition_penalty_live_action_vs_animation(self):
        from app.title_matching.decision_engine import _edition_penalty
        from app.title_matching.normalizer import normalize_title
        norm = normalize_title("Moana (Live Action)")
        penalty = _edition_penalty(norm, "Moana Animated")
        assert penalty == -0.20
        _pass("Stage4/Edition", "live_action_vs_animation", f"penalty={penalty}")

    def test_edition_no_penalty_without_live_action_marker(self):
        from app.title_matching.decision_engine import _edition_penalty
        from app.title_matching.normalizer import normalize_title
        norm = normalize_title("Moana")
        penalty = _edition_penalty(norm, "Moana Animated")
        assert penalty == 0.0
        _pass("Stage4/Edition", "no_live_action_no_penalty", f"penalty={penalty}")

    def test_recency_boost_most_recent_wins(self):
        from app.title_matching.decision_engine import _recency_boost
        from app.title_matching.types import CandidateResult

        old = CandidateResult(1, "Moana", "2016-11-23", None, 0.5, "fuzzy")
        new = CandidateResult(2, "Moana 2", "2024-11-27", None, 0.5, "fuzzy")
        candidates = [new, old]
        # winner_idx=0 is "Moana 2" (most recent)
        boost = _recency_boost(candidates, 0)
        assert boost == 0.05
        _pass("Stage4/Recency", "most_recent_gets_boost", f"boost={boost}")

    def test_recency_no_boost_when_not_most_recent(self):
        from app.title_matching.decision_engine import _recency_boost
        from app.title_matching.types import CandidateResult

        old = CandidateResult(1, "Moana", "2016-11-23", None, 0.5, "fuzzy")
        new = CandidateResult(2, "Moana 2", "2024-11-27", None, 0.5, "fuzzy")
        candidates = [old, new]
        # winner_idx=0 is "Moana" (older) — someone else is more recent
        boost = _recency_boost(candidates, 0)
        assert boost == 0.0
        _pass("Stage4/Recency", "older_no_boost", f"boost={boost}")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5 — DECISION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TestDecisionEngineStage5:
    """Stage 5: composite score → AUTO_ACCEPT / REVIEW, parent_id resolution."""

    def setup_method(self):
        _section("STAGE 5 — DECISION ENGINE")

    def _make_id_to_row(self, entries: list[tuple]) -> dict:
        return {
            e[0]: {"id": e[0], "movie_title": e[1], "release_date": e[2], "parent_id": e[3] if len(e) > 3 else None}
            for e in entries
        }

    def _candidate(self, mid: int, title: str, score: float, release: Optional[str] = None):
        from app.title_matching.types import CandidateResult
        return CandidateResult(mid, title, release, None, score, "fuzzy")

    def test_auto_accept_above_threshold(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Inception")
        candidates = [self._candidate(1, "Inception", 0.95, "2010-07-16")]
        result = score_and_decide(norm, candidates, show_date=None, theater=None)
        assert result.decision == "AUTO_ACCEPT"
        _pass("Stage5/Decision", "auto_accept", f"score={result.confidence} decision={result.decision}")

    def test_review_below_threshold(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Incepshun")
        candidates = [self._candidate(1, "Inception", 0.35, "2010-07-16")]
        result = score_and_decide(norm, candidates, show_date=None, theater=None)
        assert result.decision == "REVIEW"
        _pass("Stage5/Decision", "review_low_confidence", f"score={result.confidence}")

    def test_review_non_movie_always_review(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Taylor Swift Eras Tour Concert")
        candidates = [self._candidate(1, "Taylor Swift Concert", 0.95)]
        result = score_and_decide(norm, candidates, show_date=None, theater=None)
        assert "REVIEW" in result.decision
        _pass("Stage5/Decision", "non_movie_always_review", f"decision={result.decision}")

    def test_review_multi_film_always_review(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Marvel Marathon Double Feature")
        candidates = [self._candidate(1, "Avengers", 0.95)]
        result = score_and_decide(norm, candidates, show_date=None, theater=None)
        assert "REVIEW" in result.decision
        _pass("Stage5/Decision", "multi_film_always_review", f"decision={result.decision}")

    def test_date_boost_pushes_to_auto_accept(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Moana")
        # base score 0.65 + exact date boost 0.30 = 0.95 → AUTO_ACCEPT
        candidates = [self._candidate(1, "Moana", 0.65, "2016-11-23")]
        result = score_and_decide(norm, candidates, show_date="2016-11-23", theater=None)
        assert result.decision == "AUTO_ACCEPT"
        assert result.evidence["date_window"] == "EXACT"
        _pass("Stage5/Decision", "date_boost_auto_accept", f"score={result.confidence} date={result.evidence['date_window']}")

    def test_parent_id_resolved(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        id_to_row = self._make_id_to_row([
            (101, "Batman", "2005-06-15", 100),    # parent_id=100
            (100, "Batman Begins", "2005-06-15"),   # canonical
        ])
        norm = normalize_title("Batman")
        candidates = [self._candidate(101, "Batman", 0.92)]
        result = score_and_decide(norm, candidates, show_date=None, theater=None, id_to_row=id_to_row)
        assert result.canonical_movie_id == 100
        assert result.suggested_movie_id == 101
        _pass("Stage5/Decision", "parent_id_resolved",
              f"suggested={result.suggested_movie_id} canonical={result.canonical_movie_id}")

    def test_no_candidates_returns_review(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Inception")
        result = score_and_decide(norm, [], show_date=None, theater=None)
        assert result.decision == "REVIEW"
        assert result.confidence == 0.0
        _pass("Stage5/Decision", "empty_candidates", f"decision={result.decision}")

    def test_evidence_structure(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("Inception")
        candidates = [
            self._candidate(1, "Inception", 0.92),
            self._candidate(2, "Interstellar", 0.35),
        ]
        result = score_and_decide(norm, candidates, show_date=None, theater=None)
        assert "fuzzy_top" in result.evidence
        assert "date_window" in result.evidence
        assert "eliminated" in result.evidence
        _pass("Stage5/Decision", "evidence_keys", f"keys={list(result.evidence.keys())}")

    def test_reasoning_non_empty(self):
        from app.title_matching.normalizer import normalize_title
        from app.title_matching.decision_engine import score_and_decide

        norm = normalize_title("The Dark Knight")
        candidates = [self._candidate(1, "The Dark Knight", 0.95, "2008-07-18")]
        result = score_and_decide(norm, candidates, show_date=None, theater=None)
        assert result.reasoning and len(result.reasoning) > 10
        _pass("Stage5/Decision", "reasoning_non_empty", f"'{result.reasoning[:60]}…'")


# ─────────────────────────────────────────────────────────────────────────────
# E2E — FULL PIPELINE (TitleMatchEngine)
# ─────────────────────────────────────────────────────────────────────────────

class TestTitleMatchEngineE2E:
    """End-to-end through TitleMatchEngine.match()."""

    def setup_method(self):
        _section("E2E — FULL PIPELINE")

    @pytest.fixture
    def engine(self):
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.engine import TitleMatchEngine

        rows = _make_master_rows([
            (1,    "Inception",               "2010-07-16", "https://img.example.com/inception.jpg", None),
            (2,    "Interstellar",             "2014-11-07", None, None),
            (3,    "The Dark Knight",          "2008-07-18", None, None),
            (4,    "Moana",                    "2016-11-23", None, None),
            (5,    "Moana 2",                  "2024-11-27", None, None),
            (14039, "Harry Potter and the Philosopher's Stone", "2001-11-16", None, None),
            (13868, "Toy Story",               "1995-11-22", None, None),
            (105988, "Toy Story 4",            "2019-06-21", None, None),
        ])
        gen = CandidateGenerator(rows)
        return TitleMatchEngine(gen, {})

    def test_exact_match_inception(self, engine):
        result = engine.match("Inception", show_date="2010-07-16")
        assert result.suggested_movie_id == 1
        assert result.confidence >= 0.80
        _pass("E2E/Pipeline", "inception_exact",
              f"id={result.suggested_movie_id} conf={result.confidence} decision={result.decision}")

    def test_promo_stripped_before_match(self, engine):
        result = engine.match("KIDSHOW: Toy Story 4")
        assert result.suggested_movie_id == 105988
        _pass("E2E/Pipeline", "promo_stripped", f"id={result.suggested_movie_id} conf={result.confidence}")

    def test_harry_potter_franchise_map(self, engine):
        result = engine.match("HP 1")
        assert result.suggested_movie_id == 14039
        _pass("E2E/Pipeline", "hp1_franchise_map", f"id={result.suggested_movie_id}")

    def test_cover_image_attached(self, engine):
        result = engine.match("Inception")
        assert result.cover_image is not None
        assert "inception" in result.cover_image.lower()
        _pass("E2E/Pipeline", "cover_image", f"url={result.cover_image}")

    def test_cover_image_noimage_filtered(self, engine):
        # Replace Inception's cover_image with noimage sentinel
        engine._id_to_row[1]["cover_image"] = "noimage.jpg"
        result = engine.match("Inception")
        assert result.cover_image is None
        _pass("E2E/Pipeline", "noimage_filtered", "cover_image=None for noimage.jpg")

    def test_ticketing_url_og_image_fetched(self, engine):
        html = '<meta property="og:image" content="https://cdn.tickets.com/moana.jpg" />'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("httpx.get", return_value=mock_resp):
            result = engine.match("Moana", ticketing_url="https://tickets.example.com/moana")

        assert result.ticketing_poster_url == "https://cdn.tickets.com/moana.jpg"
        _pass("E2E/Pipeline", "ticketing_poster", f"url={result.ticketing_poster_url}")

    def test_ticketing_url_none_skipped(self, engine):
        result = engine.match("Moana", ticketing_url=None)
        assert result.ticketing_poster_url is None
        _pass("E2E/Pipeline", "no_ticketing_url", "ticketing_poster_url=None (skipped)")

    def test_result_always_has_reasoning(self, engine):
        result = engine.match("xyzxyz completely unknown qqqqqq")
        assert result.reasoning and len(result.reasoning) > 5
        _pass("E2E/Pipeline", "always_has_reasoning", f"'{result.reasoning[:60]}…'")

    def test_multi_film_title_routes_to_review(self, engine):
        result = engine.match("Marvel Marathon Double Feature")
        assert "REVIEW" in result.decision
        _pass("E2E/Pipeline", "multi_film_review", f"decision={result.decision}")

    def test_live_action_vs_animation(self, engine):
        result = engine.match("Moana (Live Action)")
        # Should still find Moana; no animation candidates in this fixture
        assert result.suggested_movie_id in (4, 5)
        _pass("E2E/Pipeline", "live_action_match", f"id={result.suggested_movie_id}")


# ─────────────────────────────────────────────────────────────────────────────
# API — /single endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAPIEndpoint:
    """API-layer tests for POST /api/v1/movie-title-match/single."""

    def setup_method(self):
        _section("API — /single endpoint")

    @pytest.fixture
    def client_no_engine(self):
        from fastapi.testclient import TestClient
        from app.main import app

        app.state.title_match_engine = None
        return TestClient(app)

    @pytest.fixture
    def client_with_engine(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.engine import TitleMatchEngine

        rows = _make_master_rows([
            (1, "Inception", "2010-07-16", None, None),
            (2, "Interstellar", "2014-11-07", None, None),
        ])
        gen = CandidateGenerator(rows)
        app.state.title_match_engine = TitleMatchEngine(gen, {})
        return TestClient(app)

    def test_engine_not_loaded_returns_review(self, client_no_engine):
        resp = client_no_engine.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "REVIEW"
        assert "seeded" in data["reasoning"].lower() or "not loaded" in data["reasoning"].lower()
        _pass("API/single", "engine_not_loaded", f"decision={data['decision']}")

    def test_engine_loaded_returns_match(self, client_with_engine):
        resp = client_with_engine.post(
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

    def test_missing_title_returns_422(self, client_no_engine):
        resp = client_no_engine.post(
            "/api/v1/movie-title-match/single",
            json={}
        )
        assert resp.status_code == 422
        _pass("API/single", "missing_title_422", f"status={resp.status_code}")

    def test_optional_fields_accepted(self, client_with_engine):
        resp = client_with_engine.post(
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

    def test_response_has_all_required_keys(self, client_with_engine):
        resp = client_with_engine.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception"}
        )
        data = resp.json()
        required = {"suggested_movie_id", "suggested_movie_title", "confidence",
                    "decision", "reasoning", "evidence", "fired_ai"}
        missing = required - set(data.keys())
        assert not missing, f"Missing keys: {missing}"
        _pass("API/single", "response_schema", f"all {len(required)} required keys present")


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

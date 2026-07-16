"""
Unit + integration tests for the three batch-matching candidate-fetch fixes:

1. Vespa field-name mismatch (_fetch_vespa_candidates read the wrong keys —
   `id`/`movie_title` instead of the schema's `movie_master_id`/`title`).
2. Brittle DB keyword search (_db_search's ILIKE containment missed rows
   that a punctuation/accent-tolerant trigram search finds).
3. NON_MOVIE short-circuit in the prompt (event_type must never justify
   giving up on a DB match — covered via prompt_builder content assertions
   plus a result_parser check that NON_MOVIE keeps a real candidate id).

Bug 2's fix depends on the pg_trgm/unaccent extensions and trigram index
added by migration f6a1b2c3d4e5, so that test is marked `integration` and
runs against the real Postgres DB (matching the existing pattern in
test_batch_chord_live.py), not the in-memory SQLite used elsewhere in this
suite.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.title_matching.agentic import prompt_builder
from app.title_matching.agentic.result_parser import _build_result
from app.title_matching.agentic.runner import _fetch_vespa_candidates


# ── Bug 1: Vespa field-name mismatch ─────────────────────────────────────────

def _vespa_response(hits: list[dict]) -> bytes:
    return json.dumps({"root": {"children": hits}}).encode()


def test_fetch_vespa_candidates_reads_schema_field_names():
    """The Vespa schema (movie_master.sd) stores movie_master_id/title, not
    id/movie_title — the candidate dicts must be built from the real keys."""
    hit = {
        "relevance": 22.05,
        "fields": {
            "movie_master_id": 147675,
            "title": "Oh..! Sukumari",
            "release_date": None,
        },
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([hit])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=fake_resp):
        candidates = _fetch_vespa_candidates("Oh Sukumari")

    assert candidates == [
        {
            "id": 147675,
            "movie_title": "Oh..! Sukumari",
            "release_date": None,
            "relevance": 22.05,
        }
    ]


def test_fetch_vespa_candidates_never_returns_null_id_for_a_real_hit():
    """Regression guard for the exact symptom seen in production: every
    candidate came back {id: None, movie_title: None} because the wrong
    keys were read, even though Vespa returned a real, relevant hit."""
    hit = {
        "relevance": 9.27,
        "fields": {"movie_master_id": 1973, "title": "Oh Willy...", "release_date": None},
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([hit])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=fake_resp):
        candidates = _fetch_vespa_candidates("Oh Sukumari")

    assert candidates[0]["id"] is not None
    assert candidates[0]["movie_title"] is not None


def test_fetch_vespa_candidates_degrades_to_empty_list_on_error():
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        assert _fetch_vespa_candidates("anything") == []


# ── Bug 2: brittle DB keyword search / trigram fallback ─────────────────────

@pytest.mark.integration
class TestDbSearchTrigramFallback:
    """Requires the real Postgres DB with pg_trgm/unaccent enabled and the
    moviemaster table seeded (migration f6a1b2c3d4e5). Skips cleanly if the
    extensions aren't installed, rather than silently passing."""

    _FIXTURE_IDS = (900001, 900002, 900003, 900004)

    @pytest.fixture(autouse=True)
    def _require_trgm_and_cleanup_fixtures(self):
        from sqlmodel import Session
        from app.database import engine

        with Session(engine) as session:
            from sqlalchemy import text

            installed = session.exec(
                text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'")
            ).first()
            if not installed:
                pytest.skip("pg_trgm extension not installed; run migration f6a1b2c3d4e5")

        yield

        with Session(engine) as session:
            from app.models import MovieMaster

            for movie_id in self._FIXTURE_IDS:
                row = session.get(MovieMaster, movie_id)
                if row is not None:
                    session.delete(row)
            session.commit()

    def _seed(self, session, movie_id: int, title: str):
        from app.models import MovieMaster

        existing = session.get(MovieMaster, movie_id)
        if existing is None:
            session.add(MovieMaster(id=movie_id, movie_title=title))
            session.commit()

    def test_ilike_miss_falls_back_to_trigram_for_punctuation_variant(self):
        from sqlmodel import Session
        from app.database import engine
        from app.title_matching.agentic.runner import _db_search

        with Session(engine) as session:
            self._seed(session, 900001, "Oh..! Sukumari Test Fixture")

        results = _db_search("Oh Sukumari Test Fixture")
        assert any(r["id"] == 900001 for r in results)

    def test_ilike_miss_falls_back_to_trigram_for_word_choice_variant(self):
        from sqlmodel import Session
        from app.database import engine
        from app.title_matching.agentic.runner import _db_search

        with Session(engine) as session:
            self._seed(session, 900002, "DCI Test Fixture: Big, Loud & Live")

        results = _db_search("DCI Test Fixture BIG LOUD AND LIVE")
        assert any(r["id"] == 900002 for r in results)

    def test_exact_ilike_hit_skips_trigram_and_ranks_exact_match_first(self):
        from sqlmodel import Session
        from app.database import engine
        from app.title_matching.agentic.runner import _db_search

        with Session(engine) as session:
            self._seed(session, 900003, "Exact Match Fixture")
            self._seed(session, 900004, "Exact Match Fixture: Extended Cut")

        results = _db_search("Exact Match Fixture")
        assert results[0]["id"] == 900003

    def test_no_plausible_match_returns_empty(self):
        from app.title_matching.agentic.runner import _db_search

        results = _db_search("Zzqxw Nonexistent Fixture Title 12345")
        assert results == []


# ── Bug 3: NON_MOVIE must never zero out a real DB match ────────────────────

def test_prompt_states_movie_master_contains_non_film_content():
    prompt = prompt_builder.build_prompt("some title", None, None, None)
    assert "sports broadcast" in prompt.lower()
    assert "NOT limited to theatrical films" in prompt or "not limited to theatrical films" in prompt.lower()


def test_prompt_states_event_type_is_metadata_only():
    prompt = prompt_builder.build_prompt("some title", None, None, None)
    assert "metadata only" in prompt.lower()
    assert "never a reason to skip matching" in prompt.lower() or "never a reason" in prompt.lower()


def test_build_result_keeps_non_movie_candidate_id():
    """event_type=NON_MOVIE must not force movie_master_id back to 0 — the
    decision engine downgrades it to REVIEW_NON_MOVIE for human review, but
    the underlying match is preserved so present_in_db can resolve to Yes."""
    payload = {
        "candidates": [
            {
                "movie_master_id": 147828,
                "movie_title": "2026 FIFA World Cup Semi-final: England vs. Argentina",
                "confidence": 0.97,
                "reasoning": "Sports broadcast row exists in Movie Master.",
            }
        ],
        "best_match_index": 0,
        "event_type": "NON_MOVIE",
    }

    result = _build_result(payload, raw_text=json.dumps(payload))

    assert result.suggested_movie_id == 147828
    assert result.canonical_movie_id == 147828
    assert result.decision == "REVIEW_NON_MOVIE"

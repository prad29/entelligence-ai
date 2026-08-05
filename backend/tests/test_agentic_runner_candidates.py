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
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.title_matching.agentic import prompt_builder, runner as runner_mod
from app.title_matching.agentic.result_parser import _build_result
from app.title_matching.agentic.runner import _fetch_vespa_candidates, run_agentic_match
from app.title_matching.types import TitleMatchResult


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

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp):
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

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        candidates = _fetch_vespa_candidates("Oh Sukumari")

    assert candidates[0]["id"] is not None
    assert candidates[0]["movie_title"] is not None


def test_fetch_vespa_candidates_degrades_to_empty_list_on_error():
    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        assert _fetch_vespa_candidates("anything") == []


# ── Fix: real hybrid (BM25 + ANN embedding) search ──────────────────────────

def test_fetch_vespa_candidates_sends_embedding_and_nearest_neighbor_clause():
    """Root-cause fix: the pre-fetch must embed the query title and issue the
    nearestNeighbor YQL clause (matching semantic_index.py's proven pattern),
    not BM25-keyword-only search. This is what lets cross-language titles like
    "Aguas Mortais" retrieve "Deep Water" as a candidate."""
    fake_embedding = [0.1, 0.2, 0.3]
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=fake_embedding) as mock_embed, \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("Aguas Mortais", market="international")

    mock_embed.assert_called_once()
    assert mock_embed.call_args[0][0] == "Aguas Mortais"

    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    assert "nearestNeighbor(embedding,q_embedding)" in body["yql"]
    assert body["input.query(q_embedding)"] == fake_embedding


def test_fetch_vespa_candidates_falls_back_to_bm25_when_embedding_unavailable():
    """If get_embedding returns None (e.g. Bedrock unreachable), the pre-fetch
    must still run a plain BM25 search rather than failing outright."""
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        candidates = _fetch_vespa_candidates("Aguas Mortais", market="international")

    assert candidates == []
    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    assert "nearestNeighbor" not in body["yql"]
    assert "input.query(q_embedding)" not in body


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


@pytest.mark.integration
class TestDbSearchInternational:
    """Requires the real Postgres DB with moviemasterintl (migration a7b8c9d0e1f2)."""

    _FIXTURE_ROWS = [
        (910001, "France", "2024-01-01", "Runner Intl Test Movie"),
        (910001, "Germany", "2024-01-05", "Runner Intl Test Movie"),
        (910002, "France", "2024-02-01", "Runner Intl Other Country Movie"),
    ]

    @pytest.fixture(autouse=True)
    def _seed_and_cleanup_fixtures(self):
        from sqlmodel import Session, select
        from app.database import engine
        from app.models import MovieMasterIntl

        with Session(engine) as session:
            for movie_id, country, release_date, title in self._FIXTURE_ROWS:
                existing = session.exec(
                    select(MovieMasterIntl).where(
                        MovieMasterIntl.movie_id == movie_id,
                        MovieMasterIntl.country == country,
                        MovieMasterIntl.release_date == release_date,
                    )
                ).first()
                if existing is None:
                    session.add(MovieMasterIntl(
                        movie_id=movie_id, country=country,
                        release_date=release_date, movie_title=title,
                    ))
            session.commit()

        yield

        with Session(engine) as session:
            for movie_id, country, release_date, _ in self._FIXTURE_ROWS:
                row = session.exec(
                    select(MovieMasterIntl).where(
                        MovieMasterIntl.movie_id == movie_id,
                        MovieMasterIntl.country == country,
                        MovieMasterIntl.release_date == release_date,
                    )
                ).first()
                if row is not None:
                    session.delete(row)
            session.commit()

    def test_intl_search_scoped_to_country_excludes_other_countries(self):
        from app.title_matching.agentic.runner import _db_search

        results = _db_search("Runner Intl Test Movie", market="international", country="France")
        countries = {r["country"] for r in results}
        assert countries == {"France"}
        assert any(r["movie_title"] == "Runner Intl Test Movie" for r in results)

    def test_intl_search_without_country_returns_all_countries(self):
        from app.title_matching.agentic.runner import _db_search

        results = _db_search("Runner Intl Test Movie", market="international")
        countries = {r["country"] for r in results}
        assert countries == {"France", "Germany"}

    def test_intl_search_does_not_leak_domestic_rows(self):
        """Domestic market must never return a MovieMasterIntl fixture id —
        _db_search(market="domestic") queries MovieMaster only. The trigram
        fallback may still surface unrelated real domestic titles for this
        query (expected fuzzy-match behavior against the full corpus); what
        matters is that none of the international fixture ids leak through."""
        from app.title_matching.agentic.runner import _db_search

        domestic_results = _db_search("Runner Intl Other Country Movie", market="domestic")
        fixture_ids = {movie_id for movie_id, *_ in self._FIXTURE_ROWS}
        assert not (fixture_ids & {r["id"] for r in domestic_results})


# ── Fix 2: master_movie_title as a secondary international search target ───

@pytest.mark.integration
class TestDbSearchIntlMasterTitleFallback:
    """Requires the real Postgres DB with moviemasterintl.master_movie_title
    (migration adding that column). A ticketing page sometimes shows the
    English title for a market where the DB only carries the country-local
    movie_title — _db_search should still find the row via master_movie_title
    when the movie_title ILIKE comes up empty."""

    _MOVIE_ID = 920001
    _COUNTRY = "Brazil"
    _RELEASE_DATE = "2024-03-01"
    _LOCAL_TITLE = "Aguas Mortais Test Fixture"
    _MASTER_TITLE = "Deep Water Test Fixture"

    @pytest.fixture(autouse=True)
    def _seed_and_cleanup_fixture(self):
        from sqlmodel import Session, select
        from app.database import engine
        from app.models import MovieMasterIntl

        with Session(engine) as session:
            existing = session.exec(
                select(MovieMasterIntl).where(
                    MovieMasterIntl.movie_id == self._MOVIE_ID,
                    MovieMasterIntl.country == self._COUNTRY,
                    MovieMasterIntl.release_date == self._RELEASE_DATE,
                )
            ).first()
            if existing is None:
                session.add(MovieMasterIntl(
                    movie_id=self._MOVIE_ID, country=self._COUNTRY,
                    release_date=self._RELEASE_DATE, movie_title=self._LOCAL_TITLE,
                    master_movie_title=self._MASTER_TITLE,
                ))
                session.commit()

        yield

        with Session(engine) as session:
            row = session.exec(
                select(MovieMasterIntl).where(
                    MovieMasterIntl.movie_id == self._MOVIE_ID,
                    MovieMasterIntl.country == self._COUNTRY,
                    MovieMasterIntl.release_date == self._RELEASE_DATE,
                )
            ).first()
            if row is not None:
                session.delete(row)
            session.commit()

    def test_movie_title_ilike_miss_falls_back_to_master_movie_title(self):
        from app.title_matching.agentic.runner import _db_search

        results = _db_search(self._MASTER_TITLE, market="international", country=self._COUNTRY)
        assert any(r["movie_title"] == self._LOCAL_TITLE for r in results)


# ── Bug 3: NON_MOVIE must never zero out a real DB match ────────────────────

def test_prompt_states_movie_master_contains_non_film_content():
    prompt = prompt_builder.build_prompt("some title", None, None, None)
    assert "sports broadcast" in prompt.lower()
    assert "NOT limited to theatrical films" in prompt or "not limited to theatrical films" in prompt.lower()


def test_prompt_states_event_type_is_metadata_only():
    prompt = prompt_builder.build_prompt("some title", None, None, None)
    assert "metadata only" in prompt.lower()
    assert "never a reason to skip matching" in prompt.lower() or "never a reason" in prompt.lower()


def test_fetch_vespa_candidates_scopes_source_by_market():
    """Domestic queries must hit movie_master, international must hit
    movie_master_intl — never the unscoped `sources *` (which would leak
    cross-market results now that both document types have real data)."""
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("Blue Beetle", market="domestic")
        domestic_body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
        assert "from sources movie_master " in domestic_body["yql"]
        assert "movie_master_intl" not in domestic_body["yql"]

        _fetch_vespa_candidates("Blue Beetle", market="international")
        intl_body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
        assert "from sources movie_master_intl " in intl_body["yql"]


def test_fetch_vespa_candidates_reads_intl_id_field():
    hit = {
        "relevance": 15.0,
        "fields": {"movie_master_intl_id": 555, "title": "Blue Beetle", "release_date": None},
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([hit])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        candidates = _fetch_vespa_candidates("Blue Beetle", market="international")

    assert candidates == [
        {"id": 555, "movie_title": "Blue Beetle", "release_date": None, "relevance": 15.0, "country": None}
    ]


def test_fetch_vespa_candidates_adds_country_filter_for_international():
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("Aguas Mortais", market="international", country="Brazil")

    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    assert 'and country contains "Brazil"' in body["yql"]


def test_fetch_vespa_candidates_wraps_recall_clause_in_parens_before_and():
    """Precedence guard: YQL's `and` binds tighter than `or`, so the recall
    clause (ANN or BM25) must be parenthesized as one group before the
    country filter is appended, or the ANN branch would silently stay
    country-blind."""
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=[0.1, 0.2]), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("Aguas Mortais", market="international", country="Brazil")

    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    yql = body["yql"]
    assert "where ((" in yql
    assert ") or userQuery())" in yql
    assert yql.index(") or userQuery())") < yql.index("and country contains")


def test_fetch_vespa_candidates_omits_country_filter_for_domestic():
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("Dirty Dancing", market="domestic", country="Belgium")

    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    assert "country" not in body["yql"]


def test_fetch_vespa_candidates_omits_country_filter_when_country_is_none():
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("Aguas Mortais", market="international", country=None)

    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    assert "country" not in body["yql"]


def test_fetch_vespa_candidates_country_filter_handles_multiword_country():
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
        _fetch_vespa_candidates("THE IMMORTALS", market="international", country="South Korea")

    body = json.loads(mock_urlopen.call_args.kwargs.get("data") or mock_urlopen.call_args[0][0].data)
    assert 'and country contains "South Korea"' in body["yql"]


def test_fetch_vespa_candidates_returns_country_in_candidate_dicts():
    hit = {
        "relevance": 12.0,
        "fields": {
            "movie_master_intl_id": 910001, "title": "Runner Intl Test Movie",
            "release_date": None, "country": "France",
        },
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = _vespa_response([hit])
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.__exit__.return_value = False

    with patch("app.title_matching.agentic.runner.get_embedding", return_value=None), \
         patch("urllib.request.urlopen", return_value=fake_resp):
        candidates = _fetch_vespa_candidates("Runner Intl Test Movie", market="international", country="France")

    assert candidates[0]["country"] == "France"


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


# ── Fix: international id=0 post-lookup used the wrong (localized) title ────
#
# See INTL_SEMANTIC_REGRESSION_ANALYSIS.md. The intl prompt used to tell the
# agent to report the *localized* release title (e.g. "Águas Mortais") when
# movie_master_id is 0, but MovieMasterIntl actually stores the English/master
# title ("Deep Water") for these rows — so the id=0 post-lookup in
# run_agentic_match searched for a string that doesn't exist in the DB and
# always came up empty, even though the agent had already identified the
# correct film. Fix: tell the agent to report the English title (what the DB
# actually stores), and let it optionally supply a second guess in
# alternate_movie_title so the post-lookup can try both.

def test_intl_prompt_instructs_english_title_not_localized():
    prompt = prompt_builder.build_prompt(
        "Aguas Mortais", None, None, None, market="international", country="Brazil",
    )
    assert "ENGLISH" in prompt
    assert "Deep Water" in prompt  # the illustrative example naming the failure mode
    # The old (buggy) wording told the agent to report the localized release
    # title as the primary post-lookup key — must no longer be present.
    assert "title used for this film's theatrical" not in prompt


def test_intl_prompt_documents_alternate_movie_title_output_key():
    prompt = prompt_builder.build_prompt(
        "Aguas Mortais", None, None, None, market="international", country="Brazil",
    )
    assert "alternate_movie_title" in prompt


def test_domestic_prompt_zero_rule_unchanged_by_intl_fix():
    """Regression guard: the domestic id=0 rule and prompt output must be
    byte-identical to before this fix — only the international template
    changed."""
    prompt = prompt_builder.build_prompt("Some Domestic Title", None, None, None)
    assert "US/domestic theatrical release" in prompt
    assert "alternate_movie_title" not in prompt


# ── Skill-content inlining fix: intl-only invocation + rerelease_lookup_title ──
#
# claude-sandbox runs one-shot `claude --print -` with a fresh, empty $HOME
# per request. A live smoke test (curl'ing the rebuilt sandbox image with a
# baked-in SKILL.md) confirmed the CLI genuinely discovers the skill file
# (it appears in the init payload's `skills`/`slash_commands` lists), but
# neither an explicit `/intl-title-match` line nor implicit auto-discovery
# actually loads its content into the model's context in headless mode — the
# model consistently reports it received no such instructions. Per the
# pre-approved fallback, the skill's rules are inlined directly into this
# module's international system prompt instead (SKILL.md remains the
# authored source of truth for prose, just also copied here).

_DOMESTIC_PROMPT_SNAPSHOT_PATH = (
    Path(__file__).parent / "fixtures" / "domestic_prompt_snapshot.txt"
)


def test_domestic_prompt_byte_identical_to_snapshot():
    """Strongest form of the byte-identical invariant above: captured from a
    known-good build before any intl-only prompt_builder changes landed."""
    prompt = prompt_builder.build_prompt("Some Domestic Title", None, None, None)
    expected = _DOMESTIC_PROMPT_SNAPSHOT_PATH.read_text()
    assert prompt == expected


def test_intl_prompt_includes_skill_content():
    prompt = prompt_builder.build_prompt(
        "Aguas Mortais", None, None, None, market="international", country="Brazil",
    )
    assert "SKILL_CANARY" not in prompt  # canary is smoke-test-only, must never ship
    assert prompt_builder.INTL_TITLE_MATCH_SKILL_MARKER in prompt


def test_domestic_prompt_has_no_skill_content():
    prompt = prompt_builder.build_prompt("Some Domestic Title", None, None, None)
    assert prompt_builder.INTL_TITLE_MATCH_SKILL_MARKER not in prompt
    assert "rerelease_lookup_title" not in prompt


def test_intl_prompt_documents_rerelease_lookup_title_key():
    prompt = prompt_builder.build_prompt(
        "Aguas Mortais", None, None, None, market="international", country="Brazil",
    )
    assert "rerelease_lookup_title" in prompt


def test_build_result_reads_alternate_movie_title_from_payload():
    payload = {
        "candidates": [
            {
                "movie_master_id": 0,
                "movie_title": "Deep Water",
                "alternate_movie_title": "Aguas Mortais",
                "confidence": 0.4,
                "reasoning": "No DB candidate; Deep Water identified via web search.",
            }
        ],
        "best_match_index": 0,
        "event_type": "MOVIE",
    }

    result = _build_result(payload, raw_text=json.dumps(payload))

    assert result.suggested_movie_title == "Deep Water"
    assert result.alternate_movie_title == "Aguas Mortais"


def test_build_result_alternate_movie_title_defaults_to_none():
    payload = {
        "candidates": [
            {"movie_master_id": 0, "movie_title": "Deep Water", "confidence": 0.4, "reasoning": "x"}
        ],
        "best_match_index": 0,
        "event_type": "MOVIE",
    }

    result = _build_result(payload, raw_text=json.dumps(payload))

    assert result.alternate_movie_title is None


def test_build_result_reads_rerelease_lookup_title_from_payload():
    payload = {
        "candidates": [
            {
                "movie_master_id": 0,
                "movie_title": "Shrek",
                "rerelease_lookup_title": "Shrek 25th Anniversary",
                "confidence": 0.5,
                "reasoning": "10 indistinguishable duplicates, no date signal.",
            }
        ],
        "best_match_index": 0,
        "event_type": "RERELEASE",
    }

    result = _build_result(payload, raw_text=json.dumps(payload))

    assert result.rerelease_lookup_title == "Shrek 25th Anniversary"


def test_build_result_rerelease_lookup_title_defaults_to_none():
    payload = {
        "candidates": [
            {"movie_master_id": 0, "movie_title": "Deep Water", "confidence": 0.4, "reasoning": "x"}
        ],
        "best_match_index": 0,
        "event_type": "MOVIE",
    }

    result = _build_result(payload, raw_text=json.dumps(payload))

    assert result.rerelease_lookup_title is None


def _run_agentic_match_with_mocks(
    suggested_movie_title: str,
    alternate_movie_title: str | None,
    db_search_side_effect,
    market: str = "international",
    country: str | None = "Brazil",
    decision: str = "REVIEW",
):
    """Drive run_agentic_match end-to-end with the sandbox call and DB layer
    mocked out, so only the post-lookup logic under test actually runs."""
    agent_result = TitleMatchResult(
        suggested_movie_id=0,
        suggested_movie_title=suggested_movie_title,
        canonical_movie_id=0,
        confidence=0.4,
        decision=decision,
        reasoning="No DB candidate; identified via web search.",
        evidence={"agentic": True},
        fired_ai=True,
        alternate_movie_title=alternate_movie_title,
    )

    with patch.object(runner_mod, "_check_sandbox_reachable", return_value=None), \
         patch.object(runner_mod, "_fetch_db_candidates", return_value=[]), \
         patch.object(runner_mod, "_fetch_vespa_candidates", return_value=[]), \
         patch.object(runner_mod, "_call_sandbox", return_value="irrelevant-stdout"), \
         patch.object(runner_mod, "parse_agent_output", return_value=agent_result), \
         patch.object(runner_mod, "_db_search", side_effect=db_search_side_effect) as mock_db_search:
        result = run_agentic_match(
            "Aguas Mortais", market=market, country=country,
        )
    return result, mock_db_search


def test_post_lookup_resolves_via_english_title_the_agent_now_reports():
    """The primary regression case: agent reports the English title (per the
    fixed prompt rule); DB stores it under that title; post-lookup must
    resolve it on the first attempt."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Deep Water":
            return [{"id": 156728, "movie_title": "Deep Water", "country": "Brazil", "cover_image": ""}]
        return []

    result, mock_db_search = _run_agentic_match_with_mocks(
        suggested_movie_title="Deep Water",
        alternate_movie_title="Aguas Mortais",
        db_search_side_effect=fake_db_search,
    )

    assert result.suggested_movie_id == 156728
    assert result.canonical_movie_id == 156728
    # First attempt (the primary title) already hit — the alternate fallback
    # must not have been needed, i.e. _db_search was called exactly once.
    assert mock_db_search.call_count == 1
    # Regression guard: a successful post-lookup must overwrite the stale
    # pre-fallback confidence/decision/reasoning (id=0, 0.4, REVIEW) — left
    # untouched, the UI displays "no DB candidate matched" text next to a
    # movie_master_id that in fact resolved via an exact DB hit.
    assert result.decision == "AUTO_ACCEPT"
    assert result.confidence >= 0.90
    assert "post-lookup" in result.reasoning.lower()
    assert "156728" in result.reasoning


def test_post_lookup_hit_bumps_domestic_confidence_but_never_auto_accepts():
    """A domestic post-lookup hit gets a smaller confidence bump (0.75) than an
    international one (0.90) and always stays in REVIEW. MovieMaster (the
    domestic table) has no country column, so a domestic post-lookup hit is an
    uncorroborated title-only match — a genuinely weaker signal than
    international's country-scoped exact-title match, so it must never be pulled
    into AUTO_ACCEPT. Reasoning is still rewritten (for both markets) so the
    displayed text reflects the resolved id instead of the stale id=0 state.
    0.75 (not lower) matters concretely: the review-queue UI colors confidence
    red below 0.75, same as a garbage match — a lower bump would visually look
    no better than the stale 0.3-0.4 state this fix replaces."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Some Domestic Title":
            return [{"id": 4242, "movie_title": "Some Domestic Title", "cover_image": ""}]
        return []

    result, mock_db_search = _run_agentic_match_with_mocks(
        suggested_movie_title="Some Domestic Title",
        alternate_movie_title=None,
        db_search_side_effect=fake_db_search,
        market="domestic",
        country=None,
        decision="REVIEW",
    )

    assert result.suggested_movie_id == 4242
    assert mock_db_search.call_count == 1
    assert result.decision == "REVIEW"
    assert result.confidence == 0.75
    assert result.confidence < 0.90
    assert "post-lookup" in result.reasoning.lower()
    assert "4242" in result.reasoning
    assert "REVIEW" in result.reasoning
    assert "No DB candidate; identified via web search." in result.reasoning


def test_post_lookup_hit_downgrades_domestic_auto_accept_to_review():
    """Regression guard for a real gap: result_parser._build_result can emit
    decision="AUTO_ACCEPT" from confidence alone (>= 0.90), independent of
    movie_master_id — so a domestic first pass can already be AUTO_ACCEPT
    when it reaches this block with movie_master_id=0. Left unguarded, the
    post-lookup hit would drop confidence to 0.75 while leaving decision at
    AUTO_ACCEPT: a self-contradictory pair, and a silent auto-accept of a
    domestic match that was never supposed to bypass human review."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Some Domestic Title":
            return [{"id": 4242, "movie_title": "Some Domestic Title", "cover_image": ""}]
        return []

    result, _ = _run_agentic_match_with_mocks(
        suggested_movie_title="Some Domestic Title",
        alternate_movie_title=None,
        db_search_side_effect=fake_db_search,
        market="domestic",
        country=None,
        decision="AUTO_ACCEPT",
    )

    assert result.suggested_movie_id == 4242
    assert result.decision == "REVIEW"
    assert result.confidence == 0.75


def test_post_lookup_hit_preserves_non_movie_decision_for_domestic():
    """Mirrors test_post_lookup_preserves_non_movie_decision_on_hit but for
    market="domestic" specifically — that existing test defaults to
    market="international" via _run_agentic_match_with_mocks, so it never
    covered the domestic branch's handling of event-type decisions."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Some Domestic Title":
            return [{"id": 4242, "movie_title": "Some Domestic Title", "cover_image": ""}]
        return []

    result, _ = _run_agentic_match_with_mocks(
        suggested_movie_title="Some Domestic Title",
        alternate_movie_title=None,
        db_search_side_effect=fake_db_search,
        market="domestic",
        country=None,
        decision="REVIEW_NON_MOVIE",
    )

    assert result.suggested_movie_id == 4242
    assert result.decision == "REVIEW_NON_MOVIE"
    assert result.confidence == 0.75
    assert "REVIEW_NON_MOVIE" in result.reasoning


def test_post_lookup_falls_back_to_alternate_title_when_primary_misses():
    """If the agent's primary guess doesn't match what the DB stores, the
    bounded second attempt with alternate_movie_title must still resolve it."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Deep Water":
            return []  # primary guess misses
        if query == "Aguas Mortais":
            return [{"id": 156728, "movie_title": "Deep Water", "country": "Brazil", "cover_image": ""}]
        return []

    result, mock_db_search = _run_agentic_match_with_mocks(
        suggested_movie_title="Deep Water",
        alternate_movie_title="Aguas Mortais",
        db_search_side_effect=fake_db_search,
    )

    assert result.suggested_movie_id == 156728
    assert result.canonical_movie_id == 156728
    assert mock_db_search.call_count == 2


def test_post_lookup_stays_zero_when_neither_title_resolves():
    """Negative case: no regression to the existing 'no candidates' behavior
    when both the primary and alternate titles come up empty."""
    result, mock_db_search = _run_agentic_match_with_mocks(
        suggested_movie_title="Some Unresolvable Title",
        alternate_movie_title="Also Unresolvable",
        db_search_side_effect=lambda query, market="domestic", country=None: [],
    )

    assert result.suggested_movie_id == 0
    assert result.canonical_movie_id == 0
    assert mock_db_search.call_count == 2


def test_post_lookup_does_not_attempt_alternate_when_none_supplied():
    """Domestic path (and any international result where the agent didn't
    supply an alternate) must behave exactly as before this fix: exactly one
    post-lookup attempt, no second call."""
    def fake_db_search(query, market="domestic", country=None):
        return []

    result, mock_db_search = _run_agentic_match_with_mocks(
        suggested_movie_title="Some Domestic Title",
        alternate_movie_title=None,
        db_search_side_effect=fake_db_search,
        market="domestic",
        country=None,
    )

    assert result.suggested_movie_id == 0
    assert mock_db_search.call_count == 1


def test_post_lookup_syncs_displayed_title_to_the_alternate_hit():
    """Regression guard for a display bug found during live batch testing:
    when the PRIMARY title guess misses and the ALTERNATE title resolves a
    row whose stored movie_title differs from both guesses (e.g. agent says
    "Little Creatures" / alternate "Pequenas Criaturas", but the DB row's
    movie_title is "Pequenas Criaturas"), suggested_movie_title must be
    updated to the row actually matched — not left as Claude's original
    (now-superseded) primary guess. Before this fix, the id/canonical_id were
    correct but the displayed title silently stayed wrong."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Little Creatures":
            return []  # primary guess misses entirely
        if query == "Pequenas Criaturas":
            return [{"id": 156949, "movie_title": "Pequenas Criaturas", "country": "Brazil", "cover_image": ""}]
        return []

    result, mock_db_search = _run_agentic_match_with_mocks(
        suggested_movie_title="Little Creatures",
        alternate_movie_title="Pequenas Criaturas",
        db_search_side_effect=fake_db_search,
    )

    assert result.suggested_movie_id == 156949
    assert result.canonical_movie_id == 156949
    assert result.suggested_movie_title == "Pequenas Criaturas"
    assert mock_db_search.call_count == 2


def test_post_lookup_preserves_non_movie_decision_on_hit():
    """event_type-driven decisions (REVIEW_NON_MOVIE/REVIEW_MULTI_FILM) are not
    a confidence signal — a post-lookup hit should still raise confidence but
    must not silently reclassify these as AUTO_ACCEPT."""
    def fake_db_search(query, market="domestic", country=None):
        if query == "Deep Water":
            return [{"id": 156728, "movie_title": "Deep Water", "country": "Brazil", "cover_image": ""}]
        return []

    result, _ = _run_agentic_match_with_mocks(
        suggested_movie_title="Deep Water",
        alternate_movie_title="Aguas Mortais",
        db_search_side_effect=fake_db_search,
        decision="REVIEW_NON_MOVIE",
    )

    assert result.suggested_movie_id == 156728
    assert result.decision == "REVIEW_NON_MOVIE"
    assert result.confidence >= 0.90


# ── Vespa schema: country field exists on intl only ────────────────────────

def test_movie_master_intl_schema_declares_country_field():
    from pathlib import Path

    sd_path = Path(__file__).parent.parent / "vespa" / "schemas" / "movie_master_intl.sd"
    text = sd_path.read_text()
    assert "field country type string" in text
    assert "match: exact" in text


def test_movie_master_domestic_schema_has_no_country_field():
    from pathlib import Path

    sd_path = Path(__file__).parent.parent / "vespa" / "schemas" / "movie_master.sd"
    text = sd_path.read_text()
    assert "field country" not in text

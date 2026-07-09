"""
Tests for the semantic (embedding-based) retrieval module.

Mocks all external dependencies (Bedrock, Vespa) so tests run fully offline.

Run with:
    pytest backend/tests/test_semantic_index.py -v -s
"""

from __future__ import annotations

import json
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides):
    """Settings-like object with semantic defaults."""
    defaults = {
        "BEDROCK_REGION": "us-east-1",
        "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
        "EMBEDDING_DIMENSION": 16,  # tiny for fast tests
        "SEMANTIC_SEARCH_ENABLED": True,
        "VESPA_URL": "http://localhost:8080",
    }
    defaults.update(overrides)

    class _S:
        def __getattr__(self, name):
            return defaults[name]

    return _S()


def _fake_vec(dim: int = 16, seed: int = 0) -> list[float]:
    """Deterministic unit vector."""
    import math
    vals = [(seed * 7 + i * 3) % 100 / 100.0 for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


def _mock_bedrock_client(embeddings: list[Optional[list[float]]]):
    """Fake boto3 client that returns embeddings in order."""
    call_count = [0]
    client = MagicMock()
    client.exceptions = MagicMock()
    client.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})

    def _invoke(**kwargs):
        idx = call_count[0]
        call_count[0] += 1
        emb = embeddings[idx] if idx < len(embeddings) else _fake_vec()
        body = MagicMock()
        body.read.return_value = json.dumps({"embedding": emb}).encode()
        return {"body": body}

    client.invoke_model.side_effect = _invoke
    return client


def _mock_vespa_app(hits: list[dict]):
    """Fake pyvespa Vespa application that returns given hits."""
    app = MagicMock()
    result = MagicMock()
    result.hits = hits
    app.query.return_value = result
    return app


# ---------------------------------------------------------------------------
# TestComposeEmbedText
# ---------------------------------------------------------------------------

class TestComposeEmbedText:

    def test_title_only(self):
        from app.title_matching.semantic_index import _compose_embed_text
        row = {"movie_title": "Inception", "release_date": None, "director": None}
        assert _compose_embed_text(row) == "Inception"

    def test_title_year_director(self):
        from app.title_matching.semantic_index import _compose_embed_text
        row = {"movie_title": "Inception", "release_date": "2010-07-16", "director": "Christopher Nolan"}
        assert _compose_embed_text(row) == "Inception 2010 Christopher Nolan"

    def test_title_year_no_director(self):
        from app.title_matching.semantic_index import _compose_embed_text
        row = {"movie_title": "Moana", "release_date": "2016-11-23", "director": None}
        assert _compose_embed_text(row) == "Moana 2016"

    def test_sentinel_year_excluded(self):
        from app.title_matching.semantic_index import _compose_embed_text
        row = {"movie_title": "Unknown", "release_date": "0000-00-00", "director": None}
        assert _compose_embed_text(row) == "Unknown"

    def test_missing_release_date_key(self):
        from app.title_matching.semantic_index import _compose_embed_text
        row = {"movie_title": "No Date"}
        assert _compose_embed_text(row) == "No Date"


# ---------------------------------------------------------------------------
# TestGetEmbedding
# ---------------------------------------------------------------------------

class TestGetEmbedding:

    def test_returns_embedding_on_success(self):
        dim = 16
        expected = _fake_vec(dim, seed=5)
        client = _mock_bedrock_client([expected])
        settings = _make_settings(EMBEDDING_DIMENSION=dim)

        from app.title_matching.semantic_index import get_embedding
        result = get_embedding("Love Island Season Finale", settings, client=client)

        assert result is not None
        assert len(result) == dim

    def test_returns_none_when_no_client(self):
        settings = _make_settings()
        with patch("app.title_matching.semantic_index._get_bedrock_client", return_value=None):
            from app.title_matching.semantic_index import get_embedding
            result = get_embedding("test", settings)
        assert result is None

    def test_returns_none_on_exception(self):
        client = MagicMock()
        client.exceptions = MagicMock()
        client.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
        client.invoke_model.side_effect = Exception("boom")
        settings = _make_settings()

        from app.title_matching.semantic_index import get_embedding
        result = get_embedding("test", settings, client=client)
        assert result is None

    def test_retries_on_throttle_then_succeeds(self):
        dim = 16
        expected = _fake_vec(dim, seed=7)
        call_count = [0]
        client = MagicMock()
        ThrottleExc = type("ThrottlingException", (Exception,), {})
        client.exceptions = MagicMock()
        client.exceptions.ThrottlingException = ThrottleExc

        def _invoke(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise ThrottleExc("rate exceeded")
            body = MagicMock()
            body.read.return_value = json.dumps({"embedding": expected}).encode()
            return {"body": body}

        client.invoke_model.side_effect = _invoke
        settings = _make_settings(EMBEDDING_DIMENSION=dim)

        from app.title_matching.semantic_index import get_embedding
        with patch("time.sleep"):  # skip actual sleep
            result = get_embedding("test", settings, client=client)

        assert result is not None
        assert call_count[0] == 3  # 2 throttled + 1 success


# ---------------------------------------------------------------------------
# TestVespaSemanticIndexSearch
# ---------------------------------------------------------------------------

class TestVespaSemanticIndexSearch:

    @pytest.fixture
    def index(self):
        from app.title_matching.semantic_index import VespaSemanticIndex
        idx = VespaSemanticIndex.__new__(VespaSemanticIndex)
        idx._settings = _make_settings()
        return idx

    def test_returns_results(self, index):
        hits = [
            {"fields": {"movie_master_id": 147057}, "relevance": 0.9},
            {"fields": {"movie_master_id": 1}, "relevance": 0.7},
        ]
        index._app = _mock_vespa_app(hits)

        results = index.search(
            query_embedding=_fake_vec(),
            query_text="Love Island Season Finale",
            k=5,
        )

        assert len(results) == 2
        assert results[0][0] == 147057

    def test_excludes_already_found_ids(self, index):
        hits = [
            {"fields": {"movie_master_id": 147057}, "relevance": 0.9},
            {"fields": {"movie_master_id": 1}, "relevance": 0.7},
        ]
        index._app = _mock_vespa_app(hits)

        results = index.search(
            query_embedding=_fake_vec(),
            query_text="Love Island Season Finale",
            k=5,
            exclude_ids={147057},
        )

        ids = [r[0] for r in results]
        assert 147057 not in ids
        assert 1 in ids

    def test_scores_scaled_to_0_06(self, index):
        hits = [{"fields": {"movie_master_id": 1}, "relevance": 1.0}]
        index._app = _mock_vespa_app(hits)

        results = index.search(query_embedding=_fake_vec(), query_text="test", k=5)

        assert len(results) == 1
        score = results[0][1]
        assert 0.0 <= score <= 0.6

    def test_respects_k_limit(self, index):
        hits = [{"fields": {"movie_master_id": i}, "relevance": 0.5} for i in range(20)]
        index._app = _mock_vespa_app(hits)

        results = index.search(query_embedding=_fake_vec(), query_text="test", k=3)
        assert len(results) <= 3

    def test_returns_empty_on_vespa_error(self, index):
        index._app = MagicMock()
        index._app.query.side_effect = Exception("connection refused")

        results = index.search(query_embedding=_fake_vec(), query_text="test", k=5)
        assert results == []

    def test_returns_empty_on_no_hits(self, index):
        index._app = _mock_vespa_app([])

        results = index.search(query_embedding=_fake_vec(), query_text="test", k=5)
        assert results == []


# ---------------------------------------------------------------------------
# TestBuildSemanticIndex
# ---------------------------------------------------------------------------

class TestBuildSemanticIndex:

    @pytest.fixture
    def master_rows(self):
        return [
            {"id": 1, "movie_title": "Inception", "release_date": "2010-07-16", "director": "Christopher Nolan"},
            {"id": 147057, "movie_title": "Peacock's Love Island USA Theatrical Event", "release_date": "2024-08-19", "director": None},
            {"id": 3, "movie_title": "Interstellar", "release_date": "2014-11-07", "director": "Christopher Nolan"},
        ]

    def test_returns_none_when_disabled(self, master_rows):
        settings = _make_settings(SEMANTIC_SEARCH_ENABLED=False)

        from app.title_matching.semantic_index import build_semantic_index
        result = build_semantic_index(master_rows, settings)
        assert result is None

    def test_returns_none_when_vespa_deploy_fails(self, master_rows):
        settings = _make_settings()

        with patch("app.title_matching.semantic_index._deploy_vespa_app", return_value=False):
            from app.title_matching.semantic_index import build_semantic_index
            result = build_semantic_index(master_rows, settings)
        assert result is None

    def test_returns_index_when_already_fully_fed(self, master_rows):
        settings = _make_settings()

        with patch("app.title_matching.semantic_index._deploy_vespa_app", return_value=True), \
             patch("app.title_matching.semantic_index._count_indexed_docs", return_value=len(master_rows)), \
             patch("app.title_matching.semantic_index.VespaSemanticIndex") as MockIndex:
            MockIndex.return_value = MagicMock()

            from app.title_matching.semantic_index import build_semantic_index
            result = build_semantic_index(master_rows, settings)

        assert result is not None
        # No embedding should have been called since count == len(master_rows)
        MockIndex.assert_called_once()

    def test_feeds_missing_rows_when_partially_indexed(self, master_rows):
        settings = _make_settings(EMBEDDING_DIMENSION=16)
        dim = 16
        embeddings = [_fake_vec(dim, seed=i) for i in range(len(master_rows))]
        bedrock_client = _mock_bedrock_client(embeddings)

        fed_rows = []

        def _fake_feed(vespa_url, rows, embs):
            fed_rows.extend(rows)
            return len(rows)

        with patch("app.title_matching.semantic_index._deploy_vespa_app", return_value=True), \
             patch("app.title_matching.semantic_index._count_indexed_docs", return_value=1), \
             patch("app.title_matching.semantic_index._get_bedrock_client", return_value=bedrock_client), \
             patch("app.title_matching.semantic_index._feed_rows", side_effect=_fake_feed), \
             patch("app.title_matching.semantic_index.VespaSemanticIndex"):
            from app.title_matching.semantic_index import build_semantic_index
            build_semantic_index(master_rows, settings)

        # Should have fed the 2 missing rows (index had 1, total is 3)
        assert len(fed_rows) == 2

    def test_returns_partial_index_when_no_bedrock_but_some_docs(self, master_rows):
        settings = _make_settings()

        with patch("app.title_matching.semantic_index._deploy_vespa_app", return_value=True), \
             patch("app.title_matching.semantic_index._count_indexed_docs", return_value=2), \
             patch("app.title_matching.semantic_index._get_bedrock_client", return_value=None), \
             patch("app.title_matching.semantic_index.VespaSemanticIndex") as MockIndex:
            MockIndex.return_value = MagicMock()

            from app.title_matching.semantic_index import build_semantic_index
            result = build_semantic_index(master_rows, settings)

        # Returns index (partial) even without Bedrock since some docs exist
        assert result is not None

    def test_returns_none_when_no_bedrock_and_no_docs(self, master_rows):
        settings = _make_settings()

        with patch("app.title_matching.semantic_index._deploy_vespa_app", return_value=True), \
             patch("app.title_matching.semantic_index._count_indexed_docs", return_value=0), \
             patch("app.title_matching.semantic_index._get_bedrock_client", return_value=None):
            from app.title_matching.semantic_index import build_semantic_index
            result = build_semantic_index(master_rows, settings)

        assert result is None


# ---------------------------------------------------------------------------
# TestCandidateGeneratorSemanticIntegration
# ---------------------------------------------------------------------------

class TestCandidateGeneratorSemanticIntegration:
    """Integration: CandidateGenerator uses semantic index correctly."""

    @pytest.fixture
    def master_rows(self):
        return [
            {"id": 1, "movie_title": "The Dark Knight", "release_date": "2008-07-18", "cover_image": None, "parent_id": None},
            {"id": 147057, "movie_title": "Peacock's Love Island USA Theatrical Event", "release_date": "2024-08-19", "cover_image": None, "parent_id": None},
        ]

    def test_semantic_candidate_surfaced(self, master_rows):
        """Love Island Season Finale should surface id=147057 via semantic."""
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        mock_index = MagicMock()
        mock_index.search.return_value = [(147057, 0.45)]

        gen = CandidateGenerator(master_rows, semantic_index=mock_index)
        normalized = normalize_title("Love Island Season Finale")

        with patch("app.title_matching.semantic_index.get_embedding", return_value=_fake_vec()):
            results = gen.generate(normalized, {})

        ids = [c.movie_master_id for c in results]
        assert 147057 in ids
        semantic = [c for c in results if c.source == "semantic"]
        assert len(semantic) >= 1
        assert semantic[0].score == pytest.approx(0.45)

    def test_no_semantic_when_index_is_none(self, master_rows):
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        gen = CandidateGenerator(master_rows, semantic_index=None)
        normalized = normalize_title("Love Island Season Finale")
        results = gen.generate(normalized, {})

        semantic = [c for c in results if c.source == "semantic"]
        assert semantic == []

    def test_semantic_does_not_duplicate_fuzzy_hits(self, master_rows):
        """If fuzzy already found id=1, semantic should not add it again."""
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        # Mock index that tries to return id=1 (already found by fuzzy)
        mock_index = MagicMock()
        mock_index.search.return_value = [(1, 0.5), (147057, 0.4)]

        gen = CandidateGenerator(master_rows, semantic_index=mock_index)
        normalized = normalize_title("The Dark Knight")

        with patch("app.title_matching.semantic_index.get_embedding", return_value=_fake_vec()):
            results = gen.generate(normalized, {})

        # id=1 should appear exactly once
        count_1 = sum(1 for c in results if c.movie_master_id == 1)
        assert count_1 == 1

    def test_semantic_fills_remaining_slots(self, master_rows):
        """Semantic only fills up to k total candidates."""
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        mock_index = MagicMock()
        # Return many semantic hits
        mock_index.search.return_value = [(147057, 0.4)]

        gen = CandidateGenerator(master_rows, semantic_index=mock_index)
        normalized = normalize_title("Love Island Season Finale")

        with patch("app.title_matching.semantic_index.get_embedding", return_value=_fake_vec()):
            results = gen.generate(normalized, {}, k=5)

        assert len(results) <= 5

    def test_semantic_skipped_when_embedding_fails(self, master_rows):
        """If get_embedding returns None, semantic search is skipped gracefully."""
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        mock_index = MagicMock()
        gen = CandidateGenerator(master_rows, semantic_index=mock_index)
        normalized = normalize_title("Love Island Season Finale")

        with patch("app.title_matching.semantic_index.get_embedding", return_value=None):
            results = gen.generate(normalized, {})

        semantic = [c for c in results if c.source == "semantic"]
        assert semantic == []
        # Vespa search should not have been called
        mock_index.search.assert_not_called()

"""
End-to-end smoke test for semantic retrieval via Vespa.

Tests the full pipeline:
  - Deploy Vespa app package
  - Feed a small set of master rows (including id=147057 Love Island)
  - Embed query via Bedrock (or mock if no AWS creds)
  - Confirm semantic search surfaces id=147057 for "Love Island Season Finale"
  - Contrast against fuzzy-only baseline (which gets it wrong)

Run with:
    pytest backend/tests/test_semantic_e2e.py -v -s

Requires Vespa container running on localhost:8080.
Bedrock is mocked so no AWS credentials are needed.
"""

from __future__ import annotations

import json
import math
import time
from unittest.mock import MagicMock, patch

import pytest

VESPA_URL = "http://localhost:8080"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_vec(dim: int, seed: int) -> list[float]:
    """Deterministic unit vector for a given seed."""
    vals = [(seed * 7 + i * 3) % 100 / 100.0 for i in range(dim)]
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


def _mock_bedrock_with_vecs(vecs: list[list[float]]):
    """Return a boto3 mock that dispenses vecs in order."""
    call_count = [0]
    client = MagicMock()
    client.exceptions = MagicMock()
    client.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})

    def _invoke(**kwargs):
        idx = call_count[0]
        call_count[0] += 1
        emb = vecs[idx % len(vecs)]
        body = MagicMock()
        body.read.return_value = json.dumps({"embedding": emb}).encode()
        return {"body": body}

    client.invoke_model.side_effect = _invoke
    return client


def _vespa_available() -> bool:
    try:
        import requests
        r = requests.get(f"{VESPA_URL}/state/v1/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vespa_app():
    """Deploy the Vespa app package and return a pyvespa Vespa instance."""
    from vespa.application import Vespa
    from app.title_matching.semantic_index import _deploy_vespa_app

    if not _vespa_available():
        pytest.skip("Vespa not running on localhost:8080")

    deployed = _deploy_vespa_app(VESPA_URL)
    assert deployed, "Vespa app deploy failed"

    # Give Vespa a moment after deployment before feeding
    time.sleep(3)
    return Vespa(url=VESPA_URL)


@pytest.fixture(scope="module")
def dim():
    return 1024  # Must match schema (embedding field dim)


@pytest.fixture(scope="module")
def test_rows():
    """Small corpus including the canonical Love Island test case."""
    return [
        {"id": 1,      "movie_title": "The Dark Knight",                         "release_date": "2008-07-18", "director": "Christopher Nolan",   "cover_image": None, "parent_id": None},
        {"id": 2,      "movie_title": "Inception",                               "release_date": "2010-07-16", "director": "Christopher Nolan",   "cover_image": None, "parent_id": None},
        {"id": 4,      "movie_title": "Moana",                                   "release_date": "2016-11-23", "director": "John Musker",          "cover_image": None, "parent_id": None},
        {"id": 137221, "movie_title": "Moana",                                   "release_date": "2026-07-10", "director": "Thomas Kail",          "cover_image": None, "parent_id": None},
        {"id": 147057, "movie_title": "Peacock's Love Island USA Theatrical Event", "release_date": "2024-08-19", "director": None,               "cover_image": None, "parent_id": None},
        {"id": 99999,  "movie_title": "Love",                                    "release_date": "2015-10-28", "director": "Gaspar Noe",           "cover_image": None, "parent_id": None},
    ]


@pytest.fixture(scope="module")
def fed_index(vespa_app, test_rows, dim):
    """Feed test rows into Vespa with mock Bedrock embeddings and return index."""
    from app.title_matching.semantic_index import VespaSemanticIndex, _feed_rows, _compose_embed_text

    # Assign embeddings: Love Island (id=147057) gets seed=147 so its vector
    # is clearly distinct, and our "Love Island Season Finale" query will use
    # the SAME seed to simulate a matching embedding.
    seed_map = {
        1: 1, 2: 2, 4: 4, 137221: 37, 147057: 147, 99999: 99
    }
    embeddings = [_unit_vec(dim, seed_map[r["id"]]) for r in test_rows]

    # Clear existing docs first
    try:
        import requests
        requests.delete(f"{VESPA_URL}/document/v1/movie_master/movie_master/docid/", timeout=5)
    except Exception:
        pass

    fed = _feed_rows(VESPA_URL, test_rows, embeddings)
    assert fed > 0, f"No rows fed to Vespa — fed={fed}"

    # Allow index to settle
    time.sleep(2)

    settings_mock = MagicMock()
    settings_mock.VESPA_URL = VESPA_URL
    return VespaSemanticIndex(vespa_url=VESPA_URL, settings=settings_mock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSemanticRetrievalE2E:

    def test_vespa_is_healthy(self):
        if not _vespa_available():
            pytest.skip("Vespa not running")
        import requests
        r = requests.get(f"{VESPA_URL}/state/v1/health", timeout=5)
        assert r.status_code == 200
        print("\n[PASS] Vespa health check OK")

    def test_love_island_surfaced_by_semantic(self, fed_index, dim):
        """
        KEY TEST: 'Love Island Season Finale' must surface id=147057.

        Fuzzy alone returns id=99999 'Love' (wrong).
        Semantic search with the same embedding as id=147057 must return it.
        """
        # Query embedding = same seed as id=147057 → should be nearest neighbour
        query_embedding = _unit_vec(dim, 147)

        results = fed_index.search(
            query_embedding=query_embedding,
            query_text="Love Island Season Finale",
            k=5,
        )

        ids = [r[0] for r in results]
        scores = {r[0]: r[1] for r in results}

        print(f"\n[RESULT] Semantic hits: {[(r[0], round(r[1], 3)) for r in results]}")

        assert 147057 in ids, (
            f"id=147057 'Peacock's Love Island USA Theatrical Event' NOT in results.\n"
            f"Got: {ids}\n"
            f"This is the canonical semantic retrieval failure case from the strategy doc."
        )
        print(f"[PASS] id=147057 found with score={scores[147057]:.3f}")

    def test_love_island_ranked_above_fuzzy_baseline(self, fed_index, dim):
        """
        id=147057 should rank above id=99999 'Love' (the wrong fuzzy answer).
        """
        query_embedding = _unit_vec(dim, 147)
        results = fed_index.search(
            query_embedding=query_embedding,
            query_text="Love Island Season Finale",
            k=5,
        )
        id_to_score = {r[0]: r[1] for r in results}

        if 147057 in id_to_score and 99999 in id_to_score:
            assert id_to_score[147057] >= id_to_score[99999], (
                f"id=147057 score {id_to_score[147057]:.3f} should be >= "
                f"id=99999 'Love' score {id_to_score[99999]:.3f}"
            )
            print(f"[PASS] id=147057 ({id_to_score[147057]:.3f}) > id=99999 ({id_to_score[99999]:.3f})")
        elif 147057 in id_to_score:
            print(f"[PASS] id=147057 found, id=99999 'Love' not in top results")

    def test_fuzzy_baseline_gets_it_wrong(self, test_rows):
        """
        Show the baseline: fuzzy-only returns 'Love' (id=99999), NOT id=147057.
        This documents what we're fixing.
        """
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        gen = CandidateGenerator(test_rows, semantic_index=None)
        normalized = normalize_title("Love Island Season Finale")
        results = gen.generate(normalized, {})

        ids = [c.movie_master_id for c in results]
        top_id = results[0].movie_master_id if results else None
        print(f"\n[BASELINE] Fuzzy-only top candidates: {ids}")
        print(f"[BASELINE] Top result: id={top_id} (expected 147057, fuzzy gets it wrong)")

        assert 147057 not in ids[:3], (
            "Fuzzy already finds id=147057 in top 3 — semantic retrieval baseline check invalid"
        )
        print("[PASS] Confirmed: fuzzy does NOT find id=147057 — semantic is needed")

    def test_score_in_pipeline_range(self, fed_index, dim):
        """Semantic scores must be in [0, 0.6] for pipeline compatibility."""
        query_embedding = _unit_vec(dim, 147)
        results = fed_index.search(
            query_embedding=query_embedding,
            query_text="Love Island Season Finale",
            k=5,
        )
        for mid, score in results:
            assert 0.0 <= score <= 0.6, f"Score {score:.3f} for id={mid} out of [0, 0.6]"
        print(f"[PASS] All scores in [0, 0.6]: {[(r[0], round(r[1],3)) for r in results]}")

    def test_exclude_ids_respected(self, fed_index, dim):
        """Excluded IDs must not appear in results."""
        query_embedding = _unit_vec(dim, 147)
        results = fed_index.search(
            query_embedding=query_embedding,
            query_text="Love Island Season Finale",
            k=5,
            exclude_ids={147057},
        )
        ids = [r[0] for r in results]
        assert 147057 not in ids
        print(f"[PASS] exclude_ids respected, results: {ids}")

    def test_full_candidate_generator_with_semantic(self, test_rows, fed_index, dim):
        """
        Full CandidateGenerator integration: semantic fills remaining slots
        after fuzzy, and id=147057 appears with source='semantic'.
        """
        from app.title_matching.candidate_generator import CandidateGenerator
        from app.title_matching.normalizer import normalize_title

        settings_mock = MagicMock()
        settings_mock.VESPA_URL = VESPA_URL
        settings_mock.EMBEDDING_DIMENSION = dim
        settings_mock.EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
        settings_mock.BEDROCK_REGION = "us-east-1"

        query_vec = _unit_vec(dim, 147)
        bedrock_client = _mock_bedrock_with_vecs([query_vec])

        gen = CandidateGenerator(test_rows, semantic_index=fed_index)
        normalized = normalize_title("Love Island Season Finale")

        with patch("app.title_matching.semantic_index._get_bedrock_client", return_value=bedrock_client), \
             patch("app.title_matching.semantic_index.get_embedding", return_value=query_vec):
            results = gen.generate(normalized, {}, k=10)

        ids = [c.movie_master_id for c in results]
        sources = {c.movie_master_id: c.source for c in results}

        print(f"\n[RESULT] CandidateGenerator results:")
        for c in results:
            print(f"  id={c.movie_master_id:>7}  score={c.score:.3f}  source={c.source}  title={c.movie_title}")

        assert 147057 in ids, f"id=147057 not in candidates: {ids}"
        assert sources.get(147057) == "semantic", (
            f"id=147057 source should be 'semantic', got '{sources.get(147057)}'"
        )
        print(f"\n[PASS] id=147057 found with source='semantic'")

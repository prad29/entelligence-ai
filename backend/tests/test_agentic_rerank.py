"""
Tests for the Category-B independent verification pass
(app/title_matching/agentic/rerank.py).

Covers:
1. Pure merge logic (_merge_rerank) — CONFIRM/OVERRULE/NO_DB_MATCH, decision
   thresholds, event-type-decision preservation, invalid-id discarding.
2. verify_candidate_pick's Bedrock Converse call — mocked client, never a
   real network call.
3. The runner.py call site — market/flag/candidate-presence gating, and
   ordering relative to the id=0 post-lookup (rerelease_lookup_title first).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.title_matching.agentic import rerank as rerank_mod
from app.title_matching.agentic import runner as runner_mod
from app.title_matching.agentic.rerank import _merge_rerank, verify_candidate_pick
from app.title_matching.agentic.runner import run_agentic_match
from app.title_matching.types import TitleMatchResult


def _first_pass(
    suggested_movie_id: int = 25331,
    suggested_movie_title: str = "Odyssey",
    confidence: float = 0.85,
    decision: str = "REVIEW",
    reasoning: str = "Reasoning identifies The Odyssey but title emitted is Odyssey.",
) -> TitleMatchResult:
    return TitleMatchResult(
        suggested_movie_id=suggested_movie_id,
        suggested_movie_title=suggested_movie_title,
        canonical_movie_id=suggested_movie_id,
        confidence=confidence,
        decision=decision,
        reasoning=reasoning,
        evidence={"agentic": True},
        fired_ai=True,
    )


# ── _merge_rerank: pure logic, no network ───────────────────────────────────

class TestMergeRerank:

    def test_confirm_keeps_first_pass_id_and_title(self):
        first = _first_pass()
        verdict = {
            "verdict": "CONFIRM", "movie_master_id": 25331, "movie_title": "Odyssey",
            "confidence": 0.95, "reasoning": "Correct, well-supported.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0, 25331})

        assert merged.suggested_movie_id == 25331
        assert merged.suggested_movie_title == "Odyssey"
        assert merged.confidence == 0.95
        assert merged.decision == "AUTO_ACCEPT"

    def test_overrule_replaces_id_and_title(self):
        """Row-3 shape: first pass points at a weak placeholder-dated
        candidate; verification overrules with a different valid id."""
        first = _first_pass()
        verdict = {
            "verdict": "OVERRULE", "movie_master_id": 99999, "movie_title": "The Odyssey",
            "confidence": 0.92, "reasoning": "Placeholder date on 25331; 99999 fits better.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0, 25331, 99999})

        assert merged.suggested_movie_id == 99999
        assert merged.canonical_movie_id == 99999
        assert merged.suggested_movie_title == "The Odyssey"
        assert merged.confidence == 0.92
        assert merged.decision == "AUTO_ACCEPT"
        assert "OVERRULE" in merged.reasoning
        assert "The Odyssey but title emitted is Odyssey" in merged.reasoning  # first-pass reasoning preserved

    def test_no_db_match_zeroes_id_and_carries_titles_forward(self):
        """Row-12 shape: confident identification, no valid candidate id —
        the verdict's titles must still populate suggested_movie_title so the
        post-lookup has something to search."""
        first = _first_pass(suggested_movie_id=0, suggested_movie_title="", confidence=0.4)
        verdict = {
            "verdict": "NO_DB_MATCH", "movie_master_id": 0, "movie_title": "Madame",
            "alternate_movie_title": "Le Triangle d'or",
            "confidence": 0.5, "reasoning": "Confirmed film identity; no candidate fits.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0})

        assert merged.suggested_movie_id == 0
        assert merged.canonical_movie_id == 0
        assert merged.suggested_movie_title == "Madame"
        assert merged.alternate_movie_title == "Le Triangle d'or"

    def test_no_db_match_carries_rerelease_lookup_title_forward(self):
        first = _first_pass(suggested_movie_id=0, suggested_movie_title="Shrek", confidence=0.5)
        verdict = {
            "verdict": "NO_DB_MATCH", "movie_master_id": 0, "movie_title": "Shrek",
            "rerelease_lookup_title": "Shrek 25th Anniversary",
            "confidence": 0.5, "reasoning": "10 indistinguishable duplicates, no date signal.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0})

        assert merged.rerelease_lookup_title == "Shrek 25th Anniversary"

    def test_verdict_id_not_in_candidate_list_is_discarded(self):
        first = _first_pass()
        verdict = {
            "verdict": "OVERRULE", "movie_master_id": 424242, "movie_title": "Invented Row",
            "confidence": 0.99, "reasoning": "hallucinated id",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0, 25331})

        assert merged.suggested_movie_id == first.suggested_movie_id
        assert merged.suggested_movie_title == first.suggested_movie_title
        assert merged.confidence == first.confidence
        assert merged.evidence["rerank"]["discarded_reason"] == "verdict id not in candidate list"

    def test_confirm_preserves_non_movie_decision(self):
        """event_type-driven decisions must never be overwritten by a
        confidence-only change — mirrors the existing post-lookup invariant."""
        first = _first_pass(decision="REVIEW_NON_MOVIE")
        verdict = {
            "verdict": "CONFIRM", "movie_master_id": 25331, "movie_title": "Odyssey",
            "confidence": 0.95, "reasoning": "Confirmed.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0, 25331})

        assert merged.decision == "REVIEW_NON_MOVIE"

    def test_overrule_preserves_multi_film_decision(self):
        first = _first_pass(decision="REVIEW_MULTI_FILM")
        verdict = {
            "verdict": "OVERRULE", "movie_master_id": 99999, "movie_title": "Better Match",
            "confidence": 0.95, "reasoning": "Better fit.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0, 25331, 99999})

        assert merged.decision == "REVIEW_MULTI_FILM"

    def test_confidence_capped_at_0_97(self):
        first = _first_pass()
        verdict = {
            "verdict": "CONFIRM", "movie_master_id": 25331, "movie_title": "Odyssey",
            "confidence": 1.0, "reasoning": "Certain.",
        }
        merged = _merge_rerank(first, verdict, valid_ids={0, 25331})

        assert merged.confidence <= 0.97

    def test_merge_does_not_mutate_first_pass_result(self):
        first = _first_pass()
        verdict = {
            "verdict": "OVERRULE", "movie_master_id": 99999, "movie_title": "Different",
            "confidence": 0.9, "reasoning": "x",
        }
        _merge_rerank(first, verdict, valid_ids={0, 25331, 99999})

        assert first.suggested_movie_id == 25331
        assert first.suggested_movie_title == "Odyssey"


# ── verify_candidate_pick: Bedrock Converse call, mocked ───────────────────

class _FakeSettings:
    BEDROCK_REGION = "us-east-1"
    AGENTIC_CLAUDE_MODEL = "us.anthropic.claude-sonnet-5"
    AGENTIC_INTL_RERANK_ENABLED = True


def _fake_converse_response(verdict: dict) -> dict:
    return {
        "output": {
            "message": {
                "content": [
                    {"toolUse": {"name": "report_verdict", "input": verdict}}
                ]
            }
        }
    }


class TestVerifyCandidatePick:

    def test_calls_bedrock_converse_and_merges_verdict(self):
        first = _first_pass()
        verdict = {
            "verdict": "CONFIRM", "movie_master_id": 25331, "movie_title": "Odyssey",
            "confidence": 0.95, "reasoning": "Confirmed.",
        }
        client = MagicMock()
        client.converse.return_value = _fake_converse_response(verdict)

        with patch.object(rerank_mod, "_get_bedrock_client", return_value=client):
            result = verify_candidate_pick(
                "Die Odyssee", None, "Germany", first,
                db_candidates=[{"id": 25331, "movie_title": "Odyssey"}],
                vespa_candidates=[],
                settings=_FakeSettings(),
            )

        client.converse.assert_called_once()
        assert result.confidence == 0.95
        assert result.decision == "AUTO_ACCEPT"

    def test_falls_back_to_first_pass_when_client_creation_fails(self):
        first = _first_pass()

        with patch.object(rerank_mod, "_get_bedrock_client", return_value=None):
            result = verify_candidate_pick(
                "Die Odyssee", None, "Germany", first,
                db_candidates=[{"id": 25331}], vespa_candidates=[],
                settings=_FakeSettings(),
            )

        assert result is first

    def test_falls_back_to_first_pass_on_converse_exception(self):
        first = _first_pass()
        client = MagicMock()
        client.converse.side_effect = Exception("throttled")

        with patch.object(rerank_mod, "_get_bedrock_client", return_value=client):
            result = verify_candidate_pick(
                "Die Odyssee", None, "Germany", first,
                db_candidates=[{"id": 25331}], vespa_candidates=[],
                settings=_FakeSettings(),
            )

        assert result.suggested_movie_id == first.suggested_movie_id
        assert result.suggested_movie_title == first.suggested_movie_title

    def test_falls_back_to_first_pass_on_malformed_response(self):
        first = _first_pass()
        client = MagicMock()
        client.converse.return_value = {"output": {"message": {"content": []}}}

        with patch.object(rerank_mod, "_get_bedrock_client", return_value=client):
            result = verify_candidate_pick(
                "Die Odyssee", None, "Germany", first,
                db_candidates=[{"id": 25331}], vespa_candidates=[],
                settings=_FakeSettings(),
            )

        assert result.suggested_movie_id == first.suggested_movie_id


# ── runner.py call site: gating + ordering ──────────────────────────────────

def _run_with_rerank_mocks(
    first_pass_result: TitleMatchResult,
    db_candidates: list[dict],
    vespa_candidates: list[dict],
    market: str = "international",
    country: str | None = "Germany",
    rerank_enabled: bool = True,
    verify_side_effect=None,
    db_search_side_effect=None,
):
    with patch.object(runner_mod, "_check_sandbox_reachable", return_value=None), \
         patch.object(runner_mod, "_fetch_db_candidates", return_value=db_candidates), \
         patch.object(runner_mod, "_fetch_vespa_candidates", return_value=vespa_candidates), \
         patch.object(runner_mod, "_call_sandbox", return_value="irrelevant-stdout"), \
         patch.object(runner_mod, "parse_agent_output", return_value=first_pass_result), \
         patch.object(runner_mod.settings, "AGENTIC_INTL_RERANK_ENABLED", rerank_enabled), \
         patch.object(runner_mod, "verify_candidate_pick", side_effect=verify_side_effect) as mock_verify, \
         patch.object(runner_mod, "_db_search", side_effect=db_search_side_effect or (lambda *a, **k: [])):
        result = run_agentic_match("Die Odyssee", market=market, country=country)
    return result, mock_verify


class TestRunnerRerankCallSite:

    def test_calls_verify_for_international_with_candidates(self):
        first = _first_pass()
        _, mock_verify = _run_with_rerank_mocks(
            first, db_candidates=[{"id": 25331}], vespa_candidates=[],
            verify_side_effect=lambda *a, **k: first,
        )
        mock_verify.assert_called_once()

    def test_skipped_when_no_candidates_at_all(self):
        first = _first_pass(suggested_movie_id=0, suggested_movie_title="Unknown")
        _, mock_verify = _run_with_rerank_mocks(
            first, db_candidates=[], vespa_candidates=[],
            verify_side_effect=lambda *a, **k: first,
        )
        mock_verify.assert_not_called()

    def test_skipped_for_domestic_market(self):
        first = _first_pass()
        _, mock_verify = _run_with_rerank_mocks(
            first, db_candidates=[{"id": 25331}], vespa_candidates=[],
            market="domestic", country=None,
            verify_side_effect=lambda *a, **k: first,
        )
        mock_verify.assert_not_called()

    def test_skipped_when_flag_disabled(self):
        first = _first_pass()
        _, mock_verify = _run_with_rerank_mocks(
            first, db_candidates=[{"id": 25331}], vespa_candidates=[],
            rerank_enabled=False,
            verify_side_effect=lambda *a, **k: first,
        )
        mock_verify.assert_not_called()

    def test_no_db_match_verdict_feeds_post_lookup_titles(self):
        """Row-12 shape end-to-end: rerank overrules to NO_DB_MATCH with a
        populated title, and the post-lookup must search for THAT title."""
        first = _first_pass(suggested_movie_id=0, suggested_movie_title="", confidence=0.4)

        def fake_verify(title, show_date, country, result, db_c, vespa_c, settings):
            merged = _first_pass(suggested_movie_id=0, suggested_movie_title="Madame", confidence=0.5)
            merged.alternate_movie_title = "Le Triangle d'or"
            return merged

        def fake_db_search(query, market="domestic", country=None):
            if query == "Madame":
                return [{"id": 777, "movie_title": "Madame", "country": "France", "cover_image": ""}]
            return []

        result, mock_verify = _run_with_rerank_mocks(
            first, db_candidates=[{"id": 1}], vespa_candidates=[],
            verify_side_effect=fake_verify,
            db_search_side_effect=fake_db_search,
        )

        mock_verify.assert_called_once()
        assert result.suggested_movie_id == 777

    def test_post_lookup_tries_rerelease_lookup_title_before_suggested_title(self):
        first = _first_pass(suggested_movie_id=0, suggested_movie_title="Shrek", confidence=0.5)
        first.rerelease_lookup_title = "Shrek 25th Anniversary"

        queries_seen = []

        def fake_db_search(query, market="domestic", country=None):
            queries_seen.append(query)
            if query == "Shrek 25th Anniversary":
                return [{"id": 888, "movie_title": "Shrek 25th Anniversary", "country": "Germany", "cover_image": ""}]
            return []

        result, _ = _run_with_rerank_mocks(
            first, db_candidates=[], vespa_candidates=[],
            verify_side_effect=lambda *a, **k: first,
            db_search_side_effect=fake_db_search,
        )

        # Rerank was skipped (no candidates), so post-lookup ran directly on `first`.
        assert queries_seen[0] == "Shrek 25th Anniversary"
        assert result.suggested_movie_id == 888

    def test_rerelease_lookup_title_not_tried_for_domestic(self):
        """Domestic results never populate rerelease_lookup_title, but guard
        the branch explicitly: market="domestic" must never attempt it even
        if somehow set."""
        first = _first_pass(suggested_movie_id=0, suggested_movie_title="Some Domestic Title", confidence=0.4)
        first.rerelease_lookup_title = "Some Domestic Title Anniversary"

        queries_seen = []

        def fake_db_search(query, market="domestic", country=None):
            queries_seen.append(query)
            return []

        _run_with_rerank_mocks(
            first, db_candidates=[], vespa_candidates=[],
            market="domestic", country=None,
            verify_side_effect=lambda *a, **k: first,
            db_search_side_effect=fake_db_search,
        )

        assert "Some Domestic Title Anniversary" not in queries_seen
        assert queries_seen[0] == "Some Domestic Title"

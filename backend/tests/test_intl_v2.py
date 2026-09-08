"""Tests for the international agentic v2 work:
  - international v2 is a fully standalone orchestrator (runner_intl_v2.py)
    that never calls the shared run_agentic_match -- v1 (domestic and intl)
    stays byte-identical.
  - the country-consistency guardrail's deterministic predicate/fall-through.
  - the rerank verification pass's pure merge logic.
  - the two post-lookup confidence fixes (domestic v2, intl v2) are fully
    isolated from each other -- no shared symbol, no import edge.
  - the sandbox-target resolution and the intl v2 batch pipeline_variant seams.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import MovieTitleIntlBatchJob
from app.title_matching.agentic import AgenticConfigError
from app.title_matching.agentic.intl_guardrail_v2 import (
    apply_country_guardrail,
    candidate_passes_intl,
)
from app.title_matching.agentic.post_lookup_intl_v2 import (
    INTL_V2_POST_LOOKUP_CONFIDENCE,
    apply_intl_v2_post_lookup_resolution,
)
from app.title_matching.agentic.post_lookup_v2 import (
    DOMESTIC_V2_POST_LOOKUP_CONFIDENCE,
    apply_domestic_v2_post_lookup_resolution,
)
from app.title_matching.agentic.prompt_builder import build_prompt
from app.title_matching.agentic.prompt_builder_intl_v2 import build_prompt_intl_v2
from app.title_matching.agentic.rerank_intl_v2 import _merge_rerank
from app.title_matching.agentic.sandbox_target import sandbox_url_for
from app.title_matching.types import TitleMatchResult


def _result(movie_id, title, confidence=0.4, decision="REVIEW", evidence=None, **kwargs):
    return TitleMatchResult(
        suggested_movie_id=movie_id, suggested_movie_title=title, canonical_movie_id=movie_id,
        confidence=confidence, decision=decision, reasoning="model reasoning",
        evidence=evidence or {}, fired_ai=True, **kwargs,
    )


# ─── v1 stays untouched ───────────────────────────────────────────────────────

def test_v1_prompt_unaffected_by_intl_v2_wording():
    intl_prompt = build_prompt("some title", None, None, None, market="international", country="Brazil")
    domestic_prompt = build_prompt("some title", None, None, None, market="domestic")
    for prompt in (intl_prompt, domestic_prompt):
        for marker in ("rerelease_lookup_title", "Section A", "Step 2c",
                       "A studio anniversary is not the film's anniversary"):
            assert marker not in prompt


def test_run_agentic_match_rejects_v2_with_international_market():
    from app.title_matching.agentic import runner as runner_mod

    with pytest.raises(ValueError):
        runner_mod.run_agentic_match("Some Title", market="international", variant="v2")


# ─── prompt_builder_intl_v2 content ──────────────────────────────────────────

def test_intl_v2_prompt_has_anniversary_and_country_rules():
    prompt = build_prompt_intl_v2("Some Title", None, None, None, "Brazil")
    for marker in (
        "A = year(show_date)", "A studio anniversary is not the film's anniversary",
        "rerelease_lookup_title", "been filtered to the request's",
        "genre / genre2 must be consistent",
    ):
        assert marker in prompt


def test_intl_v2_prompt_never_claims_director_or_synopsis_checked():
    prompt = build_prompt_intl_v2("Some Title", None, None, None, "Brazil")
    assert "NO director, cast, or synopsis columns" in prompt


# ─── country guardrail ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "cand_country,req_country,expected",
    [
        ("Brazil", "Brazil", True),
        ("brazil", "BRAZIL", True),
        (" Brazil ", "Brazil", True),
        ("France", "Brazil", False),
        ("", "Brazil", True),   # fail open on blank candidate country
        (None, "Brazil", True),
        ("Brazil", "", True),   # fail open on blank request country
        ("Brazil", None, True),
    ],
)
def test_candidate_passes_intl_table(cand_country, req_country, expected):
    assert candidate_passes_intl({"country": cand_country}, req_country) is expected


def test_country_guardrail_passthrough_when_country_matches():
    db_candidates = [{"id": 1, "movie_title": "Deep Water", "country": "Brazil"}]
    out = apply_country_guardrail(_result(1, "Deep Water"), db_candidates, "Brazil")
    assert out.suggested_movie_id == 1
    assert out.evidence["intl_country_guardrail"]["rejected_id"] is None


def test_country_guardrail_falls_through_on_mismatch():
    db_candidates = [
        {"id": 1, "movie_title": "Deep Water", "country": "France"},
        {"id": 2, "movie_title": "Deep Water", "country": "Brazil"},
    ]
    result = _result(1, "Deep Water", evidence={"all_candidates": [
        {"movie_master_id": 1, "movie_title": "Deep Water"},
        {"movie_master_id": 2, "movie_title": "Deep Water"},
    ]})
    out = apply_country_guardrail(result, db_candidates, "Brazil")
    assert out.suggested_movie_id == 2
    assert out.decision == "REVIEW"
    assert out.confidence <= 0.89
    assert out.evidence["intl_country_guardrail"]["replacement_id"] == 2


def test_country_guardrail_no_passing_replacement_returns_clean_no_match():
    db_candidates = [{"id": 1, "movie_title": "Deep Water", "country": "France"}]
    out = apply_country_guardrail(_result(1, "Deep Water"), db_candidates, "Brazil")
    assert out.suggested_movie_id == 0
    assert out.confidence <= 0.49
    assert out.decision == "REVIEW"


def test_country_guardrail_off_and_log_only_modes(monkeypatch):
    from app.config import settings

    db_candidates = [{"id": 1, "movie_title": "Deep Water", "country": "France"}]

    monkeypatch.setattr(settings, "AGENTIC_INTL_V2_COUNTRY_GUARDRAIL_MODE", "off")
    out = apply_country_guardrail(_result(1, "Deep Water"), db_candidates, "Brazil")
    assert out.suggested_movie_id == 1
    assert "intl_country_guardrail" not in out.evidence

    monkeypatch.setattr(settings, "AGENTIC_INTL_V2_COUNTRY_GUARDRAIL_MODE", "log_only")
    out2 = apply_country_guardrail(_result(1, "Deep Water"), db_candidates, "Brazil")
    assert out2.suggested_movie_id == 1  # unchanged
    assert out2.evidence["intl_country_guardrail"]["applied"] is False


# ─── rerank merge logic (pure, no network) ───────────────────────────────────

def test_merge_rerank_confirm_keeps_pick():
    first = _result(1, "Deep Water", confidence=0.6)
    verdict = {"verdict": "CONFIRM", "movie_master_id": 1, "movie_title": "Deep Water",
               "confidence": 0.95, "reasoning": "matches"}
    merged = _merge_rerank(first, verdict, {0, 1}, 0.97)
    assert merged.suggested_movie_id == 1
    assert merged.decision == "AUTO_ACCEPT"
    assert first.confidence == 0.6  # original untouched


def test_merge_rerank_overrule_replaces_pick():
    first = _result(1, "Wrong Title", confidence=0.5)
    verdict = {"verdict": "OVERRULE", "movie_master_id": 2, "movie_title": "Deep Water",
               "confidence": 0.9, "reasoning": "better match", "rerelease_lookup_title": "Deep Water 2026"}
    merged = _merge_rerank(first, verdict, {0, 1, 2}, 0.97)
    assert merged.suggested_movie_id == 2
    assert merged.suggested_movie_title == "Deep Water"
    assert merged.rerelease_lookup_title == "Deep Water 2026"


def test_merge_rerank_no_db_match_zeroes_id_but_keeps_titles():
    first = _result(1, "Wrong Title")
    verdict = {"verdict": "NO_DB_MATCH", "movie_master_id": 0, "movie_title": "Madame",
               "alternate_movie_title": "Le Triangle d'or", "confidence": 0.7, "reasoning": "no fit"}
    merged = _merge_rerank(first, verdict, {0, 1}, 0.97)
    assert merged.suggested_movie_id == 0
    assert merged.suggested_movie_title == "Madame"
    assert merged.alternate_movie_title == "Le Triangle d'or"


def test_merge_rerank_discards_verdict_id_not_in_candidates():
    first = _result(1, "Deep Water", confidence=0.6)
    verdict = {"verdict": "OVERRULE", "movie_master_id": 999, "movie_title": "X",
               "confidence": 0.9, "reasoning": "r"}
    merged = _merge_rerank(first, verdict, {0, 1}, 0.97)
    assert merged.suggested_movie_id == 1  # unchanged
    assert merged.evidence["rerank"]["discarded_reason"] == "verdict id not in candidate list"


def test_merge_rerank_preserves_non_movie_decision():
    first = _result(1, "Some Event", decision="REVIEW_NON_MOVIE")
    verdict = {"verdict": "CONFIRM", "movie_master_id": 1, "movie_title": "Some Event",
               "confidence": 0.95, "reasoning": "confirmed"}
    merged = _merge_rerank(first, verdict, {0, 1}, 0.97)
    assert merged.decision == "REVIEW_NON_MOVIE"


# ─── the two post-lookup fixes are isolated ───────────────────────────────────

def test_domestic_and_intl_post_lookup_confidence_differ():
    assert DOMESTIC_V2_POST_LOOKUP_CONFIDENCE == 0.75
    assert INTL_V2_POST_LOOKUP_CONFIDENCE == 0.90


def test_domestic_v2_post_lookup_never_auto_accepts():
    result = _result(0, "Some Title", confidence=0.95, decision="AUTO_ACCEPT")
    out = apply_domestic_v2_post_lookup_resolution(result, {"id": 42})
    assert out.confidence == 0.75
    assert out.decision == "REVIEW"


def test_domestic_v2_post_lookup_preserves_non_movie_decision():
    result = _result(0, "Some Title", decision="REVIEW_MULTI_FILM")
    out = apply_domestic_v2_post_lookup_resolution(result, {"id": 42})
    assert out.decision == "REVIEW_MULTI_FILM"


def test_intl_v2_post_lookup_can_auto_accept():
    result = _result(0, "Some Title", decision="REVIEW")
    out = apply_intl_v2_post_lookup_resolution(
        result, {"id": 42, "country": "Brazil"}, resolved_via="suggested_movie_title",
    )
    assert out.confidence == 0.90
    assert out.decision == "AUTO_ACCEPT"
    assert "movie_master_intl_id=42" in out.reasoning


def test_post_lookup_modules_have_no_shared_symbols_or_import_edge():
    import app.title_matching.agentic.post_lookup_v2 as domestic_mod
    import app.title_matching.agentic.post_lookup_intl_v2 as intl_mod

    # Both legitimately import the common TitleMatchResult type -- that's not
    # a shared code path, just a shared dependency. Only their OWN
    # functions/constants must never overlap.
    domestic_own = {"DOMESTIC_V2_POST_LOOKUP_CONFIDENCE", "apply_domestic_v2_post_lookup_resolution"}
    intl_own = {"INTL_V2_POST_LOOKUP_CONFIDENCE", "apply_intl_v2_post_lookup_resolution"}
    assert domestic_own <= set(vars(domestic_mod))
    assert intl_own <= set(vars(intl_mod))
    assert not (domestic_own & intl_own)
    assert domestic_mod.__file__ != intl_mod.__file__
    assert intl_mod not in domestic_mod.__dict__.values()
    assert domestic_mod not in intl_mod.__dict__.values()


# ─── sandbox target resolution ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "market,variant,v1_opt_in,expect_intl_url",
    [
        ("domestic", "v1", False, False),
        ("domestic", "v2", False, False),
        ("international", "v1", False, False),
        ("international", "v1", True, True),
        ("international", "v2", False, True),
    ],
)
def test_sandbox_url_for_table(monkeypatch, market, variant, v1_opt_in, expect_intl_url):
    from app.config import settings

    monkeypatch.setattr(settings, "CLAUDE_SANDBOX_URL_INTL", "http://claude-sandbox-intl:3100")
    monkeypatch.setattr(settings, "AGENTIC_INTL_V1_USE_INTL_SANDBOX", v1_opt_in)
    url = sandbox_url_for(market, variant=variant)
    if expect_intl_url:
        assert url == "http://claude-sandbox-intl:3100"
    else:
        assert url is None


def test_sandbox_url_for_falls_back_when_intl_url_unconfigured(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "CLAUDE_SANDBOX_URL_INTL", "")
    assert sandbox_url_for("international", variant="v2") is None


# ─── runner_intl_v2 never touches the shared orchestrator ────────────────────

def test_run_agentic_match_intl_v2_requires_country():
    from app.title_matching.agentic.runner_intl_v2 import run_agentic_match_intl_v2

    with pytest.raises(ValueError):
        run_agentic_match_intl_v2("Some Title", country="")


def test_run_agentic_match_intl_v2_never_calls_shared_run_agentic_match():
    from app.title_matching.agentic import runner_intl_v2 as m
    from app.title_matching.types import TitleMatchResult

    fake_result = TitleMatchResult(
        suggested_movie_id=1, suggested_movie_title="Deep Water", canonical_movie_id=1,
        confidence=0.95, decision="AUTO_ACCEPT", reasoning="r", evidence={}, fired_ai=True,
    )
    with patch.object(m, "_check_sandbox_reachable", return_value=None), \
         patch.object(m, "fetch_db_candidates_intl", return_value=[]), \
         patch.object(m, "fetch_vespa_candidates_intl", return_value=[]), \
         patch.object(m, "_call_sandbox", return_value="stdout"), \
         patch.object(m, "parse_agent_output", return_value=fake_result), \
         patch.object(m, "apply_country_guardrail", side_effect=lambda result, *a, **k: result), \
         patch("app.title_matching.agentic.runner.run_agentic_match") as mock_shared:
        result = m.run_agentic_match_intl_v2("Some Title", country="Brazil")

    mock_shared.assert_not_called()
    assert result.suggested_movie_id == 1


# ─── intl v2 batch pipeline_variant seams ─────────────────────────────────────

@pytest.fixture
def db_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.mark.parametrize(
    "pipeline_variant,expected",
    [(None, "v1"), ("v1", "v1"), ("garbage", "v1"), ("v2", "v2")],
)
def test_intl_variant_of(pipeline_variant, expected):
    import app.tasks.agentic_intl_match_task as task_mod

    assert task_mod._variant_of(MovieTitleIntlBatchJob(id="j1", pipeline_variant=pipeline_variant)) == expected


def test_intl_enqueue_next_window_publish_kwargs_depend_on_job_variant(monkeypatch, db_engine):
    import app.tasks.agentic_intl_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(
        task_mod, "_row_args_for",
        lambda jid, idxs: {i: ["Row", None, None, "Brazil", False] for i in idxs},
    )
    monkeypatch.setattr(task_mod.agentic_intl_batch_row, "apply_async", MagicMock())

    with Session(db_engine) as s:
        s.add(MovieTitleIntlBatchJob(id="job-v1", status="processing", total=1))
        s.add(MovieTitleIntlBatchJob(id="job-v2", status="processing", total=1, pipeline_variant="v2"))
        s.commit()

    task_mod.enqueue_next_window("job-v1", 1)
    task_mod.enqueue_next_window("job-v2", 1)

    calls = task_mod.agentic_intl_batch_row.apply_async.call_args_list
    assert "kwargs" not in calls[0].kwargs
    assert calls[1].kwargs["kwargs"] == {"variant": "v2"}


def test_intl_agentic_batch_row_forwards_variant_only_for_v2(monkeypatch, db_engine):
    import app.tasks.agentic_intl_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    fake_hash = {}

    def _store(job_id, row_index, row_result):
        fake_hash[str(row_index)] = json.dumps(row_result)
        return True

    monkeypatch.setattr(task_mod, "_store_row_result", _store)
    monkeypatch.setattr(task_mod, "_after_row_terminal", lambda job_id: None)

    with Session(db_engine) as s:
        s.add(MovieTitleIntlBatchJob(id="job-v1", status="processing", total=1))
        s.add(MovieTitleIntlBatchJob(id="job-v2", status="processing", total=1, pipeline_variant="v2"))
        s.commit()

    mock_v1_run = MagicMock(return_value=MagicMock(confidence=0.9, reasoning="ok"))
    mock_v2_run = MagicMock(return_value=MagicMock(confidence=0.9, reasoning="ok"))
    with patch("app.title_matching.agentic.runner.run_agentic_match", mock_v1_run), \
         patch("app.title_matching.agentic.runner_intl_v2.run_agentic_match_intl_v2", mock_v2_run), \
         patch("app.title_matching.sandbox_semaphore.acquire", return_value="holder"), \
         patch("app.title_matching.sandbox_semaphore.release"), \
         patch.object(task_mod, "_movie_exists", return_value=False):
        task_mod.agentic_intl_batch_row.run("job-v1", 0, "Some Title", country="Brazil")
        task_mod.agentic_intl_batch_row.run("job-v2", 0, "Some Title", country="Brazil", variant="v2")

    mock_v1_run.assert_called_once()
    mock_v2_run.assert_called_once()
    assert mock_v2_run.call_args.kwargs.get("country") == "Brazil"


# ─── routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "AGENTIC_TITLE_MATCH_ENABLED", True)
    return TestClient(app)


def test_intl_v2_single_requires_country(client):
    resp = client.post("/api/v2/intl-movie-title-match/single", json={"title": "Deep Water"})
    assert resp.status_code == 422


def test_intl_v2_single_returns_400_when_disabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_TITLE_MATCH_ENABLED", False)
    resp = client.post(
        "/api/v2/intl-movie-title-match/single",
        json={"title": "Deep Water", "country": "Brazil"},
    )
    assert resp.status_code == 400


def test_intl_v2_single_agentic_error_returns_honest_no_match(client):
    with patch(
        "app.title_matching.agentic.runner_intl_v2.run_agentic_match_intl_v2_async",
        side_effect=AgenticConfigError("sandbox down"),
    ):
        resp = client.post(
            "/api/v2/intl-movie-title-match/single",
            json={"title": "Deep Water", "country": "Brazil"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["suggested_movie_id"] == 0
    assert data["decision"] == "REVIEW"
    assert data["evidence"]["pipeline_variant"] == "intl_v2"

"""Tests for the domestic agentic v2 work:
  - v1's rule-based fallback matcher is retired -- every /single request
    (v1 and v2) goes through the Claude agentic runner unconditionally, and
    an infra failure degrades to an honest no-match (200, id=0) rather than
    a substituted guess or an unhandled 500.
  - the v2 metadata guardrail's deterministic predicate and fall-through.
  - the v2 batch pipeline_variant seams in agentic_match_task.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.models import MovieTitleBatchJob
from app.title_matching.agentic import AgenticConfigError, AgenticError
from app.title_matching.agentic.metadata_guardrail import (
    EXEMPT_GENRES,
    apply_metadata_guardrail,
    candidate_passes,
    is_genre_exempt,
)
from app.title_matching.agentic.prompt_builder import build_prompt
from app.title_matching.agentic.prompt_builder_v2 import build_prompt_v2
from app.title_matching.types import TitleMatchResult


def _stub_result(*_args, **_kwargs) -> TitleMatchResult:
    return TitleMatchResult(
        suggested_movie_id=1, suggested_movie_title="Inception", canonical_movie_id=1,
        confidence=0.95, decision="AUTO_ACCEPT", reasoning="stubbed", evidence={}, fired_ai=True,
    )


# ─── /single always goes through the agentic runner ─────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "AGENTIC_TITLE_MATCH_ENABLED", True)
    return TestClient(app)


def test_no_import_of_deleted_fallback_modules():
    for module_name in (
        "app.title_matching.decision_engine", "app.title_matching.candidate_generator",
        "app.title_matching.loader", "app.title_matching.engine",
    ):
        with pytest.raises(ImportError):
            __import__(module_name)


def test_single_v1_and_v2_always_call_agentic_runner(client):
    with patch("app.title_matching.agentic.runner.run_agentic_match", side_effect=_stub_result) as mock_run:
        resp1 = client.post("/api/v1/movie-title-match/single", json={"title": "Inception"})
        resp2 = client.post("/api/v2/movie-title-match/single", json={"title": "Inception"})

    assert resp1.status_code == 200 and resp1.json()["suggested_movie_id"] == 1
    assert resp2.status_code == 200 and resp2.json()["suggested_movie_id"] == 1
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[1].kwargs.get("variant") == "v2"


def test_single_returns_400_when_agentic_disabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AGENTIC_TITLE_MATCH_ENABLED", False)
    resp = client.post("/api/v1/movie-title-match/single", json={"title": "Inception"})
    assert resp.status_code == 400


@pytest.mark.parametrize("path", ["/api/v1/movie-title-match/single", "/api/v2/movie-title-match/single"])
def test_agentic_error_returns_honest_no_match_not_500(client, path):
    with patch("app.title_matching.agentic.runner.run_agentic_match", side_effect=AgenticConfigError("boom")):
        resp = client.post(path, json={"title": "Inception"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["suggested_movie_id"] == 0
    assert data["confidence"] == 0.0
    assert data["decision"] == "REVIEW"
    assert data["fired_ai"] is False
    assert data["evidence"]["agentic_error"] is True
    assert "AgenticConfigError" in data["reasoning"]


def test_ticketing_evidence_still_attached_on_error_path(client):
    from app.title_matching.evidence_types import EvidenceResult

    fake_evidence = EvidenceResult(ticketing_poster_url="https://cdn.tickets.com/x.jpg", extraction_outcome="SUCCESS")
    with patch("app.title_matching.agentic.runner.run_agentic_match", side_effect=AgenticConfigError("boom")), \
         patch("app.title_matching.evidence_fetcher.fetch_evidence", return_value=fake_evidence):
        resp = client.post(
            "/api/v1/movie-title-match/single",
            json={"title": "Inception", "ticketing_url": "https://tickets.example.com/x"},
        )

    assert resp.json()["ticketing_poster_url"] == "https://cdn.tickets.com/x.jpg"


# ─── v2 prompt content ────────────────────────────────────────────────────────

def test_v1_prompt_unaffected_by_v2_wording():
    prompt = build_prompt("some title", None, None, None)
    for marker in ("Step 2b", "INCOMPLETE-METADATA", "Concert/Special Events"):
        assert marker not in prompt


def test_v2_prompt_has_exemption_rule_and_metadata_fields():
    db_candidates = [{
        "id": 1, "movie_title": "Some Movie", "release_date": "2020-01-01", "cover_image": "",
        "genre": "Drama", "director": "Jane Doe", "cast_list": "Actor One", "synopsis": "A story.",
    }]
    prompt = build_prompt_v2("some title", None, None, None, db_candidates=db_candidates)
    for marker in ("Sports", "Concert/Special Events", "NO OTHER genre is exempt", "Jane Doe", "A story."):
        assert marker in prompt


# ─── metadata_guardrail ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "director,synopsis,genre,expected",
    [
        ("", "", "Drama", False),
        ("", "", "Sports", True),
        ("", "", "Concert/Special Events", True),
        ("", "", "Concert", False),
        ("", "", "Short Film", False),
        ("Ridley Scott", "", "Drama", True),
        ("N/A", "-", "Drama", False),
    ],
)
def test_candidate_passes_table(director, synopsis, genre, expected):
    assert candidate_passes({"director": director, "synopsis": synopsis, "genre": genre}) is expected


def test_is_genre_exempt():
    assert EXEMPT_GENRES == frozenset({"sports", "concert/special events"})
    assert is_genre_exempt("SPORTS") is True
    assert is_genre_exempt("Drama") is False


def _result(movie_id, title, confidence=0.95, decision="AUTO_ACCEPT", evidence=None):
    return TitleMatchResult(
        suggested_movie_id=movie_id, suggested_movie_title=title, canonical_movie_id=movie_id,
        confidence=confidence, decision=decision, reasoning="model reasoning",
        evidence=evidence or {}, fired_ai=True,
    )


def test_guardrail_passthrough_when_candidate_passes():
    db_candidates = [{"id": 1, "movie_title": "Drama Movie", "genre": "Drama", "director": "X", "synopsis": "Y"}]
    out = apply_metadata_guardrail(_result(1, "Drama Movie"), db_candidates)
    assert out.suggested_movie_id == 1
    assert out.evidence["metadata_guardrail"]["rejected_id"] is None


def test_guardrail_falls_through_to_model_runner_up_then_forces_review():
    db_candidates = [
        {"id": 1, "movie_title": "Stub Row", "genre": "Drama", "director": "", "synopsis": ""},
        {"id": 2, "movie_title": "Stub Row Extended", "genre": "Drama", "director": "Real Director", "synopsis": "Real plot."},
    ]
    result = _result(1, "Stub Row", evidence={"all_candidates": [
        {"movie_master_id": 1, "movie_title": "Stub Row"},
        {"movie_master_id": 2, "movie_title": "Stub Row Extended"},
    ]})
    out = apply_metadata_guardrail(result, db_candidates)
    assert out.suggested_movie_id == 2
    assert out.decision == "REVIEW"
    assert out.confidence <= 0.89
    assert out.evidence["metadata_guardrail"]["replacement_source"] == "model_candidates"


def test_guardrail_no_passing_replacement_returns_clean_no_match():
    db_candidates = [{"id": 1, "movie_title": "Stub Row", "genre": "Drama", "director": "", "synopsis": ""}]
    out = apply_metadata_guardrail(_result(1, "Stub Row"), db_candidates)
    assert out.suggested_movie_id == 0
    assert out.confidence <= 0.49
    assert out.decision == "REVIEW"


def test_guardrail_preserves_non_movie_decision_and_off_mode(monkeypatch):
    from app.config import settings

    db_candidates = [{"id": 1, "movie_title": "Stub", "genre": "Drama", "director": "", "synopsis": ""}]
    out = apply_metadata_guardrail(_result(1, "Stub", decision="REVIEW_NON_MOVIE"), db_candidates)
    assert out.decision == "REVIEW_NON_MOVIE"

    monkeypatch.setattr(settings, "AGENTIC_V2_METADATA_GUARDRAIL_MODE", "off")
    out2 = apply_metadata_guardrail(_result(1, "Stub"), db_candidates)
    assert out2.suggested_movie_id == 1
    assert "metadata_guardrail" not in out2.evidence


# ─── runner.py seams (v1 stays byte-identical) ───────────────────────────────

def test_run_agentic_match_v1_skips_metadata_enrichment_and_guardrail():
    from app.title_matching.agentic import runner as runner_mod

    fake_result = TitleMatchResult(
        suggested_movie_id=1, suggested_movie_title="X", canonical_movie_id=1,
        confidence=0.95, decision="AUTO_ACCEPT", reasoning="r", evidence={}, fired_ai=True,
    )
    with patch.object(runner_mod, "_check_sandbox_reachable", return_value=None), \
         patch.object(runner_mod, "_fetch_db_candidates", return_value=[]) as mock_fetch, \
         patch.object(runner_mod, "_fetch_vespa_candidates", return_value=[]), \
         patch.object(runner_mod, "_call_sandbox", return_value="stdout"), \
         patch.object(runner_mod, "parse_agent_output", return_value=fake_result):
        result = runner_mod.run_agentic_match("Some Title")

    assert mock_fetch.call_args.kwargs.get("include_metadata") is False
    assert "metadata_guardrail" not in result.evidence


def test_run_agentic_match_v2_requests_metadata_and_runs_guardrail():
    from app.title_matching.agentic import runner as runner_mod

    fake_result = TitleMatchResult(
        suggested_movie_id=1, suggested_movie_title="X", canonical_movie_id=1,
        confidence=0.95, decision="AUTO_ACCEPT", reasoning="r", evidence={}, fired_ai=True,
    )
    db_candidates = [{"id": 1, "movie_title": "X", "genre": "Drama", "director": "D", "synopsis": "S"}]
    with patch.object(runner_mod, "_check_sandbox_reachable", return_value=None), \
         patch.object(runner_mod, "_fetch_db_candidates", return_value=db_candidates) as mock_fetch, \
         patch.object(runner_mod, "_fetch_vespa_candidates", return_value=[]), \
         patch.object(runner_mod, "_call_sandbox", return_value="stdout"), \
         patch.object(runner_mod, "parse_agent_output", return_value=fake_result):
        result = runner_mod.run_agentic_match("Some Title", variant="v2")

    assert mock_fetch.call_args.kwargs.get("include_metadata") is True
    assert result.evidence["metadata_guardrail"]["checked"] is True


def test_run_agentic_match_rejects_unknown_variant():
    from app.title_matching.agentic import runner as runner_mod

    with pytest.raises(ValueError):
        runner_mod.run_agentic_match("Some Title", variant="v3")


# ─── v2 batch pipeline_variant seams (in-memory sqlite, no live Redis/broker) ─

@pytest.fixture
def db_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def fake_hash():
    return {}


@pytest.fixture
def patched_task(monkeypatch, db_engine, fake_hash):
    import app.tasks.agentic_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)

    def _store(job_id, row_index, row_result):
        fake_hash[str(row_index)] = json.dumps(row_result)
        return True

    monkeypatch.setattr(task_mod, "_store_row_result", _store)
    monkeypatch.setattr(task_mod.finalize_batch, "apply_async", MagicMock())
    monkeypatch.setattr(task_mod, "enqueue_next_window", MagicMock(return_value=0))
    return task_mod


@pytest.mark.parametrize(
    "pipeline_variant,expected",
    [(None, "v1"), ("v1", "v1"), ("garbage", "v1"), ("v2", "v2")],
)
def test_variant_of(pipeline_variant, expected):
    import app.tasks.agentic_match_task as task_mod

    assert task_mod._variant_of(MovieTitleBatchJob(id="j1", pipeline_variant=pipeline_variant)) == expected


def test_enqueue_next_window_publish_kwargs_depend_on_job_variant(monkeypatch, db_engine):
    import app.tasks.agentic_match_task as task_mod

    monkeypatch.setattr("app.database.engine", db_engine, raising=False)
    monkeypatch.setattr(task_mod, "_row_args_for", lambda jid, idxs: {i: ["Row", None, None, False] for i in idxs})
    monkeypatch.setattr(task_mod.agentic_batch_row, "apply_async", MagicMock())

    with Session(db_engine) as s:
        s.add(MovieTitleBatchJob(id="job-v1", status="processing", total=1))
        s.add(MovieTitleBatchJob(id="job-v2", status="processing", total=1, pipeline_variant="v2"))
        s.commit()

    task_mod.enqueue_next_window("job-v1", 1)
    task_mod.enqueue_next_window("job-v2", 1)

    calls = task_mod.agentic_batch_row.apply_async.call_args_list
    assert "kwargs" not in calls[0].kwargs
    assert calls[1].kwargs["kwargs"] == {"variant": "v2"}


def test_agentic_batch_row_forwards_variant_only_for_v2(patched_task, db_engine, fake_hash):
    """v1 call stays byte-identical (no variant kwarg at all) -- the narrow
    fakes in _batch_worker_bootstrap.py/test_batch_e2e.py would TypeError on
    an unexpected `variant=`."""
    with Session(db_engine) as s:
        s.add(MovieTitleBatchJob(id="job-v1", status="processing", total=1))
        s.add(MovieTitleBatchJob(id="job-v2", status="processing", total=1, pipeline_variant="v2"))
        s.commit()

    mock_run = MagicMock(return_value=MagicMock(confidence=0.9, reasoning="ok"))
    with patch("app.title_matching.agentic.runner.run_agentic_match", mock_run), \
         patch("app.title_matching.sandbox_semaphore.acquire", return_value="holder"), \
         patch("app.title_matching.sandbox_semaphore.release"), \
         patch.object(patched_task, "_movie_exists", return_value=False):
        patched_task.agentic_batch_row.run("job-v1", 0, "Some Title")
        patched_task.agentic_batch_row.run("job-v2", 0, "Some Title", variant="v2")

    assert "variant" not in mock_run.call_args_list[0].kwargs
    assert mock_run.call_args_list[1].kwargs.get("variant") == "v2"


def test_agentic_batch_row_stale_worker_typeerror_records_failed_row(patched_task, db_engine, fake_hash):
    """A v2 message reaching a pre-deploy worker without `variant` support
    must not wedge the job -- records failed, does not raise."""
    with Session(db_engine) as s:
        s.add(MovieTitleBatchJob(id="job-stale", status="processing", total=1, pipeline_variant="v2"))
        s.commit()

    def _stale(*args, **kwargs):
        if "variant" in kwargs:
            raise TypeError("unexpected keyword argument 'variant'")
        return MagicMock(confidence=0.9, reasoning="ok")

    with patch("app.title_matching.agentic.runner.run_agentic_match", side_effect=_stale), \
         patch("app.title_matching.sandbox_semaphore.acquire", return_value="holder"), \
         patch("app.title_matching.sandbox_semaphore.release"):
        patched_task.agentic_batch_row.run("job-stale", 0, "Some Title", variant="v2")

    with Session(db_engine) as s:
        job = s.get(MovieTitleBatchJob, "job-stale")
    assert job.processed == 1
    assert job.failed == 1

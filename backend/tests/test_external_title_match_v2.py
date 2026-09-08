"""Tests for the external API's v2 submit surface (/api/v2/singletitle,
/api/v2/batchtitle) and the (market, pipeline_variant) dispatch branch it
feeds in external_match_task.external_match_row.

Consolidated into one file per feature (repo preference) and organised as:

  1. HTTP: v2 submissions create pipeline_variant="v2" jobs.
  2. HTTP regression: v1 submissions still create pipeline_variant=None jobs.
  3. HTTP: the shared, unversioned job status/results/retry endpoints behave
     identically for a v1 and a v2 job -- proof no fork was introduced there.
  4. Task: the four (market x variant) combinations each call exactly one
     runner, with the right arguments.
  5. Migration b1c2d3e4f5a6 upgrade/downgrade round-trip.

The app under test is a standalone FastAPI instance mounting both routers,
not app.main.app: main.py only includes them when EXTERNAL_API_ENABLED is
true (default False), so importing the real app would leave these routes
unmounted. Same approach as tests/test_lobby_check_api.py.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine, delete, select
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.dependencies.api_auth import hash_api_key
from app.models import ApiKey, ApiTitleMatchJob, ApiTitleMatchRow
from app.routers import external_title_match, external_title_match_v2

# --------------------------------------------------------------------------
# HTTP fixtures
# --------------------------------------------------------------------------

_http_engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
SQLModel.metadata.create_all(_http_engine)

_app = FastAPI()
_app.include_router(external_title_match.router)
_app.include_router(external_title_match_v2.router)


def _override_get_session():
    with Session(_http_engine) as session:
        yield session


_app.dependency_overrides[get_session] = _override_get_session
client = TestClient(_app, raise_server_exceptions=False)

RAW_KEY = "ext-v2-test-key"
HEADERS = {"x-api-key": RAW_KEY}
KEY_ID = "test-key-v2"


@pytest.fixture(autouse=True)
def _seed_key_and_stub_celery(monkeypatch):
    """Seed the API key every test and stub the two Celery hand-offs.

    Submitted jobs stay at phase='queued' forever here (dispatch is stubbed),
    and _check_concurrent_jobs counts exactly those phases, so the job table
    MUST be wiped between tests or the Nth submission in this module 429s.
    """
    monkeypatch.setattr(
        "app.tasks.external_match_task.external_dispatch_job_task.delay",
        lambda job_id: None,
    )
    monkeypatch.setattr(
        "app.tasks.external_match_task.external_retry_rows_task.delay",
        lambda job_id, row_uuids: None,
    )
    with Session(_http_engine) as s:
        s.add(
            ApiKey(
                id=KEY_ID,
                key_hash=hash_api_key(RAW_KEY),
                key_prefix=RAW_KEY[:8],
                active=True,
                max_rows_per_batch=20,
                max_concurrent_jobs=50,
                requests_per_minute=100000,
                db_update_allowed=False,
            )
        )
        s.commit()
    yield
    with Session(_http_engine) as s:
        s.exec(delete(ApiTitleMatchRow))
        s.exec(delete(ApiTitleMatchJob))
        s.exec(delete(ApiKey))
        s.commit()


def _row(market: str = "domestic") -> dict:
    """A row that passes validate_rows_for_market for `market`: `country` is
    REQUIRED for international and FORBIDDEN for domestic."""
    body = {
        "row_uuid": str(uuid.uuid4()),
        "movie_title": "Verflucht normal",
        "show_date": "2026-07-28",
        "ticketing_url": "https://www.cinemaxx.de/buchtickets/1203",
    }
    if market == "international":
        body["country"] = "Germany"
    return body


def _job_row(job_id: str) -> ApiTitleMatchJob:
    with Session(_http_engine) as s:
        return s.get(ApiTitleMatchJob, job_id)


# --------------------------------------------------------------------------
# 1. v2 submissions create pipeline_variant="v2"
# --------------------------------------------------------------------------


@pytest.mark.parametrize("market", ["domestic", "international"])
def test_v2_singletitle_creates_v2_job(market):
    resp = client.post(
        f"/api/v2/singletitle?type={market}", headers=HEADERS, json=_row(market)
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["rows_total"] == 1

    job = _job_row(body["job_id"])
    assert job.pipeline_variant == "v2"
    assert job.market == market


@pytest.mark.parametrize("market", ["domestic", "international"])
def test_v2_batchtitle_creates_v2_job(market):
    rows = [_row(market) for _ in range(3)]
    resp = client.post(
        f"/api/v2/batchtitle?type={market}", headers=HEADERS, json={"rows": rows}
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    job = _job_row(job_id)
    assert job.pipeline_variant == "v2"
    assert job.rows_total == 3

    with Session(_http_engine) as s:
        stored = s.exec(
            select(ApiTitleMatchRow).where(ApiTitleMatchRow.job_id == job_id)
        ).all()
    assert len(stored) == 3
    assert {r.row_uuid for r in stored} == {r["row_uuid"] for r in rows}


def test_v2_still_enforces_market_row_validation():
    """v2 reuses v1's validate_rows_for_market verbatim -- an international
    row with no country is a 422 on the v2 surface too, and creates no job."""
    bad = _row("international")
    del bad["country"]
    resp = client.post(
        "/api/v2/singletitle?type=international", headers=HEADERS, json=bad
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "validation_failed"
    assert detail["row_errors"][0]["field"] == "country"

    with Session(_http_engine) as s:
        assert s.exec(select(ApiTitleMatchJob)).all() == []


def test_v2_requires_api_key():
    resp = client.post("/api/v2/singletitle?type=domestic", json=_row())
    assert resp.status_code == 401


def test_no_v2_job_endpoints_exist():
    """Job status/results/retry are deliberately shared and unversioned; a
    /api/v2/external/jobs/* route would be pure duplication."""
    v2_paths = [
        p for p in _app.openapi()["paths"] if p.startswith("/api/v2")
    ]
    assert sorted(v2_paths) == ["/api/v2/batchtitle", "/api/v2/singletitle"]


# --------------------------------------------------------------------------
# 2. v1 regression: untouched, still creates NULL-variant jobs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("market", ["domestic", "international"])
def test_v1_singletitle_still_creates_v1_job(market):
    resp = client.post(
        f"/api/v1/singletitle?type={market}", headers=HEADERS, json=_row(market)
    )
    assert resp.status_code == 202, resp.text
    job = _job_row(resp.json()["job_id"])
    # NULL, not the string "v1" -- _variant_of reads NULL as v1, and writing
    # anything here would change what existing integrations produce.
    assert job.pipeline_variant is None


@pytest.mark.parametrize("market", ["domestic", "international"])
def test_v1_batchtitle_still_creates_v1_job(market):
    resp = client.post(
        f"/api/v1/batchtitle?type={market}",
        headers=HEADERS,
        json={"rows": [_row(market) for _ in range(2)]},
    )
    assert resp.status_code == 202, resp.text
    job = _job_row(resp.json()["job_id"])
    assert job.pipeline_variant is None
    assert job.rows_total == 2


def test_v1_and_v2_submissions_are_identical_apart_from_the_variant():
    """The two surfaces must not drift in response shape or stored job state."""
    r1 = client.post("/api/v1/singletitle?type=domestic", headers=HEADERS, json=_row())
    r2 = client.post("/api/v2/singletitle?type=domestic", headers=HEADERS, json=_row())
    assert r1.status_code == r2.status_code == 202
    assert set(r1.json()) == set(r2.json())
    assert r1.json()["status"] == r2.json()["status"]
    assert r1.json()["rows_total"] == r2.json()["rows_total"]

    j1, j2 = _job_row(r1.json()["job_id"]), _job_row(r2.json()["job_id"])
    compared = (
        "api_key_id", "market", "db_update", "phase", "rows_total",
        "rows_processed", "rows_matched", "rows_no_match", "rows_failed", "error",
    )
    assert {c: getattr(j1, c) for c in compared} == {c: getattr(j2, c) for c in compared}
    assert (j1.pipeline_variant, j2.pipeline_variant) == (None, "v2")


# --------------------------------------------------------------------------
# 3. Shared job endpoints behave identically for both variants
# --------------------------------------------------------------------------


def _submit(version: str, market: str = "domestic") -> str:
    resp = client.post(
        f"/api/{version}/batchtitle?type={market}",
        headers=HEADERS,
        json={"rows": [_row(market) for _ in range(2)]},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


def _finish_job(job_id: str, *, fail_first: bool = False) -> None:
    """Drive a submitted job to a terminal state directly in the DB, so the
    job endpoints have something realistic to serve."""
    with Session(_http_engine) as s:
        rows = s.exec(
            select(ApiTitleMatchRow).where(ApiTitleMatchRow.job_id == job_id)
        ).all()
        for i, row in enumerate(rows):
            if fail_first and i == 0:
                row.status = "failed"
                row.error = "boom"
                row.attempts = 1
            else:
                row.status = "completed"
                row.mapped_title = "Verflucht normal"
                row.confidence = 0.9
                row.reasoning = "matched via test"
                row.present_in_db = True
                row.attempts = 1
            s.add(row)
        job = s.get(ApiTitleMatchJob, job_id)
        job.rows_processed = len(rows)
        job.rows_failed = 1 if fail_first else 0
        job.rows_matched = len(rows) - (1 if fail_first else 0)
        job.phase = "completed_with_errors" if fail_first else "completed"
        s.add(job)
        s.commit()


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_job_status_endpoint_is_shared_across_variants(version):
    job_id = _submit(version)
    _finish_job(job_id)

    resp = client.get(f"/api/v1/external/jobs/{job_id}", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] == job_id
    assert body["status"] == "completed"
    assert body["rows_total"] == 2
    assert body["rows_processed"] == 2
    # The variant is an internal dispatch detail and must not leak into the
    # public job contract.
    assert "pipeline_variant" not in body


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_job_results_endpoint_is_shared_across_variants(version):
    job_id = _submit(version)
    _finish_job(job_id)

    resp = client.get(f"/api/v1/external/jobs/{job_id}/results", headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["results"]) == 2
    assert body["next_cursor"] is None
    first = body["results"][0]
    assert set(first) >= {
        "row_uuid", "input", "mapped_title", "confidence_score",
        "ai_reasoning", "present_in_db",
    }
    assert first["mapped_title"] == "Verflucht normal"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_job_retry_endpoint_is_shared_across_variants(version):
    job_id = _submit(version)
    _finish_job(job_id, fail_first=True)

    with Session(_http_engine) as s:
        failed_uuid = s.exec(
            select(ApiTitleMatchRow.row_uuid)
            .where(ApiTitleMatchRow.job_id == job_id)
            .where(ApiTitleMatchRow.status == "failed")
        ).first()

    resp = client.post(
        f"/api/v1/external/jobs/{job_id}/retry",
        headers=HEADERS,
        json={"row_uuids": [failed_uuid]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["queued"] == [failed_uuid]
    assert resp.json()["skipped"] == []
    # Retry must not rewrite the job's variant -- a retried row has to re-run
    # on the same pipeline the job was submitted with.
    assert _job_row(job_id).pipeline_variant == (None if version == "v1" else "v2")


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_job_endpoints_are_tenancy_scoped_for_both_variants(version):
    job_id = _submit(version)
    resp = client.get(f"/api/v1/external/jobs/{job_id}", headers={"x-api-key": "nope"})
    assert resp.status_code == 401
    with Session(_http_engine) as s:
        s.add(
            ApiKey(
                id="other-key",
                key_hash=hash_api_key("other-raw-key"),
                key_prefix="other-ra",
                active=True,
                max_concurrent_jobs=50,
                requests_per_minute=100000,
            )
        )
        s.commit()
    resp = client.get(
        f"/api/v1/external/jobs/{job_id}", headers={"x-api-key": "other-raw-key"}
    )
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# 4. Task-level dispatch matrix
# --------------------------------------------------------------------------


class _Result:
    def __init__(self, title="Verflucht normal"):
        self.canonical_movie_id = 0
        self.suggested_movie_id = 0
        self.suggested_movie_title = title
        self.confidence = 0.9
        self.reasoning = "matched via test"


@pytest.fixture
def task_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def patched_task(monkeypatch, task_engine):
    import app.tasks.external_match_task as task_mod

    monkeypatch.setattr("app.database.engine", task_engine, raising=False)
    monkeypatch.setattr(task_mod.external_finalize_job, "apply_async", MagicMock())
    monkeypatch.setattr(task_mod, "enqueue_next_window", MagicMock(return_value=0))
    return task_mod


def _make_task_job(engine, market: str, variant, job_id="ext-v2-job") -> str:
    with Session(engine) as s:
        s.add(
            ApiTitleMatchJob(
                id=job_id,
                api_key_id="k1",
                market=market,
                phase="processing",
                pipeline_variant=variant,
            )
        )
        s.commit()
    return job_id


def _make_task_row(engine, job_id: str, *, country=None) -> int:
    payload = {
        "movie_title": "Verflucht normal",
        "show_date": "2026-07-28",
        "ticketing_url": "https://www.cinemaxx.de/buchtickets/1203",
    }
    if country:
        payload["country"] = country
    with Session(engine) as s:
        row = ApiTitleMatchRow(
            job_id=job_id, row_uuid="r1", input_json=json.dumps(payload),
            status="dispatched",
        )
        s.add(row)
        s.commit()
        return row.id


def _dispatch(patched_task, engine, market, variant, *, country=None):
    """Run one row through external_match_row with all three runners mocked.

    Returns (v1_mock, domestic_v2_mock, intl_v2_mock). Each runner is patched
    on its OWN module, because external_match_row imports each one lazily
    inside its branch.
    """
    import app.title_matching.agentic.runner as runner_mod
    import app.title_matching.agentic.runner_intl_v2 as runner_intl_v2_mod
    import app.title_matching.agentic.runner_v2 as runner_v2_mod
    import app.title_matching.sandbox_semaphore as sem

    m_v1 = MagicMock(return_value=_Result())
    m_dv2 = MagicMock(return_value=_Result())
    m_iv2 = MagicMock(return_value=_Result())

    job_id = _make_task_job(engine, market, variant)
    row_id = _make_task_row(engine, job_id, country=country)

    with patch.object(runner_mod, "run_agentic_match", m_v1), \
         patch.object(runner_v2_mod, "run_agentic_match_v2", m_dv2), \
         patch.object(runner_intl_v2_mod, "run_agentic_match_intl_v2", m_iv2), \
         patch.object(sem, "acquire", return_value="h"), \
         patch.object(sem, "release"):
        patched_task.external_match_row.run(job_id, row_id)

    return m_v1, m_dv2, m_iv2


@pytest.mark.parametrize("variant", [None, "v1"])
def test_domestic_v1_calls_shared_runner(patched_task, task_engine, variant):
    m_v1, m_dv2, m_iv2 = _dispatch(patched_task, task_engine, "domestic", variant)

    m_dv2.assert_not_called()
    m_iv2.assert_not_called()
    m_v1.assert_called_once()
    args, kwargs = m_v1.call_args
    assert args[0] == "Verflucht normal"
    assert args[2] is None  # theater is always None on the external contract
    assert kwargs["market"] == "domestic"
    assert kwargs["country"] is None
    assert kwargs["usage_ctx"].call_path == "agentic_cli"
    # No `variant` kwarg is threaded on this path at all.
    assert "variant" not in kwargs


@pytest.mark.parametrize("variant", [None, "v1"])
def test_international_v1_calls_shared_runner(patched_task, task_engine, variant):
    m_v1, m_dv2, m_iv2 = _dispatch(
        patched_task, task_engine, "international", variant, country="Germany"
    )

    m_dv2.assert_not_called()
    m_iv2.assert_not_called()
    m_v1.assert_called_once()
    _args, kwargs = m_v1.call_args
    assert kwargs["market"] == "international"
    assert kwargs["country"] == "Germany"
    assert kwargs["usage_ctx"].call_path == "agentic_cli"


def test_domestic_v2_calls_domestic_v2_runner(patched_task, task_engine):
    m_v1, m_dv2, m_iv2 = _dispatch(patched_task, task_engine, "domestic", "v2")

    m_v1.assert_not_called()
    m_iv2.assert_not_called()
    m_dv2.assert_called_once()
    args, kwargs = m_dv2.call_args
    assert args[0] == "Verflucht normal"
    assert args[2] is None  # theater
    # Domestic v2 takes no market/country -- an intl call through it is
    # unrepresentable by design, so passing either would be a TypeError.
    assert "market" not in kwargs
    assert "country" not in kwargs
    ctx = kwargs["usage_ctx"]
    assert ctx.call_path == "agentic_cli_v2"
    assert ctx.task_type == "domestic_mapping"
    assert ctx.caller_type == "external_api"
    assert ctx.job_type == "ApiTitleMatchJob"


def test_international_v2_calls_intl_v2_runner_with_country(patched_task, task_engine):
    m_v1, m_dv2, m_iv2 = _dispatch(
        patched_task, task_engine, "international", "v2", country="Germany"
    )

    m_v1.assert_not_called()
    m_dv2.assert_not_called()
    m_iv2.assert_called_once()
    args, kwargs = m_iv2.call_args
    assert args[0] == "Verflucht normal"
    assert args[2] is None  # theater
    # country is REQUIRED by run_agentic_match_intl_v2 (it raises on a blank
    # one) and comes from input_data, which validate_rows_for_market already
    # guarantees is present for international rows.
    assert kwargs["country"] == "Germany"
    assert "market" not in kwargs
    ctx = kwargs["usage_ctx"]
    assert ctx.call_path == "agentic_cli_v2"
    assert ctx.task_type == "intl_mapping"
    assert ctx.caller_type == "external_api"


def test_unknown_variant_degrades_to_v1(patched_task, task_engine):
    """Fail-safe: a value this build doesn't recognise must never land in an
    unhandled branch."""
    m_v1, m_dv2, m_iv2 = _dispatch(patched_task, task_engine, "domestic", "v99")

    m_v1.assert_called_once()
    m_dv2.assert_not_called()
    m_iv2.assert_not_called()


@pytest.mark.parametrize("variant", [None, "v2"])
def test_row_bookkeeping_does_not_depend_on_the_variant(
    patched_task, task_engine, variant
):
    """The variant must change only which matcher runs -- row status,
    counters and finalize behaviour are shared code and must not fork. Both
    parametrisations assert the SAME expected state, so a divergence in
    either direction fails.
    """
    _dispatch(patched_task, task_engine, "domestic", variant)

    with Session(task_engine) as s:
        row = s.exec(select(ApiTitleMatchRow)).one()
        job = s.get(ApiTitleMatchJob, "ext-v2-job")
    assert row.status == "completed"
    # Both runners are mocked to return a 0-id result, which
    # batch_io.resolve_present_in_db reports as absent with a blank title.
    assert row.mapped_title == ""
    assert row.present_in_db is False
    assert row.confidence == 0.9
    assert row.attempts == 1
    assert job.rows_processed == 1
    assert job.rows_no_match == 1
    assert job.rows_matched == 0
    assert job.rows_failed == 0
    patched_task.external_finalize_job.apply_async.assert_called_once_with(
        args=[None, "ext-v2-job"]
    )


@pytest.mark.parametrize(
    "stored,expected",
    [(None, "v1"), ("v1", "v1"), ("", "v1"), ("garbage", "v1"), ("v2", "v2")],
)
def test_variant_of(stored, expected):
    import app.tasks.external_match_task as task_mod

    job = ApiTitleMatchJob(id="j1", api_key_id="k1", market="domestic",
                           pipeline_variant=stored)
    assert task_mod._variant_of(job) == expected


# --------------------------------------------------------------------------
# 5. Migration round-trip
# --------------------------------------------------------------------------

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "b1c2d3e4f5a6_add_apititlematchjob_pipeline_variant.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig_b1c2d3e4f5a6", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, fn) -> None:
    """Execute a migration function against `engine` with alembic's global
    `op` proxy bound to a real MigrationContext -- the migration module uses
    `from alembic import op`, so it needs that proxy installed."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            fn()


def _columns(engine, table="apititlematchjob") -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


@pytest.fixture
def premigration_engine():
    """A DB at the state just before b1c2d3e4f5a6: the job table exists
    without pipeline_variant."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE apititlematchjob DROP COLUMN pipeline_variant"))
    assert "pipeline_variant" not in _columns(engine)
    yield engine
    engine.dispose()


def test_migration_revision_identifiers():
    module = _load_migration()
    assert module.revision == "b1c2d3e4f5a6"
    assert module.down_revision == "a1b2c3d4e5f7"  # the intl-batch-variant head


def test_migration_upgrade_adds_nullable_column(premigration_engine):
    module = _load_migration()
    _run(premigration_engine, module.upgrade)

    assert "pipeline_variant" in _columns(premigration_engine)
    col = next(
        c for c in inspect(premigration_engine).get_columns("apititlematchjob")
        if c["name"] == "pipeline_variant"
    )
    assert col["nullable"] is True
    # No backfill and no server_default: an existing row must read NULL,
    # which _variant_of treats as v1.
    assert col.get("default") is None


def test_migration_upgrade_is_idempotent(premigration_engine):
    """upgrade() guards on an inspector column check, so re-running it over an
    already-migrated DB is a no-op rather than a duplicate-column error."""
    module = _load_migration()
    _run(premigration_engine, module.upgrade)
    _run(premigration_engine, module.upgrade)
    assert "pipeline_variant" in _columns(premigration_engine)


def test_migration_downgrade_round_trip(premigration_engine):
    module = _load_migration()
    before = _columns(premigration_engine)

    _run(premigration_engine, module.upgrade)
    assert _columns(premigration_engine) == before | {"pipeline_variant"}

    _run(premigration_engine, module.downgrade)
    assert _columns(premigration_engine) == before


def test_existing_rows_survive_the_upgrade_reading_null(premigration_engine):
    """The real-world case: rows written before v2 existed must upgrade in
    place and read as v1."""
    from sqlalchemy import text

    with premigration_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO apititlematchjob "
                "(id, api_key_id, market, db_update, phase, rows_total, "
                " rows_processed, rows_matched, rows_no_match, rows_failed, created_at) "
                "VALUES ('legacy-1', 'k1', 'domestic', 0, 'completed', 1, 1, 1, 0, 0, "
                " '2026-09-01 00:00:00')"
            )
        )

    _load_migration_and_upgrade = _load_migration()
    _run(premigration_engine, _load_migration_and_upgrade.upgrade)

    import app.tasks.external_match_task as task_mod

    with Session(premigration_engine) as s:
        job = s.get(ApiTitleMatchJob, "legacy-1")
        assert job.pipeline_variant is None
        assert task_mod._variant_of(job) == "v1"

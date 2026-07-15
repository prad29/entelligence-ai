"""
End-to-end batch title-matching test in EAGER mode (fast, no broker).

This exercises the full data flow: dispatch_batch -> agentic_batch_row (per row)
-> finalize_batch -> status + download endpoints, with ``task_always_eager``.
run_agentic_match is monkeypatched to return deterministic TitleMatchResults
for a small set of synthetic rows:

  * one row resolves to a real movie_master_id present in MovieMaster  -> matched
  * one row resolves to id 0 (no match)                               -> no_match
  * one row raises AgenticTimeoutError on every call                  -> failed

Eager mode is deliberately paired with the real-broker chord/retry test in
tests/test_batch_chord_live.py: eager bypasses the broker + chord machinery
entirely, so it proves data-flow correctness but NOT chord/retry behavior.

Note on eager retries: with ``task_always_eager`` and
``task_eager_propagates`` left False, ``self.retry`` for the timeout row will
propagate/be caught inside the task's own handling. Regardless of the retry
path, the exhausted-agentic branch records the failed row and the batch
completes — which is exactly what we assert.
"""

from __future__ import annotations

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

import app.database as _db_module
from app.config import settings
from app.title_matching.agentic import AgenticTimeoutError
from app.title_matching.types import TitleMatchResult

# ── shared in-memory sqlite engine (same pattern as test_batch_title_match_api) ─
_sqlite_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
# Point BOTH the app-level engine and the task module's engine at this DB.
_db_module.engine = _sqlite_engine

from app.database import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import MovieMaster, MovieTitleBatchJob  # noqa: E402


def _override_get_session():
    with Session(_sqlite_engine) as session:
        yield session


app.dependency_overrides[get_session] = _override_get_session
SQLModel.metadata.create_all(_sqlite_engine)

client = TestClient(app, raise_server_exceptions=False)


# Synthetic rows: (title, expected_outcome). The runner maps title -> result.
_MATCHED_TITLE = "The Matrix"
_NOMATCH_TITLE = "Totally Unknown Film"
_FAILED_TITLE = "Times Out Movie"
_MATCHED_MOVIE_ID = 4242


def _synthetic_result(title: str) -> TitleMatchResult:
    if title == _MATCHED_TITLE:
        return TitleMatchResult(
            suggested_movie_id=_MATCHED_MOVIE_ID,
            suggested_movie_title="The Matrix",
            canonical_movie_id=_MATCHED_MOVIE_ID,
            confidence=0.96,
            decision="AUTO_ACCEPT",
            reasoning="exact alias match",
            evidence={},
        )
    if title == _NOMATCH_TITLE:
        return TitleMatchResult(
            suggested_movie_id=0,
            suggested_movie_title="",
            canonical_movie_id=0,
            confidence=0.1,
            decision="REVIEW",
            reasoning="no candidate above threshold",
            evidence={},
        )
    raise AssertionError(f"unexpected title in synthetic runner: {title!r}")


def _fake_run_agentic_match(title, show_date, theater, ticketing_url, use_poster_vision):
    # theater is always None in the batch path.
    assert theater is None
    if title == _FAILED_TITLE:
        raise AgenticTimeoutError("synthetic timeout for the failed-row path")
    return _synthetic_result(title)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Fresh DB rows + Mode B enabled + deterministic runner + eager Celery."""
    monkeypatch.setattr(settings, "AGENTIC_TITLE_MATCH_ENABLED", True)

    # Re-assert THIS test's engine + dependency override at setup time. Sibling
    # test modules (e.g. test_batch_title_match_api.py) also patch
    # app.database.engine and app.dependency_overrides[get_session] at import
    # time; whichever imported last would otherwise win and the endpoint would
    # read/write a different engine than _get_job() here. monkeypatch restores
    # the previous binding on teardown so we never clobber other modules.
    monkeypatch.setattr(_db_module, "engine", _sqlite_engine, raising=False)
    monkeypatch.setitem(app.dependency_overrides, get_session, _override_get_session)

    # Wipe tables between tests for isolation.
    with Session(_sqlite_engine) as session:
        from sqlmodel import select

        for model in (MovieTitleBatchJob, MovieMaster):
            for row in session.exec(select(model)).all():
                session.delete(row)
        session.commit()
        # Seed the matched movie.
        session.add(MovieMaster(id=_MATCHED_MOVIE_ID, movie_title="The Matrix"))
        session.commit()

    # Patch the runner used by the per-row task.
    import app.title_matching.agentic.runner as runner_mod

    monkeypatch.setattr(runner_mod, "run_agentic_match", _fake_run_agentic_match)

    # Patch the sandbox semaphore so it never touches Redis in the eager test.
    import app.title_matching.sandbox_semaphore as sem

    monkeypatch.setattr(sem, "acquire", lambda *a, **k: "eager-holder")
    monkeypatch.setattr(sem, "release", lambda *a, **k: None)

    # Never touch real S3 — batch_storage backed by an in-memory dict.
    import app.title_matching.batch_storage as storage_mod

    s3_store: dict[str, bytes] = {}
    monkeypatch.setattr(storage_mod, "put_bytes", lambda key, data: s3_store.__setitem__(key, data))
    monkeypatch.setattr(storage_mod, "get_bytes", lambda key: s3_store[key])
    monkeypatch.setattr(storage_mod, "delete", lambda key: s3_store.pop(key, None))
    monkeypatch.setattr(storage_mod, "exists", lambda key: key in s3_store)

    # Use a real (local) Redis for the per-row results hash; fall back to an
    # in-memory dict if it is unreachable so the test still runs anywhere.
    import app.tasks.agentic_match_task as task_mod

    store: dict[str, dict[str, str]] = {}

    def _fake_store(job_id, row_index, row_result):
        import json

        store.setdefault(job_id, {})[str(row_index)] = json.dumps(row_result)

    monkeypatch.setattr(task_mod, "_store_row_result", _fake_store)

    # finalize_batch reads the hash via _get_redis().hgetall — stub that read to
    # our in-memory store so the eager test needs no live Redis at all.
    class _FakeRedis:
        def hgetall(self, key):
            job_id = key.split(":")[1]
            return {k.encode(): v.encode() for k, v in store.get(job_id, {}).items()}

        def delete(self, *a, **k):
            return None

    monkeypatch.setattr(task_mod, "_get_redis", lambda: _FakeRedis())

    # Eager execution: run tasks inline, no broker.
    from app.celery_app import celery

    prev_eager = celery.conf.task_always_eager
    celery.conf.task_always_eager = True
    yield
    celery.conf.task_always_eager = prev_eager


def _csv_upload() -> bytes:
    lines = [
        "movie_title,show_date,ticketing_url",
        f"{_MATCHED_TITLE},2024-01-01,https://example.com/matrix",
        f"{_NOMATCH_TITLE},2024-02-02,https://example.com/unknown",
        f"{_FAILED_TITLE},2024-03-03,https://example.com/timeout",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


def _get_job(job_id: str) -> MovieTitleBatchJob:
    with Session(_sqlite_engine) as session:
        return session.get(MovieTitleBatchJob, job_id)


def test_eager_batch_end_to_end_output_and_counters():
    # Upload -> dispatch runs eagerly to completion inside this call.
    resp = client.post(
        "/api/v1/movie-title-match/batch",
        files={"file": ("titles.csv", _csv_upload(), "text/csv")},
        data={"use_poster_vision": "false"},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    # ── job row counters ──────────────────────────────────────────────────────
    job = _get_job(job_id)
    assert job is not None
    assert job.status == "completed", f"status={job.status} error={job.error}"
    assert job.total == 3
    assert job.processed == 3
    assert job.matched == 1
    assert job.no_match == 1
    assert job.failed == 1
    # Invariants required by the plan.
    assert job.matched + job.no_match + job.failed == job.total
    assert job.processed == job.total

    # ── status endpoint ───────────────────────────────────────────────────────
    status = client.get(f"/api/v1/movie-title-match/batch/{job_id}").json()
    assert status["status"] == "completed"
    assert status["progress"] == 1.0
    assert status["matched"] == 1
    assert status["no_match"] == 1
    assert status["failed"] == 1
    assert status["output_url"] == f"/api/v1/movie-title-match/batch/{job_id}/download"

    # ── download endpoint returns a valid xlsx with the right shape ────────────
    dl = client.get(f"/api/v1/movie-title-match/batch/{job_id}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    wb = openpyxl.load_workbook(io.BytesIO(dl.content))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])

    # Original columns preserved in order, then the 4 appended columns in order.
    assert header == [
        "movie_title",
        "show_date",
        "ticketing_url",
        "mapped_title",
        "confidence_score",
        "reasoning",
        "present_in_db",
    ]

    # Index the data rows by the original movie_title for assertions.
    by_title = {r[0]: r for r in rows[1:]}
    assert set(by_title) == {_MATCHED_TITLE, _NOMATCH_TITLE, _FAILED_TITLE}

    matched_row = by_title[_MATCHED_TITLE]
    assert matched_row[3] == "The Matrix"          # mapped_title
    assert matched_row[6] == "Yes"                 # present_in_db

    nomatch_row = by_title[_NOMATCH_TITLE]
    # openpyxl reads an empty-string cell back as None; both mean "blank".
    assert nomatch_row[3] in (None, "", "NO MATCH")  # mapped_title blank
    assert nomatch_row[6] == "No"                    # present_in_db

    failed_row = by_title[_FAILED_TITLE]
    assert failed_row[3] in (None, "")             # mapped_title blank
    assert failed_row[4] == 0                      # confidence_score == 0
    assert str(failed_row[5]).startswith("error")  # reasoning starts with "error"
    assert failed_row[6] == "No"                   # present_in_db

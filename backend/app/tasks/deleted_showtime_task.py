"""
Celery tasks for the Deleted Showtimes Check pipeline (ported from
showtime_serp_check.py's ThreadPoolExecutor-based `run()`).

Three moving parts, mirroring agentic_match_task.py's chord shape:

* :func:`process_batch` — one Celery task per (theater, show_date) batch.
  Calls SerpApi (with the script's retry/fallback ladder), decides verdicts
  for every row in the batch, stashes results in a Redis hash, and bumps job
  counters. Tracks `consecutive_failures` in the DB and sets `job.aborted`
  once the threshold is hit (mirrors the script's --abort-after guardrail)
  or immediately on a SerpApi auth failure (401/403) — either way, this task
  itself never raises, so a chord header task failing here can never wedge
  the job (same reasoning as agentic_batch_row's broad catch).
* :func:`finalize_job` — chord callback. Assembles every row result, writes
  the output workbook + audit.json, marks the job completed (or failed, if
  `job.aborted` was set), then cleans up the upload + Redis hash. Idempotent.
* :func:`dispatch_job` / :func:`dispatch_job_task` — build and enqueue the
  chord; the Celery wrapper is what the upload endpoint calls via `.delay()`
  so the HTTP response returns immediately regardless of file size.

Counter updates use server-side `column = column + N` SQL expressions, never
a Python read-modify-write (see agentic_match_task.py's _bump_counters and
LOCKED product decision #10 in this codebase).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import update
from sqlmodel import Session

from app.celery_app import celery
from app.config import settings
from app.deleted_showtimes import batch_io, job_semaphore, storage
from app.deleted_showtimes.core import (
    FALSE_,
    RETRYABLE_MISSES,
    TRUE_,
    UNKNOWN_,
    Listing,
    ShowtimeRow,
    build_query,
    decide_rows,
    parse_theater_listing,
    short_theater_name,
)
from app.deleted_showtimes.serp_client import SerpAuthError, SerpError
from app.deleted_showtimes.serp_key_rotation import AllKeysExhaustedError, RotatingSerpClient

logger = logging.getLogger(__name__)

QUEUE = "deleted-showtimes"


def _results_key(job_id: str) -> str:
    return f"deleted-showtimes:{job_id}:results"


def _get_redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL)


def _bump_counters(session: Session, job_id: str, **increments: int) -> None:
    from app.models import DeletedShowtimeJob

    values = {
        col: getattr(DeletedShowtimeJob, col) + delta
        for col, delta in increments.items()
    }
    session.execute(
        update(DeletedShowtimeJob).where(DeletedShowtimeJob.id == job_id).values(**values)
    )
    session.commit()


def _store_row_result(job_id: str, row_index: int, row: ShowtimeRow) -> None:
    payload = {
        "verdict": row.verdict,
        "reason": row.reason,
        "published": row.published,
        "nearest": row.nearest,
        "source_query": row.source_query,
        "theater_verified": row.theater_verified,
        "google_theater": row.google_theater,
        "google_address": row.google_address,
    }
    _get_redis().hset(_results_key(job_id), str(row_index), json.dumps(payload))


def _record_batch_failure(session: Session, job_id: str) -> bool:
    """Atomically bump consecutive_failures; returns True if the abort
    threshold was just crossed by THIS increment."""
    from app.models import DeletedShowtimeJob

    session.execute(
        update(DeletedShowtimeJob)
        .where(DeletedShowtimeJob.id == job_id)
        .values(consecutive_failures=DeletedShowtimeJob.consecutive_failures + 1)
    )
    session.commit()
    job = session.get(DeletedShowtimeJob, job_id)
    if job is None:
        return False
    return job.consecutive_failures >= settings.DELETED_SHOWTIME_ABORT_AFTER


def _record_batch_success(session: Session, job_id: str) -> None:
    from app.models import DeletedShowtimeJob

    session.execute(
        update(DeletedShowtimeJob).where(DeletedShowtimeJob.id == job_id).values(consecutive_failures=0)
    )
    session.commit()


def _mark_aborted(session: Session, job_id: str, reason: str) -> None:
    from app.models import DeletedShowtimeJob

    session.execute(
        update(DeletedShowtimeJob).where(DeletedShowtimeJob.id == job_id).values(aborted=True, error=reason)
    )
    session.commit()


def _attempt(client: RotatingSerpClient, theater: str, target: date, today: date, aliases: Dict[str, str],
             q: str, require_theater_verify: bool, strict_screens: bool) -> Listing:
    params = {"engine": "google", "q": q, "hl": "en", "gl": "us", "device": "desktop"}
    try:
        data = client.search(params)
    except SerpAuthError:
        raise
    except SerpError as e:
        return Listing(ok=False, reason=f"API_ERROR: {e}"[:200], query=q)
    got = parse_theater_listing(
        data, theater, target, today, aliases,
        require_theater_verify=require_theater_verify, strict_screens=strict_screens,
    )
    got.query = q
    return got


@celery.task(
    bind=True,
    name="app.tasks.deleted_showtime_task.process_batch",
    queue=QUEUE,
    max_retries=0,
    soft_time_limit=180,
    time_limit=240,
)
def process_batch(
    self,
    job_id: str,
    theater: str,
    show_date_iso: str,
    row_indices: List[int],
    row_payloads: List[Dict[str, Any]],
) -> None:
    """Process one (theater, show_date) batch: one SerpApi lookup (plus
    fallback retries) decides the verdict for every row in the batch.

    `row_payloads[i]` corresponds to `row_indices[i]` and carries the fields
    ShowtimeRow needs (title, show_time_raw, show_min) as plain JSON-safe
    values — Celery task args must be serializable, so ShowtimeRow objects
    themselves are rebuilt here rather than passed directly.

    This task must NEVER raise, escaped or otherwise: it is a chord header
    task, and a chord's callback (finalize_job) only fires once ALL header
    tasks succeed. An uncaught exception here — a semaphore acquire timeout,
    an unexpected SerpApi response shape, a soft time limit, a Redis/DB
    hiccup — would wedge the job at status="processing" forever with no
    recovery path. The whole body below is therefore wrapped in a single
    broad `except BaseException`, mirroring agentic_batch_row's identical
    guard in agentic_match_task.py.
    """
    from app.database import engine
    from app.models import DeletedShowtimeJob

    target = date.fromisoformat(show_date_iso)
    today = datetime.utcnow().date()

    batch = [
        ShowtimeRow(
            key=idx,
            theater=theater,
            title=p["title"],
            show_date=target,
            show_time_raw=p["show_time_raw"],
            show_min=p["show_min"],
        )
        for idx, p in zip(row_indices, row_payloads)
    ]

    holder = None
    try:
        with Session(engine) as session:
            job = session.get(DeletedShowtimeJob, job_id)
            if job is None:
                logger.error("process_batch: job %s not found", job_id)
                return
            if job.aborted:
                # A prior batch already tripped the abort guardrail — skip
                # the SerpApi call for this batch, but still record + count
                # these rows so `processed` reaches `total` and the chord
                # can finalize.
                for r in batch:
                    r.verdict, r.reason = UNKNOWN_, "RUN_ABORTED"
                for idx, r in zip(row_indices, batch):
                    _store_row_result(job_id, idx, r)
                _bump_counters(session, job_id, processed=len(batch), unknown_count=len(batch))
                return

            require_verify = job.theater_verify == "strict"
            strict_screens = job.strict_screen_count
            title_missing_is_deleted = job.title_missing_is_deleted
            fallback_mode = job.fallback
            max_concurrency = max(job.workers, 1)

        client = RotatingSerpClient()
        holder = job_semaphore.acquire(job_id, max_concurrency, timeout=120)

        lst = _attempt(client, theater, target, today, {}, build_query(theater, "bare"),
                       require_verify, strict_screens)

        if not lst.ok and fallback_mode != "off" and lst.reason in RETRYABLE_MISSES:
            short = short_theater_name(theater)
            title_counts: Dict[str, int] = {}
            for p in row_payloads:
                title_counts[p["title"]] = title_counts.get(p["title"], 0) + 1
            dominant_title = max(title_counts, key=title_counts.get) if title_counts else ""

            if fallback_mode == "plain":
                plan = [("plain", build_query(theater, "plain"))]
            elif fallback_mode == "movie":
                plan = [("movie", f"{dominant_title} showtimes {theater}")]
            else:  # auto
                plan = [("plain", build_query(theater, "plain"))]
                if short:
                    plan += [("short", short), ("short+showtimes", f"{short} showtimes")]
                plan += [("movie", f"{dominant_title} showtimes {short or theater}")]

            first_reason = lst.reason
            for label, alt_q in plan[:3]:
                alt = _attempt(client, theater, target, today, {}, alt_q, require_verify, strict_screens)
                if alt.ok:
                    alt.reason = f"VIA_FALLBACK ({label}) after {first_reason}"
                    lst = alt
                    break

        decide_rows(batch, lst, title_missing_is_deleted)

        tally = {TRUE_: 0, FALSE_: 0, UNKNOWN_: 0}
        for idx, r in zip(row_indices, batch):
            _store_row_result(job_id, idx, r)
            tally[r.verdict] = tally.get(r.verdict, 0) + 1

        with Session(engine) as session:
            _bump_counters(
                session, job_id,
                processed=len(batch),
                true_count=tally.get(TRUE_, 0),
                false_count=tally.get(FALSE_, 0),
                unknown_count=tally.get(UNKNOWN_, 0),
            )
            if lst.ok:
                _record_batch_success(session, job_id)
            else:
                crossed = _record_batch_failure(session, job_id)
                if crossed:
                    _mark_aborted(
                        session, job_id,
                        f"Aborted: {settings.DELETED_SHOWTIME_ABORT_AFTER} consecutive failed "
                        f"theater batches (last reason: {lst.reason})",
                    )
    except AllKeysExhaustedError as exc:
        with Session(engine) as session:
            _mark_aborted(session, job_id, f"All SerpApi keys are exhausted or rate-limited: {exc}")
            for r in batch:
                r.verdict, r.reason = UNKNOWN_, "RUN_ABORTED"
            for idx, r in zip(row_indices, batch):
                _store_row_result(job_id, idx, r)
            _bump_counters(session, job_id, processed=len(batch), unknown_count=len(batch))
    except SerpAuthError as exc:
        with Session(engine) as session:
            _mark_aborted(session, job_id, f"SerpApi rejected the key: {exc}")
            for r in batch:
                r.verdict, r.reason = UNKNOWN_, "RUN_ABORTED"
            for idx, r in zip(row_indices, batch):
                _store_row_result(job_id, idx, r)
            _bump_counters(session, job_id, processed=len(batch), unknown_count=len(batch))
    except BaseException as exc:  # noqa: BLE001
        # Any other failure (semaphore acquire TimeoutError, SoftTimeLimitExceeded,
        # an unexpected SerpApi response shape, a Redis/DB hiccup, or any
        # unexpected exception) must NOT escape this task — see the docstring
        # above. Record every row in this batch as failed/unknown and bump a
        # consecutive-failure count, exactly as a normal SerpError miss would.
        logger.exception("process_batch failed (non-auth) job=%s theater=%r date=%s",
                          job_id, theater, show_date_iso)
        try:
            for r in batch:
                r.verdict, r.reason = UNKNOWN_, f"BATCH_TASK_ERROR: {exc}"[:200]
            with Session(engine) as session:
                for idx, r in zip(row_indices, batch):
                    _store_row_result(job_id, idx, r)
                _bump_counters(session, job_id, processed=len(batch), unknown_count=len(batch))
                crossed = _record_batch_failure(session, job_id)
                if crossed:
                    _mark_aborted(
                        session, job_id,
                        f"Aborted: {settings.DELETED_SHOWTIME_ABORT_AFTER} consecutive failed "
                        f"theater batches (last reason: BATCH_TASK_ERROR)",
                    )
        except Exception:  # noqa: BLE001 - last-resort: never re-raise from here
            logger.exception(
                "process_batch: could not even record batch failure job=%s theater=%r",
                job_id, theater,
            )
    finally:
        job_semaphore.release(holder)


@celery.task(name="app.tasks.deleted_showtime_task.finalize_job", queue=QUEUE)
def finalize_job(_batch_results, job_id: str) -> None:
    """Chord callback: assemble results, write output + audit, complete (or
    fail, if aborted) the job, clean up. Idempotent — a no-op if the job is
    already in a terminal status."""
    from app.database import engine
    from app.models import DeletedShowtimeJob

    with Session(engine) as session:
        job = session.get(DeletedShowtimeJob, job_id)
        if job is None:
            logger.error("finalize_job: job %s not found", job_id)
            return
        if job.status in ("completed", "failed"):
            logger.info("finalize_job: job %s already %s, no-op", job_id, job.status)
            return

        if job.aborted:
            job.status = "failed"
            job.error = job.error or "Run aborted"
            session.add(job)
            session.commit()
            try:
                storage.delete(job.file_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("finalize_job: could not remove upload %s: %s", job.file_path, exc)
            try:
                _get_redis().delete(_results_key(job_id))
            except Exception as exc:  # noqa: BLE001
                logger.warning("finalize_job: could not delete redis hash for %s: %s", job_id, exc)
            return

        total = job.total or 0
        upload_key = job.file_path

    contents = storage.get_bytes(upload_key)
    ext = os.path.splitext(upload_key)[1]
    original_headers, rows = batch_io.parse_upload(contents, ext)
    showtime_rows = batch_io.rows_to_showtime_rows(original_headers, rows)

    r = _get_redis()
    raw = r.hgetall(_results_key(job_id))
    stored = {int(k.decode() if isinstance(k, bytes) else k): v for k, v in raw.items()}
    for i in range(total):
        val = stored.get(i)
        if val is None:
            showtime_rows[i].verdict = UNKNOWN_
            showtime_rows[i].reason = "row result missing - task may have crashed without reporting"
            continue
        data = json.loads(val.decode() if isinstance(val, bytes) else val)
        sr = showtime_rows[i]
        sr.verdict = data["verdict"]
        sr.reason = data["reason"]
        sr.published = data["published"]
        sr.nearest = data["nearest"]
        sr.source_query = data["source_query"]
        sr.theater_verified = data["theater_verified"]
        sr.google_theater = data["google_theater"]
        sr.google_address = data["google_address"]

    xlsx_bytes = batch_io.build_output_xlsx(original_headers, rows, showtime_rows)
    output_key = storage.output_key(job_id)
    storage.put_bytes(output_key, xlsx_bytes)

    audit_payload = {
        "job_id": job_id,
        "counts": {
            "TRUE": sum(1 for r in showtime_rows if r.verdict == TRUE_),
            "FALSE": sum(1 for r in showtime_rows if r.verdict == FALSE_),
            "UNABLE_TO_DETERMINE": sum(1 for r in showtime_rows if r.verdict == UNKNOWN_),
        },
        "rows": [
            {
                "row": r.key + 2,
                "theater": r.theater,
                "title": r.title,
                "show_date": str(r.show_date or ""),
                "show_time": r.show_time_raw,
                "verdict": r.verdict,
                "reason": r.reason,
                "published": r.published,
                "query": r.source_query,
            }
            for r in showtime_rows
        ],
    }
    audit_key = storage.audit_key(job_id)
    storage.put_bytes(audit_key, json.dumps(audit_payload, indent=2).encode("utf-8"))

    with Session(engine) as session:
        job = session.get(DeletedShowtimeJob, job_id)
        job.status = "completed"
        job.output_path = output_key
        job.audit_output_path = audit_key
        job.ttl = datetime.utcnow() + timedelta(hours=settings.DELETED_SHOWTIME_JOB_TTL_HOURS)
        session.add(job)
        session.commit()

    try:
        storage.delete(upload_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_job: could not remove upload %s: %s", upload_key, exc)
    try:
        r.delete(_results_key(job_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_job: could not delete redis hash for %s: %s", job_id, exc)


@celery.task(name="app.tasks.deleted_showtime_task.dispatch_job_task", queue=QUEUE)
def dispatch_job_task(job_id: str) -> None:
    """Celery task wrapper around :func:`dispatch_job` — the upload endpoint
    enqueues this instead of calling it inline, for the same ALB/nginx
    idle-timeout reason documented in agentic_match_task.dispatch_batch_task.
    """
    dispatch_job(job_id)


def dispatch_job(job_id: str) -> None:
    """Build and apply the chord of per-batch tasks + finalize callback."""
    from celery import chord, group

    from app.database import engine
    from app.models import DeletedShowtimeJob

    try:
        with Session(engine) as session:
            job = session.get(DeletedShowtimeJob, job_id)
            if job is None:
                raise ValueError(f"dispatch_job: job {job_id} not found")
            upload_key = job.file_path

        contents = storage.get_bytes(upload_key)
        ext = os.path.splitext(upload_key)[1]
        headers, rows = batch_io.parse_upload(contents, ext)
        showtime_rows = batch_io.rows_to_showtime_rows(headers, rows)

        with Session(engine) as session:
            session.execute(
                update(DeletedShowtimeJob)
                .where(DeletedShowtimeJob.id == job_id)
                .values(status="processing", total=len(showtime_rows))
            )
            session.commit()

        batches: Dict[Tuple[str, Optional[date]], List[Tuple[int, ShowtimeRow]]] = {}
        immediate_unknown: List[int] = []
        for idx, r in enumerate(showtime_rows):
            if not r.theater or r.show_date is None:
                immediate_unknown.append(idx)
                continue
            batches.setdefault((r.theater, r.show_date), []).append((idx, r))

        batch_sigs = [
            process_batch.s(
                job_id,
                theater,
                target.isoformat(),
                [idx for idx, _r in items],
                [{"title": r.title, "show_time_raw": r.show_time_raw, "show_min": r.show_min}
                 for _idx, r in items],
            )
            for (theater, target), items in batches.items()
        ]

        if immediate_unknown:
            with Session(engine) as session:
                _bump_counters(
                    session, job_id,
                    processed=len(immediate_unknown),
                    unknown_count=len(immediate_unknown),
                )
                for idx in immediate_unknown:
                    row = showtime_rows[idx]
                    row.verdict, row.reason = UNKNOWN_, "MISSING_THEATER_OR_DATE"
                    _store_row_result(job_id, idx, row)

        if not batch_sigs:
            finalize_job.delay(None, job_id)
        else:
            chord(group(batch_sigs))(finalize_job.s(job_id))
    except Exception as exc:
        logger.exception("dispatch_job failed for job %s", job_id)
        try:
            with Session(engine) as session:
                session.execute(
                    update(DeletedShowtimeJob)
                    .where(DeletedShowtimeJob.id == job_id)
                    .values(status="failed", error=str(exc))
                )
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("dispatch_job: could not mark job %s failed", job_id)
        raise

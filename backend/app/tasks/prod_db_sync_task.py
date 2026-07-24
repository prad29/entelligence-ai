"""
Celery tasks for the "Sync from Production DB" feature: pull rows from the
production MySQL tables (fq_movie_master / fq_movie_master_intl) and upsert
them into this app's own MovieMaster / MovieMasterIntl tables, then trigger
the existing Vespa reindex tasks — the same upsert+reindex sequence the CSV
seed endpoints already run, just sourced from a live DB instead of a file.

Reuses seed_loader.py's upsert functions unchanged; this module only adds
the MySQL fetch -> chunk -> upsert -> reindex-trigger orchestration plus
MovieMasterSyncJob progress tracking.

One Celery task per market, each running its full fetch+upsert+reindex
sequence sequentially (no per-row chord/fan-out) — unlike the agentic
batch-match tasks, there's no per-row external API call driving cost/latency
here, just a DB-to-DB fetch+upsert.

Domestic-only concern: refreshing the in-memory fuzzy/alias TitleMatchEngine
after a sync. That engine lives on FastAPI's app.state, which this Celery
worker process cannot reach directly. Reuses the same Redis-signal pattern
semantic_tasks.py already established for exactly this kind of cross-process
handoff (see _READY_KEY there and _attach_semantic_index_when_ready in
main.py) rather than inventing a new mechanism: this task sets
MOVIE_MASTER_SYNC_DIRTY_KEY on completion, and a FastAPI-side watcher
(added in main.py) rebuilds the engine when it sees that key set, then
clears it. This is a distinct concern from the existing semantic-index-ready
signal, which only attaches a Vespa index reference onto an already-built
engine and does not reload the fuzzy/alias master_rows snapshot.
"""

from __future__ import annotations

import logging

from sqlmodel import Session

from app.celery_app import celery
from app.config import settings
from app.title_matching import prod_db
from app.title_matching.seed_loader import seed_from_rows, seed_intl_from_rows

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 5000

MOVIE_MASTER_SYNC_DIRTY_KEY = "movie_master_sync:dirty"

_GENERIC_ERROR_MESSAGE = (
    "Production DB connection or upsert failed for job_id={job_id} — check server logs."
)


def _get_redis():
    import redis

    return redis.Redis.from_url(settings.REDIS_URL)


def _load_job(session: Session, job_id: str):
    from app.models import MovieMasterSyncJob

    return session.get(MovieMasterSyncJob, job_id)


def _mark_job_failed(job_id: str) -> None:
    """Mark a sync job "failed" using a brand-new session/connection —
    called from the outermost except block, where the session that raised
    may itself be unusable."""
    from app.database import engine as db_engine

    try:
        with Session(db_engine) as session:
            job = _load_job(session, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error = _GENERIC_ERROR_MESSAGE.format(job_id=job_id)
            session.add(job)
            session.commit()
    except Exception:
        logger.exception("prod_db_sync: job_id=%r failed to mark job as failed", job_id)


def _chunked(iterator, size: int):
    """Yield lists of up to `size` items from `iterator`."""
    chunk: list = []
    for item in iterator:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


@celery.task(
    bind=True,
    name="app.tasks.prod_db_sync_task.sync_movie_master",
    max_retries=1,
    acks_late=True,
)
def sync_movie_master_task(self, job_id: str) -> None:
    """Sync MovieMaster from fq_movie_master. max_retries=1 (not 3, unlike
    semantic_tasks.py's Bedrock-throttling retries) — a MySQL connection
    failure retried repeatedly with backoff would tie up the queue for
    minutes for what's likely a persistent outage, not a transient blip."""
    from app.database import engine as db_engine

    try:
        with Session(db_engine) as session:
            job = _load_job(session, job_id)
            if job is None:
                logger.warning("prod_db_sync: job_id=%r not found, aborting", job_id)
                return

            job.status = "processing"
            job.total = prod_db.fetch_fq_movie_master_count()
            session.add(job)
            session.commit()

            inserted = updated = skipped = processed = 0
            for chunk in _chunked(prod_db.fetch_fq_movie_master_rows(), _CHUNK_SIZE):
                result = seed_from_rows(session, chunk)
                inserted += result["inserted"]
                updated += result["updated"]
                skipped += result["skipped"]
                processed += len(chunk)

                job.processed = processed
                job.inserted = inserted
                job.updated = updated
                job.skipped = skipped
                session.add(job)
                session.commit()

            job.status = "completed"
            session.add(job)
            session.commit()

            if inserted > 0 or updated > 0:
                try:
                    _get_redis().set(MOVIE_MASTER_SYNC_DIRTY_KEY, "1")
                    logger.info(
                        "prod_db_sync: signalled '%s' for engine refresh",
                        MOVIE_MASTER_SYNC_DIRTY_KEY,
                    )
                except Exception as exc:
                    logger.warning("prod_db_sync: failed to signal Redis dirty key: %s", exc)

                from app.tasks.semantic_tasks import build_semantic_index_task
                build_semantic_index_task.delay()

            logger.info(
                "prod_db_sync: job_id=%r completed inserted=%d updated=%d skipped=%d",
                job_id, inserted, updated, skipped,
            )

    except Exception:
        # Deliberately outside the `with Session(...)` block above: the
        # exception that lands here (e.g. a dropped connection) may have
        # come from that same session, leaving its transaction unusable. A
        # fresh session guarantees the job can still be marked "failed" —
        # without this, a job could be stuck at "queued"/"processing"
        # forever and permanently block the in-flight-job guard in
        # movie_title_match.py from ever starting a new sync for this market.
        logger.exception("prod_db_sync: job_id=%r failed", job_id)
        _mark_job_failed(job_id)


@celery.task(
    bind=True,
    name="app.tasks.prod_db_sync_task.sync_movie_master_intl",
    max_retries=1,
    acks_late=True,
)
def sync_movie_master_intl_task(self, job_id: str) -> None:
    """Sync MovieMasterIntl from fq_movie_master_intl. No engine-reload
    concern here — international has no equivalent in-memory
    TitleMatchEngine reload path today (build_title_match_engine only reads
    MovieMaster, per title_matching/loader.py)."""
    from app.database import engine as db_engine

    try:
        with Session(db_engine) as session:
            job = _load_job(session, job_id)
            if job is None:
                logger.warning("prod_db_sync_intl: job_id=%r not found, aborting", job_id)
                return

            job.status = "processing"
            job.total = prod_db.fetch_fq_movie_master_intl_count()
            session.add(job)
            session.commit()

            inserted = updated = skipped = skipped_undefined_country = processed = 0
            for chunk in _chunked(prod_db.fetch_fq_movie_master_intl_rows(), _CHUNK_SIZE):
                result = seed_intl_from_rows(session, chunk)
                inserted += result["inserted"]
                updated += result["updated"]
                skipped += result["skipped"]
                skipped_undefined_country += result["skipped_undefined_country"]
                processed += len(chunk)

                job.processed = processed
                job.inserted = inserted
                job.updated = updated
                job.skipped = skipped
                job.skipped_undefined_country = skipped_undefined_country
                session.add(job)
                session.commit()

            job.status = "completed"
            session.add(job)
            session.commit()

            if inserted > 0 or updated > 0:
                from app.tasks.semantic_tasks import build_semantic_index_intl_task
                build_semantic_index_intl_task.delay()

            logger.info(
                "prod_db_sync_intl: job_id=%r completed inserted=%d updated=%d "
                "skipped=%d skipped_undefined_country=%d",
                job_id, inserted, updated, skipped, skipped_undefined_country,
            )

    except Exception:
        # See sync_movie_master_task's matching except block for why this
        # lives outside the `with Session(...)` block and uses a fresh one.
        logger.exception("prod_db_sync_intl: job_id=%r failed", job_id)
        _mark_job_failed(job_id)

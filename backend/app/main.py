from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import configure_logging
from app.routers import detect, amenities, circuits, review, jobs
from app.routers import settings as settings_router
from app.routers import movie_detect, movie_formats, movie_review, movie_jobs
from app.routers import movie_title_match
from app.routers import external_title_match
from app.routers import deleted_showtimes
from app.routers import intl_detect, intl_amenities, intl_jobs
from app.routers import usage
from app.routers import lobby_check

# Configure structured JSON logging as early as possible
configure_logging()

app = FastAPI(
    title="Amenity Screen Format Detector",
    description="Detect cinema screen formats from amenity strings.",
    version="0.3.0",
    openapi_tags=[
        {
            "name": "external-title-match",
            "description": (
                "External, API-key-authenticated surface for movie title matching. Submit one "
                "(POST /singletitle) or many (POST /batchtitle) rows for asynchronous AI matching "
                "against Movie Master, then poll /external/jobs/{job_id} for status and "
                "/external/jobs/{job_id}/results for row-level results. Every request requires "
                "an x-api-key header. This is a parallel surface to the internal Excel-upload "
                "flow under movie-title-match — both delegate to the same matching core."
            ),
        },
        {
            "name": "lobby-check",
            "description": (
                "External, API-key-authenticated surface for cinema-lobby marketing-material "
                "image extraction (Qwen 3-VL on Bedrock). Submit one or many S3 image links "
                "(POST /api/v1/lobby-check), then poll /api/v1/lobby-check/jobs/{job_id} for "
                "status and /api/v1/lobby-check/jobs/{job_id}/results for per-image results. "
                "Every request requires an x-api-key header — the SAME key used for the "
                "external-title-match surface above (singletitle/batchtitle), not a separate one. "
                "The two surfaces track concurrent-job limits independently even though they "
                "share one key."
            ),
        },
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(detect.router)
app.include_router(amenities.router)
app.include_router(circuits.router)
app.include_router(review.router)
app.include_router(settings_router.router)
app.include_router(jobs.router)
app.include_router(movie_detect.router)
app.include_router(movie_formats.router)
app.include_router(movie_review.router)
app.include_router(movie_jobs.router)
app.include_router(movie_title_match.router)
app.include_router(deleted_showtimes.router)
app.include_router(intl_detect.router)
app.include_router(intl_amenities.router)
app.include_router(intl_jobs.router)
app.include_router(usage.router)

if settings.EXTERNAL_API_ENABLED:
    app.include_router(external_title_match.router)

if settings.LOBBY_CHECK_ENABLED:
    app.include_router(lobby_check.router)


_DEFAULT_MOVIE_FORMAT_SEEDS = [
    ("70mm", "70MM", 1),
    ("35mm", "35MM", 2),
    ("3d", "3D", 3),
    ("2d", "2D", 4),
]


def _seed_default_movie_formats(session) -> None:
    from sqlmodel import select
    from app.models import MovieFormatMapping
    exists = session.exec(select(MovieFormatMapping).limit(1)).first()
    if exists:
        return
    for keyword, fmt, tier in _DEFAULT_MOVIE_FORMAT_SEEDS:
        session.add(MovieFormatMapping(
            keyword=keyword,
            format=fmt,
            priority_tier=tier,
            status="approved",
        ))
    session.commit()


def _seed_api_key(
    session,
    raw_key: str,
    *,
    label: str,
    db_update_allowed: bool = False,
    max_rows_per_batch: "int | None" = None,
) -> None:
    """
    If `raw_key` is non-empty, ensure a matching ApiKey row exists —
    idempotent, so this can run on every startup without duplicating rows
    or fighting a manually-created key.

    This is how an operator's key gets from .env (locally) or Secrets
    Manager (in production) into the hashed ApiKey table the require_api_key*
    dependencies check against, without ever hand-writing a raw INSERT. The
    env var only ever holds the plaintext; only the hash is persisted.
    """
    if not raw_key:
        return

    from sqlmodel import select
    from app.dependencies.api_auth import hash_api_key
    from app.models import ApiKey

    key_hash = hash_api_key(raw_key)
    existing = session.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()
    if existing is not None:
        return

    session.add(
        ApiKey(
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            label=label,
            db_update_allowed=db_update_allowed,
            max_rows_per_batch=max_rows_per_batch,
        )
    )
    session.commit()


async def _attach_semantic_index_when_ready(application) -> None:
    """
    Poll Redis every 15 s until the Celery semantic-index task signals readiness,
    then instantiate VespaSemanticIndex and wire it into the running CandidateGenerator.
    Runs as a background asyncio task so it never blocks the event loop.
    """
    import asyncio
    import logging

    _log = logging.getLogger(__name__)
    _READY_KEY = "semantic_index:ready"
    poll_interval = 15  # seconds

    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL)
    except Exception as exc:
        _log.warning("semantic_watcher: cannot connect to Redis: %s", exc)
        return

    # Check immediately in case the index was already built in a prior run.
    _first = True
    while True:
        if not _first:
            await asyncio.sleep(poll_interval)
        _first = False
        try:
            if not r.get(_READY_KEY):
                continue

            engine = getattr(application.state, "title_match_engine", None)
            if engine is None:
                continue

            # Already wired — nothing to do.
            if engine._gen._semantic_index is not None:
                return

            from app.title_matching.semantic_index import VespaSemanticIndex
            index = VespaSemanticIndex(settings.VESPA_URL, settings)
            engine._gen._semantic_index = index
            _log.info("semantic_watcher: VespaSemanticIndex attached to running engine")
            return

        except Exception as exc:
            _log.debug("semantic_watcher: error during attach attempt: %s", exc)


async def _refresh_engine_when_sync_dirty(application) -> None:
    """
    Poll Redis every 15 s for the movie_master_sync:dirty signal set by
    sync_movie_master_task (app/tasks/prod_db_sync_task.py) on completion,
    and rebuild the fuzzy/alias TitleMatchEngine when it appears.

    Mirrors _attach_semantic_index_when_ready's Redis-signal pattern above —
    that watcher only attaches a Vespa index reference onto an already-built
    engine; it does not reload the engine's underlying master_rows snapshot,
    which is the concern this watcher exists for. Without this, rows synced
    from the production DB stay invisible to fuzzy/alias matching (though
    still reachable via Vespa semantic search once the reindex task
    finishes) until the next app restart or CSV upload.
    """
    import asyncio
    import logging

    _log = logging.getLogger(__name__)
    poll_interval = 15  # seconds

    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL)
    except Exception as exc:
        _log.warning("sync_watcher: cannot connect to Redis: %s", exc)
        return

    from app.tasks.prod_db_sync_task import MOVIE_MASTER_SYNC_DIRTY_KEY

    while True:
        await asyncio.sleep(poll_interval)
        try:
            if not r.get(MOVIE_MASTER_SYNC_DIRTY_KEY):
                continue

            from app.database import engine as db_engine
            from sqlmodel import Session
            from app.title_matching.loader import build_title_match_engine
            from app.title_matching.engine import TitleMatchEngine

            with Session(db_engine) as session:
                gen, aliases = build_title_match_engine(session)
                application.state.title_match_engine = TitleMatchEngine(gen, aliases)

            r.delete(MOVIE_MASTER_SYNC_DIRTY_KEY)
            _log.info("sync_watcher: title_match_engine rebuilt after production DB sync")

        except Exception as exc:
            _log.debug("sync_watcher: error during refresh attempt: %s", exc)


@app.on_event("startup")
async def startup() -> None:
    """
    Initialize DB tables, load detection engines, and kick off the
    Vespa semantic index build as a background Celery task.

    The title-match engine is available immediately with fuzzy/alias
    matching. Semantic search activates once the Celery task completes
    (typically a few minutes on first run).
    """
    from app.database import create_db_and_tables, engine as db_engine
    from sqlmodel import Session
    from app.detection.loader import build_engine_from_db
    from app.movie_detection.loader import build_movie_format_engine_from_db

    create_db_and_tables()

    with Session(db_engine) as session:
        app.state.engine = build_engine_from_db(session)
        _seed_default_movie_formats(session)
        # This same ApiKey row is also what require_api_key_lobby_check
        # checks against — the lobby-check surface deliberately REUSES
        # X_API_KEY/amenity/external-api-key rather than getting its own
        # dedicated key (per product decision 2026-09-01). Its concurrent-
        # jobs budget is still independent per surface (counted against
        # LobbyCheckJob vs. ApiTitleMatchJob separately — see
        # dependencies/api_auth.py), just under the same row/limits.
        _seed_api_key(
            session, settings.X_API_KEY,
            label="env-seeded (X_API_KEY)", db_update_allowed=True,
        )
        app.state.movie_engine = build_movie_format_engine_from_db(session)

        from app.intl_detection.loader import build_intl_engine_from_db
        app.state.intl_engine = build_intl_engine_from_db(session)

        from app.title_matching.loader import build_title_match_engine
        from app.models import MovieMaster
        from sqlmodel import select as _select
        movie_count = session.exec(_select(MovieMaster).limit(1)).first()
        if movie_count:
            gen, aliases = build_title_match_engine(session)
            from app.title_matching.engine import TitleMatchEngine
            app.state.title_match_engine = TitleMatchEngine(gen, aliases)
        else:
            app.state.title_match_engine = None

    # Fire the semantic index build as a Celery task — non-blocking.
    if settings.SEMANTIC_SEARCH_ENABLED:
        try:
            from app.tasks.semantic_tasks import build_semantic_index_task
            build_semantic_index_task.delay()
            import logging as _logging
            _logging.getLogger(__name__).info(
                "startup: semantic index build queued as Celery task"
            )
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "startup: could not queue semantic index task: %s", exc
            )

        # Poll Redis in the background and attach VespaSemanticIndex once ready.
        import asyncio as _asyncio
        _asyncio.ensure_future(_attach_semantic_index_when_ready(app))

    # Poll Redis in the background and rebuild the fuzzy/alias engine after
    # a production DB sync completes (see sync_movie_master_task).
    import asyncio as _asyncio
    _asyncio.ensure_future(_refresh_engine_when_sync_dirty(app))

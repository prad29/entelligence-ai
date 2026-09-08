from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import configure_logging
from app.routers import detect, amenities, circuits, review, jobs
from app.routers import settings as settings_router
from app.routers import movie_detect, movie_formats, movie_review, movie_jobs
from app.routers import movie_title_match
from app.routers import movie_title_match_v2
from app.routers import movie_title_match_intl_v2
from app.routers import external_title_match
from app.routers import external_title_match_v2
from app.routers import deleted_showtimes
from app.routers import intl_detect, intl_amenities, intl_jobs
from app.routers import usage
from app.routers import lobby_check
from app.routers import calendar_extract

# Configure structured JSON logging as early as possible
configure_logging()

app = FastAPI(
    title="Amenity Screen Format Detector",
    description="Detect cinema screen formats from amenity strings.",
    version="0.3.0",
    openapi_tags=[
        {
            "name": "movie-title-match-v2",
            "description": (
                "Domestic-only v2 title matching (prefix /api/v2/movie-title-match). Same "
                "endpoint shapes as v1 (/api/v1/movie-title-match), but the match additionally "
                "weighs genre/cast/director/synopsis and deterministically rejects a pick whose "
                "director AND synopsis are both empty (unless genre is Sports or Concert/Special "
                "Events). v1 is untouched and remains the default for existing integrations."
            ),
        },
        {
            "name": "movie-title-match-intl-v2",
            "description": (
                "International-only v2 title matching (prefix /api/v2/intl-movie-title-match, "
                "country required on every request). A fully standalone pipeline that never "
                "calls the shared v1 orchestrator: country-scoped Vespa search (in both the "
                "exact and semantic search paths), a deterministic country-consistency "
                "guardrail, anniversary/re-release date-arithmetic rules, and an independent "
                "Bedrock verification pass over the model's first pick. International v1 "
                "(market=international on the v1 endpoints) is untouched."
            ),
        },
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
            "name": "external-title-match-v2",
            "description": (
                "v2-pipeline dispatch surface for the external title-matching API: POST "
                "/api/v2/singletitle and POST /api/v2/batchtitle. Same request bodies, same "
                "x-api-key auth, same row limits and same 202-plus-polling flow as the v1 "
                "routes above — the URL is the pipeline selector, not a header or flag. A v2 "
                "submission routes each row through the market-appropriate v2 pipeline "
                "(domestic v2's metadata weighing for type=domestic, the standalone "
                "international v2 pipeline for type=international) instead of the shared v1 "
                "matcher. There are deliberately NO /api/v2 job endpoints: status, results and "
                "retry stay on /api/v1/external/jobs/{job_id}* and serve v1 and v2 jobs "
                "identically, since a job's variant changes only which matcher runs its rows."
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
app.include_router(movie_title_match_v2.router)
app.include_router(movie_title_match_intl_v2.router)
app.include_router(deleted_showtimes.router)
app.include_router(intl_detect.router)
app.include_router(intl_amenities.router)
app.include_router(intl_jobs.router)
app.include_router(usage.router)

if settings.EXTERNAL_API_ENABLED:
    app.include_router(external_title_match.router)
    # Same gate as v1 deliberately: v2 is the same external API surface with a
    # different matching pipeline, and its jobs are polled through v1's job
    # endpoints — exposing /api/v2/singletitle while /api/v1/external/jobs was
    # switched off would leave callers unable to read their own results.
    app.include_router(external_title_match_v2.router)

if settings.LOBBY_CHECK_ENABLED:
    app.include_router(lobby_check.router)

if settings.CALENDAR_EXTRACT_ENABLED:
    app.include_router(calendar_extract.router)


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

    # Fire the semantic index build as a Celery task — non-blocking. It feeds
    # Vespa directly (build_semantic_index_task -> semantic_index.build_semantic_index),
    # which the agentic runner queries live on every request — no in-process
    # engine/snapshot to attach or refresh here.
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

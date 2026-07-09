from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import configure_logging
from app.routers import detect, amenities, circuits, review, jobs
from app.routers import settings as settings_router
from app.routers import movie_detect, movie_formats, movie_review, movie_jobs
from app.routers import movie_title_match

# Configure structured JSON logging as early as possible
configure_logging()

app = FastAPI(
    title="Amenity Screen Format Detector",
    description="Detect cinema screen formats from amenity strings.",
    version="0.3.0",
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
        app.state.movie_engine = build_movie_format_engine_from_db(session)

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

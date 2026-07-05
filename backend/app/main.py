from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import configure_logging
from app.routers import detect, amenities, circuits, review, jobs
from app.routers import settings as settings_router

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


@app.on_event("startup")
async def startup() -> None:
    """
    Initialize the database tables (if they don't exist yet) and load the
    detection engine from approved DB rows.

    Engine is built from approved AmenityMapping rows and CircuitAlias rows.
    Seed the DB first via the CLI:
        python app/cli.py seed-from-xlsx path/to/Amenities_Priority.xlsx
    """
    from app.database import create_db_and_tables, engine as db_engine
    from sqlmodel import Session
    from app.detection.loader import build_engine_from_db

    create_db_and_tables()

    with Session(db_engine) as session:
        app.state.engine = build_engine_from_db(session)

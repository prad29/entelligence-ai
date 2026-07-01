from fastapi import FastAPI

from app.routers import detect, amenities, circuits, review
from app.routers import settings as settings_router
from app.routers import jobs as jobs_router

app = FastAPI(
    title="Amenity Screen Format Detector",
    description="Detect cinema screen formats from amenity strings.",
    version="0.4.0",
)

app.include_router(detect.router)
app.include_router(amenities.router)
app.include_router(circuits.router)
app.include_router(review.router)
app.include_router(settings_router.router)
app.include_router(jobs_router.router)


@app.on_event("startup")
async def startup() -> None:
    """
    Initialize the database tables (if they don't exist yet) and load the
    detection engine from approved DB rows.

    Phase 2: engine is built from approved AmenityMapping / CircuitOverride /
    CircuitAlias rows stored in the database.  Seed the DB first via the CLI:
        python app/cli.py seed-from-xlsx path/to/Amenities_Priority.xlsx
    """
    from app.database import create_db_and_tables, engine as db_engine
    from sqlmodel import Session
    from app.detection.loader import build_engine_from_db

    create_db_and_tables()

    with Session(db_engine) as session:
        app.state.engine = build_engine_from_db(session)

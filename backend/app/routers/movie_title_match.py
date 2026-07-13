import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel
from sqlmodel import Session

from app.database import get_session

router = APIRouter(prefix="/api/v1/movie-title-match", tags=["movie-title-match"])


class TitleMatchRequest(BaseModel):
    title: str
    theater: Optional[str] = None
    show_date: Optional[str] = None       # YYYY-MM-DD
    ticketing_url: Optional[str] = None
    use_poster_vision: bool = False       # Mode B only: enable Claude vision on DB posters


@router.post("/single")
async def match_single_title(
    payload: TitleMatchRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    engine = getattr(request.app.state, 'title_match_engine', None)
    if engine is None:
        return {
            "suggested_movie_id": 0,
            "suggested_movie_title": "Engine not loaded",
            "canonical_movie_id": 0,
            "confidence": 0.0,
            "decision": "REVIEW",
            "reasoning": (
                "Movie Master has not been seeded yet. "
                "Run: python app/cli.py seed-movie-master /path/to/dump.csv"
            ),
            "evidence": {},
            "cover_image": None,
            "ticketing_poster_url": None,
            "fired_ai": False,
        }

    result = engine.match(
        title=payload.title,
        show_date=payload.show_date,
        theater=payload.theater,
        ticketing_url=payload.ticketing_url,
        use_poster_vision=payload.use_poster_vision,
    )
    return result.__dict__


@router.get("/master")
async def search_master(
    q: str = Query(""),
    limit: int = Query(20, le=100),
    session: Session = Depends(get_session),
):
    from sqlmodel import select
    from app.models import MovieMaster

    stmt = select(MovieMaster).where(
        MovieMaster.movie_title.ilike(f"%{q}%")
    ).limit(limit)
    rows = session.exec(stmt).all()
    return [
        {
            "id": r.id,
            "movie_title": r.movie_title,
            "release_date": r.release_date,
            "cover_image": r.cover_image,
            "imdb_id": r.imdb_id,
        }
        for r in rows
    ]


@router.get("/master/search")
async def agent_search_master(
    q: str = Query(""),
    limit: int = Query(10, le=50),
    session: Session = Depends(get_session),
):
    """Internal keyword search endpoint used by the Mode B agentic subprocess.

    Returns richer metadata (director, running_time) to help the agent disambiguate.
    No auth — intended for localhost-only use by the claude subprocess.
    """
    from sqlmodel import select
    from app.models import MovieMaster

    stmt = select(MovieMaster).where(
        MovieMaster.movie_title.ilike(f"%{q}%")
    ).limit(limit)
    rows = session.exec(stmt).all()
    return [
        {
            "id": r.id,
            "movie_title": r.movie_title,
            "release_date": r.release_date,
            "imdb_id": r.imdb_id,
            "director": r.director,
            "running_time": r.running_time,
        }
        for r in rows
    ]


@router.get("/master/count")
async def get_master_count(session: Session = Depends(get_session)):
    from sqlmodel import select, func
    from app.models import MovieMaster

    count = session.exec(select(func.count()).select_from(MovieMaster)).one()
    return {"count": count}


@router.post("/master/seed")
async def seed_master(
    file: UploadFile = File(...),
    request: Request = None,
    session: Session = Depends(get_session),
):
    from app.models import MovieMaster
    from sqlmodel import select, func
    from app.title_matching.seed_loader import seed_from_rows

    filename = file.filename or ""
    content = await file.read()

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        headers = [str(cell.value).strip() if cell.value is not None else "" for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        rows = []
        for excel_row in ws.iter_rows(min_row=2, values_only=True):
            rows.append({headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(excel_row)})
        wb.close()
    else:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

    existing_count = session.exec(select(func.count()).select_from(MovieMaster)).one()
    result = seed_from_rows(session, rows)

    # Reload the title match engine in app state
    if request is not None:
        try:
            from app.title_matching.loader import build_title_match_engine
            from app.title_matching.engine import TitleMatchEngine
            gen, aliases = build_title_match_engine(session)
            request.app.state.title_match_engine = TitleMatchEngine(gen, aliases)
        except Exception:
            pass

    # Queue semantic index build whenever rows were inserted or updated
    if result["inserted"] > 0 or result["updated"] > 0:
        try:
            from app.tasks.semantic_tasks import build_semantic_index_task
            build_semantic_index_task.delay()
        except Exception:
            pass

    return {
        "previously_seeded": existing_count,
        "inserted": result["inserted"],
        "updated": result["updated"],
        "skipped": result["skipped"],
        "total_in_file": len(rows),
    }

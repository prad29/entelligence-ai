from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.database import get_session

router = APIRouter(prefix="/api/v1/movie-title-match", tags=["movie-title-match"])


class TitleMatchRequest(BaseModel):
    title: str
    theater: Optional[str] = None
    show_date: Optional[str] = None       # YYYY-MM-DD
    ticketing_url: Optional[str] = None


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

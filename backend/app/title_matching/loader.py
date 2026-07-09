import logging

from sqlmodel import Session, select

from app.config import settings
from app.models import MovieMaster, MovieTitleAlias
from app.title_matching.candidate_generator import CandidateGenerator

logger = logging.getLogger(__name__)


def build_title_match_engine(session: Session) -> tuple[CandidateGenerator, dict[str, int]]:
    rows = session.exec(select(MovieMaster)).all()
    master_rows = [
        {
            'id': r.id,
            'movie_title': r.movie_title,
            'release_date': r.release_date,
            'cover_image': r.cover_image,
            'parent_id': r.parent_id,
            'director': getattr(r, 'director', None),
        }
        for r in rows
    ]
    aliases_raw = session.exec(select(MovieTitleAlias)).all()
    aliases = {a.normalized_alias.lower(): a.movie_master_id for a in aliases_raw}

    semantic_index = None
    if settings.SEMANTIC_SEARCH_ENABLED:
        try:
            from app.title_matching.semantic_index import build_semantic_index
            semantic_index = build_semantic_index(master_rows, settings)
        except Exception as exc:
            logger.warning("semantic index build failed, continuing without it: %s", exc)

    engine = CandidateGenerator(master_rows, semantic_index=semantic_index)
    return engine, aliases

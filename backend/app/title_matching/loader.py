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

    # Semantic index is built asynchronously via Celery.
    # Engine starts with None and gets updated once the task completes.
    engine = CandidateGenerator(master_rows, semantic_index=None)
    return engine, aliases

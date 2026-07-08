from sqlmodel import Session, select

from app.models import MovieMaster, MovieTitleAlias
from app.title_matching.candidate_generator import CandidateGenerator


def build_title_match_engine(session: Session) -> tuple[CandidateGenerator, dict[str, int]]:
    rows = session.exec(select(MovieMaster)).all()
    master_rows = [
        {
            'id': r.id,
            'movie_title': r.movie_title,
            'release_date': r.release_date,
            'cover_image': r.cover_image,
            'parent_id': r.parent_id,
        }
        for r in rows
    ]
    aliases_raw = session.exec(select(MovieTitleAlias)).all()
    aliases = {a.normalized_alias.lower(): a.movie_master_id for a in aliases_raw}
    engine = CandidateGenerator(master_rows)
    return engine, aliases

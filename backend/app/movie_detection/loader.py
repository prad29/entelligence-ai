from sqlmodel import Session, select

from app.models import MovieFormatMapping
from app.movie_detection.engine import MovieFormatEngine, MovieFormatMappingIndex
from app.movie_detection.types import MovieFormatApprovedMapping
from app.detection.normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens


def build_movie_format_engine_from_db(session: Session) -> MovieFormatEngine:
    rows = session.exec(
        select(MovieFormatMapping).where(MovieFormatMapping.status == "approved")
    ).all()

    mappings: list[MovieFormatApprovedMapping] = []
    for m in rows:
        kw = m.keyword
        mappings.append(
            MovieFormatApprovedMapping(
                keyword=kw,
                format=m.format,
                priority_tier=m.priority_tier,
                norm_exact=normalize_string(kw).lower(),
                norm_track_a=track_a_clean(kw),
                norm_track_b=track_b_clean(kw),
                norm_track_c=track_c_tokens(kw),
            )
        )

    return MovieFormatEngine(MovieFormatMappingIndex(mappings))

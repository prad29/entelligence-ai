from sqlmodel import Session, select

from app.models import AmenityMapping, CircuitAlias
from app.detection.engine import ScreenFormatEngine, MappingIndex
from app.detection.normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens
from app.detection.types import ApprovedMapping


def build_engine_from_db(session: Session) -> ScreenFormatEngine:
    mappings_db = session.exec(
        select(AmenityMapping).where(AmenityMapping.status == "approved")
    ).all()

    aliases_db = session.exec(select(CircuitAlias)).all()

    mappings: list[ApprovedMapping] = []
    for m in mappings_db:
        kw = m.amenity_keyword
        mappings.append(
            ApprovedMapping(
                amenity_keyword=kw,
                screen_format=m.screen_format,
                priority_tier=m.priority_tier,
                circuit_name=m.circuit_name,
                na_default=m.na_default,
                norm_exact=normalize_string(kw).lower(),
                norm_track_a=track_a_clean(kw),
                norm_track_b=track_b_clean(kw),
                norm_track_c=track_c_tokens(kw),
            )
        )

    aliases: dict[str, str] = {a.raw_or_alias: a.canonical for a in aliases_db}

    index = MappingIndex(mappings, aliases=aliases)
    return ScreenFormatEngine(index)

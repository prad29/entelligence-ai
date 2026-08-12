from sqlmodel import Session, select

from app.models import IntlAmenityMapping
from app.intl_detection.engine import IntlScreenFormatEngine, IntlMappingIndex
from app.intl_detection.types import IntlApprovedMapping
from app.detection.normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens


def build_intl_engine_from_db(session: Session) -> IntlScreenFormatEngine:
    rows = session.exec(
        select(IntlAmenityMapping).where(IntlAmenityMapping.status == "approved")
    ).all()

    mappings: list[IntlApprovedMapping] = []
    for m in rows:
        kw = m.amenity_keyword
        mappings.append(
            IntlApprovedMapping(
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

    return IntlScreenFormatEngine(IntlMappingIndex(mappings))

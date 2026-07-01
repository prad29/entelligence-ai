"""
DB-backed EngineLoader for Phase 2+.

Queries approved AmenityMapping, CircuitOverride, and CircuitAlias rows
from the database and builds a ScreenFormatEngine with a pre-compiled
MappingIndex.
"""

from sqlmodel import Session, select

from app.models import AmenityMapping, CircuitOverride, CircuitAlias
from app.detection.engine import ScreenFormatEngine, MappingIndex
from app.detection.normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens
from app.detection.types import ApprovedMapping, CircuitOverrideEntry


def build_engine_from_db(session: Session) -> ScreenFormatEngine:
    """
    Load all approved rows from the DB and return a ready-to-use ScreenFormatEngine.

    Only rows with status='approved' are included — draft, pending, and rejected
    rows are excluded from the live detection engine.
    """
    mappings_db = session.exec(
        select(AmenityMapping).where(AmenityMapping.status == "approved")
    ).all()

    overrides_db = session.exec(
        select(CircuitOverride).where(CircuitOverride.status == "approved")
    ).all()

    aliases_db = session.exec(select(CircuitAlias)).all()

    # Build ApprovedMapping dataclass instances with pre-compiled norms
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

    overrides: list[CircuitOverrideEntry] = [
        CircuitOverrideEntry(
            keyword=o.keyword,
            circuit_name=o.circuit_name,
            screen_format=o.screen_format,
        )
        for o in overrides_db
    ]

    aliases: dict[str, str] = {a.raw_or_alias: a.canonical for a in aliases_db}

    index = MappingIndex(mappings, overrides, aliases)
    return ScreenFormatEngine(index)

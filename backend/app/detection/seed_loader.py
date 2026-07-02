"""
Parse Amenities Priority.xlsx per §5A spec and seed the database.

Sheet 1 structure:
  - Rows where col A starts with AMENITIES_PRIORITY_N mark the start of a priority tier.
  - Subsequent rows are keyword/format pairs until the next tier marker.

Sheet 3 structure:
  - min_row=2 (row 1 is header)
  - col A: keyword, col B: circuit_name, col C: screen_format
  - circuit_name == "NA" signals a na_default row (not a circuit override)
"""

import openpyxl
from sqlalchemy import text as sa_text
from app.detection.normalizer import normalize_string
from app.models import AmenityMapping, CircuitOverride, CircuitAlias

# VIP circuits to always seed as aliases even if not in Sheet 3
_VIP_CIRCUIT_ALIASES = [
    "Caribbean Cinemas - US Territory",
    "Cineplex Entertainment",
]


def _clean_cell(value) -> str:
    """Normalize a cell value: coerce to str, strip literal and real non-breaking spaces."""
    s = str(value or "").strip()
    # Handle both the real \xa0 character and the literal "xa0" sequence
    s = s.replace("\xa0", " ").replace("xa0", " ")
    # Smart quotes
    s = s.replace("‘", "'").replace("’", "'")
    s = s.replace("“", '"').replace("”", '"')
    return s.strip()


def parse_xlsx(path: str):
    """
    Parse the xlsx file and return (mappings, overrides, aliases).

    Returns:
        mappings: list[AmenityMapping]
        overrides: list[CircuitOverride]
        aliases: list[CircuitAlias]
    """
    wb = openpyxl.load_workbook(path, data_only=True)

    # --- Sheet 1: Priority tiers ---
    mappings: list[AmenityMapping] = []
    current_tier: int | None = None

    for row in wb.worksheets[0].iter_rows(values_only=True):
        col_a = _clean_cell(row[0] if row else "")
        col_b = _clean_cell(row[1]) if len(row) > 1 and row[1] is not None else ""

        if col_a.startswith("AMENITIES_PRIORITY_"):
            tier_suffix = col_a.replace("AMENITIES_PRIORITY_", "")
            try:
                current_tier = int(tier_suffix)
            except ValueError:
                pass
            continue

        if current_tier is not None and col_a and col_b:
            mappings.append(
                AmenityMapping(
                    amenity_keyword=col_a,
                    screen_format=col_b,
                    priority_tier=current_tier,
                    status="approved",
                )
            )

    # Dedupe within tier: (normalised_keyword, format, tier)
    seen: set[tuple] = set()
    deduped: list[AmenityMapping] = []
    for m in mappings:
        key = (normalize_string(m.amenity_keyword).lower(), m.screen_format, m.priority_tier)
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    # --- Sheet 3: Circuit overrides ---
    overrides: list[CircuitOverride] = []
    alias_map: dict[str, str] = {}

    sheet3 = wb["Sheet3"] if "Sheet3" in wb.sheetnames else None
    if sheet3 is not None:
        for row in sheet3.iter_rows(min_row=2, values_only=True):
            if not row or not row[0]:
                continue

            # Sheet3 columns: A=keyword, B=screen_format, C=circuit_name
            kw = _clean_cell(row[0])
            fmt = _clean_cell(row[1]) if len(row) > 1 and row[1] is not None else ""
            circ = _clean_cell(row[2]) if len(row) > 2 and row[2] is not None else ""

            if not kw or not fmt:
                continue

            if circ.upper() == "NA" or not circ:
                # na_default row — attach to any existing deduped mapping with same keyword
                for m in deduped:
                    if normalize_string(m.amenity_keyword).lower() == normalize_string(kw).lower():
                        m.na_default = fmt
            else:
                overrides.append(
                    CircuitOverride(
                        keyword=kw,
                        circuit_name=circ,
                        screen_format=fmt,
                        status="approved",
                    )
                )
                # Collect canonical circuit names for alias building
                alias_map[circ.lower()] = circ

    # Build aliases list — canonical circuit names from Sheet 3 overrides
    aliases: list[CircuitAlias] = [
        CircuitAlias(raw_or_alias=raw, canonical=canonical)
        for raw, canonical in alias_map.items()
    ]

    # Always ensure VIP circuits are present as aliases
    for canonical in _VIP_CIRCUIT_ALIASES:
        raw = canonical.lower()
        if raw not in alias_map:
            aliases.append(CircuitAlias(raw_or_alias=raw, canonical=canonical))

    return deduped, overrides, aliases


def seed_db(session, path: str) -> None:
    """
    Parse the xlsx file and upsert all rows into the database.

    Uses session.merge() so re-running is idempotent (merge by primary key).
    Callers must commit() after this returns — seed_db does not commit.
    """
    mappings, overrides, aliases = parse_xlsx(path)

    # Truncate so re-runs are idempotent; CASCADE handles FK ordering
    session.exec(sa_text("TRUNCATE amenitymapping, circuitoverride, circuitalias RESTART IDENTITY CASCADE"))
    session.add_all(mappings)
    session.add_all(overrides)
    session.add_all(aliases)
    session.commit()

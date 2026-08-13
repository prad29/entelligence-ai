"""
Parse International Amenities Priorities.xlsx and seed the database.

Sheet1 structure:
  - Rows where col A starts with AMENITIES_PRIORITY_N mark the start of a
    priority tier (N in 1..5 for the real file; no P6 analogue for intl).
  - Subsequent rows are keyword/format pairs until the next tier marker.
  - There is no Sheet3 / circuit_name / na_default data for intl — those
    columns exist on IntlAmenityMapping only for future-proofing and are
    never populated by this parser.

Deliberately split into a pure `parse_intl_xlsx` (no DB/session) and a
`seed_intl_db` DB-writing wrapper that uses SQLAlchemy Core's `delete()`
statement rather than a raw table-wipe SQL statement, so the parse path is
unit-testable and the reset path still works against the in-memory SQLite
engine used by the test suite.
"""

import openpyxl
from sqlalchemy import delete
from app.detection.normalizer import normalize_string
from app.models import IntlAmenityMapping


def _clean_cell(value) -> str:
    s = str(value or "").strip()
    s = s.replace("\xa0", " ").replace("xa0", " ")
    s = s.replace("‘", "'").replace("’", "'")
    s = s.replace("“", '"').replace("”", '"')
    return s.strip()


def parse_intl_xlsx(path: str) -> list[IntlAmenityMapping]:
    """
    Parse Sheet1 of the international amenities xlsx into a list of
    IntlAmenityMapping rows. Pure function — no DB session involved.
    """
    wb = openpyxl.load_workbook(path, data_only=True)

    mappings: list[IntlAmenityMapping] = []
    current_tier: int | None = None

    for row in wb.worksheets[0].iter_rows(values_only=True):
        col_a = _clean_cell(row[0] if row else "")
        col_b = _clean_cell(row[1]) if row and len(row) > 1 and row[1] is not None else ""

        if col_a.startswith("AMENITIES_PRIORITY_"):
            tier_suffix = col_a.replace("AMENITIES_PRIORITY_", "")
            try:
                current_tier = int(tier_suffix)
            except ValueError:
                pass
            continue

        # Rows before the first tier marker (the header row) are skipped
        # because current_tier is still None.
        if current_tier is not None and col_a and col_b:
            mappings.append(
                IntlAmenityMapping(
                    amenity_keyword=col_a,
                    screen_format=col_b,
                    priority_tier=current_tier,
                    status="approved",
                )
            )

    # Dedupe within (normalized_keyword, screen_format, priority_tier), first-wins.
    # Keying on tier as well as format lets a genuine cross-tier repeat
    # (e.g. KinoEvolution appearing in both P1 and P3 with different keywords)
    # survive as two distinct rows.
    seen: set[tuple] = set()
    deduped: list[IntlAmenityMapping] = []
    for m in mappings:
        key = (normalize_string(m.amenity_keyword).lower(), m.screen_format, m.priority_tier)
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    return deduped


def seed_intl_db(session, path: str, reset: bool = True) -> int:
    """
    Parse the xlsx at `path` and insert rows into intlamenitymapping.

    When reset=True, existing rows are cleared first via SQLAlchemy Core's
    delete() statement (portable, unlike a Postgres-only table-wipe
    statement), so this works against both Postgres and an in-memory SQLite
    test engine.
    """
    rows = parse_intl_xlsx(path)

    if reset:
        session.exec(delete(IntlAmenityMapping))

    session.add_all(rows)
    session.commit()

    return len(rows)

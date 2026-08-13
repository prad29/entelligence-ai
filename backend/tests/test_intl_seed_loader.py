"""
Tests for app.intl_detection.seed_loader.

The fixture xlsx is built in-process with openpyxl into tmp_path — never a
committed binary fixture, never read from ~/Downloads. It reproduces the
real spreadsheet's shape: header row, AMENITIES_PRIORITY_N tier markers,
keyword/format pairs, blank rows, and rows missing a format.
"""

import openpyxl
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from app.intl_detection.seed_loader import parse_intl_xlsx, seed_intl_db
from app.models import IntlAmenityMapping


def _write_fixture_xlsx(path) -> str:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    rows = [
        ("Amenity", "Mapped to"),  # header row — must be skipped
        ("AMENITIES_PRIORITY_1", None),
        ("4DX", "4DX"),
        ("4DX", "4DX"),  # duplicate within tier — deduped
        ("4DX\xa0", "4DX"),  # NBSP-suffixed duplicate — also deduped
        ("4DX Voorpremière", "4DX"),  # accented keyword
        ("KINOEVOLUTION", "KinoEvolution"),
        (None, None),  # blank row — skipped
        ("MissingFormat", None),  # missing format — skipped
        ("AMENITIES_PRIORITY_3", None),
        ("KINOEVOLUTION", "KinoEvolution"),  # same keyword, different tier — kept
        ("ScreenX", "ScreenX"),
        ("AMENITIES_PRIORITY_5", None),
        ("70MM", "Standard"),
        ("Digital", "Standard"),
    ]
    for row in rows:
        ws.append(row)

    out_path = str(path / "intl_fixture.xlsx")
    wb.save(out_path)
    return out_path


@pytest.fixture()
def fixture_xlsx_path(tmp_path) -> str:
    return _write_fixture_xlsx(tmp_path)


def test_header_row_is_not_treated_as_a_mapping(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    keywords = [r.amenity_keyword for r in rows]
    assert "Amenity" not in keywords
    assert "Mapped to" not in keywords


def test_tier_markers_assign_priority_tier(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    screenx = next(r for r in rows if r.amenity_keyword == "ScreenX")
    assert screenx.priority_tier == 3


def test_rows_missing_a_format_are_skipped(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    keywords = [r.amenity_keyword for r in rows]
    assert "MissingFormat" not in keywords


def test_blank_rows_are_skipped(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    assert all(r.amenity_keyword for r in rows)


def test_all_seeded_rows_are_approved(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    assert rows
    assert all(r.status == "approved" for r in rows)


def test_circuit_name_and_na_default_are_never_populated(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    assert rows
    assert all(r.circuit_name is None for r in rows)
    assert all(r.na_default is None for r in rows)


def test_duplicate_keyword_within_tier_is_deduped(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    tier1_4dx = [r for r in rows if r.priority_tier == 1 and r.amenity_keyword.strip() == "4DX"]
    assert len(tier1_4dx) == 1


def test_same_keyword_in_two_tiers_is_kept(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    kino = [r for r in rows if r.amenity_keyword == "KINOEVOLUTION"]
    tiers = {r.priority_tier for r in kino}
    assert tiers == {1, 3}


def test_nbsp_and_smart_quotes_are_stripped(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    # The NBSP-suffixed row deduped with the plain "4DX" row, and its keyword
    # must have been cleaned before the dedupe key was computed.
    tier1_4dx = [r for r in rows if r.priority_tier == 1 and r.screen_format == "4DX" and r.amenity_keyword == "4DX"]
    assert len(tier1_4dx) == 1
    assert "\xa0" not in tier1_4dx[0].amenity_keyword


def test_accented_keyword_survives_parsing(fixture_xlsx_path):
    rows = parse_intl_xlsx(fixture_xlsx_path)
    accented = next(r for r in rows if r.amenity_keyword == "4DX Voorpremière")
    assert accented.screen_format == "4DX"


def _make_sqlite_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_seed_intl_db_inserts_parsed_rows(fixture_xlsx_path):
    session = _make_sqlite_session()
    n = seed_intl_db(session, fixture_xlsx_path, reset=True)
    assert n > 0

    stored = session.exec(select(IntlAmenityMapping)).all()
    assert len(stored) == n
    session.close()


def test_seed_intl_db_reset_clears_existing_rows(fixture_xlsx_path):
    session = _make_sqlite_session()
    seed_intl_db(session, fixture_xlsx_path, reset=True)
    n_second = seed_intl_db(session, fixture_xlsx_path, reset=True)

    rows = session.exec(select(IntlAmenityMapping)).all()
    assert len(rows) == n_second
    session.close()

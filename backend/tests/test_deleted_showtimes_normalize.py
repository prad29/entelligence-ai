"""Unit tests for app.deleted_showtimes.normalize — ported from
showtime_serp_check.py's self_test(). Pure functions, no DB/network."""

from datetime import date

from app.deleted_showtimes.normalize import (
    fmt_min,
    norm_theater,
    parse_time_to_min,
    resolve_block_dates,
    screen_drift,
    theaters_match,
    titles_match,
)


class TestTitlesMatch:
    def test_year_suffix_is_ignored(self):
        assert titles_match("Moana (2026)", "Moana")

    def test_sequel_number_is_not_ignored(self):
        assert not titles_match("Moana", "Moana 2")

    def test_roman_numeral_sequel_is_not_ignored(self):
        assert not titles_match("Rocky II", "Rocky III")

    def test_imax_decoration_is_ignored(self):
        assert titles_match("The Odyssey: The IMAX Experience", "The Odyssey")

    def test_3d_decoration_is_ignored(self):
        assert titles_match("Spider-Man: Brand New Day 3D", "Spider-Man: Brand New Day")


class TestTheatersMatch:
    def test_screen_count_optional(self):
        assert theaters_match("Regal Riverview", "Riverview 14")

    def test_different_venue_with_same_screen_count_does_not_match(self):
        assert not theaters_match("AMC Garden State 16", "AMC Garden State Plaza 16")

    def test_identical_names_match(self):
        assert theaters_match("AMC Wayne 14", "AMC Wayne 14")

    def test_screen_count_drift_is_advisory_not_a_mismatch(self):
        assert theaters_match("AMC Wayne 14", "AMC Wayne 12")
        assert screen_drift("AMC Wayne 14", "AMC Wayne 12") == "14 -> 12"

    def test_bnb_alias_form_matches(self):
        assert theaters_match("B & B Theatres Omaha Oakview Plaza 14", "B&B Omaha Oakview Plaza 14")

    def test_screens_added_still_matches_same_venue(self):
        assert theaters_match("Xscape Blankenbaker 14", "Xscape Theatres Blankenbaker 16")
        assert screen_drift("Xscape Blankenbaker 14", "Xscape Theatres Blankenbaker 16") == "14 -> 16"

    def test_strict_screens_flag_separates_differing_counts(self):
        assert not theaters_match("Xscape Blankenbaker 14", "Xscape Blankenbaker 16", strict_screens=True)

    def test_screen_drift_does_not_rescue_a_different_core_name(self):
        assert not theaters_match("AMC Garden State 16", "AMC Garden State Plaza 14")

    def test_generic_names_never_match_fail_safe(self):
        assert not theaters_match("Cinemark Movies 10", "Cinemark Movies 12")

    def test_norm_theater_extracts_trailing_screen_count(self):
        core, screens = norm_theater("AMC Wayne 14")
        assert core == "wayne"
        assert screens == 14


class TestParseTimeToMin:
    def test_pm_with_period_and_space(self):
        assert parse_time_to_min("10:45 PM") == 1365

    def test_pm_no_space(self):
        assert parse_time_to_min("10:45pm") == 1365

    def test_midnight_thirty_am(self):
        assert parse_time_to_min("12:30 AM") == 30

    def test_noon_thirty_pm(self):
        assert parse_time_to_min("12:30 PM") == 750

    def test_hour_only_pm(self):
        assert parse_time_to_min("9pm") == 1260

    def test_unparseable_returns_none(self):
        assert parse_time_to_min("not a time") is None

    def test_none_input_returns_none(self):
        assert parse_time_to_min(None) is None


class TestFmtMin:
    def test_roundtrips_with_parse_time_to_min(self):
        assert fmt_min(parse_time_to_min("10:45 PM")) == "10:45 PM"

    def test_midnight(self):
        assert fmt_min(0) == "12:00 AM"


class TestResolveBlockDates:
    def test_undated_today_backfilled_from_a_later_dated_block(self):
        blocks = [
            {"day": "Today"},
            {"day": "Tomorrow"},
            {"day": "Sat", "date": "Aug 8"},
        ]
        resolved = resolve_block_dates(blocks, date(2026, 8, 6), date(2026, 8, 7))
        assert resolved == [date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 8)]

    def test_explicit_dates_anchor_regardless_of_machine_clock(self):
        blocks = [{"day": "Today", "date": "Aug 6"}, {"day": "Tomorrow", "date": "Aug 7"}]
        # machine clock says today() == Aug 7, but the explicit dates still win
        resolved = resolve_block_dates(blocks, date(2026, 8, 6), date(2026, 8, 7))
        assert resolved == [date(2026, 8, 6), date(2026, 8, 7)]

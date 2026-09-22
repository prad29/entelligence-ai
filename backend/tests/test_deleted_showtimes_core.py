"""Unit tests for app.deleted_showtimes.core — ported from
showtime_serp_check.py's self_test(). Pure functions, no DB/network/S3."""

from datetime import date

from app.deleted_showtimes.core import (
    FALSE_,
    TRUE_,
    UNKNOWN_,
    ShowtimeRow,
    apply_site_check,
    build_query,
    decide_rows,
    parse_theater_listing,
    plain_site_reason,
    short_theater_name,
)

FIXTURE = {
    "search_parameters": {"q": '"AMC Naperville 16" showtimes'},
    "knowledge_graph": {"title": "AMC Naperville 16"},
    "showtimes": [
        {"day": "Today", "date": "Aug 6", "movies": [
            {"name": "Spider-Man: Brand New Day",
             "showing": [{"time": ["12:30pm", "4:00pm"], "type": "Standard"},
                         {"time": ["10:30pm"], "type": "3D"}]},
            {"name": "The Odyssey (2026)",
             "showing": [{"time": ["10:55 PM"], "type": "IMAX"}]},
        ]},
        {"day": "Tomorrow", "date": "Aug 7", "movies": [
            {"name": "Spider-Man: Brand New Day",
             "showing": [{"time": ["10:00pm"], "type": "Standard"}]},
        ]},
    ],
}


def _row(key, time_s, title="Spider-Man: Brand New Day", show_date=date(2026, 8, 6)):
    from app.deleted_showtimes.normalize import parse_time_to_min

    return ShowtimeRow(
        key=key, theater="AMC Naperville 16", title=title, show_date=show_date,
        show_time_raw=time_s, show_min=parse_time_to_min(time_s),
    )


class TestParseTheaterListing:
    def test_listing_ok_and_theater_verified_via_knowledge_graph(self):
        target = today = date(2026, 8, 6)
        lst = parse_theater_listing(FIXTURE, "AMC Naperville 16", target, today, {}, False)
        assert lst.ok
        assert lst.theater_verified
        assert lst.day_date == "2026-08-06"
        assert lst.total_times == 4

    def test_date_absent_from_panel_is_not_ok(self):
        target, today = date(2026, 8, 9), date(2026, 8, 6)
        lst = parse_theater_listing(FIXTURE, "AMC Naperville 16", target, today, {}, False)
        assert not lst.ok
        assert lst.reason == "DATE_NOT_IN_PANEL"

    def test_no_showtimes_panel_is_never_true(self):
        target = today = date(2026, 8, 6)
        empty = parse_theater_listing({"organic_results": []}, "AMC Wayne 14", target, today, {}, False)
        assert not empty.ok
        assert empty.reason == "NO_SHOWTIMES_PANEL"

    def test_strict_verify_without_knowledge_graph_is_unverified(self):
        target = today = date(2026, 8, 6)
        strict = parse_theater_listing(
            {"showtimes": FIXTURE["showtimes"]}, "AMC Naperville 16", target, today, {}, True,
        )
        assert not strict.ok
        assert strict.reason == "THEATER_UNVERIFIED"

    def test_truncated_today_panel_sets_coverage_floor(self):
        dateless = {"showtimes": [
            {"day": "Today", "movies": [{"name": "Spider-Man: Brand New Day",
                                          "showing": [{"time": ["2:30pm", "10:30pm"], "type": "Standard"}]}]},
            {"day": "Tomorrow", "movies": [{"name": "Spider-Man: Brand New Day",
                                             "showing": [{"time": ["9:30am"], "type": "Standard"}]}]},
            {"day": "Sat", "date": "Aug 8", "movies": [{"name": "Spider-Man: Brand New Day",
                                                         "showing": [{"time": ["9:45am"], "type": "Standard"}]}]},
        ]}
        # machine clock says Aug 7, but Aug 6 batch still resolves via anchor dates
        lst = parse_theater_listing(dateless, "AMC Wayne 14", date(2026, 8, 6), date(2026, 8, 7), {}, False)
        assert lst.ok
        assert lst.truncated
        from app.deleted_showtimes.normalize import parse_time_to_min
        assert lst.coverage_from == parse_time_to_min("2:30 PM")


class TestDecideRows:
    def setup_method(self):
        target = today = date(2026, 8, 6)
        self.lst = parse_theater_listing(FIXTURE, "AMC Naperville 16", target, today, {}, False)

    def test_exact_minute_match_is_false(self):
        r = _row(1, "10:30 PM")
        decide_rows([r], self.lst, False)
        assert r.verdict == FALSE_

    def test_five_minute_gap_is_true_with_nearest_evidence(self):
        r = _row(2, "10:35 PM")
        decide_rows([r], self.lst, False)
        assert r.verdict == TRUE_
        assert "-5 min" in r.nearest

    def test_title_variant_with_format_decoration_still_matches(self):
        r = _row(3, "10:55 PM", title="The Odyssey")
        decide_rows([r], self.lst, False)
        assert r.verdict == FALSE_

    def test_unlisted_title_is_unable_to_determine_by_default(self):
        r = _row(4, "7:00 PM", title="Barbie")
        decide_rows([r], self.lst, False)
        assert r.verdict == UNKNOWN_

    def test_unlisted_title_is_true_when_title_missing_is_deleted(self):
        r = _row(4, "7:00 PM", title="Barbie")
        decide_rows([r], self.lst, True)
        assert r.verdict == TRUE_

    def test_next_day_time_does_not_count_as_published_today(self):
        r = _row(5, "10:00 PM")
        decide_rows([r], self.lst, False)
        assert r.verdict == TRUE_

    def test_no_panel_never_yields_true(self):
        target = today = date(2026, 8, 6)
        empty = parse_theater_listing({"organic_results": []}, "AMC Wayne 14", target, today, {}, False)
        r = _row(6, "10:45 PM")
        decide_rows([r], empty, False)
        assert r.verdict == UNKNOWN_

    def test_already_started_row_is_unable_to_determine_not_true(self):
        # Third block carries an explicit date to anchor the sequence — without
        # it, Today/Tomorrow fall back to the (wrong) machine clock and the
        # Aug-6 target date never resolves at all (DATE_NOT_IN_PANEL).
        dateless = {"showtimes": [
            {"day": "Today", "movies": [{"name": "Spider-Man: Brand New Day",
                                          "showing": [{"time": ["2:30pm", "10:30pm"], "type": "Standard"}]}]},
            {"day": "Tomorrow", "movies": [{"name": "Spider-Man: Brand New Day",
                                             "showing": [{"time": ["9:30am"], "type": "Standard"}]}]},
            {"day": "Sat", "date": "Aug 8", "movies": [{"name": "Spider-Man: Brand New Day",
                                                         "showing": [{"time": ["9:45am"], "type": "Standard"}]}]},
        ]}
        lst = parse_theater_listing(dateless, "AMC Wayne 14", date(2026, 8, 6), date(2026, 8, 7), {}, False)
        past = _row(7, "11:30 AM")
        future_gone = _row(8, "10:45 PM")
        future_ok = _row(9, "10:30 PM")
        decide_rows([past, future_gone, future_ok], lst, False)
        assert past.verdict == UNKNOWN_
        assert "ALREADY_PASSED" in past.reason
        assert future_gone.verdict == TRUE_
        assert future_ok.verdict == FALSE_


class TestBuildQuery:
    def test_bare_is_browser_equivalent(self):
        assert build_query("AMC Wayne 14", "bare") == "AMC Wayne 14"

    def test_plain_adds_showtimes(self):
        assert build_query("AMC Wayne 14", "plain") == "AMC Wayne 14 showtimes"

    def test_quoted_wraps_in_quotes(self):
        assert build_query("AMC Wayne 14", "quoted") == '"AMC Wayne 14" showtimes'


class TestApplySiteCheck:
    """apply_site_check() combines one site_adapters SiteListing (passed as
    plain by_title/status_map/method, per core.py's adapter-agnostic design)
    with the Google verdict decide_rows() already wrote onto the row. Ported
    row-level comparison logic, validated live in
    scripts/site_verify_batch.py's _process_theater during research."""

    def _google_true_row(self, show_min_time="7:00 PM", title="Barbie"):
        """A row Google marks TRUE (title not listed at all -> deleted by
        default), for tests where the site is expected to override it."""
        target = today = date(2026, 8, 6)
        lst = parse_theater_listing(FIXTURE, "AMC Naperville 16", target, today, {}, False)
        r = _row(1, show_min_time, title=title)
        decide_rows([r], lst, title_missing_is_deleted=True)
        assert r.verdict == TRUE_
        return r

    def _google_false_row(self):
        """A row Google confirms FALSE (exact match), for tests where the
        site is expected to override it with a TRUE (deleted) verdict."""
        target = today = date(2026, 8, 6)
        lst = parse_theater_listing(FIXTURE, "AMC Naperville 16", target, today, {}, False)
        r = _row(2, "10:30 PM")  # exact match in FIXTURE
        decide_rows([r], lst, title_missing_is_deleted=False)
        assert r.verdict == FALSE_
        return r

    def test_site_exact_match_overrides_google_true_to_false(self):
        r = self._google_true_row(show_min_time="7:00 PM", title="Barbie")
        apply_site_check(r, {"barbie": [19 * 60]}, {}, "AMC_NEXT_F_RSC", "https://example.com")
        assert r.google_verdict == TRUE_
        assert r.site_verdict == FALSE_
        assert r.verdict == FALSE_
        assert r.reason.startswith("SITE:")
        assert r.site_url == "https://example.com"

    def test_site_no_exact_match_overrides_google_false_to_true(self):
        r = self._google_false_row()
        # Site lists the title, but only at a different time -> confident TRUE.
        apply_site_check(r, {"spider man brand new day": [12 * 60 + 30]}, {}, "REGAL_NEXT_DATA", "https://example.com")
        assert r.google_verdict == FALSE_
        assert r.site_verdict == TRUE_
        assert r.verdict == TRUE_
        assert "SITE_NO_EXACT_MATCH" in r.site_reason

    def test_title_missing_from_listing_is_not_confirmed_absence(self):
        """Precision guard: a title entirely absent from the site's extracted
        listing could be a scrape/extraction gap, not a real removal — must
        NOT flip to TRUE. Falls back to Google's verdict unchanged."""
        r = self._google_true_row(show_min_time="7:00 PM", title="Barbie")
        apply_site_check(r, {"some other movie": [20 * 60]}, {}, "REGAL_NEXT_DATA", "https://example.com")
        assert r.site_verdict == UNKNOWN_
        assert r.site_reason == "TITLE_NOT_IN_STRUCTURED_LISTING"
        assert r.verdict == r.google_verdict == TRUE_  # unchanged

    def test_listed_but_not_sellable_does_not_confirm_or_deny(self):
        """Sold-out/not-sellable is a different defect from "deleted" — must
        never be folded into TRUE/FALSE."""
        r = self._google_false_row()
        norm = "spider man brand new day"
        apply_site_check(
            r, {norm: [22 * 60 + 30]}, {(norm, 22 * 60 + 30): "Soldout"}, "AMC_NEXT_F_RSC", "https://example.com",
        )
        assert r.site_verdict == UNKNOWN_
        assert "SITE_LISTED_NOT_SELLABLE" in r.site_reason
        assert r.verdict == r.google_verdict == FALSE_  # unchanged

    def test_already_passed_row_is_unable_to_determine_not_confirmed(self):
        """Mirrors decide_rows' own truncated/coverage_from guard: a row
        earlier than the site's earliest still-listed time is unverifiable
        (the site, like Google, drops already-started showtimes), not a
        confirmed match or miss."""
        r = self._google_false_row()  # row's show_min = 10:30 PM
        # Site's remaining listing today starts at 6:00 PM... but the row is
        # earlier than that only if we pick a row time before it. Re-derive
        # with an early row instead.
        target = today = date(2026, 8, 6)
        lst = parse_theater_listing(FIXTURE, "AMC Naperville 16", target, today, {}, False)
        early_row = _row(3, "11:00 AM")
        decide_rows([early_row], lst, title_missing_is_deleted=False)
        norm = "spider man brand new day"
        apply_site_check(early_row, {norm: [18 * 60]}, {}, "AMC_NEXT_F_RSC", "https://example.com")
        assert early_row.site_verdict == UNKNOWN_
        assert "SHOWTIME_ALREADY_PASSED_OR_TRUNCATED" in early_row.site_reason

    def test_empty_listing_leaves_google_verdict_untouched(self):
        """No structured data at all (bot-walled, unresolved URL, etc.) ->
        site_verdict stays inconclusive, method code becomes the reason."""
        r = self._google_false_row()
        apply_site_check(r, {}, {}, "URL_NOT_RESOLVED", None)
        assert r.site_verdict == UNKNOWN_
        assert r.site_reason == "URL_NOT_RESOLVED"
        assert r.site_url == ""
        assert r.verdict == r.google_verdict == FALSE_


class TestPlainSiteReason:
    def test_known_codes_map_to_plain_english(self):
        assert "not available" in plain_site_reason("NO_ADAPTER_FOR_CIRCUIT").lower()
        assert "could not find" in plain_site_reason("URL_NOT_RESOLVED").lower()
        assert "confirmed present" in plain_site_reason("SITE_EXACT_MATCH status=Sellable").lower()
        assert "different time" in plain_site_reason(
            "SITE_NO_EXACT_MATCH nearest=6:00 PM published=['6:00 PM']"
        ).lower()

    def test_unknown_code_gets_generic_fallback(self):
        assert plain_site_reason("SOME_FUTURE_CODE_NOT_YET_MAPPED") == (
            "Site check inconclusive — verdict based on Google only"
        )


class TestShortTheaterName:
    def test_bnb_long_name_shortens(self):
        assert (
            short_theater_name("B & B Lee's Summit 16 with Grand Screen & MX4D")
            == "B & B Lee's Summit 16"
        )

    def test_clean_name_yields_no_variant(self):
        assert short_theater_name("AMC Wayne 14") == ""

    def test_ampersand_rpx_suffix_is_trimmed(self):
        assert short_theater_name("Regal Opry Mills IMAX & RPX") == "Regal Opry Mills IMAX"

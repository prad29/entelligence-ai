"""
Unit tests for app.deleted_showtimes.site_adapters — extraction logic per
adapter, plus registry resolution and the AMC name-mismatch regression.

Fixtures below are hand-built minimal HTML/JSON that reproduces the exact
structural shape each adapter's regex/parser expects (RSC push chunks,
__NEXT_DATA__, EasyTixs cards, TN ticketing markup, etc.) as confirmed live
against real theater pages during research — not literal captures of those
pages (none were saved to disk during that session), but structurally
faithful to what each adapter must handle correctly.

Pure extraction/matching functions only — no network calls, no DB, matching
this codebase's test convention for the rest of app.deleted_showtimes.
"""

from __future__ import annotations

import json
from datetime import date

from app.deleted_showtimes.normalize import norm_title
from app.deleted_showtimes.site_adapters import (
    amc,
    bransons_imax,
    reel_theatre,
    regal,
    science_north,
)
from app.deleted_showtimes.site_adapters import registry
from app.deleted_showtimes.site_adapters.base import SiteCheckContext


class TestRegalAdapter:
    def test_extracts_exact_performance_minutes_by_title(self):
        next_data = {
            "props": {"pageProps": {"movies": [
                {"Title": "Spider-Man: Brand New Day", "Performances": [
                    {"CalendarShowTime": "2026-08-06T12:30:00"},
                    {"CalendarShowTime": "2026-08-06T22:30:00"},
                ]},
                {"Title": "The Odyssey", "Performances": [
                    {"CalendarShowTime": "2026-08-06T22:55:00"},
                ]},
            ]}},
        }
        html = f'<html><script id="__NEXT_DATA__">{json.dumps(next_data)}</script></html>'
        by_title = regal._extract_performances(html)
        assert by_title[norm_title("Spider-Man: Brand New Day")] == [12 * 60 + 30, 22 * 60 + 30]
        assert by_title[norm_title("The Odyssey")] == [22 * 60 + 55]

    def test_no_next_data_yields_empty_listing(self):
        assert regal._extract_performances("<html><body>nothing here</body></html>") == {}

    def test_url_is_slugified_from_theater_name(self):
        assert regal.regal_url("Regal Edwards Ontario Palace IMAX & RPX") == (
            "https://www.regmovies.com/theatres/regal-edwards-ontario-palace-imax-rpx"
        )


class TestAmcAdapter:
    def _rsc_html(self, entries):
        """Build minimal self.__next_f.push([1, "...json..."]) chunks, one
        per performance, matching the real page's per-showtime object shape."""
        chunks = []
        for aria, time_str, ampm, status in entries:
            obj = {
                "aria-describedby": aria,
                "showtime": {"display": {"time": time_str, "amPm": ampm}, "status": status},
            }
            # separators=(",", ":") mirrors AMC's real minified RSC payload —
            # the adapter's regex ('"showtime":{' with no space) depends on it.
            inner = json.dumps(obj, separators=(",", ":"))
            chunks.append(f'<script>self.__next_f.push([1, {json.dumps(inner)}])</script>')
        return "<html>" + "".join(chunks) + "</html>"

    def test_extracts_titles_minutes_and_status(self):
        html = self._rsc_html([
            ("resident-evil-79697 details", "7:30", "PM", "Sellable"),
            ("resident-evil-79697 details", "10:30", "PM", "Soldout"),
        ])
        by_title, status_map = amc._extract_showtimes(html)
        norm = norm_title("resident evil")
        assert sorted(by_title[norm]) == [19 * 60 + 30, 22 * 60 + 30]
        assert status_map[(norm, 19 * 60 + 30)] == "Sellable"
        assert status_map[(norm, 22 * 60 + 30)] == "Soldout"

    def test_slug_matches_requires_every_distinguishing_token(self):
        # The real regression: "AMC Southcenter 16" must NOT match the
        # decoy "amc-south-bay-galleria-16" slug AMC's own site returned.
        assert not amc._amc_slug_matches_theater("AMC Southcenter 16", "amc-south-bay-galleria-16")
        # But a slug with extra words beyond the theater name is fine.
        assert amc._amc_slug_matches_theater("AMC Del Amo 18", "amc-del-amo-18")
        assert amc._amc_slug_matches_theater(
            "AMC Garden State Plaza 16", "amc-garden-state-plaza-16-with-mx4d"
        )

    def test_resolve_candidates_filters_by_slug_match_and_skips_undefined_market(self):
        serp_data = {"organic_results": [
            {"link": "https://www.amctheatres.com/movie-theatres/undefined/amc-southcenter-16/showtimes"},
            {"link": "https://www.amctheatres.com/movie-theatres/seattle-wa/amc-south-bay-galleria-16/showtimes"},
            {"link": "https://www.amctheatres.com/movie-theatres/tukwila-wa/amc-southcenter-16"},
        ]}
        candidates = amc._resolve_candidates(serp_data, "AMC Southcenter 16")
        assert candidates == [
            "https://www.amctheatres.com/movie-theatres/tukwila-wa/amc-southcenter-16/showtimes"
        ]

    def test_resolve_candidates_empty_when_nothing_matches(self):
        serp_data = {"organic_results": [
            {"link": "https://www.amctheatres.com/movie-theatres/seattle-wa/amc-south-bay-galleria-16/showtimes"},
        ]}
        assert amc._resolve_candidates(serp_data, "AMC Southcenter 16") == []


class TestReelTheatreAdapter:
    HTML = (
        '<div id="film-1" class="showtime">'
        '<h3 class="title">Spider-Man: Brand New Day</h3>'
        '<div data-date="20260806">'
        '<a>7:00pm</a><a>10:00pm</a>'
        '</div>'
        '<div data-date="20260807">'
        '<a>6:30pm</a>'
        '</div>'
        '</div>'
    )

    def test_scopes_extraction_to_the_target_date_only(self):
        by_title = reel_theatre._extract_listing(self.HTML, "20260806")
        assert by_title[norm_title("Spider-Man: Brand New Day")] == [19 * 60, 22 * 60]

    def test_does_not_leak_tomorrows_showtimes_into_today(self):
        """The confirmed live bug: without exact date scoping, tomorrow's
        6:30pm show would merge into today's listing."""
        by_title = reel_theatre._extract_listing(self.HTML, "20260806")
        assert (18 * 60 + 30) not in by_title[norm_title("Spider-Man: Brand New Day")]

    def test_different_target_date_scopes_correctly(self):
        by_title = reel_theatre._extract_listing(self.HTML, "20260807")
        assert by_title[norm_title("Spider-Man: Brand New Day")] == [18 * 60 + 30]


class TestBransonsImaxAdapter:
    HTML = (
        '<h3>Spider-Man: Brand New Day</h3>'
        '<div class="showtimes-container"><a>7:00PM</a><a>10:00PM</a></div>'
        # Duplicated below, exactly like the real homepage's featured-carousel
        # + full-grid repetition.
        '<h3>Spider-Man: Brand New Day in IMAX</h3>'
        '<div class="showtimes-container"><a>7:00PM</a><a>10:00PM</a></div>'
    )

    def test_dedupes_repeated_carousel_and_grid_cards(self):
        by_title = bransons_imax._extract_listing(self.HTML)
        assert by_title[norm_title("Spider-Man: Brand New Day")] == [19 * 60, 22 * 60]
        assert len(by_title) == 1


class TestScienceNorthAdapter:
    TICKETING_HTML = (
        '<span class="tn-prod-list-item__perf-date">September 22, 2026</span>'
        '<span class="tn-prod-list-item__perf-time">7:00 PM</span>'
        '<span class="tn-performance-title">Some Film</span>'
        '<span class="tn-prod-list-item__perf-date">September 23, 2026</span>'
        '<span class="tn-prod-list-item__perf-time">7:00 PM</span>'
        '<span class="tn-performance-title">Some Film</span>'
    )

    def test_filters_to_exact_target_date_label(self):
        class FakeResp:
            status_code = 200
            text = TestScienceNorthAdapter.TICKETING_HTML

        class FakeScrapedo:
            def fetch(self, url, **kwargs):
                return FakeResp()

        ctx = SiteCheckContext(scrapedo=FakeScrapedo(), serp_client=None, serp_data=None,
                                today=date(2026, 9, 22))
        by_title = science_north._extract_listing(ctx, {"some film": "https://order.sciencenorth.ca/overview/1"})
        assert by_title["some film"] == [19 * 60]


class TestRegistry:
    def test_resolves_amc_by_circuit(self):
        assert registry.resolve("AMC Del Amo 18", "AMC Entertainment Inc") is amc.fetch

    def test_resolves_amc_by_inferred_prefix_when_no_circuit_column(self):
        assert registry.resolve("AMC Del Amo 18", "") is amc.fetch

    def test_bransons_and_science_north_resolve_by_exact_theater_name_despite_shared_circuit(self):
        assert registry.resolve(bransons_imax.THEATER_NAME, "IMAX") is bransons_imax.fetch
        assert registry.resolve(science_north.THEATER_NAME, "IMAX") is science_north.fetch

    def test_unsupported_circuit_falls_back_to_none(self):
        assert registry.resolve("Cinemark Legacy", "Cinemark USA") is None
        assert registry.resolve("Cinemark Legacy", "") is None

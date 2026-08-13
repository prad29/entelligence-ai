"""
Tests for app.intl_detection.engine, using the intl_engine fixture in conftest.py
(built from ~15 real entries drawn from the actual International Amenities
Priorities.xlsx).
"""

import pytest


class TestTrackMatching:
    def test_track_a_exact(self, intl_engine):
        result = intl_engine.detect("4DX")
        assert result.screen_format == "4DX"
        assert result.match_track == "A"
        assert result.confidence == 1.0

    def test_track_a_case_and_whitespace_insensitive(self, intl_engine):
        result = intl_engine.detect("  screenx  ")
        assert result.screen_format == "ScreenX"

    def test_track_b_stopword_hyphen(self, intl_engine):
        result = intl_engine.detect("ONYX - Pathe")
        assert result.screen_format == "ONYX - Pathe"
        assert result.match_track == "B"

    def test_track_c_token_set(self, intl_engine):
        result = intl_engine.detect("4DX 3D Premium")
        assert result.screen_format == "4DX"
        assert result.match_track == "C"
        assert result.confidence == 0.75

    def test_concat_exact_short_keyword(self, intl_engine):
        result = intl_engine.detect("4DX3D")
        assert result.screen_format == "4DX"

    def test_short_keyword_xd(self, intl_engine):
        result = intl_engine.detect("XD")
        assert result.screen_format == "XD"

    def test_short_keyword_xl(self, intl_engine):
        result = intl_engine.detect("XL")
        assert result.screen_format == "XL"


class TestAccentFolding:
    def test_accented_form(self, intl_engine):
        result = intl_engine.detect("4DX Voorpremière")
        assert result.screen_format == "4DX"

    def test_unaccented_form(self, intl_engine):
        result = intl_engine.detect("4DX Voorpremiere")
        assert result.screen_format == "4DX"


class TestTierPrecedenceAndSegmentation:
    def test_tier_precedence_p1_beats_p3(self, intl_engine):
        # KinoEvolution is P1; ScreenX is P3 — P1 must win regardless of position.
        result = intl_engine.detect("ScreenX | KinoEvolution")
        assert result.screen_format == "KinoEvolution"
        assert result.priority_tier == 1

    def test_pipe_segmentation_ignore_tokens_dropped(self, intl_engine):
        result = intl_engine.detect("Reserved Seating | ScreenX")
        assert result.screen_format == "ScreenX"


class TestNoMatchAndStandardDisambiguation:
    def test_no_match(self, intl_engine):
        result = intl_engine.detect("Comfy Recliners")
        assert result.screen_format == "Standard"
        assert result.match_source == "No Match"
        assert result.confidence == 0.0
        assert result.fired_ai is False

    def test_deliberate_standard_p5(self, intl_engine):
        result = intl_engine.detect("70MM")
        assert result.screen_format == "Standard"
        assert result.match_source == "Keyword Match"
        assert result.priority_tier == 5

    def test_empty_string_input(self, intl_engine):
        result = intl_engine.detect("")
        assert result.screen_format == "Standard"
        assert result.match_source == "No Match"
        assert result.confidence == 0.0
        assert result.fired_ai is False


class TestSignatureDivergesFromDomestic:
    def test_detect_takes_no_circuit_argument(self, intl_engine):
        with pytest.raises(TypeError):
            intl_engine.detect("4DX", "AMC")

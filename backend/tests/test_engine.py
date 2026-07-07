"""
All 19 §14 acceptance tests plus key §12 edge cases.

Test data is built inline — no DB dependency.
The engine fixture is shared via conftest.py.
"""

import pytest


class TestAcceptanceSuite:
    def test_at01_imax_generic(self, engine):
        result = engine.detect("IMAX", "")
        assert result.screen_format == "IMAX"

    def test_at02_4dx_beats_imax(self, engine):
        result = engine.detect("IMAX | 4DX", "")
        assert result.screen_format == "4DX"

    def test_at03_dolby_cinema(self, engine):
        result = engine.detect("Dolby Cinema", "")
        assert result.screen_format == "Dolby Cinema"

    def test_at04_xl_amc_circuit(self, engine):
        result = engine.detect("XL", "AMC Entertainment Inc")
        assert result.screen_format == "XL at AMC"

    def test_at05_xl_unknown_circuit(self, engine):
        result = engine.detect("XL", "Some Random Theatre")
        assert result.screen_format == "Standard"

    def test_at06_xd_cinemark(self, engine):
        result = engine.detect("XD", "Cinemark Theatres")
        assert result.screen_format == "Cinemark XD"

    def test_at07_xd_na_default(self, engine):
        result = engine.detect("XD", "")
        assert result.screen_format == "XD Strike + Reel"

    def test_at08_acx_apple(self, engine):
        result = engine.detect("ACX", "Apple Cinemas")
        assert result.screen_format == "ACX Apple"

    def test_at09_acx_generic(self, engine):
        # ACX at unknown circuit → NA default (ACX) kept
        result = engine.detect("ACX", "Landmark Cinemas")
        assert result.screen_format == "ACX"

    def test_at10_gdx_universal(self, engine):
        result = engine.detect("GDX", "")
        assert result.screen_format == "GDX"

    def test_at11_cine_xl_harkins(self, engine):
        result = engine.detect("CINE XL", "Harkins Theatres")
        assert result.screen_format == "Cine XL Harkins"

    def test_at12_ultraavx_cineplex(self, engine):
        result = engine.detect("UltraAVX", "Cineplex Entertainment")
        assert result.screen_format == "UltraAVX"

    def test_at13_vip_cineplex(self, engine):
        result = engine.detect("IMAX | VIP 19+", "Cineplex Entertainment")
        assert result.screen_format == "VIP Cineplex"
        assert result.match_source == "VIP Override"

    def test_at14_acx_infinity_2d_standard(self, engine):
        result = engine.detect("ACX Infinity 2D", "")
        assert result.screen_format == "Standard"

    def test_at15_digital_3d_standard(self, engine):
        result = engine.detect("Digital 3D", "")
        assert result.screen_format == "Standard"

    def test_at16_70mm_standard(self, engine):
        result = engine.detect("70MM", "")
        assert result.screen_format == "Standard"

    def test_at17_unknown_amenity_fires_ai(self, engine):
        result = engine.detect("Holographic Surround Plus", "")
        assert result.screen_format == "Standard"
        assert result.fired_ai is True

    def test_at18_empty_amenity(self, engine):
        result = engine.detect("", "")
        assert result.screen_format == "Standard"
        assert result.match_source == "Empty Input"

    def test_at19_ignore_tokens_filtered(self, engine):
        result = engine.detect("IMAX | CC | Reserved Seating", "")
        assert result.screen_format == "IMAX"


class TestEdgeCases:
    def test_4dx_beats_imax_btx(self, engine):
        result = engine.detect("4DX | IMAX | BTX", "")
        assert result.screen_format == "4DX"

    def test_leftmost_wins_dolby_imax(self, engine):
        result = engine.detect("Dolby Cinema | IMAX", "")
        assert result.screen_format == "Dolby Cinema"

    def test_leftmost_wins_imax_dolby(self, engine):
        result = engine.detect("IMAX | Dolby Cinema", "")
        assert result.screen_format == "IMAX"

    def test_track_c_corruption(self, engine):
        result = engine.detect("I--Maxentral Premium", "")
        assert result.screen_format == "IMAX"
        assert result.match_track == "C"

    def test_case_insensitive(self, engine):
        result = engine.detect("imax", "")
        assert result.screen_format == "IMAX"

    def test_vip_beats_imax_cineplex(self, engine):
        result = engine.detect("VIP | UltraAVX Cineplex | Dolby Atmos", "Cineplex Entertainment")
        assert result.screen_format == "VIP Cineplex"

    def test_empty_standard_no_ai(self, engine):
        result = engine.detect("", "")
        assert result.screen_format == "Standard"
        assert result.fired_ai is False

    def test_p6_standard_no_ai(self, engine):
        result = engine.detect("Digital 3D", "")
        assert result.screen_format == "Standard"
        assert result.fired_ai is False

    def test_rpx_regal_circuit_match(self, engine):
        result = engine.detect("RPX", "Regal Cinemas")
        assert result.screen_format == "RPX Regal"
        assert result.fired_ai is False

    def test_rpx_regal_full_amenity_string(self, engine):
        # Exact string from feature request — RPX should win for Regal
        amenity = "2D | CC | DV | HDR | Laser | No Passes | RPX | Stadium | Reserved-Selected"
        result = engine.detect(amenity, "Regal Cinemas")
        assert result.screen_format == "RPX Regal"
        assert result.fired_ai is False

    def test_p4_circuit_beats_generic_generic_first(self, engine):
        # GDX is P4 global; RPX is P4 Regal-scoped; circuit-specific wins even when generic is first
        result = engine.detect("GDX | RPX", "Regal Cinemas")
        assert result.screen_format == "RPX Regal"

    def test_p4_circuit_beats_generic_circuit_first(self, engine):
        # Circuit-specific first — also wins (baseline sanity)
        result = engine.detect("RPX | GDX", "Regal Cinemas")
        assert result.screen_format == "RPX Regal"

    def test_rpx_unknown_circuit_returns_standard(self, engine):
        # RPX with non-Regal circuit, no na_default → Standard, no AI invocation
        result = engine.detect("RPX", "Some Other Theatre")
        assert result.screen_format == "Standard"
        assert result.fired_ai is False


class TestImaxCircuitOverride:
    def test_blank_amenity_returns_imax(self, engine):
        result = engine.detect("", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_blank_amenity_returns_imax_lowercase_circuit(self, engine):
        result = engine.detect("", "imax")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p1_4dx_not_overridden(self, engine):
        result = engine.detect("4DX", "IMAX")
        assert result.screen_format == "4DX"
        assert result.match_source != "IMAX Circuit Override"

    def test_p1_mx4d_not_overridden(self, engine):
        result = engine.detect("MX4D", "IMAX")
        assert result.screen_format == "MX4D"
        assert result.match_source != "IMAX Circuit Override"

    def test_p2_dolby_cinema_overridden(self, engine):
        result = engine.detect("Dolby Cinema", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p2_imax_keyword_overridden(self, engine):
        result = engine.detect("IMAX", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p3_btx_overridden(self, engine):
        result = engine.detect("BTX", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p3_screenx_overridden(self, engine):
        result = engine.detect("ScreenX", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p4_gdx_overridden(self, engine):
        result = engine.detect("GDX", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p6_standard_overridden(self, engine):
        result = engine.detect("Digital 3D", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_no_match_returns_imax_not_ai(self, engine):
        result = engine.detect("Holographic Surround Plus", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"
        assert result.fired_ai is False

    def test_dolby_and_imax_keywords_returns_imax(self, engine):
        result = engine.detect("Dolby Cinema | IMAX", "IMAX")
        assert result.screen_format == "IMAX"
        assert result.match_source == "IMAX Circuit Override"

    def test_p1_4dx_wins_among_mixed_segments(self, engine):
        result = engine.detect("Dolby Cinema | 4DX | BTX", "IMAX")
        assert result.screen_format == "4DX"

    def test_amc_imax_not_overridden(self, engine):
        result = engine.detect("Dolby Cinema", "AMC IMAX")
        assert result.screen_format == "Dolby Cinema"
        assert result.match_source != "IMAX Circuit Override"

    def test_abc_imax_not_overridden(self, engine):
        result = engine.detect("BTX", "ABC IMAX")
        assert result.screen_format == "BTX"
        assert result.match_source != "IMAX Circuit Override"

    def test_abc_imax_blank_amenity_returns_standard(self, engine):
        result = engine.detect("", "ABC IMAX")
        assert result.screen_format == "Standard"
        assert result.match_source == "Empty Input"

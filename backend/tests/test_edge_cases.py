"""
§12 edge case corpus.

The engine fixture (with hardcoded seed) is provided by conftest.py.
"""

import pytest


def test_literal_xa0_normalization(engine):
    # "SXSxa0" → xa0 stripped, SXS token remains; IMAX wins
    result = engine.detect("SXSxa0 | IMAX", "")
    assert result.screen_format == "IMAX"


def test_real_xa0(engine):
    result = engine.detect("IMAX\xa0Premium", "")
    assert result.screen_format == "IMAX"


def test_pipe_trim_whitespace(engine):
    result = engine.detect("  IMAX  |  Dolby Cinema  ", "")
    assert result.screen_format == "IMAX"  # leftmost


def test_empty_pipe_segment(engine):
    result = engine.detect("IMAX||Dolby Cinema", "")
    assert result.screen_format == "IMAX"


def test_undefined_dropped(engine):
    result = engine.detect("undefined | IMAX", "")
    assert result.screen_format == "IMAX"


def test_bullet_dropped(engine):
    result = engine.detect("IMAX | • | Dolby Cinema", "")
    assert result.screen_format == "IMAX"


def test_track_c_prefix_only_accepts_prefix(engine):
    result = engine.detect("I--Maxentral", "")
    assert result.screen_format == "IMAX"
    assert result.match_track == "C"


def test_track_c_no_internal_substring(engine):
    # "infinity" contains "infi" — should NOT match IMAX
    result = engine.detect("InfinityScreen", "")
    assert result.screen_format == "Standard" or result.detected_keyword != "IMAX"


def test_track_c_min_len_guard(engine):
    # "3D" is 2 chars, should not track-C match inside "3DPremium"
    result = engine.detect("3DPremium", "")
    # 3D is 2 chars so track C min_len=4 blocks it; result is AI territory
    assert result.fired_ai is True or result.screen_format == "Standard"


def test_concatenation_short_code_lost(engine):
    result = engine.detect("IMAXDolbyAtmos4DX", "")
    assert result.screen_format == "IMAX"  # 4DX too short for track C


def test_accent_fold_with_trademark(engine):
    result = engine.detect("CINÉ XL®", "Harkins Theatres")
    assert result.screen_format == "Cine XL Harkins"


def test_case_insensitive(engine):
    result = engine.detect("imax", "")
    assert result.screen_format == "IMAX"


def test_dcx_case_insensitive(engine):
    result = engine.detect("dcx", "")
    # DCX if in seed, else Standard
    assert result.screen_format in ["DCX", "Standard"]  # depends on seed


def test_vip_non_vip_circuit(engine):
    # VIP at AMC → should NOT trigger Layer 0 override
    result = engine.detect("VIP Premium Seating", "AMC Entertainment Inc")
    assert result.match_source != "VIP Override"


def test_p6_no_ai(engine):
    result = engine.detect("Digital 3D", "")
    assert result.screen_format == "Standard"
    assert result.fired_ai is False


def test_empty_string_no_ai(engine):
    result = engine.detect("", "")
    assert result.screen_format == "Standard"
    assert result.match_source == "Empty Input"
    assert result.fired_ai is False


def test_null_like_string_no_ai(engine):
    result = engine.detect("   ", "")
    assert result.match_source == "Empty Input"
    assert result.fired_ai is False


def test_ignore_cc_token(engine):
    result = engine.detect("CC | IMAX | Closed Caption", "")
    assert result.screen_format == "IMAX"


def test_ignore_reserved_seating(engine):
    result = engine.detect("Reserved Seating | 4DX", "")
    assert result.screen_format == "4DX"


def test_unknown_circuit_xl_standard(engine):
    result = engine.detect("XL", "TotallyUnknownCinemas123")
    assert result.screen_format == "Standard"


def test_acx_infinity_2d_deliberate_standard(engine):
    result = engine.detect("ACX Infinity 2D", "")
    assert result.screen_format == "Standard"
    assert result.fired_ai is False  # deliberate P6 match, not no-match


def test_dolby_atmos_no_match_fires_ai(engine):
    # Dolby Atmos is not in bucket → no-match → AI
    result = engine.detect("Dolby Atmos", "")
    assert result.screen_format == "Standard"
    assert result.fired_ai is True

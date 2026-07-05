"""
Shared pytest fixtures for the Amenity Screen Format Detector test suite.

The engine fixture is built from a hard-coded seed so tests have no
DB or Bedrock dependency.
"""

import pytest

from app.detection.types import ApprovedMapping
from app.detection.normalizer import normalize_string, track_a_clean, track_b_clean, track_c_tokens
from app.detection.engine import MappingIndex, ScreenFormatEngine


def _make_mapping(
    keyword: str,
    fmt: str,
    tier: int,
    circuit_name: str | None = None,
    na_default: str | None = None,
) -> ApprovedMapping:
    return ApprovedMapping(
        amenity_keyword=keyword,
        screen_format=fmt,
        priority_tier=tier,
        circuit_name=circuit_name,
        na_default=na_default,
        norm_exact=normalize_string(keyword).lower(),
        norm_track_a=track_a_clean(keyword),
        norm_track_b=track_b_clean(keyword),
        norm_track_c=track_c_tokens(keyword),
    )


CIRCUIT_ALIASES: dict[str, str] = {
    "amc entertainment inc": "AMC Entertainment Inc",
    "amc": "AMC Entertainment Inc",
    "cinemark theatres": "Cinemark Theatres",
    "cinemark": "Cinemark Theatres",
    "apple cinemas": "Apple Cinemas",
    "harkins theatres": "Harkins Theatres",
    "cineplex entertainment": "Cineplex Entertainment",
    "landmark cinemas": "Landmark Cinemas",
    "caribbean cinemas - us territory": "Caribbean Cinemas - US Territory",
    "regal cinemas": "Regal Cinemas",
    "regal": "Regal Cinemas",
}

# P1 — Motion / 4D
_4DX = _make_mapping("4DX", "4DX", 1)
_MX4D = _make_mapping("MX4D", "MX4D", 1)

# P2 — Premium branded
_IMAX = _make_mapping("IMAX", "IMAX", 2)
_DOLBY = _make_mapping("Dolby Cinema", "Dolby Cinema", 2)

# P3 — Other premium
_BTX = _make_mapping("BTX", "BTX", 3)
_SCREENX = _make_mapping("ScreenX", "ScreenX", 3)

# P4 — Circuit-branded PLFs (circuit_name set, na_default as fallback)
_XL = _make_mapping("XL", "XL at AMC", 4, circuit_name="AMC Entertainment Inc")
_XD = _make_mapping("XD", "Cinemark XD", 4, circuit_name="Cinemark Theatres", na_default="XD Strike + Reel")
_ACX = _make_mapping("ACX", "ACX Apple", 4, circuit_name="Apple Cinemas", na_default="ACX")
_GDX = _make_mapping("GDX", "GDX", 4)
_CINE_XL = _make_mapping("CINE XL", "Cine XL Harkins", 4, circuit_name="Harkins Theatres")
_ULTRA_AVX = _make_mapping("UltraAVX", "UltraAVX", 4, circuit_name="Cineplex Entertainment")
_ACX_INFINITY = _make_mapping("ACX Infinity", "ACX Infinity", 4)
_DCX = _make_mapping("DCX", "DCX", 4)
_RPX_REGAL = _make_mapping("RPX", "RPX Regal", 4, circuit_name="Regal Cinemas")

# P6 — Deliberate Standard (no AI)
_ACX_INFINITY_2D = _make_mapping("ACX Infinity 2D", "Standard", 6)
_DIGITAL_3D = _make_mapping("Digital 3D", "Standard", 6)
_70MM = _make_mapping("70MM", "Standard", 6)
_DIGITAL = _make_mapping("Digital", "Standard", 6)
_3D = _make_mapping("3D", "Standard", 6)

ALL_MAPPINGS: list[ApprovedMapping] = [
    _4DX, _MX4D,
    _IMAX, _DOLBY,
    _BTX, _SCREENX,
    _XL, _XD, _ACX, _GDX, _CINE_XL, _ULTRA_AVX, _ACX_INFINITY, _DCX, _RPX_REGAL,
    _ACX_INFINITY_2D,
    _DIGITAL_3D, _70MM, _DIGITAL, _3D,
]

# Kept for backward compat — engine ignores it
CIRCUIT_OVERRIDES: list = []


@pytest.fixture(scope="module")
def engine() -> ScreenFormatEngine:
    idx = MappingIndex(mappings=ALL_MAPPINGS, aliases=CIRCUIT_ALIASES)
    return ScreenFormatEngine(idx)

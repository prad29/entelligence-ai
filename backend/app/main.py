from fastapi import FastAPI

from app.routers import detect

app = FastAPI(
    title="Amenity Screen Format Detector",
    description="Detect cinema screen formats from amenity strings.",
    version="0.1.0",
)

app.include_router(detect.router)


def _build_phase1_engine():
    """
    In-memory seed for Phase 1.
    Phase 2 replaces this with DB-backed EngineLoader.
    """
    from app.detection.types import ApprovedMapping, CircuitOverrideEntry
    from app.detection.normalizer import (
        normalize_string,
        track_a_clean,
        track_b_clean,
        track_c_tokens,
    )
    from app.detection.engine import MappingIndex, ScreenFormatEngine

    def _compile(m: ApprovedMapping) -> ApprovedMapping:
        kw = m.amenity_keyword
        m.norm_exact = normalize_string(kw).lower()
        m.norm_track_a = track_a_clean(kw)
        m.norm_track_b = track_b_clean(kw)
        m.norm_track_c = track_c_tokens(kw)
        return m

    def _m(keyword, fmt, tier, circuit_name=None, na_default=None):
        return _compile(ApprovedMapping(
            amenity_keyword=keyword,
            screen_format=fmt,
            priority_tier=tier,
            circuit_name=circuit_name,
            na_default=na_default,
        ))

    mappings = [
        # Tier 1
        _m("4DX", "4DX", 1),
        _m("MX4D", "MX4D", 1),
        # Tier 2
        _m("IMAX", "IMAX", 2),
        _m("Dolby Cinema", "Dolby Cinema", 2),
        # Tier 3
        _m("BTX", "BTX", 3),
        _m("ScreenX", "ScreenX", 3),
        # Tier 4 — circuit-sensitive
        _m("XL", "XL at AMC", 4, na_default=None),
        _m("XD", "Cinemark XD", 4, na_default="XD Strike + Reel"),
        _m("ACX", "ACX at Apple Cinemas", 4, na_default="ACX"),
        _m("GDX", "GDX", 4),
        _m("CINE XL", "Cine XL at Harkins Theatres", 4, na_default=None),
        _m("UltraAVX", "UltraAVX", 4),
        _m("ACX Infinity", "ACX Infinity", 4),
        # Tier 6 — deliberate Standard
        _m("ACX Infinity 2D", "Standard", 6),
        _m("Digital 3D", "Standard", 6),
        _m("70MM", "Standard", 6),
        _m("Digital", "Standard", 6),
        _m("3D", "Standard", 6),
    ]

    overrides = [
        CircuitOverrideEntry("XL", "AMC Entertainment Inc", "XL at AMC"),
        CircuitOverrideEntry("XD", "Cinemark Theatres", "Cinemark XD"),
        CircuitOverrideEntry("ACX", "Apple Cinemas", "ACX at Apple Cinemas"),
        CircuitOverrideEntry("CINE XL", "Harkins Theatres", "Cine XL at Harkins Theatres"),
        CircuitOverrideEntry("UltraAVX", "Cineplex Entertainment", "UltraAVX"),
    ]

    circuit_aliases = {
        "amc entertainment inc": "AMC Entertainment Inc",
        "amc": "AMC Entertainment Inc",
        "cinemark theatres": "Cinemark Theatres",
        "cinemark": "Cinemark Theatres",
        "apple cinemas": "Apple Cinemas",
        "harkins theatres": "Harkins Theatres",
        "cineplex entertainment": "Cineplex Entertainment",
        "landmark cinemas": "Landmark Cinemas",
        "caribbean cinemas - us territory": "Caribbean Cinemas - US Territory",
    }

    idx = MappingIndex(
        mappings=mappings,
        overrides=overrides,
        circuit_aliases=circuit_aliases,
    )
    return ScreenFormatEngine(idx)


@app.on_event("startup")
async def startup() -> None:
    app.state.engine = _build_phase1_engine()

"""
Circuit/theater-name -> adapter lookup for the Deleted Showtimes
site-verification step.

`resolve()` returns `None` for any theater with no matching adapter — the
existing Google-only verdict then applies unchanged, exactly as it did
before this step existed. Circuits without a built adapter (Cinemark,
Marcus, CMX, B&B, and any future new chain) are the expected common case,
not an error.

Circuit detection uses the upload's `Circuit Name` column when present
(passed in as `circuit_name`); otherwise it's inferred from the theater-name
prefix, extending the same convention `normalize._THEATER_NOISE` already
uses for Google-side theater-name matching — so a sheet without a Circuit
Name column still gets adapter coverage, and the upload contract stays
backward-compatible (no new required column).
"""

from __future__ import annotations

from typing import Optional

from app.deleted_showtimes.site_adapters import amc, bransons_imax, regal, reel_theatre, science_north
from app.deleted_showtimes.site_adapters.base import AdapterFn  # noqa: F401  (re-exported for callers)

# Exact theater-name matches take priority over circuit matches — Branson's
# and Science North both carry Circuit Name "IMAX", which has no adapter of
# its own (IMAX is a format/brand, not a chain with one common website).
_THEATER_NAME_ADAPTERS = {
    bransons_imax.THEATER_NAME: bransons_imax.fetch,
    science_north.THEATER_NAME: science_north.fetch,
}

_CIRCUIT_ADAPTERS = {
    amc.CIRCUIT_NAME: amc.fetch,
    regal.CIRCUIT_NAME: regal.fetch,
    reel_theatre.CIRCUIT_NAME: reel_theatre.fetch,
}

# theater-name prefix (lowercased) -> circuit name, used only when the upload
# has no Circuit Name column at all.
_CIRCUIT_PREFIX_HINTS = [
    ("amc", amc.CIRCUIT_NAME),
    ("regal", regal.CIRCUIT_NAME),
    ("reel theatre", reel_theatre.CIRCUIT_NAME),
]


def infer_circuit(theater_name: str, circuit_name: str = "") -> str:
    if circuit_name:
        return circuit_name
    lower = theater_name.strip().lower()
    for prefix, circuit in _CIRCUIT_PREFIX_HINTS:
        if lower.startswith(prefix):
            return circuit
    return ""


def resolve(theater_name: str, circuit_name: str = "") -> Optional[AdapterFn]:
    adapter = _THEATER_NAME_ADAPTERS.get(theater_name)
    if adapter is not None:
        return adapter
    circuit = infer_circuit(theater_name, circuit_name)
    return _CIRCUIT_ADAPTERS.get(circuit)

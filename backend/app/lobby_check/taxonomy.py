"""Fixed enums for lobby-check extraction. 22 material types, 11 defects,
2 conditions — ported verbatim from the validated mmvision.py prototype
(see repo root; handoff §6 there: "do not add the withheld 15" material
types without re-running the accuracy eval).
"""

from __future__ import annotations

MATERIAL_TYPES: list[str] = [
    "One Sheet", "Banner", "Banner Stand", "Easel Back Standee", "Spectacular Standee",
    "Box Standee", "Wrap", "Floor Decal", "Counter Card", "Static Clings",
    "Billboards/Marquees", "Video Wall", "Costume Displays", "Bus Shelter",
    "Digital Bus Shelter", "Digital Displays",
    "Popcorn Tub", "Drink Cup", "Kids Tray", "Buttons (Staff Worn)", "T-Shirts (Staff Worn)",
    "Other",
]

DEFECTS: list[str] = [
    "tear", "crease_or_fold", "peeling_or_lifting", "fading", "water_damage",
    "graffiti", "obscured_by_sticker", "broken_glazing",
    "broken_or_missing_frame_part", "detached_or_sagging_mount",
    "poster_slipped_or_empty",
]

# Model-emitted (see prompt.py's "## Condition" section) — a promotion from
# the mmvision.py prototype, which only derived this from `defects`. See
# docs/plans/2026-09-01-lobby-check-design.md §3.4 for why.
MATERIAL_CONDITIONS: list[str] = ["good", "damaged"]

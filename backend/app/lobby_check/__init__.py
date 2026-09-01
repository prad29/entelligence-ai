"""Cinema lobby marketing-material image extraction (Qwen 3-VL on Bedrock).

Productionizes the root-level mmvision.py prototype as /api/v1/lobby-check.
See docs/plans/2026-09-01-lobby-check-design.md for the full design.
"""

from app.lobby_check.extractor import extract_material_record

__all__ = ["extract_material_record"]

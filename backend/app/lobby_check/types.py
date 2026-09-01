"""Value objects returned by extractor.extract_material_record."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    """One image's extraction outcome. `record` holds the validated fields
    named in prompt.FIELD_ORDER; empty on a failed extraction (the caller
    should check `error` first)."""

    record: dict = field(default_factory=dict)
    framing: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    parse_retries: int = 0
    condition_conflict: bool = False
    error: str = ""

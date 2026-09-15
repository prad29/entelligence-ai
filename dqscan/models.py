"""Shared data model for the dqscan rule pack, compiler, engine and report.

This module is the contract every other dqscan module imports against. It
carries no SQL and no I/O, so it can move into the eventual Lambda unchanged.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Rule:
    """One entry from rules.yaml, class-agnostic fields plus every
    class-specific field left blank when not applicable."""

    id: str
    name: str
    rule_class: str  # YAML key is `class`; renamed since `class` is reserved
    severity: str
    status: str  # active | calibration | dormant
    requires: list[str]
    detail_columns: list[str]
    message: str
    blocked_by: Any = None
    note: str = ""

    # presence / format / cross_column
    expr: str = ""

    # domain
    column: str = ""
    allowed: list[str] = field(default_factory=list)

    # cross_row
    group_by: list[str] = field(default_factory=list)
    having: str = ""
    filter: str = ""

    # custom
    sql: str = ""


@dataclass
class CompiledRule:
    rule: Rule
    count_sql: str
    count_params: dict[str, Any]
    detail_sql: Optional[str] = None
    detail_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleOutcome:
    """The per-rule row that ends up in the run summary / report."""

    rule: Rule
    run_status: str  # active | calibration | dormant | error
    count: Optional[int] = None
    percentage: Optional[float] = None
    detail_rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    error: Optional[str] = None
    dormant_reason: str = ""
    needs_calibration: bool = False


@dataclass
class ScanMeta:
    from_date: str
    to_date: str
    window_column: str
    rows_scanned: int
    distinct_showtimes: int
    distinct_crawl_days: int
    duration_seconds: float
    schema: str
    pack_version: str
    calibration_threshold: float
    detail_row_cap: int
    include_calibration: bool = True
    include_dormant: bool = False
    extent_note: str = ""


@dataclass
class RunResult:
    meta: ScanMeta
    outcomes: list[RuleOutcome]

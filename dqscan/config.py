"""Config loading for dqscan: YAML + ${VAR} env interpolation, no literal creds.

No SQL, no I/O beyond reading the config/.env files. Pairs with models.py as
the other module every other part of dqscan can import without pulling in a
DB driver.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace  # noqa: F401  (replace re-exported for callers)

import yaml
from dotenv import load_dotenv

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

WINDOW_COLUMN_CHOICES = ("date_sh", "running_date")

_SCAN_DEFAULTS = {
    "window_column": "date_sh",
    "detail_row_cap": 500,
    "calibration_threshold": 0.20,
    "include_calibration": True,
    "include_dormant": False,
    "query_timeout_seconds": 60,
}


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    schema: str


@dataclass(frozen=True, slots=True)
class ScanConfig:
    window_column: str
    detail_row_cap: int
    calibration_threshold: float
    include_calibration: bool
    include_dormant: bool
    query_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class Config:
    database: DatabaseConfig
    scan: ScanConfig


def _interpolate(value):
    if isinstance(value, str):
        def _sub(match: re.Match) -> str:
            var_name = match.group(1)
            if var_name not in os.environ:
                raise ValueError(f"Missing required environment variable: {var_name}")
            return os.environ[var_name]

        return _VAR_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def _build_database_config(raw: dict) -> DatabaseConfig:
    for field_name in ("host", "port", "user", "password", "schema"):
        if field_name not in raw:
            raise ValueError(f"Missing required database config field: {field_name}")
    return DatabaseConfig(
        host=raw["host"],
        port=int(raw["port"]),
        user=raw["user"],
        password=raw["password"],
        schema=raw["schema"],
    )


def _build_scan_config(raw: dict) -> ScanConfig:
    merged = {**_SCAN_DEFAULTS, **raw}
    window_column = merged["window_column"]
    if window_column not in WINDOW_COLUMN_CHOICES:
        raise ValueError(
            f"scan.window_column must be one of {WINDOW_COLUMN_CHOICES}, got: {window_column!r}"
        )
    return ScanConfig(
        window_column=window_column,
        detail_row_cap=int(merged["detail_row_cap"]),
        calibration_threshold=float(merged["calibration_threshold"]),
        include_calibration=bool(merged["include_calibration"]),
        include_dormant=bool(merged["include_dormant"]),
        query_timeout_seconds=int(merged["query_timeout_seconds"]),
    )


def load_config(path: str = "config.yaml") -> Config:
    # Do not override real environment (e.g. CI/deploy) with .env placeholders.
    load_dotenv()

    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    raw = _interpolate(raw)

    database = _build_database_config(raw.get("database") or {})
    scan = _build_scan_config(raw.get("scan") or {})
    return Config(database=database, scan=scan)

"""Shared string literals for the usage-observability tables and API.

Kept as plain module constants (not enums) to match how this codebase already
models discriminator columns — e.g. DeletedShowtimeJob.theater_verify and
MovieMasterSyncJob.market are documented-string columns, not DB enums.
"""

from __future__ import annotations

# --- task_type (spec §5) -----------------------------------------------------
# The spec enumerates three; movie_format_detection is the fourth real Bedrock
# call site (movie-format classification, spec §2/§3) and needs its own bucket
# so its cost is attributable separately from amenity detection.
TASK_DOMESTIC_MAPPING = "domestic_mapping"
TASK_INTL_MAPPING = "intl_mapping"
TASK_AMENITY_DETECTION = "amenity_detection"
TASK_MOVIE_FORMAT_DETECTION = "movie_format_detection"

TASK_TYPES: tuple[str, ...] = (
    TASK_DOMESTIC_MAPPING,
    TASK_INTL_MAPPING,
    TASK_AMENITY_DETECTION,
    TASK_MOVIE_FORMAT_DETECTION,
)

# --- call_path (spec §5) -----------------------------------------------------
PATH_BEDROCK_DIRECT = "bedrock_direct"
PATH_AGENTIC_CLI = "agentic_cli"

CALL_PATHS: tuple[str, ...] = (PATH_BEDROCK_DIRECT, PATH_AGENTIC_CLI)

# --- caller_type (spec §3 — no portal auth; portal is a single bucket) -------
CALLER_PORTAL = "portal"
CALLER_EXTERNAL_API = "external_api"

CALLER_TYPES: tuple[str, ...] = (CALLER_PORTAL, CALLER_EXTERNAL_API)

# --- status (spec §5) --------------------------------------------------------
STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"
STATUS_TIMEOUT = "timeout"

# --- rollup (spec §8) -------------------------------------------------------
ROLLUP_WATERMARK_NAME = "hourly"

# Sentinel used for the rollup's nullable-in-raw dimensions. Postgres treats
# NULLs as distinct inside a unique index, so an ON CONFLICT upsert can never
# match a row whose api_key_id/market is NULL — the rollup therefore stores ''
# where the raw LlmCallLog stores NULL.
ROLLUP_NULL_SENTINEL = ""

# --- Serper call_type (spec §7) ---------------------------------------------
SERPER_CALL_SEARCH = "search"
SERPER_CALL_SCRAPE = "scrape"

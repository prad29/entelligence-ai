from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint
from typing import Optional
from datetime import datetime
import uuid


class MovieFormatMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    keyword: str = Field(index=True)
    format: str                         # "70MM" | "35MM" | "3D" | "2D"
    priority_tier: int                  # 1=70MM, 2=35MM, 3=3D, 4=2D
    status: str = Field(default="approved")
    notes: Optional[str] = None
    created_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)


class MovieFormatReviewItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str
    payload: Optional[str] = None
    source_string: Optional[str] = None
    suggested_format: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    status: str = Field(default="pending")
    reviewer: Optional[str] = None
    decided_at: Optional[datetime] = None
    mapping_id: Optional[int] = None


class MovieFormatJob(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    status: str = Field(default="queued")
    total: int = Field(default=0)
    processed: int = Field(default=0)
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    include_diagnostics: bool = Field(default=False)
    audit_mode: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None
    stats: Optional[str] = None


class MovieTitleBatchJob(SQLModel, table=True):
    """Batch job for the Mode B agentic title matching pipeline."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    status: str = Field(default="queued")  # queued|processing|completed|failed
    total: int = Field(default=0)
    processed: int = Field(default=0)
    matched: int = Field(default=0)
    no_match: int = Field(default=0)
    failed: int = Field(default=0)
    error: Optional[str] = None  # top-level job failure message (not per-row)
    use_poster_vision: bool = Field(default=False)
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None
    stats: Optional[str] = None  # JSON string


class AmenityMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    amenity_keyword: str = Field(index=True)
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str] = Field(default=None, index=True)
    na_default: Optional[str] = None
    status: str = Field(default="pending")  # draft|pending|approved|rejected
    notes: Optional[str] = None
    created_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)



class CircuitAlias(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    raw_or_alias: str = Field(index=True, unique=True)
    canonical: str


class DetectionJob(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    status: str = Field(default="queued")
    total: int = Field(default=0)
    processed: int = Field(default=0)
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    include_diagnostics: bool = Field(default=False)
    audit_mode: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None
    stats: Optional[str] = None


class IntlAmenityMapping(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    amenity_keyword: str = Field(index=True)
    screen_format: str
    priority_tier: int
    circuit_name: Optional[str] = Field(default=None, index=True)  # dormant — intl has no circuit data yet
    na_default: Optional[str] = None  # dormant — carried through for future-proofing, not part of the write contract
    status: str = Field(default="pending")  # draft|pending|approved|rejected
    notes: Optional[str] = None
    created_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    version: int = Field(default=1)


class IntlDetectionJob(SQLModel, table=True):
    """Batch job for the International Amenity Detection feature.

    One-table-per-feature convention, matching DetectionJob above (see also
    MovieTitleIntlBatchJob at models.py:153-159) — kept as its own table
    rather than a shared table with a market discriminator.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    status: str = Field(default="queued")
    total: int = Field(default=0)
    processed: int = Field(default=0)
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    include_diagnostics: bool = Field(default=False)
    audit_mode: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None
    stats: Optional[str] = None


class ReviewItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str
    payload: Optional[str] = None
    source_string: Optional[str] = None
    circuit: Optional[str] = None
    suggested_format: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    status: str = Field(default="pending")
    reviewer: Optional[str] = None
    decided_at: Optional[datetime] = None
    mapping_id: Optional[int] = None


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    table_name: str
    record_id: str
    action: str
    before_json: Optional[str] = None
    after_json: Optional[str] = None
    actor: Optional[str] = None
    ts: datetime = Field(default_factory=datetime.utcnow)


class MovieMaster(SQLModel, table=True):
    id: int = Field(primary_key=True)
    movie_title: str = Field(index=True)
    release_date: Optional[str] = None
    imdb_id: Optional[str] = None
    cover_image: Optional[str] = None
    director: Optional[str] = None
    cast_list: Optional[str] = None
    running_time: Optional[int] = None
    parent_id: Optional[int] = None
    search_tags: Optional[str] = None
    title_tag: Optional[str] = None
    short_name: Optional[str] = None
    cover_image_phash: Optional[str] = None


class MovieTitleAlias(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    normalized_alias: str = Field(index=True)
    country_code: Optional[str] = None
    movie_master_id: int = Field(foreign_key="moviemaster.id")
    source: str = Field(default="human")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class MovieTitleIntlBatchJob(SQLModel, table=True):
    """Batch job for the Mode B agentic international title matching pipeline.

    Kept as a separate table from MovieTitleBatchJob (not a shared table with
    a type discriminator) to match this codebase's one-table-per-feature
    convention (MovieFormatJob, DetectionJob, MovieTitleBatchJob).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    status: str = Field(default="queued")  # queued|processing|completed|failed
    total: int = Field(default=0)
    processed: int = Field(default=0)
    matched: int = Field(default=0)
    no_match: int = Field(default=0)
    failed: int = Field(default=0)
    error: Optional[str] = None  # top-level job failure message (not per-row)
    use_poster_vision: bool = Field(default=False)
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None
    stats: Optional[str] = None  # JSON string


class MovieMasterSyncJob(SQLModel, table=True):
    """Background job for syncing MovieMaster/MovieMasterIntl from the
    production MySQL DB (fq_movie_master / fq_movie_master_intl).

    Uses a single shared table with a `market` discriminator column,
    deliberately DIFFERENT from the one-table-per-market convention used for
    MovieTitleBatchJob/MovieTitleIntlBatchJob above. Those are split because
    each market's *processing logic* differs materially (country param,
    different prompt); this sync job's lifecycle/fields are identical for
    both markets and only the source query differs, so a discriminator
    column avoids two near-duplicate tables for no benefit.

    This sync is upsert-only in both directions (Postgres and Vespa) — it
    never deletes MovieMaster/MovieMasterIntl rows that were removed
    upstream in the production MySQL tables, nor removes stale Vespa
    documents. A "sync" in the everyday sense implies mirroring deletions
    too; this feature deliberately does not do that in v1.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    market: str  # "domestic" | "international"
    status: str = Field(default="queued")  # queued|processing|completed|failed
    total: int = Field(default=0)
    processed: int = Field(default=0)
    inserted: int = Field(default=0)
    updated: int = Field(default=0)
    skipped: int = Field(default=0)
    skipped_undefined_country: int = Field(default=0)  # international only; always 0 for domestic
    error: Optional[str] = None  # top-level job failure message, scrubbed of connection details
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None


class MovieMasterIntl(SQLModel, table=True):
    """International Movie Master, grain (movie_id, country, release_date)."""

    __table_args__ = (
        UniqueConstraint("movie_id", "country", "release_date", name="uq_intl_movie_country_date"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    source_row_id: Optional[int] = Field(default=None, index=True)
    movie_id: int = Field(index=True)  # soft reference, not a FK to moviemaster.id
    movie_title: str = Field(index=True)
    master_movie_title: Optional[str] = None
    country: str = Field(index=True)
    country_id: Optional[int] = None
    release_date: Optional[str] = None
    studio: Optional[str] = None
    rating: Optional[str] = None
    genre: Optional[str] = None
    genre2: Optional[str] = None
    running_time: Optional[int] = None
    updated_on: Optional[str] = None


class DeletedShowtimeJob(SQLModel, table=True):
    """Batch job for the Deleted Showtimes Check feature (ported from the
    standalone showtime_serp_check.py script).

    One-table-per-feature convention, matching MovieTitleBatchJob /
    MovieTitleIntlBatchJob above. `total`/`processed` count ROWS (not
    theater x date batches) since that's the unit the UI progress bar
    tracks; `consecutive_failures` is the DB-durable counter the abort
    guardrail checks before each batch task proceeds, replacing the
    script's in-process counter (batches now run as independent Celery
    tasks with no shared memory).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    status: str = Field(default="queued")  # queued|processing|completed|failed
    total: int = Field(default=0)
    processed: int = Field(default=0)
    true_count: int = Field(default=0)
    false_count: int = Field(default=0)
    unknown_count: int = Field(default=0)
    consecutive_failures: int = Field(default=0)
    aborted: bool = Field(default=False)
    error: Optional[str] = None  # top-level job failure message (not per-row)
    # Advanced options (all default to showtime_serp_check.py's own defaults).
    title_missing_is_deleted: bool = Field(default=False)
    strict_screen_count: bool = Field(default=False)
    theater_verify: str = Field(default="warn")  # off|warn|strict
    fallback: str = Field(default="auto")  # off|auto|plain|movie
    workers: int = Field(default=4)
    file_path: Optional[str] = None
    output_path: Optional[str] = None
    audit_output_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ttl: Optional[datetime] = None
    original_filename: Optional[str] = None


class SerpApiKeySlot(SQLModel, table=True):
    """Rotation state for SerpApi keys used by the Deleted Showtimes Check
    feature. One row per configured key slot (see Settings.SERPAPI_API_KEYS).
    `key_fingerprint` detects when the physical key in a slot's env var has
    been swapped for a different key, so stale exhaustion state doesn't leak
    onto a replacement key. Absence of a row (or a fingerprint mismatch) means
    "available" — rows are written only on exhaustion, never lazily seeded,
    to avoid a race between concurrent Celery workers inserting the same PK."""

    slot: int = Field(primary_key=True)
    key_fingerprint: str
    exhausted_at: Optional[datetime] = None
    last_error: Optional[str] = None
    failure_count: int = Field(default=0)


class ApiKey(SQLModel, table=True):
    """External API tenancy record. Keys are stored hashed (SHA-256) — the
    plaintext key is shown to the client once at creation and never
    persisted. `key_prefix` (first 8 chars of the plaintext) exists only so
    an admin listing can identify a key without ever storing/logging the
    full secret.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    key_hash: str = Field(index=True, unique=True)
    key_prefix: str
    label: Optional[str] = None
    active: bool = Field(default=True)
    max_rows_per_batch: Optional[int] = None  # falls back to settings.MAX_BATCH_ROWS if null
    max_concurrent_jobs: int = Field(default=5)
    requests_per_minute: int = Field(default=60)
    db_update_allowed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    rotated_at: Optional[datetime] = None


class ApiTitleMatchJob(SQLModel, table=True):
    """External-API job for the singletitle/batchtitle endpoints.

    A separate job model and Celery task module from MovieTitleBatchJob /
    MovieTitleIntlBatchJob (see external_match_task.py) — this surface needs
    durable, individually addressable rows for partial retrieval and
    row-scoped retry across a job that can run for over an hour, which the
    existing xlsx + ephemeral-Redis-hash pipeline was never built for. Both
    paths call the same run_agentic_match core, so matching logic itself
    never forks.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    api_key_id: str = Field(foreign_key="apikey.id", index=True)
    market: str  # "domestic" | "international"
    db_update: bool = Field(default=False)
    phase: str = Field(default="queued")  # queued|syncing|processing|completed|completed_with_errors|failed
    rows_total: int = Field(default=0)
    rows_processed: int = Field(default=0)
    rows_matched: int = Field(default=0)
    rows_no_match: int = Field(default=0)
    rows_failed: int = Field(default=0)
    error: Optional[str] = None  # top-level job failure message (not per-row)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    ttl: Optional[datetime] = None


class ApiTitleMatchRow(SQLModel, table=True):
    """One row of an ApiTitleMatchJob. row_uuid is client-supplied and is the
    sole join key between input and output — never parsed or interpreted,
    only echoed back. Unique per job (not globally) via the composite
    constraint below.
    """

    __table_args__ = (
        UniqueConstraint("job_id", "row_uuid", name="uq_api_row_job_uuid"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(foreign_key="apititlematchjob.id", index=True)
    row_uuid: str = Field(index=True)
    input_json: str  # the submitted row as received, including metadata
    status: str = Field(default="pending")  # pending|processing|completed|failed
    mapped_title: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    present_in_db: bool = Field(default=False)
    attempts: int = Field(default=0)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

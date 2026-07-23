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

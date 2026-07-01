from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
import uuid


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


class CircuitOverride(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    keyword: str = Field(index=True)
    circuit_name: str = Field(index=True)
    screen_format: str
    na_default: Optional[str] = None
    status: str = Field(default="approved")


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

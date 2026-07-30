"""
Pure, side-effect-free request/response schemas for the external
singletitle/batchtitle API (app/routers/external_title_match.py).

No DB, network, or Celery imports at module load time — same convention as
batch_io.py, so these are unit-testable in isolation.

Internal field names (`confidence`, `reasoning`) are kept as-is on
ApiTitleMatchRow; renaming to the external contract's `confidence_score` /
`ai_reasoning` happens only here, at the response boundary.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, model_validator

# "title" is NOT accepted as an alias here — unlike batch_io.py's internal
# Excel flow, the external contract's field names are strict.


class ExternalRowInput(BaseModel):
    row_uuid: str
    movie_title: str
    show_date: str  # YYYY-MM-DD
    ticketing_url: str
    country: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_fields(self) -> "ExternalRowInput":
        try:
            uuid.UUID(self.row_uuid)
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"row_uuid is not a valid UUID: {self.row_uuid!r}")

        try:
            datetime.strptime(self.show_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Expected YYYY-MM-DD, got {self.show_date!r}")

        parsed = urlparse(self.ticketing_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"ticketing_url is not well-formed: {self.ticketing_url!r}")

        return self


class ExternalBatchRequest(BaseModel):
    rows: list[ExternalRowInput]

    @model_validator(mode="after")
    def _validate_rows(self) -> "ExternalBatchRequest":
        if not self.rows:
            raise ValueError("rows must be non-empty")

        seen: set[str] = set()
        for row in self.rows:
            if row.row_uuid in seen:
                raise ValueError(f"Duplicate row_uuid within submission: {row.row_uuid!r}")
            seen.add(row.row_uuid)

        return self


class RowError(BaseModel):
    row_uuid: str
    field: str
    message: str


def validate_rows_for_market(rows: list[ExternalRowInput], market: str) -> list[RowError]:
    """
    Cross-field validation that depends on the `type` query param, which
    ExternalRowInput's own field validators never see. Currently just the
    country-required-iff-international rule.
    """
    errors: list[RowError] = []
    for row in rows:
        has_country = bool((row.country or "").strip())
        if market == "international" and not has_country:
            errors.append(
                RowError(row_uuid=row.row_uuid, field="country", message="Required when type=international")
            )
        elif market == "domestic" and has_country:
            errors.append(
                RowError(row_uuid=row.row_uuid, field="country", message="Not allowed when type=domestic")
            )
    return errors


def serialize_row_result(row: Any) -> dict:
    """row is an ApiTitleMatchRow. Renames internal fields to the external
    contract's naming (confidence -> confidence_score, reasoning -> ai_reasoning)
    and returns present_in_db as a native bool.
    """
    return {
        "row_uuid": row.row_uuid,
        "input": json.loads(row.input_json),
        "mapped_title": row.mapped_title or "",
        "confidence_score": row.confidence or 0,
        "ai_reasoning": row.reasoning or "",
        "present_in_db": bool(row.present_in_db),
    }


def serialize_job_status(job: Any) -> dict:
    """job is an ApiTitleMatchJob."""
    payload = {
        "job_id": job.id,
        "status": job.phase,
        "rows_total": job.rows_total,
        "rows_processed": job.rows_processed,
        "rows_matched": job.rows_matched,
        "rows_no_match": job.rows_no_match,
        "rows_failed": job.rows_failed,
    }
    if job.started_at:
        payload["started_at"] = job.started_at.isoformat() + "Z"
    if job.completed_at:
        payload["completed_at"] = job.completed_at.isoformat() + "Z"
    if job.error:
        payload["error"] = job.error
    return payload

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

from pydantic import BaseModel, Field, model_validator

# "title" is NOT accepted as an alias here — unlike batch_io.py's internal
# Excel flow, the external contract's field names are strict.


class ExternalRowInput(BaseModel):
    row_uuid: str = Field(
        description="Client-supplied UUID. Sole join key between input and output; "
        "must be unique within a submission. Opaque to the API — never parsed or interpreted.",
        examples=["3f7a1c92-5d84-4b21-9e6f-1a2b3c4d5e6f"],
    )
    movie_title: str = Field(
        description="Raw title string as it appears at source (local-language or exhibitor-supplied). "
        "Not normalized, case-folded, or repaired — tolerance for degraded input is a property "
        "of the matching layer, not this contract.",
        examples=["Verflucht normal"],
    )
    show_date: str = Field(
        description="Showing date, YYYY-MM-DD. Boosts candidate scoring by proximity to a "
        "candidate's release date and is passed to the matching agent as corroborating evidence.",
        examples=["2026-07-28"],
    )
    ticketing_url: str = Field(
        description="Ticketing/showtime page for this showing. Often the strongest available "
        "signal when movie_title is a placeholder (e.g. 'ESTRENO').",
        examples=["https://www.cinemaxx.de/buchtickets/zusammenfassung/1203/HO00001310/3700"],
    )
    country: Optional[str] = Field(
        default=None,
        description="Full English country name as held in the international Movie Master table. "
        "Required when type=international, rejected when type=domestic.",
        examples=["Germany"],
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Arbitrary key-value passthrough, returned untouched on the result row. "
        "Where theater_name and any other client-side columns belong.",
        examples=[{"theater_name": "HOLI Hamburg"}],
    )

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
    rows: list[ExternalRowInput] = Field(
        description="Rows to match, in submission order. Must be non-empty, capped at the "
        "calling key's row limit (defaults to MAX_BATCH_ROWS), and every row_uuid must be "
        "unique within this list.",
    )

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


class ValidationFailedResponse(BaseModel):
    """422 body shape when any row fails validation. The whole submission is
    rejected — a client fixes every row_error in a single pass rather than
    resubmitting repeatedly."""

    error: str = Field(default="validation_failed", examples=["validation_failed"])
    row_errors: list[RowError]


class SubmitJobResponse(BaseModel):
    job_id: str = Field(description="UUID identifying this job. Use with the /external/jobs/* endpoints.")
    status: str = Field(examples=["queued"])
    rows_total: int
    submitted_at: str = Field(description="ISO-8601 UTC timestamp.", examples=["2026-07-31T09:14:22Z"])


class JobStatusResponse(BaseModel):
    job_id: str
    status: str = Field(
        description="queued | syncing | processing | completed | completed_with_errors | failed",
        examples=["processing"],
    )
    rows_total: int
    rows_processed: int
    rows_matched: int
    rows_no_match: int
    rows_failed: int
    started_at: Optional[str] = Field(default=None, description="ISO-8601 UTC timestamp.")
    completed_at: Optional[str] = Field(default=None, description="ISO-8601 UTC timestamp.")
    error: Optional[str] = Field(default=None, description="Set only when status=failed.")


class RowResultResponse(BaseModel):
    row_uuid: str = Field(description="Echoed verbatim from the input — the join key.")
    input: dict[str, Any] = Field(description="The submitted row as received, including metadata.")
    mapped_title: str = Field(description="Resolved Movie Master title. Empty string if no confident match.")
    confidence_score: float = Field(description="0 for failed or unmatched rows.")
    ai_reasoning: str = Field(description="Agent's explanation, or 'error: ...' for a failed row.")
    present_in_db: bool = Field(
        description="Whether the resolved movie id exists in MovieMaster/MovieMasterIntl."
    )


class JobResultsResponse(JobStatusResponse):
    results: list[RowResultResponse] = Field(
        description="Completed rows only (status completed or failed), sorted by submission order. "
        "Returned whether or not the job has finished — supports partial retrieval mid-run."
    )
    next_cursor: Optional[str] = Field(
        default=None,
        description="Opaque cursor for the next page of results, or null if this is the last page.",
    )


class RetryRequestBody(BaseModel):
    row_uuids: list[str] = Field(
        description="row_uuid values to retry. Must belong to this job and currently be failed.",
        examples=[["3f7a1c92-5d84-4b21-9e6f-1a2b3c4d5e6f"]],
    )


class RetryResponse(BaseModel):
    job_id: str
    queued: list[str] = Field(description="row_uuids that were actually re-queued for matching.")
    skipped: list[str] = Field(
        description="row_uuids not retried — already at the attempt cap, or not currently failed."
    )


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

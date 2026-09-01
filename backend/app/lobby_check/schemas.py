"""
Pure, side-effect-free request/response schemas for the /lobby-check API
(app/routers/lobby_check.py).

No DB, network, or Celery imports at module load time — same convention as
title_matching/external_schemas.py, so these are unit-testable in isolation.
(Reading app.config.settings for the SSRF host allow-list is the one
exception, same as several routers already do — it is plain config, not a
DB/network/Celery call.)

Internal field names on LobbyCheckRow (`visual_notes`, `defects_json`) are
renamed to the external contract's naming (`ai_reasoning`, `defects`) only
here, at the response boundary. Diagnostic fields (tokens, cost, latency,
framing, model_id, parse_retries) are never serialized into any response —
per user decision, they are observability-dashboard-only (see
docs/plans/2026-09-01-lobby-check-design.md §6.6).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from app.config import settings


def _allowed_hosts() -> set[str]:
    return {
        h.strip().lower()
        for h in (settings.LOBBY_CHECK_ALLOWED_URL_HOSTS or "").split(",")
        if h.strip()
    }


class LobbyCheckImageInput(BaseModel):
    row_uuid: str = Field(
        description="Client-supplied UUID. Sole join key between input and output; "
        "must be unique within a submission. Opaque to the API — never parsed or interpreted.",
        examples=["3f7a1c92-5d84-4b21-9e6f-1a2b3c4d5e6f"],
    )
    image_url: str = Field(
        description="https URL of the lobby photo, on an allow-listed host.",
        examples=["https://mm-intelligence.s3.amazonaws.com/August2026FullLobbyAug20212026/"
                  "intelligence_photos/1787248984204.jpg"],
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Arbitrary key-value passthrough, returned untouched on the result row.",
        examples=[{"theater_name": "Regal Union Square"}],
    )

    @model_validator(mode="after")
    def _validate_fields(self) -> "LobbyCheckImageInput":
        try:
            uuid.UUID(self.row_uuid)
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"row_uuid is not a valid UUID: {self.row_uuid!r}")

        parsed = urlparse(self.image_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"image_url must be an https URL: {self.image_url!r}")

        allowed = _allowed_hosts()
        host = parsed.netloc.lower()
        if allowed and host not in allowed:
            raise ValueError(
                f"image_url host {host!r} is not allow-listed (allowed: {sorted(allowed)})"
            )

        return self


class LobbyCheckRequest(BaseModel):
    images: list[LobbyCheckImageInput] = Field(
        description="Images to process, in submission order. Must be non-empty, capped at the "
        "calling key's row limit (defaults to LOBBY_CHECK_MAX_BATCH_ROWS), and every row_uuid "
        "must be unique within this list.",
    )

    @model_validator(mode="after")
    def _validate_images(self) -> "LobbyCheckRequest":
        if not self.images:
            raise ValueError("images must be non-empty")

        seen: set[str] = set()
        for image in self.images:
            if image.row_uuid in seen:
                raise ValueError(f"Duplicate row_uuid within submission: {image.row_uuid!r}")
            seen.add(image.row_uuid)

        return self


class LobbyCheckRowError(BaseModel):
    row_uuid: str
    field: str
    message: str


class ValidationFailedResponse(BaseModel):
    """422 body shape when the batch cap is exceeded. Per-image field errors
    (bad row_uuid/image_url, duplicate row_uuid) are raised by pydantic
    itself before this ever runs, and surface as FastAPI's normal 422 body."""

    error: str = Field(default="validation_failed", examples=["validation_failed"])
    row_errors: list[LobbyCheckRowError]


def validate_batch_size(
    images: list[LobbyCheckImageInput], max_rows: int
) -> list[LobbyCheckRowError]:
    """The one cross-image check that depends on the caller's own batch cap
    (ApiKey.max_rows_per_batch or settings.LOBBY_CHECK_MAX_BATCH_ROWS),
    which LobbyCheckImageInput's per-image validators never see."""
    if len(images) > max_rows:
        return [
            LobbyCheckRowError(
                row_uuid="",
                field="images",
                message=f"Submission has {len(images)} images, exceeding this key's "
                        f"limit of {max_rows}",
            )
        ]
    return []


class SubmitLobbyCheckResponse(BaseModel):
    job_id: str = Field(description="UUID identifying this job. Use with the /lobby-check/jobs/* endpoints.")
    status: str = Field(examples=["queued"])
    rows_total: int
    poll_url: str = Field(description="GET this to poll job status.")


class LobbyCheckJobStatusResponse(BaseModel):
    job_id: str
    status: str = Field(
        description="queued | processing | completed | completed_with_errors | failed",
        examples=["processing"],
    )
    rows_total: int
    rows_processed: int
    rows_succeeded: int
    rows_failed: int
    rows_needs_review: int
    progress_pct: float = Field(description="100 * rows_processed / rows_total, 0 if rows_total is 0.")
    started_at: Optional[str] = Field(default=None, description="ISO-8601 UTC timestamp.")
    completed_at: Optional[str] = Field(default=None, description="ISO-8601 UTC timestamp.")
    error: Optional[str] = Field(default=None, description="Set only when status=failed.")
    results_url: str = Field(description="GET this for per-image results.")


class LobbyCheckResult(BaseModel):
    row_uuid: str = Field(description="Echoed verbatim from the input — the join key.")
    status: str = Field(description="pending | dispatched | completed | failed")
    input: dict[str, Any] = Field(description="The submitted row as received, including metadata.")

    movie_title: Optional[str] = Field(default=None, description="Empty string if illegible.")
    confidence_movie_title: Optional[float] = None

    material_type: Optional[str] = None
    confidence_material_type: Optional[float] = None

    material_quantity: Optional[int] = None
    confidence_material_quantity: Optional[float] = None

    material_condition: Optional[str] = Field(default=None, description="good | damaged")
    confidence_material_condition: Optional[float] = None

    ai_reasoning: Optional[str] = Field(default=None, description="Model's visual_notes.")
    defects: list[str] = Field(default_factory=list)
    defect_evidence: Optional[str] = None
    condition_conflict: bool = Field(
        default=False,
        description="True if the model's material_condition disagreed with its own "
        "defects list even after the repair retry — the persisted condition is the "
        "defects-derived value, and its confidence has been capped accordingly.",
    )

    needs_review: bool = Field(
        default=False,
        description="True if any of the four confidences fell below the review threshold.",
    )
    error: Optional[str] = Field(default=None, description="Set only when status=failed.")


class LobbyCheckResultsResponse(BaseModel):
    job_id: str
    status: str
    results: list[LobbyCheckResult] = Field(
        description="Rows in any status, sorted by submission order. Returned whether or "
        "not the job has finished — supports partial retrieval mid-run."
    )
    next_cursor: Optional[str] = Field(
        default=None,
        description="Opaque cursor for the next page of results, or null if this is the last page.",
    )
    has_more: bool = False


def serialize_row_result(row: Any, *, review_threshold: float) -> dict:
    """row is a LobbyCheckRow. Renames internal fields to the external
    contract's naming (visual_notes -> ai_reasoning, defects_json ->
    defects) and computes needs_review from the four confidences. Never
    returns tokens/cost/latency/framing/model_id/parse_retries — those are
    dashboard-only (design doc §6.6).
    """
    confidences = [
        row.confidence_movie_title,
        row.confidence_material_type,
        row.confidence_material_quantity,
        row.confidence_material_condition,
    ]
    present = [c for c in confidences if c is not None]
    needs_review = bool(present) and min(present) < review_threshold

    return {
        "row_uuid": row.row_uuid,
        "status": row.status,
        "input": json.loads(row.input_json),
        "movie_title": row.movie_title,
        "confidence_movie_title": row.confidence_movie_title,
        "material_type": row.material_type,
        "confidence_material_type": row.confidence_material_type,
        "material_quantity": row.material_quantity,
        "confidence_material_quantity": row.confidence_material_quantity,
        "material_condition": row.material_condition,
        "confidence_material_condition": row.confidence_material_condition,
        "ai_reasoning": row.visual_notes,
        "defects": json.loads(row.defects_json) if row.defects_json else [],
        "defect_evidence": row.defect_evidence,
        "condition_conflict": bool(row.condition_conflict),
        "needs_review": needs_review,
        "error": row.error,
    }


def serialize_job_status(job: Any) -> dict:
    """job is a LobbyCheckJob."""
    progress_pct = (
        round(100.0 * job.rows_processed / job.rows_total, 1) if job.rows_total else 0.0
    )
    payload = {
        "job_id": job.id,
        "status": job.phase,
        "rows_total": job.rows_total,
        "rows_processed": job.rows_processed,
        "rows_succeeded": job.rows_succeeded,
        "rows_failed": job.rows_failed,
        "rows_needs_review": job.rows_needs_review,
        "progress_pct": progress_pct,
    }
    if job.started_at:
        payload["started_at"] = job.started_at.isoformat() + "Z"
    if job.completed_at:
        payload["completed_at"] = job.completed_at.isoformat() + "Z"
    if job.error:
        payload["error"] = job.error
    return payload

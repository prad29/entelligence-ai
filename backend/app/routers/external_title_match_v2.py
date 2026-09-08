"""v2 submit surface for the external singletitle/batchtitle contract.

URL-versioned (/api/v2/singletitle, /api/v2/batchtitle), NOT a header or
query flag on the existing v1 routes: an external caller's pipeline choice is
part of the endpoint they integrated against, so it should be visible in
access logs, Swagger and their own client code rather than hidden in a
header. /api/v1's route bodies are untouched by this module.

This router is deliberately thin. It owns exactly one decision -- stamping
pipeline_variant="v2" on the job -- and reuses v1's request schemas, row
validation, job creation and dispatch verbatim via
external_title_match._submit_job. Everything after submission is shared too:
the SAME job status/results/retry endpoints under /api/v1/external/jobs serve
v1 and v2 jobs alike (job lookup is keyed by job_id + api_key_id, never by
variant, and there is no v1-vs-v2 difference in those semantics), and the
same Celery task module processes the rows.

The pipeline split happens exactly once, as late as possible, inside
app.tasks.external_match_task.external_match_row -- which reads
job.pipeline_variant together with job.market and calls one of three fully
isolated runners (v1's run_agentic_match, domestic v2's
run_agentic_match_v2, or international v2's run_agentic_match_intl_v2). No
matching-decision code is shared between the two markets' v2 pipelines; see
runner_intl_v2.py's module docstring for why that isolation is load-bearing.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.database import get_session
from app.dependencies.api_auth import require_db_update_permission
from app.models import ApiKey
from app.routers.external_title_match import _SUBMIT_RESPONSES, _submit_job
from app.title_matching.external_schemas import (
    ExternalBatchRequest,
    ExternalRowInput,
    SubmitJobResponse,
)

router = APIRouter(prefix="/api/v2", tags=["external-title-match-v2"])

# The v2 pipeline this row is routed to, resolved per-market in
# external_match_row. Stored on ApiTitleMatchJob.pipeline_variant; NULL (what
# /api/v1 writes) means v1.
_PIPELINE_VARIANT = "v2"


@router.post(
    "/singletitle",
    status_code=202,
    response_model=SubmitJobResponse,
    summary="Submit one title for asynchronous matching through the v2 pipeline",
    description=(
        "Identical request/response contract, timing and 202-plus-polling flow as "
        "POST /api/v1/singletitle — the only difference is which matching pipeline runs the "
        "row. type=domestic routes through domestic v2, which additionally weighs "
        "genre/cast/director/synopsis and deterministically rejects a pick whose director AND "
        "synopsis are both empty. type=international routes through the standalone "
        "international v2 pipeline: country-scoped candidate search, a country-consistency "
        "guardrail, anniversary/re-release date arithmetic, and an independent verification "
        "pass over the model's first pick. Poll the SAME "
        "GET /api/v1/external/jobs/{job_id} endpoints for status and results — there are no "
        "/api/v2 job endpoints."
    ),
    responses=_SUBMIT_RESPONSES,
)
async def submit_single_title_v2(
    payload: ExternalRowInput,
    type: Literal["domestic", "international"] = Query(
        ..., description="Selects MovieMaster (domestic) or MovieMasterIntl (international)."
    ),
    db_update: bool = Query(
        False,
        description="If true, refreshes the local Movie Master corpus from production before "
        "matching begins. Requires db_update_allowed on the calling API key.",
    ),
    api_key: ApiKey = Depends(require_db_update_permission),
    session: Session = Depends(get_session),
):
    return _submit_job(
        [payload], type, db_update, api_key, session, pipeline_variant=_PIPELINE_VARIANT
    )


@router.post(
    "/batchtitle",
    status_code=202,
    response_model=SubmitJobResponse,
    summary="Submit a batch of titles for asynchronous matching through the v2 pipeline",
    description=(
        "Identical request/response contract, row limits and partial-results behavior as "
        "POST /api/v1/batchtitle, with every row dispatched through the market-appropriate v2 "
        "pipeline (domestic v2 for type=domestic, the standalone international v2 pipeline for "
        "type=international) instead of v1. Jobs from both surfaces live in the same table and "
        "are served by the same GET /api/v1/external/jobs/{job_id}/results and "
        "POST /api/v1/external/jobs/{job_id}/retry endpoints — a retried row re-runs on the "
        "same pipeline its job was submitted with."
    ),
    responses=_SUBMIT_RESPONSES,
)
async def submit_batch_title_v2(
    payload: ExternalBatchRequest,
    type: Literal["domestic", "international"] = Query(
        ..., description="Selects MovieMaster (domestic) or MovieMasterIntl (international)."
    ),
    db_update: bool = Query(
        False,
        description="If true, refreshes the local Movie Master corpus from production before "
        "matching begins. Requires db_update_allowed on the calling API key.",
    ),
    api_key: ApiKey = Depends(require_db_update_permission),
    session: Session = Depends(get_session),
):
    return _submit_job(
        payload.rows, type, db_update, api_key, session, pipeline_variant=_PIPELINE_VARIANT
    )

"""
Usage / observability read API (design §9).

Six read-only endpoints over the tables the earlier observability work
fills in:

    GET /api/v1/usage/summary          — totals + derived rates for a range
    GET /api/v1/usage/timeseries       — hour/day buckets for the line chart
    GET /api/v1/usage/breakdown        — grouped totals for bar/pie charts
    GET /api/v1/usage/dedupe           — dedupe rate + estimated $ saved
    GET /api/v1/usage/serpapi-credits  — latest snapshot per key slot + history
    GET /api/v1/usage/serper-usage     — self-tracked Serper quota (§7)

All of the arithmetic lives in app.observability.queries; this module is
purely the HTTP surface — parse, validate, delegate. That split is what lets
the report endpoint reuse the exact same filter object without duplicating a
line of aggregation logic.

No auth, matching every other internal router here (jobs.py,
deleted_showtimes.py): this is an internal ops tool, not a public API
surface.

settings.USAGE_TRACKING_ENABLED is deliberately *not* checked here — it
gates *writing* new rows, not reading the ones already collected. Flipping
the kill switch must not blank out the dashboard's history.

Timestamps are naive UTC throughout, matching the datetime.utcnow() default
on every model in app.models. A tz-aware query param is converted to UTC and
flattened; a naive one is taken as UTC as-is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.observability import queries, report as report_mod
from app.observability.constants import CALLER_TYPES, TASK_TYPES
from app.observability.queries import UsageFilters
from app.observability.serper_quota import serper_quota_status

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])

# What the dashboard opens on when no range is supplied.
DEFAULT_RANGE_DAYS = 7

# A bare date is a *whole day*, not midnight: start=D&end=D has to mean all
# of D, otherwise the single-day case returns an empty range.
_DATE_ONLY_LENGTH = 10


def _parse_dt(raw: str, *, field: str, end_of_day: bool) -> datetime:
    """Parse a query-param timestamp into naive UTC.

    Accepts YYYY-MM-DD (widened to the whole day when it's the upper bound)
    or any ISO-8601 datetime, with or without an offset.
    """
    text = raw.strip()
    try:
        if len(text) == _DATE_ONLY_LENGTH:
            day = datetime.strptime(text, "%Y-%m-%d")
            return day + timedelta(days=1) if end_of_day else day
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be YYYY-MM-DD or an ISO-8601 datetime, got {raw!r}",
        ) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _validate_choice(value: Optional[str], allowed, *, field: str) -> Optional[str]:
    if not value:
        return None
    if value not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be one of {', '.join(allowed)}; got {value!r}",
        )
    return value


def usage_filters(
    start: Optional[str] = Query(None, description="Range start, YYYY-MM-DD or ISO-8601. Defaults to 7 days ago."),
    end: Optional[str] = Query(None, description="Range end (exclusive), YYYY-MM-DD or ISO-8601. Defaults to now."),
    task_type: Optional[str] = Query(None, description=f"One of: {', '.join(TASK_TYPES)}"),
    model_id: Optional[str] = Query(None, description="Exact Bedrock model id"),
    caller_type: Optional[str] = Query(None, description=f"One of: {', '.join(CALLER_TYPES)}"),
    api_key_id: Optional[str] = Query(None, description="External-API caller attribution"),
    market: Optional[str] = Query(None),
) -> UsageFilters:
    """The one place range/filter parsing happens — shared by every endpoint
    in this module and by the report endpoint, so a downloaded CSV/PDF can
    never disagree with the dashboard it was generated from."""
    now = datetime.utcnow()
    parsed_end = _parse_dt(end, field="end", end_of_day=True) if end else now
    parsed_start = (
        _parse_dt(start, field="start", end_of_day=False)
        if start
        else parsed_end - timedelta(days=DEFAULT_RANGE_DAYS)
    )

    if parsed_start >= parsed_end:
        raise HTTPException(
            status_code=400,
            detail=f"start must be strictly before end (got {parsed_start} .. {parsed_end})",
        )

    max_days = settings.USAGE_REPORT_MAX_DAYS
    span_days = (parsed_end - parsed_start).total_seconds() / 86400.0
    if span_days > max_days:
        raise HTTPException(
            status_code=400,
            detail=(
                f"range spans {span_days:.1f} days; at most {max_days} days may be "
                "queried at once (USAGE_REPORT_MAX_DAYS)"
            ),
        )

    return UsageFilters(
        start=parsed_start,
        end=parsed_end,
        task_type=_validate_choice(task_type, TASK_TYPES, field="task_type"),
        model_id=model_id or None,
        caller_type=_validate_choice(caller_type, CALLER_TYPES, field="caller_type"),
        api_key_id=api_key_id or None,
        market=market or None,
    )


@router.get("/summary")
def get_summary(
    filters: UsageFilters = Depends(usage_filters),
    session: Session = Depends(get_session),
) -> dict:
    """Aggregated totals for the range: requests, tokens, cost, plus derived
    rates. Reads LlmUsageRollupHourly and merges the not-yet-rolled raw tail."""
    return queries.summary(session, filters)


@router.get("/timeseries")
def get_timeseries(
    granularity: str = Query("hour", description=f"One of: {', '.join(queries.GRANULARITIES)}"),
    filters: UsageFilters = Depends(usage_filters),
    session: Session = Depends(get_session),
) -> dict:
    """Time-bucketed series for the dashboard's line chart."""
    _validate_choice(granularity, queries.GRANULARITIES, field="granularity")
    return queries.timeseries(session, filters, granularity)


@router.get("/breakdown")
def get_breakdown(
    dimension: str = Query("task_type", description=f"One of: {', '.join(queries.BREAKDOWN_DIMENSIONS)}"),
    filters: UsageFilters = Depends(usage_filters),
    session: Session = Depends(get_session),
) -> dict:
    """Grouped totals for bar/pie charts, ordered by cost descending."""
    _validate_choice(dimension, queries.BREAKDOWN_DIMENSIONS, field="dimension")
    return queries.breakdown(session, filters, dimension)


@router.get("/dedupe")
def get_dedupe(
    filters: UsageFilters = Depends(usage_filters),
    session: Session = Depends(get_session),
) -> dict:
    """Dedupe rate and estimated dollars saved by the existing dedup caches,
    per task_type and overall."""
    return queries.dedupe_stats(session, filters)


@router.get("/serpapi-credits")
def get_serpapi_credits(
    history_hours: int = Query(24, ge=1, le=24 * 90, description="History window, in hours"),
    session: Session = Depends(get_session),
) -> dict:
    """Latest SerpApiCreditSnapshot per key slot plus a history series.

    Deliberately takes no UsageFilters: credits are a property of the key
    pool right now, not of an LLM-call date range.
    """
    return queries.serpapi_credits(session, history_hours=history_hours)


@router.get("/serper-usage")
def get_serper_usage(session: Session = Depends(get_session)) -> dict:
    """Self-tracked Serper balance: SERPER_QUOTA_TOTAL minus SerperCallLog
    rows since SERPER_QUOTA_PERIOD_START (spec §7 — Serper has no
    remaining-credits API)."""
    return serper_quota_status(session)


@router.get("/report")
def get_report(
    format: Literal["csv", "pdf"] = Query("csv", description="csv or pdf"),
    filters: UsageFilters = Depends(usage_filters),
    session: Session = Depends(get_session),
) -> Response:
    """Download a full usage/cost report for the same range/filters the
    dashboard endpoints above use — collect_report() calls the exact same
    queries.* functions, so the numbers here can never drift from the
    screen this was generated from."""
    data = report_mod.collect_report(session, filters)
    stem = f"usage-report-{filters.start.date().isoformat()}-to-{filters.end.date().isoformat()}"

    if format == "csv":
        return Response(
            content=report_mod.build_csv(data),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'},
        )
    return Response(
        content=report_mod.build_pdf(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
    )

"""Aggregation queries for the usage API (spec §9).

Every query merges two sources, split by the rollup watermark rather than a
time boundary:
  - `LlmUsageRollupHourly` for everything already folded in (id <= watermark).
  - The raw `LlmCallLog` "tail" — rows newer than the watermark — for
    whatever hasn't been rolled up yet (spec §3: hourly freshness is fine,
    but the tail keeps the dashboard from lagging a full hour behind).

Splitting by id (not by an hour boundary) is what keeps this correct: the
rollup never contains a row above the watermark and the tail query never
re-reads a row at or below it, so nothing is double-counted and nothing is
missed, regardless of how the batch happened to fall across hour buckets.

The requested `start` is floored to the top of its hour before either query
runs, since the rollup's grain is an hour — a mid-hour start would otherwise
silently exclude the partial hour it falls in. This is a dashboard
convenience, not a billing ledger, so the small edge rounding is accepted
rather than built out into its own alignment-tracking response field.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.observability.constants import ROLLUP_WATERMARK_NAME, STATUS_SUCCESS

GRANULARITIES = ("hour", "day")
BREAKDOWN_DIMENSIONS = ("task_type", "model_id", "caller_type", "api_key_id", "market")

_SUM_FIELDS = (
    "request_count",
    "cache_hit_count",
    "failure_count",
    "retry_count_sum",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "latency_ms_sum",
)


@dataclass(frozen=True)
class UsageFilters:
    start: datetime
    end: datetime
    task_type: Optional[str] = None
    model_id: Optional[str] = None
    caller_type: Optional[str] = None
    api_key_id: Optional[str] = None
    market: Optional[str] = None


def _zero_sums() -> dict:
    return {f: 0 for f in _SUM_FIELDS}


def _add_sums(dst: dict, src) -> None:
    for f in _SUM_FIELDS:
        dst[f] += getattr(src, f)


def _add_row_sums(dst: dict, row) -> None:
    """Fold one raw LlmCallLog row into a running sums dict, using the same
    field names as the rollup so both sources merge without translation."""
    dst["request_count"] += 1
    dst["cache_hit_count"] += 1 if row.cache_hit else 0
    dst["failure_count"] += 0 if row.status == STATUS_SUCCESS else 1
    dst["retry_count_sum"] += row.retry_count
    dst["input_tokens"] += row.input_tokens
    dst["output_tokens"] += row.output_tokens
    dst["cache_read_tokens"] += row.cache_read_tokens
    dst["cache_write_tokens"] += row.cache_write_tokens
    dst["cost_usd"] += row.cost_usd
    dst["latency_ms_sum"] += row.latency_ms


def _derived(sums: dict) -> dict:
    n = sums["request_count"]
    if n == 0:
        return {"avg_latency_ms": None, "failure_rate": None, "cache_hit_rate": None, "cost_per_request": None}
    return {
        "avg_latency_ms": sums["latency_ms_sum"] / n,
        "failure_rate": sums["failure_count"] / n,
        "cache_hit_rate": sums["cache_hit_count"] / n,
        "cost_per_request": sums["cost_usd"] / n,
    }


def _watermark_id(session: Session) -> int:
    from app.models import LlmUsageRollupWatermark

    watermark = session.get(LlmUsageRollupWatermark, ROLLUP_WATERMARK_NAME)
    return watermark.last_rolled_id if watermark is not None else 0


def _rollup_query(session: Session, filters: UsageFilters, aligned_start: datetime):
    from app.models import LlmUsageRollupHourly

    stmt = select(LlmUsageRollupHourly).where(
        LlmUsageRollupHourly.bucket_hour >= aligned_start,
        LlmUsageRollupHourly.bucket_hour < filters.end,
    )
    if filters.task_type:
        stmt = stmt.where(LlmUsageRollupHourly.task_type == filters.task_type)
    if filters.model_id:
        stmt = stmt.where(LlmUsageRollupHourly.model_id == filters.model_id)
    if filters.caller_type:
        stmt = stmt.where(LlmUsageRollupHourly.caller_type == filters.caller_type)
    if filters.api_key_id:
        stmt = stmt.where(LlmUsageRollupHourly.api_key_id == filters.api_key_id)
    if filters.market:
        stmt = stmt.where(LlmUsageRollupHourly.market == filters.market)
    return session.exec(stmt).all()


def _raw_tail_query(session: Session, filters: UsageFilters, aligned_start: datetime, watermark_id: int):
    from app.models import LlmCallLog

    stmt = select(LlmCallLog).where(
        LlmCallLog.id > watermark_id,
        LlmCallLog.ts >= aligned_start,
        LlmCallLog.ts < filters.end,
    )
    if filters.task_type:
        stmt = stmt.where(LlmCallLog.task_type == filters.task_type)
    if filters.model_id:
        stmt = stmt.where(LlmCallLog.model_id == filters.model_id)
    if filters.caller_type:
        stmt = stmt.where(LlmCallLog.caller_type == filters.caller_type)
    if filters.api_key_id:
        stmt = stmt.where(LlmCallLog.api_key_id == filters.api_key_id)
    if filters.market:
        stmt = stmt.where(LlmCallLog.market == filters.market)
    return session.exec(stmt).all()


def summary(session: Session, filters: UsageFilters) -> dict:
    aligned_start = filters.start.replace(minute=0, second=0, microsecond=0)
    watermark_id = _watermark_id(session)

    sums = _zero_sums()
    for row in _rollup_query(session, filters, aligned_start):
        _add_sums(sums, row)
    for row in _raw_tail_query(session, filters, aligned_start, watermark_id):
        _add_row_sums(sums, row)

    return {
        "range": {"start": filters.start.isoformat(), "end": filters.end.isoformat()},
        "totals": sums,
        "derived": _derived(sums),
    }


def _bucket_key(ts: datetime, granularity: str) -> datetime:
    if granularity == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(minute=0, second=0, microsecond=0)


def timeseries(session: Session, filters: UsageFilters, granularity: str) -> dict:
    aligned_start = filters.start.replace(minute=0, second=0, microsecond=0)
    watermark_id = _watermark_id(session)

    buckets: dict[datetime, dict] = {}
    for row in _rollup_query(session, filters, aligned_start):
        key = _bucket_key(row.bucket_hour, granularity)
        buckets.setdefault(key, _zero_sums())
        _add_sums(buckets[key], row)
    for row in _raw_tail_query(session, filters, aligned_start, watermark_id):
        key = _bucket_key(row.ts, granularity)
        buckets.setdefault(key, _zero_sums())
        _add_row_sums(buckets[key], row)

    points = [
        {"bucket": bucket.isoformat(), **sums}
        for bucket, sums in sorted(buckets.items())
    ]
    return {
        "range": {"start": filters.start.isoformat(), "end": filters.end.isoformat()},
        "granularity": granularity,
        "points": points,
    }


def breakdown(session: Session, filters: UsageFilters, dimension: str) -> dict:
    aligned_start = filters.start.replace(minute=0, second=0, microsecond=0)
    watermark_id = _watermark_id(session)

    groups: dict[str, dict] = {}
    for row in _rollup_query(session, filters, aligned_start):
        key = getattr(row, dimension) or ""
        groups.setdefault(key, _zero_sums())
        _add_sums(groups[key], row)
    for row in _raw_tail_query(session, filters, aligned_start, watermark_id):
        key = getattr(row, dimension) or ""
        groups.setdefault(key, _zero_sums())
        _add_row_sums(groups[key], row)

    rows = sorted(
        ({dimension: key, **sums} for key, sums in groups.items()),
        key=lambda r: r["cost_usd"],
        reverse=True,
    )
    return {
        "range": {"start": filters.start.isoformat(), "end": filters.end.isoformat()},
        "dimension": dimension,
        "rows": rows,
    }


def dedupe_stats(session: Session, filters: UsageFilters) -> dict:
    """Dedupe rate and estimated $ saved, per task_type and overall.

    Savings are estimated per task_type rather than with one blended
    average: an agentic mapping call and a Bedrock amenity classification
    differ by orders of magnitude in cost, so a single average would
    misstate the saving for both.
    """
    aligned_start = filters.start.replace(minute=0, second=0, microsecond=0)
    watermark_id = _watermark_id(session)

    by_task: dict[str, dict] = {}
    for row in _rollup_query(session, filters, aligned_start):
        g = by_task.setdefault(row.task_type, _zero_sums())
        _add_sums(g, row)
    for row in _raw_tail_query(session, filters, aligned_start, watermark_id):
        g = by_task.setdefault(row.task_type, _zero_sums())
        _add_row_sums(g, row)

    by_task_type = []
    total_attempted = 0
    total_cache_hits = 0
    total_savings = 0.0
    for task_type, sums in by_task.items():
        attempted = sums["request_count"]
        cache_hits = sums["cache_hit_count"]
        billable = attempted - cache_hits
        avg_real_cost = (sums["cost_usd"] / billable) if billable > 0 else 0.0
        savings = cache_hits * avg_real_cost
        by_task_type.append({
            "task_type": task_type,
            "attempted": attempted,
            "cache_hits": cache_hits,
            "dedupe_rate": (cache_hits / attempted) if attempted else None,
            "estimated_savings_usd": savings,
        })
        total_attempted += attempted
        total_cache_hits += cache_hits
        total_savings += savings

    return {
        "range": {"start": filters.start.isoformat(), "end": filters.end.isoformat()},
        "by_task_type": by_task_type,
        "overall": {
            "attempted": total_attempted,
            "cache_hits": total_cache_hits,
            "dedupe_rate": (total_cache_hits / total_attempted) if total_attempted else None,
            "estimated_savings_usd": total_savings,
        },
    }


def serpapi_credits(session: Session, history_hours: int) -> dict:
    """Latest SerpApiCreditSnapshot per key slot, plus a history series over
    the last `history_hours`. Not filtered by UsageFilters — credits are a
    property of the key pool right now, not of an LLM-call date range, and
    the snapshot stream is never pruned (spec §6)."""
    from app.models import SerpApiCreditSnapshot

    since = datetime.utcnow() - timedelta(hours=history_hours)
    history = session.exec(
        select(SerpApiCreditSnapshot)
        .where(SerpApiCreditSnapshot.ts >= since)
        .order_by(SerpApiCreditSnapshot.ts)
    ).all()

    latest_by_slot: dict[int, SerpApiCreditSnapshot] = {}
    for row in history:
        latest_by_slot[row.slot] = row  # history is ts-ascending, so last wins

    slots = [
        {
            "slot": row.slot,
            "plan_searches_left": row.plan_searches_left,
            "extra_credits": row.extra_credits,
            "total_searches_left": row.total_searches_left,
            "this_month_usage": row.this_month_usage,
            "account_email": row.account_email,
            "error": row.error,
            "as_of": row.ts.isoformat(),
        }
        for row in sorted(latest_by_slot.values(), key=lambda r: r.slot)
    ]
    known_totals = [s["total_searches_left"] for s in slots if s["total_searches_left"] is not None]

    return {
        "slots": slots,
        "total_searches_left": sum(known_totals) if known_totals else None,
        "history": [
            {"ts": row.ts.isoformat(), "slot": row.slot, "total_searches_left": row.total_searches_left}
            for row in history
        ],
        "history_hours": history_hours,
    }

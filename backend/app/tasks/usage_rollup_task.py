"""Hourly usage rollup and daily raw-log prune (spec §8).

rollup_llm_usage_hourly() folds new LlmCallLog rows into LlmUsageRollupHourly,
grouped by (hour, task_type, model_id, caller_type, api_key_id, market), and
advances LlmUsageRollupWatermark to the highest LlmCallLog.id it processed.
The watermark — not a time boundary — is what makes this idempotent and safe
to re-run: a batch always starts at `id > last_rolled_id`, and the read,
every upsert, and the watermark advance all happen in one DB transaction, so
a crash mid-batch leaves the watermark exactly where it was and the next run
reprocesses the same rows rather than silently skipping or double-counting
them.

prune_llm_call_logs() deletes raw LlmCallLog rows older than
settings.USAGE_RAW_RETENTION_DAYS, bounded additionally by the watermark
(id <= last_rolled_id) so a row that hasn't been rolled up yet is never
deleted out from under the rollup.

Both tasks are total: any failure is logged and swallowed, never raised, so a
bad batch doesn't crash the beat schedule (design §8).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.dialects import postgresql, sqlite
from sqlmodel import Session, select

from app.celery_app import celery
from app.config import settings
from app.observability.constants import ROLLUP_WATERMARK_NAME, STATUS_SUCCESS

logger = logging.getLogger(__name__)

_ROLLUP_DIMS = ("bucket_hour", "task_type", "model_id", "caller_type", "api_key_id", "market")

# Additive sums this rollup accumulates per dimension group. Kept as a tuple
# (not hardcoded per-field) so the ON CONFLICT DO UPDATE clause and the
# per-row group accumulation can't drift apart.
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


def _insert_stmt(session: Session, model):
    """Dialect-appropriate INSERT builder — same rationale as
    serp_key_rotation.py's helper of the same name: production runs Postgres,
    the test/dev path uses SQLite, and both dialects share the same
    `excluded`-alias ON CONFLICT API."""
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return postgresql.insert(model)
    return sqlite.insert(model)


def rollup_llm_usage_hourly() -> int:
    """Fold new LlmCallLog rows into LlmUsageRollupHourly. Returns the number
    of raw rows processed (0 on a clean no-op or any failure). Never raises.
    """
    if not settings.USAGE_TRACKING_ENABLED:
        return 0

    try:
        from app.database import engine
        from app.models import LlmCallLog, LlmUsageRollupHourly, LlmUsageRollupWatermark

        with Session(engine) as session:
            watermark = session.get(LlmUsageRollupWatermark, ROLLUP_WATERMARK_NAME)
            if watermark is None:
                watermark = LlmUsageRollupWatermark(name=ROLLUP_WATERMARK_NAME)
                session.add(watermark)
                session.flush()

            rows = session.exec(
                select(LlmCallLog)
                .where(LlmCallLog.id > watermark.last_rolled_id)
                .order_by(LlmCallLog.id)
                .limit(settings.USAGE_ROLLUP_BATCH_SIZE)
            ).all()

            if not rows:
                return 0

            groups: dict[tuple, dict[str, Any]] = {}
            for row in rows:
                bucket_hour = row.ts.replace(minute=0, second=0, microsecond=0)
                key = (
                    bucket_hour,
                    row.task_type,
                    row.model_id,
                    row.caller_type,
                    row.api_key_id or "",
                    row.market or "",
                )
                g = groups.setdefault(key, {f: 0 for f in _SUM_FIELDS})
                g["request_count"] += 1
                g["cache_hit_count"] += 1 if row.cache_hit else 0
                g["failure_count"] += 0 if row.status == STATUS_SUCCESS else 1
                g["retry_count_sum"] += row.retry_count
                g["input_tokens"] += row.input_tokens
                g["output_tokens"] += row.output_tokens
                g["cache_read_tokens"] += row.cache_read_tokens
                g["cache_write_tokens"] += row.cache_write_tokens
                g["cost_usd"] += row.cost_usd
                g["latency_ms_sum"] += row.latency_ms

            now = datetime.utcnow()
            for (bucket_hour, task_type, model_id, caller_type, api_key_id, market), sums in groups.items():
                stmt = _insert_stmt(session, LlmUsageRollupHourly).values(
                    bucket_hour=bucket_hour,
                    task_type=task_type,
                    model_id=model_id,
                    caller_type=caller_type,
                    api_key_id=api_key_id,
                    market=market,
                    updated_at=now,
                    **sums,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=list(_ROLLUP_DIMS),
                    set_={
                        field: getattr(LlmUsageRollupHourly, field) + getattr(stmt.excluded, field)
                        for field in _SUM_FIELDS
                    }
                    | {"updated_at": stmt.excluded.updated_at},
                )
                session.execute(stmt)

            watermark.last_rolled_id = max(row.id for row in rows)
            watermark.last_rolled_hour = max(key[0] for key in groups)
            watermark.updated_at = now
            session.commit()

            logger.info(
                "usage_rollup: folded %d row(s) into %d bucket(s), watermark -> %d",
                len(rows), len(groups), watermark.last_rolled_id,
            )
            return len(rows)
    except Exception as exc:  # noqa: BLE001 — design §8
        logger.warning("usage_rollup_failed error=%s", exc)
        return 0


def prune_llm_call_logs() -> int:
    """Delete raw LlmCallLog rows older than USAGE_RAW_RETENTION_DAYS that
    have already been rolled up. Returns the number of rows deleted (0 on a
    clean no-op or any failure). Never raises."""
    if not settings.USAGE_TRACKING_ENABLED:
        return 0

    try:
        from app.database import engine
        from app.models import LlmCallLog, LlmUsageRollupWatermark

        cutoff = datetime.utcnow() - timedelta(days=settings.USAGE_RAW_RETENTION_DAYS)

        with Session(engine) as session:
            watermark = session.get(LlmUsageRollupWatermark, ROLLUP_WATERMARK_NAME)
            # No watermark yet means nothing has ever been rolled up — pruning
            # now would permanently lose data the rollup hasn't seen.
            if watermark is None or watermark.last_rolled_id == 0:
                return 0

            rows = session.exec(
                select(LlmCallLog).where(
                    LlmCallLog.ts < cutoff,
                    LlmCallLog.id <= watermark.last_rolled_id,
                )
            ).all()
            if not rows:
                return 0

            for row in rows:
                session.delete(row)
            session.commit()

            logger.info("usage_prune: deleted %d row(s) older than %s", len(rows), cutoff)
            return len(rows)
    except Exception as exc:  # noqa: BLE001 — design §8
        logger.warning("usage_prune_failed error=%s", exc)
        return 0


@celery.task(name="app.tasks.usage_rollup_task.rollup_llm_usage_hourly")
def rollup_llm_usage_hourly_task() -> int:
    return rollup_llm_usage_hourly()


@celery.task(name="app.tasks.usage_rollup_task.prune_llm_call_logs")
def prune_llm_call_logs_task() -> int:
    return prune_llm_call_logs()

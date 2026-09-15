"""Seven-step scan orchestration: introspect, gate, compile, execute,
classify, report, summarise.

This module (and compiler.py, models.py, rules.yaml) is what moves into the
Lambda unchanged. Only __main__.py and report.py's destination are expected
to change there.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from dqscan import compiler, db, introspect, report
from dqscan.config import Config
from dqscan.models import CompiledRule, Rule, RuleOutcome, RunResult, ScanMeta

logger = logging.getLogger("dqscan.engine")

TABLE = "movies_shows"
RULES_PATH = Path(__file__).parent / "rules.yaml"

# presence/format/cross_column/domain all reduce to one boolean predicate
# over a single row, so they can share one SUM(CASE WHEN ...) pass over the
# window instead of each scanning the table separately. cross_row/custom
# need their own GROUP BY / window-function queries and are executed as
# before, one at a time.
_BATCHABLE_CLASSES = {"presence", "format", "cross_column", "domain"}
_BATCH_CHUNK_SIZE = 10

# MySQL error 3024: "Query execution was interrupted, maximum statement
# execution time exceeded" — the MAX_EXECUTION_TIME hint firing.
_MYSQL_TIMEOUT_ERRNO = 3024


def _is_timeout_error(exc: SQLAlchemyError) -> bool:
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    return bool(args) and args[0] == _MYSQL_TIMEOUT_ERRNO


def run(
    config: Config,
    *,
    from_date: str,
    to_date: str,
    out_path: str,
    rules_path: Path | str = RULES_PATH,
) -> RunResult:
    start = time.monotonic()
    query_timeout_seconds = config.scan.query_timeout_seconds
    # 0/None means "no timeout" end to end — don't let the socket-level
    # backstop impose one behind the user's back.
    timeout_ms = query_timeout_seconds * 1000 if query_timeout_seconds else None
    read_timeout_seconds = query_timeout_seconds + 15 if query_timeout_seconds else None
    engine = db.get_engine(config.database, read_timeout_seconds=read_timeout_seconds)
    window_column = config.scan.window_column
    window_params = {"from_date": from_date, "to_date": to_date}

    # Step 1: introspect
    existing_columns = introspect.get_existing_columns(
        engine, config.database.schema, TABLE, timeout_ms=timeout_ms
    )

    rows_scanned, distinct_showtimes, distinct_crawl_days, extent_note = _scan_extent(
        engine, window_column, window_params, timeout_ms
    )

    # Step 2: gate
    pack_meta, rules = compiler.load_rules(str(rules_path))
    compilable, outcomes = compiler.gate_rules(rules, existing_columns)

    # Step 3: compile. Every compilable rule gets its detail_sql ready now
    # (cheap, no I/O) so firing rules don't need a second round-trip later.
    # AssertionError (the read-only SQL shape check) is a safety-boundary
    # violation, not a rule-authoring mistake — it must abort the whole run,
    # so only the rule-authoring exceptions are caught here.
    compiled_by_id: dict[str, CompiledRule] = {}
    for rule in compilable:
        try:
            compiled_by_id[rule.id] = compiler.compile_rule(
                rule,
                window_column=window_column,
                table=TABLE,
                allowlist_regex=pack_meta["allowlist_regex"],
                ca_provinces=pack_meta["ca_provinces"],
                detail_row_cap=config.scan.detail_row_cap,
                existing_columns=existing_columns,
            )
        except (KeyError, ValueError, TypeError, NotImplementedError) as exc:
            logger.exception("Rule %s failed to compile", rule.id)
            outcomes.append(RuleOutcome(rule=rule, run_status="error", error=str(exc)))

    batchable = [r for r in compilable if r.rule_class in _BATCHABLE_CLASSES and r.id in compiled_by_id]
    individual = [r for r in compilable if r.rule_class not in _BATCHABLE_CLASSES and r.id in compiled_by_id]

    work_items: list[list[Rule]] = [
        batchable[i : i + _BATCH_CHUNK_SIZE] for i in range(0, len(batchable), _BATCH_CHUNK_SIZE)
    ]
    work_items += [[rule] for rule in individual]

    # Steps 4-5: execute + classify. One rule (or, for a batch, one whole
    # chunk) failing must not stop the run — and neither should the user
    # hitting Ctrl+C: whatever's already been collected still gets a report.
    processed_ids: set[str] = set()
    try:
        for item_rules in work_items:
            if len(item_rules) > 1:
                outcomes.extend(
                    _process_batch(
                        engine, item_rules, pack_meta, window_column, window_params,
                        compiled_by_id, rows_scanned, config, timeout_ms,
                    )
                )
            else:
                outcomes.append(
                    _process_individual(
                        engine, item_rules[0], compiled_by_id, window_params, rows_scanned, config, timeout_ms
                    )
                )
            processed_ids.update(r.id for r in item_rules)
    except KeyboardInterrupt:
        remaining = [r for r in batchable + individual if r.id not in processed_ids]
        logger.warning(
            "Run interrupted; writing a partial report for %d/%d compiled rules",
            len(processed_ids), len(compiled_by_id),
        )
        for rule in remaining:
            outcomes.append(RuleOutcome(rule=rule, run_status="dormant", dormant_reason="skipped: run interrupted"))

    duration = time.monotonic() - start
    meta = ScanMeta(
        from_date=from_date,
        to_date=to_date,
        window_column=window_column,
        rows_scanned=rows_scanned,
        distinct_showtimes=distinct_showtimes,
        distinct_crawl_days=distinct_crawl_days,
        duration_seconds=round(duration, 2),
        schema=config.database.schema,
        pack_version=str(pack_meta["version"]),
        calibration_threshold=config.scan.calibration_threshold,
        detail_row_cap=config.scan.detail_row_cap,
        include_calibration=config.scan.include_calibration,
        include_dormant=config.scan.include_dormant,
        extent_note=extent_note,
    )
    result = RunResult(meta=meta, outcomes=outcomes)

    # Step 6: report
    report.write_report(result, out_path)
    return result


def _scan_extent(engine, window_column: str, window_params: dict, timeout_ms: int) -> tuple[int, int, int, str]:
    sql = (
        f"SELECT COUNT(*) AS rows_scanned, "
        f"COUNT(DISTINCT mm_id) AS distinct_showtimes, "
        f"COUNT(DISTINCT running_date) AS distinct_crawl_days "
        f"FROM {TABLE} WHERE {window_column} >= :from_date AND {window_column} <= :to_date"
    )
    try:
        row = db.run_query(engine, sql, window_params, timeout_ms)[0]
    except SQLAlchemyError as exc:
        # Nothing downstream requires this — percentage/needs_calibration
        # just can't be computed, and every sheet already renders that as
        # blank. A raw set of counts beats no report at all.
        logger.warning("Scan-extent query failed (%s); rows-scanned/percentages will be unavailable", exc)
        return 0, 0, 0, "Rows scanned / distinct counts not computed: scan-extent query failed or timed out"
    return (
        row["rows_scanned"] or 0,
        row["distinct_showtimes"] or 0,
        row["distinct_crawl_days"] or 0,
        "",
    )


def _fetch_detail(engine, compiled: CompiledRule, window_params: dict, timeout_ms: int) -> list[dict]:
    return db.run_query(engine, compiled.detail_sql, {**compiled.detail_params, **window_params}, timeout_ms)


def _classify(rule: Rule, count: int, detail_rows: list[dict], rows_scanned: int, config: Config) -> RuleOutcome:
    percentage = (count / rows_scanned) if rows_scanned else None
    needs_calibration = (
        rule.status == "active"
        and percentage is not None
        and percentage > config.scan.calibration_threshold
    )
    run_status = "calibration" if (rule.status == "calibration" or needs_calibration) else "active"

    cap = config.scan.detail_row_cap
    truncated = len(detail_rows) > cap
    if truncated:
        detail_rows = detail_rows[:cap]

    return RuleOutcome(
        rule=rule,
        run_status=run_status,
        count=count,
        percentage=percentage,
        detail_rows=detail_rows,
        truncated=truncated,
        needs_calibration=needs_calibration,
    )


def _execute_individually(engine, rule, compiled, window_params, rows_scanned, config, timeout_ms) -> RuleOutcome:
    count = int(
        db.run_scalar(engine, compiled.count_sql, {**compiled.count_params, **window_params}, timeout_ms) or 0
    )
    detail_rows = _fetch_detail(engine, compiled, window_params, timeout_ms) if count > 0 else []
    return _classify(rule, count, detail_rows, rows_scanned, config)


def _process_individual(engine, rule, compiled_by_id, window_params, rows_scanned, config, timeout_ms) -> RuleOutcome:
    try:
        return _execute_individually(
            engine, rule, compiled_by_id[rule.id], window_params, rows_scanned, config, timeout_ms
        )
    except SQLAlchemyError as exc:
        logger.exception("Rule %s failed during execution", rule.id)
        return RuleOutcome(rule=rule, run_status="error", error=str(exc))


def _process_batch(
    engine, chunk, pack_meta, window_column, window_params, compiled_by_id, rows_scanned, config, timeout_ms
) -> list[RuleOutcome]:
    try:
        sql, batch_params, _rule_ids = compiler.compile_batch(
            chunk,
            window_column=window_column,
            table=TABLE,
            allowlist_regex=pack_meta["allowlist_regex"],
            ca_provinces=pack_meta["ca_provinces"],
        )
        row = db.run_query(engine, sql, {**batch_params, **window_params}, timeout_ms)[0]
    except SQLAlchemyError as exc:
        if _is_timeout_error(exc):
            # The batch didn't fail because of a broken rule — it ran out
            # of the same time budget any of its rules would individually.
            # Retrying each of the chunk's rules one at a time can only
            # spend up to chunk_size x timeout instead of 1x timeout for
            # the exact same "not enough time" reason — strictly worse, not
            # a fallback. Fail the whole chunk immediately instead.
            logger.warning("Batch of %d rules timed out; marking all as error (no point retrying individually)", len(chunk))
            return [RuleOutcome(rule=rule, run_status="error", error=str(exc)) for rule in chunk]

        # Any other failure (bad expr, transient connection issue, ...) is
        # a per-rule problem — isolate which rule is actually broken.
        logger.warning("Batch of %d rules failed (%s); falling back to individual execution", len(chunk), exc)
        return [
            _process_individual(engine, rule, compiled_by_id, window_params, rows_scanned, config, timeout_ms)
            for rule in chunk
        ]

    results = []
    for idx, rule in enumerate(chunk):
        count = int(row[f"c_{idx}"] or 0)
        compiled = compiled_by_id[rule.id]
        try:
            detail_rows = _fetch_detail(engine, compiled, window_params, timeout_ms) if count > 0 else []
        except SQLAlchemyError as exc:
            logger.exception("Rule %s's detail query failed after a successful batch count", rule.id)
            results.append(RuleOutcome(rule=rule, run_status="error", error=str(exc)))
            continue
        results.append(_classify(rule, count, detail_rows, rows_scanned, config))
    return results

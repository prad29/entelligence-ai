"""CLI entry point: python -m dqscan --from ... --to ... --out ...

The only file expected to be thrown away when this becomes a Lambda; every
import below it (config, db, introspect, compiler, engine, report) is meant
to survive that move untouched.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import logging
import sys

from tabulate import tabulate

from dqscan import engine
from dqscan.config import WINDOW_COLUMN_CHOICES, load_config

logger = logging.getLogger("dqscan")

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m dqscan")
    parser.add_argument("--from", dest="from_date", required=True, help="window start (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", required=True, help="window end (YYYY-MM-DD)")
    parser.add_argument("--out", dest="out_path", required=True, help="output .xlsx path")
    parser.add_argument("--config", dest="config_path", default="config.yaml", help="path to config.yaml")
    parser.add_argument(
        "--window-column",
        dest="window_column",
        choices=WINDOW_COLUMN_CHOICES,
        default=None,
        help="column to scan the date range against (default: from config, normally date_sh)",
    )
    parser.add_argument(
        "--query-timeout",
        dest="query_timeout_seconds",
        type=int,
        default=None,
        help="per-query timeout in seconds; a rule that exceeds it is marked 'error' and the run continues (default: from config)",
    )
    parser.add_argument("--debug", action="store_true", help="log every SQL statement at DEBUG level")
    return parser.parse_args(argv)


def _print_summary(run_result) -> None:
    def sort_key(outcome):
        return (
            _SEVERITY_ORDER.get(outcome.rule.severity, len(_SEVERITY_ORDER)),
            outcome.run_status,
            -(outcome.count or 0),
        )

    rows = []
    for outcome in sorted(run_result.outcomes, key=sort_key):
        percentage = f"{outcome.percentage * 100:.2f}%" if outcome.percentage is not None else "-"
        count = "ERROR" if outcome.run_status == "error" else (outcome.count if outcome.count is not None else "-")
        rows.append([outcome.rule.id, outcome.rule.severity, outcome.run_status, count, percentage])

    print(tabulate(rows, headers=["Rule ID", "Severity", "Status", "Count", "% of rows"]))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Parse (not string-compare) --from/--to: raw string comparison silently
    # misorders non-zero-padded dates (e.g. "2026-1-1" > "2026-01-02" is True
    # even though Jan 1 is before Jan 2), and a malformed date would otherwise
    # reach the DB as a bound param and fail there with a much less clear error.
    try:
        from_date = datetime.date.fromisoformat(args.from_date)
    except ValueError:
        print(f"error: --from ({args.from_date!r}) is not a valid YYYY-MM-DD date", file=sys.stderr)
        return 1
    try:
        to_date = datetime.date.fromisoformat(args.to_date)
    except ValueError:
        print(f"error: --to ({args.to_date!r}) is not a valid YYYY-MM-DD date", file=sys.stderr)
        return 1
    if from_date > to_date:
        print(f"error: --from ({args.from_date}) is after --to ({args.to_date})", file=sys.stderr)
        return 1
    # Normalize to zero-padded ISO strings so the SQL bound params are always
    # well-formed, regardless of how the caller spelled the input.
    args.from_date = from_date.isoformat()
    args.to_date = to_date.isoformat()

    config = load_config(args.config_path)
    scan_overrides = {}
    if args.window_column is not None:
        scan_overrides["window_column"] = args.window_column
    if args.query_timeout_seconds is not None:
        scan_overrides["query_timeout_seconds"] = args.query_timeout_seconds
    if scan_overrides:
        config = dataclasses.replace(config, scan=dataclasses.replace(config.scan, **scan_overrides))

    run_result = engine.run(config, from_date=args.from_date, to_date=args.to_date, out_path=args.out_path)
    _print_summary(run_result)
    print(f"\nWrote {args.out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

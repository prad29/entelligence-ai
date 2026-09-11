"""Daily cron entry point: scan from the end of the last successful run
through now, email the report, and advance the state file on success.

Intentionally separate from __main__.py's --from/--to CLI: that one is for
manual, retrospective analysis (explicit window, no state, no email); this
one is for the unattended incremental job (auto-computed window, state
tracking, always emails).

Usage (crontab, 6PM daily):
    0 18 * * * cd /path/to/repo && /path/to/venv/bin/python -m dqscan.run_daily --config config.prod.yaml >> /var/log/dqscan/daily.log 2>&1

On failure, state is deliberately NOT advanced and the exception propagates
(non-zero exit) -- the next run retries the same window plus whatever's
accumulated since, rather than silently skipping a day's data. Wire cron
failure alerting (e.g. a CloudWatch alarm on the log, or a non-zero-exit
notifier) separately -- this module has no failure-path emailer of its own.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dqscan import emailer, engine, state
from dqscan.config import load_config

logger = logging.getLogger("dqscan.run_daily")

REPORTS_DIR = Path(__file__).parent / "reports"
REPORT_RETENTION_DAYS = 30

_DATE_FORMAT = "%Y-%m-%d"
_DEFAULT_LOOKBACK_DAYS = 1  # first-ever run with no state file: scan just yesterday


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m dqscan.run_daily")
    parser.add_argument("--config", dest="config_path", default="config.yaml", help="path to config.yaml")
    parser.add_argument(
        "--state-path", dest="state_path", default=str(state.DEFAULT_STATE_PATH),
        help="path to the last-run state file",
    )
    parser.add_argument("--debug", action="store_true", help="log every SQL statement at DEBUG level")
    return parser.parse_args(argv)


def _prune_old_reports(reports_dir: Path, retention_days: int) -> None:
    if not reports_dir.exists():
        return
    cutoff = datetime.now().timestamp() - retention_days * 86400
    for f in reports_dir.glob("dqscan_*.xlsx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                logger.info("Pruned old report: %s", f)
        except OSError as exc:
            logger.warning("Could not prune report %s: %s", f, exc)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    now = datetime.now()
    last_end = state.read_last_run_end(args.state_path)
    from_date = (last_end or (now - timedelta(days=_DEFAULT_LOOKBACK_DAYS))).strftime(_DATE_FORMAT)
    to_date = now.strftime(_DATE_FORMAT)

    if from_date > to_date:
        logger.info("Nothing new since last run (from=%s, to=%s); skipping", from_date, to_date)
        return 0

    config = load_config(args.config_path)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"dqscan_{config.database.schema}_{from_date}_to_{to_date}.xlsx"

    logger.info("Starting daily scan: from=%s to=%s schema=%s", from_date, to_date, config.database.schema)
    run_result = engine.run(config, from_date=from_date, to_date=to_date, out_path=str(out_path))

    if config.email.enabled:
        emailer.send_report_email(
            run_result,
            str(out_path),
            sender=config.email.sender,
            recipients=config.email.recipients,
            aws_region=config.email.aws_region,
        )
    else:
        logger.info("email.enabled is false; report written to %s but not emailed", out_path)

    # Only advance state, and only prune, after a fully successful run
    # (scan + email) -- a failure anywhere above must leave the window
    # untouched so the next run retries it.
    state.write_last_run_end(now, args.state_path)
    _prune_old_reports(REPORTS_DIR, REPORT_RETENTION_DAYS)

    logger.info("Daily scan complete: rows_scanned=%d", run_result.meta.rows_scanned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

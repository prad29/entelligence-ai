"""Daily cron entry point: scan from the end of the last successful run
through now, email the report, and advance the state file on success.

Intentionally separate from __main__.py's --from/--to CLI: that one is for
manual, retrospective analysis (explicit window, no state, no email); this
one is for the unattended incremental job (auto-computed window, state
tracking, always emails).

Usage (crontab, 6PM daily):
    0 18 * * * cd /path/to/repo && /path/to/venv/bin/python -m dqscan.run_daily --env prod >> /var/log/dqscan/daily.log 2>&1

--env selects which config file to load (dev -> config.yaml, prod ->
config.prod.yaml) -- flip that one flag to move the whole job from dev to
prod, no code change. --config is still available for an explicit path
override (e.g. pointing at some other environment entirely) and wins if
given.

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

# The one flag that moves the whole job between environments -- everything
# else (DB host/schema, email sender/recipients) lives in the config file
# each name resolves to, not here, so switching envs never means touching code.
_CONFIG_BY_ENV = {
    "dev": "config.yaml",
    "prod": "config.prod.yaml",
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m dqscan.run_daily")
    parser.add_argument(
        "--env", dest="env", choices=sorted(_CONFIG_BY_ENV), default="dev",
        help=f"which environment's config to load ({_CONFIG_BY_ENV}) -- ignored if --config is also given",
    )
    parser.add_argument(
        "--config", dest="config_path", default=None,
        help="explicit path to a config file, overriding --env",
    )
    parser.add_argument(
        "--state-path", dest="state_path", default=None,
        help="path to the last-run state file (default: schema-qualified, "
        "e.g. dqscan/.state/last_run_<schema>.json -- so dev/prod configs "
        "never share, and silently corrupt, one another's incremental window)",
    )
    parser.add_argument(
        "--recipients", dest="recipients", default=None,
        help="comma-separated email addresses, overriding config.email.recipients for this run only "
        "(the config file itself is never touched)",
    )
    parser.add_argument(
        "--from", dest="from_date", default=None,
        help="override the auto-computed window start (YYYY-MM-DD); requires --to too. "
        "State still advances to now() on success, same as a normal incremental run.",
    )
    parser.add_argument(
        "--to", dest="to_date", default=None,
        help="override the auto-computed window end (YYYY-MM-DD); requires --from too.",
    )
    parser.add_argument("--debug", action="store_true", help="log every SQL statement at DEBUG level")
    args = parser.parse_args(argv)
    if bool(args.from_date) != bool(args.to_date):
        parser.error("--from and --to must be given together")
    return args


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

    config_path = args.config_path or _CONFIG_BY_ENV[args.env]
    config = load_config(config_path)
    state_path = args.state_path or (state.DEFAULT_STATE_PATH.parent / f"last_run_{config.database.schema}.json")

    now = datetime.now()
    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        last_end = state.read_last_run_end(state_path)
        from_date = (last_end or (now - timedelta(days=_DEFAULT_LOOKBACK_DAYS))).strftime(_DATE_FORMAT)
        to_date = now.strftime(_DATE_FORMAT)

    if from_date > to_date:
        logger.info("Nothing new since last run (from=%s, to=%s); skipping", from_date, to_date)
        return 0

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"dqscan_{config.database.schema}_{from_date}_to_{to_date}.xlsx"

    logger.info("Starting daily scan: from=%s to=%s schema=%s", from_date, to_date, config.database.schema)
    run_result = engine.run(config, from_date=from_date, to_date=to_date, out_path=str(out_path))

    if config.email.enabled:
        recipients = args.recipients.split(",") if args.recipients else config.email.recipients
        emailer.send_report_email(
            run_result,
            str(out_path),
            sender=config.email.sender,
            recipients=recipients,
            aws_region=config.email.aws_region,
        )
    else:
        logger.info("email.enabled is false; report written to %s but not emailed", out_path)

    # Only advance state, and only prune, after a fully successful run
    # (scan + email) -- a failure anywhere above must leave the window
    # untouched so the next run retries it.
    state.write_last_run_end(now, state_path)
    _prune_old_reports(REPORTS_DIR, REPORT_RETENTION_DAYS)

    logger.info("Daily scan complete: rows_scanned=%d", run_result.meta.rows_scanned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Daily cron entry point: scan from today through the latest showtime date
currently in movies_shows, and email the report.

Intentionally separate from __main__.py's --from/--to CLI: that one is for
manual, retrospective analysis (explicit window, no email); this one is for
the unattended job (auto-computed window, always emails).

Usage (crontab, 6PM daily):
    0 18 * * * cd /path/to/repo && /path/to/venv/bin/python -m dqscan.run_daily --env prod >> /var/log/dqscan/daily.log 2>&1

--env selects which config file to load (dev -> config.yaml, prod ->
config.prod.yaml) -- flip that one flag to move the whole job from dev to
prod, no code change. --config is still available for an explicit path
override (e.g. pointing at some other environment entirely) and wins if
given.

Changed 2026-09-24 (per request): the window used to be incremental and
state-tracked (from = end of the last successful run, or yesterday on the
very first run; to = today) via dqscan/state.py's last-run JSON file. That
module is left in place but is no longer read or written here -- every run
now scans from CURRENT_DATE() through whatever the furthest-out date_sh in
the table is, regardless of what a previous run already covered. This is a
deliberate tradeoff: simpler and always catches the full remaining future
catalog, at the cost of re-scanning showtimes a prior run already checked
on every firing (acceptable since each rule query is still scoped to this
window, not the whole table's history).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dqscan import db, emailer, engine, sns_notifier
from dqscan.config import load_config

logger = logging.getLogger("dqscan.run_daily")

REPORTS_DIR = Path(__file__).parent / "reports"
REPORT_RETENTION_DAYS = 30

_DATE_FORMAT = "%Y-%m-%d"

# The one flag that moves the whole job between environments -- everything
# else (DB host/schema, email sender/recipients) lives in the config file
# each name resolves to, not here, so switching envs never means touching code.
_CONFIG_BY_ENV = {
    "dev": "config.yaml",
    "prod": "config.prod.yaml",
}


def _latest_date_sh(config) -> Optional[str]:
    """The furthest-out showtime date currently in movies_shows, or None if
    the table is empty. A short-lived engine of its own -- separate from the
    one engine.run() creates internally for the actual scan -- since this
    has to run before the window it computes even exists."""
    eng = db.get_engine(config.database)
    try:
        latest = db.run_scalar(eng, f"SELECT MAX(date_sh) FROM {engine.TABLE}")
    finally:
        eng.dispose()
    return latest.strftime(_DATE_FORMAT) if latest else None


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
        "--recipients", dest="recipients", default=None,
        help="comma-separated email addresses, overriding config.email.recipients for this run only "
        "(the config file itself is never touched)",
    )
    parser.add_argument(
        "--from", dest="from_date", default=None,
        help="override the auto-computed window start (YYYY-MM-DD); requires --to too.",
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

    now = datetime.now()
    if args.from_date and args.to_date:
        from_date, to_date = args.from_date, args.to_date
    else:
        from_date = now.strftime(_DATE_FORMAT)
        to_date = _latest_date_sh(config) or from_date

    if from_date > to_date:
        logger.info("No showtimes at or after today (from=%s, to=%s); skipping", from_date, to_date)
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

    if config.sns.enabled:
        # No --recipients equivalent here by design: SNS subscribers self-
        # manage through the topic, not through this job's arguments.
        sns_notifier.publish_report_notification(
            run_result,
            str(out_path),
            topic_arn=config.sns.topic_arn,
            s3_bucket=config.sns.s3_bucket,
            s3_prefix=config.sns.s3_prefix,
            aws_region=config.sns.aws_region,
        )

    if not config.email.enabled and not config.sns.enabled:
        logger.info("email and sns are both disabled; report written to %s but not sent", out_path)

    # Only prune after a fully successful run (scan + notification) -- no
    # reason to touch old reports if this run itself failed partway through.
    _prune_old_reports(REPORTS_DIR, REPORT_RETENTION_DAYS)

    logger.info("Daily scan complete: rows_scanned=%d", run_result.meta.rows_scanned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

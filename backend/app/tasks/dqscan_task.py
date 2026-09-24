"""dqscan trigger + cron scheduler.

dqscan isn't an app/ package — it's a standalone tool that lives at the repo
root, shipped into this same image (see backend/Dockerfile.prod). Shelling
out to its existing `python -m dqscan.run_daily` CLI reuses its
orchestration (window computation, xlsx report, SES/SNS delivery) unchanged
rather than re-implementing it here.

check_dqscan_cron replaces what used to be a host crontab entry
(codedeploy/scripts/setup_dqscan.sh) — it runs every minute via Celery Beat
and fires run_dqscan_scan when DqscanSettings.cron_expression matches "now",
so changing the schedule/DB-target from the Settings page takes effect
within a minute, no redeploy or EC2 access needed.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from croniter import croniter

from app.celery_app import celery
from app.config import settings

logger = logging.getLogger(__name__)

QUEUE = "dqscan"


def _dqscan_root() -> Path:
    """Directory containing dqscan/ and config.yaml/config.prod.yaml.

    In the deployed image these are copied to /app alongside the backend app
    itself (see backend/Dockerfile.prod). In local dev without Docker, they're
    at the repo root, three levels up from this file (backend/app/tasks/).
    """
    app_root = Path("/app")
    if (app_root / "dqscan").is_dir():
        return app_root
    return Path(__file__).resolve().parents[3]


@celery.task(name="app.tasks.dqscan_task.run_dqscan_scan", queue=QUEUE)
def run_dqscan_scan(env: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> dict:
    root = _dqscan_root()
    cmd = [sys.executable, "-m", "dqscan.run_daily", "--env", env]
    if from_date and to_date:
        cmd += ["--from", from_date, "--to", to_date]

    logger.info("dqscan_trigger_start env=%s from=%s to=%s root=%s", env, from_date, to_date, root)

    # Deliberately NOT capture_output=True -- that buffers everything until
    # the subprocess exits, so dqscan's own progress logging (engine.py's
    # "Progress: X/Y work items done" lines, emitted as each rule/batch
    # finishes) would never reach `docker compose logs` while a scan is
    # actually running, and would be discarded entirely on success. Letting
    # stdout/stderr inherit this process's instead makes dqscan's logging
    # stream straight through to this worker's own log output in real time.
    result = subprocess.run(
        cmd,
        cwd=str(root),
        text=True,
        timeout=settings.DQSCAN_TIMEOUT_SECONDS,
    )

    if result.returncode != 0:
        # No captured output to include here -- it already streamed to this
        # worker's own logs above, which is where to look for what failed.
        logger.error("dqscan_trigger_failed env=%s returncode=%s", env, result.returncode)
        raise RuntimeError(f"dqscan exited {result.returncode}; see worker logs above for details")

    logger.info("dqscan_trigger_complete env=%s", env)
    return {"env": env, "from": from_date, "to": to_date}


@celery.task(name="app.tasks.dqscan_task.check_dqscan_cron")
def check_dqscan_cron() -> None:
    from sqlmodel import Session

    from app.database import engine
    from app.models import DqscanSettings

    now = datetime.utcnow().replace(second=0, microsecond=0)

    with Session(engine) as session:
        cfg = session.get(DqscanSettings, 1)
        if cfg is None:
            return

        try:
            is_due = croniter.match(cfg.cron_expression, now)
        except Exception:
            logger.warning("dqscan_cron_invalid_expression expr=%s", cfg.cron_expression)
            return

        if not is_due or cfg.last_cron_fired_at == now:
            return

        cfg.last_cron_fired_at = now
        session.add(cfg)
        session.commit()
        env = cfg.env

    # No --from/--to -- let run_daily.py auto-compute the window itself
    # (today through the latest date_sh in movies_shows, as of 2026-09-24).
    run_dqscan_scan.delay(env)

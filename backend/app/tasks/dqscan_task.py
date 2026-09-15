"""Manual dqscan trigger — runs dqscan/run_daily.py as a subprocess.

Unlike every other task in this file, dqscan isn't an app/ package — it's a
standalone tool that lives at the repo root and is normally run by a cron on
amenity-app (see codedeploy/scripts/setup_dqscan.sh). Shelling out to its
existing `python -m dqscan.run_daily` CLI reuses its orchestration (state
tracking, xlsx report, SES/SNS delivery) unchanged rather than re-implementing
it here.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

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
def run_dqscan_scan(env: str, from_date: str, to_date: str) -> dict:
    root = _dqscan_root()
    cmd = [
        sys.executable, "-m", "dqscan.run_daily",
        "--env", env,
        "--from", from_date,
        "--to", to_date,
    ]
    logger.info("dqscan_trigger_start env=%s from=%s to=%s root=%s", env, from_date, to_date, root)

    result = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=settings.DQSCAN_TIMEOUT_SECONDS,
    )

    if result.returncode != 0:
        output = (result.stdout + result.stderr)[-4000:]
        logger.error("dqscan_trigger_failed env=%s output=%s", env, output)
        raise RuntimeError(f"dqscan exited {result.returncode}: {output[-2000:]}")

    logger.info("dqscan_trigger_complete env=%s", env)
    return {"env": env, "from": from_date, "to": to_date}

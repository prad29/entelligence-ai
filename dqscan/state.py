"""Persists the end of the last successful scan window, so the daily cron
can resume from there instead of re-deriving "yesterday" each time.

A single JSON file, not a DB table -- only one process (the daily cron)
ever writes it, and losing it just means the next run falls back to a
sane default window instead of failing outright.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dqscan.state")

DEFAULT_STATE_PATH = Path(__file__).parent / ".state" / "last_run.json"
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def read_last_run_end(path: Path | str = DEFAULT_STATE_PATH) -> Optional[datetime]:
    """Returns the `to` timestamp of the last successful run, or None if no
    state file exists yet (first-ever run) or it can't be parsed."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return datetime.strptime(data["last_run_end"], _DATETIME_FORMAT)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("State file %s is unreadable (%s); treating as no prior run", path, exc)
        return None


def write_last_run_end(end: datetime, path: Path | str = DEFAULT_STATE_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_run_end": end.strftime(_DATETIME_FORMAT)}))

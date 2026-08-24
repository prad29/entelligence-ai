"""Hourly SerpApi credit snapshot (spec §6/§7, design §8).

SerpApiCallLog counts what we spent; this task records what SerpApi says is
left, per key slot, from SerpApi's documented /account endpoint. The two
together answer "are we about to run out on slot 7" without waiting for a
quota error to tell us.

Design §8 is explicit that failures here "are logged and skipped, never crash
the beat schedule", so nothing in this module raises. A key whose poll fails
still gets a row, with `error` populated instead of the counts — a failed
poll must be visible, not silently absent, or a dashboard gap looks
indistinguishable from a healthy quiet hour.

urllib is used rather than httpx to match app/deleted_showtimes/serp_client.py,
the other SerpApi client in the codebase, and to add no dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Optional

from sqlmodel import Session

from app.celery_app import celery
from app.config import settings
from app.deleted_showtimes.serp_key_rotation import _fingerprint

logger = logging.getLogger(__name__)

# Short by design: this is a status poll on a beat schedule, not a user
# request. If SerpApi is slow enough to exceed this, "unreachable" is the
# honest answer and the next run is only an hour away.
_ACCOUNT_TIMEOUT_SECONDS = 20

# Keep error text bounded — an HTML error page from a proxy can be enormous
# and the column is only there for triage.
_ERROR_MAX_CHARS = 300


def _fetch_account_json(api_key: str) -> dict:
    """GET settings.SERPAPI_ACCOUNT_URL for one key. The single network seam
    in this module, so tests monkeypatch exactly one function."""
    url = (
        settings.SERPAPI_ACCOUNT_URL
        + "?"
        + urllib.parse.urlencode({"api_key": api_key})
    )
    req = urllib.request.Request(
        url, headers={"User-Agent": "entelligence-usage-observability/1.0"}
    )
    with urllib.request.urlopen(req, timeout=_ACCOUNT_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(
            f"unexpected /account payload type: {type(payload).__name__}"
        )
    return payload


def _as_optional_int(value: Any) -> Optional[int]:
    """int, or None for anything that isn't one. None is the right answer for
    an unparseable count: 0 would read as "no credits left" on a dashboard."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@celery.task(name="app.tasks.serpapi_credit_task.snapshot_serpapi_credits")
def snapshot_serpapi_credits() -> int:
    """Write one SerpApiCreditSnapshot row per configured key slot.

    Returns the number of rows written (0 on any failure). Never raises.
    """
    if not settings.USAGE_TRACKING_ENABLED:
        return 0

    try:
        from app.models import SerpApiCreditSnapshot

        keys = settings.SERPAPI_API_KEYS
        if not keys:
            logger.info("serpapi_credit_snapshot: no SERPAPI_API_KEY* configured — nothing to poll")
            return 0

        rows: list[Any] = []
        for slot, key in keys:
            fingerprint = _fingerprint(key)
            try:
                data = _fetch_account_json(key)
            except Exception as exc:  # noqa: BLE001 — design §8
                logger.warning(
                    "serpapi_credit_snapshot: slot %d poll failed (%s: %s)",
                    slot, type(exc).__name__, exc,
                )
                rows.append(
                    SerpApiCreditSnapshot(
                        slot=slot,
                        key_fingerprint=fingerprint,
                        error=f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_CHARS],
                    )
                )
                continue

            rows.append(
                SerpApiCreditSnapshot(
                    slot=slot,
                    key_fingerprint=fingerprint,
                    plan_searches_left=_as_optional_int(data.get("plan_searches_left")),
                    extra_credits=_as_optional_int(data.get("extra_credits")),
                    total_searches_left=_as_optional_int(data.get("total_searches_left")),
                    this_month_usage=_as_optional_int(data.get("this_month_usage")),
                    # '' would be indistinguishable from "SerpApi didn't say".
                    account_email=(data.get("account_email") or None),
                )
            )

        from app.database import engine

        with Session(engine) as session:
            for row in rows:
                session.add(row)
            session.commit()

        logger.info("serpapi_credit_snapshot: wrote %d row(s)", len(rows))
        return len(rows)
    except Exception as exc:  # noqa: BLE001 — see module docstring / design §8
        logger.warning("serpapi_credit_snapshot_failed error=%s", exc)
        return 0

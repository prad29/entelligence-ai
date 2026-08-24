"""Self-tracked Serper quota status (spec §7).

Serper publishes no remaining-credits API — only a web dashboard — so
"credits left" is derived entirely from our own SerperCallLog rows against a
manually configured quota (settings.SERPER_QUOTA_TOTAL). This will drift if
the plan is topped up or changed without updating that setting; the payload
carries an explicit `warning` when the quota isn't configured, so a consumer
never mistakes an unconfigured 0 for "no credits left".
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, func, select

from app.config import settings


def serper_quota_status(session: Session) -> dict:
    from app.models import SerperCallLog

    stmt = select(func.count()).select_from(SerperCallLog)
    period_start = (settings.SERPER_QUOTA_PERIOD_START or "").strip()
    if period_start:
        try:
            since = datetime.strptime(period_start, "%Y-%m-%d")
            stmt = stmt.where(SerperCallLog.ts >= since)
        except ValueError:
            period_start = ""

    used = session.exec(stmt).one()
    quota_total = settings.SERPER_QUOTA_TOTAL
    quota_configured = quota_total > 0

    return {
        "quota_configured": quota_configured,
        "quota_total": quota_total if quota_configured else None,
        "quota_period_start": period_start or None,
        "used": used,
        "remaining": (quota_total - used) if quota_configured else None,
        "warning": (
            None
            if quota_configured
            else "SERPER_QUOTA_TOTAL is not configured — only usage count is known, "
            "not remaining balance."
        ),
    }

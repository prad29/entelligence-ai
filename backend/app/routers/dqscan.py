"""dqscan Settings-page section: cron config + manual trigger.

No auth — matches this codebase's no-auth internal-tool convention (see
calendar_extract.py's module docstring). The manual trigger and the
scheduled cron both run through the same run_dqscan_scan Celery task
(app/tasks/dqscan_task.py) — this router only decides *when*/*with what
arguments* to enqueue it, never runs dqscan itself.

Recipients are managed as real SNS subscriptions on whichever topic
matches DqscanSettings.env at Save time (see dqscan's own config.yaml/
config.prod.yaml for the topic ARNs — reused here via dqscan.config.
load_config so the ARN lives in exactly one place). Each address still has
to click AWS's one-time confirmation email before they receive anything —
this endpoint only manages the subscription list, not confirmation.
Switching `env` does NOT move anyone between topics automatically; the
next Save under the new env subscribes/unsubscribes against that env's
topic based on whatever recipient list is submitted then.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

import boto3
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.database import get_session
from app.tasks.dqscan_task import _dqscan_root, run_dqscan_scan

router = APIRouter(prefix="/api/v1/dqscan", tags=["dqscan"])

logger = logging.getLogger(__name__)


def _topic_arn_and_region(env: str) -> tuple[str, str]:
    from dqscan.config import load_config

    config_file = "config.prod.yaml" if env == "prod" else "config.yaml"
    cfg = load_config(str(_dqscan_root() / config_file))
    return cfg.sns.topic_arn, cfg.sns.aws_region


def _current_email_subscriptions(topic_arn: str, aws_region: str) -> dict[str, str]:
    """email -> 'confirmed' | 'pending'."""
    sns = boto3.client("sns", region_name=aws_region)
    result: dict[str, str] = {}
    paginator = sns.get_paginator("list_subscriptions_by_topic")
    for page in paginator.paginate(TopicArn=topic_arn):
        for sub in page["Subscriptions"]:
            if sub["Protocol"] != "email":
                continue
            arn = sub["SubscriptionArn"]
            result[sub["Endpoint"]] = "pending" if arn == "PendingConfirmation" else "confirmed"
    return result


def _apply_recipients(topic_arn: str, aws_region: str, desired: list[str]) -> dict[str, str]:
    sns = boto3.client("sns", region_name=aws_region)
    current = _current_email_subscriptions(topic_arn, aws_region)
    desired_set = set(desired)

    for email in desired_set - current.keys():
        sns.subscribe(TopicArn=topic_arn, Protocol="email", Endpoint=email)

    to_remove = {e for e in current.keys() - desired_set if current[e] != "pending"}
    if to_remove:
        for page in sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=topic_arn):
            for sub in page["Subscriptions"]:
                if sub["Protocol"] == "email" and sub["Endpoint"] in to_remove:
                    sns.unsubscribe(SubscriptionArn=sub["SubscriptionArn"])
    for email in current.keys() - desired_set:
        if current[email] == "pending":
            # AWS has no API to revoke an unconfirmed subscription -- it
            # just expires (~3 days) on its own if never confirmed.
            logger.info("dqscan_recipient_removed_while_pending email=%s topic=%s", email, topic_arn)

    return _current_email_subscriptions(topic_arn, aws_region)


def _get_or_create_settings(session: Session):
    from app.models import DqscanSettings

    cfg = session.get(DqscanSettings, 1)
    if cfg is None:
        cfg = DqscanSettings(id=1)
        session.add(cfg)
        session.commit()
        session.refresh(cfg)
    return cfg


def _serialize(cfg, recipient_status: dict[str, str] | None = None) -> dict:
    recipients = json.loads(cfg.recipients_json or "[]")
    return {
        "env": cfg.env,
        "cron_expression": cfg.cron_expression,
        "recipients": [
            {"email": e, "status": (recipient_status or {}).get(e, "unknown")} for e in recipients
        ],
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.get("/settings")
async def get_settings(session: Session = Depends(get_session)):
    cfg = _get_or_create_settings(session)
    topic_arn, aws_region = _topic_arn_and_region(cfg.env)
    try:
        status = _current_email_subscriptions(topic_arn, aws_region)
    except Exception:  # noqa: BLE001
        logger.exception("dqscan_settings_sns_lookup_failed")
        status = {}
    return _serialize(cfg, status)


class SaveSettingsRequest(BaseModel):
    env: str
    cron_expression: str
    recipients: list[str]


@router.put("/settings")
async def save_settings(payload: SaveSettingsRequest, session: Session = Depends(get_session)):
    from croniter import croniter

    from app.models import DqscanSettings

    if payload.env not in ("dev", "prod"):
        return {"error": "env must be 'dev' or 'prod'"}
    if not croniter.is_valid(payload.cron_expression):
        return {"error": "invalid cron expression"}

    topic_arn, aws_region = _topic_arn_and_region(payload.env)
    status = _apply_recipients(topic_arn, aws_region, payload.recipients)

    cfg = _get_or_create_settings(session)
    cfg.env = payload.env
    cfg.cron_expression = payload.cron_expression
    cfg.recipients_json = json.dumps(payload.recipients)
    cfg.updated_at = datetime.utcnow()
    session.add(cfg)
    session.commit()
    session.refresh(cfg)

    return _serialize(cfg, status)


class TriggerScanRequest(BaseModel):
    # Both optional -- omit both for "last 24 hours".
    from_date: date | None = None
    to_date: date | None = None


@router.post("/trigger")
async def trigger_scan(payload: TriggerScanRequest, session: Session = Depends(get_session)):
    cfg = _get_or_create_settings(session)
    to_date = payload.to_date or date.today()
    from_date = payload.from_date or (to_date - timedelta(days=1))

    task = run_dqscan_scan.delay(cfg.env, from_date.isoformat(), to_date.isoformat())
    return {"task_id": task.id, "env": cfg.env, "from": from_date.isoformat(), "to": to_date.isoformat()}

"""SES-based emailer for dqscan run results.

Sends the run's xlsx report as an attachment plus a plain-text findings
summary. Uses send_raw_email (not send_email) -- SES's simple send_email API
has no attachment support at all.
"""

from __future__ import annotations

import logging
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import boto3

from dqscan.models import RunResult

logger = logging.getLogger("dqscan.emailer")

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _summarize(run_result: RunResult) -> str:
    """High-level, pointer-form summary for the email body -- one bullet per
    firing rule, described by its issue (rule.message), not its ID. Full
    per-row detail lives only in the attached xlsx; this is meant to be
    readable in a few seconds without opening the attachment."""
    meta = run_result.meta
    active_findings = [
        o
        for o in run_result.outcomes
        if o.run_status in ("active", "calibration") and (o.count or 0) > 0
    ]
    active_findings.sort(key=lambda o: (_SEVERITY_ORDER.get(o.rule.severity, 99), -(o.count or 0)))
    errors = [o for o in run_result.outcomes if o.run_status == "error"]

    lines = [
        f"dqscan run: {meta.from_date} to {meta.to_date} (schema: {meta.schema})",
        f"Rows scanned: {meta.rows_scanned:,} | Distinct showtimes: {meta.distinct_showtimes:,} | "
        f"Duration: {meta.duration_seconds}s",
        "",
    ]
    if meta.extent_note:
        lines.append(f"Note: {meta.extent_note}")
        lines.append("")

    if errors:
        lines.append(f"{len(errors)} rule(s) errored during this run:")
        for o in errors:
            lines.append(f"  - {o.rule.name}: {o.error}")
        lines.append("")

    if not active_findings:
        lines.append("No findings.")
        return "\n".join(lines)

    lines.append(f"{len(active_findings)} issue(s) found:")
    lines.append("")
    current_severity = None
    for o in active_findings:
        if o.rule.severity != current_severity:
            current_severity = o.rule.severity
            lines.append(f"{current_severity.upper()}:")
        pct = f"{o.percentage * 100:.2f}%" if o.percentage is not None else "count only, no % baseline"
        flag = " (needs calibration -- review threshold)" if o.run_status == "calibration" else ""
        lines.append(f"  - {o.rule.message} -- {o.count} row(s), {pct}{flag}")

    lines.append("")
    lines.append("Full row-level detail is in the attached report.")
    return "\n".join(lines)


def send_report_email(
    run_result: RunResult,
    xlsx_path: str,
    *,
    sender: str,
    recipients: list[str],
    aws_region: str = "us-east-1",
) -> str:
    """Send the run's xlsx report as an attachment via SES. Returns the SES MessageId.

    Raises botocore.exceptions.ClientError (unmodified) on any SES failure --
    e.g. MessageRejected if `sender` isn't a verified identity -- so a cron
    wrapper can log/alert on it rather than this module silently swallowing it.
    """
    meta = run_result.meta
    subject = f"dqscan report: {meta.schema} {meta.from_date}..{meta.to_date}"

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(_summarize(run_result), "plain"))

    xlsx_file = Path(xlsx_path)
    with open(xlsx_file, "rb") as f:
        attachment = MIMEApplication(f.read(), _subtype="xlsx")
    attachment.add_header("Content-Disposition", "attachment", filename=xlsx_file.name)
    msg.attach(attachment)

    ses = boto3.client("ses", region_name=aws_region)
    response = ses.send_raw_email(
        Source=sender,
        Destinations=recipients,
        RawMessage={"Data": msg.as_string()},
    )
    message_id = response["MessageId"]
    logger.info("Sent dqscan report email: message_id=%s recipients=%s", message_id, recipients)
    return message_id

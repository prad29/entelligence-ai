"""SNS-based report notifier for dqscan run results.

Unlike emailer.py (SES, attaches the xlsx directly), this uploads the
report to S3 and publishes a plain-text notification with a download link
to an SNS topic -- SNS's email protocol has no attachment support at all,
so there's no way around the upload-and-link step.

The point of this path over emailer.py: recipients self-manage via the SNS
topic (subscribe/unsubscribe through the confirmation link AWS emails
them), so adding or removing someone never means touching a config file or
redeploying. The tradeoff, accepted deliberately: no attachment, and the
notification comes from AWS's own SNS sending address, not a custom
"From" -- this was chosen specifically to sidestep a deliverability
problem with the SES path (see emailer.py's module docstring).
"""

from __future__ import annotations

import logging
from pathlib import Path

import boto3

from dqscan.emailer import _summarize, build_subject
from dqscan.models import RunResult

logger = logging.getLogger("dqscan.sns_notifier")

# S3 presigned URLs are capped at 7 days by AWS itself when signed with
# long-term IAM credentials -- matches run_daily's report retention window,
# so a link never outlives the report it points to.
_PRESIGNED_URL_EXPIRY_SECONDS = 7 * 86400

_ATTACHMENT_LINE = "Full row level details are attached in email"


def _upload_report(xlsx_path: str, *, s3_bucket: str, s3_prefix: str, schema: str, aws_region: str) -> str:
    s3 = boto3.client("s3", region_name=aws_region)
    xlsx_file = Path(xlsx_path)
    key = f"{s3_prefix.rstrip('/')}/{schema}/{xlsx_file.name}"
    s3.upload_file(str(xlsx_file), s3_bucket, key)
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": s3_bucket, "Key": key}, ExpiresIn=_PRESIGNED_URL_EXPIRY_SECONDS,
    )
    logger.info("Uploaded report to s3://%s/%s", s3_bucket, key)
    return url


def publish_report_notification(
    run_result: RunResult,
    xlsx_path: str,
    *,
    topic_arn: str,
    s3_bucket: str,
    s3_prefix: str = "dqscan-reports",
    aws_region: str = "us-east-1",
) -> str:
    """Uploads the xlsx to S3, publishes the same findings summary as
    emailer.send_report_email (with the attachment line swapped for a
    download link) to the SNS topic. Returns the SNS MessageId.

    Raises botocore.exceptions.ClientError unmodified on any S3/SNS
    failure, same contract as emailer.send_report_email.
    """
    meta = run_result.meta
    subject = build_subject(run_result)[:100]  # SNS Subject hard cap

    download_url = _upload_report(
        xlsx_path, s3_bucket=s3_bucket, s3_prefix=s3_prefix, schema=meta.schema, aws_region=aws_region
    )

    body = _summarize(run_result).replace(
        _ATTACHMENT_LINE,
        f"Full row level details - {download_url}\n(link expires in 7 days)",
    )

    sns = boto3.client("sns", region_name=aws_region)
    response = sns.publish(TopicArn=topic_arn, Subject=subject, Message=body)
    message_id = response["MessageId"]
    logger.info("Published dqscan report notification: message_id=%s topic=%s", message_id, topic_arn)
    return message_id

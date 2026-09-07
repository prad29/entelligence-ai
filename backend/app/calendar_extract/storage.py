"""
S3-backed storage for Competitive Calendar Extraction uploads/outputs.

Mirrors app/deleted_showtimes/storage.py: its own dedicated bucket/prefixes
(settings.CALENDAR_EXTRACT_S3_BUCKET), independent of every other feature's
S3 storage — backend and celery-worker are separate containers with no
shared filesystem.
"""

from __future__ import annotations

from app.config import settings

UPLOAD_PREFIX = "competitive-calendar-in"
OUTPUT_PREFIX = "competitive-calendar-out"


def _client():
    import boto3

    return boto3.client("s3", region_name=settings.CALENDAR_EXTRACT_S3_REGION)


def _require_bucket() -> str:
    if not settings.CALENDAR_EXTRACT_S3_BUCKET:
        raise RuntimeError(
            "CALENDAR_EXTRACT_S3_BUCKET is not configured — calendar-extract "
            "upload/output storage requires an S3 bucket shared by all "
            "backend containers."
        )
    return settings.CALENDAR_EXTRACT_S3_BUCKET


def upload_key(job_id: str) -> str:
    return f"{UPLOAD_PREFIX}/{job_id}.pdf"


def output_key(job_id: str) -> str:
    return f"{OUTPUT_PREFIX}/{job_id}_output.xlsx"


def put_bytes(key: str, data: bytes) -> None:
    _client().put_object(Bucket=_require_bucket(), Key=key, Body=data)


def get_bytes(key: str) -> bytes:
    resp = _client().get_object(Bucket=_require_bucket(), Key=key)
    return resp["Body"].read()


def exists(key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        _client().head_object(Bucket=_require_bucket(), Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise

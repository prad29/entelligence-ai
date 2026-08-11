"""
S3-backed storage for the Deleted Showtimes Check upload/output files.

Uses its own dedicated bucket/prefixes (settings.DELETED_SHOWTIME_S3_BUCKET),
independent of the Mode B agentic batch pipeline's bucket — backend,
celery-worker, and the deleted-showtimes worker are separate containers with
no shared filesystem, so a file written to local /tmp by one is invisible to
the others (the router writes the upload, but finalize_job runs on a
different container).
"""

from __future__ import annotations

from app.config import settings

UPLOAD_PREFIX = "deleted-showtimes-input"
OUTPUT_PREFIX = "deleted-showtimes-output"
# Audit JSON lands in the same output folder as the result workbook,
# distinguished only by filename — there is no separate audit prefix.
AUDIT_PREFIX = "deleted-showtimes-output"


def _client():
    import boto3

    return boto3.client("s3", region_name=settings.DELETED_SHOWTIME_S3_REGION)


def _require_bucket() -> str:
    if not settings.DELETED_SHOWTIME_S3_BUCKET:
        raise RuntimeError(
            "DELETED_SHOWTIME_S3_BUCKET is not configured — deleted-showtimes "
            "upload/output storage requires an S3 bucket shared by all "
            "backend containers."
        )
    return settings.DELETED_SHOWTIME_S3_BUCKET


def upload_key(job_id: str, ext: str) -> str:
    return f"{UPLOAD_PREFIX}/{job_id}{ext}"


def output_key(job_id: str) -> str:
    return f"{OUTPUT_PREFIX}/{job_id}_output.xlsx"


def audit_key(job_id: str) -> str:
    return f"{AUDIT_PREFIX}/{job_id}_audit.json"


def put_bytes(key: str, data: bytes) -> None:
    _client().put_object(Bucket=_require_bucket(), Key=key, Body=data)


def get_bytes(key: str) -> bytes:
    resp = _client().get_object(Bucket=_require_bucket(), Key=key)
    return resp["Body"].read()


def delete(key: str) -> None:
    _client().delete_object(Bucket=_require_bucket(), Key=key)


def exists(key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        _client().head_object(Bucket=_require_bucket(), Key=key)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise

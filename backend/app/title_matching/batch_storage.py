"""
S3-backed storage for the Mode B batch upload/output files.

backend, celery-worker, and celery-agentic-worker are separate containers with
no shared filesystem, so a file written to local /tmp by one is invisible to
the others (the router writes the upload, but finalize_batch runs on a
different container). S3 gives every container the same object store instead.

Plain functions (not a class) so tests can monkeypatch put/get/delete directly,
matching the existing _get_redis() pattern in agentic_match_task.py.
"""

from __future__ import annotations

from app.config import settings

UPLOAD_PREFIX = "batch-uploads"
OUTPUT_PREFIX = "batch-outputs"


def _client():
    import boto3

    return boto3.client("s3", region_name=settings.AGENTIC_BATCH_S3_REGION)


def _require_bucket() -> str:
    if not settings.AGENTIC_BATCH_S3_BUCKET:
        raise RuntimeError(
            "AGENTIC_BATCH_S3_BUCKET is not configured — batch upload/output "
            "storage requires an S3 bucket shared by all backend containers."
        )
    return settings.AGENTIC_BATCH_S3_BUCKET


def upload_key(job_id: str, ext: str) -> str:
    return f"{UPLOAD_PREFIX}/{job_id}{ext}"


def output_key(job_id: str) -> str:
    return f"{OUTPUT_PREFIX}/{job_id}_output.xlsx"


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

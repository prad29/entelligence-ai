"""
AWS Bedrock async batch inference via CreateModelInvocationJob.
Requires: S3 bucket + IAM role ARN configured in settings.
Falls back gracefully if not configured.
"""
import hashlib
import json
import logging
import time
from typing import Optional

import boto3
import botocore.exceptions

from app.config import settings

logger = logging.getLogger(__name__)

_BATCH_PROMPT = (
    'Classify this theater amenity into exactly one movie format: 70MM, 35MM, 3D, or 2D.\n'
    'Amenity: "{amenity}"\n'
    'Reply ONLY with valid JSON: {{"format": "<70MM|35MM|3D|2D>", "confidence": <0.0-1.0>, "reasoning": "<brief>"}}'
)


def _record_id(amenity: str) -> str:
    return hashlib.sha256(amenity.strip().lower().encode()).hexdigest()[:16]


def _s3():
    return boto3.client("s3", region_name=settings.BEDROCK_REGION)


def _bedrock():
    return boto3.client("bedrock", region_name=settings.BEDROCK_REGION)


def is_configured() -> bool:
    return bool(settings.S3_BATCH_BUCKET and settings.BEDROCK_BATCH_ROLE_ARN)


def submit_batch_job(job_id: str, amenities: list[str]) -> Optional[str]:
    """Write JSONL to S3 and submit CreateModelInvocationJob. Returns job ARN."""
    s3 = _s3()
    bedrock = _bedrock()

    lines = []
    for amenity in amenities:
        record = {
            "recordId": _record_id(amenity),
            "modelInput": {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": _BATCH_PROMPT.format(amenity=amenity)}],
            },
        }
        lines.append(json.dumps(record, separators=(",", ":")))

    input_key = f"movie-batch/{job_id}/input.jsonl"
    output_prefix = f"movie-batch/{job_id}/output/"
    input_uri = f"s3://{settings.S3_BATCH_BUCKET}/{input_key}"
    output_uri = f"s3://{settings.S3_BATCH_BUCKET}/{output_prefix}"

    s3.put_object(
        Bucket=settings.S3_BATCH_BUCKET,
        Key=input_key,
        Body="\n".join(lines).encode("utf-8"),
        ContentType="application/jsonl",
    )

    try:
        resp = bedrock.create_model_invocation_job(
            jobName=f"movie-fmt-{job_id[:8]}",
            modelId=settings.ASYNC_BATCH_MODEL_ID,
            roleArn=settings.BEDROCK_BATCH_ROLE_ARN,
            inputDataConfig={"s3InputDataConfig": {"s3Uri": input_uri, "s3InputFormat": "JSONL"}},
            outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_uri}},
        )
        arn = resp["jobArn"]
        logger.info("bedrock_batch_submitted job_id=%s arn=%s count=%d", job_id, arn, len(amenities))
        return arn
    except botocore.exceptions.ClientError as e:
        logger.error("bedrock_batch_submit_failed job_id=%s error=%s", job_id, e)
        return None


def poll_batch_job(job_arn: str) -> Optional[str]:
    """Poll until terminal status. Returns 'Completed', 'Failed', 'Stopped', or None on timeout."""
    bedrock = _bedrock()
    deadline = time.time() + settings.BATCH_JOB_MAX_WAIT
    while time.time() < deadline:
        resp = bedrock.get_model_invocation_job(jobIdentifier=job_arn)
        status = resp["status"]
        if status in ("Completed", "Failed", "Stopped"):
            logger.info("bedrock_batch_done arn=%s status=%s", job_arn, status)
            return status
        time.sleep(settings.BATCH_JOB_POLL_INTERVAL)
    logger.warning("bedrock_batch_timeout arn=%s", job_arn)
    return None


def read_batch_results(job_id: str, amenities: list[str]):
    """Read output JSONL from S3. Returns dict keyed by amenity.lower() -> BedrockSuggestion or None."""
    from app.detection.types import BedrockSuggestion

    s3 = _s3()
    output_prefix = f"movie-batch/{job_id}/output/"
    id_to_amenity = {_record_id(a): a.strip().lower() for a in amenities}
    results: dict[str, Optional[object]] = {}

    resp = s3.list_objects_v2(Bucket=settings.S3_BATCH_BUCKET, Prefix=output_prefix)
    for obj in resp.get("Contents", []):
        if not obj["Key"].endswith(".jsonl"):
            continue
        body = s3.get_object(Bucket=settings.S3_BATCH_BUCKET, Key=obj["Key"])["Body"].read()
        for line in body.decode("utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                rid = record.get("recordId", "")
                content = record.get("modelOutput", {}).get("content", [{}])
                text = (content[0].get("text", "{}") if content else "{}").strip()
                if text.startswith("```"):
                    parts = text.split("```", 2)
                    inner = parts[1] if len(parts) >= 2 else text
                    text = inner.lstrip("json").strip()
                parsed = json.loads(text)
                amenity_key = id_to_amenity.get(rid)
                if amenity_key:
                    results[amenity_key] = BedrockSuggestion(
                        detected_keyword=None,
                        suggested_screen_format=parsed.get("format", "2D"),
                        confidence=float(parsed.get("confidence", 0.5)),
                        reasoning=parsed.get("reasoning", ""),
                    )
            except Exception as exc:
                logger.warning("bedrock_batch_parse_error line=%r error=%s", line[:80], exc)

    return results


def run_async_batch(job_id: str, amenities: list[str]) -> dict:
    """
    Full pipeline: submit → poll → read.
    Returns dict keyed by amenity.lower(). Empty dict on any failure.
    """
    unique = list({a.strip().lower() for a in amenities if a.strip()})
    if not unique:
        return {}

    arn = submit_batch_job(job_id, unique)
    if not arn:
        return {}

    status = poll_batch_job(arn)
    if status != "Completed":
        return {}

    return read_batch_results(job_id, unique)

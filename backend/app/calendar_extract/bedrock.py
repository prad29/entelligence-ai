"""Bedrock Converse call + parse, shared by classify.py and extractor.py.

Only supports the "output_config" (JSON-schema-constrained output) backend —
the only backend CALENDAR_EXTRACT_MODEL_ID (Claude Haiku 4.5, chosen by
scripts/calendar_eval.py's harness) needs. If the model is ever changed to
one requiring a different backend, this module is the one place to extend.

boto3 clients are NOT thread-safe; the module-level cache below is only
valid under Celery's prefork pool, matching lobby_check/extractor.py's same
caveat.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.config import settings
from app.observability.context import LlmCallContext, TokenUsage
from app.observability.llm_logging import log_llm_call

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("bedrock-runtime", region_name=settings.CALENDAR_EXTRACT_S3_REGION)
    return _client


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_output_config(resp: dict) -> dict:
    blocks = resp["output"]["message"]["content"]
    text = "".join(b.get("text", "") for b in blocks).strip()
    if not text:
        raise ValueError("empty text response")
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            raise ValueError(f"no JSON object found in: {text[:200]}")
        return json.loads(m.group(0))


def call_converse_json(
    *,
    system_prompt: str,
    prompt_only_suffix: str,
    schema: dict[str, Any],
    user_text: str,
    tool_name: str,
    task_type: str,
    max_tokens: int = 8000,
) -> tuple[dict, TokenUsage]:
    """One Converse call constrained to `schema` via output_config, with one
    repair retry on a parse/validation failure. Returns (parsed_json, usage).
    Raises on a second failure."""
    client = _get_client()
    ctx = LlmCallContext(task_type=task_type, call_path="calendar_extract")

    extra = ""
    last_error: Exception | None = None
    for attempt in range(2):
        kwargs = {
            "modelId": settings.CALENDAR_EXTRACT_MODEL_ID,
            "messages": [{"role": "user", "content": [{"text": user_text + extra}]}],
            "system": [{"text": system_prompt}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0},
            "outputConfig": {
                "textFormat": {
                    "type": "json_schema",
                    "structure": {"jsonSchema": {"name": tool_name, "schema": json.dumps(schema)}},
                }
            },
        }
        t0 = time.time()
        resp = client.converse(**kwargs)
        latency_ms = int((time.time() - t0) * 1000)
        usage_dict = resp.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_dict.get("inputTokens", 0),
            output_tokens=usage_dict.get("outputTokens", 0),
        )
        try:
            rec = _parse_output_config(resp)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log_llm_call(
                ctx, model_id=settings.CALENDAR_EXTRACT_MODEL_ID, usage=usage,
                latency_ms=latency_ms, status="failure", error_type=type(exc).__name__,
                retry_count=attempt,
            )
            extra = f"\n\nYour previous response failed to parse: {exc}. Return corrected JSON only."
            continue
        log_llm_call(
            ctx, model_id=settings.CALENDAR_EXTRACT_MODEL_ID, usage=usage,
            latency_ms=latency_ms, status="success", retry_count=attempt,
        )
        return rec, usage

    raise RuntimeError(f"calendar_extract Bedrock call failed after retry: {last_error}")

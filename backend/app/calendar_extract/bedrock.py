"""Bedrock Converse call + parse, shared by classify.py and extractor.py.

Supports the same three response-shaping backends scripts/calendar_eval.py's
harness validated per model — "output_config" (JSON-schema-constrained
output, e.g. Claude Haiku 4.5), "forced_tool" (tool-use, e.g. Claude Sonnet
5 / Amazon Nova), and "prompt_only" (plain text with a JSON-only
instruction suffix, parsed out of the response — e.g. Mistral Large / Qwen).
`_MODEL_BACKENDS` below must have an entry for whatever
CALENDAR_EXTRACT_MODEL_ID is currently set to; unknown models fall back to
"prompt_only" as the least assumption-laden option.

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
from app.observability.constants import PATH_BEDROCK_CONVERSE
from app.observability.context import LlmCallContext, TokenUsage
from app.observability.llm_logging import log_llm_call

# Kept in sync with scripts/calendar_eval.py's MODELS registry — that
# harness is what determined each model's correct backend.
_MODEL_BACKENDS: dict[str, str] = {
    "nvidia.nemotron-nano-12b-v2": "output_config",
    "us.amazon.nova-2-lite-v1:0": "forced_tool",
    "qwen.qwen3-vl-235b-a22b": "prompt_only",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": "output_config",
    "us.anthropic.claude-sonnet-5": "forced_tool",
    "mistral.mistral-large-3-675b-instruct": "prompt_only",
}

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3

        _client = boto3.client("bedrock-runtime", region_name=settings.CALENDAR_EXTRACT_S3_REGION)
    return _client


def _backend_for(model_id: str) -> str:
    return _MODEL_BACKENDS.get(model_id, "prompt_only")


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_text_json(resp: dict) -> dict:
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


def _parse_tool_use(resp: dict) -> dict:
    blocks = resp["output"]["message"]["content"]
    for b in blocks:
        if "toolUse" in b:
            return b["toolUse"]["input"]
    raise ValueError("no toolUse block in response")


def _build_kwargs(
    *, backend: str, model_id: str, system_prompt: str, prompt_only_suffix: str,
    schema: dict[str, Any], user_text: str, tool_name: str, max_tokens: int, use_temp: bool,
) -> dict[str, Any]:
    system_text = system_prompt + (prompt_only_suffix if backend == "prompt_only" else "")
    kwargs: dict[str, Any] = {
        "modelId": model_id,
        "messages": [{"role": "user", "content": [{"text": user_text}]}],
        "system": [{"text": system_text}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if use_temp:
        kwargs["inferenceConfig"]["temperature"] = 0.0

    if backend == "output_config":
        kwargs["outputConfig"] = {
            "textFormat": {
                "type": "json_schema",
                "structure": {"jsonSchema": {"name": tool_name, "schema": json.dumps(schema)}},
            }
        }
    elif backend == "forced_tool":
        kwargs["toolConfig"] = {
            "tools": [{"toolSpec": {"name": tool_name, "description": "Emit the requested structured data.",
                                    "inputSchema": {"json": schema}}}],
            "toolChoice": {"tool": {"name": tool_name}},
        }
    return kwargs


def call_converse_json(
    *,
    system_prompt: str,
    prompt_only_suffix: str,
    schema: dict[str, Any],
    user_text: str,
    tool_name: str,
    task_type: str,
    job_id: str | None = None,
    max_tokens: int = 8000,
) -> tuple[dict, TokenUsage]:
    """One Converse call constrained to `schema` via whatever backend
    CALENDAR_EXTRACT_MODEL_ID needs, with one repair retry on a parse/
    validation failure. Returns (parsed_json, usage). Raises on a second
    failure."""
    client = _get_client()
    ctx = LlmCallContext(
        task_type=task_type, call_path=PATH_BEDROCK_CONVERSE,
        job_id=job_id, job_type="CalendarExtractJob",
    )
    model_id = settings.CALENDAR_EXTRACT_MODEL_ID
    backend = _backend_for(model_id)
    parse = _parse_tool_use if backend == "forced_tool" else _parse_text_json

    extra = ""
    use_temp = True
    last_error: Exception | None = None
    for attempt in range(2):
        kwargs = _build_kwargs(
            backend=backend, model_id=model_id, system_prompt=system_prompt,
            prompt_only_suffix=prompt_only_suffix, schema=schema,
            user_text=user_text + extra, tool_name=tool_name, max_tokens=max_tokens,
            use_temp=use_temp,
        )
        t0 = time.time()
        try:
            resp = client.converse(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if use_temp and "temperature" in str(exc).lower():
                use_temp = False
                continue
            raise
        latency_ms = int((time.time() - t0) * 1000)
        usage_dict = resp.get("usage", {})
        usage = TokenUsage(
            input_tokens=usage_dict.get("inputTokens", 0),
            output_tokens=usage_dict.get("outputTokens", 0),
        )
        try:
            rec = parse(resp)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log_llm_call(
                ctx, model_id=model_id, usage=usage,
                latency_ms=latency_ms, status="failure", error_type=type(exc).__name__,
                retry_count=attempt,
            )
            extra = f"\n\nYour previous response failed to parse: {exc}. Return corrected JSON only."
            continue
        log_llm_call(
            ctx, model_id=model_id, usage=usage,
            latency_ms=latency_ms, status="success", retry_count=attempt,
        )
        return rec, usage

    raise RuntimeError(f"calendar_extract Bedrock call failed after retry: {last_error}")

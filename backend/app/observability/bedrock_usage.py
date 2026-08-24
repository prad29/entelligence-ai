"""Extract token usage from a Bedrock InvokeModel response.

bedrock_client.py already receives everything needed and throws it away
(see its resp.json() at bedrock_client.py:81, which only reads
outputs/choices). This module reads the usage facts out of the same response
without changing what the client returns.

Three sources are consulted, most-specific first, because settings.BEDROCK_MODEL_ID
is configurable and the response shape follows the model family:
  1. Anthropic-style body usage: input_tokens/output_tokens plus
     cache_read_input_tokens/cache_creation_input_tokens.
  2. OpenAI/Mistral-style body usage: prompt_tokens/completion_tokens.
  3. Bedrock's own response headers, which are present for every model:
     X-Amzn-Bedrock-Input-Token-Count / -Output-Token-Count.

Never raises — a missing or malformed usage block yields zeros, and the
underlying call proceeds untouched (spec §7).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

from app.observability.context import TokenUsage

logger = logging.getLogger(__name__)

_INPUT_HEADER = "x-amzn-bedrock-input-token-count"
_OUTPUT_HEADER = "x-amzn-bedrock-output-token-count"

# Request tagging header (spec §7): a free secondary attribution path that
# also surfaces in AWS Bedrock model invocation logging, so the AWS-side
# audit trail (§4) can be sliced by task without joining to our DB.
REQUEST_METADATA_HEADER = "X-Amzn-Bedrock-Request-Metadata"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _header_int(headers: Optional[Mapping[str, str]], name: str) -> int:
    if not headers:
        return 0
    # httpx.Headers is already case-insensitive, but a plain dict from a test
    # or a mocked client is not — normalise so both work.
    try:
        direct = headers.get(name)
    except Exception:  # noqa: BLE001
        direct = None
    if direct is not None:
        return _as_int(direct)
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    return _as_int(lowered.get(name))


def extract_bedrock_usage(
    body: Optional[dict],
    headers: Optional[Mapping[str, str]] = None,
) -> TokenUsage:
    """Best-effort token counts for one Bedrock InvokeModel response."""
    usage: dict = {}
    if isinstance(body, dict) and isinstance(body.get("usage"), dict):
        usage = body["usage"]

    input_tokens = _as_int(usage.get("input_tokens")) or _as_int(usage.get("prompt_tokens"))
    output_tokens = _as_int(usage.get("output_tokens")) or _as_int(usage.get("completion_tokens"))

    # Cache accounting is opportunistic (spec §3) — absent for models/configs
    # where prompt caching is off, which is not an error.
    cache_read = _as_int(usage.get("cache_read_input_tokens")) or _as_int(
        usage.get("cache_read_tokens")
    )
    cache_write = _as_int(usage.get("cache_creation_input_tokens")) or _as_int(
        usage.get("cache_write_tokens")
    )

    if not input_tokens:
        input_tokens = _header_int(headers, _INPUT_HEADER)
    if not output_tokens:
        output_tokens = _header_int(headers, _OUTPUT_HEADER)

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
    )


def build_request_metadata(task_type: str, market: Optional[str] = None) -> str:
    """Compact JSON for the X-Amzn-Bedrock-Request-Metadata header (spec §7).

    Bedrock bounds this header's length, so it is serialized without spaces
    and carries only the two dimensions that are useful in CloudWatch/Athena
    when cross-checking our own LlmCallLog against AWS invocation logging.
    """
    payload = {"task_type": task_type}
    if market:
        payload["market"] = market
    return json.dumps(payload, separators=(",", ":"))

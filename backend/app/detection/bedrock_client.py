import json
import logging
import time
from typing import Optional

import httpx

from app.config import settings
from app.detection.types import BedrockSuggestion
from app.observability.bedrock_usage import (
    REQUEST_METADATA_HEADER,
    build_request_metadata,
    extract_bedrock_usage,
)
from app.observability.constants import (
    CALLER_PORTAL,
    PATH_BEDROCK_DIRECT,
    STATUS_FAILURE,
    STATUS_SUCCESS,
    TASK_AMENITY_DETECTION,
)
from app.observability.context import LlmCallContext, TokenUsage
from app.observability.llm_logging import log_llm_call

logger = logging.getLogger(__name__)

# Default attribution when a caller doesn't supply one. This client's original
# and still most common caller is domestic amenity/screen-format detection
# (app/workers/batch_worker.py); movie-format classification
# (app/workers/movie_batch_worker.py) passes its own context instead.
DEFAULT_USAGE_CONTEXT = LlmCallContext(
    task_type=TASK_AMENITY_DETECTION,
    call_path=PATH_BEDROCK_DIRECT,
    caller_type=CALLER_PORTAL,
)

SYSTEM_PROMPT = (
    "You are a theater screen-format classifier. "
    "Use ONLY the provided known formats list — no training-data inference. "
    "Return Standard if nothing matches. Return valid JSON only."
)

_INVOKE_PATH = "/model/{model_id}/invoke"
_LIST_MODELS_PATH = "/foundation-models"


def _base_url() -> str:
    return f"https://bedrock-runtime.{settings.BEDROCK_REGION}.amazonaws.com"


def _control_url() -> str:
    return f"https://bedrock.{settings.BEDROCK_REGION}.amazonaws.com"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {settings.BEDROCK_API_KEY}"}


_MAX_RETRIES = 3
_BACKOFF_BASE = 1


class BedrockClient:
    def classify_single(
        self,
        amenity: str,
        circuit: str,
        known_formats: list,
        usage_ctx: Optional[LlmCallContext] = None,
    ) -> Optional[BedrockSuggestion]:
        """Classify one amenity string via Bedrock.

        Return value and positional signature are unchanged from before
        instrumentation; `usage_ctx` is an optional trailing keyword so all
        existing call sites keep working untouched.

        Every exit path writes exactly one LlmCallLog row (spec §7). The write
        itself is wrapped separately from the Bedrock call so a logging
        failure can never change what this method returns.
        """
        ctx = usage_ctx or DEFAULT_USAGE_CONTEXT
        model_id = settings.BEDROCK_MODEL_ID
        started = time.monotonic()
        attempt_count = 0

        def _emit(
            usage: TokenUsage,
            status: str,
            error_type: Optional[str],
        ) -> None:
            try:
                log_llm_call(
                    ctx,
                    model_id=model_id,
                    usage=usage,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    status=status,
                    error_type=error_type,
                    retry_count=attempt_count,
                )
            except Exception as log_exc:  # noqa: BLE001 — spec §7
                logger.warning(
                    "bedrock_usage_log_failed amenity=%r error=%s", amenity, log_exc
                )

        try:
            prompt = (
                f'Amenity: "{amenity}"\nCircuit: "{circuit or "unknown"}"\n'
                "Known formats:\n"
                + "\n".join(f"- {f}" for f in known_formats)
                + '\n\nReturn ONLY JSON: {"detected_keyword": null_or_str, "suggested_screen_format": str, "confidence": 0.0-1.0, "reasoning": str}'
            )
            body = {
                "system": SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 256,
                "temperature": 0,
            }
            url = _base_url() + _INVOKE_PATH.format(model_id=model_id)

            # Request tagging (spec §7): free secondary attribution that also
            # lands in AWS Bedrock model invocation logging (§4).
            request_headers = {
                **_auth_headers(),
                "Content-Type": "application/json",
                REQUEST_METADATA_HEADER: build_request_metadata(
                    ctx.task_type, ctx.market
                ),
            }

            resp = None
            for attempt in range(_MAX_RETRIES + 1):
                attempt_count = attempt
                resp = httpx.post(
                    url,
                    headers=request_headers,
                    json=body,
                    timeout=10,
                )
                if resp.status_code != 429:
                    break
                if attempt < _MAX_RETRIES:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))

            if resp.status_code == 429:
                logger.warning(
                    "bedrock_throttled_after_retries",
                    extra={"amenity": amenity, "retries": _MAX_RETRIES},
                )
                _emit(TokenUsage(), STATUS_FAILURE, "Throttled")
                return None

            resp.raise_for_status()
            raw = resp.json()
            # Usage is captured before any parsing can fail: the tokens were
            # spent whether or not the model's JSON is well-formed.
            usage = extract_bedrock_usage(raw, getattr(resp, "headers", None))
            # Support Mistral (choices[0].message.content) and legacy outputs[0].text shapes
            text = (raw.get("outputs") or [{}])[0].get("text") or (
                raw.get("choices") or [{}]
            )[0].get("message", {}).get("content", "{}")
            # Strip markdown code fences if present (```json ... ```)
            text = text.strip()
            if text.startswith("```"):
                # Split on first fence opening, take content after it
                parts = text.split("```", 2)
                # parts[0]='', parts[1]='json\n{...}\n', parts[2]=''
                inner = parts[1] if len(parts) >= 2 else text
                if inner.startswith("json"):
                    inner = inner[4:]
                text = inner.strip()
            parsed = json.loads(text)
            _emit(usage, STATUS_SUCCESS, None)
            return BedrockSuggestion(
                detected_keyword=parsed.get("detected_keyword"),
                suggested_screen_format=parsed.get("suggested_screen_format", "Standard"),
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", ""),
            )
        except Exception as e:
            logger.error(
                "bedrock_classify_single_error",
                extra={"error": str(e), "amenity": amenity},
            )
            _emit(TokenUsage(), STATUS_FAILURE, type(e).__name__)
            return None

    def check_connection(self) -> bool:
        try:
            url = _control_url() + _LIST_MODELS_PATH
            resp = httpx.get(
                url,
                headers=_auth_headers(),
                params={"byOutputModality": "TEXT"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


bedrock_client = BedrockClient()

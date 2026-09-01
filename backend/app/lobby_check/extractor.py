"""Boto3 Converse-API call to Qwen 3-VL on Bedrock, plus parse/validate/
repair-retry — the productionized core of mmvision.py's call_bedrock /
parse_response / validate (repo root prototype).

Model for this module's instrumentation pattern:
app/detection/bedrock_client.py's `_emit()` closure and its "usage is
captured before any parsing can fail" ordering. Every converse ATTEMPT
(not just every extraction) writes its own LlmCallLog row — a repaired
extraction therefore writes two rows, unlike bedrock_client.classify_single's
single row per extraction, because each attempt here spends real tokens
(that client's retries are pure HTTP 429 replays that spend none).

boto3 clients are NOT thread-safe; the module-level client cache below is
only valid under Celery's prefork pool — the pool celery-lobby-check-worker
actually runs. If this queue is ever switched to a threaded pool this must
become thread-local.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

from app.config import settings
from app.lobby_check.errors import (
    LobbyCheckImageError,
    LobbyCheckSchemaError,
    LobbyCheckThrottleError,
    LobbyCheckTransientError,
)
from app.lobby_check.prompt import (
    FIELD_ORDER,
    FRAMING_HINT,
    PROMPT_ONLY_SUFFIX,
    SYSTEM_PROMPT,
    USER_TEXT,
)
from app.lobby_check.taxonomy import DEFECTS, MATERIAL_CONDITIONS, MATERIAL_TYPES
from app.lobby_check.types import ExtractionResult
from app.observability.bedrock_usage import extract_converse_usage
from app.observability.constants import STATUS_FAILURE, STATUS_SUCCESS
from app.observability.context import LlmCallContext, TokenUsage
from app.observability.cost import estimate_cost_usd
from app.observability.llm_logging import log_llm_call

logger = logging.getLogger(__name__)

# Bedrock error codes classified per docs/plans/2026-09-01-lobby-check-
# design.md §4.3. Anything not in either set (ValidationException,
# AccessDeniedException, ...) is deterministic and propagates unclassified —
# the Celery task layer (lobby_check_task.py) treats "not one of these two
# LobbyCheck*Error types" as fail-fast, no retry.
_THROTTLE_ERROR_CODES = {
    "ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException",
}
_TRANSIENT_ERROR_CODES = {
    "ModelTimeoutException", "ModelNotReadyException", "InternalServerException",
    "ServiceUnavailableException",
}

# The one validate() error that is handled by salvage rather than by failing
# the row outright — see _salvage_condition_conflict below.
CONDITION_CONFLICT_ERROR = "material_condition inconsistent with defects"

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_client = None
# Only ever flipped False, never back to True — under Celery prefork each
# worker process discovers this independently, which is race-free (mirrors
# mmvision.py's call_bedrock: a losing thread reading this after a winning
# thread already flipped it would otherwise retry with stale kwargs and
# fail a second time uncaught).
_supports_temperature = True


def _ensure_bearer_token_auth() -> None:
    """Alias BEDROCK_API_KEY (amenity/bedrock-api-key — the same bearer
    token bedrock_client.py already uses for its raw httpx calls) to
    AWS_BEARER_TOKEN_BEDROCK, the only name boto3 recognizes for bearer
    auth. This IS the production mechanism for lobby-check's Bedrock calls
    (product decision 2026-09-01) — NOT a local-dev-only fallback: lobby-
    check deliberately does not use the static AWS_ACCESS_KEY_ID/
    AWS_SECRET_ACCESS_KEY pair (amenity/aws-bedrock-keys) other Bedrock call
    sites in this backend rely on.

    Mutates os.environ for the whole process, which is safe here because
    celery-lobby-check-worker is its own dedicated container running only
    lobby_check_task.py's tasks — no other Bedrock call site executes in
    that process to be affected by the global env var.
    """
    if not os.environ.get("AWS_BEARER_TOKEN_BEDROCK") and settings.BEDROCK_API_KEY:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.BEDROCK_API_KEY


def _get_client():
    global _client
    if _client is None:
        _ensure_bearer_token_auth()
        import boto3
        import botocore.config

        _client = boto3.client(
            "bedrock-runtime",
            region_name=settings.BEDROCK_REGION,
            config=botocore.config.Config(
                # Explicit Celery-level retries instead (lobby_check_task.py)
                # so throttles stay visible and wall-clock stays bounded —
                # botocore's own adaptive retries would otherwise silently
                # multiply latency against the soft time limit.
                retries={"max_attempts": 0},
                read_timeout=settings.LOBBY_CHECK_TIMEOUT_SECONDS,
                connect_timeout=10,
            ),
        )
    return _client


def _messages(img: bytes, framing: str, extra_user_text: str = "") -> list[dict]:
    content: list[dict] = [
        {"image": {"format": "jpeg", "source": {"bytes": img}}},
        {"text": USER_TEXT + FRAMING_HINT.get(framing, "")},
    ]
    if extra_user_text:
        content.append({"text": extra_user_text})
    return [{"role": "user", "content": content}]


def _build_converse_kwargs(
    img: bytes, framing: str, model_id: str, use_temp: bool, extra_user_text: str = ""
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "modelId": model_id,
        "messages": _messages(img, framing, extra_user_text),
        "system": [{"text": SYSTEM_PROMPT + PROMPT_ONLY_SUFFIX}],
        "inferenceConfig": {"maxTokens": 1500},
    }
    if use_temp:
        kwargs["inferenceConfig"]["temperature"] = 0.0
    return kwargs


def _call_converse(img: bytes, framing: str, model_id: str, extra_user_text: str = "") -> dict:
    """One converse call, with mmvision.py's temperature-unsupported
    fallback retry (a real Bedrock behavior for some models)."""
    global _supports_temperature

    client = _get_client()
    use_temp = _supports_temperature
    kwargs = _build_converse_kwargs(img, framing, model_id, use_temp, extra_user_text)
    try:
        return client.converse(**kwargs)
    except Exception as exc:  # noqa: BLE001
        if "temperature" in str(exc).lower() and use_temp:
            _supports_temperature = False
            kwargs = _build_converse_kwargs(img, framing, model_id, False, extra_user_text)
            return client.converse(**kwargs)
        raise


def _error_code(exc: Exception) -> Optional[str]:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return response.get("Error", {}).get("Code")
    return None


def _reraise_classified(exc: Exception) -> None:
    """Raises the LobbyCheck*Error matching exc's failure mode, or
    re-raises exc itself unchanged if it isn't a recognized throttle/
    transient shape — the task layer treats that as deterministic
    (fail-fast, no retry), which is the safer default for an error shape
    this module doesn't recognize.
    """
    code = _error_code(exc)
    if code in _THROTTLE_ERROR_CODES:
        raise LobbyCheckThrottleError(str(exc)) from exc
    if code in _TRANSIENT_ERROR_CODES:
        raise LobbyCheckTransientError(str(exc)) from exc

    import botocore.exceptions

    if isinstance(
        exc,
        (
            botocore.exceptions.ConnectTimeoutError,
            botocore.exceptions.ReadTimeoutError,
            botocore.exceptions.EndpointConnectionError,
        ),
    ):
        raise LobbyCheckTransientError(str(exc)) from exc
    raise exc


def parse_response(resp: dict) -> dict:
    """Qwen is Bedrock's "prompt_only" backend (mmvision.py's model
    registry) — no native structured-output/tool-calling support, so the
    schema is enforced by prompt instruction + this parse, not a toolUse
    block."""
    blocks = resp["output"]["message"]["content"]
    text = "".join(b.get("text", "") for b in blocks).strip()
    if not text:
        raise ValueError("empty text response")
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = JSON_RE.search(text)
        if not m:
            raise ValueError(f"no JSON object found in: {text[:200]}")
        return json.loads(m.group(0))


def validate(rec: dict) -> list[str]:
    errs = []
    for k in FIELD_ORDER:
        if k not in rec:
            errs.append(f"missing key '{k}'")

    mt = rec.get("material_type")
    if mt is not None and mt not in MATERIAL_TYPES:
        errs.append(f"material_type '{mt}' is not one of the {len(MATERIAL_TYPES)} allowed values")

    d = rec.get("defects")
    if d is not None:
        if not isinstance(d, list):
            errs.append("defects must be an array")
        else:
            for x in d:
                if x not in DEFECTS:
                    errs.append(f"defect '{x}' is not an allowed value")

    q = rec.get("material_quantity")
    if q is not None and not isinstance(q, int):
        try:
            rec["material_quantity"] = int(q)
        except (TypeError, ValueError):
            errs.append(f"material_quantity '{q}' is not an integer")

    mc = rec.get("material_condition")
    if mc is not None and mc not in MATERIAL_CONDITIONS:
        errs.append(f"material_condition '{mc}' is not one of {MATERIAL_CONDITIONS}")
    elif mc is not None and isinstance(d, list):
        has_defects = len(d) > 0
        if (mc == "damaged") != has_defects:
            errs.append(CONDITION_CONFLICT_ERROR)

    return errs


def _salvage_condition_conflict(rec: dict) -> dict:
    """The model's material_condition still disagreed with its own defects
    list after the repair retry. `defects` is grounded in defect_evidence;
    material_condition is an unsourced summary judgment, so the grounded
    field wins: persist the defects-derived condition and clamp its
    confidence. The row still succeeds — a conflict is a low-confidence
    signal, not a failure. See docs/plans/2026-09-01-lobby-check-design.md §3.4.
    """
    rec = dict(rec)
    defects = rec.get("defects") or []
    rec["material_condition"] = "damaged" if defects else "good"
    try:
        emitted = float(rec.get("confidence_material_condition"))
    except (TypeError, ValueError):
        emitted = settings.LOBBY_CHECK_CONDITION_CONFLICT_CONFIDENCE_CAP
    rec["confidence_material_condition"] = min(
        emitted, settings.LOBBY_CHECK_CONDITION_CONFLICT_CONFIDENCE_CAP
    )
    return rec


def _decision(rec: dict) -> str:
    fields = (
        "confidence_movie_title", "confidence_material_type",
        "confidence_material_quantity", "confidence_material_condition",
    )
    try:
        values = [float(rec[f]) for f in fields if rec.get(f) is not None]
    except (TypeError, ValueError):
        return "REVIEW"
    if values and min(values) >= settings.LOBBY_CHECK_REVIEW_CONFIDENCE_THRESHOLD:
        return "AUTO_ACCEPT"
    return "REVIEW"


def extract_material_record(
    image_bytes: bytes,
    framing: str,
    *,
    usage_ctx: LlmCallContext,
    model_id: Optional[str] = None,
) -> ExtractionResult:
    """Extract one image's structured record. Raises LobbyCheckThrottleError/
    LobbyCheckTransientError (Celery-retryable) or LobbyCheckSchemaError /
    the original botocore exception (deterministic, fail-fast) on failure —
    never returns a half-populated result on failure. On success, returns an
    ExtractionResult with tokens/cost SUMMED across every attempt (both a
    primary call and a repair retry spend real tokens), while each attempt
    still gets its own LlmCallLog row via log_llm_call below.
    """
    model_id = model_id or settings.LOBBY_CHECK_MODEL_ID
    result = ExtractionResult(framing=framing)
    started = time.monotonic()

    def _emit(usage: TokenUsage, status: str, error_type: Optional[str],
               retry_count: int, decision: Optional[str] = None) -> None:
        try:
            log_llm_call(
                usage_ctx,
                model_id=model_id,
                usage=usage,
                latency_ms=int((time.monotonic() - started) * 1000),
                status=status,
                error_type=error_type,
                retry_count=retry_count,
                decision=decision,
            )
        except Exception as log_exc:  # noqa: BLE001 — observability must never break extraction
            logger.warning("lobby_check_llm_log_failed error=%s", log_exc)

    extra_user_text = ""
    for attempt in range(2):
        try:
            resp = _call_converse(image_bytes, framing, model_id, extra_user_text)
        except Exception as exc:  # noqa: BLE001
            _emit(TokenUsage(), STATUS_FAILURE, type(exc).__name__, attempt)
            result.latency_ms = int((time.monotonic() - started) * 1000)
            _reraise_classified(exc)

        # Usage is captured before any parsing can fail: the tokens were
        # spent whether or not the model's JSON is well-formed.
        usage = extract_converse_usage(resp)
        cost = estimate_cost_usd(
            model_id, usage.input_tokens, usage.output_tokens,
            usage.cache_read_tokens, usage.cache_write_tokens,
        )
        result.input_tokens += usage.input_tokens
        result.output_tokens += usage.output_tokens
        result.cost_usd += cost

        try:
            rec = parse_response(resp)
            errs = validate(rec)
        except Exception as exc:  # noqa: BLE001
            rec, errs = {}, [str(exc)]

        if not errs:
            _emit(usage, STATUS_SUCCESS, None, attempt, decision=_decision(rec))
            result.record = rec
            result.parse_retries = attempt
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result

        if attempt == 1 and errs == [CONDITION_CONFLICT_ERROR]:
            rec = _salvage_condition_conflict(rec)
            _emit(usage, STATUS_SUCCESS, None, attempt, decision=_decision(rec))
            result.record = rec
            result.parse_retries = attempt
            result.condition_conflict = True
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result

        _emit(usage, STATUS_SUCCESS, "SchemaValidationError", attempt)
        if attempt == 0:
            extra_user_text = (
                "\n\nYour previous response was rejected: " + "; ".join(errs)
                + ". Return a corrected JSON object only."
            )
            result.parse_retries = 1
            continue

        result.latency_ms = int((time.monotonic() - started) * 1000)
        raise LobbyCheckSchemaError("; ".join(errs))

    raise AssertionError("unreachable")  # the loop always returns or raises

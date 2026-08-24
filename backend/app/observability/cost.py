"""Cost calculation for a single LLM call, via litellm used purely as a
pricing *library* (design doc §4) — litellm's proxy is deliberately not run.

Cost is computed once here at ingest time and stored on LlmCallLog.cost_usd
(spec §5: "computed at write time, stored"), so no dashboard query ever has
to recompute pricing.

Hard contract: estimate_cost_usd NEVER raises. A model litellm doesn't know
about, an argument signature that changed between litellm versions, or any
other failure degrades to 0.0 plus a warning — a pricing gap must not become
a pipeline failure (spec §7).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_BEDROCK_PREFIX = "bedrock/"


def _cost_per_token(**kwargs):
    """Thin indirection over litellm.cost_per_token.

    Imported lazily inside the function so (a) importing this module never
    pays litellm's non-trivial import cost, and (b) tests can monkeypatch
    this single name instead of reaching into the litellm package.
    """
    import litellm

    return litellm.cost_per_token(**kwargs)


def candidate_model_names(model_id: str) -> list[str]:
    """Ordered litellm model names to try for a raw Bedrock model id.

    Bedrock ids in this codebase appear both bare
    ("us.anthropic.claude-sonnet-5", settings.AGENTIC_CLAUDE_MODEL) and as
    provider-scoped names litellm prefers ("bedrock/..."). Try the id exactly
    as recorded first — so the stored model_id and the priced model_id agree
    whenever possible — then the bedrock/-prefixed form.
    """
    if model_id.startswith(_BEDROCK_PREFIX):
        return [model_id]
    return [model_id, _BEDROCK_PREFIX + model_id]


def estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Return the USD cost of one call, or 0.0 if it cannot be determined.

    Cache-read/cache-write tokens are passed through opportunistically
    (spec §3) and silently dropped if the installed litellm doesn't accept
    those kwargs.
    """
    total_tokens = (
        (input_tokens or 0)
        + (output_tokens or 0)
        + (cache_read_tokens or 0)
        + (cache_write_tokens or 0)
    )
    if total_tokens <= 0:
        # Cache-hit rows and failed calls have no tokens and cost nothing;
        # skip litellm entirely rather than paying an import + lookup.
        return 0.0

    last_error: Exception | None = None
    for name in candidate_model_names(model_id):
        base = {
            "model": name,
            "prompt_tokens": input_tokens or 0,
            "completion_tokens": output_tokens or 0,
        }
        with_cache = dict(base)
        if cache_read_tokens:
            with_cache["cache_read_input_tokens"] = cache_read_tokens
        if cache_write_tokens:
            with_cache["cache_creation_input_tokens"] = cache_write_tokens

        attempts = [with_cache, base] if with_cache != base else [base]
        for kwargs in attempts:
            try:
                prompt_cost, completion_cost = _cost_per_token(**kwargs)
                return float(prompt_cost) + float(completion_cost)
            except TypeError as exc:
                # Signature mismatch (older litellm without cache kwargs) —
                # fall through to the no-cache-kwargs attempt.
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001 — unknown model, bad map, etc.
                last_error = exc
                break  # this candidate name is not priceable; try the next

    logger.warning(
        "usage_cost_unavailable model_id=%r input=%d output=%d error=%s",
        model_id, input_tokens or 0, output_tokens or 0, last_error,
    )
    return 0.0

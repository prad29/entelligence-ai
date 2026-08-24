"""Usage/cost extraction for the agentic CLI path (spec §7).

The Claude Code CLI is run by claude-sandbox with
`--output-format stream-json --verbose`, and emits one JSON object per line.
The terminal `{"type": "result", ...}` event carries both a `usage` block and
`total_cost_usd`. app/title_matching/agentic/result_parser.py reads only the
`result` string out of that event, so both numbers were previously thrown
away — this module recovers them from the same raw stdout.

`total_cost_usd` is treated as authoritative and handed to log_llm_call as
`cost_usd=`, bypassing our litellm token arithmetic: the CLI knows about
multi-turn tool loops, cache tiers and prompt-caching discounts that a single
input/output token pair cannot express.

Everything here is total. This runs inside run_agentic_match, in the request
path of a real title match, and spec §7 makes observability strictly
subordinate to the pipeline it observes: an unrecognised payload shape must
produce zeros, never an exception.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from app.observability.context import TokenUsage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgenticUsage:
    """Tokens plus the cost the CLI reported for itself.

    `cost_usd is None` means "the CLI didn't tell us" — log_llm_call then
    falls back to pricing the tokens via litellm.
    """

    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: Optional[float] = None


# TokenUsage field -> accepted keys inside the CLI's `usage` object, in
# precedence order. The first pair is the Messages API shape the CLI actually
# emits; the second is a defensive alias, so a renamed field degrades to a
# zero for that one counter instead of silently mis-attributing another.
_TOKEN_FIELDS: dict[str, tuple[str, ...]] = {
    "input_tokens": ("input_tokens", "prompt_tokens"),
    "output_tokens": ("output_tokens", "completion_tokens"),
    "cache_read_tokens": ("cache_read_input_tokens", "cache_read_tokens"),
    "cache_write_tokens": ("cache_creation_input_tokens", "cache_write_tokens"),
}


def _as_token_count(value: Any) -> int:
    """Non-negative int, or 0 for anything that isn't one."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return count if count > 0 else 0


def _as_cost(value: Any) -> Optional[float]:
    """Finite non-negative float, or None — None means 'not reported', which
    log_llm_call reads as 'price it from tokens instead'."""
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(cost) or math.isinf(cost) or cost < 0:
        return None
    return cost


def _result_events(stdout: str) -> Iterator[dict]:
    """Yield every well-formed `type == "result"` object in the stream."""
    for line in stdout.splitlines():
        line = line.strip()
        # Cheap prefilter: stream-json events are always top-level objects, so
        # anything else is CLI chatter and not worth a json.loads attempt.
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            yield obj


def extract_agentic_usage(stdout: Any) -> AgenticUsage:
    """Pull tokens and reported cost out of raw CLI stdout. Never raises."""
    try:
        if not isinstance(stdout, str) or not stdout.strip():
            return AgenticUsage()

        last_event: Optional[dict] = None
        for event in _result_events(stdout):
            last_event = event

        if last_event is None:
            return AgenticUsage()

        raw_usage = last_event.get("usage")
        if not isinstance(raw_usage, dict):
            raw_usage = {}

        counts: dict[str, int] = {}
        for field_name, aliases in _TOKEN_FIELDS.items():
            counts[field_name] = 0
            for alias in aliases:
                if alias in raw_usage:
                    counts[field_name] = _as_token_count(raw_usage[alias])
                    break

        return AgenticUsage(
            usage=TokenUsage(**counts),
            cost_usd=_as_cost(last_event.get("total_cost_usd")),
        )
    except Exception as exc:  # noqa: BLE001 — see module docstring / spec §7
        logger.warning("agentic_usage_extract_failed error=%s", exc)
        return AgenticUsage()

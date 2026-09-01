"""Phase 2 tests for app.lobby_check.extractor and the limits.py ordering
invariant. No real Bedrock calls — `extractor._get_client` is monkeypatched
to a fake client, so these run offline and for $0 (see
docs/plans/2026-09-01-lobby-check-design.md §7.1, items 4/5/7/11).
"""

from __future__ import annotations

import json

import pytest

from app.lobby_check import extractor, limits
from app.lobby_check.errors import (
    LobbyCheckSchemaError,
    LobbyCheckThrottleError,
    LobbyCheckTransientError,
)
from app.observability.bedrock_usage import extract_converse_usage
from app.observability.context import LlmCallContext

CTX = LlmCallContext(task_type="lobby_check", call_path="bedrock_converse")


def _valid_record(**overrides) -> dict:
    rec = {
        "visual_notes": "wall-mounted glass case, title read from billing block",
        "material_type": "One Sheet",
        "confidence_material_type": 0.95,
        "movie_title": "Inception",
        "confidence_movie_title": 0.9,
        "material_quantity": 1,
        "confidence_material_quantity": 0.85,
        "defects": [],
        "defect_evidence": "",
        "material_condition": "good",
        "confidence_material_condition": 0.9,
    }
    rec.update(overrides)
    return rec


def _converse_response(record: dict, input_tokens=1500, output_tokens=400) -> dict:
    return {
        "output": {"message": {"content": [{"text": json.dumps(record)}]}},
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


class _FakeClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeClient:
    def __init__(self, side_effects):
        self._side_effects = list(side_effects)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        effect = self._side_effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


@pytest.fixture(autouse=True)
def _no_real_log_llm_call(monkeypatch):
    """log_llm_call opens a real DB Session — stub it out for every test in
    this module except the one that specifically wants to assert on
    failures propagating from it."""
    calls = []
    monkeypatch.setattr(extractor, "log_llm_call", lambda ctx, **kw: calls.append(kw))
    return calls


def _use_fake_client(monkeypatch, *side_effects):
    client = _FakeClient(side_effects)
    monkeypatch.setattr(extractor, "_get_client", lambda: client)
    return client


# --- parse_response -----------------------------------------------------------

def test_parse_response_bare_json():
    rec = _valid_record()
    resp = _converse_response(rec)
    assert extractor.parse_response(resp) == rec


def test_parse_response_fenced_json():
    rec = _valid_record()
    resp = {"output": {"message": {"content": [{"text": "```json\n" + json.dumps(rec) + "\n```"}]}}}
    assert extractor.parse_response(resp) == rec


def test_parse_response_json_with_leading_prose():
    rec = _valid_record()
    resp = {"output": {"message": {"content": [{"text": "Sure, here you go: " + json.dumps(rec)}]}}}
    assert extractor.parse_response(resp) == rec


def test_parse_response_empty_text_raises():
    resp = {"output": {"message": {"content": [{"text": ""}]}}}
    with pytest.raises(ValueError, match="empty text"):
        extractor.parse_response(resp)


def test_parse_response_no_json_raises():
    resp = {"output": {"message": {"content": [{"text": "no json here at all"}]}}}
    with pytest.raises(ValueError, match="no JSON object"):
        extractor.parse_response(resp)


# --- validate ------------------------------------------------------------------

def test_validate_all_material_types_round_trip():
    from app.lobby_check.taxonomy import MATERIAL_TYPES

    for mt in MATERIAL_TYPES:
        assert extractor.validate(_valid_record(material_type=mt)) == []


def test_validate_all_defects_round_trip():
    from app.lobby_check.taxonomy import DEFECTS

    for d in DEFECTS:
        rec = _valid_record(defects=[d], material_condition="damaged")
        assert extractor.validate(rec) == []


def test_validate_off_enum_material_type():
    errs = extractor.validate(_valid_record(material_type="Not A Real Type"))
    assert any("material_type" in e for e in errs)


def test_validate_quantity_coerces_string_int():
    rec = _valid_record(material_quantity="3")
    assert extractor.validate(rec) == []
    assert rec["material_quantity"] == 3


def test_validate_quantity_rejects_non_numeric_string():
    errs = extractor.validate(_valid_record(material_quantity="three"))
    assert any("material_quantity" in e for e in errs)


def test_validate_missing_key():
    rec = _valid_record()
    del rec["visual_notes"]
    errs = extractor.validate(rec)
    assert "missing key 'visual_notes'" in errs


def test_validate_condition_consistent_good():
    assert extractor.validate(_valid_record(defects=[], material_condition="good")) == []


def test_validate_condition_consistent_damaged():
    rec = _valid_record(defects=["tear"], material_condition="damaged")
    assert extractor.validate(rec) == []


def test_validate_condition_conflict_damaged_without_defects():
    rec = _valid_record(defects=[], material_condition="damaged")
    assert extractor.validate(rec) == [extractor.CONDITION_CONFLICT_ERROR]


def test_validate_condition_conflict_good_with_defects():
    rec = _valid_record(defects=["tear"], material_condition="good")
    assert extractor.validate(rec) == [extractor.CONDITION_CONFLICT_ERROR]


# --- extract_material_record: success / repair retry / schema failure --------

def test_extract_success_first_attempt(monkeypatch, _no_real_log_llm_call):
    rec = _valid_record()
    _use_fake_client(monkeypatch, _converse_response(rec))

    result = extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)

    assert result.record == rec
    assert result.parse_retries == 0
    assert result.input_tokens == 1500
    assert result.output_tokens == 400
    assert result.cost_usd > 0
    assert len(_no_real_log_llm_call) == 1
    assert _no_real_log_llm_call[0]["status"] == "success"
    assert _no_real_log_llm_call[0]["decision"] == "AUTO_ACCEPT"


def test_extract_repair_retry_then_valid(monkeypatch, _no_real_log_llm_call):
    bad_resp = _converse_response({"material_type": "One Sheet"})  # missing required keys
    good_rec = _valid_record()
    client = _use_fake_client(monkeypatch, bad_resp, _converse_response(good_rec))

    result = extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)

    assert result.record == good_rec
    assert result.parse_retries == 1
    # tokens summed across BOTH attempts
    assert result.input_tokens == 1500 * 2
    assert result.output_tokens == 400 * 2
    assert len(_no_real_log_llm_call) == 2  # one LlmCallLog row per attempt
    assert _no_real_log_llm_call[0]["error_type"] == "SchemaValidationError"
    assert _no_real_log_llm_call[1]["status"] == "success"
    # second call's message contains the rejection text
    second_call_text = client.calls[1]["messages"][0]["content"][-1]["text"]
    assert "Your previous response was rejected" in second_call_text


def test_extract_repair_retry_still_invalid_raises_schema_error(monkeypatch, _no_real_log_llm_call):
    bad_resp = _converse_response({"material_type": "One Sheet"})
    _use_fake_client(monkeypatch, bad_resp, bad_resp)

    with pytest.raises(LobbyCheckSchemaError):
        extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)

    # both attempts' tokens/log rows still accounted even though the row fails
    assert len(_no_real_log_llm_call) == 2


def test_extract_condition_conflict_salvaged_on_final_attempt(monkeypatch, _no_real_log_llm_call):
    conflicted = _valid_record(defects=["tear"], material_condition="good", confidence_material_condition=0.9)
    # first attempt: some OTHER error so the repair retry actually happens;
    # second attempt: only the condition/defects conflict remains.
    bad_resp = _converse_response({"material_type": "One Sheet"})
    _use_fake_client(monkeypatch, bad_resp, _converse_response(conflicted))

    result = extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)

    assert result.condition_conflict is True
    assert result.record["material_condition"] == "damaged"  # defects-derived, not the model's "good"
    assert result.record["confidence_material_condition"] == pytest.approx(0.5)  # capped
    assert _no_real_log_llm_call[1]["status"] == "success"


def test_extract_log_llm_call_failure_does_not_break_return(monkeypatch):
    rec = _valid_record()
    _use_fake_client(monkeypatch, _converse_response(rec))
    monkeypatch.setattr(extractor, "log_llm_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))

    result = extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)
    assert result.record == rec


# --- retry classification ------------------------------------------------------

def test_throttle_error_classified(monkeypatch, _no_real_log_llm_call):
    _use_fake_client(monkeypatch, _FakeClientError("ThrottlingException"))
    with pytest.raises(LobbyCheckThrottleError):
        extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)


def test_too_many_requests_classified_as_throttle(monkeypatch, _no_real_log_llm_call):
    _use_fake_client(monkeypatch, _FakeClientError("TooManyRequestsException"))
    with pytest.raises(LobbyCheckThrottleError):
        extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)


def test_model_timeout_classified_as_transient(monkeypatch, _no_real_log_llm_call):
    _use_fake_client(monkeypatch, _FakeClientError("ModelTimeoutException"))
    with pytest.raises(LobbyCheckTransientError):
        extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)


def test_deterministic_error_passes_through_unclassified(monkeypatch, _no_real_log_llm_call):
    """ValidationException/AccessDeniedException aren't in either retry set —
    they must propagate AS-IS (not wrapped), so the Celery task layer's
    "not a LobbyCheck{Throttle,Transient}Error" fail-fast branch catches them."""
    exc = _FakeClientError("ValidationException")
    _use_fake_client(monkeypatch, exc)
    with pytest.raises(_FakeClientError):
        extractor.extract_material_record(b"img", "wide", usage_ctx=CTX)


# --- extract_converse_usage ----------------------------------------------------

def test_extract_converse_usage_real_shape():
    usage = extract_converse_usage({
        "usage": {
            "inputTokens": 1500, "outputTokens": 400,
            "cacheReadInputTokens": 10, "cacheWriteInputTokens": 5,
        }
    })
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (1500, 400, 10, 5)


def test_extract_converse_usage_empty_dict():
    usage = extract_converse_usage({})
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


def test_extract_converse_usage_none_usage_block():
    usage = extract_converse_usage({"usage": None})
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


def test_extract_converse_usage_string_tokens_coerced():
    usage = extract_converse_usage({"usage": {"inputTokens": "1500", "outputTokens": "400"}})
    assert (usage.input_tokens, usage.output_tokens) == (1500, 400)


def test_extract_converse_usage_none_response_never_raises():
    usage = extract_converse_usage(None)
    assert (usage.input_tokens, usage.output_tokens) == (0, 0)


# --- limits.py ordering invariant ----------------------------------------------

def test_limits_ordering_invariant():
    assert limits.row_time_limit() > limits.row_soft_time_limit() > extractor.settings.LOBBY_CHECK_TIMEOUT_SECONDS

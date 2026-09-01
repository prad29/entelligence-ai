"""
Phase 1 tests for the lobby-check core: pure schema/prompt modules only, no
DB/Celery/Bedrock involved (see docs/plans/2026-09-01-lobby-check-design.md
§7.1, items 1-3 plus the prompt-coverage guard).
"""

from __future__ import annotations

import json

import pytest

from app.lobby_check.prompt import (
    EXTRACTION_SCHEMA,
    FIELD_ORDER,
    PROMPT_ONLY_SUFFIX,
    prompt_hash,
)
from app.lobby_check.schemas import (
    LobbyCheckImageInput,
    LobbyCheckRequest,
    serialize_job_status,
    serialize_row_result,
    validate_batch_size,
)
from app.lobby_check.taxonomy import DEFECTS, MATERIAL_CONDITIONS, MATERIAL_TYPES

VALID_URL = "https://mm-intelligence.s3.amazonaws.com/lobby/1787248984204.jpg"
VALID_PHOTO_ID = 678294


# --- prompt-coverage guard --------------------------------------------------

def test_prompt_only_suffix_mentions_every_field():
    for field_name in FIELD_ORDER:
        assert field_name in PROMPT_ONLY_SUFFIX, f"missing field {field_name!r}"


def test_prompt_only_suffix_mentions_every_material_type():
    for value in MATERIAL_TYPES:
        assert value in PROMPT_ONLY_SUFFIX


def test_prompt_only_suffix_mentions_every_defect():
    for value in DEFECTS:
        assert value in PROMPT_ONLY_SUFFIX


def test_prompt_only_suffix_mentions_every_condition():
    for value in MATERIAL_CONDITIONS:
        assert value in PROMPT_ONLY_SUFFIX


def test_field_order_matches_schema_required():
    assert FIELD_ORDER == EXTRACTION_SCHEMA["required"]


def test_prompt_hash_is_stable():
    assert prompt_hash() == prompt_hash()
    assert len(prompt_hash()) == 12


def test_prompt_hash_changes_with_schema():
    import copy
    import hashlib

    from app.lobby_check.prompt import SYSTEM_PROMPT

    mutated = copy.deepcopy(EXTRACTION_SCHEMA)
    mutated["properties"]["extra_field"] = {"type": "string"}
    mutated_blob = SYSTEM_PROMPT + json.dumps(mutated, sort_keys=True) + PROMPT_ONLY_SUFFIX
    mutated_hash = hashlib.sha256(mutated_blob.encode()).hexdigest()[:12]

    assert mutated_hash != prompt_hash()


# --- LobbyCheckImageInput / LobbyCheckRequest -------------------------------

def test_valid_image_input():
    img = LobbyCheckImageInput(photo_id=VALID_PHOTO_ID, image_url=VALID_URL)
    assert img.photo_id == VALID_PHOTO_ID


def test_non_numeric_photo_id_rejected():
    with pytest.raises(ValueError):
        LobbyCheckImageInput(photo_id="not-a-number", image_url=VALID_URL)


def test_non_https_url_rejected():
    with pytest.raises(ValueError, match="https URL"):
        LobbyCheckImageInput(
            photo_id=VALID_PHOTO_ID,
            image_url="http://mm-intelligence.s3.amazonaws.com/lobby/x.jpg",
        )


def test_disallowed_host_rejected():
    with pytest.raises(ValueError, match="not allow-listed"):
        LobbyCheckImageInput(
            photo_id=VALID_PHOTO_ID,
            image_url="https://evil.example.com/lobby/x.jpg",
        )


def test_duplicate_photo_id_rejected():
    with pytest.raises(ValueError, match="Duplicate photo_id"):
        LobbyCheckRequest(
            images=[
                LobbyCheckImageInput(photo_id=VALID_PHOTO_ID, image_url=VALID_URL),
                LobbyCheckImageInput(photo_id=VALID_PHOTO_ID, image_url=VALID_URL),
            ]
        )


def test_empty_images_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        LobbyCheckRequest(images=[])


def test_validate_batch_size_under_cap():
    images = [LobbyCheckImageInput(photo_id=VALID_PHOTO_ID, image_url=VALID_URL)]
    assert validate_batch_size(images, max_rows=10) == []


def test_validate_batch_size_over_cap():
    images = [
        LobbyCheckImageInput(photo_id=VALID_PHOTO_ID, image_url=VALID_URL),
        LobbyCheckImageInput(photo_id=VALID_PHOTO_ID + 1, image_url=VALID_URL),
    ]
    errors = validate_batch_size(images, max_rows=1)
    assert len(errors) == 1
    assert "exceeding this key's limit of 1" in errors[0].message


# --- serialize_row_result / serialize_job_status ----------------------------

class _FakeRow:
    def __init__(self, **kwargs):
        defaults = dict(
            photo_id=VALID_PHOTO_ID,
            status="completed",
            input_json=json.dumps({"a": 1}),
            movie_title="Inception",
            confidence_movie_title=0.9,
            material_type="One Sheet",
            confidence_material_type=0.95,
            material_quantity=1,
            confidence_material_quantity=0.8,
            material_condition="good",
            confidence_material_condition=0.85,
            visual_notes="wall-mounted glass case",
            defects_json="[]",
            defect_evidence="",
            condition_conflict=False,
            error=None,
        )
        defaults.update(kwargs)
        self.__dict__.update(defaults)


def test_serialize_row_result_renames_boundary_fields():
    row = _FakeRow()
    out = serialize_row_result(row, review_threshold=0.7)
    assert out["ai_reasoning"] == "wall-mounted glass case"
    assert out["defects"] == []
    assert out["needs_review"] is False


def test_serialize_row_result_needs_review_below_threshold():
    row = _FakeRow(confidence_material_condition=0.5)
    out = serialize_row_result(row, review_threshold=0.7)
    assert out["needs_review"] is True


def test_serialize_row_result_failed_row_no_confidences():
    row = _FakeRow(
        status="failed",
        movie_title=None,
        confidence_movie_title=None,
        material_type=None,
        confidence_material_type=None,
        material_quantity=None,
        confidence_material_quantity=None,
        material_condition=None,
        confidence_material_condition=None,
        visual_notes=None,
        defects_json=None,
        defect_evidence=None,
        error="ImageFetchError: 404",
    )
    out = serialize_row_result(row, review_threshold=0.7)
    assert out["status"] == "failed"
    assert out["needs_review"] is False
    assert out["error"] == "ImageFetchError: 404"
    assert out["defects"] == []


def test_serialize_row_result_condition_conflict_flag():
    row = _FakeRow(condition_conflict=True)
    out = serialize_row_result(row, review_threshold=0.7)
    assert out["condition_conflict"] is True


class _FakeJob:
    def __init__(self, **kwargs):
        defaults = dict(
            id="job-1",
            phase="processing",
            rows_total=10,
            rows_processed=4,
            rows_succeeded=3,
            rows_failed=1,
            rows_needs_review=1,
            started_at=None,
            completed_at=None,
            error=None,
        )
        defaults.update(kwargs)
        self.__dict__.update(defaults)


def test_serialize_job_status_progress_pct():
    job = _FakeJob()
    out = serialize_job_status(job)
    assert out["progress_pct"] == 40.0
    assert "started_at" not in out
    assert "error" not in out


def test_serialize_job_status_zero_rows_total():
    job = _FakeJob(rows_total=0, rows_processed=0)
    out = serialize_job_status(job)
    assert out["progress_pct"] == 0.0

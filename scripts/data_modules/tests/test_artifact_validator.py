#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.artifact_validator import (  # noqa: E402
    ARTIFACT_SCHEMAS,
    ERROR_BLOCKING_REVIEW,
    ERROR_MISSED_OUTLINE_NODE,
    ERROR_MISSING,
    ERROR_PENDING_DISAMBIGUATION,
    ERROR_PROJECTION_FAILURE,
    ERROR_PROJECTION_INCOMPLETE,
    ERROR_SCHEMA,
    validate_chapter_commit,
    validate_commit_artifact_files,
    validate_disambiguation_result,
    validate_extraction_result,
    validate_fulfillment_result,
    validate_review_result,
)
from data_modules.chapter_commit_schema import (  # noqa: E402
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
)
from data_modules.chapter_content_binding import (  # noqa: E402
    SCHEMA_VERSION as CHAPTER_BINDING_SCHEMA_VERSION,
)


def _dummy_binding(chapter: int = 1) -> dict:
    """Return a schema-valid binding for payload-only validator tests."""
    return {
        "schema_version": CHAPTER_BINDING_SCHEMA_VERSION,
        "chapter": chapter,
        "path": f"正文/第{chapter:04d}章.md",
        "sha256": "0" * 64,
        "bytes": 1,
    }


def _with_binding(payload: dict, *, chapter: int = 1) -> dict:
    return {**payload, "chapter_binding": _dummy_binding(chapter)}


def _commit_envelope(*, chapter: int = 1, projection_status: dict | None = None) -> dict:
    binding = _dummy_binding(chapter)
    return {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": chapter,
            "status": "accepted",
        },
        "chapter_binding": dict(binding),
        "provenance": {"chapter_binding": dict(binding)},
        "review_result": _with_binding({"blocking_count": 0}, chapter=chapter),
        "fulfillment_result": _with_binding(
            {
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            chapter=chapter,
        ),
        "disambiguation_result": _with_binding({"pending": []}, chapter=chapter),
        "extraction_result": _with_binding(
            {
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "summary_text": "摘要",
            },
            chapter=chapter,
        ),
        "projection_status": projection_status or {},
    }


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_artifact_validator_uses_chapter_commit_schema_as_authority():
    assert ARTIFACT_SCHEMAS["review_result"] is ReviewResult
    assert ARTIFACT_SCHEMAS["fulfillment_result"] is FulfillmentResult
    assert ARTIFACT_SCHEMAS["disambiguation_result"] is DisambiguationResult
    assert ARTIFACT_SCHEMAS["extraction_result"] is ExtractionResult


def test_artifact_validator_reports_missing_artifact(tmp_path):
    report = validate_review_result(tmp_path / "missing.json")

    assert report["ok"] is False
    assert report["errors"][0]["type"] == ERROR_MISSING


def test_artifact_validator_reports_schema_errors_for_wrapped_payloads(tmp_path):
    path = _write_json(
        tmp_path / "fulfillment_result.json",
        _with_binding({"fulfillment": {"missed_nodes": []}}),
    )

    report = validate_fulfillment_result(path)

    assert report["ok"] is False
    assert report["errors"][0]["type"] == ERROR_SCHEMA
    assert "nested under fulfillment" in report["errors"][0]["message"]


def test_artifact_validator_reports_policy_blockers(tmp_path):
    review = _write_json(
        tmp_path / "review_results.json",
        _with_binding({"blocking_count": 1}),
    )
    fulfillment = _write_json(
        tmp_path / "fulfillment_result.json",
        _with_binding({
            "planned_nodes": ["A"],
            "covered_nodes": [],
            "missed_nodes": ["A"],
            "extra_nodes": [],
        }),
    )
    disambiguation = _write_json(
        tmp_path / "disambiguation_result.json",
        _with_binding({"pending": [{"mention": "宗主"}]}),
    )

    assert validate_review_result(review)["errors"][0]["type"] == ERROR_BLOCKING_REVIEW
    assert validate_fulfillment_result(fulfillment)["errors"][0]["type"] == ERROR_MISSED_OUTLINE_NODE
    assert validate_disambiguation_result(disambiguation)["errors"][0]["type"] == ERROR_PENDING_DISAMBIGUATION


def test_artifact_validator_accepts_valid_extraction(tmp_path):
    path = _write_json(
        tmp_path / "extraction_result.json",
        _with_binding({
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "entities_appeared": [],
            "scenes": [],
            "summary_text": "摘要",
        }),
    )

    report = validate_extraction_result(path)

    assert report["ok"] is True
    assert report["payload"]["summary_text"] == "摘要"


def test_validate_commit_artifact_files_merges_reports(tmp_path):
    review = _write_json(
        tmp_path / "review_results.json",
        _with_binding({"blocking_count": 0}),
    )
    fulfillment = _write_json(
        tmp_path / "fulfillment_result.json",
        _with_binding(
            {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []}
        ),
    )
    disambiguation = _write_json(
        tmp_path / "disambiguation_result.json",
        _with_binding({"pending": []}),
    )
    extraction = _write_json(
        tmp_path / "extraction_result.json",
        _with_binding(
            {
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "summary_text": "摘要",
            }
        ),
    )

    report = validate_commit_artifact_files(
        review_result=review,
        fulfillment_result=fulfillment,
        disambiguation_result=disambiguation,
        extraction_result=extraction,
    )

    assert report["ok"] is True
    assert set(report["payloads"]) == {
        "review_result",
        "fulfillment_result",
        "disambiguation_result",
        "extraction_result",
    }


def test_validate_chapter_commit_reports_projection_failure(tmp_path):
    commit = _write_json(
        tmp_path / "chapter_001.commit.json",
        _commit_envelope(
            projection_status={"state": "done", "index": "failed:locked"}
        ),
    )

    report = validate_chapter_commit(commit)

    assert report["ok"] is False
    assert any(item["type"] == ERROR_PROJECTION_FAILURE for item in report["errors"])


def test_validate_chapter_commit_requires_all_projection_writers(tmp_path):
    commit = _write_json(
        tmp_path / "chapter_001.commit.json",
        _commit_envelope(
            projection_status={
                "state": "done",
                "index": "done",
                "summary": "done",
                "memory": "skipped",
            }
        ),
    )

    report = validate_chapter_commit(commit)

    assert report["ok"] is False
    incomplete = [
        item for item in report["errors"] if item["type"] == ERROR_PROJECTION_INCOMPLETE
    ]
    assert incomplete
    assert any("vector" in item["message"] for item in incomplete)


def test_validate_chapter_commit_rejects_mismatched_binding_envelope(tmp_path):
    payload = _commit_envelope(
        projection_status={
            "state": "done",
            "index": "done",
            "summary": "done",
            "memory": "done",
            "vector": "done",
        }
    )
    payload["extraction_result"]["chapter_binding"]["sha256"] = "f" * 64
    commit = _write_json(tmp_path / "chapter_001.commit.json", payload)

    report = validate_chapter_commit(commit, expected_chapter=1)

    assert report["ok"] is False
    assert any(
        item["type"] == ERROR_SCHEMA
        and "extraction_result.chapter_binding" in item["message"]
        for item in report["errors"]
    )


def test_validate_chapter_commit_checks_expected_chapter(tmp_path):
    commit = _write_json(
        tmp_path / "chapter_002.commit.json",
        _commit_envelope(
            chapter=1,
            projection_status={
                "state": "done",
                "index": "done",
                "summary": "done",
                "memory": "done",
                "vector": "done",
            },
        ),
    )

    report = validate_chapter_commit(commit, expected_chapter=2)

    assert report["ok"] is False
    assert any(
        item.get("field") == "meta.chapter"
        and "与预期章节 2 不一致" in item["message"]
        for item in report["errors"]
    )


def test_artifact_validator_rejects_missing_required_top_level_fields(tmp_path):
    """precommit 负向用例：缺关键顶层字段时 runtime validator 必须拦截。

    取代已退役的 test_webnovel_write_data_agent_prompt_requires_extraction_schema
    （plan §12.2）：字段保障由 runtime schema 强制，而非主 Skill 文案锚定。
    """
    # fulfillment_result 缺 missed_nodes
    fulfillment = _write_json(
        tmp_path / "fulfillment_result.json",
        _with_binding({"planned_nodes": [], "covered_nodes": [], "extra_nodes": []}),
    )
    report = validate_fulfillment_result(fulfillment)
    assert report["ok"] is False
    assert report["errors"][0]["type"] == ERROR_SCHEMA
    assert "missed_nodes" in report["errors"][0]["message"]

    # disambiguation_result 缺 pending
    disambiguation = _write_json(
        tmp_path / "disambiguation_result.json",
        _with_binding({}),
    )
    report = validate_disambiguation_result(disambiguation)
    assert report["ok"] is False
    assert report["errors"][0]["type"] == ERROR_SCHEMA
    assert "pending" in report["errors"][0]["message"]

    # extraction_result 缺核心字段 accepted_events
    extraction = _write_json(
        tmp_path / "extraction_result.json",
        _with_binding({"state_deltas": [], "entity_deltas": []}),
    )
    report = validate_extraction_result(extraction)
    assert report["ok"] is False
    assert report["errors"][0]["type"] == ERROR_SCHEMA
    assert "accepted_events" in report["errors"][0]["message"]

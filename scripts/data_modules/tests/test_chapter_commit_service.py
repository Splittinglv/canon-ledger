#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import (
    ChapterBindingError,
    build_chapter_binding,
)
from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager
from .review_test_helpers import standard_review


_ARTIFACT_KEYS = (
    "review_result",
    "fulfillment_result",
    "disambiguation_result",
    "extraction_result",
)


def _chapter_binding(project_root, chapter, content=None):
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    if not chapter_path.exists():
        chapter_path.write_text(
            content or f"第{chapter}章最终正文\n",
            encoding="utf-8",
        )
    return build_chapter_binding(project_root, chapter)


def _build_commit(service, project_root, **kwargs):
    binding = _chapter_binding(project_root, int(kwargs["chapter"]))
    bound = dict(kwargs)
    story_root = project_root / ".story-system"
    outline_root = project_root / "大纲"
    contract_path = story_root / "chapters" / f"chapter_{int(kwargs['chapter']):03d}.json"
    if not story_root.exists() and not outline_root.exists():
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        planned = list((kwargs.get("fulfillment_result") or {}).get("planned_nodes") or [])
        contract_path.write_text(
            json.dumps(
                {
                    "meta": {"chapter": int(kwargs["chapter"])},
                    "chapter_directive": {
                        "goal": "验证当前章节提交主链",
                        "must_cover_nodes": planned,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    for key in _ARTIFACT_KEYS:
        artifact = bound.get(key)
        if isinstance(artifact, dict):
            artifact = dict(artifact)
            if key == "review_result" and "blocking_count" in artifact:
                artifact = standard_review(
                    binding,
                    blocking_count=int(artifact.get("blocking_count") or 0),
                )
            artifact.setdefault("chapter_binding", dict(binding))
            bound[key] = artifact
    return service.build_commit(**bound)


def test_commit_service_treats_missed_nodes_as_advisory_by_default(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": [],
            "missed_nodes": ["发现陷阱"],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"


def test_commit_service_rejects_missed_nodes_in_explicit_strict_mode(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": [],
            "missed_nodes": ["发现陷阱"],
            "extra_nodes": [],
            "enforcement": "strict",
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )

    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_ordinary_pending_but_excludes_candidate_fact(
    tmp_path,
):
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("爱丽丝抵达北城。", encoding="utf-8")
    service = ChapterCommitService(tmp_path)
    pending = [
        {
            "decision_id": "alice-location-check",
            "category": "presence",
            "candidate_event_id": "alice-location",
            "reason": "这是否是实际抵达需要作者确认",
        }
    ]
    event = {
        "event_id": "alice-location",
        "chapter": 3,
        "sequence": 1,
        "event_type": "presence_observed",
        "subject": "alice",
        "payload": {
            "location_id": "north-city",
            "presence_kind": "physical",
            "evidence_quote": "爱丽丝抵达北城。",
        },
    }
    extraction = {
        "state_deltas": [],
        "entity_deltas": [],
        "accepted_events": [event],
        "fact_coverage": {
            "knowledge": "complete",
            "presence": "complete",
            "custody": "complete",
        },
        "fact_verification": {
            "knowledge": "supported",
            "presence": "supported",
            "custody": "supported",
        },
    }
    fulfillment = {
        "planned_nodes": [],
        "covered_nodes": [],
        "missed_nodes": [],
        "extra_nodes": [],
    }

    first = _build_commit(
        service,
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result=fulfillment,
        disambiguation_result={"pending": pending},
        extraction_result=extraction,
    )

    assert first["meta"]["status"] == "accepted"
    assert first["extraction_result"]["accepted_events"] == []
    assert first["extraction_result"]["fact_coverage"]["presence"] == "partial"
    assert first["extraction_result"]["fact_verification"]["presence"] == "pending"
    assert first["provenance"]["human_review"]["unresolved_count"] == 1

    from data_modules.human_review import HumanReviewService

    HumanReviewService(tmp_path).record(
        {
            "decisions": [
                {
                    "decision_id": "alice-location-check",
                    "action": "confirm",
                }
            ]
        }
    )
    second = _build_commit(
        service,
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result=fulfillment,
        disambiguation_result={"pending": pending},
        extraction_result=extraction,
    )

    assert second["meta"]["status"] == "accepted"
    assert second["disambiguation_result"]["pending"] == []
    assert second["extraction_result"]["accepted_events"][0]["verification"] == (
        "verified"
    )
    assert second["provenance"]["human_review"]["verified_event_ids"] == [
        "alice-location"
    ]


def test_commit_service_rejects_only_explicit_blocking_pending(tmp_path):
    payload = _build_commit(
        ChapterCommitService(tmp_path),
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={
            "pending": [
                {
                    "decision_id": "unsafe-missing-owner",
                    "category": "custody",
                    "reason": "当前持有人缺失，无法安全写入",
                    "blocking": True,
                }
            ]
        },
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )

    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_when_all_checks_pass(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"
    assert payload["contract_refs"]["master"] == "MASTER_SETTING.json"
    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["contract_refs"]["chapter"] == "chapter_003.json"
    assert payload["outline_snapshot"]["covered_nodes"] == ["发现陷阱"]
    assert payload["extraction_result"]["accepted_events"] == []
    assert "accepted_events" not in payload
    assert "state_deltas" not in payload
    assert "entity_deltas" not in payload


def test_commit_service_downgrades_model_claimed_dimension_verification(tmp_path):
    payload = _build_commit(
        ChapterCommitService(tmp_path),
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
            "fact_coverage": {
                "knowledge": "complete",
                "presence": "complete",
                "custody": "complete",
            },
            "fact_verification": {
                "knowledge": "verified",
                "presence": "verified",
                "custody": "verified",
            },
        },
    )

    assert payload["extraction_result"]["fact_verification"] == {
        "knowledge": "supported",
        "presence": "supported",
        "custody": "supported",
    }


def test_commit_service_rejects_world_rule_without_matching_prose_evidence(tmp_path):
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("港城守卫宣读了一条没有具体内容的宵禁条例。", encoding="utf-8")
    service = ChapterCommitService(tmp_path)

    # 证据不在正文的事件在 build_commit 阶段（提交落盘前）就被拒绝，
    # 不再等到投影期的 world_rule_evidence_untrusted 兜底。
    with pytest.raises(
        ValueError, match=r"accepted_events\[0\]\.payload\.evidence_quote"
    ):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [
                    {
                        "event_id": "未经正文证实的规则",
                        "chapter": 3,
                        "event_type": "world_rule_revealed",
                        "subject": "港城",
                        "payload": {
                            "rule_content": "宵禁后不得点燃蓝灯",
                            "rule_category": "制度",
                            "domain": "港城",
                            "field": "宵禁照明限制",
                            "evidence_quote": "港城：宵禁后不得点燃蓝灯",
                        },
                    }
                ],
            },
        )


def test_commit_service_rejects_long_term_event_without_matching_prose_evidence(tmp_path):
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("守门人没有透露密门的位置。", encoding="utf-8")
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="is not present in the bound chapter"):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [
                    {
                        "event_id": "false-secret",
                        "chapter": 3,
                        "sequence": 1,
                        "event_type": "knowledge_state_changed",
                        "subject": "linzhou",
                        "payload": {
                            "information_id": "clocktower-secret-door",
                            "content": "密门在钟楼下",
                            "state": "known",
                            "source_kind": "told",
                            "source_entity": "keeper",
                            "evidence_quote": "守门人告诉林舟：密门在钟楼下。",
                        },
                    }
                ],
            },
        )


def test_commit_service_rejects_inconsistent_custody_chain(tmp_path):
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(
        "爱丽丝把铜钥匙交给鲍勃。\n爱丽丝又把铜钥匙交给卡萝。",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="custody_transition_conflict"):
        _build_commit(
            ChapterCommitService(tmp_path),
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [
                    {
                        "event_id": "key-to-bob",
                        "chapter": 3,
                        "sequence": 1,
                        "event_type": "custody_changed",
                        "subject": "bronze-key",
                        "payload": {
                            "from_holder": "alice",
                            "to_holder": "bob",
                            "evidence_quote": "爱丽丝把铜钥匙交给鲍勃。",
                        },
                    },
                    {
                        "event_id": "key-to-carol",
                        "chapter": 3,
                        "sequence": 2,
                        "event_type": "custody_changed",
                        "subject": "bronze-key",
                        "payload": {
                            "from_holder": "alice",
                            "to_holder": "carol",
                            "evidence_quote": "爱丽丝又把铜钥匙交给卡萝。",
                        },
                    },
                ],
            },
        )


def test_commit_service_rejects_empty_fulfillment_for_authoritative_nodes(tmp_path):
    contract_path = tmp_path / ".story-system" / "chapters" / "chapter_003.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "meta": {"chapter": 3},
                "chapter_directive": {
                    "goal": "确认封蜡缺口的来源",
                    "must_cover_nodes": ["识别封蜡缺口"]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="fulfillment_planned_nodes_mismatch"):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [],
            },
        )


def test_commit_service_rejects_empty_goal_even_when_called_directly(tmp_path):
    contract_path = tmp_path / ".story-system" / "chapters" / "chapter_003.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "meta": {"chapter": 3},
                "chapter_directive": {"goal": "", "must_cover_nodes": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="chapter_contract_missing_goal"):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [],
            },
        )


def test_modern_story_system_cannot_delete_chapter_contract_to_bypass_goal(tmp_path):
    story_root = tmp_path / ".story-system"
    story_root.mkdir(parents=True)
    (story_root / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "MASTER_SETTING",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="chapter_contract_missing_goal"):
        _build_commit(
            ChapterCommitService(tmp_path),
            tmp_path,
            chapter=1,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [],
            },
        )


def test_accepted_commit_persists_authoritative_goal_in_outline_snapshot(tmp_path):
    contract_path = tmp_path / ".story-system" / "chapters" / "chapter_003.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "meta": {"chapter": 3},
                "chapter_directive": {
                    "goal": "在子时前找到账簿并确认伪造者",
                    "must_cover_nodes": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    payload = _build_commit(
        ChapterCommitService(tmp_path),
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )

    assert payload["meta"]["status"] == "accepted"
    assert payload["outline_snapshot"]["goal"] == "在子时前找到账簿并确认伪造者"
    assert payload["meta"]["validation_status"] == "valid"
    assert len(payload["meta"]["predecessor_context_hash"]) == 64


def test_commit_service_rejects_outline_nodes_missing_from_contract(tmp_path):
    contract_path = tmp_path / ".story-system" / "chapters" / "chapter_003.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "meta": {"chapter": 3},
                "chapter_directive": {"goal": "识别封蜡缺口"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    outline_path = tmp_path / "大纲" / "第3章-账簿.md"
    outline_path.parent.mkdir(parents=True, exist_ok=True)
    outline_path.write_text(
        "### 第三章：账簿\n- 必须覆盖节点：识别封蜡缺口",
        encoding="utf-8",
    )
    service = ChapterCommitService(tmp_path)

    with pytest.raises(
        ValueError,
        match="chapter_contract_missing_must_cover_nodes",
    ):
        _build_commit(
            service,
            tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [],
                "entity_deltas": [],
                "accepted_events": [],
            },
        )


def test_commit_service_includes_volume_ref_and_write_fact_provenance(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["provenance"]["write_fact_role"] == "chapter_commit"
    assert payload["provenance"]["projection_role"] == "derived_read_models"
    assert payload["chapter_binding"] == payload["provenance"]["chapter_binding"]
    for artifact_name in _ARTIFACT_KEYS:
        assert payload[artifact_name]["chapter_binding"] == payload["chapter_binding"]


def test_build_commit_rejects_artifacts_bound_to_another_chapter(tmp_path):
    service = ChapterCommitService(tmp_path)
    chapter_one_binding = _chapter_binding(tmp_path, 1)
    _chapter_binding(tmp_path, 2)
    common = {"chapter_binding": chapter_one_binding}

    with pytest.raises(ChapterBindingError) as exc_info:
        service.build_commit(
            chapter=2,
            review_result=standard_review(chapter_one_binding),
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
                **common,
            },
            disambiguation_result={"pending": [], **common},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                **common,
            },
        )

    assert exc_info.value.code == "artifact_chapter_mismatch"


def test_build_commit_rejects_one_stale_artifact_binding(tmp_path):
    service = ChapterCommitService(tmp_path)
    binding = _chapter_binding(tmp_path, 3)
    stale = {**binding, "sha256": "f" * 64}

    with pytest.raises(ChapterBindingError) as exc_info:
        service.build_commit(
            chapter=3,
            review_result=standard_review(binding),
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
                "chapter_binding": binding,
            },
            disambiguation_result={"pending": [], "chapter_binding": binding},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "chapter_binding": stale,
            },
        )

    assert exc_info.value.code == "chapter_content_hash_mismatch"


def test_commit_service_rejects_malformed_gate_artifacts(tmp_path):
    service = ChapterCommitService(tmp_path)
    valid_fulfillment = {
        "planned_nodes": [],
        "covered_nodes": [],
        "missed_nodes": [],
        "extra_nodes": [],
    }
    valid_disambiguation = {"pending": []}
    valid_extraction = {"state_deltas": [], "entity_deltas": [], "accepted_events": []}

    with pytest.raises(ValueError, match="blocking_count"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={},
            fulfillment_result=valid_fulfillment,
            disambiguation_result=valid_disambiguation,
            extraction_result=valid_extraction,
        )

    with pytest.raises(ValueError, match="fulfillment_result"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={"fulfillment": {"missed_nodes": ["遗漏节点"]}},
            disambiguation_result=valid_disambiguation,
            extraction_result=valid_extraction,
        )

    with pytest.raises(ValueError, match="disambiguation_result"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result=valid_fulfillment,
            disambiguation_result={"disambiguation": {"pending": ["宗主"]}},
            extraction_result=valid_extraction,
        )


def test_commit_service_rejects_nested_extraction_result_shape(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="top-level"):
        _build_commit(service, tmp_path,
            chapter=76,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "chapter": 76,
                "extraction": {
                    "scenes": [{"summary": "场景切分"}],
                    "unresolved_threads": ["未解线索"],
                },
            },
        )


def test_commit_service_rejects_extraction_wrapper_even_with_empty_core_fields(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="nested under extraction"):
        _build_commit(service, tmp_path,
            chapter=76,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "extraction": {
                    "scenes": [{"summary": "真实场景却被包错层"}],
                    "summary_text": "真实摘要却被包错层",
                },
            },
        )


def test_commit_service_rejects_extraction_result_missing_core_fields(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="accepted_events"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={"summary_text": "摘要"},
        )


def test_commit_service_rejects_non_object_extraction_items(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match=r"state_deltas\[0\]"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": [],
                "state_deltas": ["境界变化"],
                "entity_deltas": [],
            },
        )


def test_commit_service_rejects_non_object_accepted_event_items(tmp_path):
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match=r"accepted_events\[0\]"):
        _build_commit(service, tmp_path,
            chapter=3,
            review_result={"blocking_count": 0},
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
            disambiguation_result={"pending": []},
            extraction_result={
                "accepted_events": ["not-a-json-object"],
                "state_deltas": [],
                "entity_deltas": [],
            },
        )


def test_commit_service_normalizes_accepted_events_before_projection(tmp_path):
    service = ChapterCommitService(tmp_path)

    payload = _build_commit(service, tmp_path,
        chapter=76,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "type": "mystery_introduction",
                    "characters": ["xiaoyan"],
                    "payload": {"content": "萧炎发现石门背后的新疑点"},
                }
            ],
        },
    )

    event = payload["extraction_result"]["accepted_events"][0]
    assert event["event_id"].startswith("evt-ch076-001-")
    assert event["chapter"] == 76
    assert event["event_type"] == "open_loop_created"
    assert event["subject"] == "xiaoyan"
    assert "accepted_events" not in payload


def test_apply_projections_normalizes_events_before_router_inspection(
    tmp_path, monkeypatch
):
    captured = {}

    class SpyRouter:
        def required_writers(self, payload):
            captured["events"] = list(payload.get("extraction_result", {}).get("accepted_events") or [])
            return []

    monkeypatch.setattr(
        "data_modules.chapter_commit_service.EventProjectionRouter",
        lambda: SpyRouter(),
    )

    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=76,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "accepted_events": [
                {
                    "type": "scene_open",
                    "characters": ["xiaoyan"],
                    "payload": {"content": "萧炎推开石门，新的悬念出现"},
                }
            ],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
        },
    )

    service.apply_projections(payload)

    event = captured["events"][0]
    assert event["event_id"].startswith("evt-ch076-001-")
    assert event["chapter"] == 76
    assert event["event_type"] == "open_loop_created"
    assert event["subject"] == "xiaoyan"
    assert payload["extraction_result"]["accepted_events"] == captured["events"]


def test_apply_projections_updates_state_for_rejected_commit(tmp_path):
    import json

    (tmp_path / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")

    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=7,
        review_result={"blocking_count": 1},
        fulfillment_result={
            "planned_nodes": ["进入坊市"],
            "covered_nodes": ["进入坊市"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    projected = service.apply_projections(payload)

    state = json.loads((tmp_path / ".canon-ledger" / "state.json").read_text(encoding="utf-8"))
    assert projected["projection_status"]["state"] == "done"
    assert state["progress"]["chapter_status"]["7"] == "chapter_rejected"


def test_chapter_commit_cli_builds_and_persists_commit(tmp_path, monkeypatch):
    review_path = tmp_path / "review.json"
    fulfillment_path = tmp_path / "fulfillment.json"
    disambiguation_path = tmp_path / "disambiguation.json"
    extraction_path = tmp_path / "extraction.json"
    binding = _chapter_binding(tmp_path, 3)
    contract_path = tmp_path / ".story-system" / "chapters" / "chapter_003.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "meta": {"chapter": 3},
                "chapter_directive": {
                    "goal": "确认陷阱的来源",
                    "must_cover_nodes": ["发现陷阱"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_artifact = lambda path, payload: path.write_text(
        json.dumps({**payload, "chapter_binding": binding}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_artifact(review_path, standard_review(binding))
    _write_artifact(
        fulfillment_path,
        {
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
    )
    _write_artifact(disambiguation_path, {"pending": []})
    _write_artifact(
        extraction_path,
        {"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from chapter_commit import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chapter_commit",
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--review-result",
            str(review_path),
            "--fulfillment-result",
            str(fulfillment_path),
            "--disambiguation-result",
            str(disambiguation_path),
            "--extraction-result",
            str(extraction_path),
        ],
    )
    main()

    assert (tmp_path / ".story-system" / "commits" / "chapter_003.commit.json").is_file()


def test_apply_projections_writes_events_and_amend_proposals(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(service, tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
            "accepted_events": [
                {
                    "event_id": "evt-001",
                    "chapter": 3,
                    "event_type": "world_rule_broken",
                    "subject": "金手指",
                    "payload": {
                        "field": "world_rule",
                        "base_value": "每日一次",
                        "proposed_value": "短时失控突破",
                    },
                }
            ],
        },
    )

    service.apply_projections(payload)

    assert (tmp_path / ".story-system" / "events" / "chapter_003.events.json").is_file()
    manager = IndexManager(DataModulesConfig.from_project_root(tmp_path))
    with manager._get_conn() as conn:
        row = conn.execute(
            """
            SELECT record_type, field, override_value, status
            FROM override_contracts
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row["record_type"] == "amend_proposal"
    assert row["field"] == "world_rule"
    assert row["override_value"] == "短时失控突破"
    assert row["status"] == "pending"


def test_apply_projections_fails_closed_when_chapter_changed(
    tmp_path, monkeypatch
):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_type": "open_loop_created",
                    "subject": "石门",
                    "payload": {"content": "石门后的新疑点"},
                }
            ],
        },
    )
    (tmp_path / "正文" / "第0003章.md").write_text("审查后正文已变更\n", encoding="utf-8")
    writer_calls = []
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    projected = service.apply_projections(payload)

    assert writer_calls == []
    assert not (
        tmp_path / ".story-system" / "events" / "chapter_003.events.json"
    ).exists()
    for writer in ("state", "index", "memory", "vector"):
        assert projected["projection_status"][writer] == (
            "failed:chapter_content_changed"
        )


def test_apply_projection_writers_rechecks_binding_before_loading_writers(
    tmp_path, monkeypatch
):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=4,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )
    (tmp_path / "正文" / "第0004章.md").write_text("当前正文不再匹配\n", encoding="utf-8")
    writer_calls = []
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    projected = service.apply_projection_writers(payload)

    assert writer_calls == []
    for writer in ("state", "index", "vector"):
        assert projected["projection_status"][writer] == (
            "failed:chapter_content_changed"
        )


def test_legacy_commit_without_binding_cannot_be_projected_as_trusted(
    tmp_path, monkeypatch
):
    service = ChapterCommitService(tmp_path)
    legacy = {
        "meta": {"schema_version": "story-system/v1", "chapter": 5, "status": "accepted"},
        "projection_status": {},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
        },
    }
    writer_calls = []
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    projected = service.apply_projection_writers(legacy)

    assert writer_calls == []
    for writer in ("state", "index", "vector"):
        assert projected["projection_status"][writer] == (
            "failed:chapter_content_changed"
        )


def test_projection_chain_rechecks_binding_between_writers(tmp_path, monkeypatch):
    service = ChapterCommitService(tmp_path)
    payload = _build_commit(
        service,
        tmp_path,
        chapter=6,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [
                {"entity_id": "hero", "field": "realm", "new": "foundation"}
            ],
            "entity_deltas": [],
            "accepted_events": [],
        },
    )
    chapter_path = tmp_path / "正文" / "第0006章.md"
    calls = []

    class MutatingWriter:
        def apply(self, _payload):
            calls.append("state")
            chapter_path.write_text("投影期间正文已改\n", encoding="utf-8")
            return {"applied": True}

    class ShouldNotRunWriter:
        def apply(self, _payload):
            calls.append("index")
            return {"applied": True}

    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: {"state": MutatingWriter(), "index": ShouldNotRunWriter()},
    )
    monkeypatch.setattr(
        "data_modules.chapter_commit_service.EventProjectionRouter.required_writers",
        lambda _self, _payload: {"state", "index"},
    )

    projected = service.apply_projection_writers(payload)

    assert calls == ["state"]
    assert projected["projection_status"]["state"] == "done"
    assert projected["projection_status"]["index"] == (
        "failed:chapter_content_changed"
    )


def _run_chapter_commit_cli(monkeypatch, capsys, *argv):
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from chapter_commit import main

    monkeypatch.setattr(sys, "argv", ["chapter_commit", *argv])
    main()
    return json.loads(capsys.readouterr().out)


def _committed_chapter_with_pending_presence(tmp_path):
    """One full commit whose only presence event is parked in human review."""
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("爱丽丝抵达北城。", encoding="utf-8")
    service = ChapterCommitService(tmp_path)
    first = _build_commit(
        service,
        tmp_path,
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={
            "pending": [
                {
                    "decision_id": "alice-location-check",
                    "category": "presence",
                    "candidate_event_id": "alice-location",
                    "reason": "这是否是实际抵达需要作者确认",
                }
            ]
        },
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [
                {
                    "event_id": "alice-location",
                    "chapter": 3,
                    "sequence": 1,
                    "event_type": "presence_observed",
                    "subject": "alice",
                    "payload": {
                        "location_id": "north-city",
                        "presence_kind": "physical",
                        "evidence_quote": "爱丽丝抵达北城。",
                    },
                }
            ],
            "fact_coverage": {
                "knowledge": "complete",
                "presence": "complete",
                "custody": "complete",
            },
            "fact_verification": {
                "knowledge": "supported",
                "presence": "supported",
                "custody": "supported",
            },
        },
    )
    assert first["extraction_result"]["accepted_events"] == []
    assert first["provenance"]["human_review"]["unresolved_count"] == 1
    service.persist_commit(first)
    service.apply_projections(first)
    return service


def test_from_last_commit_replays_confirmed_decision_and_reprojects(
    tmp_path, monkeypatch, capsys
):
    _committed_chapter_with_pending_presence(tmp_path)

    from data_modules.human_review import HumanReviewService

    HumanReviewService(tmp_path).record(
        {
            "decisions": [
                {
                    "decision_id": "alice-location-check",
                    "action": "confirm",
                    "note": "作者确认这是实际抵达",
                }
            ]
        }
    )

    payload = _run_chapter_commit_cli(
        monkeypatch,
        capsys,
        "--project-root",
        str(tmp_path),
        "--chapter",
        "3",
        "--from-last-commit",
    )

    assert payload["meta"]["status"] == "accepted"
    assert payload["disambiguation_result"]["pending"] == []
    accepted = payload["extraction_result"]["accepted_events"]
    assert [event["event_id"] for event in accepted] == ["alice-location"]
    assert accepted[0]["verification"] == "verified"
    assert payload["extraction_result"]["fact_verification"]["presence"] == "supported"
    assert payload["provenance"]["human_review"]["verified_event_ids"] == [
        "alice-location"
    ]

    persisted = json.loads(
        (
            tmp_path / ".story-system" / "commits" / "chapter_003.commit.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["extraction_result"]["accepted_events"][0]["verification"] == (
        "verified"
    )
    assert (
        tmp_path / ".story-system" / "events" / "chapter_003.events.json"
    ).is_file()
    assert payload["projection_status"]["state"] == "done"


def test_from_last_commit_fails_closed_when_chapter_edited(
    tmp_path, monkeypatch, capsys
):
    _committed_chapter_with_pending_presence(tmp_path)
    (tmp_path / "正文" / "第0003章.md").write_text(
        "爱丽丝只是在梦里见到北城。", encoding="utf-8"
    )

    with pytest.raises(SystemExit) as excinfo:
        _run_chapter_commit_cli(
            monkeypatch,
            capsys,
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--from-last-commit",
        )

    assert "canon-ledger-write" in str(excinfo.value)


def test_from_last_commit_guards_arguments_and_missing_commit(
    tmp_path, monkeypatch, capsys
):
    with pytest.raises(SystemExit):
        _run_chapter_commit_cli(
            monkeypatch,
            capsys,
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--from-last-commit",
            "--review-result",
            "review.json",
        )
    assert "互斥" in capsys.readouterr().err

    with pytest.raises(SystemExit) as excinfo:
        _run_chapter_commit_cli(
            monkeypatch,
            capsys,
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--from-last-commit",
        )
    assert "未找到" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 提交原子性：投影失败必须以非零退出可见，不允许静默的部分一致
# ---------------------------------------------------------------------------


def test_cli_exits_nonzero_when_projection_write_crashes(
    tmp_path, monkeypatch, capsys
):
    _committed_chapter_with_pending_presence(tmp_path)

    def _boom(self, payload):
        raise RuntimeError("模拟投影崩溃")

    monkeypatch.setattr(ChapterCommitService, "apply_projections", _boom)
    with pytest.raises(SystemExit) as excinfo:
        _run_chapter_commit_cli(
            monkeypatch,
            capsys,
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--from-last-commit",
        )

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "事件库/投影写入中断" in err
    assert "projections retry" in err


def test_cli_exits_nonzero_when_accepted_commit_has_unhealthy_projection(
    tmp_path, monkeypatch, capsys
):
    _committed_chapter_with_pending_presence(tmp_path)

    def _partial(self, payload):
        payload = dict(payload)
        payload["projection_status"] = {
            **payload.get("projection_status", {}),
            "index": "failed:disk_full",
        }
        return payload

    monkeypatch.setattr(ChapterCommitService, "apply_projections", _partial)
    with pytest.raises(SystemExit) as excinfo:
        _run_chapter_commit_cli(
            monkeypatch,
            capsys,
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--from-last-commit",
        )

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "读模型投影未完成" in captured.err
    assert "index=failed:disk_full" in captured.err


# ---------------------------------------------------------------------------
# 静默写入护栏：state_delta 旧值链、实体类型稳定性
# ---------------------------------------------------------------------------


def _minimal_commit_kwargs(chapter, extraction):
    return dict(
        chapter=chapter,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
            **extraction,
        },
    )


def _ensure_contract(project_root, chapter):
    contract_path = (
        project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json"
    )
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    if not contract_path.exists():
        contract_path.write_text(
            json.dumps(
                {
                    "meta": {"chapter": chapter},
                    "chapter_directive": {
                        "goal": "验证多章一致性护栏",
                        "must_cover_nodes": [],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _persist_chapter(service, project_root, chapter, extraction, body):
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(body, encoding="utf-8")
    _ensure_contract(project_root, chapter)
    payload = _build_commit(
        service, project_root, **_minimal_commit_kwargs(chapter, extraction)
    )
    assert payload["meta"]["status"] == "accepted"
    service.persist_commit(payload)
    return payload


def test_state_delta_on_recorded_field_requires_matching_old_value(tmp_path):
    service = ChapterCommitService(tmp_path)
    _persist_chapter(
        service,
        tmp_path,
        3,
        {"state_deltas": [{"entity_id": "hero", "field": "realm", "new": "筑基"}]},
        "主角突破至筑基。",
    )

    (tmp_path / "正文" / "第0004章.md").write_text("主角再进一步。", encoding="utf-8")
    _ensure_contract(tmp_path, 4)
    with pytest.raises(ValueError, match="state_delta_missing_old:hero:realm"):
        _build_commit(
            service,
            tmp_path,
            **_minimal_commit_kwargs(
                4,
                {
                    "state_deltas": [
                        {"entity_id": "hero", "field": "realm", "new": "金丹"}
                    ]
                },
            ),
        )

    with pytest.raises(ValueError, match="state_delta_conflict:hero:realm"):
        _build_commit(
            service,
            tmp_path,
            **_minimal_commit_kwargs(
                4,
                {
                    "state_deltas": [
                        {
                            "entity_id": "hero",
                            "field": "realm",
                            "old": "炼气",
                            "new": "金丹",
                        }
                    ]
                },
            ),
        )

    payload = _build_commit(
        service,
        tmp_path,
        **_minimal_commit_kwargs(
            4,
            {
                "state_deltas": [
                    {
                        "entity_id": "hero",
                        "field": "realm",
                        "old": "筑基",
                        "new": "金丹",
                    }
                ]
            },
        ),
    )
    assert payload["meta"]["status"] == "accepted"


def test_entity_delta_cannot_silently_retype_recorded_entity(tmp_path):
    service = ChapterCommitService(tmp_path)
    _persist_chapter(
        service,
        tmp_path,
        3,
        {
            "entity_deltas": [
                {
                    "entity_id": "血月珠",
                    "canonical_name": "血月珠",
                    "entity_type": "物品",
                }
            ]
        },
        "血月珠在匣中发出微光。",
    )

    (tmp_path / "正文" / "第0004章.md").write_text("血月珠再次现身。", encoding="utf-8")
    _ensure_contract(tmp_path, 4)
    with pytest.raises(ValueError, match="entity_type_conflict:血月珠"):
        _build_commit(
            service,
            tmp_path,
            **_minimal_commit_kwargs(
                4,
                {
                    "entity_deltas": [
                        {
                            "entity_id": "血月珠",
                            "canonical_name": "血月珠",
                            "entity_type": "角色",
                        }
                    ]
                },
            ),
        )


# ---------------------------------------------------------------------------
# information_id 冲突：同章硬错，跨章转人工队列并可裁决更正
# ---------------------------------------------------------------------------


def _knowledge_event(event_id, chapter, sequence, information_id, claim, quote):
    return {
        "event_id": event_id,
        "chapter": chapter,
        "sequence": sequence,
        "event_type": "knowledge_state_changed",
        "subject": "linzhou",
        "payload": {
            "information_id": information_id,
            "canonical_claim": claim,
            "evidence_fragment": quote.rstrip("。"),
            "state": "known",
            "source_kind": "witnessed",
            "evidence_quote": quote,
        },
    }


def test_same_chapter_information_id_conflict_fails_commit(tmp_path):
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("钥匙藏在地窖。灯谜写在墙上。", encoding="utf-8")
    service = ChapterCommitService(tmp_path)

    with pytest.raises(ValueError, match="information_id_conflict_in_chapter:cellar-key"):
        _build_commit(
            service,
            tmp_path,
            **_minimal_commit_kwargs(
                3,
                {
                    "accepted_events": [
                        _knowledge_event(
                            "k1", 3, 1, "cellar-key", "钥匙藏在地窖", "钥匙藏在地窖。"
                        ),
                        _knowledge_event(
                            "k2", 3, 2, "cellar-key", "灯谜写在墙上", "灯谜写在墙上。"
                        ),
                    ]
                },
            ),
        )


def test_cross_chapter_information_conflict_needs_human_then_corrects_canon(tmp_path):
    from data_modules.canonical_history import load_canonical_history
    from data_modules.human_review import HumanReviewService

    service = ChapterCommitService(tmp_path)
    _persist_chapter(
        service,
        tmp_path,
        3,
        {
            "accepted_events": [
                _knowledge_event(
                    "k1", 3, 1, "cellar-key", "钥匙藏在地窖", "钥匙藏在地窖。"
                )
            ],
        },
        "钥匙藏在地窖。",
    )

    (tmp_path / "正文" / "第0004章.md").write_text("其实钥匙藏在阁楼。", encoding="utf-8")
    _ensure_contract(tmp_path, 4)
    conflict_kwargs = _minimal_commit_kwargs(
        4,
        {
            "accepted_events": [
                _knowledge_event(
                    "k2", 4, 1, "cellar-key", "钥匙藏在阁楼", "其实钥匙藏在阁楼。"
                )
            ],
        },
    )
    held = _build_commit(service, tmp_path, **conflict_kwargs)

    assert held["meta"]["status"] == "accepted"
    assert held["extraction_result"]["accepted_events"] == []
    unresolved = held["disambiguation_result"]["pending"]
    assert len(unresolved) == 1
    assert unresolved[0]["source"] == "information_id_conflict"
    assert "既往表述" in unresolved[0]["existing_fact"]
    service.persist_commit(held)

    review = HumanReviewService(tmp_path)
    queue_item = review.list_items(4)[0]
    review.record(
        {
            "decisions": [
                {"decision_id": queue_item["decision_id"], "action": "confirm"}
            ]
        }
    )
    resolved = _build_commit(service, tmp_path, **conflict_kwargs)
    assert [
        event["event_id"]
        for event in resolved["extraction_result"]["accepted_events"]
    ] == ["k2"]
    assert (
        resolved["extraction_result"]["accepted_events"][0]["verification"]
        == "verified"
    )
    service.persist_commit(resolved)

    history = load_canonical_history(tmp_path, 4)
    row = history.information.get("cellar-key") or {}
    assert row.get("canonical_claim") == "钥匙藏在阁楼"

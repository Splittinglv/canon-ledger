#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.human_review import (
    HumanReviewService,
    review_manual_check_items_from_review,
)


def _binding(project_root, chapter=3, text="爱丽丝抵达北城。"):
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(text, encoding="utf-8")
    return build_chapter_binding(project_root, chapter)


def _presence_event(*, location="north-city", quote="爱丽丝抵达北城。"):
    return {
        "event_id": "alice-location",
        "chapter": 3,
        "sequence": 1,
        # A model is not allowed to grant this status to itself.
        "verification": "verified",
        "event_type": "presence_observed",
        "subject": "alice",
        "payload": {
            "location_id": location,
            "presence_kind": "physical",
            "transition_explicit": True,
            "evidence_quote": quote,
        },
    }


def _pending():
    return [
        {
            "decision_id": "confirm-alice-location",
            "category": "presence",
            "candidate_event_id": "alice-location",
            "evidence_quote": "爱丽丝抵达北城。",
            "reason": "“抵达”是否代表当前物理位置需要作者确认",
        }
    ]


def test_model_cannot_self_assign_verified_status(tmp_path):
    binding = _binding(tmp_path)
    result = HumanReviewService(tmp_path).apply_decisions(
        3,
        binding,
        [],
        [_presence_event()],
    )

    assert result["events"][0]["verification"] == "supported"


def test_unresolved_candidate_stays_out_of_canon_until_human_confirms(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)

    first = service.apply_decisions(3, binding, _pending(), [_presence_event()])

    assert first["events"] == []
    assert first["affected_dimensions"] == ["presence"]
    assert first["unresolved"][0]["blocking"] is False
    assert service.list_items(3)[0]["status"] == "pending"

    service.record(
        {
            "decisions": [
                {
                    "decision_id": "confirm-alice-location",
                    "action": "confirm",
                    "note": "作者确认这是实际抵达",
                }
            ]
        }
    )
    second = service.apply_decisions(3, binding, _pending(), [_presence_event()])

    assert second["unresolved"] == []
    assert second["events"][0]["verification"] == "verified"
    assert second["verified_event_ids"] == ["alice-location"]
    assert service.list_items(3)[0]["status"] == "confirm"


def test_decision_does_not_survive_chapter_content_change(tmp_path):
    first_binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, first_binding, _pending(), [_presence_event()])
    service.record(
        {
            "decisions": [
                {
                    "decision_id": "confirm-alice-location",
                    "action": "confirm",
                }
            ]
        }
    )

    second_binding = _binding(tmp_path, text="爱丽丝只是在梦里见到北城。")
    result = service.apply_decisions(
        3,
        second_binding,
        _pending(),
        [_presence_event(quote="爱丽丝只是在梦里见到北城。")],
    )

    assert result["events"] == []
    # 队列里的 decision_id 一律带章节前缀；record 也接受无歧义短 ID。
    assert result["unresolved"][0]["decision_id"] == "ch0003-confirm-alice-location"
    assert service.list_items(3)[0]["status"] == "pending"


def test_human_can_replace_a_candidate(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, binding, _pending(), [_presence_event()])
    replacement = _presence_event(location="north-gate")
    replacement["event_id"] = "alice-at-north-gate"
    service.record(
        {
            "decisions": [
                {
                    "decision_id": "confirm-alice-location",
                    "action": "replace",
                    "replacement_event": replacement,
                }
            ]
        }
    )

    result = service.apply_decisions(3, binding, _pending(), [_presence_event()])

    assert [event["event_id"] for event in result["events"]] == [
        "alice-at-north-gate"
    ]
    assert result["events"][0]["verification"] == "verified"


def test_human_can_ignore_a_candidate(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, binding, _pending(), [_presence_event()])
    service.record(
        {
            "decisions": [
                {
                    "decision_id": "confirm-alice-location",
                    "action": "ignore",
                }
            ]
        }
    )

    result = service.apply_decisions(3, binding, _pending(), [_presence_event()])

    assert result["events"] == []
    assert result["unresolved"] == []
    assert result["resolved_decision_ids"] == ["ch0003-confirm-alice-location"]


def test_replace_must_keep_event_identity_and_carry_evidence(tmp_path):
    """replace 只能改措辞：事件类型与主体锁定，且必须携带正文证据。"""
    import pytest

    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, binding, _pending(), [_presence_event()])

    retyped = _presence_event()
    retyped["event_type"] = "custody_changed"
    retyped["payload"] = {
        "from_holder": "",
        "to_holder": "alice",
        "evidence_quote": "爱丽丝抵达北城。",
    }
    with pytest.raises(ValueError, match="must_keep_event_type"):
        service.record(
            {
                "decisions": [
                    {
                        "decision_id": "confirm-alice-location",
                        "action": "replace",
                        "replacement_event": retyped,
                    }
                ]
            }
        )

    resubjected = _presence_event()
    resubjected["subject"] = "bob"
    with pytest.raises(ValueError, match="must_keep_subject"):
        service.record(
            {
                "decisions": [
                    {
                        "decision_id": "confirm-alice-location",
                        "action": "replace",
                        "replacement_event": resubjected,
                    }
                ]
            }
        )

    unevidenced = _presence_event(quote="")
    with pytest.raises(ValueError, match="missing_evidence|evidence"):
        service.record(
            {
                "decisions": [
                    {
                        "decision_id": "confirm-alice-location",
                        "action": "replace",
                        "replacement_event": unevidenced,
                    }
                ]
            }
        )


def test_short_decision_id_across_chapters_is_ambiguous(tmp_path):
    """两章各有一条同名短 ID 时，用短 ID 裁决必须确定性报错。"""
    import pytest

    service = HumanReviewService(tmp_path)
    binding_three = _binding(tmp_path, chapter=3)
    binding_four = _binding(tmp_path, chapter=4, text="爱丽丝再度抵达北城。")
    event_four = _presence_event(quote="爱丽丝再度抵达北城。")
    event_four["chapter"] = 4
    service.apply_decisions(3, binding_three, _pending(), [_presence_event()])
    service.apply_decisions(4, binding_four, _pending(), [event_four])

    with pytest.raises(ValueError, match="human_review_decision_id_ambiguous"):
        service.record(
            {
                "decisions": [
                    {"decision_id": "confirm-alice-location", "action": "confirm"}
                ]
            }
        )

    resolved = service.record(
        {
            "decisions": [
                {
                    "decision_id": "ch0003-confirm-alice-location",
                    "action": "confirm",
                }
            ]
        }
    )
    assert resolved["recorded"] == ["ch0003-confirm-alice-location"]


def test_verified_event_survives_repeated_replays(tmp_path):
    """已裁决 verified 的事件在第二次、第三次重放时不得降级回 supported。"""
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, binding, _pending(), [_presence_event()])
    service.record(
        {
            "decisions": [
                {"decision_id": "confirm-alice-location", "action": "confirm"}
            ]
        }
    )

    for _ in range(3):
        result = service.apply_decisions(3, binding, _pending(), [_presence_event()])
        assert result["events"][0]["verification"] == "verified"
        assert result["verified_event_ids"] == ["alice-location"]


def _knowledge_pending():
    return [
        {
            "decision_id": "confirm-door",
            "category": "knowledge",
            "candidate_event_id": "door-known",
            "candidate_event": {
                "event_id": "door-known",
                "chapter": 3,
                "sequence": 1,
                "event_type": "knowledge_state_changed",
                "subject": "alice",
                "payload": {
                    "information_id": "clocktower-secret-door",
                    "canonical_claim": "密门在钟楼下",
                    "evidence_fragment": "密门在钟楼下",
                    "state": "known",
                    "source_kind": "told",
                    "evidence_quote": "爱丽丝得知密门在钟楼下。",
                },
            },
            "evidence_quote": "爱丽丝得知密门在钟楼下。",
            "reason": "是否已知密门",
        }
    ]


def test_replace_must_keep_information_id(tmp_path):
    import pytest

    binding = _binding(tmp_path, text="爱丽丝得知密门在钟楼下。")
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, binding, _knowledge_pending(), [])
    replacement = _knowledge_pending()[0]["candidate_event"]
    replacement = {
        **replacement,
        "payload": {
            **replacement["payload"],
            "information_id": "another-secret",
            "canonical_claim": "密门在钟楼下",
        },
    }
    with pytest.raises(ValueError, match="must_keep_information_id"):
        service.record(
            {
                "decisions": [
                    {
                        "decision_id": "confirm-door",
                        "action": "replace",
                        "replacement_event": replacement,
                    }
                ]
            }
        )


def test_review_manual_checks_all_fact_categories_enter_queue():
    items = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "character",
                    "location": "第1段",
                    "description": "是否已知密门",
                    "evidence": "他推开密门",
                    "reason": "账本没有获得记录",
                },
                {
                    "category": "timeline",
                    "location": "第2段",
                    "description": "转场耗时",
                    "evidence": "已到南港",
                    "reason": "缺少距离",
                },
                {
                    "category": "continuity",
                    "location": "第3段",
                    "description": "死者是否出场",
                    "evidence": "白芷站在门口",
                    "reason": "上章已写死",
                },
                {
                    "category": "setting",
                    "location": "第4段",
                    "description": "境界是否越级",
                    "evidence": "他放出斗气",
                    "reason": "设定措辞有解释空间",
                },
            ]
        }
    )
    categories = {item["category"] for item in items}
    assert categories == {"knowledge_boundary", "timeline", "continuity", "setting"}
    timeline = next(item for item in items if item["category"] == "timeline")
    assert timeline["dimension"] == "presence"
    assert timeline["options"] == ["confirm", "ignore"]
    knowledge = next(item for item in items if item["category"] == "knowledge_boundary")
    assert knowledge["options"] == ["confirm", "ignore", "replace"]


def test_timeline_review_check_confirm_without_event_clears_queue(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    pending = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "timeline",
                    "location": "第1段",
                    "description": "转场耗时",
                    "evidence": "爱丽丝抵达北城。",
                    "reason": "缺少过夜说明",
                }
            ]
        }
    )
    result = service.apply_decisions(3, binding, pending, [])
    assert len(result["unresolved"]) == 1
    decision_id = result["unresolved"][0]["decision_id"]
    service.record({"decisions": [{"decision_id": decision_id, "action": "confirm"}]})
    resolved = service.apply_decisions(3, binding, pending, [])
    assert resolved["unresolved"] == []
    assert resolved["events"] == []

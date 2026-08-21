#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import pytest

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
    listed = service.list_items(3)[0]
    assert listed["status"] == "resolved"
    assert listed["decision_action"] == "confirm"


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


def test_v2_generic_replace_is_disabled(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.apply_decisions(3, binding, _pending(), [_presence_event()])
    replacement = _presence_event(location="north-gate")
    replacement["event_id"] = "alice-at-north-gate"
    with pytest.raises(ValueError, match="human_review_action_not_offered"):
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
    assert result["rewrite_required"] == []
    assert result["resolved_decision_ids"] == ["ch0003-confirm-alice-location"]
    assert service.list_items(3)[0]["status"] == "resolved"


def test_review_rewrite_requires_new_prose_instead_of_resolving_fact(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    pending = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "timeline",
                    "location": "第1段",
                    "description": "转场耗时不足",
                    "evidence": "爱丽丝抵达北城。",
                    "reason": "上一刻仍在千里之外",
                }
            ]
        }
    )
    first = service.apply_decisions(3, binding, pending, [])
    decision_id = first["unresolved"][0]["decision_id"]

    service.record(
        {"decisions": [{"decision_id": decision_id, "action": "rewrite"}]}
    )
    result = service.apply_decisions(3, binding, pending, [])

    assert result["unresolved"] == []
    assert [item["decision_id"] for item in result["rewrite_required"]] == [
        decision_id
    ]
    assert result["resolved_decision_ids"] == []
    listed = service.list_items(3)[0]
    assert listed["status"] == "rewrite_required"
    assert listed["decision_action"] == "rewrite"


def test_legacy_review_ignore_is_treated_as_rewrite_required(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    pending = [
        {
            "decision_id": "legacy-timeline-check",
            "source": "review_manual_check",
            "category": "timeline",
            "dimension": "presence",
            "candidate_event_id": "timeline-check-1",
            "evidence_quote": "爱丽丝抵达北城。",
            "reason": "旧版 ignore 表示作者确认正文穿帮",
            "options": ["confirm", "ignore"],
        }
    ]
    first = service.apply_decisions(3, binding, pending, [])
    assert first["affected_dimensions"] == []
    assert first["unresolved"][0]["fact_dimensions"] == []
    assert first["unresolved"][0]["dimension"] == ""
    decision_id = first["unresolved"][0]["decision_id"]
    service.record(
        {"decisions": [{"decision_id": decision_id, "action": "rewrite"}]}
    )

    # 模拟 v7.2 已落盘的旧裁决：当时把「确认穿帮」存成 ignore，且没有
    # outcome/source/category 等新字段。
    ledger = json.loads(service.ledger_path.read_text(encoding="utf-8"))
    legacy = ledger["decisions"][0]
    legacy["action"] = "ignore"
    for field in ("outcome", "source", "category"):
        legacy.pop(field, None)
    service.ledger_path.write_text(
        json.dumps(ledger, ensure_ascii=False),
        encoding="utf-8",
    )

    result = service.apply_decisions(3, binding, pending, [])

    assert result["resolved_decision_ids"] == []
    assert [item["decision_id"] for item in result["rewrite_required"]] == [
        decision_id
    ]
    assert result["affected_dimensions"] == []
    assert service.list_items(3)[0]["status"] == "rewrite_required"


def test_same_binding_rerun_cannot_erase_pending_human_review(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.persist_queue(3, binding, _pending())

    service.persist_queue(3, binding, [])

    items = service.list_items(3)
    assert len(items) == 1
    assert items[0]["status"] == "pending"


def test_new_binding_can_replace_old_pending_queue_after_re_review(tmp_path):
    first_binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.persist_queue(3, first_binding, _pending())

    second_binding = _binding(tmp_path, text="爱丽丝仍留在南港，没有抵达北城。")
    service.persist_queue(3, second_binding, [])

    assert service.list_items(3) == []
    queue = json.loads(service.queue_path(3).read_text(encoding="utf-8"))
    assert queue["chapter_binding"]["sha256"] == second_binding["sha256"]


def test_disabled_replace_cannot_bypass_event_identity_checks(tmp_path):
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
    with pytest.raises(ValueError, match="human_review_action_not_offered"):
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
    with pytest.raises(ValueError, match="human_review_action_not_offered"):
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
    with pytest.raises(ValueError, match="human_review_action_not_offered"):
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


def test_disabled_replace_cannot_change_information_identity(tmp_path):
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
    with pytest.raises(ValueError, match="human_review_action_not_offered"):
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
    assert timeline["fact_dimensions"] == []
    assert "dimension" not in timeline
    assert timeline["options"] == ["confirm", "rewrite"]
    knowledge = next(item for item in items if item["category"] == "knowledge_boundary")
    assert knowledge["fact_dimensions"] == []
    assert "dimension" not in knowledge
    assert knowledge["options"] == ["confirm", "rewrite", "replace"]


def test_continuity_check_prefers_explicit_fact_dimensions():
    items = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "continuity",
                    "location": "第3段",
                    "description": "王印是否仍由白芷持有",
                    "evidence": "白芷从袖中取出王印。",
                    "reason": "上一章似乎已经交给城主",
                    "fact_dimensions": ["custody", "presence"],
                    "review_kind": "ambiguity",
                    "trigger_kind": "ambiguous_fact",
                    "materiality": "high",
                    "disposition": "human_required",
                    "required": True,
                    "source_event_id": "event-royal-seal-transfer",
                }
            ]
        }
    )

    assert items[0]["dimension"] == "custody"
    assert items[0]["fact_dimensions"] == ["custody", "presence"]
    assert items[0]["source_event_id"] == "event-royal-seal-transfer"


def test_manual_review_router_skips_audit_and_ignore_but_keeps_advisory():
    base = {
        "category": "continuity",
        "location": "第3段",
        "description": "可能存在事实疑点",
        "reason": "需要按策略分流",
        "fact_dimensions": ["presence"],
    }
    items = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    **base,
                    "description": "只留审计",
                    "materiality": "low",
                    "disposition": "audit_only",
                },
                {
                    **base,
                    "description": "明确忽略",
                    "materiality": "low",
                    "disposition": "ignore",
                },
                {
                    **base,
                    "description": "作者可稍后复核",
                    "evidence": "远处似乎有人影一闪而过。",
                    "materiality": "low",
                    "disposition": "advisory",
                    "required": False,
                },
                {
                    **base,
                    "description": "卷末正史快照",
                    "review_kind": "checkpoint",
                    "trigger_kind": "volume_end",
                    "materiality": "critical",
                    "disposition": "human_required",
                    "required": True,
                },
            ]
        }
    )

    assert [item["disposition"] for item in items] == [
        "advisory",
        "human_required",
    ]
    assert [item["blocking"] for item in items] == [False, False]


def test_gate_summary_separates_required_and_advisory_items(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    service.persist_queue(
        3,
        binding,
        [
            {
                **_pending()[0],
                "decision_id": "required-check",
                "candidate_event_id": "required-event",
                "disposition": "human_required",
                "required": True,
            },
            {
                **_pending()[0],
                "decision_id": "advisory-check",
                "candidate_event_id": "advisory-event",
                "materiality": "low",
                "disposition": "advisory",
                "required": False,
            },
            {
                **_pending()[0],
                "decision_id": "audit-check",
                "candidate_event_id": "audit-event",
                "evidence_quote": "",
                "materiality": "low",
                "disposition": "audit_only",
                "required": False,
            },
        ],
    )

    summary = service.gate_summary(before_chapter=4)

    assert [item["decision_id"] for item in summary["pending"]] == [
        "ch0003-required-check"
    ]
    assert [item["decision_id"] for item in summary["advisory_pending"]] == [
        "ch0003-advisory-check"
    ]
    assert summary["counts"]["pending"] == 1
    assert summary["counts"]["advisory_pending"] == 1
    assert all(
        "audit-check" not in item["decision_id"]
        for item in service.list_items(3)
    )


def test_advisory_manual_check_does_not_degrade_fact_verification(tmp_path):
    binding = _binding(tmp_path)
    pending = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "continuity",
                    "description": "远景人影身份可稍后复核",
                    "reason": "不影响当前正史判断",
                    "evidence": "远处似乎有人影一闪而过。",
                    "fact_dimensions": ["presence"],
                    "materiality": "low",
                    "disposition": "advisory",
                    "required": False,
                }
            ]
        }
    )

    result = HumanReviewService(tmp_path).apply_decisions(
        3, binding, pending, []
    )

    assert len(result["unresolved"]) == 1
    assert result["affected_dimensions"] == []


def test_advisory_candidate_remains_supported_while_waiting(tmp_path):
    binding = _binding(tmp_path)
    event = _presence_event()
    pending = [
        {
            **_pending()[0],
            "candidate_event_id": event["event_id"],
            "candidate_event": event,
            "fact_dimensions": ["presence"],
            "materiality": "low",
            "disposition": "advisory",
            "required": False,
        }
    ]

    result = HumanReviewService(tmp_path).apply_decisions(
        3,
        binding,
        pending,
        [event],
    )

    assert len(result["unresolved"]) == 1
    assert [item["event_id"] for item in result["events"]] == [event["event_id"]]
    assert result["events"][0]["verification"] == "supported"
    assert result["affected_dimensions"] == []


def test_gate_summary_escalates_advisory_canon_changes_until_replayed(tmp_path):
    for action in ("confirm", "ignore"):
        root = tmp_path / action
        binding = _binding(root)
        event = _presence_event()
        pending = [
            {
                **_pending()[0],
                "candidate_event_id": event["event_id"],
                "candidate_event": event,
                "fact_dimensions": ["presence"],
                "materiality": "low",
                "disposition": "advisory",
                "required": False,
            }
        ]
        service = HumanReviewService(root)
        first = service.apply_decisions(3, binding, pending, [event])
        decision_id = first["unresolved"][0]["decision_id"]
        decision = {"decision_id": decision_id, "action": action}
        service.record({"decisions": [decision]})

        summary = service.gate_summary(before_chapter=4)
        if action == "confirm":
            assert summary["not_replayed"] == []
            assert summary["advisory_not_replayed"][0][
                "decision_action"
            ] == "confirm"
            continue

        assert summary["advisory_not_replayed"] == []
        assert summary["not_replayed"][0]["decision_action"] == action

        # Once the exact decision receipt is present in the chapter commit,
        # the temporary replay blocker must clear.
        listed = service.list_items(3)[0]
        commit_path = (
            root / ".story-system" / "commits" / "chapter_003.commit.json"
        )
        commit_path.parent.mkdir(parents=True, exist_ok=True)
        commit_path.write_text(
            json.dumps(
                {
                    "provenance": {
                        "human_review": {
                            "resolved_decision_ids": [decision_id],
                            "decision_receipts": [
                                {
                                    "decision_id": decision_id,
                                    "decision_sha256": listed[
                                        "decision_sha256"
                                    ],
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        replayed = service.gate_summary(before_chapter=4)
        assert replayed["not_replayed"] == []


def test_gate_summary_escalates_advisory_rewrite_to_blocker(tmp_path):
    binding = _binding(tmp_path)
    pending = [
        {
            "decision_id": "advisory-prose-bug",
            "source": "review_manual_check",
            "category": "timeline",
            "reason": "作者需要判断这是否是时间线穿帮",
            "evidence_quote": "爱丽丝抵达北城。",
            "options": ["confirm", "rewrite"],
            "fact_dimensions": [],
            "review_kind": "ambiguity",
            "materiality": "low",
            "disposition": "advisory",
            "required": False,
        }
    ]
    service = HumanReviewService(tmp_path)
    first = service.apply_decisions(3, binding, pending, [])
    service.record(
        {
            "decisions": [
                {
                    "decision_id": first["unresolved"][0]["decision_id"],
                    "action": "rewrite",
                }
            ]
        }
    )

    summary = service.gate_summary(before_chapter=4)

    assert summary["advisory_rewrite_required"] == []
    assert summary["rewrite_required"][0]["decision_action"] == "rewrite"


def test_checkpoint_can_offer_rewrite_and_enters_rewrite_state(tmp_path):
    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    event = _presence_event()
    pending = [
        {
            **_pending()[0],
            "candidate_event_id": event["event_id"],
            "candidate_event": event,
            "review_kind": "checkpoint",
            "trigger_kind": "author_marked",
            "materiality": "high",
            "disposition": "human_required",
            "required": True,
            "options": ["confirm", "ignore", "rewrite"],
        }
    ]
    first = service.apply_decisions(3, binding, pending, [event])

    service.record(
        {
            "decisions": [
                {
                    "decision_id": first["unresolved"][0]["decision_id"],
                    "action": "rewrite",
                }
            ]
        }
    )
    resolved = service.apply_decisions(3, binding, pending, [event])

    assert resolved["events"] == []
    assert len(resolved["rewrite_required"]) == 1


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


def test_character_review_confirm_can_close_doubt_without_inventing_fact(tmp_path):
    binding = _binding(tmp_path, text="爱丽丝径直推开了密门。")
    service = HumanReviewService(tmp_path)
    pending = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "character",
                    "location": "第1段",
                    "description": "爱丽丝是否知道密门位置",
                    "evidence": "爱丽丝径直推开了密门。",
                    "reason": "账本没有明确的获知记录",
                }
            ]
        }
    )
    first = service.apply_decisions(3, binding, pending, [])
    decision_id = first["unresolved"][0]["decision_id"]

    service.record(
        {"decisions": [{"decision_id": decision_id, "action": "confirm"}]}
    )
    resolved = service.apply_decisions(3, binding, pending, [])

    assert resolved["unresolved"] == []
    assert resolved["events"] == []
    assert resolved["resolved_decision_ids"] == [decision_id]


def test_character_review_cannot_backfill_fact_with_v2_replace(tmp_path):
    binding = _binding(tmp_path, text="爱丽丝径直推开了密门。")
    service = HumanReviewService(tmp_path)
    pending = review_manual_check_items_from_review(
        {
            "manual_checks": [
                {
                    "category": "character",
                    "location": "第1段",
                    "description": "爱丽丝是否知道密门位置",
                    "evidence": "爱丽丝径直推开了密门。",
                    "reason": "账本没有明确的获知记录",
                }
            ]
        }
    )
    first = service.apply_decisions(3, binding, pending, [])
    decision_id = first["unresolved"][0]["decision_id"]
    replacement = {
        "event_id": "alice-knows-secret-door",
        "chapter": 3,
        "sequence": 1,
        "event_type": "knowledge_state_changed",
        "subject": "alice",
        "payload": {
            "information_id": "clocktower-secret-door",
            "canonical_claim": "密门在钟楼下",
            "evidence_fragment": "径直推开了密门",
            "state": "known",
            "source_kind": "inferred",
            "evidence_quote": "爱丽丝径直推开了密门。",
        },
    }

    with pytest.raises(ValueError, match="human_review_action_not_offered"):
        service.record(
            {
                "decisions": [
                    {
                        "decision_id": decision_id,
                        "action": "replace",
                        "replacement_event": replacement,
                    }
                ]
            }
        )


def test_extraction_candidate_cannot_offer_rewrite_action(tmp_path):
    import pytest

    binding = _binding(tmp_path)
    service = HumanReviewService(tmp_path)
    pending = [{**_pending()[0], "options": ["confirm", "ignore", "rewrite"]}]
    first = service.apply_decisions(3, binding, pending, [_presence_event()])

    with pytest.raises(ValueError, match="human_review_action_not_offered"):
        service.record(
            {
                "decisions": [
                    {
                        "decision_id": first["unresolved"][0]["decision_id"],
                        "action": "rewrite",
                    }
                ]
            }
        )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.human_review import HumanReviewService


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
    assert result["unresolved"][0]["decision_id"] == "confirm-alice-location"
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
    assert result["resolved_decision_ids"] == ["confirm-alice-location"]

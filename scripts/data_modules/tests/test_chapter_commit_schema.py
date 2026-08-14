#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from copy import deepcopy

import pytest

from data_modules.chapter_commit_schema import (
    ChapterCommitSchema,
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
    normalize_accepted_events,
)


def _binding(chapter=3, *, digest="a" * 64, path=None):
    return {
        "schema_version": "webnovel-chapter-content-binding/v1",
        "chapter": chapter,
        "path": path or f"正文/第{chapter:04d}章.md",
        "sha256": digest,
        "bytes": 12,
    }


def _commit_payload(chapter=3):
    binding = _binding(chapter)
    return {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": chapter,
            "status": "accepted",
        },
        "chapter_binding": binding,
        "provenance": {"chapter_binding": binding},
        "review_result": {"blocking_count": 0, "chapter_binding": binding},
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        "disambiguation_result": {"pending": [], "chapter_binding": binding},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "chapter_binding": binding,
        },
        "projection_status": {"state": "pending"},
    }


def test_artifact_models_preserve_valid_top_level_payloads():
    binding = _binding()
    review = ReviewResult.model_validate(
        {
            "blocking_count": 0,
            "issues_count": 2,
            "has_blocking": False,
            "chapter_binding": binding,
        }
    )
    fulfillment = FulfillmentResult.model_validate(
        {
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        }
    )
    disambiguation = DisambiguationResult.model_validate(
        {"pending": [], "chapter_binding": binding}
    )
    extraction = ExtractionResult.model_validate(
        {
            "accepted_events": [],
            "state_deltas": [{"entity_id": "xiaoyan", "field": "realm", "new": "斗者"}],
            "entity_deltas": [],
            "summary_text": "本章摘要",
            "chapter_binding": binding,
        }
    )

    assert review.model_dump()["issues_count"] == 2
    assert fulfillment.covered_nodes == ["发现陷阱"]
    assert disambiguation.pending == []
    assert extraction.state_deltas[0]["entity_id"] == "xiaoyan"


def test_artifact_models_reject_nested_wrappers_and_missing_core_fields():
    with pytest.raises(ValueError, match="nested under fulfillment"):
        FulfillmentResult.model_validate({"fulfillment": {"missed_nodes": []}})

    with pytest.raises(ValueError, match="nested under disambiguation"):
        DisambiguationResult.model_validate({"disambiguation": {"pending": []}})

    with pytest.raises(ValueError, match="nested under extraction"):
        ExtractionResult.model_validate(
            {
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "extraction": {"summary_text": "wrapped"},
            }
        )

    with pytest.raises(ValueError, match="accepted_events"):
        ExtractionResult.model_validate(
            {
                "state_deltas": [],
                "entity_deltas": [],
                "chapter_binding": _binding(),
            }
        )


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (ReviewResult, {"blocking_count": 0}),
        (
            FulfillmentResult,
            {
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            },
        ),
        (DisambiguationResult, {"pending": []}),
        (
            ExtractionResult,
            {"accepted_events": [], "state_deltas": [], "entity_deltas": []},
        ),
    ],
)
def test_all_commit_artifact_models_require_chapter_binding(model, payload):
    with pytest.raises(ValueError, match="chapter_binding"):
        model.model_validate(payload)


def test_chapter_commit_schema_requires_one_shared_binding():
    payload = _commit_payload()
    parsed = ChapterCommitSchema.model_validate(payload)
    assert parsed.chapter_binding.model_dump() == _binding()

    mismatched_artifact = deepcopy(payload)
    mismatched_artifact["extraction_result"]["chapter_binding"] = _binding(
        digest="b" * 64
    )
    with pytest.raises(ValueError, match="extraction_result.chapter_binding"):
        ChapterCommitSchema.model_validate(mismatched_artifact)

    mismatched_provenance = deepcopy(payload)
    mismatched_provenance["provenance"]["chapter_binding"] = _binding(
        digest="b" * 64
    )
    with pytest.raises(ValueError, match="provenance.chapter_binding"):
        ChapterCommitSchema.model_validate(mismatched_provenance)


def test_chapter_commit_schema_rejects_binding_for_another_chapter():
    payload = _commit_payload(chapter=3)
    payload["meta"]["chapter"] = 4

    with pytest.raises(ValueError, match="does not match commit chapter"):
        ChapterCommitSchema.model_validate(payload)


def test_accepted_event_model_normalizes_aliases_before_story_event_validation():
    events = normalize_accepted_events(
        76,
        [
            {
                "type": "scene_open",
                "characters": ["xiaoyan"],
                "payload": {"content": "新的谜团"},
            }
        ],
    )

    assert events[0]["event_id"].startswith("evt-ch076-001-")
    assert events[0]["chapter"] == 76
    assert events[0]["event_type"] == "open_loop_created"
    assert events[0]["subject"] == "xiaoyan"


def test_accepted_event_model_rejects_malformed_event_collections():
    with pytest.raises(ValueError, match="accepted_events must be a list"):
        normalize_accepted_events(3, {"event_type": "open_loop_created"})

    with pytest.raises(ValueError, match=r"accepted_events\[0\]"):
        normalize_accepted_events(3, ["not-a-json-object"])


def test_accepted_event_model_rejects_blank_subject_and_unknown_type():
    with pytest.raises(ValueError, match="subject"):
        normalize_accepted_events(
            3,
            [
                {
                    "event_type": "open_loop_created",
                    "subject": "   ",
                    "payload": {"content": "三年之约提及"},
                }
            ],
        )

    with pytest.raises(ValueError, match="event_type"):
        normalize_accepted_events(
            3,
            [
                {
                    "event_id": "evt-unknown",
                    "event_type": "not_a_story_event",
                    "subject": "xiaoyan",
                    "payload": {},
                }
            ],
        )


def test_accepted_event_model_rejects_chapter_spoofing():
    with pytest.raises(ValueError, match="does not match commit chapter 10"):
        normalize_accepted_events(
            10,
            [
                {
                    "event_id": "evt-future-secret",
                    "chapter": 1,
                    "event_type": "world_rule_revealed",
                    "subject": "掌柜身份",
                    "payload": {"description": "掌柜是第十章才揭示的暗线首领"},
                }
            ],
        )

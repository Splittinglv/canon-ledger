#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from copy import deepcopy

import pytest

from .review_test_helpers import standard_review

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
        "schema_version": "canon-ledger-chapter-content-binding/v1",
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
        "review_result": standard_review(binding),
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
    review = ReviewResult.model_validate(standard_review(binding))
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

    assert review.model_dump()["issues_count"] == 0
    assert fulfillment.covered_nodes == ["发现陷阱"]
    assert disambiguation.pending == []
    assert extraction.state_deltas[0]["entity_id"] == "xiaoyan"


def test_extraction_fact_coverage_is_explicit_and_closed():
    payload = {
        "accepted_events": [],
        "state_deltas": [],
        "entity_deltas": [],
        "fact_coverage": {
            "knowledge": "complete",
            "presence": "partial",
            "custody": "complete",
        },
        "chapter_binding": _binding(),
    }

    extraction = ExtractionResult.model_validate(payload)
    assert extraction.fact_coverage == payload["fact_coverage"]

    bad = deepcopy(payload)
    bad["fact_coverage"].pop("custody")
    with pytest.raises(ValueError, match="must contain exactly"):
        ExtractionResult.model_validate(bad)

    bad = deepcopy(payload)
    bad["fact_coverage"]["presence"] = "unknown"
    with pytest.raises(ValueError, match="must be complete or partial"):
        ExtractionResult.model_validate(bad)


def test_extraction_fact_verification_is_separate_and_closed():
    payload = {
        "accepted_events": [],
        "state_deltas": [],
        "entity_deltas": [],
        "fact_verification": {
            "knowledge": "verified",
            "presence": "supported",
            "custody": "pending",
        },
        "chapter_binding": _binding(),
    }

    extraction = ExtractionResult.model_validate(payload)
    assert extraction.fact_verification == payload["fact_verification"]

    bad = deepcopy(payload)
    bad["fact_verification"]["presence"] = "confident"
    with pytest.raises(ValueError, match="must be verified, supported, pending"):
        ExtractionResult.model_validate(bad)


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
                "payload": {
                    "content": "新的谜团",
                    "evidence_quote": "新的谜团",
                },
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


def test_long_term_consistency_events_are_normalized():
    events = normalize_accepted_events(
        3,
        [
            {
                "event_id": "learn-secret",
                "event_type": "information_learned",
                "sequence": 1,
                "subject": "alice",
                "payload": {
                    "information_id": "secret-door",
                    "content": "密门在钟楼下",
                    "state": "KNOWN",
                    "source_kind": "TOLD",
                    "source_entity": "keeper",
                    "evidence_quote": "守门人告诉她：密门在钟楼下。",
                },
            },
            {
                "event_id": "alice-north",
                "event_type": "location_changed",
                "sequence": 2,
                "subject": "alice",
                "payload": {
                    "location_id": "north-city",
                    "presence_kind": "PHYSICAL",
                    "scene_index": "2",
                    "transition_explicit": True,
                    "evidence_quote": "爱丽丝抵达北城。",
                },
            },
            {
                "event_id": "key-transfer",
                "event_type": "artifact_transferred",
                "sequence": 3,
                "subject": "bronze-key",
                "payload": {
                    "from_holder": "alice",
                    "to_holder": "bob",
                    "evidence_quote": "爱丽丝把铜钥匙交给鲍勃。",
                },
            },
        ],
    )

    assert [event["event_type"] for event in events] == [
        "knowledge_state_changed",
        "presence_observed",
        "custody_changed",
    ]
    assert events[0]["payload"]["state"] == "known"
    assert events[0]["payload"]["source_kind"] == "told"
    assert events[1]["payload"]["scene_index"] == 2
    assert events[1]["payload"]["presence_kind"] == "physical"
    assert events[2]["payload"]["location_id"] == ""


def test_long_term_consistency_events_require_unique_positive_sequences():
    event = {
        "event_type": "presence_observed",
        "subject": "alice",
        "payload": {
            "location_id": "north-city",
            "presence_kind": "physical",
            "evidence_quote": "爱丽丝抵达北城。",
        },
    }

    with pytest.raises(ValueError, match="sequence must be a positive integer"):
        normalize_accepted_events(3, [event])

    duplicate = [
        {**event, "event_id": "arrival-1", "sequence": 1},
        {**event, "event_id": "arrival-2", "sequence": 1},
    ]
    with pytest.raises(ValueError, match="sequence 1 is duplicated"):
        normalize_accepted_events(3, duplicate)


@pytest.mark.parametrize(
    ("event", "message"),
    [
        (
            {
                "event_type": "knowledge_state_changed",
                "sequence": 1,
                "subject": "alice",
                "payload": {
                    "information_id": "secret-door",
                    "content": "密门在钟楼下",
                    "state": "known",
                    "source_kind": "told",
                    "evidence_quote": "守门人只说了半句话。",
                },
            },
            "verbatim fragment",
        ),
        (
            {
                "event_type": "presence_observed",
                "sequence": 1,
                "subject": "alice",
                "payload": {
                    "presence_kind": "dream",
                    "evidence_quote": "她梦见自己站在南港。",
                },
            },
            "location_id",
        ),
        (
            {
                "event_type": "custody_changed",
                "sequence": 1,
                "subject": "bronze-key",
                "payload": {
                    "to_holder": "bob",
                    "evidence_quote": "鲍勃拿到了铜钥匙。",
                },
            },
            "from_holder and to_holder",
        ),
    ],
)
def test_long_term_consistency_events_fail_closed(event, message):
    with pytest.raises(ValueError, match=message):
        normalize_accepted_events(3, [event])

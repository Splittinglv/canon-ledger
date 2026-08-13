#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from data_modules.commit_artifacts import (
    RETRIEVAL_SCHEMA_VERSION,
    extraction_list,
    extraction_result_from_commit,
    extraction_text,
    retrieval_source_marker,
)


def test_extraction_result_prefers_canonical_nested_payload():
    payload = {
        "extraction_result": {
            "accepted_events": [{"event_id": "nested"}],
            "summary_text": "nested summary",
        },
        "accepted_events": [{"event_id": "legacy"}],
        "summary_text": "legacy summary",
    }

    extraction = extraction_result_from_commit(payload)

    assert extraction["accepted_events"] == [{"event_id": "nested"}]
    assert extraction["summary_text"] == "nested summary"
    assert extraction_list(payload, "accepted_events") == [{"event_id": "nested"}]
    assert extraction_text(payload, "summary_text") == "nested summary"


def test_extraction_result_keeps_read_compatibility_for_legacy_commit_payload():
    payload = {
        "accepted_events": [{"event_id": "legacy"}],
        "summary_text": "legacy summary",
    }

    extraction = extraction_result_from_commit(payload)

    assert extraction["accepted_events"] == [{"event_id": "legacy"}]
    assert extraction["summary_text"] == "legacy summary"


def test_retrieval_marker_tracks_fact_snapshot_not_projection_status():
    assert RETRIEVAL_SCHEMA_VERSION == "fact-only-v3"
    payload = {
        "meta": {"chapter": 3, "status": "accepted"},
        "projection_status": {"vector": "pending"},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [
                {"entity_id": "medicine_box", "field": "owner", "new": "shopkeeper"}
            ],
            "entity_deltas": [],
            "scenes": [],
            "summary_text": "阿青交付药箱。",
        },
    }
    before = retrieval_source_marker(payload)
    payload["projection_status"]["vector"] = "done"
    assert retrieval_source_marker(payload) == before
    payload["extraction_result"]["state_deltas"][0]["new"] = "a_qing"
    assert retrieval_source_marker(payload) != before

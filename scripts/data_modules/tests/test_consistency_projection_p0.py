#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 regression coverage for commit-derived long-term consistency."""

from __future__ import annotations

import json

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager
from data_modules.index_projection_writer import IndexProjectionWriter
from data_modules.memory.store import ScratchpadManager
from data_modules.memory_contract_adapter import MemoryContractAdapter
from data_modules.memory_projection_writer import MemoryProjectionWriter
from data_modules.projections import retry_projection
from data_modules.state_projection_writer import StateProjectionWriter


def _prepare_project(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    config = DataModulesConfig.from_project_root(tmp_path)
    config.ensure_dirs()
    return config


def _commit(project_root, chapter: int, extraction: dict):
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
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
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            **extraction,
        },
    )
    return service.apply_projections(payload)


def test_commit_mainline_projects_timeline_and_returns_its_actual_metadata(tmp_path):
    config = _prepare_project(tmp_path)
    projected = _commit(
        tmp_path,
        4,
        {
            "timeline_events": [
                {
                    "timeline_id": "tl-leave-sect",
                    "sequence": 1,
                    "event": "主角离开宗门",
                    "time_hint": "第三日黄昏",
                    "event_type": "travel",
                }
            ]
        },
    )

    assert projected["projection_status"]["memory"] == "done"
    rows = ScratchpadManager(config).query(category="timeline", status="active")
    assert len(rows) == 1
    assert rows[0].payload["timeline_id"] == "tl-leave-sect"

    timeline = MemoryContractAdapter(config).get_timeline(1, 10)
    assert [item.to_dict() for item in timeline] == [
        {
            "event": "主角离开宗门",
            "chapter": 4,
            "time_hint": "第三日黄昏",
            "event_type": "travel",
        }
    ]


def test_timeline_uses_sequence_and_rejects_duplicate_identity(tmp_path):
    config = _prepare_project(tmp_path)
    _commit(
        tmp_path,
        4,
        {
            "timeline_events": [
                {"timeline_id": "tl-second", "sequence": 2, "event": "第二件事"},
                {"timeline_id": "tl-first", "sequence": 1, "event": "第一件事"},
            ]
        },
    )
    assert [item.event for item in MemoryContractAdapter(config).get_timeline(4, 4)] == [
        "第一件事",
        "第二件事",
    ]

    with pytest.raises(ValueError, match="timeline_id is duplicated"):
        _commit(
            tmp_path,
            5,
            {
                "timeline_events": [
                    {"timeline_id": "same", "sequence": 1, "event": "A"},
                    {"timeline_id": "same", "sequence": 2, "event": "B"},
                ]
            },
        )


def test_legacy_memory_facts_timeline_is_lifted_into_commit_mainline(tmp_path):
    config = _prepare_project(tmp_path)
    projected = _commit(
        tmp_path,
        3,
        {"memory_facts": {"timeline_events": [{"event": "旧格式时间线"}]}},
    )
    assert projected["projection_status"]["memory"] == "done"
    assert [item.event for item in MemoryContractAdapter(config).get_timeline(1, 3)] == [
        "旧格式时间线"
    ]


def test_timeline_id_cannot_silently_overwrite_a_different_chapter_fact(tmp_path):
    _prepare_project(tmp_path)
    _commit(
        tmp_path,
        1,
        {"timeline_events": [{"timeline_id": "tl-dup", "sequence": 1, "event": "第一事实"}]},
    )
    with pytest.raises(ValueError, match="timeline_id_conflict:tl-dup"):
        _commit(
            tmp_path,
            2,
            {"timeline_events": [{"timeline_id": "tl-dup", "sequence": 1, "event": "第二事实"}]},
        )


def test_close_and_payoff_use_stable_ids_and_old_reprojection_cannot_reopen(tmp_path):
    config = _prepare_project(tmp_path)
    created = _commit(
        tmp_path,
        2,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop-created",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-jade", "content": "玉佩为何发热"},
                },
                {
                    "event_id": "evt-promise-created",
                    "chapter": 2,
                    "event_type": "promise_created",
                    "subject": "hero",
                    "payload": {"promise_id": "promise-save", "content": "救回同伴"},
                },
            ]
        },
    )
    closed = _commit(
        tmp_path,
        8,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop-closed",
                    "chapter": 8,
                    "event_type": "open_loop_closed",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-jade", "resolution": "玉佩已经认主"},
                },
                {
                    "event_id": "evt-promise-paid",
                    "chapter": 8,
                    "event_type": "promise_paid_off",
                    "subject": "hero",
                    "payload": {"promise_id": "promise-save", "resolution": "同伴获救"},
                },
            ]
        },
    )

    store = ScratchpadManager(config)
    assert not store.query(category="open_loop", status="active")
    assert not store.query(category="reader_promise", status="active")
    assert store.query(category="open_loop", status="resolved")[0].payload["resolution"] == "玉佩已经认主"
    assert store.query(category="reader_promise", status="resolved")[0].payload["resolution"] == "同伴获救"
    assert closed["projection_status"]["memory"] == "done"

    # A delayed/retried creation projection is idempotent and must not reopen
    # an obligation that a later accepted chapter has already resolved.
    replay_result = MemoryProjectionWriter(tmp_path).apply(created)
    assert replay_result["applied"] is True
    assert not store.query(category="open_loop", status="active")
    assert not store.query(category="reader_promise", status="active")


def test_compaction_tombstone_prevents_resolved_loop_from_reopening(tmp_path):
    config = _prepare_project(tmp_path)
    created = _commit(
        tmp_path,
        2,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop-created",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-jade", "content": "玉佩为何发热"},
                }
            ]
        },
    )
    _commit(
        tmp_path,
        8,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop-closed",
                    "chapter": 8,
                    "event_type": "open_loop_closed",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-jade", "resolution": "玉佩已经认主"},
                }
            ]
        },
    )

    store = ScratchpadManager(config)
    compacted = store.load()
    from data_modules.memory.compactor import compact_scratchpad
    from data_modules.memory.schema import MemoryItem

    compacted.story_facts.append(
        MemoryItem(
            id="soft-filler",
            layer="semantic",
            category="story_fact",
            subject="filler",
            field="filler",
            value="filler",
            source_chapter=9,
        )
    )

    store.save(compact_scratchpad(compacted, max_items=1))
    assert not store.query(category="open_loop", status="active")
    assert not store.query(category="open_loop", status="resolved")
    assert "loop-jade" in store.dump()["meta"]["resolved_lifecycle_ids"]["open_loop"]

    MemoryProjectionWriter(tmp_path).apply(created)
    assert not store.query(category="open_loop", status="active")
    assert "loop-jade" in store.lifecycle_ids("open_loop")


def test_compaction_never_drops_active_lifecycle_constraints(tmp_path):
    config = _prepare_project(tmp_path)
    store = ScratchpadManager(config)
    from data_modules.memory.compactor import compact_scratchpad
    from data_modules.memory.schema import MemoryItem

    data = store.load()
    for index in range(3):
        data.open_loops.append(
            MemoryItem(
                id=f"loop-{index}",
                layer="semantic",
                category="open_loop",
                subject=f"伏笔{index}",
                field="status",
                value=f"伏笔{index}",
                payload={"lifecycle_id": f"loop-{index}"},
                source_chapter=index + 1,
            )
        )
    compacted = compact_scratchpad(data, max_items=1)
    assert {item.payload["lifecycle_id"] for item in compacted.open_loops} == {
        "loop-0",
        "loop-1",
        "loop-2",
    }


def test_compaction_tombstones_legacy_payload_resolved_loop(tmp_path):
    config = _prepare_project(tmp_path)
    from data_modules.memory.compactor import compact_scratchpad
    from data_modules.memory.schema import MemoryItem

    store = ScratchpadManager(config)
    data = store.load()
    legacy = MemoryItem(
        id="legacy-resolved-loop",
        layer="semantic",
        category="open_loop",
        subject="旧回收伏笔",
        field="status",
        value="旧回收伏笔",
        payload={"status": "resolved"},
        status="active",
        source_chapter=2,
    )
    data.open_loops.append(legacy)
    data.story_facts.append(
        MemoryItem(
            id="filler",
            layer="semantic",
            category="story_fact",
            subject="filler",
            field="filler",
            value="filler",
            source_chapter=3,
        )
    )
    store.save(compact_scratchpad(data, max_items=1))
    assert "legacy-resolved-loop" in store.lifecycle_ids("open_loop")
    assert not store.query(category="open_loop", status="active")

    delayed_create = ChapterCommitService(tmp_path).build_commit(
        chapter=2,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "evt-delayed-legacy",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "canonical-after-legacy", "content": "旧回收伏笔"},
                }
            ],
            "state_deltas": [],
            "entity_deltas": [],
        },
    )
    MemoryProjectionWriter(tmp_path).apply(delayed_create)
    assert not store.query(category="open_loop", status="active")
    assert "canonical-after-legacy" in store.lifecycle_ids("open_loop")


def test_legacy_lifecycle_row_has_explicit_target_and_migrates_without_duplicate(tmp_path):
    config = _prepare_project(tmp_path)
    from data_modules.memory.writer import MemoryWriter

    writer = MemoryWriter(config)
    writer.update_from_chapter_result(
        2,
        {"memory_facts": {"open_loops": [{"content": "旧伏笔", "status": "active"}]}},
    )
    adapter = MemoryContractAdapter(config)
    legacy_id = adapter.get_lifecycle_obligations()[0].id

    # Replaying the old accepted creation migrates the exact deterministic
    # legacy row instead of leaving an uncloseable duplicate behind.
    created = _commit(
        tmp_path,
        2,
        {
            "accepted_events": [
                {
                    "event_id": "evt-old",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "evt-old", "content": "旧伏笔"},
                }
            ]
        },
    )
    active = ScratchpadManager(config).query(category="open_loop", status="active")
    assert len(active) == 1
    assert active[0].payload["lifecycle_id"] == "evt-old"

    # The public adapter exposes the exact stable target used by data-agent.
    assert adapter.get_open_loops()[0].id == "evt-old"
    assert adapter.get_lifecycle_obligations()[0].id == "evt-old"
    _commit(
        tmp_path,
        4,
        {
            "accepted_events": [
                {
                    "event_id": "evt-close-old",
                    "chapter": 4,
                    "event_type": "open_loop_closed",
                    "subject": "hero",
                    "payload": {"loop_id": "evt-old", "resolution": "已揭晓"},
                }
            ]
        },
    )
    assert not ScratchpadManager(config).query(category="open_loop", status="active")
    assert created["projection_status"]["memory"] == "done"
    assert legacy_id != "evt-old"


def test_closed_legacy_lifecycle_cannot_be_reopened_by_delayed_canonical_create(tmp_path):
    config = _prepare_project(tmp_path)
    from data_modules.memory.writer import MemoryWriter

    MemoryWriter(config).update_from_chapter_result(
        2,
        {"memory_facts": {"open_loops": [{"content": "旧伏笔", "status": "active"}]}},
    )
    legacy_id = MemoryContractAdapter(config).get_lifecycle_obligations()[0].id
    _commit(
        tmp_path,
        4,
        {
            "accepted_events": [
                {
                    "event_id": "evt-close-legacy",
                    "chapter": 4,
                    "event_type": "open_loop_closed",
                    "subject": "hero",
                    "payload": {"loop_id": legacy_id, "resolution": "已揭晓"},
                }
            ]
        },
    )
    delayed_create = ChapterCommitService(tmp_path).build_commit(
        chapter=2,
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
                    "event_id": "evt-original-create",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "canonical-loop", "content": "旧伏笔"},
                }
            ],
            "state_deltas": [],
            "entity_deltas": [],
        },
    )
    MemoryProjectionWriter(tmp_path).apply(delayed_create)
    store = ScratchpadManager(config)
    assert not store.query(category="open_loop", status="active")
    assert {legacy_id, "canonical-loop"}.issubset(store.lifecycle_ids("open_loop"))


def test_missing_lifecycle_target_fails_memory_without_changing_active_obligations(tmp_path):
    config = _prepare_project(tmp_path)
    _commit(
        tmp_path,
        2,
        {
            "accepted_events": [
                {
                    "event_id": "evt-promise-created",
                    "chapter": 2,
                    "event_type": "promise_created",
                    "subject": "hero",
                    "payload": {"promise_id": "promise-save", "content": "救回同伴"},
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="invalid_consistency_fact:missing_promise_id"):
        _commit(
            tmp_path,
            5,
            {
                "accepted_events": [
                    {
                        "event_id": "evt-promise-paid-without-id",
                        "chapter": 5,
                        "event_type": "promise_paid_off",
                        "subject": "hero",
                        "payload": {"resolution": "同伴获救"},
                    }
                ]
            },
        )

    active = ScratchpadManager(config).query(category="reader_promise", status="active")
    assert [item.payload["lifecycle_id"] for item in active] == ["promise-save"]
    assert not (tmp_path / ".story-system" / "events" / "chapter_005.events.json").exists()
    assert not (tmp_path / ".story-system" / "commits" / "chapter_005.commit.json").exists()


def test_unmatched_lifecycle_target_fails_memory_without_closing_another_obligation(tmp_path):
    config = _prepare_project(tmp_path)
    _commit(
        tmp_path,
        2,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop-created",
                    "chapter": 2,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-jade", "content": "玉佩为何发热"},
                }
            ]
        },
    )

    with pytest.raises(ValueError, match="invalid_consistency_fact:unmatched_open_loop_id:loop-unknown"):
        _commit(
            tmp_path,
            6,
            {
                "accepted_events": [
                    {
                        "event_id": "evt-loop-closed-unknown",
                        "chapter": 6,
                        "event_type": "open_loop_closed",
                        "subject": "hero",
                        "payload": {"loop_id": "loop-unknown", "resolution": "错误目标"},
                    }
                ]
            },
        )

    active = ScratchpadManager(config).query(category="open_loop", status="active")
    assert [item.payload["lifecycle_id"] for item in active] == ["loop-jade"]
    assert not (tmp_path / ".story-system" / "events" / "chapter_006.events.json").exists()


def test_lifecycle_cannot_resolve_before_its_creation_chapter(tmp_path):
    config = _prepare_project(tmp_path)
    _commit(
        tmp_path,
        10,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop-created-late",
                    "chapter": 10,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-late", "content": "第十章伏笔"},
                }
            ]
        },
    )
    with pytest.raises(ValueError, match="lifecycle_resolution_before_creation:loop-late:5<10"):
        _commit(
            tmp_path,
            5,
            {
                "accepted_events": [
                    {
                        "event_id": "evt-loop-closed-early",
                        "chapter": 5,
                        "event_type": "open_loop_closed",
                        "subject": "hero",
                        "payload": {"loop_id": "loop-late", "resolution": "不可能的提前回收"},
                    }
                ]
            },
        )
    active = ScratchpadManager(config).query(category="open_loop", status="active")
    assert [item.payload["lifecycle_id"] for item in active] == ["loop-late"]


def test_state_deltas_sync_nested_entity_current_and_old_retry_cannot_roll_back(tmp_path):
    config = _prepare_project(tmp_path)
    _commit(
        tmp_path,
        1,
        {
            "entity_deltas": [
                {
                    "entity_id": "hero",
                    "entity_type": "角色",
                    "canonical_name": "主角",
                    "tier": "核心",
                }
            ]
        },
    )
    old = _commit(
        tmp_path,
        3,
        {
            "state_deltas": [
                {"entity_id": "hero", "field": "power.realm", "old": "", "new": "炼气"}
            ]
        },
    )
    _commit(
        tmp_path,
        10,
        {
            "state_deltas": [
                {"entity_id": "hero", "field": "power.realm", "old": "炼气", "new": "金丹"}
            ]
        },
    )

    entity = IndexManager(config).get_entity("hero")
    assert entity["current_json"]["power"]["realm"] == "金丹"
    assert "power.realm" not in entity["current_json"]

    StateProjectionWriter(tmp_path).apply(old)
    state = json.loads((tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert state["entity_state"]["hero"]["power"]["realm"] == "金丹"
    assert state["progress"]["current_chapter"] == 10


def test_old_state_retry_cannot_override_newer_entity_current_snapshot(tmp_path):
    config = _prepare_project(tmp_path)
    old = _commit(
        tmp_path,
        3,
        {
            "state_deltas": [
                {"entity_id": "hero", "field": "power.realm", "old": "", "new": "炼气"}
            ]
        },
    )
    _commit(
        tmp_path,
        10,
        {
            "entity_deltas": [
                {
                    "entity_id": "hero",
                    "entity_type": "角色",
                    "canonical_name": "主角",
                    "current": {"power": {"realm": "金丹"}},
                }
            ]
        },
    )

    IndexProjectionWriter(tmp_path).apply(old)
    entity = IndexManager(config).get_entity("hero")
    assert entity["current_json"]["power"]["realm"] == "金丹"


def test_index_current_materializer_preserves_json_scalar_types(tmp_path):
    config = _prepare_project(tmp_path)
    _commit(
        tmp_path,
        3,
        {
            "state_deltas": [
                {"entity_id": "hero", "field": "cooldown", "new": 0},
                {"entity_id": "hero", "field": "sealed", "new": False},
                {"entity_id": "hero", "field": "inventory", "new": ["玉佩"]},
                {"entity_id": "hero", "field": "literal_num", "new": "3"},
                {"entity_id": "hero", "field": "literal_bool", "new": "true"},
                {"entity_id": "hero", "field": "literal_null", "new": "null"},
                {"entity_id": "hero", "field": "actual_null", "new": None},
            ]
        },
    )

    current = IndexManager(config).get_entity("hero")["current_json"]
    assert current["cooldown"] == 0
    assert current["sealed"] is False
    assert current["inventory"] == ["玉佩"]
    assert current["literal_num"] == "3"
    assert current["literal_bool"] == "true"
    assert current["literal_null"] == "null"
    assert current["actual_null"] is None


def test_old_entity_retry_cannot_roll_back_newer_metadata(tmp_path):
    config = _prepare_project(tmp_path)
    old = _commit(
        tmp_path,
        3,
        {
            "entity_deltas": [
                {
                    "entity_id": "hero",
                    "entity_type": "角色",
                    "canonical_name": "旧名",
                    "tier": "装饰",
                    "desc": "旧描述",
                    "is_archived": False,
                }
            ]
        },
    )
    _commit(
        tmp_path,
        10,
        {
            "entity_deltas": [
                {
                    "entity_id": "hero",
                    "entity_type": "角色",
                    "canonical_name": "新名",
                    "tier": "核心",
                    "desc": "新描述",
                    "is_archived": True,
                }
            ]
        },
    )

    IndexProjectionWriter(tmp_path).apply(old)
    entity = IndexManager(config).get_entity("hero")
    assert entity["canonical_name"] == "新名"
    assert entity["tier"] == "核心"
    assert entity["desc"] == "新描述"
    assert entity["last_appearance"] == 10
    assert entity["is_archived"] == 1


def test_retry_only_runs_failed_writer_and_keeps_newer_state(tmp_path, monkeypatch):
    _prepare_project(tmp_path)
    _commit(
        tmp_path,
        1,
        {"entity_deltas": [{"entity_id": "hero", "entity_type": "角色"}]},
    )
    old = _commit(
        tmp_path,
        3,
        {"state_deltas": [{"entity_id": "hero", "field": "realm", "new": "炼气"}]},
    )
    _commit(
        tmp_path,
        10,
        {"state_deltas": [{"entity_id": "hero", "field": "realm", "new": "金丹"}]},
    )

    old["projection_status"]["state"] = "failed:temporary"
    ChapterCommitService(tmp_path).persist_commit(old)

    calls: list[str] = []
    original_state_apply = StateProjectionWriter.apply

    def spy_state(self, payload):
        calls.append("state")
        return original_state_apply(self, payload)

    def fail_if_called(self, payload):  # pragma: no cover - assertion path
        raise AssertionError("retry must not re-run an already completed index writer")

    monkeypatch.setattr(StateProjectionWriter, "apply", spy_state)
    monkeypatch.setattr(IndexProjectionWriter, "apply", fail_if_called)

    report = retry_projection(tmp_path, chapter=3)
    assert report["ok"] is True
    assert calls == ["state"]

    state = json.loads((tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert state["entity_state"]["hero"]["realm"] == "金丹"


def test_retry_recognizes_legacy_plain_failed_status(tmp_path, monkeypatch):
    _prepare_project(tmp_path)
    payload = _commit(
        tmp_path,
        3,
        {
            "accepted_events": [
                {
                    "event_id": "evt-loop",
                    "chapter": 3,
                    "event_type": "open_loop_created",
                    "subject": "hero",
                    "payload": {"loop_id": "loop-legacy-retry", "content": "待解谜题"},
                }
            ]
        },
    )
    payload["projection_status"]["memory"] = "failed"
    ChapterCommitService(tmp_path).persist_commit(payload)

    calls: list[str] = []

    def spy(self, commit_payload):
        calls.append("memory")
        return {"applied": False, "writer": "memory", "reason": "not_required"}

    monkeypatch.setattr(MemoryProjectionWriter, "apply", spy)
    report = retry_projection(tmp_path, chapter=3)
    assert calls == ["memory"]
    assert report["ok"] is True

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.chapter_content_binding import build_chapter_binding  # noqa: E402
from data_modules.projections import replay_projections  # noqa: E402
from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.memory.store import ScratchpadManager  # noqa: E402
from project_memory import add_pattern  # noqa: E402


_DIMENSIONS = ("setting", "timeline", "continuity", "character", "logic")


def _accepted_commit(
    project_root: Path,
    *,
    chapter: int,
    body: str,
    extraction: dict,
) -> dict:
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(body, encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    review = {
        "review_mode": "standard",
        "review_status": "completed",
        "review_skipped": False,
        "review_degraded": False,
        "reviewed_dimensions": list(_DIMENSIONS),
        "skipped_dimensions": [],
        "dimension_results": [
            {"dimension": dimension, "conclusion": "未发现长期一致性冲突。"}
            for dimension in _DIMENSIONS
        ],
        "issues": [],
        "issues_count": 0,
        "blocking_count": 0,
        "has_blocking": False,
        "chapter_binding": dict(binding),
    }
    bound_extraction = {
        "state_deltas": [],
        "entity_deltas": [],
        "accepted_events": [],
        **extraction,
        "chapter_binding": dict(binding),
    }
    service = ChapterCommitService(project_root)
    return service.build_commit(
        chapter=chapter,
        review_result=review,
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": dict(binding),
        },
        disambiguation_result={"pending": [], "chapter_binding": dict(binding)},
        extraction_result=bound_extraction,
    )


def _commit_and_project(project_root: Path, payload: dict) -> dict:
    service = ChapterCommitService(project_root)
    service.persist_commit(payload)
    return service.apply_projections(payload)


def test_same_chapter_revision_removes_old_facts_and_keeps_init_metadata(tmp_path):
    state_path = tmp_path / ".webnovel" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "project_info": {"title": "北城旧案", "genre": "悬疑"},
                "world_settings": {"locations": ["北城"]},
                "progress": {"current_volume": 2, "volumes_planned": [2]},
                "protagonist_state": {"name": "沈砚", "attributes": {"身份": "仵作"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    first = _accepted_commit(
        tmp_path,
        chapter=1,
        body="沈砚在旧库房发现染血账簿。",
        extraction={
            "state_deltas": [
                {"entity_id": "沈砚", "field": "location.current", "new": "旧库房"}
            ],
            "entity_deltas": [
                {
                    "entity_id": "沈砚",
                    "canonical_name": "沈砚",
                    "entity_type": "角色",
                    "tier": "主角",
                    "is_protagonist": True,
                },
                {
                    "entity_id": "染血账簿",
                    "canonical_name": "染血账簿",
                    "entity_type": "物品",
                },
            ],
            "accepted_events": [
                {
                    "event_id": "线索-染血账簿",
                    "event_type": "open_loop_created",
                    "chapter": 1,
                    "subject": "染血账簿",
                    "payload": {
                        "loop_id": "待查-染血账簿",
                        "description": "染血账簿从何而来仍待查明。",
                    },
                }
            ],
            "scenes": [
                {
                    "scene_index": 1,
                    "location": "旧库房",
                    "summary": "沈砚找到染血账簿。",
                    "characters": ["沈砚"],
                }
            ],
            "summary_text": "沈砚在旧库房找到染血账簿。",
        },
    )
    projected_first = _commit_and_project(tmp_path, first)
    assert projected_first["projection_status"]["state"] == "done"

    revised = _accepted_commit(
        tmp_path,
        chapter=1,
        body="沈砚改在渡口确认失踪船票的去向。",
        extraction={
            "state_deltas": [
                {"entity_id": "沈砚", "field": "location.current", "new": "北城渡口"}
            ],
            "entity_deltas": [
                {
                    "entity_id": "沈砚",
                    "canonical_name": "沈砚",
                    "entity_type": "角色",
                    "tier": "主角",
                    "is_protagonist": True,
                }
            ],
            "accepted_events": [],
            "scenes": [
                {
                    "scene_index": 1,
                    "location": "北城渡口",
                    "summary": "沈砚确认船票去向。",
                    "characters": ["沈砚"],
                }
            ],
            "summary_text": "沈砚在北城渡口确认船票去向。",
        },
    )
    projected = _commit_and_project(tmp_path, revised)
    assert projected["projection_status"]["state"] == "done"

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["project_info"] == {"title": "北城旧案", "genre": "悬疑"}
    assert state["world_settings"] == {"locations": ["北城"]}
    assert state["progress"]["current_volume"] == 2
    assert state["protagonist_state"]["name"] == "沈砚"
    assert state["protagonist_state"]["attributes"] == {"身份": "仵作"}
    assert state["protagonist_state"]["location"]["current"] == "北城渡口"

    event_file = tmp_path / ".story-system" / "events" / "chapter_001.events.json"
    assert json.loads(event_file.read_text(encoding="utf-8")) == []
    scratch_path = tmp_path / ".webnovel" / "memory_scratchpad.json"
    if scratch_path.exists():
        assert "染血账簿" not in scratch_path.read_text(encoding="utf-8")
    summary = (tmp_path / ".webnovel" / "summaries" / "ch0001.md").read_text(encoding="utf-8")
    assert "北城渡口" in summary
    assert "旧库房" not in summary

    with sqlite3.connect(tmp_path / ".webnovel" / "index.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM story_events").fetchone()[0] == 0
        scenes = conn.execute("SELECT location, summary FROM scenes").fetchall()
        assert scenes == [("北城渡口", "沈砚确认船票去向。")]
        changes = conn.execute(
            "SELECT new_value FROM state_changes WHERE entity_id = '沈砚'"
        ).fetchall()
        assert len(changes) == 1
        assert "北城渡口" in changes[0][0]
        assert conn.execute(
            "SELECT COUNT(*) FROM entities WHERE id = '染血账簿'"
        ).fetchone()[0] == 0
    vector_path = tmp_path / ".webnovel" / "vectors.db"
    if vector_path.is_file():
        with sqlite3.connect(vector_path) as conn:
            vector_text = "\n".join(
                str(row[0] or "") for row in conn.execute("SELECT content FROM vectors")
            )
        assert "染血账簿" not in vector_text


def test_replay_rebuilds_deleted_read_models_even_when_commit_says_done(tmp_path):
    payload = _accepted_commit(
        tmp_path,
        chapter=1,
        body="顾青把铜钥匙交给守门人保管。",
        extraction={
            "state_deltas": [
                {"entity_id": "铜钥匙", "field": "holder", "new": "守门人"}
            ],
            "entity_deltas": [
                {"entity_id": "铜钥匙", "canonical_name": "铜钥匙", "entity_type": "物品"}
            ],
            "accepted_events": [
                {
                    "event_id": "物品-铜钥匙",
                    "event_type": "artifact_obtained",
                    "chapter": 1,
                    "subject": "铜钥匙",
                    "payload": {"artifact_id": "铜钥匙", "holder": "守门人"},
                }
            ],
            "summary_text": "铜钥匙交由守门人保管。",
        },
    )
    _commit_and_project(tmp_path, payload)

    for relative in (
        ".webnovel/state.json",
        ".webnovel/index.db",
        ".webnovel/vectors.db",
        ".webnovel/memory_scratchpad.json",
        ".story-system/events/chapter_001.events.json",
    ):
        (tmp_path / relative).unlink(missing_ok=True)
    shutil.rmtree(tmp_path / ".webnovel" / "summaries")

    report = replay_projections(tmp_path, start_chapter=1, end_chapter=1)

    assert report["ok"] is True
    assert (tmp_path / ".webnovel" / "state.json").is_file()
    assert (tmp_path / ".webnovel" / "index.db").is_file()
    assert (tmp_path / ".webnovel" / "summaries" / "ch0001.md").is_file()
    assert (tmp_path / ".story-system" / "events" / "chapter_001.events.json").is_file()
    with sqlite3.connect(tmp_path / ".webnovel" / "index.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM story_events").fetchone()[0] == 1


def test_event_rewrite_replaces_same_chapter_sqlite_rows(tmp_path):
    from data_modules.event_log_store import EventLogStore

    store = EventLogStore(tmp_path)
    store.write_events(
        2,
        [
            {
                "event_id": "旧线索",
                "event_type": "open_loop_created",
                "chapter": 2,
                "subject": "旧木匣",
                "payload": {"loop_id": "旧木匣", "description": "旧木匣尚未打开。"},
            }
        ],
    )
    store.write_events(
        2,
        [
            {
                "event_id": "新线索",
                "event_type": "open_loop_created",
                "chapter": 2,
                "subject": "断裂船桨",
                "payload": {"loop_id": "断裂船桨", "description": "船桨断裂原因待查。"},
            }
        ],
    )

    assert [event["event_id"] for event in store.read_events(2)] == ["新线索"]
    with sqlite3.connect(tmp_path / ".webnovel" / "index.db") as conn:
        assert conn.execute("SELECT event_id FROM story_events").fetchall() == [("新线索",)]


def test_failed_stage_does_not_install_half_rebuilt_read_models(tmp_path, monkeypatch):
    first = _accepted_commit(
        tmp_path,
        chapter=1,
        body="许舟留在南岸客栈等候消息。",
        extraction={
            "state_deltas": [
                {"entity_id": "许舟", "field": "location", "new": "南岸客栈"}
            ],
            "entity_deltas": [
                {"entity_id": "许舟", "canonical_name": "许舟", "entity_type": "角色"}
            ],
            "summary_text": "许舟在南岸客栈等候消息。",
        },
    )
    _commit_and_project(tmp_path, first)
    old_state = (tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8")
    old_summary = (tmp_path / ".webnovel" / "summaries" / "ch0001.md").read_text(encoding="utf-8")

    revised = _accepted_commit(
        tmp_path,
        chapter=1,
        body="许舟已经渡河抵达北岸码头。",
        extraction={
            "state_deltas": [
                {"entity_id": "许舟", "field": "location", "new": "北岸码头"}
            ],
            "entity_deltas": [
                {"entity_id": "许舟", "canonical_name": "许舟", "entity_type": "角色"}
            ],
            "summary_text": "许舟抵达北岸码头。",
        },
    )

    monkeypatch.setattr(
        "data_modules.summary_projection_writer.SummaryProjectionWriter.apply",
        lambda self, payload: {
            "applied": False,
            "writer": "summary",
            "reason": "error:模拟摘要投影失败",
        },
    )
    projected = _commit_and_project(tmp_path, revised)

    assert projected["projection_status"]["summary"].startswith("failed:")
    assert (tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8") == old_state
    assert (tmp_path / ".webnovel" / "summaries" / "ch0001.md").read_text(encoding="utf-8") == old_summary
    assert "北岸码头" not in old_summary


def test_rewriting_history_keeps_later_canonical_chapter_as_current_head(tmp_path):
    chapter_one = _accepted_commit(
        tmp_path,
        chapter=1,
        body="周宁从柜中取出一柄短刀。",
        extraction={
            "state_deltas": [
                {"entity_id": "周宁", "field": "weapon", "new": "短刀"}
            ],
            "entity_deltas": [
                {"entity_id": "周宁", "canonical_name": "周宁", "entity_type": "角色"}
            ],
        },
    )
    _commit_and_project(tmp_path, chapter_one)
    chapter_two = _accepted_commit(
        tmp_path,
        chapter=2,
        body="周宁在追逐中丢失兵器，只能空手返回。",
        extraction={
            "state_deltas": [
                {"entity_id": "周宁", "field": "weapon", "new": "无"}
            ],
            "entity_deltas": [
                {"entity_id": "周宁", "canonical_name": "周宁", "entity_type": "角色"}
            ],
        },
    )
    _commit_and_project(tmp_path, chapter_two)

    revised_one = _accepted_commit(
        tmp_path,
        chapter=1,
        body="周宁从柜中取出一柄长剑。",
        extraction={
            "state_deltas": [
                {"entity_id": "周宁", "field": "weapon", "new": "长剑"}
            ],
            "entity_deltas": [
                {"entity_id": "周宁", "canonical_name": "周宁", "entity_type": "角色"}
            ],
        },
    )
    _commit_and_project(tmp_path, revised_one)

    state = json.loads((tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert state["entity_state"]["周宁"]["weapon"] == "无"
    with sqlite3.connect(tmp_path / ".webnovel" / "index.db") as conn:
        values = [
            str(row[0])
            for row in conn.execute(
                "SELECT new_value FROM state_changes WHERE entity_id = '周宁' ORDER BY chapter"
            )
        ]
    assert len(values) == 2
    assert "长剑" in values[0]
    assert "无" in values[1]
    assert all("短刀" not in value for value in values)


def test_full_replay_keeps_author_confirmed_consistency_rule(tmp_path):
    payload = _accepted_commit(
        tmp_path,
        chapter=1,
        body="沈砚在霜月初三离开北城。",
        extraction={"summary_text": "沈砚离开北城。"},
    )
    _commit_and_project(tmp_path, payload)
    rule = "离开北城后的时间锚必须晚于霜月初三。"
    add_pattern(
        tmp_path,
        pattern_type="timeline",
        description=rule,
        source_chapter=1,
    )

    report = replay_projections(tmp_path, start_chapter=1, end_chapter=1)

    assert report["ok"] is True
    rows = ScratchpadManager(
        DataModulesConfig.from_project_root(tmp_path)
    ).query(category="world_rule", status="active")
    assert any(item.value == rule for item in rows)

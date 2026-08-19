#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""默认写作上下文的结构化 canon 与历史时点回归测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.config import DataModulesConfig
from data_modules.memory_contract_adapter import MemoryContractAdapter
from data_modules.story_contracts import synchronize_setting_canon
from init_project import init_project
from style_memory import add_style_items
from .review_test_helpers import inject_hard_evidence_quotes


_DIMENSIONS = ("setting", "timeline", "continuity", "character", "logic")


def _write_contracts(project_root: Path, *chapters: int) -> None:
    story_root = project_root / ".story-system"
    (story_root / "volumes").mkdir(parents=True, exist_ok=True)
    (story_root / "chapters").mkdir(parents=True, exist_ok=True)
    (story_root / "reviews").mkdir(parents=True, exist_ok=True)
    (story_root / "commits").mkdir(parents=True, exist_ok=True)
    (story_root / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "MASTER_SETTING",
                },
                "initial_canon": {
                    "project": {"title": "北城旧案", "genre": "悬疑"},
                    "protagonist": {"name": "沈砚", "desire": "查清旧案"},
                    "world": {"factions": "巡检司与盐帮并立"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (story_root / "volumes" / "volume_001.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "VOLUME_BRIEF",
                    "volume": 1,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for chapter in chapters:
        (story_root / "chapters" / f"chapter_{chapter:03d}.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "schema_version": "story-system/v1",
                        "contract_type": "CHAPTER_BRIEF",
                        "chapter": chapter,
                    },
                    "chapter_directive": {
                        "goal": f"推进第{chapter}章调查",
                        "must_cover_nodes": [],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (story_root / "reviews" / f"chapter_{chapter:03d}.review.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "schema_version": "story-system/v1",
                        "contract_type": "REVIEW_CONTRACT",
                        "chapter": chapter,
                    },
                    "must_check": [],
                    "blocking_rules": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _accepted_commit(
    project_root: Path,
    chapter: int,
    extraction: dict,
    *,
    project: bool = True,
) -> dict:
    extraction, chapter_text = inject_hard_evidence_quotes(
        copy.deepcopy(extraction),
        chapter=chapter,
        chapter_text=f"第{chapter}章中文正文。\n",
    )
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(chapter_text, encoding="utf-8")
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
    payload = ChapterCommitService(project_root).build_commit(
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
        extraction_result={
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            **extraction,
            "chapter_binding": dict(binding),
        },
    )
    service = ChapterCommitService(project_root)
    service.persist_commit(payload)
    return service.apply_projections(payload) if project else payload


def test_real_init_puts_chinese_setup_facts_into_first_context(tmp_path, monkeypatch):
    monkeypatch.setattr("init_project.is_git_available", lambda: False)
    init_project(
        str(tmp_path),
        "雾港来信",
        "悬疑",
        protagonist_name="林舟",
        protagonist_desire="查明姐姐失踪真相",
        protagonist_flaw="过度相信旧友",
        world_scale="一座封闭港城",
        factions="巡夜司、商会与码头帮派",
        power_system_type="梦境可留下实体伤痕",
        gf_visibility="只有林舟能看见潮汐刻度",
        gf_irreversible_cost="每次使用都会遗忘一段童年",
        golden_finger_style="冷峻短句",
    )

    pack = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).load_context(1, budget_tokens=20_000)
    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)

    assert pack.completeness["status"] == "complete"
    assert "查明姐姐失踪真相" in serialized
    assert "巡夜司、商会与码头帮派" in serialized
    assert "每次使用都会遗忘一段童年" in serialized
    assert "冷峻短句" not in serialized


def test_planned_setting_writeback_enters_canon_and_stale_snapshot_blocks(tmp_path):
    """规划写回的长期设定必须进入 canon；设定变更后不得误报完整。"""
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1)
    settings_dir = tmp_path / "设定集"
    settings_dir.mkdir()
    (settings_dir / "世界观.md").write_text(
        "# 世界观\n\n## 核心规则\n- 硬约束：潮汐退去前，雾港城门不得开启。\n",
        encoding="utf-8",
    )
    (settings_dir / "文风提示词.md").write_text(
        "# 文风提示词\n\n冷峻短句，减少修饰。\n",
        encoding="utf-8",
    )
    synchronize_setting_canon(tmp_path)

    adapter = MemoryContractAdapter(DataModulesConfig.from_project_root(tmp_path))
    first_pack = adapter.load_context(1, budget_tokens=20_000)
    first_serialized = json.dumps(first_pack.to_dict(), ensure_ascii=False)

    assert first_pack.completeness["status"] == "complete"
    assert "潮汐退去前，雾港城门不得开启" in first_serialized
    assert "冷峻短句" not in first_serialized

    (settings_dir / "文风提示词.md").write_text(
        "# 文风提示词\n\n改用舒缓长句。\n",
        encoding="utf-8",
    )
    style_changed_pack = adapter.load_context(1, budget_tokens=20_000)
    style_changed_serialized = json.dumps(style_changed_pack.to_dict(), ensure_ascii=False)

    assert style_changed_pack.completeness["status"] == "complete"
    assert "舒缓长句" not in style_changed_serialized

    (settings_dir / "世界观.md").write_text(
        "# 世界观\n\n## 核心规则\n"
        "- 硬约束：潮汐退去前，雾港城门不得开启。\n"
        "- 通行规则：持黑铜令者只能从北门入城。\n",
        encoding="utf-8",
    )
    stale_pack = adapter.load_context(1, budget_tokens=20_000)

    assert stale_pack.completeness["status"] == "blocked"
    assert "stale_setting_canon" in stale_pack.completeness["missing_sources"]


def test_default_context_replays_state_timeline_story_fact_and_question_loop(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1, 2)
    _accepted_commit(
        tmp_path,
        1,
        {
            "entity_deltas": [
                {"entity_id": "linzhou", "canonical_name": "林舟", "entity_type": "角色"}
            ],
            "state_deltas": [
                {"entity_id": "linzhou", "field": "location", "new": "旧码头"}
            ],
            "timeline_events": [
                {
                    "timeline_id": "发现铜铃",
                    "sequence": 1,
                    "event": "林舟在子时发现染血铜铃",
                    "time_hint": "第一日子时",
                },
                {
                    "timeline_id": "铜铃来源",
                    "sequence": 2,
                    "event": "铜铃来自十年前封存的沉船",
                    "time_hint": "十年前",
                },
            ],
            "accepted_events": [
                {
                    "event_id": "未解账簿",
                    "chapter": 1,
                    "event_type": "open_loop_created",
                    "subject": "linzhou",
                    "payload": {
                        "loop_id": "账簿去向",
                        "unanswered_question": "真正的账簿藏在哪里？",
                    },
                },
                {
                    "event_id": "获得铜铃",
                    "chapter": 1,
                    "event_type": "artifact_obtained",
                    "subject": "铜铃",
                    "payload": {"name": "染血铜铃", "owner": "林舟"},
                },
            ],
        },
    )

    pack = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).load_context(2, budget_tokens=20_000)
    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)

    assert pack.completeness["status"] == "complete"
    assert "旧码头" in serialized
    assert "林舟在子时发现染血铜铃" in serialized
    assert "林舟获得染血铜铃" in serialized
    assert "铜铃来自十年前封存的沉船" in serialized
    assert "真正的账簿藏在哪里？" in serialized
    assert pack.sections["runtime_status"]["history_as_of_chapter"] == 1


def test_learned_style_is_not_a_hard_constraint_or_scratchpad_fact(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_contracts(tmp_path, 1, 2)
    _accepted_commit(tmp_path, 1, {})
    description = "对白更口语化，少用排比。"
    result = add_style_items(tmp_path, [description])

    pack = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).load_context(2, budget_tokens=20_000)
    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)

    assert result["status"] == "success"
    assert description in (tmp_path / "设定集" / "文风提示词.md").read_text(encoding="utf-8")
    assert pack.completeness["status"] == "complete"
    assert description not in serialized
    assert not any(
        item.get("value") == description
        for item in pack.sections.get("hard_constraints") or []
    )
    assert not (tmp_path / ".canon-ledger" / "memory_scratchpad.json").exists()


def test_historical_context_and_supplementary_queries_never_read_future_commit(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1, 2, 3)
    _accepted_commit(
        tmp_path,
        1,
        {
            "entity_deltas": [
                {"entity_id": "linzhou", "canonical_name": "林舟", "entity_type": "角色"}
            ],
            "state_deltas": [
                {"entity_id": "linzhou", "field": "location", "new": "北城"}
            ],
            "accepted_events": [
                {
                    "event_id": "旧井疑问",
                    "chapter": 1,
                    "event_type": "open_loop_created",
                    "subject": "linzhou",
                    "payload": {
                        "loop_id": "旧井来信",
                        "unanswered_question": "是谁从封死的旧井寄出信件？",
                    },
                }
            ],
        },
    )
    _accepted_commit(
        tmp_path,
        2,
        {
            "state_deltas": [
                {
                    "entity_id": "linzhou",
                    "field": "location",
                    "old": "北城",
                    "new": "南港",
                }
            ],
            "accepted_events": [
                {
                    "event_id": "旧井答案",
                    "chapter": 2,
                    "event_type": "open_loop_closed",
                    "subject": "linzhou",
                    "payload": {"loop_id": "旧井来信", "resolution": "信件由暗渠送出"},
                }
            ],
        },
    )

    adapter = MemoryContractAdapter(DataModulesConfig.from_project_root(tmp_path))
    old_pack = adapter.load_context(2, budget_tokens=20_000)
    new_pack = adapter.load_context(3, budget_tokens=20_000)
    old_text = json.dumps(old_pack.to_dict(), ensure_ascii=False)
    new_text = json.dumps(new_pack.to_dict(), ensure_ascii=False)
    old_entity = adapter.query_entity("linzhou", as_of_chapter=1)
    new_entity = adapter.query_entity("linzhou", as_of_chapter=2)

    assert "北城" in old_text
    assert "南港" not in old_text
    assert "是谁从封死的旧井寄出信件？" in old_text
    assert "是谁从封死的旧井寄出信件？" not in new_text
    assert old_entity is not None and old_entity.attributes["location"] == "北城"
    assert new_entity is not None and new_entity.attributes["location"] == "南港"


def test_asof_replays_knowledge_physical_presence_and_custody(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1, 2, 3)
    full_coverage = {
        "knowledge": "complete",
        "presence": "complete",
        "custody": "complete",
    }
    _accepted_commit(
        tmp_path,
        1,
        {
            "fact_coverage": full_coverage,
            "accepted_events": [
                {
                    "event_id": "linzhou-learns-door",
                    "chapter": 1,
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
                },
                {
                    "event_id": "linzhou-at-north-city",
                    "chapter": 1,
                    "sequence": 2,
                    "event_type": "presence_observed",
                    "subject": "linzhou",
                    "payload": {
                        "location_id": "north-city",
                        "scene_index": 1,
                        "presence_kind": "physical",
                        "time_anchor": "第一日子时",
                        "transition_explicit": True,
                        "evidence_quote": "子时，林舟抵达北城。",
                    },
                },
                {
                    "event_id": "baizhi-dreams-south-port",
                    "chapter": 1,
                    "sequence": 3,
                    "event_type": "presence_observed",
                    "subject": "baizhi",
                    "payload": {
                        "location_id": "south-port",
                        "scene_index": 2,
                        "presence_kind": "dream",
                        "evidence_quote": "白芷梦见自己站在南港。",
                    },
                },
                {
                    "event_id": "linzhou-takes-key",
                    "chapter": 1,
                    "sequence": 4,
                    "event_type": "custody_changed",
                    "subject": "bronze-key",
                    "payload": {
                        "from_holder": "",
                        "to_holder": "linzhou",
                        "location_id": "north-city",
                        "evidence_quote": "林舟拾起铜钥匙，收进袖中。",
                    },
                },
            ],
        },
    )

    adapter = MemoryContractAdapter(DataModulesConfig.from_project_root(tmp_path))
    chapter_two = adapter.export_asof_snapshot(chapter=2)

    assert chapter_two["schema_version"] == "canon-ledger-asof-snapshot/v3"
    assert chapter_two["coverage"] == full_coverage
    assert chapter_two["verification"] == {
        "knowledge": "verified",
        "presence": "verified",
        "custody": "verified",
    }
    assert chapter_two["information"]["clocktower-secret-door"]["content"] == "密门在钟楼下"
    assert chapter_two["knowledge_by_entity"]["linzhou"]["clocktower-secret-door"]["state"] == "known"
    assert chapter_two["presence"]["linzhou"]["location_id"] == "north-city"
    assert "baizhi" not in chapter_two["presence"]
    assert any(
        row["entity_id"] == "baizhi" and row["presence_kind"] == "dream"
        for row in chapter_two["presence_history"]
    )
    assert chapter_two["custody"]["bronze-key"]["holder_id"] == "linzhou"

    _accepted_commit(
        tmp_path,
        2,
        {
            "fact_coverage": full_coverage,
            "accepted_events": [
                {
                    "event_id": "baizhi-learns-door",
                    "chapter": 2,
                    "sequence": 1,
                    "event_type": "knowledge_state_changed",
                    "subject": "baizhi",
                    "payload": {
                        "information_id": "clocktower-secret-door",
                        # 与第 1 章既往表述一致：这里验证同一信息扩散给新实体，
                        # 表述不一致的场景由 information_id 冲突专测覆盖。
                        "canonical_claim": "密门在钟楼下",
                        "evidence_fragment": "密门就在钟楼下面",
                        "state": "known",
                        "source_kind": "told",
                        "source_entity": "linzhou",
                        "evidence_quote": "林舟告诉白芷：密门就在钟楼下面。",
                    },
                },
                {
                    "event_id": "linzhou-at-south-port",
                    "chapter": 2,
                    "sequence": 2,
                    "event_type": "presence_observed",
                    "subject": "linzhou",
                    "payload": {
                        "location_id": "south-port",
                        "scene_index": 1,
                        "presence_kind": "physical",
                        "transition_explicit": True,
                        "evidence_quote": "翌日，林舟乘船抵达南港。",
                    },
                },
                {
                    "event_id": "key-to-baizhi",
                    "chapter": 2,
                    "sequence": 3,
                    "event_type": "custody_changed",
                    "subject": "bronze-key",
                    "payload": {
                        "from_holder": "linzhou",
                        "to_holder": "baizhi",
                        "location_id": "south-port",
                        "evidence_quote": "林舟把铜钥匙交给白芷。",
                    },
                },
            ],
        },
    )

    still_chapter_two = adapter.export_asof_snapshot(chapter=2)
    chapter_three = adapter.export_asof_snapshot(chapter=3)
    assert "baizhi" not in still_chapter_two["knowledge_by_entity"]
    assert still_chapter_two["presence"]["linzhou"]["location_id"] == "north-city"
    assert still_chapter_two["custody"]["bronze-key"]["holder_id"] == "linzhou"
    assert chapter_three["knowledge_by_entity"]["baizhi"]["clocktower-secret-door"]["state"] == "known"
    assert chapter_three["presence"]["linzhou"]["location_id"] == "south-port"
    assert chapter_three["custody"]["bronze-key"]["holder_id"] == "baizhi"

    context = adapter.load_context(3, budget_tokens=20_000).to_dict()["sections"]
    assert context["knowledge"]["by_entity"]["baizhi"]["clocktower-secret-door"]["state"] == "known"
    assert context["presence"]["current"]["linzhou"]["location_id"] == "south-port"
    assert context["custody"]["current"]["bronze-key"]["holder_id"] == "baizhi"
    assert context["fact_coverage"] == full_coverage
    assert context["fact_verification"] == {
        "knowledge": "verified",
        "presence": "verified",
        "custody": "verified",
    }


def test_consistency_replay_uses_sequence_instead_of_array_order(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text(
        "{}", encoding="utf-8"
    )
    _write_contracts(tmp_path, 1, 2)
    _accepted_commit(
        tmp_path,
        1,
        {
            "fact_coverage": {
                "knowledge": "complete",
                "presence": "complete",
                "custody": "complete",
            },
            # Deliberately reverse the causal order in the JSON array.
            "accepted_events": [
                {
                    "event_id": "alice-at-south",
                    "chapter": 1,
                    "sequence": 2,
                    "event_type": "presence_observed",
                    "subject": "alice",
                    "payload": {
                        "location_id": "south-port",
                        "presence_kind": "physical",
                        "evidence_quote": "随后，爱丽丝抵达南港。",
                    },
                },
                {
                    "event_id": "key-to-carol",
                    "chapter": 1,
                    "sequence": 4,
                    "event_type": "custody_changed",
                    "subject": "bronze-key",
                    "payload": {
                        "from_holder": "bob",
                        "to_holder": "carol",
                        "evidence_quote": "鲍勃把铜钥匙交给卡萝。",
                    },
                },
                {
                    "event_id": "alice-at-north",
                    "chapter": 1,
                    "sequence": 1,
                    "event_type": "presence_observed",
                    "subject": "alice",
                    "payload": {
                        "location_id": "north-city",
                        "presence_kind": "physical",
                        "evidence_quote": "爱丽丝先抵达北城。",
                    },
                },
                {
                    "event_id": "key-to-bob",
                    "chapter": 1,
                    "sequence": 3,
                    "event_type": "custody_changed",
                    "subject": "bronze-key",
                    "payload": {
                        "from_holder": "alice",
                        "to_holder": "bob",
                        "evidence_quote": "爱丽丝把铜钥匙交给鲍勃。",
                    },
                },
            ],
        },
    )

    snapshot = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).export_asof_snapshot(chapter=2)

    assert snapshot["presence"]["alice"]["location_id"] == "south-port"
    assert snapshot["custody"]["bronze-key"]["holder_id"] == "carol"


def test_legacy_commits_report_partial_long_term_fact_coverage(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1, 2)
    _accepted_commit(tmp_path, 1, {})

    snapshot = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).export_asof_snapshot(chapter=2)

    assert snapshot["coverage"] == {
        "knowledge": "partial",
        "presence": "partial",
        "custody": "partial",
    }


def test_invalid_bound_commit_is_omitted_and_blocks_context(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1, 2)
    _accepted_commit(
        tmp_path,
        1,
        {
            "accepted_events": [
                {
                    "event_id": "旧规则",
                    "chapter": 1,
                    "event_type": "world_rule_revealed",
                    "subject": "港城",
                    "payload": {
                        "rule_content": "宵禁后不得点燃蓝灯",
                        "rule_category": "制度",
                        "domain": "港城",
                        "field": "宵禁照明限制",
                    },
                }
            ]
        },
    )
    (tmp_path / "正文" / "第0001章.md").write_text("正文已经被修改。", encoding="utf-8")

    pack = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).load_context(2, budget_tokens=20_000)
    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)

    assert pack.completeness["status"] == "blocked"
    assert "chapter_content_hash_mismatch" in serialized
    assert "宵禁后不得点燃蓝灯" not in serialized


def test_story_structure_recipe_cannot_be_promoted_to_world_rule(tmp_path):
    (tmp_path / ".canon-ledger").mkdir(parents=True)
    (tmp_path / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
    _write_contracts(tmp_path, 1, 2)
    with pytest.raises(ValueError, match="世界规则缺少受控类别"):
        _accepted_commit(
            tmp_path,
            1,
            {
                "accepted_events": [
                    {
                        "event_id": "伪装成规则的章法配方",
                        "chapter": 1,
                        "event_type": "world_rule_revealed",
                        "subject": "故事",
                        "payload": {
                            "rule_content": "每隔三次场景转换安排一个意外",
                            "rule_category": "制度",
                            "domain": "故事",
                            "field": "场景转换",
                        },
                    }
                ]
            },
        )
    with pytest.raises(ValueError, match="世界规则缺少受控类别"):
        _accepted_commit(
            tmp_path,
            1,
            {
                "accepted_events": [
                    {
                        "event_id": "伪造故事内制度的套路",
                        "chapter": 1,
                        "event_type": "world_rule_revealed",
                        "subject": "潮汐法典",
                        "payload": {
                            "rule_content": "故事每推进一回，都以突发变故收束",
                            "rule_category": "制度",
                            "domain": "潮汐法典",
                            "field": "触发条件",
                        },
                    }
                ]
            },
        )
    _accepted_commit(
        tmp_path,
        1,
        {
            "accepted_events": [
                {
                    "event_id": "港城宵禁铁律",
                    "chapter": 1,
                    "event_type": "world_rule_revealed",
                    "subject": "港城",
                    "payload": {
                        "rule_content": "宵禁后不得点燃蓝灯",
                        "rule_category": "制度",
                        "domain": "港城",
                        "field": "宵禁照明限制",
                    },
                }
            ]
        },
    )

    pack = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).load_context(2, budget_tokens=20_000)
    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)

    assert "每章末尾必须出现一个意外" not in serialized
    assert "宵禁后不得点燃蓝灯" in serialized
    assert pack.completeness["status"] == "complete"


def test_missing_contracts_fail_closed_without_creating_read_models(tmp_path):
    config = DataModulesConfig.from_project_root(tmp_path)
    pack = MemoryContractAdapter(config).load_context(1)

    assert pack.completeness["status"] == "blocked"
    assert set(pack.completeness["missing_sources"]) >= {
        "missing_master_contract",
        "missing_volume_contract",
        "missing_chapter_contract",
        "missing_review_contract",
    }
    assert not config.index_db.exists()
    assert not config.vector_db.exists()


def test_query_entity_reads_bound_commit_entity_shape(tmp_path):
    config = DataModulesConfig.from_project_root(tmp_path)
    _write_contracts(tmp_path, 1)
    _accepted_commit(
        tmp_path,
        1,
        {
            "entity_deltas": [
                {"entity_id": "linzhou", "canonical_name": "林舟", "entity_type": "角色", "tier": "重要"}
            ],
            "state_deltas": [
                {"entity_id": "linzhou", "field": "location", "new": "旧码头"},
                {"entity_id": "linzhou", "field": "injured", "new": True},
            ],
        },
    )

    snapshot = MemoryContractAdapter(config).query_entity("linzhou")

    assert snapshot is not None
    assert snapshot.name == "林舟"
    assert snapshot.attributes == {"location": "旧码头", "injured": True}
    assert "current_json" not in snapshot.attributes, "查询结果不得暴露旧投影字段"

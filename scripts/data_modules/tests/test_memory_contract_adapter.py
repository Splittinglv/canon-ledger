#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MemoryContractAdapter 集成测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# 确保 scripts/ 在 sys.path 中
_scripts_dir = str(Path(__file__).resolve().parent.parent.parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from data_modules.config import DataModulesConfig
from data_modules.memory_contract import (
    CommitResult,
    ContextPack,
    EntitySnapshot,
    MemoryContract,
    OpenLoop,
    Rule,
    TimelineEvent,
)
from data_modules.memory_contract_adapter import MemoryContractAdapter


def _make_project(tmp_path: Path) -> DataModulesConfig:
    """创建最小项目结构并返回配置。"""
    webnovel_dir = tmp_path / ".webnovel"
    webnovel_dir.mkdir(parents=True, exist_ok=True)
    (webnovel_dir / "state.json").write_text("{}", encoding="utf-8")
    (webnovel_dir / "summaries").mkdir(exist_ok=True)
    return DataModulesConfig.from_project_root(tmp_path)


class TestAdapterSatisfiesProtocol:
    def test_isinstance_check(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert isinstance(adapter, MemoryContract)


class TestReadSummary:
    def test_read_existing_summary(self, tmp_path):
        cfg = _make_project(tmp_path)
        summary_dir = cfg.webnovel_dir / "summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "ch0010.md").write_text("第10章摘要", encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        text = adapter.read_summary(10)
        assert text == "第10章摘要"

    def test_read_missing_summary(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.read_summary(999) == ""


class TestQueryEntity:
    def test_query_nonexistent_entity(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.query_entity("nobody") is None

    def test_query_existing_entity(self, tmp_path):
        cfg = _make_project(tmp_path)
        # 写入包含实体的 state.json
        state = {
            "entities_v3": {
                "角色": {
                    "xiaoyan": {
                        "name": "萧炎",
                        "tier": "核心",
                        "aliases": ["他"],
                        "realm": "斗帝",
                        "first_appearance": 1,
                        "last_appearance": 100,
                    }
                }
            },
            "state_changes": [
                {"entity_id": "xiaoyan", "field": "realm", "old": "斗圣", "new": "斗帝", "chapter": 100}
            ],
        }
        (cfg.state_file).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        snap = adapter.query_entity("xiaoyan")
        assert snap is not None
        assert snap.name == "萧炎"
        assert snap.type == "角色"
        assert snap.tier == "核心"
        assert "他" in snap.aliases
        assert len(snap.recent_state_changes) == 1


class TestQueryRules:
    def test_query_rules_empty(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.query_rules() == []

    def test_query_rules_with_data(self, tmp_path):
        cfg = _make_project(tmp_path)
        # 写入 scratchpad 数据
        from data_modules.memory.schema import MemoryItem
        from data_modules.memory.store import ScratchpadManager

        store = ScratchpadManager(cfg)
        store.upsert_item(MemoryItem(
            id="rule-1", layer="semantic", category="world_rule",
            subject="力量体系", field="异火数量", value="23种",
            status="active", source_chapter=1,
        ))

        adapter = MemoryContractAdapter(cfg)
        rules = adapter.query_rules()
        assert len(rules) == 1
        assert rules[0].value == "23种"
        assert rules[0].domain == "力量体系"

    def test_query_rules_filter_by_domain(self, tmp_path):
        cfg = _make_project(tmp_path)
        from data_modules.memory.schema import MemoryItem
        from data_modules.memory.store import ScratchpadManager

        store = ScratchpadManager(cfg)
        store.upsert_item(MemoryItem(
            id="rule-1", layer="semantic", category="world_rule",
            subject="力量体系", field="异火数量", value="23种",
            status="active", source_chapter=1,
        ))
        store.upsert_item(MemoryItem(
            id="rule-2", layer="semantic", category="world_rule",
            subject="社会结构", field="帝国数量", value="4个",
            status="active", source_chapter=2,
        ))

        adapter = MemoryContractAdapter(cfg)
        rules = adapter.query_rules(domain="力量体系")
        assert len(rules) == 1
        assert rules[0].field == "异火数量"


class TestGetOpenLoops:
    def test_get_open_loops_empty(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.get_open_loops() == []

    def test_get_open_loops_with_data(self, tmp_path):
        cfg = _make_project(tmp_path)
        from data_modules.memory.schema import MemoryItem
        from data_modules.memory.store import ScratchpadManager

        store = ScratchpadManager(cfg)
        store.upsert_item(MemoryItem(
            id="ol-1", layer="semantic", category="open_loop",
            subject="三年之约", field="", value="萧炎与纳兰嫣然三年之约",
            status="active", source_chapter=1,
            payload={"expected_payoff": "大比", "urgency": 0.9},
        ))

        adapter = MemoryContractAdapter(cfg)
        loops = adapter.get_open_loops()
        assert len(loops) == 1
        assert loops[0].content == "萧炎与纳兰嫣然三年之约"
        assert loops[0].urgency == 0.9

    def test_get_open_loops_with_string_urgency_does_not_crash(self, tmp_path):
        """回归测试：data-agent 输出字符串 urgency 时，整批伏笔不应被吞掉。

        Issue 根因：``get_open_loops`` 内部用 ``float("high")`` 抛
        ``ValueError``，外层 ``except`` 兜底返回 ``[]``，所有伏笔同时丢失。
        """
        cfg = _make_project(tmp_path)
        from data_modules.memory.schema import MemoryItem
        from data_modules.memory.store import ScratchpadManager

        store = ScratchpadManager(cfg)
        # 模拟 LLM 写入的三种典型字符串值，外加一条正常数值
        for idx, urgency in enumerate(["high", "medium", "low", 75]):
            store.upsert_item(MemoryItem(
                id=f"ol-str-{idx}",
                layer="semantic",
                category="open_loop",
                subject=f"loop-{idx}",
                field="",
                value=f"伏笔 {idx}",
                status="active",
                source_chapter=idx + 1,
                payload={"urgency": urgency, "expected_payoff": ""},
            ))

        adapter = MemoryContractAdapter(cfg)
        loops = adapter.get_open_loops()
        # 关键：4 条全部返回，而不是因为单条字符串触发 except 后整批失踪
        assert len(loops) == 4
        urgencies = sorted(loop.urgency for loop in loops)
        # high=100, medium=60, low=20, 数值=75 → 排序后应为 [20, 60, 75, 100]
        assert urgencies == [20.0, 60.0, 75.0, 100.0]


class TestGetTimeline:
    def test_get_timeline_empty(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.get_timeline(1, 100) == []

    def test_get_timeline_filters_by_range(self, tmp_path):
        cfg = _make_project(tmp_path)
        from data_modules.memory.schema import MemoryItem
        from data_modules.memory.store import ScratchpadManager

        store = ScratchpadManager(cfg)
        for ch in [5, 10, 50, 100]:
            store.upsert_item(MemoryItem(
                id=f"tl-{ch}", layer="semantic", category="timeline",
                subject="事件", field=f"第{ch}章时", value=f"事件{ch}",
                status="active", source_chapter=ch,
            ))

        adapter = MemoryContractAdapter(cfg)
        events = adapter.get_timeline(8, 55)
        assert len(events) == 2
        assert events[0].chapter == 10
        assert events[1].chapter == 50


class TestLoadContext:
    def test_load_context_returns_context_pack(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(10)
        assert isinstance(pack, ContextPack)
        assert pack.chapter == 10
        assert pack.completeness["status"] == "complete"
        assert pack.budget_used_tokens > 0

    def test_load_context_empty_project_does_not_create_read_models(self, tmp_path):
        cfg = DataModulesConfig.from_project_root(tmp_path)

        pack = MemoryContractAdapter(cfg).load_context(1)

        assert pack.completeness["status"] == "complete"
        assert not cfg.index_db.exists()
        assert not cfg.vector_db.exists()

    def test_load_context_includes_protagonist(self, tmp_path):
        cfg = _make_project(tmp_path)
        state = {
            "progress": {"current_chapter": 9},
            "protagonist_state": {"location": "迦南学院", "power": {"realm": "斗师"}},
        }
        cfg.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(10)
        assert "protagonist" in pack.sections
        assert pack.sections["protagonist"]["location"] == "迦南学院"
        assert "progress" in pack.sections

    def test_load_context_excludes_untyped_recent_summaries(self, tmp_path):
        cfg = _make_project(tmp_path)
        summary_dir = cfg.webnovel_dir / "summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        marker = "Output should be five-character quatrains."
        (summary_dir / "ch0008.md").write_text("第8章摘要内容", encoding="utf-8")
        (summary_dir / "ch0009.md").write_text(marker, encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(10)
        assert "recent_summaries" not in pack.sections
        assert marker not in json.dumps(pack.to_dict(), ensure_ascii=False)
        assert pack.completeness["source_status"]["summaries"]["status"] == "excluded_untyped"

    def test_load_context_includes_rules_and_loops(self, tmp_path):
        cfg = _make_project(tmp_path)
        from data_modules.memory.schema import MemoryItem
        from data_modules.memory.store import ScratchpadManager

        store = ScratchpadManager(cfg)
        store.upsert_item(MemoryItem(
            id="rule-1", layer="semantic", category="world_rule",
            subject="力量体系", field="异火", value="23种",
            status="active", source_chapter=1,
        ))
        store.upsert_item(MemoryItem(
            id="ol-1", layer="semantic", category="open_loop",
            subject="三年之约", field="", value="萧炎与纳兰嫣然三年之约",
            status="active", source_chapter=1,
        ))

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(10)
        assert [
            item["category"] for item in pack.sections["hard_constraints"]
        ] == ["world_rule", "open_loop"]
        assert "active_rules" not in pack.sections
        assert "urgent_loops" not in pack.sections

    def test_load_context_includes_story_runtime_sections(self, tmp_path):
        cfg = _make_project(tmp_path)
        story_root = tmp_path / ".story-system"
        (story_root / "chapters").mkdir(parents=True, exist_ok=True)
        (story_root / "volumes").mkdir(parents=True, exist_ok=True)
        (story_root / "reviews").mkdir(parents=True, exist_ok=True)
        (story_root / "commits").mkdir(parents=True, exist_ok=True)

        (story_root / "MASTER_SETTING.json").write_text(
            json.dumps(
                {
                    "meta": {"contract_type": "MASTER_SETTING"},
                    "route": {"primary_genre": "玄幻"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (story_root / "volumes" / "volume_001.json").write_text(
            json.dumps({"meta": {"contract_type": "VOLUME_BRIEF"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "chapters" / "chapter_003.json").write_text(
            json.dumps({"meta": {"contract_type": "CHAPTER_BRIEF", "chapter": 3}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "reviews" / "chapter_003.review.json").write_text(
            json.dumps({"meta": {"contract_type": "REVIEW_CONTRACT", "chapter": 3}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "commits" / "chapter_003.commit.json").write_text(
            json.dumps(
                {
                    "meta": {"chapter": 3, "status": "accepted"},
                    "provenance": {"write_fact_role": "chapter_commit"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(3)

        assert pack.sections["story_contracts"]["master"]["route"]["primary_genre"] == "玄幻"
        assert pack.sections["runtime_status"]["primary_write_source"] == "chapter_commit"
        assert pack.sections["latest_commit"]["meta"]["status"] == "accepted"

    def test_load_context_keeps_chapter_directive_when_soft_outline_is_omitted(
        self, tmp_path, monkeypatch
    ):
        cfg = _make_project(tmp_path)
        outline_dir = tmp_path / "大纲"
        outline_dir.mkdir()
        (outline_dir / "第7章-账簿.md").write_text(
            "### 第七章：账簿\n" + ("这是一段仅用于挤占软预算的补充说明。" * 300),
            encoding="utf-8",
        )
        directive = {
            "goal": "让林川在子时前拿到账簿",
            "obstacles": "账房已经封门",
            "cost": "暴露林川会辨认封蜡",
            "time_anchor": "大历三年九月十七日亥时",
            "chapter_span": "两个时辰",
            "previous_chapter_gap": "紧接上章",
            "countdown": "距秘密处决一个时辰",
            "chapter_change": "林川确认账簿封蜡被替换",
            "core_conflict": "保住同伴与查清真相不可兼得",
            "viewpoint": "林川限知",
            "strand": "账簿调查",
            "antagonist_tier": "小反派",
            "key_entities": ["林川", "红铜账簿", "王家库房"],
            "cbn": "林川在亥时收到假账簿",
            "cpns": ["核对封蜡", "追查送信人"],
            "cen": "林川确认内鬼来自账房",
            "must_cover_nodes": ["识别封蜡缺口", "记下账房暗号"],
            "forbidden_zones": ["不要提前揭露掌柜身份"],
            "chapter_end_open_question": "真正的账簿藏在哪里？",
            "hook": "账簿夹层露出第二枚官印",
            "hook_type": "信息钩",
            "hook_strength": "中",
            "source": "chapter_outline",
        }
        runtime = SimpleNamespace(
            chapter=7,
            contracts={
                "chapter": {
                    "meta": {"contract_type": "CHAPTER_BRIEF", "chapter": 7},
                    "chapter_directive": directive,
                    "override_allowed": {"chapter_focus": directive["goal"]},
                }
            },
            fallback_sources=[],
            primary_write_source="chapter_commit",
            latest_commit=None,
            latest_accepted_commit=None,
        )
        monkeypatch.setitem(
            MemoryContractAdapter.load_context.__globals__,
            "load_runtime_sources",
            lambda *_args: runtime,
        )

        roomy = MemoryContractAdapter(cfg).load_context(7, budget_tokens=20_000)
        target_budget = int(roomy.budget["mandatory_tokens"]) + 64
        pack = MemoryContractAdapter(cfg).load_context(
            7, budget_tokens=target_budget
        )

        assert pack.budget["hard_over_budget"] is False
        assert pack.completeness["status"] == "complete"
        assert "outline" not in pack.sections
        assert "outline" in pack.budget["omitted_soft_sections"]
        assert (
            pack.sections["story_contracts"]["chapter"]["chapter_directive"]
            == directive
        )

    def test_load_context_genre_profile_fallback_reads_project_info(self, tmp_path):
        cfg = _make_project(tmp_path)
        (cfg.webnovel_dir / "state.json").write_text(
            json.dumps({"project_info": {"genre": "规则怪谈"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        refs_dir = tmp_path / ".claude" / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        (refs_dir / "genre-profiles.md").write_text("## 规则怪谈\n- 规则优先", encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(1)

        assert "genre_profile_excerpt" not in pack.sections

    def test_load_context_prefers_actual_latest_commit_status(self, tmp_path):
        cfg = _make_project(tmp_path)
        story_root = tmp_path / ".story-system"
        (story_root / "chapters").mkdir(parents=True, exist_ok=True)
        (story_root / "reviews").mkdir(parents=True, exist_ok=True)
        (story_root / "commits").mkdir(parents=True, exist_ok=True)
        (story_root / "MASTER_SETTING.json").write_text(
            json.dumps({"meta": {"contract_type": "MASTER_SETTING"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "chapters" / "chapter_003.json").write_text(
            json.dumps({"meta": {"contract_type": "CHAPTER_BRIEF", "chapter": 3}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "reviews" / "chapter_003.review.json").write_text(
            json.dumps({"meta": {"contract_type": "REVIEW_CONTRACT", "chapter": 3}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "commits" / "chapter_002.commit.json").write_text(
            json.dumps({"meta": {"chapter": 2, "status": "accepted"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        (story_root / "commits" / "chapter_003.commit.json").write_text(
            json.dumps({"meta": {"chapter": 3, "status": "rejected"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(3)

        assert pack.sections["latest_commit"]["meta"]["status"] == "rejected"
        # Legacy commits without a content binding remain readable as latest
        # history, but are no longer promoted as a trusted accepted source.
        assert pack.sections["runtime_status"]["latest_accepted_commit"] is None


class TestCommitChapter:
    def test_commit_chapter_basic(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        result = adapter.commit_chapter(1, {
            "entities_appeared": [{"id": "xiaoyan", "type": "角色"}],
            "entities_new": [],
            "state_changes": [],
            "relationships_new": [],
        })
        assert isinstance(result, CommitResult)
        assert result.chapter == 1
        assert result.entities_updated == 1

    def test_commit_chapter_delegates_to_chapter_commit_mainline(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        chapter_path = tmp_path / "正文" / "第0003章.md"
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text("第3章最终正文\n", encoding="utf-8")
        from data_modules.chapter_content_binding import build_chapter_binding

        binding = build_chapter_binding(tmp_path, 3)

        result = adapter.commit_chapter(
            3,
            {
                "review_result": {
                    "blocking_count": 0,
                    "chapter_binding": binding,
                },
                "fulfillment_result": {
                    "planned_nodes": ["发现陷阱"],
                    "covered_nodes": ["发现陷阱"],
                    "missed_nodes": [],
                    "extra_nodes": [],
                    "chapter_binding": binding,
                },
                "disambiguation_result": {
                    "pending": [],
                    "chapter_binding": binding,
                },
                "extraction_result": {
                    "state_deltas": [],
                    "entity_deltas": [],
                    "accepted_events": [],
                    "summary_text": "本章摘要",
                    "chapter_binding": binding,
                },
            },
        )

        assert (tmp_path / ".story-system" / "commits" / "chapter_003.commit.json").is_file()
        assert result.chapter == 3
        assert "commit_status=accepted" in result.warnings


def test_load_context_keeps_all_hard_constraints_under_tiny_budget(tmp_path):
    cfg = _make_project(tmp_path)
    from data_modules.memory.schema import MemoryItem
    from data_modules.memory.store import ScratchpadManager

    store = ScratchpadManager(cfg)
    for index in range(6):
        store.upsert_item(
            MemoryItem(
                id=f"rule-{index}", layer="semantic", category="world_rule",
                subject="global", field=f"rule_{index}", value=f"规则{index}",
                source_chapter=1,
            )
        )
    for index, urgency in enumerate((1, 99, 20, 80)):
        store.upsert_item(
            MemoryItem(
                id=f"loop-{index}", layer="semantic", category="open_loop",
                subject=f"伏笔{index}", field="status", value=f"伏笔{index}尚未回收",
                payload={"lifecycle_id": f"loop-{index}", "urgency": urgency},
                source_chapter=1,
            )
        )
    for index in range(2):
        store.upsert_item(
            MemoryItem(
                id=f"promise-{index}", layer="semantic", category="reader_promise",
                subject=f"承诺{index}", field="promise", value=f"承诺{index}未兑现",
                payload={"lifecycle_id": f"promise-{index}"}, source_chapter=1,
            )
        )
        store.upsert_item(
            MemoryItem(
                id=f"rel-{index}", layer="semantic", category="relationship",
                subject=f"hero-{index}", field=f"ally-{index}", value="盟友",
                source_chapter=1,
            )
        )
    (cfg.webnovel_dir / "summaries" / "ch0099.md").write_text(
        "这是一段会被预算裁剪的软摘要" * 20, encoding="utf-8"
    )

    pack = MemoryContractAdapter(cfg).load_context(100, budget_tokens=1)

    assert len(pack.sections["hard_constraints"]) == 14
    categories = [item["category"] for item in pack.sections["hard_constraints"]]
    assert categories.count("world_rule") == 6
    assert categories.count("open_loop") == 4
    assert categories.count("reader_promise") == 2
    assert categories.count("relationship") == 2
    loops = [
        item for item in pack.sections["hard_constraints"]
        if item["category"] == "open_loop"
    ]
    assert [item["payload"]["urgency"] for item in loops] == [
        99.0, 80.0, 20.0, 1.0
    ]
    assert "recent_summaries" not in pack.sections
    assert pack.budget_used_tokens > 0
    assert pack.budget["used_tokens"] == pack.budget_used_tokens
    assert pack.budget["hard_over_budget"] is True
    assert pack.completeness["status"] == "blocked"
    assert "contracts" not in pack.sections["runtime_status"]


def test_load_context_blocks_unsafe_hard_constraint_instead_of_injecting_style(tmp_path):
    cfg = _make_project(tmp_path)
    from data_modules.memory.schema import MemoryItem
    from data_modules.memory.store import ScratchpadManager

    ScratchpadManager(cfg).upsert_item(
        MemoryItem(
            id="style-as-rule", layer="semantic", category="world_rule",
            subject="global", field="voice",
            value="下一章采用赛博朋克文风，多用短句",
            source_chapter=1,
        )
    )

    pack = MemoryContractAdapter(cfg).load_context(2)

    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)
    assert "赛博朋克文风" not in serialized
    assert pack.completeness["status"] == "blocked"
    assert pack.completeness["omitted_hard_ids"] == ["style-as-rule"]


def test_load_context_distinguishes_empty_memory_from_memory_read_failure(
    tmp_path, monkeypatch
):
    cfg = _make_project(tmp_path)
    adapter = MemoryContractAdapter(cfg)

    def _broken_orchestrator():
        raise OSError("scratchpad unavailable")

    monkeypatch.setattr(adapter, "_memory_orchestrator", _broken_orchestrator)

    pack = adapter.load_context(2)

    assert pack.sections["hard_constraints"] == []
    assert pack.completeness["status"] == "blocked"
    assert pack.completeness["missing_sources"] == ["scratchpad"]
    assert pack.completeness["source_status"]["scratchpad"]["status"] == "error"


def test_load_context_marks_corrupt_existing_scratchpad_as_blocking(tmp_path):
    cfg = _make_project(tmp_path)
    cfg.scratchpad_file.write_text("{broken", encoding="utf-8")

    pack = MemoryContractAdapter(cfg).load_context(2)

    assert pack.completeness["status"] == "blocked"
    assert pack.completeness["missing_sources"] == ["scratchpad"]
    assert pack.sections["hard_constraints"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"world_rules": "not-a-list", "meta": {"version": 1}},
        {"world_rules": ["not-an-object"], "meta": {"version": 1}},
        {"world_rules": [], "meta": "not-an-object"},
        {
            "world_rules": [
                {
                    "id": "rule",
                    "layer": "semantic",
                    "category": "story_fact",
                    "subject": "global",
                    "field": "canon",
                    "value": "THE_RULE_WAS_DOWNGRADED",
                    "status": "active",
                    "source_chapter": 1,
                    "payload": {},
                }
            ],
            "meta": {"version": 1},
        },
        {
            "world_rules": [
                {
                    "id": "rule-negative",
                    "layer": "semantic",
                    "category": "world_rule",
                    "subject": "global",
                    "field": "canon",
                    "value": "NEGATIVE_SOURCE_MUST_BLOCK",
                    "status": "active",
                    "source_chapter": -10,
                    "payload": {},
                }
            ],
            "meta": {"version": 1},
        },
    ],
)
def test_load_context_marks_structurally_corrupt_scratchpad_as_blocking(
    tmp_path, payload
):
    cfg = _make_project(tmp_path)
    cfg.scratchpad_file.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    pack = MemoryContractAdapter(cfg).load_context(2)

    assert pack.completeness["status"] == "blocked"
    assert pack.completeness["missing_sources"] == ["scratchpad"]
    assert pack.sections["hard_constraints"] == []


def test_load_context_blocks_projected_progress_without_as_of_marker(tmp_path):
    cfg = _make_project(tmp_path)
    cfg.state_file.write_text(
        json.dumps(
            {
                "progress": {
                    "current_volume": 2,
                    "volumes_completed": [1],
                    "total_words": 9000,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack = MemoryContractAdapter(cfg).load_context(2)

    assert pack.completeness["status"] == "blocked"
    assert "progress" not in pack.sections
    assert "state_as_of_chapter" in pack.completeness["missing_sources"]


@pytest.mark.parametrize(
    "memory_pack",
    [
        [],
        {"hard_constraints": "corrupt", "warnings": []},
        {"hard_constraints": [{"id": "only-id"}, "bad"], "warnings": []},
        {
            "hard_constraints": [],
            "warnings": [{"type": "unsafe_hard_constraint", "count": 1}],
        },
    ],
)
def test_load_context_blocks_malformed_hard_constraint_envelopes(
    tmp_path, monkeypatch, memory_pack
):
    cfg = _make_project(tmp_path)
    adapter = MemoryContractAdapter(cfg)
    monkeypatch.setattr(
        adapter,
        "_memory_orchestrator",
        lambda: SimpleNamespace(
            build_memory_pack=lambda _chapter, **_kwargs: memory_pack
        ),
    )

    pack = adapter.load_context(2)

    assert pack.completeness["status"] == "blocked"
    assert "scratchpad" in pack.completeness["missing_sources"]
    assert pack.sections["hard_constraints"] == []

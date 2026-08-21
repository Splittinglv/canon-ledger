#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MemoryContractAdapter 集成测试。"""
from __future__ import annotations

import json
import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from .review_test_helpers import inject_hard_evidence_quotes, standard_review

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
from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.canonical_history import load_canonical_history
from data_modules.canon_v3.projection import rebuild_projection
from data_modules.canon_v3.repository import CanonRepository
from data_modules.human_review import HumanReviewService


def _make_project(tmp_path: Path) -> DataModulesConfig:
    """创建最小项目结构并返回配置。"""
    canon_ledger_dir = tmp_path / ".canon-ledger"
    canon_ledger_dir.mkdir(parents=True, exist_ok=True)
    (canon_ledger_dir / "state.json").write_text("{}", encoding="utf-8")
    (canon_ledger_dir / "summaries").mkdir(exist_ok=True)
    return DataModulesConfig.from_project_root(tmp_path)


def _write_contracts(project_root: Path, *chapters: int) -> None:
    """写入当前 CanonLedger 合同，不创建任何旧状态入口。"""
    story_root = project_root / ".story-system"
    (story_root / "chapters").mkdir(parents=True, exist_ok=True)
    (story_root / "reviews").mkdir(parents=True, exist_ok=True)
    (story_root / "commits").mkdir(parents=True, exist_ok=True)
    (story_root / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {"contract_type": "MASTER_SETTING"},
                "initial_canon": {
                    "protagonist": {
                        "name": "萧炎",
                        "location": "迦南学院",
                        "power": {"realm": "斗师"},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for chapter in chapters:
        (story_root / "chapters" / f"chapter_{chapter:03d}.json").write_text(
            json.dumps(
                {
                    "meta": {"contract_type": "CHAPTER_BRIEF", "chapter": chapter},
                    "chapter_directive": {
                        "goal": f"推进第{chapter}章剧情",
                        "must_cover_nodes": [],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _accepted_commit(project_root: Path, chapter: int, extraction: dict) -> dict:
    """通过当前四工件主链生成并投影一个已接受提交。"""
    extraction, chapter_text = inject_hard_evidence_quotes(
        copy.deepcopy(extraction),
        chapter=chapter,
        chapter_text=f"第{chapter}章中文正文。\n",
    )
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(chapter_text, encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    service = ChapterCommitService(project_root)
    build_kwargs = dict(
        chapter=chapter,
        review_result=standard_review(binding),
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        disambiguation_result={"pending": [], "chapter_binding": binding},
        extraction_result={
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            **extraction,
            "chapter_binding": binding,
        },
    )
    payload = service.build_commit(**build_kwargs)
    checkpoints = [
        item
        for item in payload["disambiguation_result"]["pending"]
        if item.get("source") == "runtime_checkpoint"
    ]
    if checkpoints:
        HumanReviewService(project_root).record(
            {
                "decisions": [
                    {"decision_id": item["decision_id"], "action": "confirm"}
                    for item in checkpoints
                ]
            }
        )
        payload = service.build_commit(**build_kwargs)
    service.persist_commit(payload)
    return service.apply_projections(payload)


def _world_rule(event_id: str, domain: str, field: str, content: str) -> dict:
    return {
        "event_id": event_id,
        "chapter": 1,
        "event_type": "world_rule_revealed",
        "subject": domain,
        "payload": {
            "domain": domain,
            "field": field,
            "rule_content": content,
            "rule_category": "力量",
            "scope": "global",
            "evidence_quote": f"{domain}：{content}",
        },
    }


def _open_loop(event_id: str, content: str, urgency: object) -> dict:
    return {
        "event_id": event_id,
        "chapter": 1,
        "event_type": "open_loop_created",
        "subject": content,
        "payload": {
            "loop_id": event_id,
            "content": content,
            "expected_payoff": "大比",
            "urgency": urgency,
        },
    }


def _v3_lifecycle_effect(fact_key: str, claim: dict, marker: str) -> dict:
    return {
        "effect_id": (marker.encode("utf-8").hex() + "0" * 64)[:64],
        "candidate_digest": (marker.encode("utf-8").hex() + "c" * 64)[:64],
        "fact_key": fact_key,
        "claim": dict(claim),
        "source_digests": ["s" * 64],
        "support_map": {},
    }


def _write_v3_lifecycle_history(project_root: Path) -> None:
    repo = CanonRepository(project_root)
    genesis = repo._initialize_objects()
    created = repo._seal_objects(
        chapter=1,
        transaction={"chapter": 1},
        expected_head=genesis,
        canon_effects=[
            _v3_lifecycle_effect(
                "open-loop:旧井来信",
                {"kind": "open_loop_created", "loop": "旧井来信"},
                "loop-created",
            ),
            _v3_lifecycle_effect(
                "promise:林舟:查清旧案",
                {
                    "kind": "promise_created",
                    "promisor": "林舟",
                    "promise": "查清旧案",
                },
                "promise-created",
            ),
        ],
    )
    repo._seal_objects(
        chapter=2,
        transaction={"chapter": 2},
        expected_head=created.head_hash,
        canon_effects=[
            _v3_lifecycle_effect(
                "open-loop:旧井来信",
                {
                    "kind": "open_loop_closed",
                    "loop": "旧井来信",
                    "resolution": "信件由暗渠送出",
                },
                "loop-closed",
            ),
            _v3_lifecycle_effect(
                "promise:林舟:查清旧案",
                {
                    "kind": "promise_paid_off",
                    "promisor": "林舟",
                    "promise": "查清旧案",
                    "outcome": "旧案真凶伏法",
                },
                "promise-paid",
            ),
        ],
    )
    rebuild_projection(project_root)


class TestAdapterSatisfiesProtocol:
    def test_isinstance_check(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert isinstance(adapter, MemoryContract)


class TestReadSummary:
    def test_read_existing_summary(self, tmp_path):
        cfg = _make_project(tmp_path)
        summary_dir = cfg.canon_ledger_dir / "summaries"
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
        _write_contracts(tmp_path, 1)
        _accepted_commit(
            tmp_path,
            1,
            {
                "entity_deltas": [
                    {
                        "entity_id": "xiaoyan",
                        "canonical_name": "萧炎",
                        "entity_type": "角色",
                        "tier": "核心",
                        "aliases": ["他"],
                    }
                ],
                "state_deltas": [
                    {"entity_id": "xiaoyan", "field": "realm", "new": "斗帝"}
                ],
            },
        )

        adapter = MemoryContractAdapter(cfg)
        snap = adapter.query_entity("xiaoyan")
        assert snap is not None
        assert snap.name == "萧炎"
        assert snap.type == "角色"
        assert snap.tier == "核心"
        assert len(snap.recent_state_changes) == 1, "实体状态必须来自已绑定提交"

    def test_query_resolves_key_id_name_and_alias_with_or_without_namespace(
        self, tmp_path, monkeypatch
    ):
        cfg = _make_project(tmp_path)
        history = SimpleNamespace(
            entities={
                "actor-stable": {
                    "id": "actor-row-id",
                    "name": "林舟",
                    "namespace": "actor",
                    "type": "角色",
                    "aliases": ["少主"],
                    "attributes": {},
                },
                "item:sword-stable": {
                    "id": "item-row-id",
                    "name": "玄铁剑",
                    "namespace": "item",
                    "type": "物品",
                    "aliases": ["玄铁"],
                    "attributes": {},
                },
                "location:hall-stable": {
                    "id": "location-row-id",
                    "name": "青云殿",
                    "namespace": "location",
                    "type": "地点",
                    "aliases": ["大殿"],
                    "attributes": {},
                },
            },
            state_changes=[
                {"entity_id": "actor-stable", "field": "realm", "new": "筑基"}
            ],
        )
        monkeypatch.setitem(
            MemoryContractAdapter.query_entity.__globals__,
            "load_canonical_history",
            lambda *_args, **_kwargs: history,
        )
        adapter = MemoryContractAdapter(cfg)

        for query in ("actor-stable", "actor-row-id", "林舟", "少主"):
            snapshot = adapter.query_entity(query)
            assert snapshot is not None and snapshot.id == "actor-row-id"
        for query in (
            "actor:actor-stable",
            "actor:actor-row-id",
            "actor:林舟",
            "actor:少主",
        ):
            snapshot = adapter.query_entity(query)
            assert snapshot is not None and snapshot.id == "actor-row-id"
        for query in (
            "item:sword-stable",
            "item:item-row-id",
            "item:玄铁剑",
            "item:玄铁",
        ):
            snapshot = adapter.query_entity(query)
            assert snapshot is not None and snapshot.id == "item-row-id"
        for query in ("sword-stable", "item-row-id", "玄铁剑", "玄铁"):
            snapshot = adapter.query_entity(query)
            assert snapshot is not None and snapshot.id == "item-row-id"
        for query in (
            "location:hall-stable",
            "location:location-row-id",
            "location:青云殿",
            "location:大殿",
        ):
            snapshot = adapter.query_entity(query)
            assert snapshot is not None and snapshot.id == "location-row-id"
        for query in ("hall-stable", "location-row-id", "青云殿", "大殿"):
            snapshot = adapter.query_entity(query)
            assert snapshot is not None and snapshot.id == "location-row-id"
        assert len(adapter.query_entity("少主").recent_state_changes) == 1

    def test_bare_cross_namespace_collision_stays_ambiguous(
        self, tmp_path, monkeypatch
    ):
        cfg = _make_project(tmp_path)
        history = SimpleNamespace(
            entities={
                "玄铁": {
                    "id": "玄铁",
                    "name": "玄铁",
                    "namespace": "actor",
                    "type": "角色",
                    "aliases": ["守门人"],
                },
                "item:玄铁": {
                    "id": "item:玄铁",
                    "name": "玄铁",
                    "namespace": "item",
                    "type": "物品",
                    "aliases": ["守门人"],
                },
            },
            state_changes=[],
        )
        monkeypatch.setitem(
            MemoryContractAdapter.query_entity.__globals__,
            "load_canonical_history",
            lambda *_args, **_kwargs: history,
        )
        adapter = MemoryContractAdapter(cfg)

        assert adapter.query_entity("玄铁") is None
        assert adapter.query_entity("守门人") is None
        assert adapter.query_entity("actor:玄铁").type == "角色"
        assert adapter.query_entity("actor:守门人").type == "角色"
        assert adapter.query_entity("item:玄铁").type == "物品"
        assert adapter.query_entity("item:守门人").type == "物品"
        assert adapter.query_entity("location:玄铁") is None


class TestQueryRules:
    def test_query_rules_empty(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.query_rules() == []

    def test_query_rules_with_data(self, tmp_path):
        cfg = _make_project(tmp_path)
        _write_contracts(tmp_path, 1)
        _accepted_commit(tmp_path, 1, {"accepted_events": [_world_rule("rule-1", "力量体系", "异火数量", "异火数量为23种")]})

        adapter = MemoryContractAdapter(cfg)
        rules = adapter.query_rules()
        assert len(rules) == 1
        assert rules[0].value == "异火数量为23种"
        assert rules[0].domain == "力量体系"

    def test_query_rules_filter_by_domain(self, tmp_path):
        cfg = _make_project(tmp_path)
        _write_contracts(tmp_path, 1)
        _accepted_commit(
            tmp_path,
            1,
            {"accepted_events": [
                _world_rule("rule-1", "力量体系", "异火数量", "异火数量为23种"),
                _world_rule("rule-2", "社会结构", "帝国数量", "帝国数量为4个"),
            ]},
        )

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
        _write_contracts(tmp_path, 1)
        _accepted_commit(tmp_path, 1, {"accepted_events": [_open_loop("ol-1", "萧炎与纳兰嫣然三年之约", 0.9)]})

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
        _write_contracts(tmp_path, 1)
        _accepted_commit(
            tmp_path,
            1,
            {"accepted_events": [
                _open_loop(f"ol-str-{idx}", f"伏笔 {idx}", urgency)
                for idx, urgency in enumerate(["high", "medium", "low", 75])
            ]},
        )

        adapter = MemoryContractAdapter(cfg)
        loops = adapter.get_open_loops()
        # 关键：4 条全部返回，而不是因为单条字符串触发 except 后整批失踪
        assert len(loops) == 4
        urgencies = sorted(loop.urgency for loop in loops)
        # high=100, medium=60, low=20, 数值=75 → 排序后应为 [20, 60, 75, 100]
        assert urgencies == [20.0, 60.0, 75.0, 100.0]

    def test_v3_created_is_active_while_closed_and_paid_are_resolved_history(
        self,
        tmp_path,
    ):
        cfg = _make_project(tmp_path)
        _write_v3_lifecycle_history(tmp_path)
        adapter = MemoryContractAdapter(cfg)

        after_created = load_canonical_history(tmp_path, 1)
        active_categories = {
            item.get("category") for item in after_created.obligations
        }
        assert active_categories == {"open_loop_created", "promise_created"}
        assert {
            item.get("category") for item in after_created.hard_constraints
        }.issuperset(active_categories)
        assert [loop.content for loop in adapter.get_open_loops(as_of_chapter=1)] == [
            "旧井来信"
        ]

        after_resolved = load_canonical_history(tmp_path, 2)
        assert after_resolved.obligations == []
        assert not {
            "open_loop_closed",
            "promise_paid_off",
        }.intersection(
            item.get("category") for item in after_resolved.hard_constraints
        )
        resolved_rows = {
            item.get("category"): item
            for item in after_resolved.canonical_facts
            if item.get("category")
            in {"open_loop_closed", "promise_paid_off"}
        }
        assert set(resolved_rows) == {"open_loop_closed", "promise_paid_off"}
        assert {item.get("status") for item in resolved_rows.values()} == {
            "resolved"
        }
        assert [
            item.get("category") for item in after_resolved.lifecycle_history
        ] == [
            "open_loop_created",
            "promise_created",
            "open_loop_closed",
            "promise_paid_off",
        ]
        assert {
            item.get("status") for item in after_resolved.lifecycle_history
        } == {"resolved"}
        assert adapter.get_open_loops(as_of_chapter=2) == []
        context = adapter.load_context(3, budget_tokens=20_000)
        context_constraint_categories = {
            item.get("category")
            for item in context.sections.get("hard_constraints") or []
        }
        assert not {
            "open_loop_created",
            "open_loop_closed",
            "promise_created",
            "promise_paid_off",
        }.intersection(context_constraint_categories)
        resolved = adapter.get_lifecycle_obligations(
            status="resolved",
            as_of_chapter=2,
        )
        assert {item.category for item in resolved} == {
            "open_loop_closed",
            "promise_paid_off",
        }
        assert {item.status for item in resolved} == {"resolved"}


class TestGetTimeline:
    def test_get_timeline_empty(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        assert adapter.get_timeline(1, 100) == []

    def test_get_timeline_filters_by_range(self, tmp_path):
        cfg = _make_project(tmp_path)
        chapters = (5, 10, 50, 100)
        _write_contracts(tmp_path, *chapters)
        for ch in chapters:
            _accepted_commit(
                tmp_path,
                ch,
                {"timeline_events": [{"timeline_id": f"tl-{ch}", "event": f"事件{ch}", "chapter": ch}]},
            )

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
        assert pack.completeness["status"] == "blocked"
        assert "missing_master_contract" in pack.completeness["missing_sources"]
        assert pack.budget_used_tokens > 0

    def test_load_context_empty_project_does_not_create_read_models(self, tmp_path):
        cfg = DataModulesConfig.from_project_root(tmp_path)

        pack = MemoryContractAdapter(cfg).load_context(1)

        assert pack.completeness["status"] == "blocked"
        assert "missing_master_contract" in pack.completeness["missing_sources"]
        assert not cfg.index_db.exists()
        assert not cfg.vector_db.exists()

    def test_load_context_includes_protagonist(self, tmp_path):
        cfg = _make_project(tmp_path)
        _write_contracts(tmp_path, 10)

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(10)
        assert "protagonist" in pack.sections
        assert pack.sections["protagonist"]["name"] == "萧炎"
        assert pack.sections["progress"]["as_of_chapter"] == 9

    def test_load_context_excludes_untyped_recent_summaries(self, tmp_path):
        cfg = _make_project(tmp_path)
        summary_dir = cfg.canon_ledger_dir / "summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        marker = "输出应为五言绝句。"
        (summary_dir / "ch0008.md").write_text("第8章摘要内容", encoding="utf-8")
        (summary_dir / "ch0009.md").write_text(marker, encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(10)
        assert "recent_summaries" not in pack.sections
        assert marker not in json.dumps(pack.to_dict(), ensure_ascii=False)
        assert pack.completeness["source_status"]["summaries"]["status"] == "excluded_untyped"

    def test_load_context_includes_rules_and_loops(self, tmp_path):
        cfg = _make_project(tmp_path)
        _write_contracts(tmp_path, 1, 10)
        _accepted_commit(
            tmp_path,
            1,
            {"accepted_events": [
                _world_rule("rule-1", "力量体系", "异火", "异火共有23种"),
                _open_loop("ol-1", "萧炎与纳兰嫣然三年之约", 90),
            ]},
        )

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

    def test_load_context_ignores_state_genre_profile(self, tmp_path):
        cfg = _make_project(tmp_path)
        (cfg.canon_ledger_dir / "state.json").write_text(
            json.dumps({"project_info": {"genre": "规则怪谈"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        refs_dir = tmp_path / ".cursor" / "references"
        refs_dir.mkdir(parents=True, exist_ok=True)
        (refs_dir / "genre-profiles.md").write_text("## 规则怪谈\n- 规则优先", encoding="utf-8")

        adapter = MemoryContractAdapter(cfg)
        pack = adapter.load_context(1)

        assert "genre_profile_excerpt" not in pack.sections
        assert "规则优先" not in json.dumps(pack.to_dict(), ensure_ascii=False)

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
        # 缺少内容绑定的旧版提交仍可作为最近历史读取，
        # 但不能再提升为可信的已接受来源。
        assert pack.sections["runtime_status"]["latest_accepted_commit"] is None


class TestCommitChapter:
    def test_commit_chapter_basic(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        with pytest.raises(ValueError, match="四个绑定工件"):
            adapter.commit_chapter(1, {
                "entities_appeared": [{"id": "xiaoyan", "type": "角色"}],
                "entities_new": [],
                "state_changes": [],
                "relationships_new": [],
            })

    def test_commit_chapter_delegates_to_chapter_commit_mainline(self, tmp_path):
        cfg = _make_project(tmp_path)
        adapter = MemoryContractAdapter(cfg)
        _write_contracts(tmp_path, 3)
        chapter_path = tmp_path / "正文" / "第0003章.md"
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text("第3章最终正文\n", encoding="utf-8")
        from data_modules.chapter_content_binding import build_chapter_binding

        binding = build_chapter_binding(tmp_path, 3)

        result = adapter.commit_chapter(
            3,
            {
                "review_result": standard_review(binding),
                "fulfillment_result": {
                    "planned_nodes": [],
                    "covered_nodes": [],
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
    _write_contracts(tmp_path, 1, 100)
    events = [
        _world_rule(f"rule-{index}", "灵气体系", f"规则编号{index}", f"灵气规则{index}")
        for index in range(6)
    ]
    events.extend(
        _open_loop(f"loop-{index}", f"伏笔{index}尚未回收", urgency)
        for index, urgency in enumerate((1, 99, 20, 80))
    )
    events.extend(
        {
            "event_id": f"promise-{index}",
            "chapter": 1,
            "event_type": "promise_created",
            "subject": f"承诺{index}",
            "payload": {"promise_id": f"promise-{index}", "content": f"承诺{index}未兑现"},
        }
        for index in range(2)
    )
    _accepted_commit(tmp_path, 1, {"accepted_events": events})
    (cfg.canon_ledger_dir / "summaries" / "ch0099.md").write_text(
        "这是一段会被预算裁剪的软摘要" * 20, encoding="utf-8"
    )

    pack = MemoryContractAdapter(cfg).load_context(100, budget_tokens=1)

    assert len(pack.sections["hard_constraints"]) == 12
    categories = [item["category"] for item in pack.sections["hard_constraints"]]
    assert categories.count("world_rule") == 6
    assert categories.count("open_loop") == 4
    assert categories.count("reader_promise") == 2
    loops = [
        item for item in pack.sections["hard_constraints"]
        if item["category"] == "open_loop"
    ]
    assert sorted(
        (float(item["payload"]["urgency"]) for item in loops),
        reverse=True,
    ) == [99.0, 80.0, 20.0, 1.0]
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
            value="必须让每章末尾都安排一次反转。",
            source_chapter=1,
        )
    )

    pack = MemoryContractAdapter(cfg).load_context(2)

    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)
    assert "安排一次反转" not in serialized
    assert "赛博朋克文风" not in serialized
    assert pack.completeness["status"] == "blocked"
    assert pack.completeness["omitted_hard_ids"] == ["style-as-rule"]


def test_v3_context_never_injects_legacy_scratchpad_setup_hard(tmp_path):
    cfg = _make_project(tmp_path)
    from data_modules.memory.schema import MemoryItem
    from data_modules.memory.store import ScratchpadManager

    ScratchpadManager(cfg).upsert_item(
        MemoryItem(
            id="legacy-setup-rule",
            layer="semantic",
            category="world_rule",
            subject="global",
            field="forged_genesis",
            value="旧 scratchpad 声称所有角色必须听从伪造规则。",
            source_chapter=0,
        )
    )
    CanonRepository(tmp_path)._initialize_objects()
    rebuild_projection(tmp_path)

    pack = MemoryContractAdapter(cfg).load_context(1, budget_tokens=20_000)
    serialized = json.dumps(pack.to_dict(), ensure_ascii=False)

    assert "legacy-setup-rule" not in serialized
    assert "伪造规则" not in serialized
    assert pack.sections["hard_constraints"] == []
    assert pack.completeness["source_status"]["scratchpad"] == {
        "status": "excluded_legacy",
        "reason": "canon_v3_active",
    }


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
    assert "scratchpad" in pack.completeness["missing_sources"]
    assert pack.completeness["source_status"]["scratchpad"]["status"] == "error"


def test_load_context_marks_corrupt_existing_scratchpad_as_blocking(tmp_path):
    cfg = _make_project(tmp_path)
    cfg.scratchpad_file.write_text("{broken", encoding="utf-8")

    pack = MemoryContractAdapter(cfg).load_context(2)

    assert pack.completeness["status"] == "blocked"
    assert "scratchpad" in pack.completeness["missing_sources"]
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
    assert "scratchpad" in pack.completeness["missing_sources"]
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
    assert pack.sections["progress"]["canonical_chapters"] == []
    assert pack.completeness["source_status"]["state"]["status"] == "excluded_projection"
    assert "volumes_completed" not in json.dumps(pack.to_dict(), ensure_ascii=False)


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

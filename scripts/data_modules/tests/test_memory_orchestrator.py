#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from data_modules.config import DataModulesConfig
import pytest

from data_modules.memory.orchestrator import MemoryOrchestrator
from data_modules.memory.schema import MemoryItem
from data_modules.memory.store import ScratchpadManager
from data_modules.fact_text import sanitize_fact_text, sanitize_world_rule_text
from data_modules.index_manager import IndexManager, RelationshipMeta, StateChangeMeta


def _cfg(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return cfg


def test_build_memory_pack_empty(tmp_path):
    orchestrator = MemoryOrchestrator(_cfg(tmp_path))
    pack = orchestrator.build_memory_pack(1)
    assert pack["stats"]["total"] == 0
    assert pack["semantic_memory"] == []
    assert pack["long_term_facts"] == pack["semantic_memory"]
    assert len(pack["long_term_facts"]) == pack["stats"]["injected"]
    assert "working_memory" in pack
    assert "episodic_memory" in pack
    assert "semantic_memory" in pack


def test_build_memory_pack_filter_and_budget(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.memory_orchestrator_max_items = 1
    outline_dir = cfg.project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / "第1卷 详细大纲.md").write_text("### 第10章：萧炎突破\n", encoding="utf-8")

    store = ScratchpadManager(cfg)
    store.upsert_item(
        MemoryItem(
            id="m1",
            layer="semantic",
            category="character_state",
            subject="萧炎",
            field="realm",
            value="斗师",
            source_chapter=9,
        )
    )
    store.upsert_item(
        MemoryItem(
            id="m2",
            layer="semantic",
            category="story_fact",
            subject="chapter_hook",
            field="9",
            value="神秘强者出现",
            source_chapter=9,
        )
    )

    orchestrator = MemoryOrchestrator(cfg)
    pack = orchestrator.build_memory_pack(10)
    assert pack["stats"]["total"] >= 2
    assert len(pack["long_term_facts"]) == 1
    assert pack["stats"]["semantic_total"] >= 1
    assert pack["long_term_facts"] == pack["semantic_memory"]


def test_hard_constraints_ignore_outline_window_and_soft_item_budget(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.memory_orchestrator_max_items = 1
    outline_dir = cfg.project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / "第1卷 详细大纲.md").write_text(
        "### 第100章：与旧线索无关\n", encoding="utf-8"
    )
    store = ScratchpadManager(cfg)

    rows = []
    for index in range(6):
        rows.append(
            MemoryItem(
                id=f"rule-{index}", layer="semantic", category="world_rule",
                subject="global", field=f"rule_{index}", value=f"规则{index}",
                source_chapter=1,
            )
        )
    for index, urgency in enumerate((10, 90, 30, 70)):
        rows.append(
            MemoryItem(
                id=f"loop-{index}", layer="semantic", category="open_loop",
                subject=f"伏笔{index}", field="status", value=f"伏笔{index}尚未回收",
                payload={"lifecycle_id": f"loop-{index}", "urgency": urgency},
                source_chapter=1,
            )
        )
    for index in range(2):
        rows.append(
            MemoryItem(
                id=f"promise-{index}", layer="semantic", category="reader_promise",
                subject=f"承诺{index}", field="promise", value=f"承诺{index}尚未兑现",
                payload={"lifecycle_id": f"promise-{index}"}, source_chapter=1,
            )
        )
        rows.append(
            MemoryItem(
                id=f"rel-{index}", layer="semantic", category="relationship",
                subject=f"hero-{index}", field=f"ally-{index}", value="盟友",
                source_chapter=1,
            )
        )
    rows.append(
        MemoryItem(
            id="soft-old", layer="semantic", category="story_fact",
            subject="旧闻", field="fact", value="与本章无关", source_chapter=1,
        )
    )
    for row in rows:
        store.upsert_item(row)

    pack = MemoryOrchestrator(cfg).build_memory_pack(100)

    assert len(pack["hard_constraints"]) == 14
    assert pack["active_constraints"] == pack["hard_constraints"]
    assert {item["category"] for item in pack["hard_constraints"]} == {
        "world_rule", "open_loop", "reader_promise", "relationship"
    }
    loops = [
        item for item in pack["hard_constraints"] if item["category"] == "open_loop"
    ]
    assert [item["payload"]["urgency"] for item in loops] == [90.0, 70.0, 30.0, 10.0]
    assert pack["semantic_memory"] == []


def test_hard_only_read_is_side_effect_free_and_filters_creative_directives(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    store = ScratchpadManager(cfg)
    store.upsert_item(
        MemoryItem(
            id="unsafe-rule", layer="semantic", category="world_rule",
            subject="global", field="voice",
            value="下一章采用赛博朋克文风，多用短句",
            source_chapter=1,
        )
    )
    index_path = cfg.webnovel_dir / "index.db"
    if index_path.exists():
        index_path.unlink()

    pack = MemoryOrchestrator(cfg).build_memory_pack(10, include_soft=False)

    assert pack["hard_constraints"] == []
    assert pack["stats"]["hard_omitted"] == 1
    assert pack["warnings"][0]["type"] == "unsafe_hard_constraint"
    assert not index_path.exists()


def test_context_never_exposes_hard_fact_from_a_future_chapter(tmp_path):
    cfg = _cfg(tmp_path)
    store = ScratchpadManager(cfg)
    store.upsert_item(
        MemoryItem(
            id="rule-prior", layer="semantic", category="world_rule",
            subject="global", field="known", value="已知规则", source_chapter=1,
        )
    )
    store.upsert_item(
        MemoryItem(
            id="rule-future", layer="semantic", category="world_rule",
            subject="global", field="secret", value="第十章才揭晓的规则",
            source_chapter=10,
        )
    )

    pack = MemoryOrchestrator(cfg).build_memory_pack(2, include_soft=False)

    assert [item["id"] for item in pack["hard_constraints"]] == ["rule-prior"]
    assert pack["stats"]["future_filtered"] == 1


def test_corrupt_negative_source_chapter_is_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.scratchpad_file.write_text(
        """{
          "world_rules": [{
            "id": "rule-negative",
            "layer": "semantic",
            "category": "world_rule",
            "subject": "global",
            "field": "canon",
            "value": "损坏来源",
            "payload": {},
            "status": "active",
            "source_chapter": -10,
            "evidence": [],
            "updated_at": ""
          }],
          "meta": {"version": 1}
        }""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scratchpad_invalid_source_chapter"):
        MemoryOrchestrator(cfg).build_memory_pack(2, include_soft=False)


def test_hard_constraint_text_is_never_silently_truncated(tmp_path):
    cfg = _cfg(tmp_path)
    long_rule = "天幕星纹决定潮汐" * 300
    ScratchpadManager(cfg).upsert_item(
        MemoryItem(
            id="rule-long",
            layer="semantic",
            category="world_rule",
            subject="天象",
            field="潮汐",
            value=long_rule,
            source_chapter=1,
        )
    )

    pack = MemoryOrchestrator(cfg).build_memory_pack(2, include_soft=False)

    assert pack["hard_constraints"][0]["value"] == long_rule
    assert pack["stats"]["hard_omitted"] == 0


@pytest.mark.parametrize(
    "directive",
    [
        "请模仿海明威的文风。",
        "请用抒情诗意的笔调写。",
        "采用全知视角叙述。",
        "请以冷峻克制的笔调描述故事。",
        "文字要简洁有力。",
        "只使用简单语言。",
        "每章末尾必须出现一个意外。",
    ],
)
def test_creative_style_directives_are_not_facts(directive):
    assert sanitize_fact_text(directive) == ""


def test_world_rule_boundary_distinguishes_story_law_from_chapter_recipe():
    assert sanitize_world_rule_text("必须让每章末尾都安排一次反转。") == ""
    assert sanitize_world_rule_text("月门只在子时开启，其他时辰无法通行。")


def test_soft_index_evidence_is_bounded_before_target_chapter(tmp_path):
    cfg = _cfg(tmp_path)
    index = IndexManager(cfg)
    index.record_state_change(
        StateChangeMeta("hero", "identity", "unknown", "trusted", "prior", 1)
    )
    index.record_state_change(
        StateChangeMeta("hero", "identity", "trusted", "FUTURE_SECRET", "future", 10)
    )
    index.upsert_relationship(
        RelationshipMeta("hero", "ally", "盟友", "此前关系", 1)
    )
    index.upsert_relationship(
        RelationshipMeta("hero", "future", "FUTURE_RELATION", "future", 10)
    )
    index.record_appearance("prior-person", 1, ["prior-person"])
    index.record_appearance("future-person", 10, ["future-person"])

    pack = MemoryOrchestrator(cfg).build_memory_pack(2)
    serialized = str(pack)

    assert "trusted" in serialized
    assert "此前关系" in serialized
    assert "prior-person" in serialized
    assert "FUTURE_SECRET" not in serialized
    assert "FUTURE_RELATION" not in serialized
    assert "future-person" not in serialized


@pytest.mark.parametrize("current_chapter", [10, 2, -1, True, "1", None])
def test_working_memory_never_exposes_untrusted_state_snapshot(
    tmp_path,
    current_chapter,
):
    cfg = _cfg(tmp_path)
    progress = {}
    if current_chapter is not None:
        progress["current_chapter"] = current_chapter
    cfg.state_file.write_text(
        __import__("json").dumps(
            {
                "progress": progress,
                "protagonist_state": {"identity": "FUTURE_SECRET"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack = MemoryOrchestrator(cfg).build_memory_pack(2)

    assert "FUTURE_SECRET" not in str(pack)

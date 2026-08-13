#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from data_modules.consistency_context import sanitize_story_contracts
from data_modules.story_contracts import persist_story_seed


def test_sanitize_story_contracts_strips_style_and_craft_payloads():
    cleaned = sanitize_story_contracts(
        {
            "master_setting": {
                "master_constraints": {
                    "core_tone": "先压后爆",
                    "pacing_strategy": "三章内首个反打",
                },
                "route": {
                    "primary_genre": "玄幻",
                    "recommended_base_tables": ["命名规则", "场景写法"],
                    "recommended_dynamic_tables": ["桥段套路", "人设与关系"],
                },
                "base_context": [
                    {"_table": "命名规则", "编号": "NR-001"},
                    {"_table": "场景写法", "编号": "SP-001"},
                ],
            },
            "chapter_brief": {
                "reasoning": {
                    "genre": "玄幻",
                    "style_priority": "热血冲突",
                    "pacing_strategy": "快推慢收",
                    "inject_target": "CHAPTER_BRIEF.writing_guidance",
                },
                "dynamic_context": [{"_table": "桥段套路", "编号": "TR-001"}],
            },
            "volume_brief": {
                "selected_pacing": {"wave": "压抑后爆"},
                "anti_patterns": [
                    {"text": "配角抢戏", "source_table": "人设与关系"},
                    {"text": "打脸收尾太软", "source_table": "爽点与节奏"},
                    {"text": "节奏标签化", "source_table": "裁决规则"},
                ],
            },
            "review_contract": {
                "anti_patterns": [
                    {"text": "情绪标签化", "source_table": "题材与调性推理"},
                ]
            },
        }
    )

    master = cleaned["master_setting"]
    assert master["master_constraints"] == {}
    assert master["route"]["recommended_base_tables"] == ["命名规则"]
    assert master["route"]["recommended_dynamic_tables"] == ["人设与关系"]
    assert [row["编号"] for row in master["base_context"]] == ["NR-001"]

    chapter = cleaned["chapter_brief"]
    assert chapter["reasoning"] == {"genre": "玄幻"}
    assert chapter["dynamic_context"] == []

    texts = {row["text"] for row in cleaned["volume_brief"]["anti_patterns"]}
    assert texts == {"配角抢戏"}
    assert "wave" not in (cleaned["volume_brief"].get("selected_pacing") or {})
    assert cleaned["review_contract"]["anti_patterns"] == []


def test_sanitize_story_contracts_leaves_empty_contracts_empty():
    cleaned = sanitize_story_contracts(
        {
            "master_setting": {},
            "chapter_brief": {},
            "volume_brief": {},
            "review_contract": {},
        }
    )
    assert cleaned["master_setting"] == {}
    assert cleaned["chapter_brief"] == {}
    assert cleaned["volume_brief"] == {}
    assert cleaned["review_contract"] == {}


def test_persist_story_seed_does_not_write_style_fields(tmp_path):
    persist_story_seed(
        project_root=tmp_path,
        master_payload={
            "meta": {"schema_version": "story-system/v1", "contract_type": "MASTER_SETTING"},
            "route": {
                "primary_genre": "玄幻",
                "recommended_base_tables": ["命名规则", "场景写法"],
                "recommended_dynamic_tables": ["桥段套路"],
            },
            "master_constraints": {"core_tone": "先压后爆", "pacing_strategy": "快推"},
            "base_context": [{"_table": "场景写法", "编号": "SP-001"}],
            "source_trace": [],
            "override_policy": {"locked": [], "append_only": [], "override_allowed": []},
        },
        chapter_payload={
            "meta": {"schema_version": "story-system/v1", "contract_type": "CHAPTER_BRIEF", "chapter": 1},
            "reasoning": {"genre": "玄幻", "style_priority": "热血"},
            "dynamic_context": [{"_table": "桥段套路", "编号": "TR-001"}],
            "override_allowed": {"chapter_focus": "试炼"},
        },
        anti_patterns=[{"text": "打脸收尾太软", "source_table": "爽点与节奏"}],
    )

    import json

    master = json.loads((tmp_path / ".story-system" / "MASTER_SETTING.json").read_text(encoding="utf-8"))
    chapter = json.loads((tmp_path / ".story-system" / "chapters" / "chapter_001.json").read_text(encoding="utf-8"))
    anti = json.loads((tmp_path / ".story-system" / "anti_patterns.json").read_text(encoding="utf-8"))

    assert master["master_constraints"] == {}
    assert master["route"]["recommended_dynamic_tables"] == []
    assert chapter["dynamic_context"] == []
    assert chapter["reasoning"] == {"genre": "玄幻"}
    assert anti == []
    markdown = (tmp_path / ".story-system" / "MASTER_SETTING.md").read_text(encoding="utf-8")
    assert "调性" not in markdown
    assert "节奏" not in markdown

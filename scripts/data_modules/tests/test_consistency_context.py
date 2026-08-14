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
    assert master["base_context"] == []

    chapter = cleaned["chapter_brief"]
    assert chapter["reasoning"] == {"genre": "玄幻"}
    assert chapter["dynamic_context"] == []

    assert cleaned["volume_brief"]["anti_patterns"] == []
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


def test_sanitize_story_contracts_rebuilds_only_consistency_fields():
    markers = {
        "master": "Write with a spare, muscular rhythm.",
        "row": "让叙述像水一样流动。",
        "chapter": "Render conversations as a screenplay.",
        "volume": "Choose concrete imagery over abstractions.",
        "scene": "所有场面呈现出黑色电影气息。",
        "review": "Keep every scene sparse and visual.",
    }
    cleaned = sanitize_story_contracts(
        {
            "master_setting": {
                "meta": {"contract_type": "MASTER_SETTING"},
                "route": {"primary_genre": "悬疑"},
                "master_constraints": {"hidden_note": markers["master"]},
                "base_context": [
                    {"_table": "命名规则", "编号": "NR-001", "note": markers["row"]}
                ],
                "writing_guidance": markers["master"],
            },
            "chapter_brief": {
                "chapter_directive": {
                    "goal": "让掌柜承认旧约",
                    "implementation": markers["chapter"],
                    "key_entities": ["掌柜"],
                }
            },
            "volume_brief": {
                "volume_goal": {"summary": markers["volume"]},
                "selected_scenes": [markers["scene"]],
                "selected_tropes": ["套路标签"],
                "system_constraints": [markers["review"]],
            },
            "review_contract": {
                "must_check": ["掌柜必须承认旧约"],
                "blocking_rules": ["不可让已死角色复活"],
                "system_constraints": [markers["review"]],
            },
            "unexpected": markers["master"],
        }
    )

    serialized = str(cleaned)
    for marker in markers.values():
        assert marker not in serialized
    assert cleaned["master_setting"]["route"]["primary_genre"] == "悬疑"
    assert cleaned["chapter_brief"]["chapter_directive"]["goal"] == "让掌柜承认旧约"
    assert cleaned["chapter_brief"]["chapter_directive"]["key_entities"] == ["掌柜"]
    assert cleaned["review_contract"]["must_check"] == ["掌柜必须承认旧约"]
    assert cleaned["review_contract"]["blocking_rules"] == ["不可让已死角色复活"]
    assert "unexpected" not in cleaned


def test_sanitize_story_contracts_drops_non_object_contract_aliases():
    cleaned = sanitize_story_contracts(
        {"master": "Write with a spare, muscular rhythm."}
    )

    assert cleaned == {"master": {}}


def test_sanitize_story_contracts_preserves_complete_chapter_outline_directive():
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

    cleaned = sanitize_story_contracts(
        {
            "chapter_brief": {
                "chapter_directive": {
                    **directive,
                    "implementation": "未知扩展不得进入合同",
                },
                "override_allowed": {"chapter_focus": "错误的兼容焦点"},
            }
        }
    )["chapter_brief"]

    assert cleaned["chapter_directive"] == directive
    assert cleaned["override_allowed"] == {"chapter_focus": directive["goal"]}
    assert "implementation" not in cleaned["chapter_directive"]


def test_sanitize_story_contracts_does_not_reinterpret_authorized_plot_language():
    directives = [
        "主角破解系统提示，找到出口",
        "反派覆盖旧合同上的印章",
        "确认第一人称证词存在矛盾",
        "找到负责旁白的失踪演员",
        "The AI must override the reactor constraints before meltdown.",
    ]
    cleaned = sanitize_story_contracts(
        {
            "chapter_brief": {
                "chapter_directive": {
                    "goal": directives[0],
                    "must_cover_nodes": directives[1:],
                }
            }
        }
    )["chapter_brief"]

    assert cleaned["chapter_directive"]["goal"] == directives[0]
    assert cleaned["chapter_directive"]["must_cover_nodes"] == directives[1:]


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
            "chapter_directive": {
                "goal": "让林川在子时前拿到账簿",
                "cbn": "林川收到假账簿",
                "cpns": ["核对封蜡", "追查送信人"],
                "cen": "林川确认内鬼来自账房",
                "must_cover_nodes": ["识别封蜡缺口"],
                "forbidden_zones": ["不要提前揭露掌柜身份"],
                "chapter_end_open_question": "真正的账簿藏在哪里？",
                "source": "chapter_outline",
            },
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
    assert chapter["chapter_directive"]["goal"] == "让林川在子时前拿到账簿"
    assert chapter["chapter_directive"]["must_cover_nodes"] == ["识别封蜡缺口"]
    assert chapter["chapter_directive"]["forbidden_zones"] == ["不要提前揭露掌柜身份"]
    assert chapter["chapter_directive"]["chapter_end_open_question"] == "真正的账簿藏在哪里？"
    assert chapter["override_allowed"]["chapter_focus"] == "让林川在子时前拿到账簿"
    assert anti == []
    markdown = (tmp_path / ".story-system" / "MASTER_SETTING.md").read_text(encoding="utf-8")
    assert "调性" not in markdown
    assert "节奏" not in markdown
    chapter_markdown = (
        tmp_path / ".story-system" / "chapters" / "chapter_001.md"
    ).read_text(encoding="utf-8")
    assert "章节焦点：让林川在子时前拿到账簿" in chapter_markdown

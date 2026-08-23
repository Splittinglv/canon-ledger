#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from data_modules.canon_v3.fact_boundary import (
    FactBoundaryClass,
    classify_author_axiom_leaf,
    classify_setting_leaf,
)


@pytest.mark.parametrize(
    "field",
    [
        "文风",
        "叙事视角",
        "真正渴望",
        "人物动机",
        "性格",
        "价值观",
        "恐惧",
        "偏好",
        "性格缺陷",
        "人设类型",
        "成长弧",
        "目标读者",
    ],
)
def test_known_soft_fields_never_become_canon(field: str) -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "设定集/主角卡.md",
                "section": "主角",
                "field": field,
                "value": "任意作者设计",
                "category": "character_state",
            }
        )
        == FactBoundaryClass.KNOWN_SOFT
    )


@pytest.mark.parametrize(
    "field",
    ["姓名", "身份", "生死", "地点", "境界", "规则", "代价", "持有者"],
)
def test_explicit_objective_fields_are_hard_facts(field: str) -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "设定集/主角卡.md",
                "section": "主角",
                "field": field,
                "value": "客观值",
                "category": "character_state",
            }
        )
        == FactBoundaryClass.HARD_FACT
    )


def test_custom_setting_field_is_ambiguous_instead_of_guessed() -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "设定集/自定义.md",
                "section": "作者自定义",
                "field": "镜面回声",
                "value": "只在月下出现",
                "category": "story_fact",
            }
        )
        == FactBoundaryClass.AMBIGUOUS
    )


@pytest.mark.parametrize(
    "field",
    ["目标", "弱点", "核心弱点", "goal", "title", "style", "voice", "belief"],
)
def test_generic_design_or_fact_fields_require_classification(field: str) -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "设定集/自定义.md",
                "section": "自定义",
                "field": field,
                "value": "可能是人物动机，也可能是客观规则目标",
                "category": "story_fact",
            }
        )
        == FactBoundaryClass.AMBIGUOUS
    )


def test_architectural_style_is_not_confused_with_writing_style() -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "设定集/世界观.md",
                "section": "城市",
                "field": "建筑风格",
                "value": "哥特式",
                "category": "story_fact",
            }
        )
        == FactBoundaryClass.HARD_FACT
    )


def test_role_design_section_does_not_hide_objective_identity() -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "设定集/角色.md",
                "section": "角色设计",
                "field": "姓名",
                "value": "沈砡",
                "category": "character_state",
            }
        )
        == FactBoundaryClass.HARD_FACT
    )


def test_normalized_legacy_payload_source_is_classified() -> None:
    assert (
        classify_setting_leaf(
            {
                "id": "setting-old",
                "category": "character_state",
                "subject": "主角卡",
                "field": "真正渴望（可能不自知）",
                "value": "证明自己",
                "payload": {
                    "source": "设定集/主角卡.md",
                    "section": "主角卡",
                },
            }
        )
        == FactBoundaryClass.KNOWN_SOFT
    )


@pytest.mark.parametrize(
    ("subject", "field"),
    [
        ("protagonist", "name"),
        ("initial_world", "currency_system"),
        ("golden_finger", "irreversible_cost"),
        ("characters", "co_protagonists"),
    ],
)
def test_supported_initial_objective_fields_remain_hard(
    subject: str,
    field: str,
) -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "legacy:initial_canon",
                "subject": subject,
                "field": field,
                "value": "客观值",
                "category": (
                    "world_rule"
                    if subject == "initial_world"
                    else "character_state"
                ),
            }
        )
        == FactBoundaryClass.HARD_FACT
    )


@pytest.mark.parametrize("field", ["title", "genre", "viewpoint", "motivation"])
def test_initial_design_metadata_is_soft(field: str) -> None:
    assert (
        classify_setting_leaf(
            {
                "source": "legacy:initial_canon",
                "subject": "project",
                "field": field,
                "value": "作者设计",
                "category": "story_fact",
            }
        )
        == FactBoundaryClass.KNOWN_SOFT
    )


@pytest.mark.parametrize(
    ("source", "section", "field", "value", "category"),
    [
        (
            "设定集/自定义.md",
            "写作规则",
            "规则",
            "对白要简短",
            "world_rule",
        ),
        (
            "设定集/自定义.md",
            "自定义风格",
            "风格要求",
            "多用短句",
            "story_fact",
        ),
        (
            "设定集/长期文风.md",
            "偏好",
            "规则",
            "冷峻表达",
            "world_rule",
        ),
        (
            "设定集/世界观.md",
            "世界规则",
            "规则",
            "每段最多三句话",
            "world_rule",
        ),
    ],
)
def test_custom_writing_rules_cannot_be_promoted_by_world_rule_category(
    source: str,
    section: str,
    field: str,
    value: str,
    category: str,
) -> None:
    assert classify_setting_leaf(
        {
            "source": source,
            "section": section,
            "field": field,
            "value": value,
            "category": category,
        }
    ) == FactBoundaryClass.KNOWN_SOFT


@pytest.mark.parametrize(
    ("key", "category", "value"),
    [
        ("core_motivation", "character_permanent_state", "永恒复仇"),
        ("hero_personality", "character_identity", "冷漠寡言"),
        ("immutable_law", "world_rule", "全书文风冷峻，短句为主"),
        ("dialogue_law", "world_rule", "对白要简短"),
        ("rule_17", "world_rule", "每段最多三句话"),
    ],
)
def test_author_axiom_boundary_checks_key_category_and_actual_value(
    key: str, category: str, value: str
) -> None:
    assert classify_author_axiom_leaf(
        axiom_key=key,
        category=category,
        value=value,
    ) == FactBoundaryClass.KNOWN_SOFT


def test_author_axiom_boundary_keeps_objective_world_rule() -> None:
    assert classify_author_axiom_leaf(
        axiom_key="death_is_irreversible",
        category="world_rule",
        value="死者不能复生",
    ) == FactBoundaryClass.HARD_FACT

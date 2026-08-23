#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Versioned product boundary for imported, non-manuscript setting leaves.

This module deliberately classifies *field semantics*, not prose tone.  A
legacy setting leaf is only promoted when it names an objective fact family;
known craft/design fields are advisory and unknown custom fields require a
human classification before migration may publish ``CURRENT``.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Mapping


FACT_BOUNDARY_POLICY_VERSION = "canon-v3/fact-boundary/v1"


class FactBoundaryClass(str, Enum):
    HARD_FACT = "hard_fact"
    KNOWN_SOFT = "known_soft"
    AMBIGUOUS = "ambiguous"


_SOFT_FIELDS = frozenset(
    {
        "文风",
        "写作风格",
        "文笔",
        "叙事风格",
        "叙事视角",
        "视角",
        "口吻",
        "语气",
        "行文节奏",
        "写作节奏",
        "题材",
        "目标读者",
        "卖点",
        "爽点",
        "真正渴望",
        "核心欲望",
        "欲望",
        "人物动机",
        "核心动机",
        "动机",
        "人物目标",
        "角色目标",
        "内在目标",
        "外在目标",
        "愿望",
        "恐惧",
        "价值观",
        "性格",
        "人物性格",
        "角色性格",
        "性格特征",
        "人格",
        "人格特质",
        "性格缺陷",
        "人设类型",
        "人物模板",
        "人物原型",
        "角色定位",
        "剧情功能",
        "成长弧",
        "角色弧",
        "人物弧",
        "人物弧光",
        "真正渴望（可能不自知）",
        "习惯",
        "爱好",
        "偏好",
        "口头禅",
        "处事方式",
        "行为风格",
        "相处模式",
        "标题",
        "书名",
        "genre",
        "primary_genre",
        "author_genre_label",
        "tone",
        "pacing",
        "viewpoint",
        "motivation",
        "desire",
        "fear",
        "values",
        "personality",
        "trait",
        "traits",
        "habit",
        "preference",
        "flaw",
        "archetype",
        "character_arc",
        "target_audience",
    }
)

_SOFT_SECTIONS = frozenset(
    {
        "文风提示词",
        "写作偏好",
        "长期文风",
        "整本小说风格",
        "目标读者",
        "作品卖点",
        "人物弧光",
    }
)

_SOFT_SOURCE_PARTS = (
    "文风",
    "写作偏好",
    "创作偏好",
    "写作规则",
    "叙事偏好",
    "行文偏好",
)

_SOFT_SECTION_PARTS = (
    "文风",
    "文笔",
    "写作",
    "创作偏好",
    "叙事风格",
    "叙事偏好",
    "行文",
    "语言风格",
    "自定义风格",
    "长期风格",
    "全书风格",
    "整本小说风格",
    "作品风格",
    "风格要求",
    "style",
    "prose",
    "narrative style",
    "writing preference",
)

_SOFT_FIELD_PARTS = (
    "文风",
    "文笔",
    "写作风格",
    "写作偏好",
    "写作节奏",
    "叙事风格",
    "叙事视角",
    "叙述视角",
    "行文",
    "语言风格",
    "风格要求",
    "句式",
    "措辞",
    "口吻",
    "语气",
    "对白风格",
)

_AUTHOR_AXIOM_SOFT_KEY_PARTS = (
    *_SOFT_FIELD_PARTS,
    "outline",
    "plot",
    "style",
    "prose",
    "tone",
    "voice",
    "pacing",
    "preference",
    "motivation",
    "personality",
    "character arc",
    "character_arc",
    "archetype",
    "desire",
    "fear",
    "trait",
    "habit",
    "target audience",
    "target_audience",
    "大纲",
    "章纲",
    "剧情",
    "动机",
    "性格",
    "人格",
    "人设",
    "成长弧",
    "角色弧",
    "欲望",
    "渴望",
    "恐惧",
    "价值观",
    "偏好",
    "爱好",
    "习惯",
    "口头禅",
    "目标读者",
)

_SETTING_CRAFT_VALUE_RE = re.compile(
    r"(?:文风|文笔|写作(?:风格|偏好|节奏|规则|要求)|"
    r"叙事(?:风格|视角|节奏|口吻)|行文|语言风格|风格要求|"
    r"句式|短句|长句|措辞|对白(?:风格|节奏|要|应)|口吻|语气|"
    r"(?:每|每一)(?:段|句|行).{0,12}(?:最多|最少|不超过|不少于|只能|必须|保持).{0,12}(?:句话|句|字数|字|段|行)|"
    r"(?:每|每一)(?:章|节).{0,16}(?:场|次|个|段|句|字)|"
    r"(?:第一|第二|第三|有限|全知|单一|多重)人称|"
    r"(?:感叹号|问号|逗号|句号|标点|自然段|段落长度|句子长度|句长|字数)|"
    r"(?:多用|少用|避免使用|不要使用|禁用).{0,12}(?:形容词|副词|成语|四字|标点|比喻|排比|短句|长句)|"
    r"(?:对白|旁白|描写).{0,8}(?:简短|克制|冷峻|口语|书面|精炼|冗长)|"
    r"留白|阅读体验|可读性|目标读者|作品卖点|爽点|"
    r"\b(?:writing\s+style|prose|narrative\s+style|tone|voice|pacing|"
    r"preference|target[ _-]?audience)\b)",
    re.IGNORECASE,
)

_AUTHOR_AXIOM_SOFT_VALUE_RE = re.compile(
    r"(?:文风|文笔|写作(?:风格|偏好|节奏|规则|要求)|"
    r"叙事(?:风格|视角|节奏|口吻)|行文|语言风格|风格要求|"
    r"句式|短句|长句|措辞|对白(?:风格|节奏|要|应)|口吻|语气|"
    r"(?:每|每一)(?:段|句|行).{0,12}(?:最多|最少|不超过|不少于|只能|必须|保持).{0,12}(?:句话|句|字数|字|段|行)|"
    r"(?:每|每一)(?:章|节).{0,16}(?:场|次|个|段|句|字)|"
    r"(?:第一|第二|第三|有限|全知|单一|多重)人称|"
    r"(?:感叹号|问号|逗号|句号|标点|自然段|段落长度|句子长度|句长|字数)|"
    r"(?:多用|少用|避免使用|不要使用|禁用).{0,12}(?:形容词|副词|成语|四字|标点|比喻|排比|短句|长句)|"
    r"(?:对白|旁白|描写).{0,8}(?:简短|克制|冷峻|口语|书面|精炼|冗长)|"
    r"留白|阅读体验|可读性|"
    r"目标读者|作品卖点|爽点|人物动机|角色动机|性格|人格|人设|"
    r"成长弧|角色弧|人物弧|价值观|欲望|渴望|恐惧|章纲|大纲|"
    r"\b(?:writing\s+style|prose|narrative\s+style|tone|voice|pacing|"
    r"preference|motivation|personality|character[ _-]?arc|target[ _-]?audience)\b)",
    re.IGNORECASE,
)

_HARD_FIELDS = frozenset(
    {
        "姓名",
        "身份",
        "年龄",
        "性别",
        "种族",
        "阵营",
        "生死",
        "状态",
        "境界",
        "等级",
        "地点",
        "位置",
        "持有者",
        "归属",
        "关系",
        "时间",
        "日期",
        "规则",
        "硬约束",
        "限制",
        "代价",
        "冷却",
        "禁忌",
        "货币",
        "职业",
        "称号",
        "人物称号",
        "道具",
        "制度",
        "建筑风格",
        "name",
    }
)

_INITIAL_HARD_FIELDS = {
    "world": frozenset(
        {
            "scale",
            "factions",
            "power_system_type",
            "social_class",
            "resource_distribution",
            "currency_system",
            "currency_exchange",
            "sect_hierarchy",
            "cultivation_chain",
            "cultivation_subtiers",
        }
    ),
    "protagonist": frozenset({"name"}),
    "golden_finger": frozenset(
        {"name", "type", "visibility", "irreversible_cost"}
    ),
    "characters": frozenset({"heroine_names", "co_protagonists"}),
}

_INITIAL_PROJECT_SOFT_FIELDS = frozenset(
    {
        "title",
        "genre",
        "primary_genre",
        "author_genre_label",
        "style",
        "voice",
        "tone",
        "pacing",
        "viewpoint",
        "target_audience",
    }
)

_FIELD_ANNOTATION_RE = re.compile(r"[（(].*?[）)]\s*$")


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _payload(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    value = fact.get("payload")
    return value if isinstance(value, Mapping) else {}


def _field_key(value: Any) -> str:
    text = _text(value)
    return _FIELD_ANNOTATION_RE.sub("", text).strip().lower()


def _contains_part(value: str, parts: tuple[str, ...]) -> bool:
    normalized = value.casefold().replace("-", " ")
    return any(part.casefold() in normalized for part in parts)


def _author_axiom_value_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_text(item) for item in value)
    return _text(value)


def classify_author_axiom_leaf(
    *,
    axiom_key: Any,
    category: Any,
    value: Any,
) -> FactBoundaryClass:
    """Keep the managed axiom channel fact-only across key/category/value.

    The category taxonomy is intentionally hard-only, so a soft key or an
    actual craft/persona value cannot be laundered through ``world_rule`` and
    then approved into Canon.
    """

    key = _text(axiom_key).casefold().replace("-", " ")
    category_text = _text(getattr(category, "value", category)).casefold()
    value_text = _author_axiom_value_text(value)
    if _contains_part(key, _AUTHOR_AXIOM_SOFT_KEY_PARTS):
        return FactBoundaryClass.KNOWN_SOFT
    if _contains_part(category_text, _AUTHOR_AXIOM_SOFT_KEY_PARTS):
        return FactBoundaryClass.KNOWN_SOFT
    if _AUTHOR_AXIOM_SOFT_VALUE_RE.search(value_text):
        return FactBoundaryClass.KNOWN_SOFT
    return FactBoundaryClass.HARD_FACT


def classify_setting_leaf(fact: Mapping[str, Any]) -> FactBoundaryClass:
    """Classify one structured setting leaf without guessing from prose tone.

    Unknown/custom fields remain ambiguous.  They require a managed author
    decision before becoming Canon; callers must not silently promote them.
    """

    payload = _payload(fact)
    source = _text(fact.get("source") or payload.get("source")).replace(
        "\\", "/"
    )
    section = _text(fact.get("section") or payload.get("section"))
    subject = _text(fact.get("subject"))
    field = _text(fact.get("field"))
    field_key = _field_key(field)
    value = _text(fact.get("value"))
    category = _text(fact.get("category"))

    if (
        source.endswith("/文风提示词.md")
        or _contains_part(source, _SOFT_SOURCE_PARTS)
        or section in _SOFT_SECTIONS
        or _contains_part(section, _SOFT_SECTION_PARTS)
    ):
        return FactBoundaryClass.KNOWN_SOFT
    if (
        source == "legacy:initial_canon"
        and subject == "project"
        and field_key in _INITIAL_PROJECT_SOFT_FIELDS
    ):
        return FactBoundaryClass.KNOWN_SOFT
    if (
        field in _SOFT_FIELDS
        or field_key in _SOFT_FIELDS
        or _contains_part(field, _SOFT_FIELD_PARTS)
    ):
        return FactBoundaryClass.KNOWN_SOFT
    if field == "事实" and value.startswith(
        ("本书题材", "写作风格", "叙事视角", "目标读者")
    ):
        return FactBoundaryClass.KNOWN_SOFT
    if _SETTING_CRAFT_VALUE_RE.search(value):
        return FactBoundaryClass.KNOWN_SOFT

    initial_section = (
        "world"
        if subject == "initial_world"
        else subject
        if source == "legacy:initial_canon"
        else ""
    )
    if field_key in _INITIAL_HARD_FIELDS.get(initial_section, ()):
        return FactBoundaryClass.HARD_FACT
    if category == "world_rule" or field in _HARD_FIELDS or field_key in _HARD_FIELDS:
        return FactBoundaryClass.HARD_FACT
    return FactBoundaryClass.AMBIGUOUS


def is_known_soft_setting(fact: Mapping[str, Any]) -> bool:
    return classify_setting_leaf(fact) == FactBoundaryClass.KNOWN_SOFT


__all__ = [
    "FACT_BOUNDARY_POLICY_VERSION",
    "FactBoundaryClass",
    "classify_author_axiom_leaf",
    "classify_setting_leaf",
    "is_known_soft_setting",
]

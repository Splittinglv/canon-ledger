#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strip craft/style payloads from write-time context.

Story System CSV 技法表仍可被显式 `--table` 检索，但默认写章上下文只保留
设定、章纲、人物、时间线、伏笔等一致性事实。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List

CRAFT_TABLES = frozenset(
    {
        "场景写法",
        "写作技法",
        "桥段套路",
        "爽点与节奏",
    }
)
CONSISTENCY_TABLES = frozenset(
    {
        "命名规则",
        "人设与关系",
        "金手指与设定",
    }
)
MASTER_STYLE_CONSTRAINT_KEYS = ("core_tone", "pacing_strategy")


def is_craft_table(name: Any) -> bool:
    return str(name or "").strip() in CRAFT_TABLES


def filter_consistency_tables(tables: Iterable[Any]) -> List[str]:
    filtered: List[str] = []
    for item in tables or []:
        name = str(item or "").strip()
        if name in CONSISTENCY_TABLES and name not in filtered:
            filtered.append(name)
    return filtered


def _drop_craft_rows(rows: Any) -> List[Any]:
    if not isinstance(rows, list):
        return []
    kept: List[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        table = row.get("_table") or row.get("table") or row.get("source_table")
        if is_craft_table(table):
            continue
        kept.append(row)
    return kept


def _sanitize_reasoning(reasoning: Any) -> Dict[str, Any]:
    if not isinstance(reasoning, dict):
        return {}
    genre = str(reasoning.get("genre") or "").strip()
    return {"genre": genre} if genre else {}


def _sanitize_anti_patterns(rows: Any) -> List[Any]:
    if not isinstance(rows, list):
        return []
    kept: List[Any] = []
    for row in rows:
        if isinstance(row, dict):
            if is_craft_table(row.get("source_table") or row.get("_table") or row.get("table")):
                continue
            if str(row.get("source_table") or "").strip() == "裁决规则":
                continue
            if str(row.get("source_table") or "").strip() == "题材与调性推理":
                continue
            kept.append(row)
            continue
        text = str(row or "").strip()
        if text:
            kept.append(text)
    return kept


def sanitize_story_contracts(contracts: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return a write-safe copy of runtime story contracts."""
    payload = deepcopy(contracts or {})
    master = payload.get("master") or payload.get("master_setting")
    if isinstance(master, dict) and master:
        constraints = dict(master.get("master_constraints") or {})
        for key in MASTER_STYLE_CONSTRAINT_KEYS:
            constraints.pop(key, None)
        master["master_constraints"] = constraints
        route = dict(master.get("route") or {})
        route["recommended_base_tables"] = filter_consistency_tables(
            route.get("recommended_base_tables") or []
        )
        route["recommended_dynamic_tables"] = filter_consistency_tables(
            route.get("recommended_dynamic_tables") or []
        )
        master["route"] = route
        master["base_context"] = _drop_craft_rows(master.get("base_context"))
        payload["master"] = master
        if "master_setting" in payload:
            payload["master_setting"] = master

    chapter = payload.get("chapter") or payload.get("chapter_brief")
    if isinstance(chapter, dict) and chapter:
        chapter["reasoning"] = _sanitize_reasoning(chapter.get("reasoning"))
        chapter["dynamic_context"] = []
        payload["chapter"] = chapter
        if "chapter_brief" in payload:
            payload["chapter_brief"] = chapter

    volume = payload.get("volume") or payload.get("volume_brief")
    if isinstance(volume, dict) and volume:
        selected_pacing = volume.get("selected_pacing")
        if isinstance(selected_pacing, dict):
            selected_pacing = dict(selected_pacing)
            selected_pacing.pop("wave", None)
            volume["selected_pacing"] = selected_pacing
        volume["anti_patterns"] = _sanitize_anti_patterns(volume.get("anti_patterns"))
        payload["volume"] = volume
        if "volume_brief" in payload:
            payload["volume_brief"] = volume

    review = payload.get("review") or payload.get("review_contract")
    if isinstance(review, dict) and review:
        review["anti_patterns"] = _sanitize_anti_patterns(review.get("anti_patterns"))
        payload["review"] = review
        if "review_contract" in payload:
            payload["review_contract"] = review

    if "anti_patterns" in payload:
        payload["anti_patterns"] = _sanitize_anti_patterns(payload.get("anti_patterns"))
    return payload

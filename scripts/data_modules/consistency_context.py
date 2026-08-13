#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strip craft/style payloads from write-time story contracts.

Story System CSV 技法表仍可被显式检索；默认写章上下文只重建公开的
合同字段，并且只保留设定、章纲、人物、时间线、伏笔等一致性事实。
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List

from .fact_text import sanitize_fact_atom, sanitize_fact_text


CRAFT_TABLES = frozenset({"场景写法", "写作技法", "桥段套路", "爽点与节奏"})
CONSISTENCY_TABLES = frozenset({"命名规则", "人设与关系", "金手指与设定"})
MASTER_STYLE_CONSTRAINT_KEYS = ("core_tone", "pacing_strategy")

_META_FIELDS = frozenset({"schema_version", "contract_type", "generator_version", "chapter", "volume"})
_ROUTE_FIELDS = frozenset(
    {
        "primary_genre",
        "canonical_genre",
        "author_genre_label",
        "route_source",
        "genre_filter",
        "recommended_base_tables",
        "recommended_dynamic_tables",
    }
)
_CHAPTER_DIRECTIVE_FIELDS = frozenset(
    {
        "time_anchor",
        "chapter_span",
        "countdown",
        "key_entities",
    }
)
_BASE_ROW_FIELDS = frozenset(
    {
        "_table",
        "table",
        "source_table",
        "编号",
        "分类",
        "层级",
        "命名对象",
        "规则",
        "人设类型",
        "核心动机",
        "行为逻辑",
        "互动模式",
        "设定类型",
        "数值控制边界",
        "与剧情交互方式",
        "核心摘要",
        "_score",
        "score",
        "rank",
    }
)
_SOURCE_TRACE_FIELDS = frozenset(
    {"_table", "table", "source_table", "source_id", "id", "编号", "_score", "score", "rank"}
)
_ANTI_PATTERN_FIELDS = frozenset(
    {"text", "_table", "table", "source_table", "source_id", "id", "编号", "weight", "score"}
)
_CRAFT_KEY_RE = re.compile(
    r"(?:style|writing|prose|tone|voice|pacing|rhythm|cadence|imagery|literary|"
    r"diction|narrat|sentence|paragraph|prompt|guidance|technique|craft|trope|"
    r"文风|风格|文笔|写作|写法|润色|口吻|语气|笔调|行文|字句|韵律|节奏|"
    r"氛围|镜头|修辞|句式|段落|提示词|指导|技巧|套路|桥段|爽点)",
    re.IGNORECASE,
)
_DROP = object()


def is_craft_table(name: Any) -> bool:
    return str(name or "").strip() in CRAFT_TABLES


def filter_consistency_tables(tables: Iterable[Any]) -> List[str]:
    filtered: List[str] = []
    for item in tables or []:
        name = str(item or "").strip()
        if name in CONSISTENCY_TABLES and name not in filtered:
            filtered.append(name)
    return filtered


def _is_craft_key(key: Any) -> bool:
    return bool(_CRAFT_KEY_RE.search(str(key or "")))


def _safe_text(value: Any) -> str:
    text = str(value or "")
    return sanitize_fact_text(text, max_chars=max(1200, len(text))).strip()


def _safe_atom(value: Any, *, max_chars: int = 160) -> str:
    return sanitize_fact_atom(value, max_chars=max_chars).strip()


def _safe_fact_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = _safe_text(item)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _safe_directive_value(key: str, value: Any) -> Any:
    """Normalize only the documented outline-derived directive shapes."""
    if key in {"key_entities"}:
        if not isinstance(value, (list, tuple)):
            return _DROP
        atoms = [
            atom
            for item in value
            for atom in [_safe_atom(item, max_chars=120)]
            if atom
        ]
        return list(dict.fromkeys(atoms))
    if not isinstance(value, str):
        return _DROP
    cleaned = _safe_atom(value, max_chars=160)
    return cleaned if cleaned else _DROP


def _sanitize_value(value: Any, *, key: Any = None) -> Any:
    """Recursively retain factual values and fail closed on craft keys/text."""
    if key is not None and _is_craft_key(key):
        return _DROP
    if isinstance(value, str):
        cleaned = _safe_text(value)
        return cleaned if cleaned else _DROP
    if isinstance(value, dict):
        cleaned_dict: Dict[str, Any] = {}
        for child_key, child_value in value.items():
            cleaned = _sanitize_value(child_value, key=child_key)
            if cleaned is not _DROP:
                cleaned_dict[str(child_key)] = cleaned
        return cleaned_dict
    if isinstance(value, (list, tuple)):
        cleaned_list: List[Any] = []
        for child in value:
            cleaned = _sanitize_value(child)
            if cleaned is not _DROP:
                cleaned_list.append(cleaned)
        return cleaned_list
    if value is None:
        return _DROP
    return deepcopy(value)


def _sanitize_meta(meta: Any) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for key in _META_FIELDS:
        if key not in meta:
            continue
        value = meta[key]
        if key in {"chapter", "volume"}:
            if type(value) is int and value > 0:
                cleaned[key] = value
            continue
        atom = _safe_atom(value, max_chars=120)
        if atom:
            cleaned[key] = atom
    return cleaned


def _sanitize_route(route: Any) -> Dict[str, Any]:
    if not isinstance(route, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    for key in _ROUTE_FIELDS:
        if key not in route:
            continue
        if key in {"recommended_base_tables", "recommended_dynamic_tables"}:
            cleaned[key] = filter_consistency_tables(route.get(key) or [])
            continue
        atom = _safe_atom(route[key], max_chars=160)
        if atom:
            cleaned[key] = atom
    return cleaned


def _sanitize_rows(rows: Any, *, allowed_fields: frozenset[str]) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    kept: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        table = row.get("_table") or row.get("table") or row.get("source_table")
        if is_craft_table(table) or str(table or "").strip() in {"裁决规则", "题材与调性推理"}:
            continue
        cleaned: Dict[str, Any] = {}
        for key in allowed_fields:
            if key not in row:
                continue
            value = _sanitize_value(row[key], key=key)
            if value is not _DROP:
                cleaned[key] = value
        if cleaned:
            kept.append(cleaned)
    return kept


def _drop_craft_rows(rows: Any) -> List[Dict[str, Any]]:
    return _sanitize_rows(rows, allowed_fields=_BASE_ROW_FIELDS)


def _sanitize_source_trace(rows: Any) -> List[Dict[str, Any]]:
    return _sanitize_rows(rows, allowed_fields=_SOURCE_TRACE_FIELDS)


def _sanitize_reasoning(reasoning: Any) -> Dict[str, Any]:
    if not isinstance(reasoning, dict):
        return {}
    genre = _safe_text(reasoning.get("genre"))
    return {"genre": genre} if genre else {}


def _sanitize_anti_patterns(rows: Any) -> List[Any]:
    if not isinstance(rows, list):
        return []
    kept: List[Any] = []
    for row in rows:
        if isinstance(row, dict):
            table = row.get("source_table") or row.get("_table") or row.get("table")
            if is_craft_table(table) or str(table or "").strip() in {"裁决规则", "题材与调性推理"}:
                continue
            cleaned: Dict[str, Any] = {}
            for key in _ANTI_PATTERN_FIELDS:
                if key not in row:
                    continue
                value = _sanitize_value(row[key], key=key)
                if value is not _DROP:
                    cleaned[key] = value
            if cleaned.get("text"):
                kept.append(cleaned)
            continue
        text = _safe_text(row)
        if text:
            kept.append(text)
    return kept


def _sanitize_override_policy(policy: Any) -> Dict[str, List[str]]:
    if not isinstance(policy, dict):
        return {}
    cleaned: Dict[str, List[str]] = {}
    for key in ("locked", "append_only", "override_allowed"):
        values = policy.get(key)
        if not isinstance(values, list):
            continue
        cleaned[key] = [
            str(value).strip()
            for value in values
            if str(value or "").strip() and not _is_craft_key(value)
        ]
    return cleaned


def _sanitize_master(master: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "meta": _sanitize_meta(master.get("meta")),
        "route": _sanitize_route(master.get("route")),
        # Generic tables and untyped master extensions are design advice, not
        # project canon.  Explicit story facts live in the hard-memory ledger.
        "master_constraints": {},
        "base_context": [],
        "source_trace": [],
        "override_policy": _sanitize_override_policy(master.get("override_policy")),
    }


def _sanitize_chapter(chapter: Dict[str, Any]) -> Dict[str, Any]:
    directive: Dict[str, Any] = {}
    raw_directive = chapter.get("chapter_directive")
    if isinstance(raw_directive, dict):
        for key in _CHAPTER_DIRECTIVE_FIELDS:
            if key not in raw_directive:
                continue
            value = _safe_directive_value(key, raw_directive[key])
            if value is not _DROP:
                directive[key] = value

    cleaned = {
        "meta": _sanitize_meta(chapter.get("meta")),
        "override_allowed": {},
        "chapter_directive": directive,
        # Retrieved CSV rows are optional soft craft material and never part of
        # the default long-term-consistency contract.
        "dynamic_context": [],
        "source_trace": [],
    }
    reasoning = _sanitize_reasoning(chapter.get("reasoning"))
    if "reasoning" in chapter or reasoning:
        cleaned["reasoning"] = reasoning
    return cleaned


def _sanitize_volume(volume: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "meta": _sanitize_meta(volume.get("meta")),
        "volume_goal": {},
        "selected_tropes": [],
        "selected_pacing": {},
        "selected_scenes": [],
        "anti_patterns": [],
        "system_constraints": [],
        "overrides": {},
    }


def _sanitize_review(review: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {
        "meta": _sanitize_meta(review.get("meta")),
        "genre_specific_risks": [],
        "anti_patterns": [],
    }
    # These fields are plot/canon obligations consumed by pre-write gates.
    # Keep only declarative fragments; style/craft text is stripped by the
    # shared fact sanitizer.  Generic system constraints remain excluded.
    for key in (
        "must_check",
        "blocking_rules",
        "must_cover_nodes",
        "mandatory_nodes",
        "prohibitions",
    ):
        cleaned[key] = _safe_fact_list(review.get(key))
    thresholds: Dict[str, Any] = {}
    raw_thresholds = review.get("review_thresholds")
    if isinstance(raw_thresholds, dict):
        for key in ("blocking_count", "missed_nodes"):
            value = raw_thresholds.get(key)
            if type(value) is int and value >= 0:
                thresholds[key] = value
    cleaned["system_constraints"] = []
    cleaned["review_thresholds"] = thresholds
    cleaned["overrides"] = {}
    return cleaned


def _set_aliases(
    result: Dict[str, Any],
    payload: Dict[str, Any],
    canonical: str,
    alias: str,
    sanitizer: Any,
) -> None:
    source = payload.get(canonical) or payload.get(alias)
    if isinstance(source, dict) and source:
        cleaned = sanitizer(source)
        result[canonical] = cleaned
        if alias in payload:
            result[alias] = deepcopy(cleaned)
        return
    for key in (canonical, alias):
        if key in payload:
            result[key] = {}


def sanitize_story_contracts(contracts: Dict[str, Any] | None) -> Dict[str, Any]:
    """Rebuild write-time contracts from public, consistency-safe fields.

    Unknown top-level and nested extension fields are intentionally omitted.
    Only explicitly typed consistency fields are rebuilt into the public view.
    """
    payload = deepcopy(contracts or {})
    result: Dict[str, Any] = {}
    _set_aliases(result, payload, "master", "master_setting", _sanitize_master)
    _set_aliases(result, payload, "chapter", "chapter_brief", _sanitize_chapter)
    _set_aliases(result, payload, "volume", "volume_brief", _sanitize_volume)
    _set_aliases(result, payload, "review", "review_contract", _sanitize_review)
    if "anti_patterns" in payload:
        result["anti_patterns"] = _sanitize_anti_patterns(payload.get("anti_patterns"))
    return result

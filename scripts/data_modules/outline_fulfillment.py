#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind data-agent fulfillment claims to the persisted chapter outline.

The chapter contract is the authoritative source for must-cover nodes.  A
data artifact must not be able to turn those obligations off by reporting an
empty ``planned_nodes`` list.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from chapter_outline_loader import (
        load_chapter_execution_directive,
        load_chapter_plot_structure,
    )
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import (
        load_chapter_execution_directive,
        load_chapter_plot_structure,
    )

from .consistency_context import (
    sanitize_chapter_directive_text,
    sanitize_story_contracts,
)


def merged_planned_nodes(directive: Any) -> list[str]:
    """Merge canonical and legacy must-cover fields without reordering."""
    if not isinstance(directive, dict):
        return []
    nodes: list[str] = []
    for key in ("must_cover_nodes", "mandatory_nodes"):
        values = directive.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value and value not in nodes:
                nodes.append(value)
    return nodes


def load_authoritative_chapter_goal(
    project_root: str | Path,
    chapter: int,
) -> str | None:
    """读取现代章合同中的权威目标，并保留纯旧版项目兼容性。

    章合同一旦存在，``chapter_directive.goal`` 就必须是非空文本；不能再
    回退到 ``override_allowed.chapter_focus``，否则合同落盘时丢失目标也会
    被静默接受。没有章合同、章纲里也没有目标时返回 ``None``，供不含
    Story System 的旧版项目和纯单元边界继续使用。
    """
    root = Path(project_root)
    target_chapter = int(chapter)
    try:
        outline_directive = (
            load_chapter_execution_directive(root, target_chapter) or {}
        )
    except Exception as exc:
        raise ValueError("chapter_outline_read_failed") from exc
    outline_goal = sanitize_chapter_directive_text(
        outline_directive.get("goal")
        if isinstance(outline_directive, dict)
        else ""
    )

    path = (
        root
        / ".story-system"
        / "chapters"
        / f"chapter_{target_chapter:03d}.json"
    )
    if not path.is_file():
        story_root = root / ".story-system"
        modern_contract_tree = any(
            candidate.exists()
            for candidate in (
                story_root / "MASTER_SETTING.json",
                story_root / "volumes",
                story_root / "reviews",
            )
        )
        if outline_goal or modern_contract_tree:
            raise ValueError("chapter_contract_missing_goal")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("chapter_contract_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("chapter_contract_must_be_object")

    meta = payload.get("meta")
    if isinstance(meta, dict) and "chapter" in meta:
        declared = meta.get("chapter")
        if type(declared) is not int or declared != target_chapter:
            raise ValueError("chapter_contract_chapter_mismatch")

    raw_directive = payload.get("chapter_directive")
    if not isinstance(raw_directive, dict):
        raise ValueError("chapter_contract_missing_goal")
    raw_goal = raw_directive.get("goal")
    if not isinstance(raw_goal, str) or not raw_goal.strip():
        raise ValueError("chapter_contract_missing_goal")
    goal = sanitize_chapter_directive_text(raw_goal)
    if not goal:
        raise ValueError("chapter_contract_goal_invalid")
    if outline_goal and goal != outline_goal:
        raise ValueError("chapter_contract_outline_goal_mismatch")
    return goal


def load_authoritative_planned_nodes(
    project_root: str | Path,
    chapter: int,
) -> list[str] | None:
    """Return persisted must-cover nodes, or ``None`` for legacy contracts.

    ``None`` means that the contract contains no authoritative node field and
    preserves compatibility with old projects.  An explicitly present empty
    field returns ``[]`` and is still authoritative.
    """
    root = Path(project_root)
    try:
        outline_structure = load_chapter_plot_structure(root, int(chapter)) or {}
    except Exception as exc:
        raise ValueError("chapter_outline_read_failed") from exc
    outline_nodes = [
        item
        for item in (outline_structure.get("mandatory_nodes") or [])
        if isinstance(item, str) and item
    ]

    path = (
        root
        / ".story-system"
        / "chapters"
        / f"chapter_{int(chapter):03d}.json"
    )
    if not path.is_file():
        if outline_nodes:
            raise ValueError("chapter_contract_missing_must_cover_nodes")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("chapter_contract_invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("chapter_contract_must_be_object")

    meta = payload.get("meta")
    if isinstance(meta, dict) and "chapter" in meta:
        declared = meta.get("chapter")
        if type(declared) is not int or declared != int(chapter):
            raise ValueError("chapter_contract_chapter_mismatch")

    raw_directive = payload.get("chapter_directive")
    if raw_directive is None:
        if outline_nodes:
            raise ValueError("chapter_contract_missing_must_cover_nodes")
        return None
    if not isinstance(raw_directive, dict):
        raise ValueError("chapter_directive_must_be_object")
    has_authoritative_field = any(
        key in raw_directive for key in ("must_cover_nodes", "mandatory_nodes")
    )
    if not has_authoritative_field:
        if outline_nodes:
            raise ValueError("chapter_contract_missing_must_cover_nodes")
        return None
    for key in ("must_cover_nodes", "mandatory_nodes"):
        if key not in raw_directive:
            continue
        raw_nodes = raw_directive[key]
        if not isinstance(raw_nodes, list):
            raise ValueError("chapter_must_cover_nodes_must_be_list")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in raw_nodes
        ):
            raise ValueError("chapter_must_cover_node_must_be_nonempty_text")

    cleaned_contracts = sanitize_story_contracts({"chapter": payload})
    chapter_contract = cleaned_contracts.get("chapter") or {}
    directive = chapter_contract.get("chapter_directive") or {}
    nodes = merged_planned_nodes(directive)
    raw_count = sum(
        len(raw_directive.get(key) or [])
        for key in ("must_cover_nodes", "mandatory_nodes")
        if key in raw_directive
    )
    if raw_count and not nodes:
        raise ValueError("chapter_must_cover_nodes_sanitized_empty")
    if outline_nodes and nodes != outline_nodes:
        raise ValueError("chapter_contract_outline_nodes_mismatch")
    return nodes


def fulfillment_node_errors(
    fulfillment: Any,
    authoritative_nodes: list[str] | None,
) -> list[str]:
    """Validate that fulfillment is a complete partition of outline nodes."""
    if authoritative_nodes is None:
        return []

    def values(name: str) -> list[Any]:
        if isinstance(fulfillment, dict):
            value = fulfillment.get(name)
        else:
            value = getattr(fulfillment, name, None)
        return value if isinstance(value, list) else []

    planned = values("planned_nodes")
    covered = values("covered_nodes")
    missed = values("missed_nodes")
    errors: list[str] = []

    if planned != authoritative_nodes:
        errors.append("fulfillment_planned_nodes_mismatch")
        return errors
    if any(not isinstance(item, str) for item in [*covered, *missed]):
        errors.append("fulfillment_node_must_be_text")
        return errors
    if len(covered) != len(set(covered)) or len(missed) != len(set(missed)):
        errors.append("fulfillment_node_duplicate")
    overlap = set(covered).intersection(missed)
    if overlap:
        errors.append("fulfillment_node_covered_and_missed")
    expected = set(authoritative_nodes)
    classified = set(covered).union(missed)
    if classified != expected:
        errors.append("fulfillment_node_partition_mismatch")
    return errors

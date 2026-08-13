#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize the public hard-constraint envelope without losing corruption."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .schema import HARD_CONSTRAINT_CATEGORIES


def normalize_hard_constraints(memory_pack: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Return ``(items, error)`` for canonical and documented legacy shapes.

    An empty list is a valid authoritative answer. A malformed producer value
    is not equivalent to an empty memory store and must block context use.
    """
    if not isinstance(memory_pack, dict):
        return [], "memory_pack_must_be_object"

    if "hard_constraints" in memory_pack:
        raw = memory_pack.get("hard_constraints")
    elif "active_constraints" in memory_pack:
        raw = memory_pack.get("active_constraints")
    else:
        # Older producers legitimately omitted both keys when the store was
        # empty. Keep that narrow compatibility behavior.
        return [], ""

    normalized: List[Dict[str, Any]] = []
    if isinstance(raw, dict):
        if "items" in raw:
            raw = raw.get("items")
        else:
            category_aliases = {
                "world_rules": "world_rule",
                "open_loops": "open_loop",
                "reader_promises": "reader_promise",
                "relationships": "relationship",
            }
            unknown = set(raw) - set(category_aliases)
            if unknown:
                return [], "hard_constraints_unknown_group"
            grouped: List[Any] = []
            for key, category in category_aliases.items():
                rows = raw.get(key, [])
                if not isinstance(rows, list):
                    return [], f"hard_constraints_group_must_be_list:{key}"
                for row in rows:
                    if not isinstance(row, dict):
                        return [], f"hard_constraint_must_be_object:{key}"
                    item = dict(row)
                    item.setdefault("category", category)
                    grouped.append(item)
            raw = grouped

    if not isinstance(raw, list):
        return [], "hard_constraints_must_be_list"

    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            return [], f"hard_constraint_must_be_object:{index}"
        item = dict(row)
        item_id = item.get("id")
        category = item.get("category")
        value = item.get("value")
        if not isinstance(item_id, str) or not item_id.strip():
            return [], f"hard_constraint_missing_id:{index}"
        if category not in HARD_CONSTRAINT_CATEGORIES:
            return [], f"hard_constraint_invalid_category:{index}"
        if not isinstance(value, str) or not value.strip():
            return [], f"hard_constraint_missing_value:{index}"
        normalized.append(item)
    return normalized, ""

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize the public hard-constraint envelope without losing corruption."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .schema import HARD_CONSTRAINT_CATEGORIES


def normalize_hard_constraints(memory_pack: Any) -> Tuple[List[Dict[str, Any]], str]:
    """校验当前 ``hard_constraints`` 平铺列表，返回条目和错误码。"""
    if not isinstance(memory_pack, dict):
        return [], "memory_pack_must_be_object"

    if "hard_constraints" not in memory_pack:
        return [], "hard_constraints_missing"
    raw = memory_pack.get("hard_constraints")

    normalized: List[Dict[str, Any]] = []
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scratchpad 压缩器。
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

from .schema import (
    CATEGORY_KEY_RULES,
    CATEGORY_TO_BUCKET,
    PERSISTENT_ACTIVE_CATEGORIES,
    MemoryItem,
    ScratchpadData,
    memory_item_key,
    now_iso,
)


def _key_for(item: MemoryItem) -> Tuple:
    return memory_item_key(item)


def _is_resolved_lifecycle(item: MemoryItem) -> bool:
    if item.category not in {"open_loop", "reader_promise"}:
        return False
    if item.status == "resolved":
        return True
    payload = item.payload or {}
    state = str(
        payload.get("lifecycle_status") or payload.get("status") or ""
    ).strip().lower()
    return state in {"resolved", "closed", "done", "paid_off", "payoff"}


def _is_resolved_open_loop(item: MemoryItem) -> bool:
    """Backward-compatible private helper retained for older test/plugins."""
    return item.category == "open_loop" and _is_resolved_lifecycle(item)


def compact_scratchpad(data: ScratchpadData, max_items: int = 500) -> ScratchpadData:
    if data.count_items() <= max_items:
        return data

    # 1) 同 key 的 outdated 只保留最新，避免历史膨胀。
    for bucket in CATEGORY_TO_BUCKET.values():
        rows: List[MemoryItem] = list(getattr(data, bucket))
        latest_outdated: Dict[Tuple, MemoryItem] = {}
        keep: List[MemoryItem] = []
        for row in rows:
            if row.status != "outdated":
                keep.append(row)
                continue
            key = _key_for(row)
            prev = latest_outdated.get(key)
            if prev is None or (row.updated_at or "") >= (prev.updated_at or ""):
                latest_outdated[key] = row
        keep.extend(latest_outdated.values())
        setattr(data, bucket, keep)

    # 2) 清理已回收伏笔与读者承诺。旧 schema 可能仍标为 active、只在
    # payload 写 resolved；删除前必须留下 canonical/public/legacy 三种精确
    # ID tombstone，避免延迟创建投影把义务重开。
    resolved_ids: Dict[str, List[str]] = {
        "open_loop": [],
        "reader_promise": [],
    }
    for category, bucket, field in (
        ("open_loop", "open_loops", "status"),
        ("reader_promise", "reader_promises", "promise"),
    ):
        for row in list(getattr(data, bucket)):
            if not _is_resolved_lifecycle(row):
                continue
            payload = row.payload or {}
            resolved_ids[category].append(row.id)
            resolved_ids[category].append(str(payload.get("lifecycle_id") or ""))
            legacy_raw = (
                f"{category}|{row.value or row.subject}|{field}|{row.source_chapter}"
            )
            resolved_ids[category].append(
                f"mem-{category}-{hashlib.sha256(legacy_raw.encode('utf-8')).hexdigest()[:16]}"
            )

    if any(resolved_ids.values()):
        meta = data.meta if isinstance(data.meta, dict) else {}
        data.meta = meta
        ledger = meta.get("resolved_lifecycle_ids")
        if not isinstance(ledger, dict):
            ledger = {}
            meta["resolved_lifecycle_ids"] = ledger
        for category, category_ids in resolved_ids.items():
            known = ledger.get(category)
            if not isinstance(known, list):
                known = []
            ledger[category] = sorted(
                {
                    str(item).strip()
                    for item in [*known, *category_ids]
                    if str(item).strip()
                }
            )
    data.open_loops = [
        row for row in data.open_loops if not _is_resolved_lifecycle(row)
    ]
    data.reader_promises = [
        row for row in data.reader_promises if not _is_resolved_lifecycle(row)
    ]

    # 3) 时间线是可追溯的精确事实，不能用摘要替换旧事件。展示层可以按
    # 预算裁剪，但存储层始终保留每个稳定 timeline_id。

    # 4) 若仍超限，按状态和新鲜度做全局截断。所有 active 持久事实
    # （规则、关系、角色状态及生命周期约束）不能为了满足缓存容量而
    # 静默丢弃；当它们本身超过 max_items 时，一致性优先于软容量上限。
    if data.count_items() > max_items:
        mandatory: List[Tuple[str, MemoryItem]] = []
        ranked: List[Tuple[str, MemoryItem]] = []
        for bucket in CATEGORY_TO_BUCKET.values():
            for row in list(getattr(data, bucket)):
                if (
                    row.status == "active"
                    and row.category in PERSISTENT_ACTIVE_CATEGORIES
                ):
                    mandatory.append((bucket, row))
                else:
                    ranked.append((bucket, row))

        ranked.sort(
            key=lambda item: (
                0 if item[1].status == "active" else 1,
                -int(item[1].source_chapter or 0),
                item[1].updated_at or "",
            )
        )
        mandatory.sort(key=lambda item: (item[1].source_chapter, item[1].id))
        remaining = max(0, max_items - len(mandatory))
        keep = [*mandatory, *ranked[:remaining]]
        kept_ids = {item.id for _, item in keep}
        for bucket in CATEGORY_TO_BUCKET.values():
            rows = [row for row in list(getattr(data, bucket)) if row.id in kept_ids]
            setattr(data, bucket, rows)

    data.meta = {**dict(data.meta or {}), "last_updated": now_iso(), "total_items": data.count_items()}
    return data

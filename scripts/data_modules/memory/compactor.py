#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scratchpad 压缩器。
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

from .schema import CATEGORY_KEY_RULES, CATEGORY_TO_BUCKET, MemoryItem, ScratchpadData, memory_item_key, now_iso


def _key_for(item: MemoryItem) -> Tuple:
    return memory_item_key(item)


def _is_resolved_open_loop(item: MemoryItem) -> bool:
    if item.category != "open_loop":
        return False
    if item.status == "resolved":
        return True
    state = str((item.payload or {}).get("status", "") or "").strip().lower()
    return state in {"resolved", "closed", "done", "paid_off", "payoff"}


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

    # 2) 清理已回收伏笔。旧 schema 可能仍标为 active、只在 payload
    # 写 resolved；删除前必须留下精确 ID tombstone，避免旧创建重放重开。
    resolved_loop_ids: List[str] = []
    for row in data.open_loops:
        if not _is_resolved_open_loop(row):
            continue
        payload = row.payload or {}
        resolved_loop_ids.append(row.id)
        resolved_loop_ids.append(str(payload.get("lifecycle_id") or ""))
        # Reconstruct the exact deterministic legacy identity from the fields
        # that old writers persisted.  This lets a delayed canonical creation
        # prove it is the same pre-upgrade obligation without prose search.
        raw = f"open_loop|{row.value or row.subject}|status|{row.source_chapter}"
        resolved_loop_ids.append(
            f"mem-open_loop-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
        )
    if resolved_loop_ids:
        meta = data.meta if isinstance(data.meta, dict) else {}
        data.meta = meta
        ledger = meta.get("resolved_lifecycle_ids")
        if not isinstance(ledger, dict):
            ledger = {}
            meta["resolved_lifecycle_ids"] = ledger
        known = ledger.get("open_loop")
        if not isinstance(known, list):
            known = []
        ledger["open_loop"] = sorted(
            {str(item).strip() for item in [*known, *resolved_loop_ids] if str(item).strip()}
        )
    data.open_loops = [row for row in data.open_loops if not _is_resolved_open_loop(row)]

    # 3) 压缩过旧 timeline（与当前最新章节相距 50 章以上）。
    timeline = sorted(data.timeline, key=lambda x: x.source_chapter)
    if timeline:
        latest_chapter = max(x.source_chapter for x in timeline)
        old = [x for x in timeline if (latest_chapter - x.source_chapter) > 50]
        fresh = [x for x in timeline if (latest_chapter - x.source_chapter) <= 50]
        if len(old) > 1:
            samples = []
            for row in old[:8]:
                label = row.value or row.subject or row.field or row.id
                if label:
                    samples.append(str(label))
            summary_text = "；".join(samples) if samples else "早期关键事件"
            summary_item = MemoryItem(
                id=f"timeline-summary-upto-{old[-1].source_chapter}",
                layer="semantic",
                category="story_fact",
                subject="timeline_summary",
                field=f"<=ch{old[-1].source_chapter}",
                value=f"早期事件摘要：{summary_text}",
                payload={
                    "from_chapter": old[0].source_chapter,
                    "to_chapter": old[-1].source_chapter,
                    "items_merged": len(old),
                },
                status="active",
                source_chapter=old[-1].source_chapter,
                evidence=["compactor:timeline"],
                updated_at=now_iso(),
            )
            replaced = False
            for i, row in enumerate(list(data.story_facts)):
                if row.subject == summary_item.subject and row.subject == "timeline_summary":
                    data.story_facts[i] = summary_item
                    replaced = True
                    break
            if not replaced:
                data.story_facts.append(summary_item)
        data.timeline = fresh

    # 4) 若仍超限，按状态和新鲜度做全局截断。活跃伏笔和读者承诺
    # 是硬生命周期约束，不能为了满足缓存容量而静默丢弃；当它们本身
    # 超过 max_items 时，一致性优先于软容量上限。
    if data.count_items() > max_items:
        mandatory: List[Tuple[str, MemoryItem]] = []
        ranked: List[Tuple[str, MemoryItem]] = []
        for bucket in CATEGORY_TO_BUCKET.values():
            for row in list(getattr(data, bucket)):
                if row.status == "active" and row.category in {"open_loop", "reader_promise"}:
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

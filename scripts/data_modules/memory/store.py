#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scratchpad 持久化与查询。
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DataModulesConfig, get_config
from ..cli_output import print_error, print_success
from ..cli_args import normalize_global_project_root, load_json_arg

from .schema import (
    BUCKET_TO_CATEGORY,
    CATEGORY_KEY_RULES,
    CATEGORY_TO_BUCKET,
    MemoryItem,
    ScratchpadData,
    memory_item_key,
    now_iso,
)

try:
    from security_utils import atomic_write_json, read_json_safe
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json, read_json_safe

from filelock import FileLock


class ScratchpadManager:
    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()
        self.path = Path(self.config.scratchpad_file)
        self._lock = FileLock(str(self.path) + ".lock", timeout=30)

    def load(self) -> ScratchpadData:
        if not self.path.exists():
            return ScratchpadData.empty()
        payload = read_json_safe(self.path, default={})
        if not isinstance(payload, dict):
            return ScratchpadData.empty()
        return ScratchpadData.from_dict(payload)

    def save(self, data: ScratchpadData, _use_lock: bool = True) -> None:
        self.config.ensure_dirs()
        if bool(getattr(self.config, "memory_compactor_enabled", True)):
            threshold = max(1, int(getattr(self.config, "memory_compactor_threshold", 500)))
            if data.count_items() > threshold:
                from .compactor import compact_scratchpad

                data = compact_scratchpad(data, max_items=threshold)
        payload = data.to_dict()
        payload.setdefault("meta", {})
        payload["meta"]["last_updated"] = now_iso()
        payload["meta"]["total_items"] = data.count_items()
        atomic_write_json(self.path, payload, use_lock=_use_lock, backup=True)

    def _key_for(self, item: MemoryItem) -> tuple[Any, ...]:
        return memory_item_key(item)

    def upsert_item(self, item: MemoryItem) -> Dict[str, int]:
        normalized = item.normalized()
        with self._lock:
            data = self.load()
            bucket = CATEGORY_TO_BUCKET[normalized.category]
            rows: List[MemoryItem] = list(getattr(data, bucket))
            target_key = self._key_for(normalized)

            outdated = 0
            replaced_existing = False
            new_rows: List[MemoryItem] = []
            for row in rows:
                row_key = self._key_for(row)
                if row_key == target_key and row.id != normalized.id:
                    # 同 key 旧值降级为 outdated，保留审计轨迹
                    if row.status != "outdated":
                        row = MemoryItem(**{**asdict(row), "status": "outdated", "updated_at": now_iso()})
                        outdated += 1
                    replaced_existing = True
                elif row.id == normalized.id:
                    replaced_existing = True
                    continue
                new_rows.append(row)

            normalized.updated_at = normalized.updated_at or now_iso()
            new_rows.append(normalized)
            setattr(data, bucket, new_rows)
            self.save(data, _use_lock=False)

        return {
            "added": 0 if replaced_existing else 1,
            "updated": 1 if replaced_existing else 0,
            "outdated": outdated,
        }

    def mark_status(self, item_id: str, status: str) -> bool:
        if not item_id:
            return False
        with self._lock:
            data = self.load()
            updated = False
            for bucket in BUCKET_TO_CATEGORY:
                rows: List[MemoryItem] = getattr(data, bucket)
                for i, row in enumerate(rows):
                    if row.id == item_id:
                        rows[i] = MemoryItem(**{**asdict(row), "status": status, "updated_at": now_iso()})
                        updated = True
            if updated:
                self.save(data, _use_lock=False)
        return updated

    def lifecycle_ids(self, category: str) -> set[str]:
        """Return stable lifecycle IDs currently known for a category."""
        bucket = CATEGORY_TO_BUCKET.get(category)
        if not bucket:
            return set()
        data = self.load()
        ids: set[str] = set(self._resolved_lifecycle_ids(data, category))
        for row in getattr(data, bucket):
            lifecycle_id = self._row_lifecycle_id(row)
            if lifecycle_id:
                ids.add(lifecycle_id)
        return ids

    def lifecycle_sources(self, category: str) -> Dict[str, int]:
        """Return known lifecycle IDs and the chapter where each was created."""
        bucket = CATEGORY_TO_BUCKET.get(category)
        if not bucket:
            return {}
        data = self.load()
        sources: Dict[str, int] = {
            lifecycle_id: 0
            for lifecycle_id in self._resolved_lifecycle_ids(data, category)
        }
        for row in getattr(data, bucket):
            lifecycle_id = self._row_lifecycle_id(row)
            if lifecycle_id:
                sources[lifecycle_id] = int(row.source_chapter or 0)
        return sources

    def timeline_identity_conflicts(
        self, rows: List[Dict[str, Any]]
    ) -> List[str]:
        """Return timeline IDs that would overwrite a different stored fact."""
        known: Dict[str, tuple[int, str]] = {}
        for item in self.query(category="timeline", status=None):
            timeline_id = str((item.payload or {}).get("timeline_id") or item.id).strip()
            if timeline_id:
                known[timeline_id] = (int(item.source_chapter or 0), str(item.value or ""))

        conflicts: List[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            timeline_id = str(row.get("timeline_id") or "").strip()
            if not timeline_id or timeline_id not in known:
                continue
            try:
                chapter = int(row.get("chapter") or 0)
            except (TypeError, ValueError):
                chapter = 0
            event = str(row.get("event") or "")
            if known[timeline_id] != (chapter, event):
                conflicts.append(timeline_id)
        return conflicts

    @staticmethod
    def _row_lifecycle_id(row: MemoryItem) -> str:
        """Return the public target ID, including pre-lifecycle scratchpads.

        Older releases stored lifecycle rows without ``payload.lifecycle_id``.
        Their deterministic ``MemoryItem.id`` is the only lossless identifier
        left on disk, so expose it as the explicit migration target instead of
        attempting unsafe subject/content matching.
        """
        return str((row.payload or {}).get("lifecycle_id") or row.id or "").strip()

    @staticmethod
    def _resolved_lifecycle_ids(data: ScratchpadData, category: str) -> set[str]:
        ledger = (data.meta or {}).get("resolved_lifecycle_ids") or {}
        if not isinstance(ledger, dict):
            return set()
        values = ledger.get(category) or []
        if not isinstance(values, list):
            return set()
        return {str(value).strip() for value in values if str(value).strip()}

    @staticmethod
    def _remember_resolved_lifecycle(
        data: ScratchpadData,
        category: str,
        lifecycle_id: str,
    ) -> None:
        """Keep a compact tombstone so compaction cannot reopen an obligation."""
        meta = data.meta if isinstance(data.meta, dict) else {}
        data.meta = meta
        ledger = meta.get("resolved_lifecycle_ids")
        if not isinstance(ledger, dict):
            ledger = {}
            meta["resolved_lifecycle_ids"] = ledger
        values = ledger.get(category)
        if not isinstance(values, list):
            values = []
        lifecycle_id = str(lifecycle_id or "").strip()
        if lifecycle_id and lifecycle_id not in values:
            values.append(lifecycle_id)
            values.sort()
        ledger[category] = values

    def upsert_lifecycle_item(self, item: MemoryItem) -> Dict[str, int]:
        """Persist an obligation creation without reopening an existing item.

        Normal ``upsert_item`` intentionally replaces same-ID values for facts
        such as world rules.  Lifecycle creation is different: a delayed
        projection of the original creation event must be a no-op after a
        later chapter has already resolved it.
        """
        normalized = item.normalized()
        bucket = CATEGORY_TO_BUCKET[normalized.category]
        lifecycle_id = str((normalized.payload or {}).get("lifecycle_id") or "").strip()
        legacy_item_id = str((normalized.payload or {}).get("legacy_item_id") or "").strip()
        with self._lock:
            data = self.load()
            resolved_ids = self._resolved_lifecycle_ids(data, normalized.category)
            if lifecycle_id in resolved_ids or (legacy_item_id and legacy_item_id in resolved_ids):
                # A legacy row may have been closed by its public MemoryItem
                # ID before a delayed canonical creation is replayed.  Record
                # both aliases as tombstones so neither identity can reopen it.
                self._remember_resolved_lifecycle(data, normalized.category, lifecycle_id)
                self._remember_resolved_lifecycle(data, normalized.category, legacy_item_id)
                self.save(data, _use_lock=False)
                return {"added": 0, "updated": 0, "outdated": 0, "preserved": 1}
            rows: List[MemoryItem] = list(getattr(data, bucket))
            for index, row in enumerate(rows):
                row_lifecycle_id = self._row_lifecycle_id(row)
                if row.id == normalized.id or (lifecycle_id and row_lifecycle_id == lifecycle_id):
                    return {"added": 0, "updated": 0, "outdated": 0, "preserved": 1}
                if (
                    legacy_item_id
                    and row.id == legacy_item_id
                    and not str((row.payload or {}).get("lifecycle_id") or "").strip()
                ):
                    # Deterministic migration from the exact ID produced by
                    # the pre-lifecycle writer.  This is identity based; prose
                    # is never searched or compared to choose a target.
                    merged_payload = {**dict(row.payload or {}), **dict(normalized.payload or {})}
                    merged_payload.pop("legacy_item_id", None)
                    rows[index] = MemoryItem(
                        **{
                            **asdict(row),
                            "id": normalized.id,
                            "payload": merged_payload,
                            "evidence": list(dict.fromkeys([*row.evidence, *normalized.evidence])),
                            "updated_at": now_iso(),
                        }
                    )
                    setattr(data, bucket, rows)
                    self.save(data, _use_lock=False)
                    return {
                        "added": 0,
                        "updated": 1,
                        "outdated": 0,
                        "preserved": 0,
                        "migrated": 1,
                    }
            normalized.payload.pop("legacy_item_id", None)
            rows.append(normalized)
            setattr(data, bucket, rows)
            self.save(data, _use_lock=False)
        return {"added": 1, "updated": 0, "outdated": 0, "preserved": 0}

    def resolve_lifecycle_item(
        self,
        category: str,
        lifecycle_id: str,
        *,
        chapter: int,
        resolution: str,
        resolved_by: str,
    ) -> Dict[str, int]:
        """Resolve exactly one lifecycle item by its stable ID."""
        bucket = CATEGORY_TO_BUCKET.get(category)
        lifecycle_id = str(lifecycle_id or "").strip()
        if not bucket or not lifecycle_id:
            return {"matched": 0, "resolved": 0, "already_resolved": 0}

        with self._lock:
            data = self.load()
            if lifecycle_id in self._resolved_lifecycle_ids(data, category):
                return {"matched": 1, "resolved": 0, "already_resolved": 1}
            rows: List[MemoryItem] = list(getattr(data, bucket))
            for index, row in enumerate(rows):
                row_lifecycle_id = self._row_lifecycle_id(row)
                if row_lifecycle_id != lifecycle_id:
                    continue
                if row.status == "resolved":
                    return {"matched": 1, "resolved": 0, "already_resolved": 1}
                payload = dict(row.payload or {})
                payload.update(
                    {
                        "lifecycle_id": lifecycle_id,
                        "lifecycle_status": "resolved",
                        "resolved_chapter": int(chapter or 0),
                        "resolution": str(resolution or "").strip(),
                        "resolved_by": str(resolved_by or "").strip(),
                    }
                )
                rows[index] = MemoryItem(
                    **{
                        **asdict(row),
                        "payload": payload,
                        "status": "resolved",
                        "updated_at": now_iso(),
                    }
                )
                self._remember_resolved_lifecycle(data, category, lifecycle_id)
                setattr(data, bucket, rows)
                self.save(data, _use_lock=False)
                return {"matched": 1, "resolved": 1, "already_resolved": 0}
        return {"matched": 0, "resolved": 0, "already_resolved": 0}

    def query(
        self,
        category: Optional[str] = None,
        subject: Optional[str] = None,
        status: Optional[str] = "active",
    ) -> List[MemoryItem]:
        data = self.load()
        categories = [category] if category else list(CATEGORY_TO_BUCKET.keys())
        result: List[MemoryItem] = []
        for cat in categories:
            bucket = CATEGORY_TO_BUCKET.get(cat)
            if not bucket:
                continue
            rows: List[MemoryItem] = getattr(data, bucket)
            for row in rows:
                if subject and row.subject != subject:
                    continue
                if status and row.status != status:
                    continue
                result.append(row)
        return result

    def stats(self) -> Dict[str, Any]:
        data = self.load()
        by_category: Dict[str, int] = {}
        active = 0
        outdated = 0
        contradicted = 0
        tentative = 0
        resolved = 0
        for category, bucket in CATEGORY_TO_BUCKET.items():
            rows: List[MemoryItem] = getattr(data, bucket)
            by_category[category] = len(rows)
            for row in rows:
                if row.status == "active":
                    active += 1
                elif row.status == "outdated":
                    outdated += 1
                elif row.status == "contradicted":
                    contradicted += 1
                elif row.status == "tentative":
                    tentative += 1
                elif row.status == "resolved":
                    resolved += 1
        return {
            "total": data.count_items(),
            "active": active,
            "outdated": outdated,
            "contradicted": contradicted,
            "tentative": tentative,
            "resolved": resolved,
            "by_category": by_category,
            "path": str(self.path),
        }

    def dump(self) -> Dict[str, Any]:
        return self.load().to_dict()

    def conflicts(self) -> List[Dict[str, Any]]:
        data = self.load()
        conflicts: List[Dict[str, Any]] = []
        for category, bucket in CATEGORY_TO_BUCKET.items():
            key_count: Dict[tuple[Any, ...], int] = {}
            rows: List[MemoryItem] = getattr(data, bucket)
            for row in rows:
                if row.status != "active":
                    continue
                key = self._key_for(row)
                key_count[key] = key_count.get(key, 0) + 1
            for key, cnt in key_count.items():
                if cnt > 1:
                    conflicts.append({"category": category, "key": list(key), "active_items": cnt})
        return conflicts


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Memory Scratchpad CLI")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats")
    p_query = sub.add_parser("query")
    p_query.add_argument("--category", type=str, default=None)
    p_query.add_argument("--subject", type=str, default=None)
    p_query.add_argument("--status", type=str, default="active")
    sub.add_parser("dump")
    sub.add_parser("conflicts")

    p_update = sub.add_parser("update")
    p_update.add_argument("--chapter", type=int, required=True)
    p_update.add_argument("--data", required=True, help="章节结构化结果 JSON")

    sub.add_parser("bootstrap")

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))

    config = None
    if args.project_root:
        from project_locator import resolve_project_root

        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    manager = ScratchpadManager(config)

    if args.command == "stats":
        print_success(manager.stats(), message="memory_stats")
        return
    if args.command == "dump":
        print_success(manager.dump(), message="memory_dump")
        return
    if args.command == "conflicts":
        print_success(manager.conflicts(), message="memory_conflicts")
        return
    if args.command == "query":
        rows = [row.to_dict() for row in manager.query(args.category, args.subject, args.status)]
        print_success(rows, message="memory_query")
        return
    if args.command == "update":
        from .writer import MemoryWriter

        resolved_config = config or get_config()
        payload = load_json_arg(args.data, base_dir=resolved_config.project_root)
        writer = MemoryWriter(resolved_config)
        result = writer.update_from_chapter_result(args.chapter, payload)
        print_success(result, message="memory_updated")
        return
    if args.command == "bootstrap":
        from .bootstrap import bootstrap_from_index

        result = bootstrap_from_index(config or get_config())
        print_success(result, message="memory_bootstrapped")
        return

    print_error("UNKNOWN_COMMAND", "未知命令", suggestion="请查看 --help")

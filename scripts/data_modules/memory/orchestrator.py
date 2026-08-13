#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长期记忆编排器。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

from ..config import DataModulesConfig, get_config
from ..fact_text import sanitize_fact_atom, sanitize_fact_text
from .schema import (
    BUCKET_TO_CATEGORY,
    CATEGORY_TO_BUCKET,
    VALID_LAYERS,
    VALID_STATUSES,
    MemoryItem,
)
from .schema import HARD_CONSTRAINT_CATEGORIES
from .store import ScratchpadManager
from .budget import allocate_limits

try:
    from chapter_outline_loader import load_chapter_outline
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import load_chapter_outline


class MemoryOrchestrator:
    PRIORITY = {
        "world_rule": 0,
        "character_state": 1,
        "relationship": 2,
        "story_fact": 3,
        "open_loop": 4,
        "reader_promise": 5,
        "timeline": 6,
    }

    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()
        self.store = ScratchpadManager(self.config)
        self._index_manager_instance = None

    def _index_manager(self):
        # Hard-constraint reads must not create/migrate index.db.  The
        # structured index is needed only for optional episodic evidence.
        if self._index_manager_instance is None:
            from ..index_manager import IndexManager

            self._index_manager_instance = IndexManager(self.config)
        return self._index_manager_instance

    def _assert_store_readable(self) -> None:
        """Distinguish an empty scratchpad from a corrupt authoritative file."""
        path = self.store.path
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("scratchpad_root_must_be_object")
        meta = payload.get("meta", {})
        if not isinstance(meta, dict):
            raise ValueError("scratchpad_meta_must_be_object")
        for bucket in CATEGORY_TO_BUCKET.values():
            if bucket not in payload:
                continue
            rows = payload[bucket]
            if not isinstance(rows, list):
                raise ValueError(f"scratchpad_bucket_must_be_list:{bucket}")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"scratchpad_row_must_be_object:{bucket}")
                if str(row.get("category") or "") != BUCKET_TO_CATEGORY[bucket]:
                    raise ValueError(f"scratchpad_category_mismatch:{bucket}")
                if str(row.get("layer") or "") not in VALID_LAYERS:
                    raise ValueError(f"scratchpad_invalid_layer:{bucket}")
                if str(row.get("status") or "") not in VALID_STATUSES:
                    raise ValueError(f"scratchpad_invalid_status:{bucket}")
                for key in ("id", "subject", "field", "value"):
                    if not isinstance(row.get(key), str):
                        raise ValueError(f"scratchpad_invalid_{key}:{bucket}")
                source_chapter = row.get("source_chapter")
                if isinstance(source_chapter, bool) or not isinstance(source_chapter, int):
                    raise ValueError(f"scratchpad_invalid_source_chapter:{bucket}")
                if source_chapter < 0:
                    raise ValueError(f"scratchpad_invalid_source_chapter:{bucket}")
                if not isinstance(row.get("payload", {}), dict):
                    raise ValueError(f"scratchpad_invalid_payload:{bucket}")

    def build_memory_pack(
        self,
        chapter: int,
        task_type: str = "write",
        *,
        include_soft: bool = True,
    ) -> Dict[str, Any]:
        self._assert_store_readable()
        stored_active_items = self.store.query(status="active")
        # Writing chapter N may consume only facts established before N.
        # Source chapter 0 denotes imported/global setup facts.
        active_items = [
            item
            for item in stored_active_items
            if int(item.source_chapter or 0) == 0
            or 0 < int(item.source_chapter or 0) < int(chapter)
        ]
        future_filtered = len(stored_active_items) - len(active_items)
        hard_items = sorted(
            (item for item in active_items if item.category in HARD_CONSTRAINT_CATEGORIES),
            key=self._hard_sort_key,
        )
        hard_constraints: List[Dict[str, Any]] = []
        omitted_hard_ids: List[str] = []
        for item in hard_items:
            payload = self._serialize_hard_constraint(item)
            if payload is None:
                omitted_hard_ids.append(item.id)
            else:
                hard_constraints.append(payload)

        if not include_soft:
            warnings: List[Dict[str, Any]] = []
            if omitted_hard_ids:
                warnings.append(
                    {
                        "type": "unsafe_hard_constraint",
                        "count": len(omitted_hard_ids),
                        "ids": omitted_hard_ids,
                    }
                )
            return {
                "working_memory": [],
                "episodic_memory": [],
                "semantic_memory": [],
                "long_term_facts": [],
                "hard_constraints": hard_constraints,
                # Compatibility alias.  Consumers must prefer hard_constraints
                # and must not concatenate the two lists.
                "active_constraints": list(hard_constraints),
                "recent_changes": [],
                "warnings": warnings,
                "stats": {
                    "total": len(active_items),
                    "stored_active_total": len(stored_active_items),
                    "future_filtered": future_filtered,
                    "hard_total": len(hard_items),
                    "hard_injected": len(hard_constraints),
                    "hard_omitted": len(omitted_hard_ids),
                    "working_total": 0,
                    "episodic_total": 0,
                    "semantic_total": 0,
                    "injected": 0,
                    "layered_total_injected": len(hard_constraints),
                    "filtered": 0,
                    "conflicts": 0,
                },
            }

        outline = load_chapter_outline(self.config.project_root, chapter, max_chars=1500)

        working = self._build_working_memory(chapter=chapter, outline=outline)
        index_available = Path(self.config.index_db).is_file()
        episodic = (
            self._build_episodic_memory(chapter=chapter)
            if index_available
            else []
        )
        conflicts = self.store.conflicts()
        soft_items = [
            item for item in active_items if item.category not in HARD_CONSTRAINT_CATEGORIES
        ]
        filtered = self._filter_relevant(soft_items, chapter=chapter, outline=outline)

        max_items = max(1, int(getattr(self.config, "memory_orchestrator_max_items", 30)))
        limits = allocate_limits(max_items=max_items, task_type=task_type)
        semantic_items = self._apply_budget(filtered, max_items=limits["semantic"])
        working_items = working[: limits["working"]]
        episodic_items = episodic[: limits["episodic"]]
        semantic_payload = [
            payload
            for item in semantic_items
            for payload in [self._serialize_soft_evidence(item)]
            if payload is not None
        ]

        recent_changes = [
            self._sanitize_index_row(
                row,
                allowed={
                    "chapter", "entity_id", "field", "old", "new",
                    "old_value", "new_value", "reason",
                },
            )
            for row in (
                self._index_manager().get_recent_state_changes(
                limit=max(
                    1,
                    int(
                        getattr(
                            self.config,
                            "memory_orchestrator_recent_changes_limit",
                            10,
                        )
                    ),
                ),
                before_chapter=chapter,
            )
                if index_available
                else []
            )
        ]
        recent_changes = [row for row in recent_changes if row]
        warnings = []
        if omitted_hard_ids:
            warnings.append(
                {
                    "type": "unsafe_hard_constraint",
                    "count": len(omitted_hard_ids),
                    "ids": omitted_hard_ids,
                }
            )
        if conflicts:
            safe_conflicts = []
            for conflict in conflicts[:5]:
                safe_key = [
                    sanitize_fact_atom(value, max_chars=120) or "[unsafe]"
                    for value in (conflict.get("key") or [])
                ]
                safe_conflicts.append(
                    {
                        "category": sanitize_fact_atom(
                            conflict.get("category"), max_chars=40
                        ),
                        "key": safe_key,
                        "active_items": int(conflict.get("active_items") or 0),
                    }
                )
            warnings.append(
                {
                    "type": "memory_conflict",
                    "count": len(conflicts),
                    "sample": safe_conflicts,
                }
            )

        return {
            "working_memory": working_items,
            "episodic_memory": episodic_items,
            "semantic_memory": semantic_payload,
            # long_term_facts 保持对外 contract：仅包含可直接注入的长期语义事实。
            "long_term_facts": semantic_payload,
            "hard_constraints": hard_constraints,
            "active_constraints": list(hard_constraints),
            "recent_changes": list(recent_changes),
            "warnings": warnings,
            "stats": {
                "total": len(active_items),
                "stored_active_total": len(stored_active_items),
                "future_filtered": future_filtered,
                "hard_total": len(hard_items),
                "hard_injected": len(hard_constraints),
                "hard_omitted": len(omitted_hard_ids),
                "working_total": len(working),
                "episodic_total": len(episodic),
                "semantic_total": len(filtered),
                "injected": len(semantic_payload),
                "layered_total_injected": (
                    len(hard_constraints)
                    + len(working_items)
                    + len(episodic_items)
                    + len(semantic_payload)
                ),
                "filtered": max(0, len(soft_items) - len(filtered)),
                "conflicts": len(conflicts),
            },
        }

    @staticmethod
    def _hard_sort_key(item: MemoryItem) -> tuple[Any, ...]:
        category_order = {
            "world_rule": 0,
            "open_loop": 1,
            "reader_promise": 2,
            "relationship": 3,
        }
        urgency = 0.0
        if item.category in {"open_loop", "reader_promise"}:
            try:
                urgency = float((item.payload or {}).get("urgency") or 0.0)
                if not math.isfinite(urgency):
                    urgency = 0.0
            except (TypeError, ValueError):
                urgency = 0.0
        return (
            category_order.get(item.category, 99),
            -urgency,
            int(item.source_chapter or 0),
            item.id,
        )

    @staticmethod
    def _serialize_hard_constraint(item: MemoryItem) -> Dict[str, Any] | None:
        """Emit a closed, factual view of a mandatory scratchpad item."""
        raw_value = str(item.value or "")
        value = sanitize_fact_text(raw_value, max_chars=max(1, len(raw_value)))
        if not value:
            return None
        subject = sanitize_fact_atom(item.subject, max_chars=120)
        field = sanitize_fact_atom(item.field, max_chars=120)
        if (item.subject and not subject) or (item.field and not field):
            return None
        raw_payload = item.payload or {}
        payload: Dict[str, Any] = {}

        if item.category == "world_rule":
            scope = sanitize_fact_atom(raw_payload.get("scope"), max_chars=80)
            if scope:
                payload["scope"] = scope
        elif item.category in {"open_loop", "reader_promise"}:
            lifecycle_id = sanitize_fact_atom(
                raw_payload.get("lifecycle_id") or item.id,
                max_chars=160,
            )
            if lifecycle_id:
                payload["lifecycle_id"] = lifecycle_id
            try:
                urgency = float(raw_payload.get("urgency") or 0.0)
                payload["urgency"] = urgency if math.isfinite(urgency) else 0.0
            except (TypeError, ValueError):
                payload["urgency"] = 0.0
            raw_expected_payoff = str(raw_payload.get("expected_payoff") or "")
            expected_payoff = sanitize_fact_text(
                raw_expected_payoff,
                max_chars=max(1, len(raw_expected_payoff)),
            )
            if expected_payoff:
                payload["expected_payoff"] = expected_payoff
        # Relationship descriptions are optional free prose.  The canonical
        # from/to/type triple is sufficient for consistency and avoids
        # admitting instructions through an unbounded description field.

        safe_id = sanitize_fact_atom(item.id, max_chars=160)
        if not safe_id:
            return None
        return {
            "id": safe_id,
            "category": item.category,
            "subject": subject,
            "field": field,
            "value": value,
            "payload": payload,
            "status": "active",
            "source_chapter": int(item.source_chapter or 0),
        }

    @staticmethod
    def _serialize_soft_evidence(item: MemoryItem) -> Dict[str, Any] | None:
        """Expose soft memory as quoted facts, never arbitrary payload prose."""
        safe_id = sanitize_fact_atom(item.id, max_chars=160)
        subject = sanitize_fact_atom(item.subject, max_chars=120)
        field = sanitize_fact_atom(item.field, max_chars=120)
        if item.category == "character_state":
            value = sanitize_fact_atom(item.value, max_chars=160)
        else:
            value = sanitize_fact_text(item.value, max_chars=800)
        if not safe_id or not value:
            return None
        return {
            "id": safe_id,
            "layer": item.layer,
            "category": item.category,
            "subject": subject,
            "field": field,
            "value": value,
            "status": item.status,
            "source_chapter": int(item.source_chapter or 0),
        }

    @staticmethod
    def _sanitize_index_row(
        row: Dict[str, Any],
        *,
        allowed: set[str],
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key in sorted(allowed):
            if key not in row:
                continue
            value = row.get(key)
            if value is None or isinstance(value, (bool, int, float)):
                result[key] = value
                continue
            atom = sanitize_fact_atom(value, max_chars=160)
            if atom:
                result[key] = atom
                continue
            fact = sanitize_fact_text(value, max_chars=400)
            if fact:
                result[key] = fact
        return result

    def _filter_relevant(self, items: List[MemoryItem], chapter: int, outline: str) -> List[MemoryItem]:
        if not items:
            return []
        if not outline:
            return sorted(items, key=lambda x: (x.source_chapter, x.updated_at), reverse=True)

        keep: List[MemoryItem] = []
        source_window = max(1, int(getattr(self.config, "memory_orchestrator_source_window", 20)))
        for item in items:
            if item.subject and item.subject in outline:
                keep.append(item)
                continue
            if item.field and item.field in outline:
                keep.append(item)
                continue
            if item.value and item.value[:20] in outline:
                keep.append(item)
                continue
            if item.source_chapter > 0 and chapter - item.source_chapter <= source_window:
                keep.append(item)

        return sorted(keep, key=lambda x: (self.PRIORITY.get(x.category, 99), -x.source_chapter))

    def _apply_budget(self, items: List[MemoryItem], max_items: int) -> List[MemoryItem]:
        if max_items <= 0:
            return []
        if len(items) <= max_items:
            return list(items)
        return list(items[:max_items])

    def _load_state(self) -> Dict[str, Any]:
        path = self.config.state_file
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            import sys
            print(f"⚠️ state.json 读取失败: {exc}", file=sys.stderr)
            return {}

    def _load_recent_summaries(self, chapter: int, window: int) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        summary_dir = self.config.webnovel_dir / "summaries"
        if not summary_dir.exists():
            return result
        for ch in range(max(1, chapter - window), chapter):
            path = summary_dir / f"ch{ch:04d}.md"
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            text = sanitize_fact_text(text, max_chars=800)
            if text:
                result.append({"layer": "working", "source": "summary", "chapter": ch, "content": text})
        return result

    def _build_working_memory(self, chapter: int, outline: str) -> List[Dict[str, Any]]:
        state = self._load_state()
        result: List[Dict[str, Any]] = []
        if outline:
            result.append({"layer": "working", "source": "outline", "chapter": chapter, "content": outline})

        progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
        raw_state_chapter = progress.get("current_chapter")
        projected_payload_present = any(
            bool(state.get(key))
            for key in (
                "protagonist_state",
                "plot_threads",
                "disambiguation_pending",
            )
        )
        state_snapshot_safe = False
        if raw_state_chapter is None:
            state_snapshot_safe = not projected_payload_present
        elif type(raw_state_chapter) is int and raw_state_chapter >= 0:
            state_snapshot_safe = (
                raw_state_chapter == 0 or raw_state_chapter < int(chapter)
            )
        if state_snapshot_safe:
            state_export = {
                "protagonist_state": state.get("protagonist_state", {}),
                "plot_threads": state.get("plot_threads", {}),
                "disambiguation_pending": state.get("disambiguation_pending", []),
            }
            result.append(
                {
                    "layer": "working",
                    "source": "state_export",
                    "chapter": chapter,
                    "content": state_export,
                }
            )
        return result

    def _build_episodic_memory(self, chapter: int) -> List[Dict[str, Any]]:
        _ = chapter
        changes_limit = max(1, int(getattr(self.config, "memory_orchestrator_recent_changes_limit", 10)))
        rel_limit = max(1, min(20, changes_limit))

        index_manager = self._index_manager()
        recent_changes = index_manager.get_recent_state_changes(
            limit=changes_limit,
            before_chapter=chapter,
        )
        recent_relationships = index_manager.get_recent_relationships(
            limit=rel_limit,
            before_chapter=chapter,
        )
        recent_appearances = index_manager.get_recent_appearances(
            limit=rel_limit,
            before_chapter=chapter,
        )

        result: List[Dict[str, Any]] = []
        for row in recent_changes:
            content = self._sanitize_index_row(
                row,
                allowed={
                    "chapter", "entity_id", "field", "old", "new",
                    "old_value", "new_value", "reason",
                },
            )
            if not content:
                continue
            result.append(
                {
                    "layer": "episodic",
                    "source": "state_change",
                    "chapter": int(row.get("chapter") or 0),
                    "entity_id": content.get("entity_id", ""),
                    "field": content.get("field", ""),
                    "content": content,
                }
            )
        for row in recent_relationships:
            content = self._sanitize_index_row(
                row,
                allowed={
                    "chapter", "from_entity", "to_entity", "type",
                    "relationship_type", "description",
                },
            )
            if not content:
                continue
            result.append(
                {
                    "layer": "episodic",
                    "source": "relationship",
                    "chapter": int(row.get("chapter") or 0),
                    "entity_id": content.get("from_entity", ""),
                    "field": content.get("to_entity", ""),
                    "content": content,
                }
            )
        for row in recent_appearances:
            content = self._sanitize_index_row(
                row,
                allowed={
                    "chapter", "last_chapter", "entity_id", "scene_id",
                    "location", "appearance_type", "name", "total",
                },
            )
            if not content:
                continue
            result.append(
                {
                    "layer": "episodic",
                    "source": "appearance",
                    "chapter": int(
                        content.get("last_chapter")
                        or content.get("chapter")
                        or 0
                    ),
                    "entity_id": content.get("entity_id", ""),
                    "field": "appearance",
                    "content": content,
                }
            )

        result.sort(key=lambda x: int(x.get("chapter") or 0), reverse=True)
        return result

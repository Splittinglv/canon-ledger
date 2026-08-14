#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
章节结果 -> 长期记忆项映射。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from ..commit_artifacts import extraction_list
from ..config import DataModulesConfig, get_config
from ..fact_text import normalize_world_rule_payload, world_rule_evidence_in_commit
from ..urgency_utils import coerce_urgency
from .schema import MemoryItem
from .store import ScratchpadManager


class MemoryWriter:
    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()
        self.store = ScratchpadManager(self.config)

    def _item_id(self, category: str, subject: str, field: str, chapter: int) -> str:
        raw = f"{category}|{subject}|{field}|{chapter}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"mem-{category}-{digest}"

    def _stable_item_id(self, category: str, external_id: str) -> str:
        """Map an event-level stable ID to a stable scratchpad item ID."""
        return self._item_id(category, str(external_id or "").strip(), "identity", 0)

    def _upsert(self, item: MemoryItem, stats: Dict[str, Any]) -> None:
        result = self.store.upsert_item(item)
        stats["items_added"] += int(result.get("added", 0))
        stats["items_updated"] += int(result.get("updated", 0))
        stats["items_outdated"] += int(result.get("outdated", 0))

    def _upsert_current_relationship(
        self,
        item: MemoryItem,
        stats: Dict[str, Any],
    ) -> None:
        """Upsert a relationship without letting an old retry roll it back.

        Relationship identity is the same ``(from_entity, to_entity)`` key
        used by the scratchpad schema.  A newer active row therefore wins over
        a delayed projection of an earlier chapter; same-chapter replays remain
        idempotent through the normal ``upsert_item`` path.
        """
        for current in self.store.query(
            category="relationship",
            subject=item.subject,
            status="active",
        ):
            if current.field != item.field:
                continue
            if int(current.source_chapter or 0) > int(item.source_chapter or 0):
                stats["items_preserved"] = int(stats.get("items_preserved", 0)) + 1
                return
        self._upsert(item, stats)

    @staticmethod
    def _coerce_loop_content(payload: Dict[str, Any], event: Dict[str, Any]) -> str:
        """从 open_loop 事件 payload 多个候选字段里取出有意义的悬念内容。

        优先级：content → unanswered_question（信息悬疑）
        → loop_type + description（结构化）→ description → subject 兜底。
        若兜底到 subject（通常是角色 ID），加上 loop_type 前缀避免变成纯 ID。
        """
        for key in ("content", "unanswered_question"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value

        description = str(payload.get("description") or "").strip()
        loop_type = str(payload.get("loop_type") or "").strip()

        if description and loop_type:
            return f"{loop_type}：{description}"
        if description:
            return description
        if loop_type:
            return loop_type

        subject = str(event.get("subject") or "").strip()
        return subject

    @staticmethod
    def _relationship_from_event(event: Dict[str, Any]) -> Dict[str, Any] | None:
        """Normalize an accepted relationship event like IndexProjectionWriter."""
        payload = dict(event.get("payload") or {})
        from_entity = str(
            payload.get("from_entity") or event.get("subject") or ""
        ).strip()
        to_entity = str(payload.get("to_entity") or payload.get("to") or "").strip()
        rel_type = str(
            payload.get("relationship_type")
            or payload.get("relation_type")
            or payload.get("type")
            or ""
        ).strip()
        if not from_entity or not to_entity or not rel_type:
            return None
        return {
            "from": from_entity,
            "to": to_entity,
            "type": rel_type,
            "description": str(payload.get("description") or "").strip(),
        }

    def update_from_chapter_result(self, chapter: int, result: Dict[str, Any]) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "chapter": int(chapter),
            "items_added": 0,
            "items_updated": 0,
            "items_outdated": 0,
            "items_preserved": 0,
            "warnings": [],
        }

        # Stage 2: 零成本结构化映射
        for change in result.get("state_changes", []) or []:
            entity_id = str(change.get("entity_id", "") or "").strip()
            field = str(
                change.get("field", "")
                or change.get("field_path", "")
                or ""
            ).strip()
            if not entity_id or not field:
                continue
            new_val = change.get("new")
            if new_val is None:
                new_val = change.get("new_value")
            if new_val is None:
                new_val = change.get("to")
            old_val = change.get("old")
            if old_val is None:
                old_val = change.get("old_value")
            if old_val is None:
                old_val = change.get("from")
            item = MemoryItem(
                id=self._item_id("character_state", entity_id, field, chapter),
                layer="semantic",
                category="character_state",
                subject=entity_id,
                field=field,
                value=str(new_val if new_val is not None else "" or ""),
                payload={"old_value": old_val},
                source_chapter=int(chapter),
                evidence=[f"state_change:{entity_id}:{field}:{chapter}"],
            )
            self._upsert(item, stats)

        for entity in result.get("entities_new", []) or []:
            entity_id = str(entity.get("suggested_id") or entity.get("id") or "").strip()
            name = str(entity.get("name", "") or "").strip()
            if not entity_id:
                continue
            item = MemoryItem(
                id=self._item_id("character_state", entity_id, "first_seen", chapter),
                layer="semantic",
                category="character_state",
                subject=entity_id,
                field="first_seen",
                value=name,
                payload={
                    "tier": entity.get("tier"),
                    "type": entity.get("type") or entity.get("entity_type"),
                },
                source_chapter=int(chapter),
                evidence=[f"entity_new:{entity_id}:{chapter}"],
            )
            self._upsert(item, stats)

        for rel in result.get("relationships_new", []) or []:
            from_entity = str(rel.get("from") or rel.get("from_entity") or "").strip()
            to_entity = str(rel.get("to") or rel.get("to_entity") or "").strip()
            rel_type = str(rel.get("type", "") or "").strip()
            if not from_entity or not to_entity or not rel_type:
                continue
            item = MemoryItem(
                id=self._item_id("relationship", from_entity, to_entity, chapter),
                layer="semantic",
                category="relationship",
                subject=from_entity,
                field=to_entity,
                value=rel_type,
                payload={"description": rel.get("description", ""), "to_entity": to_entity},
                source_chapter=int(chapter),
                evidence=[f"relationship:{from_entity}:{to_entity}:{chapter}"],
            )
            self._upsert_current_relationship(item, stats)

        chapter_meta = result.get("chapter_meta") or {}
        hook = chapter_meta.get("hook")
        if isinstance(hook, dict):
            hook_content = str(hook.get("content", "") or "").strip()
            if hook_content:
                item = MemoryItem(
                    id=self._item_id("story_fact", "chapter_hook", str(chapter), chapter),
                    layer="semantic",
                    category="story_fact",
                    subject="chapter_hook",
                    field=str(chapter),
                    value=hook_content,
                    payload={"hook_type": hook.get("type"), "strength": hook.get("strength")},
                    source_chapter=int(chapter),
                    evidence=[f"chapter_meta:hook:{chapter}"],
                )
                self._upsert(item, stats)
        elif isinstance(hook, str) and hook.strip():
            item = MemoryItem(
                id=self._item_id("story_fact", "chapter_hook", str(chapter), chapter),
                layer="semantic",
                category="story_fact",
                subject="chapter_hook",
                field=str(chapter),
                value=hook.strip(),
                payload={},
                source_chapter=int(chapter),
                evidence=[f"chapter_meta:hook:{chapter}"],
            )
            self._upsert(item, stats)

        self._apply_consistency_facts(chapter, result, stats)

        return stats

    def _apply_consistency_facts(
        self,
        chapter: int,
        facts: Dict[str, Any],
        stats: Dict[str, Any],
    ) -> None:
        """把当前提交的一等时间线与世界规则写入可重建暂存投影。"""
        timeline_events = facts.get("timeline_events") or []
        for row in timeline_events:
            if not isinstance(row, dict):
                continue
            event = str(row.get("event", "") or "").strip()
            if not event:
                continue
            try:
                source_chapter = int(row.get("chapter") or chapter)
            except (TypeError, ValueError):
                source_chapter = int(chapter)
            timeline_id = str(row.get("timeline_id") or "").strip()
            if not timeline_id:
                stats["warnings"].append("时间线事件缺少 timeline_id，已拒绝写入")
                continue
            item = MemoryItem(
                id=self._stable_item_id("timeline", timeline_id),
                layer="semantic",
                category="timeline",
                subject=event[:64],
                field="event",
                value=event,
                payload={
                    "timeline_id": timeline_id,
                    "sequence": row.get("sequence"),
                    "time_hint": row.get("time_hint"),
                    "event_type": row.get("event_type"),
                },
                source_chapter=source_chapter,
                evidence=[f"chapter_commit:timeline:{chapter}"],
            )
            self._upsert(item, stats)

        world_rules = facts.get("world_rules") or []
        for row in world_rules:
            if not isinstance(row, dict):
                continue
            normalized_rule = normalize_world_rule_payload(
                {
                    "rule_content": row.get("rule"),
                    "rule_category": row.get("rule_category") or row.get("category"),
                    "domain": row.get("domain"),
                    "field": row.get("field"),
                    "scope": row.get("scope"),
                    "evidence_quote": row.get("evidence_quote"),
                },
                row.get("domain"),
            )
            if normalized_rule is None:
                continue
            rule = normalized_rule["rule_content"]
            subject = normalized_rule["domain"]
            field = normalized_rule["field"]
            item = MemoryItem(
                id=self._item_id("world_rule", subject, field, chapter),
                layer="semantic",
                category="world_rule",
                subject=subject,
                field=field,
                value=rule,
                payload={
                    "scope": normalized_rule["scope"],
                    "rule_category": normalized_rule["rule_category"],
                    "rule_text": rule,
                },
                source_chapter=int(chapter),
                evidence=[f"chapter_commit:world_rule:{chapter}"],
            )
            self._upsert(item, stats)

    def _lifecycle_events(
        self,
        accepted_events: List[Dict[str, Any]],
        chapter: int,
    ) -> tuple[list[MemoryItem], list[dict[str, str]], list[str]]:
        """Build lifecycle operations without any prose/subject matching."""
        creations: list[MemoryItem] = []
        resolutions: list[dict[str, str]] = []
        errors: list[str] = []

        for event in accepted_events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip()
            payload = dict(event.get("payload") or {})
            event_id = str(event.get("event_id") or "").strip()
            try:
                source_chapter = int(event.get("chapter") or chapter)
            except (TypeError, ValueError):
                source_chapter = int(chapter)

            if event_type == "open_loop_created":
                lifecycle_id = str(payload.get("loop_id") or event_id or "").strip()
                content = self._coerce_loop_content(payload, event)
                if not lifecycle_id or not content:
                    errors.append("invalid_open_loop_created")
                    continue
                creations.append(
                    MemoryItem(
                        id=self._stable_item_id("open_loop", lifecycle_id),
                        layer="semantic",
                        category="open_loop",
                        subject=content,
                        field="status",
                        value=content,
                        payload={
                            "lifecycle_id": lifecycle_id,
                            "lifecycle_status": "active",
                            "urgency": coerce_urgency(payload.get("urgency")),
                            "planted_chapter": payload.get("planted_chapter") or source_chapter,
                            "expected_payoff": payload.get("expected_payoff") or payload.get("loop_deadline"),
                            "created_by": event_id,
                        },
                        source_chapter=source_chapter,
                        evidence=[f"accepted_event:{event_id}"],
                    )
                )
            elif event_type == "promise_created":
                lifecycle_id = str(payload.get("promise_id") or event_id or "").strip()
                content = str(
                    payload.get("content")
                    or payload.get("description")
                    or event.get("subject")
                    or ""
                ).strip()
                if not lifecycle_id or not content:
                    errors.append("invalid_promise_created")
                    continue
                creations.append(
                    MemoryItem(
                        id=self._stable_item_id("reader_promise", lifecycle_id),
                        layer="semantic",
                        category="reader_promise",
                        subject=content,
                        field="promise",
                        value=content,
                        payload={
                            "lifecycle_id": lifecycle_id,
                            "lifecycle_status": "active",
                            "promise_type": payload.get("type") or "promise_created",
                            "target": payload.get("target") or event.get("subject") or "",
                            "created_by": event_id,
                        },
                        source_chapter=source_chapter,
                        evidence=[f"accepted_event:{event_id}"],
                    )
                )
            elif event_type == "open_loop_closed":
                lifecycle_id = str(
                    payload.get("loop_id")
                    or payload.get("target_loop_id")
                    or payload.get("open_loop_id")
                    or payload.get("target_id")
                    or payload.get("resolves_event_id")
                    or ""
                ).strip()
                if not lifecycle_id:
                    errors.append("missing_loop_id")
                    continue
                resolutions.append(
                    {
                        "category": "open_loop",
                        "lifecycle_id": lifecycle_id,
                        "chapter": str(source_chapter),
                        "resolution": str(
                            payload.get("resolution") or payload.get("description") or ""
                        ).strip(),
                        "event_id": event_id,
                    }
                )
            elif event_type == "promise_paid_off":
                lifecycle_id = str(
                    payload.get("promise_id")
                    or payload.get("target_promise_id")
                    or payload.get("target_id")
                    or payload.get("resolves_event_id")
                    or ""
                ).strip()
                if not lifecycle_id:
                    errors.append("missing_promise_id")
                    continue
                resolutions.append(
                    {
                        "category": "reader_promise",
                        "lifecycle_id": lifecycle_id,
                        "chapter": str(source_chapter),
                        "resolution": str(
                            payload.get("resolution") or payload.get("description") or ""
                        ).strip(),
                        "event_id": event_id,
                    }
                )
        return creations, resolutions, errors

    def _validate_lifecycle_targets(
        self,
        creations: list[MemoryItem],
        resolutions: list[dict[str, str]],
    ) -> list[str]:
        known = {
            "open_loop": self.store.lifecycle_sources("open_loop"),
            "reader_promise": self.store.lifecycle_sources("reader_promise"),
        }
        for item in creations:
            lifecycle_id = str((item.payload or {}).get("lifecycle_id") or "").strip()
            if lifecycle_id:
                known.setdefault(item.category, {})[lifecycle_id] = int(item.source_chapter or 0)

        errors: list[str] = []
        for resolution in resolutions:
            category = resolution["category"]
            lifecycle_id = resolution["lifecycle_id"]
            category_sources = known.get(category, {})
            if lifecycle_id not in category_sources:
                errors.append(f"unmatched_{category}_id:{lifecycle_id}")
                continue
            created_chapter = int(category_sources.get(lifecycle_id) or 0)
            resolved_chapter = int(resolution.get("chapter") or 0)
            if created_chapter > 0 and resolved_chapter < created_chapter:
                errors.append(
                    f"lifecycle_resolution_before_creation:{lifecycle_id}:"
                    f"{resolved_chapter}<{created_chapter}"
                )
        return errors

    def validate_commit_projection(self, commit_payload: Dict[str, Any]) -> list[str]:
        """Validate canonical facts before any derived writer mutates state."""
        chapter = int((commit_payload.get("meta") or {}).get("chapter") or 0)
        accepted_events = list(extraction_list(commit_payload, "accepted_events"))
        creations, resolutions, errors = self._lifecycle_events(accepted_events, chapter)
        errors.extend(self._validate_lifecycle_targets(creations, resolutions))
        for index, event in enumerate(accepted_events):
            if not isinstance(event, dict) or event.get("event_type") != "world_rule_revealed":
                continue
            if not world_rule_evidence_in_commit(
                self.config.project_root,
                commit_payload,
                event,
            ):
                event_id = str(event.get("event_id") or f"event-{chapter}-{index}")
                errors.append(f"world_rule_evidence_untrusted:{event_id}")
        timeline_rows = [
            row
            for row in extraction_list(commit_payload, "timeline_events")
            if isinstance(row, dict)
        ]
        for timeline_id in self.store.timeline_identity_conflicts(timeline_rows):
            errors.append(f"timeline_id_conflict:{timeline_id}")
        return errors

    def apply_commit_projection(self, commit_payload: Dict[str, Any]) -> Dict[str, Any]:
        chapter = int((commit_payload.get("meta") or {}).get("chapter") or 0)
        entity_deltas = list(extraction_list(commit_payload, "entity_deltas"))
        accepted_events = list(extraction_list(commit_payload, "accepted_events"))
        projection_errors = self.validate_commit_projection(commit_payload)
        if projection_errors:
            return {
                "chapter": chapter,
                "items_added": 0,
                "items_updated": 0,
                "items_outdated": 0,
                "items_resolved": 0,
                "warnings": [],
                "error": projection_errors[0],
            }
        creations, resolutions, _ = self._lifecycle_events(accepted_events, chapter)

        consistency_facts: Dict[str, Any] = {
            "timeline_events": list(extraction_list(commit_payload, "timeline_events")),
            "world_rules": [],
        }
        for event in accepted_events:
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("event_type") or "").strip()
            payload = dict(event.get("payload") or {})
            # A broken-rule event reports a contradiction or a proposed
            # amendment.  It must not turn its proposed value into an active
            # world rule until an explicit ``world_rule_revealed`` commit
            # establishes that rule as canonical.
            if event_type == "world_rule_revealed":
                normalized_rule = normalize_world_rule_payload(
                    payload,
                    event.get("subject"),
                )
                if normalized_rule is not None:
                    consistency_facts["world_rules"].append(
                        {
                            "rule": normalized_rule["rule_content"],
                            "rule_category": normalized_rule["rule_category"],
                            "scope": normalized_rule["scope"],
                            "domain": normalized_rule["domain"],
                            "field": normalized_rule["field"],
                            "evidence_quote": normalized_rule["evidence_quote"],
                        }
                    )

        relationship_rows = [
            {
                "from": row.get("from_entity") or row.get("from"),
                "to": row.get("to_entity") or row.get("to"),
                "type": row.get("relation_type") or row.get("relationship_type") or row.get("type"),
                "description": row.get("description") or "",
            }
            for row in entity_deltas
            if isinstance(row, dict)
            and str(row.get("from_entity") or row.get("from") or "").strip()
            and str(row.get("to_entity") or row.get("to") or "").strip()
        ]
        relationship_rows.extend(
            derived
            for event in accepted_events
            if isinstance(event, dict)
            and str(event.get("event_type") or "").strip() == "relationship_changed"
            for derived in [self._relationship_from_event(event)]
            if derived is not None
        )

        result = {
            "entities_new": [
                {
                    "suggested_id": row.get("entity_id") or row.get("id"),
                    "name": row.get("canonical_name")
                    or (row.get("payload") or {}).get("name")
                    or row.get("name")
                    or row.get("entity_id")
                    or row.get("id"),
                    "type": row.get("type") or row.get("entity_type") or "角色",
                    "tier": row.get("tier") or "装饰",
                }
                for row in entity_deltas
                if isinstance(row, dict)
                and str(row.get("entity_id") or row.get("id") or "").strip()
                and not (row.get("from_entity") or row.get("from"))
            ],
            "state_changes": list(extraction_list(commit_payload, "state_deltas")),
            "relationships_new": relationship_rows,
            **consistency_facts,
        }
        stats = self.update_from_chapter_result(chapter, result)
        stats["items_resolved"] = 0
        stats.setdefault("items_preserved", 0)

        # Apply all creations before closures so a deliberately same-chapter
        # create/resolve pair is deterministic.  Existing items are preserved,
        # 因而延迟到达的创建重放也不能重新打开已关闭义务。
        for item in creations:
            outcome = self.store.upsert_lifecycle_item(item)
            stats["items_added"] += int(outcome.get("added", 0))
            stats["items_updated"] += int(outcome.get("updated", 0))
            stats["items_outdated"] += int(outcome.get("outdated", 0))
            stats["items_preserved"] += int(outcome.get("preserved", 0))

        for resolution in resolutions:
            outcome = self.store.resolve_lifecycle_item(
                resolution["category"],
                resolution["lifecycle_id"],
                chapter=int(resolution["chapter"]),
                resolution=resolution["resolution"],
                resolved_by=resolution["event_id"],
            )
            # Target validation above prevents this from being a silent no-op.
            stats["items_resolved"] += int(outcome.get("resolved", 0))
            stats["items_preserved"] += int(outcome.get("already_resolved", 0))
        return stats

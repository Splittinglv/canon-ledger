#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Dict

from chapter_outline_loader import volume_num_for_chapter_from_state

from .chapter_commit_schema import (
    ChapterCommitSchema,
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
    normalize_timeline_events,
)
from .canon_evidence import (
    CHAPTER_COMMIT_SCHEMA_V2,
    EVIDENCE_CONTRACT_VERSION,
    LINKED_CANON_FIELDS,
    EvidenceContractClassification,
    classify_evidence_contract,
    merge_withheld_records,
    partition_linked_records,
    strict_commit_linked_records,
    validate_event_evidence,
    validate_mutation_source_bindings,
)
from .chapter_content_binding import (
    ChapterBindingError,
    build_chapter_binding,
    chapter_bindings_equal,
    verify_chapter_binding,
    verify_commit_content_binding,
)
from .commit_artifacts import extraction_list
from .config import DataModulesConfig
from .event_log_store import EventLogStore
from .event_projection_router import EventProjectionRouter
from .fact_text import bound_chapter_text_for_commit, event_evidence_in_chapter
from .story_contracts import write_json
from .index_manager import IndexManager
from .override_ledger_service import (
    AmendProposalTrigger,
    ensure_override_ledger_columns,
    persist_amend_proposals,
)
from .outline_fulfillment import (
    fulfillment_node_errors,
    load_authoritative_chapter_goal,
    load_authoritative_planned_nodes,
)


def _delta_declared_old(delta: dict[str, Any]) -> tuple[bool, Any]:
    for key in ("old", "old_value", "from"):
        if key in delta and delta.get(key) not in (None, ""):
            return True, delta.get(key)
    return False, None


def _information_conflict_items(
    chapter: int,
    probes: list[dict[str, Any] | None],
    history: Any,
) -> list[dict[str, Any]]:
    """Deterministically catch an ``information_id`` reused for different facts.

    Two claims under one ID in the same chapter are a data bug and fail the
    commit.  A claim that differs from the recorded canon is routed to the
    human queue: the author decides whether it is a rewording (confirm keeps
    the recorded claim), a correction (replace), or noise (ignore).
    """
    seen: dict[str, tuple[str, str]] = {}
    items: list[dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        if str(probe.get("event_type") or "") != "knowledge_state_changed":
            continue
        payload = probe.get("payload") if isinstance(probe.get("payload"), dict) else {}
        information_id = str(payload.get("information_id") or "").strip()
        claim = str(
            payload.get("canonical_claim") or payload.get("content") or ""
        ).strip()
        event_id = str(probe.get("event_id") or "").strip()
        if not information_id or not claim:
            continue
        prior = seen.get(information_id)
        if prior is not None and prior[0] != claim:
            raise ValueError(
                f"information_id_conflict_in_chapter:{information_id}:"
                f"{prior[1]}:{event_id}"
            )
        seen[information_id] = (claim, event_id)
        existing = history.information.get(information_id) or {}
        existing_claim = str(
            existing.get("canonical_claim") or existing.get("content") or ""
        ).strip()
        if not existing_claim or existing_claim == claim:
            continue
        source_chapter = existing.get("source_chapter")
        items.append(
            {
                "source": "information_id_conflict",
                "category": "knowledge_identity",
                "dimension": "knowledge",
                "candidate_event_id": event_id,
                "candidate_event": probe,
                "evidence_quote": str(payload.get("evidence_quote") or ""),
                "existing_fact": (
                    f"信息 {information_id} 既往表述（第 {source_chapter} 章起）："
                    f"{existing_claim}"
                ),
                "reason": (
                    f"信息 {information_id} 的本章表述与既往记录不同：可能是同一信息换了说法，"
                    "也可能是模型把另一条信息误用了同一个 ID。confirm=是同一信息，"
                    "并以本章新表述为准更正正史；replace=作者亲自改写本条表述；"
                    "ignore=本章不记录这条候选（另一条信息请让模型改用新 ID 重新提交）。"
                ),
            }
        )
    return items


_IDENTITY_KEYS = (
    "entity_id",
    "id",
    "subject",
    "from_entity",
    "to_entity",
    "from_holder",
    "to_holder",
    "source_entity",
    "holder_id",
)


def _normalize_alias_key(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _display_names_from_entity_row(row: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("name", "canonical_name"):
        text = _normalize_alias_key(row.get(key))
        if text:
            names.append(text)
    for bucket_key in ("aliases", "mentions"):
        bucket = row.get(bucket_key)
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            text = _normalize_alias_key(item)
            if text:
                names.append(text)
    return list(dict.fromkeys(names))


def _history_alias_owners(history: Any) -> dict[str, list[str]]:
    """Build the same name→ids map as as-of ``alias_index``, plus NFKC."""
    owners: dict[str, list[str]] = {}
    entities = history.entities if isinstance(getattr(history, "entities", None), dict) else {}
    for entity_id, entity in entities.items():
        if not isinstance(entity, dict):
            continue
        eid = str(entity.get("id") or entity_id or "").strip()
        if not eid:
            continue
        for name in _display_names_from_entity_row(entity):
            bucket = owners.setdefault(name, [])
            if eid not in bucket:
                bucket.append(eid)
    return owners


def _collect_new_entity_rows(extraction: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for appeared in list(getattr(extraction, "entities_appeared", None) or []):
        if not isinstance(appeared, dict):
            continue
        entity_id = str(appeared.get("id") or appeared.get("entity_id") or "").strip()
        if entity_id:
            rows.append({"entity_id": entity_id, **appeared})
    for delta in list(getattr(extraction, "entity_deltas", None) or []):
        if not isinstance(delta, dict):
            continue
        if (delta.get("from_entity") or delta.get("from")) and (
            delta.get("to_entity") or delta.get("to")
        ):
            continue
        entity_id = str(delta.get("entity_id") or delta.get("id") or "").strip()
        if entity_id:
            rows.append({"entity_id": entity_id, **delta})
    return rows


def _entity_name_collision_items(
    extraction: Any,
    history: Any,
) -> list[dict[str, Any]]:
    """Route a new entity_id that reuses a recorded display name to the queue."""
    owners = _history_alias_owners(history)
    known_ids = {
        str(entity_id or "").strip()
        for entity_id in (getattr(history, "entities", None) or {})
    }
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _collect_new_entity_rows(extraction):
        new_id = str(row.get("entity_id") or "").strip()
        if not new_id or new_id in known_ids or new_id in seen:
            continue
        matched: list[str] = []
        matched_names: list[str] = []
        for name in _display_names_from_entity_row(row):
            for owner in owners.get(name) or []:
                if owner != new_id and owner not in matched:
                    matched.append(owner)
                    matched_names.append(name)
        if not matched:
            continue
        seen.add(new_id)
        old_id = matched[0]
        shown = "、".join(matched_names[:3])
        items.append(
            {
                "source": "entity_name_collision",
                "category": "entity_identity",
                "dimension": "knowledge",
                "candidate_event_id": f"entity-identity-{new_id}",
                "new_entity_id": new_id,
                "matched_entity_id": old_id,
                "existing_fact": f"已有实体 {old_id} 使用名称 {shown}",
                "reason": (
                    f"新 ID {new_id} 的显示名/别名与已有实体 {old_id} 相同（{shown}）。"
                    f"confirm=合并到已有实体 {old_id}；"
                    "ignore=本章不登记这个新 ID；"
                    "replace=声明就是新人并保留新 ID。"
                ),
            }
        )
    return items


def _obj_mentions_entity(value: Any, entity_id: str) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _IDENTITY_KEYS and str(nested or "").strip() == entity_id:
                return True
            if _obj_mentions_entity(nested, entity_id):
                return True
        return False
    if isinstance(value, list):
        return any(
            str(item or "").strip() == entity_id or _obj_mentions_entity(item, entity_id)
            for item in value
        )
    return False


def _rewrite_entity_id(value: Any, old_id: str, new_id: str) -> Any:
    if isinstance(value, dict):
        rewritten = {}
        for key, nested in value.items():
            if key in _IDENTITY_KEYS and str(nested or "").strip() == old_id:
                rewritten[key] = new_id
            else:
                rewritten[key] = _rewrite_entity_id(nested, old_id, new_id)
        return rewritten
    if isinstance(value, list):
        return [
            new_id
            if str(item or "").strip() == old_id
            else _rewrite_entity_id(item, old_id, new_id)
            for item in value
        ]
    return value


def _drop_entity_records(rows: list[Any], entity_id: str) -> list[Any]:
    kept: list[Any] = []
    for row in rows:
        if _obj_mentions_entity(row, entity_id):
            continue
        kept.append(row)
    return kept


def _apply_identity_actions(
    extraction_payload: dict[str, Any],
    accepted_events: list[dict[str, Any]],
    collisions: list[dict[str, Any]],
    identity_actions: dict[str, str],
) -> list[dict[str, Any]]:
    events = list(accepted_events)
    for item in collisions:
        new_id = str(item.get("new_entity_id") or "").strip()
        old_id = str(item.get("matched_entity_id") or "").strip()
        if not new_id:
            continue
        action = identity_actions.get(new_id)
        if action == "replace":
            continue
        if action == "confirm" and old_id:
            events = _rewrite_entity_id(events, new_id, old_id)
            for field in (
                "entity_deltas",
                "entities_appeared",
                "scenes",
                "state_deltas",
            ):
                extraction_payload[field] = _rewrite_entity_id(
                    list(extraction_payload.get(field) or []),
                    new_id,
                    old_id,
                )
            continue
        events = _drop_entity_records(events, new_id)
        for field in ("entity_deltas", "entities_appeared", "scenes", "state_deltas"):
            extraction_payload[field] = _drop_entity_records(
                list(extraction_payload.get(field) or []),
                new_id,
            )
    return events


def _state_deltas_from_events(events: list[Any]) -> list[dict[str, Any]]:
    deltas: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in {"character_state_changed", "power_breakthrough"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entity = str(payload.get("entity_id") or event.get("subject") or "").strip()
        field_name = str(
            payload.get("field") or payload.get("field_path") or ""
        ).strip()
        if event_type == "power_breakthrough" and not field_name:
            field_name = "realm"
        delta: dict[str, Any] = {
            "entity_id": entity,
            "field": field_name,
            "source_event_id": str(event.get("event_id") or "").strip(),
        }
        for key in ("new", "new_value", "to", "new_state"):
            if key in payload:
                delta[key] = payload[key]
                break
        for key in ("old", "old_value", "from", "previous_state"):
            if key in payload:
                delta[key] = payload[key]
                break
        deltas.append(delta)
    return deltas


def _is_inferred_knowledge(event: dict[str, Any], probe: dict[str, Any] | None) -> bool:
    source = probe if isinstance(probe, dict) else event
    if str(source.get("event_type") or event.get("event_type") or "") != (
        "knowledge_state_changed"
    ):
        return False
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    if not payload and isinstance(event.get("payload"), dict):
        payload = event["payload"]
    return str(payload.get("source_kind") or "").strip().lower() == "inferred"


_ISSUE_FACT_DIMENSIONS = {
    "knowledge": ["knowledge"],
    "presence": ["presence"],
    "custody": ["custody"],
}
_CHECKPOINT_TRIGGER_KINDS = {
    "author_marked",
    "retcon",
    "core_character_permanent_state",
    "core_secret_reveal",
    "key_item_change",
    "world_rule_change",
    "power_permanent_change",
    "major_relationship_change",
    "major_time_change",
    "core_obligation_change",
    "volume_end",
}


def _history_fact_index(history: Any) -> dict[str, list[dict[str, Any]]]:
    """Index traceable N-1 fact rows by every stable identifier they expose."""
    result: dict[str, list[dict[str, Any]]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            identifiers: set[str] = set()
            for key in (
                "id",
                "event_id",
                "source_event_id",
                "information_id",
                "timeline_id",
                "rule_id",
                "loop_id",
                "promise_id",
            ):
                text = str(value.get(key) or "").strip()
                if text:
                    identifiers.add(text)
            for identifier in identifiers:
                rows = result.setdefault(identifier, [])
                if value not in rows:
                    rows.append(value)
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for attribute in (
        "canonical_facts",
        "state_changes",
        "rules",
        "obligations",
        "timeline",
        "information",
        "knowledge_by_entity",
        "presence",
        "presence_history",
        "custody",
        "custody_history",
        "relationships",
    ):
        visit(getattr(history, attribute, None))
    return result


def _row_conflict_kinds(row: dict[str, Any]) -> set[str]:
    category = str(row.get("category") or "").strip()
    kinds: set[str] = set()
    if category == "character_state" or (
        "entity_id" in row and "field" in row and "new" in row
    ):
        kinds.add("state")
    if category == "timeline" or str(row.get("timeline_id") or "").strip():
        kinds.add("timeline")
    if str(row.get("information_id") or "").strip():
        kinds.add("knowledge")
    if (
        str(row.get("entity_id") or "").strip()
        and str(row.get("location_id") or "").strip()
        and str(row.get("presence_kind") or "").strip()
    ):
        kinds.add("presence")
    if str(row.get("artifact_id") or "").strip() and (
        "holder_id" in row or "from_holder" in row or "to_holder" in row
    ):
        kinds.add("custody")
    if category == "world_rule":
        kinds.add("world_rule")
    if category in {
        "relationship",
        "story_fact",
        "open_loop",
        "reader_promise",
    }:
        kinds.add("mechanical")
    return kinds


def _canonical_anchor_is_traceable(
    rows: list[dict[str, Any]],
    canonical_evidence: str,
    conflict_kind: str,
) -> bool:
    """Require the claimed canon quote to resolve to the claimed fact kind.

    Reviewer prose may prefix a quote with a chapter label, so containment is
    accepted in that direction. A short model fragment is not enough to turn a
    candidate into an automatic rejection.
    """
    claimed = canonical_evidence.strip()
    if not claimed:
        return False
    for row in rows:
        if conflict_kind not in _row_conflict_kinds(row):
            continue
        source_quote = str(row.get("evidence_quote") or "").strip()
        if source_quote and source_quote in claimed:
            return True
    return False


def _runtime_review_verdicts(
    review: Any,
    chapter_text: str,
    history: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Recompute issue authority without trusting reviewer ``blocking``.

    A directly actionable issue needs both a verbatim current-chapter anchor
    and a stable N-1 canon anchor. Missing anchors become human review (or a
    low-value audit entry) instead of causing automatic prose edits.
    """
    fact_index = _history_fact_index(history)
    confirmed: list[dict[str, Any]] = []
    human_required: list[dict[str, Any]] = []
    audit_only: list[dict[str, Any]] = []
    for index, raw in enumerate(list(getattr(review, "issues", None) or [])):
        issue = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        quote = str(issue.get("evidence_quote") or "").strip()
        fact_id = str(issue.get("canonical_fact_id") or "").strip()
        conflict_kind = str(issue.get("conflict_kind") or "").strip()
        canonical_evidence = str(issue.get("canonical_evidence") or "").strip()
        canon_rows = fact_index.get(fact_id, [])
        anchored = bool(
            quote
            and quote in chapter_text
            and fact_id
            and canon_rows
            and conflict_kind
            and canonical_evidence
            and _canonical_anchor_is_traceable(
                canon_rows,
                canonical_evidence,
                conflict_kind,
            )
        )
        verdict = {
            **issue,
            "runtime_index": index,
            "runtime_confirmed": anchored,
            "model_blocking_hint": bool(issue.get("blocking", False)),
        }
        if anchored:
            confirmed.append(verdict)
            continue

        severity = str(issue.get("severity") or "low")
        materiality = "normal" if severity == "medium" else severity
        item = {
            "source": "runtime_unanchored_issue",
            "category": str(issue.get("category") or "logic"),
            "decision_id": f"runtime-issue-{index + 1}",
            "description": str(issue.get("description") or "事实疑点需要复核"),
            "evidence_quote": quote,
            "existing_fact": canonical_evidence,
            "reason": (
                "reviewer 报告了事实冲突，但 runtime 无法同时验证本章逐字证据"
                "与 N-1 正史事实 ID；禁止自动改文，交由作者判断。"
            ),
            "options": ["confirm", "ignore", "rewrite"],
            "fact_dimensions": list(_ISSUE_FACT_DIMENSIONS.get(conflict_kind, [])),
            "review_kind": "ambiguity",
            "trigger_kind": "ambiguous_fact",
            "materiality": materiality if materiality in {"critical", "high", "normal", "low"} else "normal",
            "disposition": "advisory" if severity == "low" and quote else (
                "audit_only" if severity == "low" else "human_required"
            ),
            "required": False if severity == "low" else True,
            "source_event_id": "",
            "blocking": False,
            "runtime_issue": verdict,
        }
        if severity == "low" and not quote:
            audit_only.append(item)
        else:
            human_required.append(item)
    return {
        "confirmed": confirmed,
        "human_required": human_required,
        "audit_only": audit_only,
    }


def _event_checkpoint_trigger(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_type = str(event.get("event_type") or "")
    requested = str(payload.get("trigger_kind") or "").strip()
    if bool(payload.get("checkpoint_required")):
        return requested if requested in _CHECKPOINT_TRIGGER_KINDS else "author_marked"
    if event_type == "world_rule_broken":
        return "retcon"
    if event_type == "world_rule_revealed":
        return "world_rule_change"
    if event_type == "power_breakthrough":
        return "power_permanent_change"

    materiality = str(payload.get("materiality") or "normal").strip().lower()
    important = materiality in {"critical", "high"}
    if event_type == "character_state_changed" and (
        important or bool(payload.get("permanent"))
    ):
        return "core_character_permanent_state"
    if event_type == "knowledge_state_changed" and (
        important or bool(payload.get("first_reveal")) or bool(payload.get("core_secret"))
    ):
        return "core_secret_reveal"
    if event_type in {"custody_changed", "artifact_obtained"} and (
        important or bool(payload.get("key_item"))
    ):
        return "key_item_change"
    if event_type == "relationship_changed" and important:
        return "major_relationship_change"
    if event_type in {
        "open_loop_closed",
        "promise_created",
        "promise_paid_off",
    } and (important or bool(payload.get("core_obligation"))):
        return "core_obligation_change"
    return ""


def _checkpoint_fact_dimensions(event_type: str) -> list[str]:
    if event_type == "knowledge_state_changed":
        return ["knowledge"]
    if event_type == "presence_observed":
        return ["presence"]
    if event_type in {"custody_changed", "artifact_obtained"}:
        return ["custody"]
    return []


def _author_checkpoint_marker(project_root: Path, chapter: int) -> dict[str, Any] | None:
    path = project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    directive = payload.get("chapter_directive") if isinstance(payload, dict) else None
    marker = directive.get("human_review") if isinstance(directive, dict) else None
    if marker is True:
        return {"required": True, "reason": "作者将本章标记为关键审核节点。"}
    if not isinstance(marker, dict) or marker.get("required") is not True:
        return None
    return marker


def _checkpoint_review_items(
    project_root: Path,
    chapter: int,
    events: list[dict[str, Any]],
    timeline_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    by_id = {
        str(event.get("event_id") or ""): event
        for event in events
        if str(event.get("event_id") or "")
    }
    triggers: dict[str, str] = {}
    for event_id, event in by_id.items():
        trigger = _event_checkpoint_trigger(event)
        if trigger:
            triggers[event_id] = trigger
    for row in timeline_events:
        if not isinstance(row, dict):
            continue
        materiality = str(row.get("materiality") or "normal").strip().lower()
        if materiality in {"critical", "high"} or bool(row.get("major_time_change")):
            source_id = str(row.get("source_event_id") or "").strip()
            if source_id in by_id:
                triggers[source_id] = "major_time_change"

    for event_id, trigger in triggers.items():
        event = by_id[event_id]
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        items.append(
            {
                "source": "runtime_checkpoint",
                "category": (
                    "setting"
                    if trigger in {"retcon", "world_rule_change"}
                    else "timeline"
                    if trigger == "major_time_change"
                    else "character"
                    if trigger in {"core_character_permanent_state", "core_secret_reveal"}
                    else "continuity"
                ),
                "decision_id": f"checkpoint-{event_id}",
                "candidate_event_id": event_id,
                "candidate_event": event,
                "evidence_quote": str(payload.get("evidence_quote") or ""),
                "reason": (
                    f"{event_type} 会改变后续长期正史，属于 {trigger} 关键节点；"
                    "正文证据成立，但写入前默认由作者确认。"
                ),
                "options": ["confirm", "ignore", "rewrite"],
                "fact_dimensions": _checkpoint_fact_dimensions(event_type),
                "review_kind": "checkpoint",
                "trigger_kind": trigger,
                "materiality": "high",
                "disposition": "human_required",
                "required": True,
                "source_event_id": event_id,
                "blocking": False,
            }
        )

    marker = _author_checkpoint_marker(project_root, chapter)
    if marker is not None:
        trigger = str(marker.get("trigger_kind") or "author_marked").strip()
        if trigger not in _CHECKPOINT_TRIGGER_KINDS:
            trigger = "author_marked"
        items.append(
            {
                "source": "author_checkpoint",
                "category": "continuity",
                "decision_id": f"author-checkpoint-{chapter}",
                "reason": str(marker.get("reason") or "作者要求本章提交后人工复核。"),
                "options": ["confirm", "ignore", "rewrite"],
                "fact_dimensions": [],
                "review_kind": "checkpoint",
                "trigger_kind": trigger,
                "materiality": str(marker.get("materiality") or "high"),
                "disposition": "human_required",
                "required": True,
                "source_event_id": "",
                "blocking": False,
            }
        )
    return items


def _bind_reviewer_checkpoints(
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bind reviewer checkpoints to extracted events without fact leakage."""
    def quote_matches(item_quote: str, event: dict[str, Any]) -> bool:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_quote = str(payload.get("evidence_quote") or "").strip()
        return bool(
            item_quote
            and event_quote
            and (
                item_quote == event_quote
                or item_quote in event_quote
                or event_quote in item_quote
            )
        )

    bound: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        if str(item.get("review_kind") or "") != "checkpoint":
            bound.append(item)
            continue
        check_quote = str(item.get("evidence_quote") or "").strip()
        matches = [
            event for event in events if quote_matches(check_quote, event)
        ]
        if len(matches) != 1:
            # A chapter-level checkpoint can exist without a new canon event.
            # If one quote maps to multiple facts, guessing would promote all
            # of them with one author verdict. Keep the checkpoint required but
            # unbound; source_event_id remains an optional N-1 canon anchor.
            bound.append(item)
            continue
        event = matches[0]
        event_id = str(event.get("event_id") or "").strip()
        item["candidate_event_id"] = event_id
        item["candidate_event"] = event
        bound.append(item)
    return bound


class ChapterCommitService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def _assert_v2_write_allowed(self) -> None:
        # This service is retained only to inspect historical envelopes during
        # cutover.  Production mutations are retired globally; CURRENT (and
        # even project scaffolding) must never act as an enable/disable switch.
        from .workflow_authority import WorkflowAuthority

        WorkflowAuthority(
            self.project_root
        ).assert_legacy_fact_mutation_disabled("chapter_commit")

    def validate_legacy_commit_for_migration(
        self,
        payload: Dict[str, Any],
    ) -> EvidenceContractClassification:
        """Read-only validation exception for cutover inventory code.

        It deliberately has no persist/projection side effect and is the only
        service-level legacy escape hatch.  Migration code may inspect old
        bytes; it cannot reuse this service to write them back.
        """

        classification = self._require_supported_evidence_envelope(
            payload,
            allow_legacy_replay=True,
        )
        self._validate_strict_evidence_commit(
            payload,
            allow_legacy_replay=True,
            classification=classification,
        )
        return classification

    def _validate_custody_transitions(
        self,
        chapter: int,
        accepted_events: list[dict[str, Any]],
        history: Any,
    ) -> None:
        """Reject a mechanically impossible holder chain before projection.

        The first recorded transition for an artifact may seed an existing
        off-page holder. Once custody has been recorded, every later transfer
        must start from that exact holder, including an explicitly unheld item.
        """
        holders: dict[str, str] = {
            artifact_id: str((row or {}).get("holder_id") or "")
            for artifact_id, row in history.custody.items()
        }
        recorded = set(history.custody)
        ordered = sorted(
            accepted_events,
            key=lambda event: int(event.get("sequence") or 0),
        )
        for event in ordered:
            if str(event.get("event_type") or "") != "custody_changed":
                continue
            artifact_id = str(event.get("subject") or "").strip()
            raw_payload = event.get("payload")
            payload = raw_payload if isinstance(raw_payload, dict) else {}
            from_holder = str(payload.get("from_holder") or "").strip()
            to_holder = str(payload.get("to_holder") or "").strip()
            prior_holder = holders.get(artifact_id, "")
            if artifact_id in recorded and prior_holder != from_holder:
                raise ValueError(
                    "custody_transition_conflict:"
                    f"{artifact_id}:expected_from={prior_holder or '<none>'}:"
                    f"actual_from={from_holder or '<none>'}"
                )
            recorded.add(artifact_id)
            holders[artifact_id] = to_holder

    @staticmethod
    def _normalized_probe(
        chapter: int,
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Strictly normalized, verification-free copy of one candidate event."""
        from .chapter_commit_schema import normalize_accepted_events

        stripped = {
            key: value for key, value in dict(event).items() if key != "verification"
        }
        try:
            normalized = normalize_accepted_events(int(chapter), [stripped])[0]
        except (TypeError, ValueError):
            return None
        normalized.pop("verification", None)
        return normalized

    def _validate_state_delta_chain(
        self,
        state_deltas: list[Any],
        history: Any,
    ) -> None:
        """A delta touching a recorded field must declare the matching old value.

        ``state_deltas`` and ``character_state_changed`` / ``power_breakthrough``
        events both write canon state.  This chain check is the deterministic
        guard against silently overwriting a recorded value.
        """
        recorded: dict[tuple[str, str], Any] = {}
        for change in history.state_changes:
            if not isinstance(change, dict):
                continue
            key = (
                str(change.get("entity_id") or "").strip(),
                str(change.get("field") or "").strip(),
            )
            if key[0] and key[1]:
                recorded[key] = change.get("new")
        for delta in state_deltas:
            if not isinstance(delta, dict):
                continue
            entity = str(delta.get("entity_id") or delta.get("subject") or "").strip()
            field_name = str(
                delta.get("field") or delta.get("field_path") or ""
            ).strip()
            if not entity or not field_name:
                continue
            key = (entity, field_name)
            if key not in recorded:
                continue
            prior = recorded[key]
            declared_present, declared = _delta_declared_old(delta)
            if not declared_present:
                raise ValueError(
                    f"state_delta_missing_old:{entity}:{field_name}:"
                    f"recorded={prior}"
                )
            if str(declared).strip() != str(prior).strip():
                raise ValueError(
                    f"state_delta_conflict:{entity}:{field_name}:"
                    f"recorded_old={prior}:declared_old={declared}"
                )

    def _validate_entity_type_stability(
        self,
        entity_deltas: list[Any],
        history: Any,
    ) -> None:
        """Reject a delta that silently retypes a recorded entity.

        Renames and tier promotions are legitimate narrative evolution, but an
        entity flipping type (角色→物品 …) means the extractor reused an ID for
        something else.  Entities that only carry the seeded default type are
        skipped to avoid false positives.
        """
        default_type = "角色"
        for delta in entity_deltas:
            if not isinstance(delta, dict):
                continue
            if (delta.get("from_entity") or delta.get("from")) and (
                delta.get("to_entity") or delta.get("to")
            ):
                continue
            entity_id = str(delta.get("entity_id") or delta.get("id") or "").strip()
            if not entity_id:
                continue
            declared = str(
                delta.get("entity_type") or delta.get("type") or ""
            ).strip()
            if not declared:
                continue
            row = history.entities.get(entity_id) or {}
            recorded_type = str(row.get("type") or "").strip()
            if (
                recorded_type
                and recorded_type != default_type
                and declared != recorded_type
            ):
                raise ValueError(
                    f"entity_type_conflict:{entity_id}:"
                    f"recorded={recorded_type}:declared={declared}"
                )

    def build_commit(
        self,
        chapter: int,
        review_result: Dict[str, Any],
        fulfillment_result: Dict[str, Any],
        disambiguation_result: Dict[str, Any],
        extraction_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        self._assert_v2_write_allowed()
        review = ReviewResult.model_validate(review_result)
        fulfillment = FulfillmentResult.model_validate(fulfillment_result)
        disambiguation = DisambiguationResult.model_validate(disambiguation_result)
        extraction = ExtractionResult.model_validate(extraction_result)

        artifact_models = {
            "review_result": review,
            "fulfillment_result": fulfillment,
            "disambiguation_result": disambiguation,
            "extraction_result": extraction,
        }
        for artifact_name, artifact in artifact_models.items():
            binding_payload = artifact.chapter_binding.model_dump()
            ok, code = verify_chapter_binding(
                self.project_root,
                chapter,
                binding_payload,
            )
            if not ok:
                raise ChapterBindingError(
                    code,
                    f"{artifact_name}.chapter_binding verification failed: {code}",
                )

        # Re-read once after all artifact checks.  This final fingerprint is
        # the commit's source of truth and closes the normal review→commit
        # mutation window.
        chapter_binding = build_chapter_binding(self.project_root, chapter)
        for artifact_name, artifact in artifact_models.items():
            if not chapter_bindings_equal(
                chapter_binding,
                artifact.chapter_binding,
            ):
                raise ChapterBindingError(
                    "chapter_content_hash_mismatch",
                    f"{artifact_name}.chapter_binding is stale",
                )
        authoritative_goal = load_authoritative_chapter_goal(
            self.project_root,
            chapter,
        )
        authoritative_nodes = load_authoritative_planned_nodes(
            self.project_root,
            chapter,
        )
        fulfillment_errors = fulfillment_node_errors(
            fulfillment,
            authoritative_nodes,
        )
        if fulfillment_errors:
            raise ValueError(fulfillment_errors[0])

        from .canonical_history import load_canonical_history
        from .human_review import (
            HumanReviewService,
            review_manual_check_items_from_review,
        )

        history = load_canonical_history(self.project_root, max(0, chapter - 1))
        raw_candidates = [dict(event) for event in extraction.accepted_events]
        probes: list[dict[str, Any] | None] = []
        for index, event in enumerate(raw_candidates):
            probe = self._normalized_probe(chapter, event)
            if probe is None:
                # An event that cannot normalize either gets dropped by a
                # recorded decision or fails the commit later with its own
                # schema error; it cannot be conflict-checked here.
                probes.append(None)
                continue
            if not str(event.get("event_id") or "").strip():
                # Give the raw event its deterministic ID now so an unresolved
                # conflict can drop exactly this event from the commit.
                raw_candidates[index]["event_id"] = str(probe.get("event_id") or "")
            probes.append(probe)

        # The chapter hash has already been re-read above. Validate every
        # candidate and every canon-writing derivative before any model hint
        # can route it around the normal accepted-event path.
        evidence_envelope = {
            "meta": {"chapter": chapter},
            "chapter_binding": chapter_binding,
        }
        bound_chapter_text = bound_chapter_text_for_commit(
            self.project_root,
            evidence_envelope,
        )
        if bound_chapter_text is None:
            raise ChapterBindingError(
                "chapter_content_hash_mismatch",
                "bound chapter text could not be re-read for canon evidence",
            )
        if any(probe is None for probe in probes):
            invalid_index = next(
                index for index, probe in enumerate(probes) if probe is None
            )
            # Re-run the strict normalizer outside the probe's best-effort
            # wrapper so callers receive the precise schema/evidence error.
            from .chapter_commit_schema import normalize_accepted_events

            normalize_accepted_events(chapter, [raw_candidates[invalid_index]])
            raise ValueError(f"accepted_events[{invalid_index}] is invalid")
        normalized_candidates = [dict(probe) for probe in probes if probe is not None]
        # A previous accepted-with-pending commit deliberately withheld its
        # candidate from accepted_events. Keep that chapter-bound candidate as
        # an evidence source so --from-last-commit can restore linked records
        # after the author confirms it.
        source_candidates = list(normalized_candidates)
        source_candidate_ids = {
            str(event.get("event_id") or "") for event in source_candidates
        }
        for raw_pending in list(disambiguation.pending or []):
            if not isinstance(raw_pending, dict) or not isinstance(
                raw_pending.get("candidate_event"), dict
            ):
                continue
            pending_probe = self._normalized_probe(
                chapter,
                dict(raw_pending["candidate_event"]),
            )
            if pending_probe is None:
                raise ValueError("pending candidate_event is not evidence-bound")
            event_id = str(pending_probe.get("event_id") or "")
            if event_id and event_id not in source_candidate_ids:
                source_candidates.append(pending_probe)
                source_candidate_ids.add(event_id)
        candidate_event_index = validate_event_evidence(
            source_candidates,
            bound_chapter_text,
        )
        extraction_payload = extraction.model_dump()
        linked_records = merge_withheld_records(extraction_payload)
        linked_records["timeline_events"] = normalize_timeline_events(
            chapter,
            linked_records["timeline_events"],
            require_source_binding=True,
        )
        linked_records = validate_mutation_source_bindings(
            linked_records,
            candidate_event_index,
        )
        runtime_review = _runtime_review_verdicts(
            review,
            bound_chapter_text,
            history,
        )
        checkpoint_items = _checkpoint_review_items(
            self.project_root,
            chapter,
            normalized_candidates,
            linked_records["timeline_events"],
        )
        inferred_kept: list[dict[str, Any]] = []
        inferred_probes: list[dict[str, Any] | None] = []
        inferred_items: list[dict[str, Any]] = []
        for event, probe in zip(raw_candidates, probes):
            if _is_inferred_knowledge(event, probe):
                event_id = str(
                    event.get("event_id")
                    or (probe or {}).get("event_id")
                    or ""
                ).strip()
                payload = (
                    (probe or {}).get("payload")
                    if isinstance((probe or {}).get("payload"), dict)
                    else event.get("payload")
                )
                payload = payload if isinstance(payload, dict) else {}
                inferred_items.append(
                    {
                        "source": "inferred_knowledge",
                        "category": "knowledge",
                        "dimension": "knowledge",
                        "candidate_event_id": event_id,
                        "candidate_event": dict(probe or event),
                        "evidence_quote": str(payload.get("evidence_quote") or ""),
                        "reason": (
                            "source_kind=inferred 不能自动进入正史，需要作者确认后"
                            "才能记为已知事实。"
                        ),
                    }
                )
                continue
            inferred_kept.append(event)
            inferred_probes.append(probe)
        raw_candidates = inferred_kept
        probes = inferred_probes
        conflict_items = _information_conflict_items(chapter, probes, history)
        collision_items = _entity_name_collision_items(extraction, history)
        review_items = _bind_reviewer_checkpoints(
            review_manual_check_items_from_review(review),
            normalized_candidates,
        )
        pending_input = [
            dict(item) if isinstance(item, dict) else item
            for item in disambiguation.pending
        ]
        queued_event_ids = {
            str(item.get("candidate_event_id") or item.get("event_id") or "").strip()
            for item in pending_input
            if isinstance(item, dict)
        }
        merged_pending = list(pending_input)
        for item in (
            conflict_items
            + inferred_items
            + collision_items
            + review_items
            + runtime_review["human_required"]
            + checkpoint_items
        ):
            candidate_id = str(item.get("candidate_event_id") or "").strip()
            if candidate_id and candidate_id in queued_event_ids:
                continue
            merged_pending.append(item)
            if candidate_id:
                queued_event_ids.add(candidate_id)

        human_review = HumanReviewService(self.project_root).apply_decisions(
            chapter,
            chapter_binding,
            merged_pending,
            raw_candidates,
        )
        rewrite_required = list(human_review.get("rewrite_required") or [])
        if rewrite_required:
            decision_ids = [
                str(item.get("decision_id") or "").strip()
                for item in rewrite_required
                if str(item.get("decision_id") or "").strip()
            ]
            raise ValueError(
                "human_review_rewrite_required:"
                + ",".join(decision_ids)
                + f";edit chapter {chapter} and run /canon-ledger-write {chapter}"
            )
        unresolved = list(human_review["unresolved"])
        outline_strict = fulfillment.enforcement == "strict"
        rejected = (
            bool(runtime_review["confirmed"])
            or (outline_strict and bool(fulfillment.missed_nodes))
        )
        status = "rejected" if rejected else "accepted"
        volume = volume_num_for_chapter_from_state(self.project_root, chapter) or 1
        accepted_events = _apply_identity_actions(
            extraction_payload,
            list(human_review["events"]),
            collision_items,
            human_review.get("identity_actions") or {},
        )
        accepted_events = EventLogStore(self.project_root).normalize_events(
            chapter, accepted_events
        )
        accepted_event_index = validate_event_evidence(
            accepted_events,
            bound_chapter_text,
        )
        discarded_event_ids = {
            str(value).strip()
            for value in human_review.get("discarded_event_ids") or []
            if str(value).strip()
        }
        if discarded_event_ids:
            linked_records = {
                field: [
                    row
                    for row in linked_records.get(field, [])
                    if str(row.get("source_event_id") or "").strip()
                    not in discarded_event_ids
                ]
                for field in LINKED_CANON_FIELDS
            }
        active_records, withheld_records = partition_linked_records(
            linked_records,
            set(accepted_event_index),
        )
        # Revalidate against effective (possibly human-replaced) events. This
        # also proves a pending event cannot leak its linked mutation into canon.
        active_records = validate_mutation_source_bindings(
            active_records,
            accepted_event_index,
        )
        for field in LINKED_CANON_FIELDS:
            extraction_payload[field] = active_records[field]
        extraction_payload["withheld_canon_records"] = withheld_records
        extraction_payload["evidence_contract"] = EVIDENCE_CONTRACT_VERSION
        if status == "accepted":
            self._validate_custody_transitions(chapter, accepted_events, history)
            self._validate_state_delta_chain(
                list(extraction_payload.get("state_deltas") or [])
                + _state_deltas_from_events(accepted_events),
                history,
            )
            self._validate_entity_type_stability(
                list(extraction_payload.get("entity_deltas") or []),
                history,
            )
        extraction_payload["accepted_events"] = accepted_events
        coverage = dict(extraction_payload.get("fact_coverage") or {})
        # fact_verification is extractor output at this boundary.  As with
        # individual events, a model cannot promote its own interpretation to
        # human-verified merely by emitting the enum value.
        verification = {
            dimension: (
                "supported" if state == "verified" else state
            )
            for dimension, state in dict(
                extraction_payload.get("fact_verification") or {}
            ).items()
        }
        if coverage and not verification:
            verification = {
                dimension: (
                    "supported" if state == "complete" else "pending"
                )
                for dimension, state in coverage.items()
            }
        for dimension in human_review["resolved_dimensions"]:
            if verification.get(dimension) == "pending":
                verification[dimension] = "supported"
        for dimension in human_review["affected_dimensions"]:
            if verification and dimension in {
                "knowledge",
                "presence",
                "custody",
            }:
                verification[dimension] = "pending"
        extraction_payload["fact_coverage"] = coverage
        extraction_payload["fact_verification"] = verification
        from .commit_lineage import (
            VALIDATION_VALID,
            predecessor_context_hash_for_chapter,
        )

        commit_payload = {
            "meta": {
                "schema_version": CHAPTER_COMMIT_SCHEMA_V2,
                "chapter": chapter,
                "status": status,
                "predecessor_context_hash": predecessor_context_hash_for_chapter(
                    self.project_root,
                    chapter,
                ),
                "validation_status": VALIDATION_VALID,
            },
            "chapter_binding": chapter_binding,
            "contract_refs": {
                "master": "MASTER_SETTING.json",
                "volume": f"volume_{volume:03d}.json",
                "chapter": f"chapter_{chapter:03d}.json",
                "review": f"chapter_{chapter:03d}.review.json",
            },
            "provenance": {
                "write_fact_role": "chapter_commit",
                "projection_role": "derived_read_models",
                "evidence_contract": EVIDENCE_CONTRACT_VERSION,
                "chapter_binding": chapter_binding,
                "runtime_review": {
                    "confirmed_issues": runtime_review["confirmed"],
                    "human_required_issues": runtime_review["human_required"],
                    "audit_only_issues": runtime_review["audit_only"],
                    "confirmed_count": len(runtime_review["confirmed"]),
                },
                "human_review": {
                    "resolved_decision_ids": human_review[
                        "resolved_decision_ids"
                    ],
                    "decision_receipts": human_review["decision_receipts"],
                    "verified_event_ids": human_review["verified_event_ids"],
                    "unresolved_count": len(unresolved),
                },
            },
            "outline_snapshot": {
                "goal": authoritative_goal,
                "planned_nodes": fulfillment.planned_nodes,
                "covered_nodes": fulfillment.covered_nodes,
                "missed_nodes": fulfillment.missed_nodes,
                "extra_nodes": fulfillment.extra_nodes,
            },
            "review_result": {
                **review.model_dump(),
                "runtime_confirmed_count": len(runtime_review["confirmed"]),
                "runtime_human_required_count": len(
                    runtime_review["human_required"]
                ),
                "runtime_audit_only_count": len(runtime_review["audit_only"]),
            },
            "fulfillment_result": fulfillment.model_dump(),
            "disambiguation_result": {
                **disambiguation.model_dump(),
                "pending": unresolved,
            },
            "extraction_result": extraction_payload,
            "projection_status": {
                "state": "pending",
                "index": "pending",
                "summary": "pending",
                "memory": "pending",
                "vector": "pending",
            },
        }
        if status == "accepted":
            from .memory.writer import MemoryWriter

            lifecycle_errors = MemoryWriter(
                DataModulesConfig.from_project_root(self.project_root)
            ).validate_commit_projection(commit_payload)
            if lifecycle_errors:
                raise ValueError(f"invalid_consistency_fact:{lifecycle_errors[0]}")
        return ChapterCommitSchema.model_validate(commit_payload).model_dump()

    def _require_supported_evidence_envelope(
        self,
        payload: Dict[str, Any],
        *,
        allow_legacy_replay: bool = False,
    ) -> EvidenceContractClassification:
        classification = classify_evidence_contract(payload)
        if classification == "invalid":
            raise ValueError("invalid_or_downgraded_evidence_contract_envelope")
        if classification == "legacy" and not allow_legacy_replay:
            raise ValueError("legacy_commit_requires_explicit_replay")
        return classification

    def _validate_strict_evidence_commit(
        self,
        payload: Dict[str, Any],
        *,
        allow_legacy_replay: bool = False,
        classification: EvidenceContractClassification | None = None,
    ) -> None:
        """Revalidate a current evidence-contract commit at a write boundary.

        ``build_commit`` returns an ordinary mutable dictionary, so callers can
        accidentally or manually change it before persistence or projection.
        Current strict commits take the fail-closed path. Historical v1
        markerless commits are accepted only by an explicit replay caller.
        """
        evidence_classification = classification or (
            self._require_supported_evidence_envelope(
                payload,
                allow_legacy_replay=allow_legacy_replay,
            )
        )
        extraction = (
            payload.get("extraction_result")
            if isinstance(payload.get("extraction_result"), dict)
            else {}
        )
        # This validates the complete envelope and every shared artifact
        # binding, then re-hashes the current manuscript bytes.
        binding_error = self._verify_commit_content_binding(payload)
        if binding_error:
            raise ChapterBindingError(
                binding_error,
                f"chapter commit validation failed: {binding_error}",
            )
        if evidence_classification == "legacy":
            return

        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        events = extraction.get("accepted_events")
        events = events if isinstance(events, list) else []
        from .chapter_commit_schema import normalize_accepted_events

        normalized_events = normalize_accepted_events(chapter, events)
        if normalized_events != events:
            raise ValueError(
                "strict_evidence_commit accepted_events must use canonical schema"
            )

        chapter_text = bound_chapter_text_for_commit(self.project_root, payload)
        if chapter_text is None:
            # Defensive: the envelope check above should already have returned
            # a stable binding error before this point.
            raise ChapterBindingError(
                "chapter_content_hash_mismatch",
                "strict evidence commit cannot read its bound chapter",
            )
        strict_commit_linked_records(payload, chapter_text)

    def persist_commit(
        self,
        payload: Dict[str, Any],
        *,
        allow_void_accepted: bool = False,
        allow_legacy_replay: bool = False,
    ) -> Path:
        self._assert_v2_write_allowed()
        self._validate_strict_evidence_commit(
            payload,
            allow_legacy_replay=allow_legacy_replay,
        )
        return self._persist_validated_commit(
            payload,
            allow_void_accepted=allow_void_accepted,
        )

    def _persist_validated_commit(
        self,
        payload: Dict[str, Any],
        *,
        allow_void_accepted: bool = False,
    ) -> Path:
        target = self.project_root / ".story-system" / "commits"
        target.mkdir(parents=True, exist_ok=True)
        chapter = int(payload["meta"]["chapter"])
        path = target / f"chapter_{chapter:03d}.commit.json"
        if path.is_file() and not allow_void_accepted:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = None
            if isinstance(existing, dict):
                existing_meta = (
                    existing.get("meta")
                    if isinstance(existing.get("meta"), dict)
                    else {}
                )
                new_meta = (
                    payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
                )
                old_status = str(existing_meta.get("status") or "")
                new_status = str(new_meta.get("status") or "")
                if old_status == "accepted" and new_status == "rejected":
                    raise ValueError(
                        "cannot_overwrite_accepted_with_rejected:"
                        f"chapter {chapter} already has an accepted commit; "
                        "fix blocking issues and resubmit as accepted, "
                        "or pass allow_void_accepted=True to void it explicitly"
                    )
        write_json(path, payload)
        return path

    def _projection_writers(self) -> dict[str, Any]:
        from .index_projection_writer import IndexProjectionWriter
        from .memory_projection_writer import MemoryProjectionWriter
        from .state_projection_writer import StateProjectionWriter
        from .summary_projection_writer import SummaryProjectionWriter
        from .vector_projection_writer import VectorProjectionWriter

        return {
            "state": StateProjectionWriter(self.project_root),
            "index": IndexProjectionWriter(self.project_root),
            "summary": SummaryProjectionWriter(self.project_root),
            "memory": MemoryProjectionWriter(self.project_root),
            "vector": VectorProjectionWriter(self.project_root),
        }

    def _writer_status(self, result: dict[str, Any]) -> str:
        if result.get("applied"):
            return "done"
        reason = str(result.get("reason") or "").strip()
        if reason in {
            "not_required",
            "commit_rejected",
            "no_chunks",
            "bm25_only",
            "embedding_partial",
        }:
            # BM25-only is a successful lexical-retrieval fallback, not a
            # semantic-vector write.  Preserve the result in the projection
            # log while keeping the vector writer visibly non-done.
            return "skipped"
        if reason.startswith("error:"):
            return f"failed:{reason[6:] or 'writer_error'}"
        return "skipped"

    def _persist_projection_run(
        self,
        payload: Dict[str, Any],
        writer_results: dict[str, dict[str, Any]],
        *,
        persist_payload: bool = True,
        allow_legacy_replay: bool = False,
    ) -> None:
        # A changed manuscript remains unpersisted, while its projection log
        # can still record why no writer completed. All other payloads pass the
        # strict persist boundary, including mutations by arbitrary writers.
        commit_path = (
            self.persist_commit(
                payload,
                allow_legacy_replay=allow_legacy_replay,
            )
            if persist_payload
            else None
        )
        try:
            from .projection_log import append_projection_run

            append_projection_run(
                self.project_root,
                payload,
                writer_results,
                commit_path=commit_path,
            )
        except Exception:
            pass

    def _block_invalid_lifecycle(
        self,
        payload: Dict[str, Any],
        *,
        allow_legacy_replay: bool = False,
    ) -> bool:
        """Fail before event-log/derived writes when a closure has no target."""
        if str((payload.get("meta") or {}).get("status") or "") != "accepted":
            return False

        from .memory.writer import MemoryWriter

        errors = MemoryWriter(
            DataModulesConfig.from_project_root(self.project_root)
        ).validate_commit_projection(payload)
        if not errors:
            return False

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}
        error = errors[0]
        payload["projection_status"]["memory"] = f"failed:{error}"
        required = set(EventProjectionRouter().required_writers(payload))
        writer_results: dict[str, dict[str, Any]] = {}
        for name in required:
            if name == "memory":
                writer_results[name] = {
                    "status": f"failed:{error}",
                    "error": error,
                    "reason": "lifecycle_validation_failed",
                }
            else:
                payload["projection_status"].setdefault(name, "pending")
                writer_results[name] = {
                    "status": str(payload["projection_status"].get(name) or "pending"),
                    "reason": "blocked_by_lifecycle_validation",
                }
        self._persist_projection_run(
            payload,
            writer_results,
            allow_legacy_replay=allow_legacy_replay,
        )
        return True

    def _verify_commit_content_binding(self, payload: Dict[str, Any]) -> str:
        """Return a stable failure code when a commit no longer binds current prose."""
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        try:
            chapter = int((meta or {}).get("chapter") or 0)
        except (TypeError, ValueError):
            return "artifact_chapter_mismatch"
        if chapter <= 0:
            return "artifact_chapter_mismatch"
        ok, code = verify_commit_content_binding(
            self.project_root,
            chapter,
            payload,
        )
        return "" if ok else code

    def _block_changed_chapter_content(self, payload: Dict[str, Any]) -> bool:
        """Fail closed before any event or derived read-model write."""
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return False

        error_code = self._verify_commit_content_binding(payload)
        if not error_code:
            return False

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}
        required = set(EventProjectionRouter().required_writers(payload)) or {"state"}
        writer_results: dict[str, dict[str, Any]] = {}
        for name in required:
            payload["projection_status"][name] = "failed:chapter_content_changed"
            writer_results[name] = {
                "status": "failed:chapter_content_changed",
                "error": error_code,
                "reason": "chapter_content_changed",
            }
        self._persist_projection_run(
            payload,
            writer_results,
            persist_payload=False,
        )
        return True

    def apply_projection_writers(
        self,
        payload: Dict[str, Any],
        *,
        only_writers: set[str] | None = None,
        persist_run: bool = True,
        writer_results_out: dict[str, dict[str, Any]] | None = None,
        allow_legacy_replay: bool = False,
    ) -> Dict[str, Any]:
        self._assert_v2_write_allowed()
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}

        evidence_classification = self._require_supported_evidence_envelope(
            payload,
            allow_legacy_replay=allow_legacy_replay,
        )
        if self._block_changed_chapter_content(payload):
            return payload
        self._validate_strict_evidence_commit(
            payload,
            allow_legacy_replay=allow_legacy_replay,
            classification=evidence_classification,
        )
        if self._block_invalid_lifecycle(
            payload,
            allow_legacy_replay=allow_legacy_replay,
        ):
            return payload

        writers = self._projection_writers()
        required_writers = set(EventProjectionRouter().required_writers(payload))
        writer_results: dict[str, dict[str, Any]] = {}
        for name, writer in writers.items():
            if only_writers is not None and name not in only_writers:
                writer_results[name] = {
                    "status": str(payload["projection_status"].get(name) or "pending"),
                    "reason": "not_selected",
                }
                continue
            if name not in required_writers:
                payload["projection_status"][name] = "skipped"
                writer_results[name] = {"status": "skipped", "reason": "not_required"}
                continue
            # A writer can execute arbitrary storage code.  Re-hash before
            # every subsequent writer so a concurrent/manual prose edit
            # cannot let the rest of the projection chain stamp stale facts
            # as done.
            binding_error = self._verify_commit_content_binding(payload)
            if binding_error:
                for pending_name in required_writers:
                    current = str(payload["projection_status"].get(pending_name) or "")
                    if current in {"", "pending"}:
                        payload["projection_status"][pending_name] = (
                            "failed:chapter_content_changed"
                        )
                        writer_results[pending_name] = {
                            "status": "failed:chapter_content_changed",
                            "error": binding_error,
                            "reason": "chapter_content_changed",
                        }
                if writer_results_out is not None:
                    writer_results_out.clear()
                    writer_results_out.update(writer_results)
                if persist_run:
                    self._persist_projection_run(
                        payload,
                        writer_results,
                        persist_payload=False,
                        allow_legacy_replay=allow_legacy_replay,
                    )
                return payload
            try:
                result = writer.apply(payload)
                payload["projection_status"][name] = self._writer_status(result)
                writer_results[name] = {
                    "status": payload["projection_status"][name],
                    "result": result,
                }
            except Exception as exc:
                payload["projection_status"][name] = f"failed:{exc}"
                writer_results[name] = {"status": "failed", "error": str(exc)}
        if writer_results_out is not None:
            writer_results_out.clear()
            writer_results_out.update(writer_results)
        if persist_run:
            self._persist_projection_run(
                payload,
                writer_results,
                allow_legacy_replay=allow_legacy_replay,
            )
        return payload

    def apply_projections(
        self,
        payload: Dict[str, Any],
        *,
        allow_legacy_replay: bool = False,
    ) -> Dict[str, Any]:
        self._assert_v2_write_allowed()
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        evidence_classification = self._require_supported_evidence_envelope(
            payload,
            allow_legacy_replay=allow_legacy_replay,
        )
        if self._block_changed_chapter_content(payload):
            return payload

        # Persist the canonical source before deciding between the append
        # fast-path and a corpus rebuild.  The rebuild runs in an isolated
        # project root and only installs a read model that was produced from
        # this exact ordered commit set.
        self._validate_strict_evidence_commit(
            payload,
            allow_legacy_replay=allow_legacy_replay,
            classification=evidence_classification,
        )
        self._persist_validated_commit(
            payload,
        )
        from .commit_lineage import is_needs_revalidation
        from .projection_rebuild import (
            projection_snapshot_requires_rebuild,
            rebuild_all_projections,
            record_projection_snapshot,
        )

        if projection_snapshot_requires_rebuild(self.project_root, payload):
            report = rebuild_all_projections(
                self.project_root,
                reason="canonical_snapshot_changed",
            )
            if report.get("ok"):
                chapter = int((payload.get("meta") or {}).get("chapter") or 0)
                for projected in report.get("payloads") or []:
                    if int((projected.get("meta") or {}).get("chapter") or 0) == chapter:
                        return projected
                return payload

            error = str(report.get("error") or "projection_rebuild_failed")
            payload.setdefault("projection_status", {})
            required = set(EventProjectionRouter().required_writers(payload)) or {"state"}
            writer_results: dict[str, dict[str, Any]] = {}
            for name in required:
                payload["projection_status"][name] = f"failed:{error}"
                writer_results[name] = {
                    "status": f"failed:{error}",
                    "reason": "projection_rebuild_failed",
                    "error": str(report.get("detail") or error),
                }
            self._persist_projection_run(
                payload,
                writer_results,
                allow_legacy_replay=allow_legacy_replay,
            )
            return payload

        if is_needs_revalidation(payload):
            return payload

        if status == "accepted":
            chapter = int((payload.get("meta") or {}).get("chapter") or 0)
            event_store = EventLogStore(self.project_root)
            accepted_events = extraction_list(payload, "accepted_events")
            extraction = payload.setdefault("extraction_result", {})
            if not isinstance(extraction, dict):
                extraction = {}
                payload["extraction_result"] = extraction
            extraction["accepted_events"] = event_store.normalize_events(
                chapter, accepted_events
            )
            # Normalization is a user-code boundary.  Re-read the manuscript
            # immediately before lifecycle handling and the first event write.
            if self._block_changed_chapter_content(payload):
                return payload
            if self._block_invalid_lifecycle(
                payload,
                allow_legacy_replay=allow_legacy_replay,
            ):
                return payload
            event_store.write_events(chapter, extraction["accepted_events"])

            proposals = AmendProposalTrigger().check(chapter, extraction["accepted_events"])
            if proposals:
                manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
                with manager._get_conn() as conn:
                    ensure_override_ledger_columns(conn)
                    persist_amend_proposals(conn, chapter, proposals)
                    conn.commit()

        projected = self.apply_projection_writers(
            payload,
            allow_legacy_replay=allow_legacy_replay,
        )
        projection_status = projected.get("projection_status") or {}
        if not any(str(value).startswith("failed") for value in projection_status.values()):
            record_projection_snapshot(self.project_root, projected)
        return projected

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild a fact-only, chapter-bounded view from canonical commits.

Projection files are disposable read models.  This module deliberately reads
the accepted commit envelopes again, verifies their binding to the final
chapter text, and derives the exact facts visible at a requested chapter.
"""
from __future__ import annotations

import json
import re
import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .chapter_content_binding import verify_commit_content_binding
from .canon_evidence import (
    classify_evidence_contract,
    cutover_commit_linked_records,
    strict_commit_linked_records,
)
from .commit_artifacts import extraction_dict, extraction_list
from .commit_lineage import VALIDATION_NEEDS_REVALIDATION
from .consistency_context import sanitize_initial_canon
from .fact_text import (
    bound_chapter_text_for_commit,
    event_evidence_in_chapter,
    normalize_author_text,
    normalize_world_rule_payload,
    sanitize_fact_atom,
    world_rule_evidence_in_chapter,
)
from .story_contracts import sanitize_setting_canon, verify_setting_canon


_COMMIT_NAME = re.compile(r"^chapter_(\d+)\.commit\.json$")
_KNOWLEDGE_STATES = {"known", "suspected", "forgotten"}
_KNOWLEDGE_SOURCE_KINDS = {
    "witnessed", "told", "inferred", "read", "remembered", "forgotten", "unknown"
}
_PRESENCE_KINDS = {"physical", "remote", "memory", "dream", "mentioned"}
_FACT_COVERAGE_DIMENSIONS = ("knowledge", "presence", "custody")
_EVENT_COVERAGE_DIMENSION = {
    "knowledge_state_changed": "knowledge",
    "presence_observed": "presence",
    "custody_changed": "custody",
}
_ACTIVE_LIFECYCLE_KINDS = frozenset(
    {
        "promise_created",
        "open_loop_created",
        # v2 compatibility names retained in immutable cutover history.
        "reader_promise",
        "open_loop",
    }
)
_RESOLVED_LIFECYCLE_KINDS = frozenset(
    {"promise_paid", "promise_paid_off", "open_loop_closed"}
)
_LIFECYCLE_KINDS = _ACTIVE_LIFECYCLE_KINDS | _RESOLVED_LIFECYCLE_KINDS
_ENTITY_NAMESPACES = frozenset({"actor", "item", "location"})


def _namespace_from_entity_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(marker in text for marker in ("地点", "场所", "location")):
        return "location"
    if any(marker in text for marker in ("物品", "法宝", "道具", "item")):
        return "item"
    if any(marker in text for marker in ("角色", "人物", "actor", "person")):
        return "actor"
    return ""


def _ensure_entity_namespace(
    entities: Dict[str, Dict[str, Any]],
    entity_id: str,
    namespace: str,
    *,
    name: str = "",
) -> Dict[str, Any] | None:
    """Apply the namespace implied by a structured fact kind.

    Generic legacy entity rows are allowed to omit a type, but they may never
    pre-empt the authoritative role of an artifact/custody/presence/state fact.
    An already explicit incompatible namespace is an identity collision and is
    returned as ``None`` so migration can fail closed.
    """

    if not entity_id or namespace not in _ENTITY_NAMESPACES:
        return None
    row = entities.setdefault(
        entity_id,
        {"id": entity_id, "name": name or entity_id},
    )
    existing_namespace = str(
        row.get("namespace")
        or _namespace_from_entity_type(row.get("type"))
        or ""
    ).strip().lower()
    if existing_namespace and existing_namespace != namespace:
        return None
    row["namespace"] = namespace
    row["type"] = {
        "actor": "角色",
        "item": "物品",
        "location": "地点",
    }[namespace]
    if name and not str(row.get("name") or "").strip():
        row["name"] = name
    return row


def _text(value: Any, max_chars: int = 1200) -> str:
    raw = str(value or "")
    return normalize_author_text(raw, max_chars=max(1, min(max_chars, len(raw) or 1)))


def _atom(value: Any, max_chars: int = 160) -> str:
    return sanitize_fact_atom(value, max_chars=max_chars)


def _safe_value(value: Any) -> tuple[Any, bool]:
    """Sanitize a typed state value without turning it into prose control."""
    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None, True
        return value, False
    if isinstance(value, str):
        cleaned = _text(value, max_chars=600)
        return cleaned, bool(value.strip() and not cleaned)
    if isinstance(value, list):
        result: List[Any] = []
        rejected = False
        for item in value:
            cleaned, bad = _safe_value(item)
            rejected = rejected or bad
            if not bad:
                result.append(cleaned)
        return result, rejected
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        rejected = False
        for raw_key, raw_value in value.items():
            key = _atom(raw_key, max_chars=120)
            if not key:
                rejected = True
                continue
            cleaned, bad = _safe_value(raw_value)
            rejected = rejected or bad
            if not bad:
                result[key] = cleaned
        return result, rejected
    return None, True


def _first(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return ""


def _present(payload: Dict[str, Any], *keys: str) -> Any:
    """Return the first present key, preserving explicit null/false/zero."""
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _loop_content(payload: Dict[str, Any], event: Dict[str, Any]) -> str:
    content = _first(payload, "content", "unanswered_question", "description")
    if content:
        return _text(content)
    loop_type = _text(payload.get("loop_type"))
    subject = _text(event.get("subject"))
    if loop_type and subject:
        return f"{loop_type}：{subject}"
    return loop_type or subject


@dataclass
class CanonicalHistory:
    as_of_chapter: int
    valid_chapters: List[int] = field(default_factory=list)
    invalid_sources: List[str] = field(default_factory=list)
    omitted_fact_ids: List[str] = field(default_factory=list)
    initial_canon: Dict[str, Dict[str, str]] = field(default_factory=dict)
    setting_canon: Dict[str, Any] = field(default_factory=dict)
    hard_constraints: List[Dict[str, Any]] = field(default_factory=list)
    canonical_facts: List[Dict[str, Any]] = field(default_factory=list)
    entities: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    state_changes: List[Dict[str, Any]] = field(default_factory=list)
    rules: List[Dict[str, Any]] = field(default_factory=list)
    obligations: List[Dict[str, Any]] = field(default_factory=list)
    lifecycle_history: List[Dict[str, Any]] = field(default_factory=list)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    information: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    knowledge_by_entity: Dict[str, Dict[str, Dict[str, Any]]] = field(
        default_factory=dict
    )
    presence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    presence_history: List[Dict[str, Any]] = field(default_factory=list)
    custody: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    custody_history: List[Dict[str, Any]] = field(default_factory=list)
    # Exact v2 accepted-event -> canonical mapping proof used by the v3
    # cutover closure audit. It is immutable provenance, not an active fact.
    long_term_event_audit: List[Dict[str, Any]] = field(default_factory=list)
    coverage: Dict[str, str] = field(default_factory=dict)
    verification: Dict[str, str] = field(default_factory=dict)


def _v3_fact_row(record: Dict[str, Any]) -> Dict[str, Any]:
    claim = record.get("claim") if isinstance(record.get("claim"), dict) else {}
    kind = str(claim.get("kind") or "story_fact")
    subject = str(
        claim.get("subject")
        or claim.get("canonical_entity")
        or claim.get("entity")
        or claim.get("owner")
        or claim.get("promisor")
        or claim.get("item")
        or ""
    )
    field_name = str(
        claim.get("canonical_field")
        or claim.get("attribute")
        or claim.get("system")
        or ("realm" if kind == "power_breakthrough" else "")
        or ("relationship" if claim.get("object") else "")
        or ("rule" if claim.get("rule") else "")
        or ("knowledge" if claim.get("knowledge") else "")
        or ("location" if claim.get("location") else "")
        or ("custody" if claim.get("to_holder") else "")
        or ("promise" if claim.get("promise") else "")
        or ("open_loop" if claim.get("loop") else "")
        or ("timeline" if claim.get("time_anchor") else "")
        or "fact"
    )
    value = next(
        (
            claim[key]
            for key in (
                "after",
                "state",
                "presence",
                "to_holder",
                "outcome",
                "violation",
                "resolution",
                "time_anchor",
                "rule",
                "artifact",
                "entity",
                "promise",
                "loop",
            )
            if key in claim
        ),
        "",
    )
    return {
        "id": str(record.get("fact_key") or ""),
        "category": kind,
        "subject": subject,
        "field": field_name,
        "value": value,
        "payload": dict(claim),
        "status": "resolved" if kind in _RESOLVED_LIFECYCLE_KINDS else "active",
        "source_chapter": int(record.get("chapter") or 0),
        "revision": int(record.get("revision") or 0),
        "commit_hash": str(record.get("commit_hash") or ""),
        "effect_id": str(record.get("effect_id") or ""),
        "fact_digest": str(record.get("fact_digest") or ""),
        "candidate_digest": str(record.get("candidate_digest") or ""),
        "source_digests": list(record.get("source_digests") or []),
        "support_map": dict(record.get("support_map") or {}),
        "prior_fact_digest": str(record.get("prior_fact_digest") or ""),
        "prior_effect_id": str(record.get("prior_effect_id") or ""),
        "inherited_fields": dict(record.get("inherited_fields") or {}),
        "slot_id": str(claim.get("slot_id") or ""),
    }


def _compat_fact_slot(row: Dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Map legacy/v3 rows onto a conservative current-value slot."""

    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    category = str(row.get("category") or payload.get("kind") or "")
    subject = str(row.get("subject") or payload.get("subject") or "")
    field_name = str(row.get("field") or payload.get("attribute") or "")
    object_name = str(payload.get("object") or "")
    if category in {"character_state", "character_state_changed", "power_breakthrough"}:
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("state", slot_id, "", "")
        return ("state", subject, field_name, "")
    if category in {"relationship", "relationship_changed"}:
        return ("relationship", subject, object_name or field_name, "")
    if category in {"knowledge", "knowledge_state_changed"}:
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("knowledge", slot_id, "", "")
        return (
            "knowledge",
            subject,
            str(payload.get("knowledge") or field_name),
            "",
        )
    if category in {"presence", "presence_observed"}:
        return ("presence", subject, "location", "")
    if category in {"custody", "custody_changed", "artifact_obtained"}:
        return (
            "custody",
            str(payload.get("item") or payload.get("artifact") or subject),
            "holder",
            "",
        )
    if category == "world_rule_broken":
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("rule_violation", slot_id, "", "")
        return (
            "rule_violation",
            str(row.get("id") or payload.get("violation") or row.get("value") or ""),
            "",
            "",
        )
    if category in {"world_rule", "world_rule_revealed"}:
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("world_rule", slot_id, "", "")
        return ("world_rule", str(payload.get("rule") or subject), field_name, "")
    if category in {
        "reader_promise",
        "promise_created",
        "promise_paid",
        "promise_paid_off",
    }:
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("promise", slot_id, "", "")
        return (
            "promise",
            str(payload.get("promisor") or subject),
            str(payload.get("promise") or row.get("value") or field_name),
            "",
        )
    if category in {"open_loop", "open_loop_created", "open_loop_closed"}:
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("open_loop", slot_id, "", "")
        return (
            "open_loop",
            str(payload.get("loop") or subject or row.get("value") or ""),
            "",
            "",
        )
    if category in {"timeline", "timeline_observed"}:
        slot_id = str(payload.get("slot_id") or row.get("slot_id") or "")
        if slot_id:
            return ("timeline", slot_id, "", "")
        return (
            "timeline",
            str(payload.get("event") or subject or row.get("value") or ""),
            "",
            "",
        )
    return None


def _load_v3_history(
    project_root: Path,
    result: CanonicalHistory,
    as_of: int,
) -> bool:
    """Populate the compatibility view from the one HEAD-bound v3 projection."""

    if not (project_root / ".story-system" / "v3" / "CURRENT").is_file():
        return False
    try:
        from .canon_v3.projection import read_projection

        projection = read_projection(project_root, require_fresh=True)
    except Exception as exc:
        result.invalid_sources.append(
            f"canon_v3_projection:{exc.__class__.__name__}"
        )
        return True

    legacy = projection.get("legacy_base")
    if isinstance(legacy, dict) and legacy:
        try:
            from .canon_v3.migration import legacy_prefix_status

            legacy_status = legacy_prefix_status(project_root)
        except Exception as exc:
            result.invalid_sources.append(
                f"canon_v3_legacy_prefix:{exc.__class__.__name__}"
            )
            return True
        prefix_reasons = {
            str(item)
            for item in legacy_status.get("reason_codes") or []
            if str(item) != "v3_projection_stale"
        }
        if prefix_reasons:
            result.invalid_sources.extend(
                f"canon_v3_legacy_prefix:{reason}"
                for reason in sorted(prefix_reasons)
            )
            return True
        cutover = int(legacy.get("as_of_chapter") or 0)
        if as_of < cutover:
            result.invalid_sources.append(
                "canon_v3_query_before_legacy_cutover"
            )
            return True
        for field_name in (
            "initial_canon",
            "setting_canon",
            "valid_chapters",
            "omitted_fact_ids",
            "hard_constraints",
            "canonical_facts",
            "entities",
            "state_changes",
            "rules",
            "obligations",
            "lifecycle_history",
            "timeline",
            "information",
            "knowledge_by_entity",
            "presence",
            "presence_history",
            "custody",
            "custody_history",
            "long_term_event_audit",
            "coverage",
            "verification",
        ):
            if field_name in legacy:
                setattr(result, field_name, copy.deepcopy(legacy[field_name]))

    chronology = [
        dict(item)
        for item in projection.get("history") or []
        if isinstance(item, dict) and int(item.get("chapter") or 0) <= as_of
    ]
    v3_chapters = [
        int(item.get("chapter") or 0)
        for item in projection.get("chapters") or []
        if isinstance(item, dict)
        and 0 < int(item.get("chapter") or 0) <= as_of
    ]
    latest: Dict[str, Dict[str, Any]] = {
        str(item.get("id") or f"legacy-{index}"): copy.deepcopy(item)
        for index, item in enumerate(result.canonical_facts)
        if isinstance(item, dict)
    }
    v3_rows = [_v3_fact_row(item) for item in chronology]
    # Lifecycle facts are state machines, not an append-only set of active
    # obligations.  Keep every terminal event in canonical_facts as history,
    # while only the newest event for a semantic slot may remain active.
    lifecycle_latest_index: Dict[tuple[str, str, str, str], int] = {}
    for index, row in enumerate(v3_rows):
        if str(row.get("category") or "") not in _LIFECYCLE_KINDS:
            continue
        slot = _compat_fact_slot(row)
        if slot is not None:
            lifecycle_latest_index[slot] = index
    for index, row in enumerate(v3_rows):
        if str(row.get("category") or "") not in _LIFECYCLE_KINDS:
            continue
        slot = _compat_fact_slot(row)
        if slot is not None and lifecycle_latest_index.get(slot) != index:
            row["status"] = "resolved"
    superseded_slots = {
        slot for row in v3_rows if (slot := _compat_fact_slot(row)) is not None
    }
    legacy_lifecycle_rows: list[Dict[str, Any]] = []
    seen_legacy_lifecycle: set[str] = set()
    for row in [
        *result.lifecycle_history,
        *result.obligations,
        *result.hard_constraints,
    ]:
        if (
            not isinstance(row, dict)
            or str(row.get("category") or "") not in _LIFECYCLE_KINDS
        ):
            continue
        identity = str(
            row.get("fact_digest")
            or row.get("id")
            or _compat_fact_slot(row)
        )
        if identity in seen_legacy_lifecycle:
            continue
        seen_legacy_lifecycle.add(identity)
        historical = copy.deepcopy(row)
        if _compat_fact_slot(historical) in superseded_slots:
            historical["status"] = "resolved"
        legacy_lifecycle_rows.append(historical)
    result.lifecycle_history = [
        *legacy_lifecycle_rows,
        *[
            copy.deepcopy(row)
            for row in v3_rows
            if str(row.get("category") or "") in _LIFECYCLE_KINDS
        ],
    ]
    removed_information_keys: set[str] = set()
    filtered_knowledge: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for entity_id, facts_by_id in result.knowledge_by_entity.items():
        if not isinstance(facts_by_id, dict):
            continue
        kept: Dict[str, Dict[str, Any]] = {}
        for information_id, row in facts_by_id.items():
            if (
                isinstance(row, dict)
                and _compat_fact_slot(row) in superseded_slots
            ):
                removed_information_keys.add(str(information_id))
                continue
            kept[str(information_id)] = row
        if kept:
            filtered_knowledge[str(entity_id)] = kept
    result.knowledge_by_entity = filtered_knowledge
    for information_id in removed_information_keys:
        result.information.pop(information_id, None)
    latest = {
        key: row
        for key, row in latest.items()
        if _compat_fact_slot(row) not in superseded_slots
    }
    for item, row in zip(chronology, v3_rows):
        fact_key = str(item.get("fact_key") or "")
        if fact_key:
            latest[fact_key] = row
    rows = [latest[key] for key in sorted(latest)]
    result.valid_chapters = sorted(
        {
            *[int(value) for value in result.valid_chapters],
            *v3_chapters,
            *[
                int(item.get("chapter") or 0)
                for item in chronology
                if item.get("chapter")
            ],
        }
    )
    result.canonical_facts = rows
    result.state_changes = [
        *[
            row
            for row in result.state_changes
            if not isinstance(row, dict)
            or _compat_fact_slot(row) not in superseded_slots
        ],
        *[
        _v3_fact_row(item)
        for item in chronology
        if str((item.get("claim") or {}).get("kind") or "")
        in {
            "character_state_changed",
            "relationship_changed",
            "power_breakthrough",
        }
        ],
    ]

    def identity_key(namespace: str, name: str) -> str:
        return name if namespace == "actor" else f"{namespace}:{name}"

    entities: Dict[str, Dict[str, Any]] = {}
    for legacy_key, raw_entity in copy.deepcopy(result.entities).items():
        if not isinstance(raw_entity, dict):
            continue
        legacy_type = str(raw_entity.get("type") or "")
        namespace = str(raw_entity.get("namespace") or "")
        if namespace not in {"actor", "item", "location"}:
            if any(marker in legacy_type for marker in ("地点", "场所", "location")):
                namespace = "location"
            elif any(marker in legacy_type for marker in ("物品", "法宝", "item")):
                namespace = "item"
            else:
                namespace = "actor"
        canonical = str(
            raw_entity.get("id") or raw_entity.get("name") or legacy_key
        ).strip()
        if not canonical:
            continue
        key = identity_key(namespace, canonical)
        raw_entity["id"] = key
        raw_entity["namespace"] = namespace
        raw_entity.setdefault(
            "type",
            {"actor": "角色", "item": "物品", "location": "地点"}[namespace],
        )
        aliases = [str(item) for item in raw_entity.get("aliases") or [] if str(item)]
        for compatibility_name in (str(legacy_key), canonical):
            if (
                compatibility_name
                and compatibility_name != str(raw_entity.get("name") or "")
                and compatibility_name not in aliases
            ):
                aliases.append(compatibility_name)
        raw_entity["aliases"] = aliases
        previous = entities.get(key)
        if previous is None:
            entities[key] = raw_entity
        else:
            for alias in aliases:
                if alias not in previous.setdefault("aliases", []):
                    previous["aliases"].append(alias)

    def ensure_identity(namespace: str, raw_name: Any, chapter: int) -> str:
        name = str(raw_name or "").strip()
        if not name:
            return ""
        key = identity_key(namespace, name)
        type_name = {
            "actor": "角色",
            "item": "物品",
            "location": "地点",
        }.get(namespace, "实体")
        entity = entities.setdefault(
            key,
            {
                "id": key,
                "name": name,
                "type": type_name,
                "namespace": namespace,
                "tier": "核心",
                "aliases": [],
                "attributes": {},
                "first_appearance": chapter,
                "last_appearance": chapter,
            },
        )
        entity.setdefault("namespace", namespace)
        entity.setdefault("type", type_name)
        entity.setdefault("aliases", [])
        entity.setdefault("attributes", {})
        entity["last_appearance"] = max(
            int(entity.get("last_appearance") or 0), chapter
        )
        return key

    for row in rows:
        claim = row.get("payload") or {}
        chapter = int(row.get("source_chapter") or 0)
        kind = str(claim.get("kind") or "")
        actor_names = [
            claim.get("subject"),
            claim.get("object") if kind == "relationship_changed" else None,
            claim.get("owner"),
            claim.get("promisor"),
            claim.get("promisee"),
            claim.get("from_holder"),
            claim.get("to_holder"),
        ]
        for raw_name in actor_names:
            ensure_identity("actor", raw_name, chapter)
        for raw_item in (claim.get("item"), claim.get("artifact")):
            ensure_identity("item", raw_item, chapter)
        ensure_identity("location", claim.get("location"), chapter)
        if kind == "entity_observed":
            entity_key = ensure_identity(
                str(claim.get("namespace") or "actor"),
                claim.get("canonical_entity") or claim.get("entity"),
                chapter,
            )
            if entity_key and claim.get("entity"):
                entities[entity_key]["name"] = str(claim.get("entity"))
        if kind == "character_state_changed":
            key = ensure_identity("actor", claim.get("subject"), chapter)
            if key:
                entities[key]["attributes"][
                    str(
                        claim.get("canonical_field")
                        or claim.get("attribute")
                        or "state"
                    )
                ] = claim.get("after")
        if kind == "power_breakthrough":
            key = ensure_identity("actor", claim.get("subject"), chapter)
            if key:
                entities[key]["attributes"][
                    str(
                        claim.get("canonical_field")
                        or claim.get("system")
                        or "realm"
                    )
                ] = claim.get("after")

    # Alias registration is append-only within the active HEAD. Accumulate
    # every reachable registration event rather than overwriting with only the
    # latest entity_observed fact row.
    for record in chronology:
        claim = record.get("claim") if isinstance(record.get("claim"), dict) else {}
        if claim.get("kind") != "entity_observed":
            continue
        namespace = str(claim.get("namespace") or "actor")
        canonical = claim.get("canonical_entity") or claim.get("entity")
        key = ensure_identity(
            namespace,
            canonical,
            int(record.get("chapter") or 0),
        )
        if not key:
            continue
        aliases = entities[key]["aliases"]
        if claim.get("entity"):
            entities[key]["name"] = str(claim.get("entity"))
        for alias in (claim.get("entity"), *(claim.get("aliases") or [])):
            value = str(alias or "").strip()
            if value and value != str(canonical or "") and value not in aliases:
                aliases.append(value)
    result.entities = dict(sorted(entities.items()))

    v3_rules = [
        row
        for row in rows
        if str(row.get("category") or "") == "world_rule_revealed"
    ]
    relationships = [
        row for row in rows if row.get("category") == "relationship_changed"
    ]
    v3_obligations = sorted(
        (
            v3_rows[index]
            for index in lifecycle_latest_index.values()
            if str(v3_rows[index].get("category") or "")
            in _ACTIVE_LIFECYCLE_KINDS
            and str(v3_rows[index].get("status") or "") == "active"
        ),
        key=lambda row: (
            int(row.get("source_chapter") or 0),
            str(row.get("category") or ""),
            str(row.get("id") or ""),
        ),
    )
    v3_timeline = [
        row for row in rows if row.get("category") == "timeline_observed"
    ]
    result.rules = [
        *[
            row
            for row in result.rules
            if not isinstance(row, dict)
            or _compat_fact_slot(row) not in superseded_slots
        ],
        *v3_rules,
    ]
    result.obligations = [
        *[
            row
            for row in result.obligations
            if not isinstance(row, dict)
            or _compat_fact_slot(row) not in superseded_slots
        ],
        *v3_obligations,
    ]
    result.timeline = [
        *[
            row
            for row in result.timeline
            if not isinstance(row, dict)
            or _compat_fact_slot(row) not in superseded_slots
        ],
        *v3_timeline,
    ]
    result.hard_constraints = [
        *[
            row
            for row in result.hard_constraints
            if not isinstance(row, dict)
            or _compat_fact_slot(row) not in superseded_slots
        ],
        *v3_rules,
        *relationships,
        *v3_obligations,
    ]

    for item in chronology:
        claim = item.get("claim") if isinstance(item.get("claim"), dict) else {}
        kind = str(claim.get("kind") or "")
        row = _v3_fact_row(item)
        if kind == "knowledge_state_changed":
            subject = str(claim.get("subject") or "")
            information_id = str(
                claim.get("slot_id") or claim.get("knowledge") or ""
            )
            result.knowledge_by_entity.setdefault(subject, {})[information_id] = row
            result.information[information_id] = row
        elif kind == "presence_observed":
            subject = str(claim.get("subject") or "")
            result.presence[subject] = row
            result.presence_history.append(row)
        elif kind in {"custody_changed", "artifact_obtained"}:
            item_name = str(claim.get("item") or claim.get("artifact") or "")
            result.custody[item_name] = row
            result.custody_history.append(row)
    result.knowledge_by_entity = {
        key: dict(sorted(value.items()))
        for key, value in sorted(result.knowledge_by_entity.items())
    }
    result.information = dict(sorted(result.information.items()))
    result.presence = dict(sorted(result.presence.items()))
    result.custody = dict(sorted(result.custody.items()))
    result.coverage = {
        dimension: "complete" for dimension in _FACT_COVERAGE_DIMENSIONS
    }
    result.verification = {
        dimension: "supported" for dimension in _FACT_COVERAGE_DIMENSIONS
    }
    return True


def _trusted_commits(
    project_root: Path,
    as_of_chapter: int,
) -> tuple[List[Dict[str, Any]], List[str]]:
    commits_dir = project_root / ".story-system" / "commits"
    if not commits_dir.is_dir() or as_of_chapter <= 0:
        return [], []

    selected: List[tuple[int, Path]] = []
    for path in commits_dir.glob("chapter_*.commit.json"):
        match = _COMMIT_NAME.match(path.name)
        if not match:
            continue
        chapter = int(match.group(1))
        if 0 < chapter <= as_of_chapter:
            selected.append((chapter, path))

    commits: List[Dict[str, Any]] = []
    invalid: List[str] = []
    for chapter, path in sorted(selected):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            invalid.append(f"chapter_commit:{chapter}:invalid_json")
            continue
        if not isinstance(payload, dict):
            invalid.append(f"chapter_commit:{chapter}:root_must_be_object")
            continue
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if meta.get("chapter") != chapter:
            invalid.append(f"chapter_commit:{chapter}:chapter_mismatch")
            continue
        if meta.get("status") != "accepted":
            # Rejected work is not a broken historical source and contributes
            # no canon.  It is therefore ignored rather than surfaced as fact.
            continue
        if str(meta.get("validation_status") or "") == VALIDATION_NEEDS_REVALIDATION:
            # Later chapters after a rewritten prefix stay on disk but are not
            # currently canonical until they are reviewed and extracted again.
            continue
        evidence_classification = classify_evidence_contract(payload)
        if evidence_classification == "invalid":
            invalid.append(
                f"chapter_commit:{chapter}:evidence_contract_invalid:invalid_envelope"
            )
            continue
        trusted, code = verify_commit_content_binding(project_root, chapter, payload)
        if not trusted:
            invalid.append(f"chapter_commit:{chapter}:{code}")
            continue
        if evidence_classification == "strict":
            chapter_text = bound_chapter_text_for_commit(project_root, payload) or ""
            try:
                strict_commit_linked_records(payload, chapter_text)
            except (TypeError, ValueError) as exc:
                invalid.append(
                    f"chapter_commit:{chapter}:evidence_contract_invalid:{exc}"
                )
                continue
        commits.append(payload)
    return commits, invalid


def load_canonical_history(
    project_root: Path,
    as_of_chapter: int,
    *,
    prefer_v3: bool = True,
    cutover_strict: bool = False,
) -> CanonicalHistory:
    """Return all consistency facts visible at ``as_of_chapter``.

    No fact from a later chapter, rejected commit, or commit whose bound prose
    has changed can enter the result.
    """
    project_root = Path(project_root).expanduser().resolve()
    as_of = max(0, int(as_of_chapter or 0))
    result = CanonicalHistory(as_of_chapter=as_of)

    # Once v3 is active, live legacy setting files are author input only. They
    # do not become canonical merely because a JSON file changed; factual
    # values must be imported as hash-bound author_axiom candidates (or already
    # exist in the immutable migration snapshot).
    if prefer_v3 and _load_v3_history(project_root, result, as_of):
        return result

    master_path = project_root / ".story-system" / "MASTER_SETTING.json"
    if master_path.is_file():
        try:
            master = json.loads(master_path.read_text(encoding="utf-8"))
            if isinstance(master, dict):
                result.initial_canon = sanitize_initial_canon(
                    master.get("initial_canon")
                )
                setting_ok, _setting_reason = verify_setting_canon(
                    project_root,
                    master.get("setting_canon"),
                )
                if setting_ok:
                    result.setting_canon = sanitize_setting_canon(
                        master.get("setting_canon")
                    )
        except (OSError, json.JSONDecodeError):
            # Contract-source reporting is owned by load_context; do not emit
            # the same error twice here.
            pass

    commits, invalid = _trusted_commits(project_root, as_of)
    result.invalid_sources.extend(invalid)
    result.valid_chapters = [int(commit["meta"]["chapter"]) for commit in commits]

    states: Dict[tuple[str, str], Dict[str, Any]] = {}
    entities: Dict[str, Dict[str, Any]] = {}
    relationships: Dict[tuple[str, str], Dict[str, Any]] = {}
    rules: Dict[str, Dict[str, Any]] = {}
    loops: Dict[str, Dict[str, Any]] = {}
    promises: Dict[str, Dict[str, Any]] = {}
    lifecycle_history: List[Dict[str, Any]] = []
    story_facts: Dict[str, Dict[str, Any]] = {}
    timeline: Dict[str, Dict[str, Any]] = {}
    state_changes: List[Dict[str, Any]] = []
    information: Dict[str, Dict[str, Any]] = {}
    knowledge_by_entity: Dict[str, Dict[str, Dict[str, Any]]] = {}
    presence: Dict[str, Dict[str, Any]] = {}
    presence_history: List[Dict[str, Any]] = []
    custody: Dict[str, Dict[str, Any]] = {}
    custody_history: List[Dict[str, Any]] = []
    coverage_claims: List[Dict[str, Any]] = []
    verification_claims: List[Dict[str, Any]] = []
    coverage_failures: set[str] = set()
    legacy_dimensions: set[str] = set()

    audit_by_identity: Dict[tuple[int, str, str], Dict[str, Any]] = {}

    def _audit_digest(value: Any) -> str:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def audit_long_term_event(
        chapter: int,
        event_id: str,
        event_type: str,
        target: str,
        source_event: Dict[str, Any],
        normalized_facts: Dict[str, Any] | Iterable[Dict[str, Any]],
    ) -> None:
        """Retain the exact event and every fact image it produced.

        The active reducers below intentionally keep only the newest value for
        state-like slots.  Migration closure therefore cannot be proved by an
        event ID alone: the cutover also needs the historical fact image that
        each accepted event produced before a later event overwrote it.
        """

        identity = (int(chapter), str(event_id), str(event_type))
        if isinstance(normalized_facts, dict):
            facts = [normalized_facts]
        else:
            facts = [item for item in normalized_facts if isinstance(item, dict)]
        if not facts:
            raise ValueError(
                "long_term_event_audit_requires_normalized_fact:"
                f"{chapter}:{event_id}:{event_type}"
            )
        entry = audit_by_identity.get(identity)
        payload = (
            source_event.get("payload")
            if isinstance(source_event.get("payload"), dict)
            else {}
        )
        source_event_digest = _audit_digest(source_event)
        evidence_quote_digest = _audit_digest(
            _text(payload.get("evidence_quote"), max_chars=600)
        )
        if entry is None:
            entry = {
                "chapter": identity[0],
                "event_id": identity[1],
                "event_type": identity[2],
                "target": str(target),
                "targets": [str(target)],
                # Never replay the open v2 payload into future model context.
                # Digests prove exact provenance while normalized_facts below
                # contain only the reader's fact whitelist.
                "source_event_digest": source_event_digest,
                "evidence_quote_digest": evidence_quote_digest,
                "normalized_facts": [],
                "normalized_fact_digests": [],
            }
            audit_by_identity[identity] = entry
            result.long_term_event_audit.append(entry)
        elif entry.get("source_event_digest") != source_event_digest:
            raise ValueError(
                "long_term_event_audit_source_event_changed:"
                f"{chapter}:{event_id}:{event_type}"
            )
        targets = {str(item) for item in entry.get("targets") or [] if str(item)}
        targets.add(str(target))
        entry["targets"] = sorted(targets)
        entry["target"] = entry["targets"][0]
        known_digests = set(entry.get("normalized_fact_digests") or [])
        for fact in facts:
            fact_copy = copy.deepcopy(dict(fact))
            digest = _audit_digest(fact_copy)
            if digest in known_digests:
                continue
            entry["normalized_facts"].append(fact_copy)
            entry["normalized_fact_digests"].append(digest)
            known_digests.add(digest)

    def remember_state(
        *, chapter: int, entity_id: Any, field_name: Any, value: Any,
        source_id: str, old_value: Any = None, reason: Any = "",
        evidence_quote: Any = "", verification: Any = "legacy",
    ) -> None:
        entity = _atom(entity_id)
        field_name_clean = _atom(field_name)
        safe_value, rejected = _safe_value(value)
        if not entity or not field_name_clean or rejected:
            result.omitted_fact_ids.append(source_id)
            return
        if _ensure_entity_namespace(entities, entity, "actor") is None:
            result.omitted_fact_ids.append(source_id)
            return
        row = {
            "id": source_id,
            "category": "character_state",
            "subject": entity,
            "field": field_name_clean,
            "value": safe_value,
            "status": "active",
            "source_chapter": chapter,
            "source_event_id": source_id,
            "evidence_quote": _text(evidence_quote, max_chars=600),
            "verification": _atom(verification, 40) or "legacy",
        }
        states[(entity, field_name_clean)] = row
        safe_old, old_bad = _safe_value(old_value)
        state_changes.append(
            {
                "id": source_id,
                "entity_id": entity,
                "field": field_name_clean,
                "old": None if old_bad else safe_old,
                "new": safe_value,
                "reason": _text(reason, max_chars=300),
                "chapter": chapter,
                "source_event_id": source_id,
                "evidence_quote": _text(evidence_quote, max_chars=600),
                "verification": _atom(verification, 40) or "legacy",
            }
        )

    for commit in commits:
        chapter = int(commit["meta"]["chapter"])
        chapter_text = bound_chapter_text_for_commit(project_root, commit) or ""
        strict_evidence = classify_evidence_contract(commit) == "strict"
        cutover_evidence = strict_evidence or cutover_strict
        strict_records = (
            cutover_commit_linked_records(commit, chapter_text)
            if cutover_strict
            else strict_commit_linked_records(commit, chapter_text)
            if strict_evidence
            else {}
        )
        strict_entity_rows_by_source: Dict[str, List[Dict[str, Any]]] = {}
        strict_timeline_rows_by_source: Dict[str, List[Dict[str, Any]]] = {}
        if cutover_evidence:
            for linked in strict_records.get("entity_deltas", []) or []:
                if not isinstance(linked, dict):
                    continue
                source_id = _atom(linked.get("source_event_id"), 180)
                if source_id:
                    strict_entity_rows_by_source.setdefault(source_id, []).append(linked)
            for linked in strict_records.get("timeline_events", []) or []:
                if not isinstance(linked, dict):
                    continue
                source_id = _atom(linked.get("source_event_id"), 180)
                if source_id:
                    strict_timeline_rows_by_source.setdefault(source_id, []).append(linked)
        coverage_claim = extraction_dict(commit, "fact_coverage")
        verification_claim = extraction_dict(commit, "fact_verification")
        coverage_claims.append(coverage_claim)
        verification_claims.append(verification_claim)
        for dimension in _FACT_COVERAGE_DIMENSIONS:
            coverage_state = str(coverage_claim.get(dimension) or "")
            verification_state = str(verification_claim.get(dimension) or "")
            if coverage_state and coverage_state not in {"complete", "partial"}:
                coverage_failures.add(dimension)
                legacy_dimensions.add(dimension)
            if verification_state and verification_state not in {
                "supported",
                "verified",
                "pending",
                "unknown",
            }:
                coverage_failures.add(dimension)
                legacy_dimensions.add(dimension)

        # Entity appearances are useful identity evidence, but they never
        # imply physical presence. Only presence_observed may update location.
        for appeared in (
            [] if cutover_evidence else extraction_list(commit, "entities_appeared")
        ):
            if not isinstance(appeared, dict):
                continue
            entity_id = _atom(appeared.get("id") or appeared.get("entity_id"))
            if not entity_id:
                continue
            current = entities.setdefault(
                entity_id,
                {"id": entity_id, "name": entity_id},
            )
            mentions = appeared.get("mentions")
            name = ""
            if isinstance(mentions, list):
                name = next(
                    (_text(item, 240) for item in mentions if _text(item, 240)),
                    "",
                )
            entity_type = _atom(appeared.get("type") or appeared.get("entity_type"), 80)
            if name:
                current["name"] = name
            if entity_type:
                current["type"] = entity_type
                inferred_namespace = _namespace_from_entity_type(entity_type)
                if inferred_namespace:
                    current["namespace"] = inferred_namespace
            current.setdefault("first_appearance", chapter)
            current["first_appearance"] = min(
                int(current.get("first_appearance") or chapter), chapter
            )
            current["last_appearance"] = max(
                int(current.get("last_appearance") or 0), chapter
            )

        for scene in ([] if cutover_evidence else extraction_list(commit, "scenes")):
            if not isinstance(scene, dict) or not isinstance(scene.get("characters"), list):
                continue
            for raw_entity in scene.get("characters") or []:
                entity_id = _atom(raw_entity)
                if not entity_id:
                    continue
                current = entities.setdefault(
                    entity_id,
                    {"id": entity_id, "name": entity_id, "type": "角色"},
                )
                current.setdefault("first_appearance", chapter)
                current["first_appearance"] = min(
                    int(current.get("first_appearance") or chapter), chapter
                )
                current["last_appearance"] = max(
                    int(current.get("last_appearance") or 0), chapter
                )

        entity_delta_rows = (
            strict_records.get("entity_deltas", [])
            if cutover_evidence
            else extraction_list(commit, "entity_deltas")
        )
        for index, delta in enumerate(entity_delta_rows):
            if not isinstance(delta, dict):
                continue
            from_entity = _atom(delta.get("from_entity") or delta.get("from"))
            to_entity = _atom(delta.get("to_entity") or delta.get("to"))
            if from_entity and to_entity:
                if cutover_evidence:
                    # The evidence-bound relationship event below is the
                    # canonical authority; its linked delta is projection input.
                    continue
                relation = _atom(
                    _first(delta, "relationship_type", "relation_type", "type")
                )
                if relation:
                    rel_id = _atom(delta.get("id")) or f"rel-{chapter}-{index}"
                    relationships[(from_entity, to_entity)] = {
                        "id": rel_id,
                        "category": "relationship",
                        "subject": from_entity,
                        "field": to_entity,
                        "value": relation,
                        "payload": {},
                        "status": "active",
                        "source_chapter": chapter,
                    }
                continue
            entity_id = _atom(delta.get("entity_id") or delta.get("id"))
            if not entity_id:
                continue
            current = entities.setdefault(
                entity_id,
                {"id": entity_id, "name": entity_id},
            )
            name = _text(delta.get("canonical_name") or delta.get("name"), 240)
            prior_name = _text(current.get("name"), 240)
            entity_type = _atom(delta.get("entity_type") or delta.get("type"), 80)
            tier = _atom(delta.get("tier"), 80)
            if name:
                current["name"] = name
            if entity_type:
                current["type"] = entity_type
                inferred_namespace = _namespace_from_entity_type(entity_type)
                if inferred_namespace:
                    current["namespace"] = inferred_namespace
            if tier:
                current["tier"] = tier
            aliases = list(current.get("aliases") or [])
            if name and prior_name and prior_name != name and prior_name not in aliases:
                aliases.append(prior_name)
            for bucket_key in ("aliases", "mentions"):
                bucket = delta.get(bucket_key)
                if not isinstance(bucket, list):
                    continue
                for item in bucket:
                    text = _text(item, 240)
                    if text and text not in aliases:
                        aliases.append(text)
            if aliases:
                current["aliases"] = aliases
            linked_source_id = _atom(delta.get("source_event_id"), 180)
            if linked_source_id:
                current["source_chapter"] = chapter
                current["source_event_id"] = linked_source_id
            current.setdefault("first_appearance", chapter)
            current["first_appearance"] = min(
                int(current.get("first_appearance") or chapter), chapter
            )
            current["last_appearance"] = max(
                int(current.get("last_appearance") or 0), chapter
            )

        state_delta_rows = (
            []
            if cutover_evidence
            else extraction_list(commit, "state_deltas")
        )
        for index, delta in enumerate(state_delta_rows):
            if not isinstance(delta, dict):
                continue
            remember_state(
                chapter=chapter,
                entity_id=delta.get("entity_id") or delta.get("subject"),
                field_name=delta.get("field") or delta.get("field_path"),
                value=_present(delta, "new", "new_value", "to"),
                old_value=_present(delta, "old", "old_value", "from"),
                reason=delta.get("reason"),
                source_id=_atom(delta.get("id")) or f"state-{chapter}-{index}",
            )

        raw_events = extraction_list(commit, "accepted_events")
        ordered_events: List[tuple[int, int, Dict[str, Any]]] = []
        for original_index, raw_event in enumerate(raw_events):
            if not isinstance(raw_event, dict):
                continue
            event_type = str(raw_event.get("event_type") or "").strip()
            raw_sequence = raw_event.get("sequence")
            try:
                sequence = (
                    int(raw_sequence)
                    if raw_sequence not in (None, "")
                    and not isinstance(raw_sequence, bool)
                    else original_index + 1
                )
            except (TypeError, ValueError):
                sequence = original_index + 1
            dimension = _EVENT_COVERAGE_DIMENSION.get(event_type)
            if dimension and raw_sequence in (None, ""):
                coverage_failures.add(dimension)
                legacy_dimensions.add(dimension)
            if dimension:
                raw_verification = str(
                    raw_event.get("verification") or ""
                ).strip().lower()
                if not raw_verification:
                    legacy_dimensions.add(dimension)
                elif raw_verification not in {"supported", "verified"}:
                    coverage_failures.add(dimension)
            ordered_events.append((sequence, original_index, raw_event))

        accepted_event_by_id: Dict[str, Dict[str, Any]] = {}
        for _ordered_sequence, ordered_index, ordered_event in ordered_events:
            ordered_id = (
                _atom(ordered_event.get("event_id"), 180)
                or f"event-{chapter}-{ordered_index}"
            )
            accepted_event_by_id[ordered_id] = ordered_event

        for _sequence, index, event in sorted(
            ordered_events,
            key=lambda item: (item[0], item[1]),
        ):
            if not isinstance(event, dict):
                continue
            event_id = _atom(event.get("event_id"), 180) or f"event-{chapter}-{index}"
            event_type = str(event.get("event_type") or "").strip()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            subject = _atom(event.get("subject"))
            event_verification = str(
                event.get("verification") or "legacy"
            ).strip().lower()

            if event_type in {"character_state_changed", "power_breakthrough"}:
                omitted_before = len(result.omitted_fact_ids)
                remember_state(
                    chapter=chapter,
                    entity_id=payload.get("entity_id") or subject,
                    field_name=(
                        payload.get("field")
                        or payload.get("field_path")
                        or ("realm" if event_type == "power_breakthrough" else "")
                    ),
                    value=_present(payload, "new", "new_value", "to", "new_state"),
                    old_value=_present(
                        payload, "old", "old_value", "from", "previous_state"
                    ),
                    reason=event_type,
                    source_id=event_id,
                    evidence_quote=payload.get("evidence_quote"),
                    verification=event_verification,
                )
                if len(result.omitted_fact_ids) == omitted_before:
                    state_entity = _atom(payload.get("entity_id") or subject)
                    state_field = _atom(
                        payload.get("field")
                        or payload.get("field_path")
                        or ("realm" if event_type == "power_breakthrough" else "")
                    )
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "state_changes",
                        event,
                        [state_changes[-1], states[(state_entity, state_field)]],
                    )
            elif event_type == "relationship_changed":
                from_entity = _atom(payload.get("from_entity") or subject)
                to_entity = _atom(payload.get("to_entity") or payload.get("to"))
                relation = _atom(
                    _first(payload, "relationship_type", "relation_type", "type")
                )
                from_actor = (
                    _ensure_entity_namespace(entities, from_entity, "actor")
                    if from_entity
                    else None
                )
                to_actor = (
                    _ensure_entity_namespace(entities, to_entity, "actor")
                    if to_entity
                    else None
                )
                if from_entity and to_entity and relation and from_actor and to_actor:
                    relationships[(from_entity, to_entity)] = {
                        "id": event_id,
                        "category": "relationship",
                        "subject": from_entity,
                        "field": to_entity,
                        "value": relation,
                        "payload": {},
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "relationships",
                        event,
                        relationships[(from_entity, to_entity)],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "world_rule_revealed":
                normalized_rule = normalize_world_rule_payload(payload, subject)
                value = str((normalized_rule or {}).get("rule_content") or "")
                rule_id = _atom(payload.get("rule_id"), 180) or event_id
                domain = str((normalized_rule or {}).get("domain") or "")
                field_name = str((normalized_rule or {}).get("field") or "")
                if (
                    value
                    and rule_id
                    and domain
                    and field_name
                    and world_rule_evidence_in_chapter(payload, subject, chapter_text)
                ):
                    rules[rule_id] = {
                        "id": rule_id,
                        "category": "world_rule",
                        "subject": domain,
                        "field": field_name,
                        "value": value,
                        "payload": {
                            "scope": str((normalized_rule or {}).get("scope") or "global"),
                            "rule_category": str(
                                (normalized_rule or {}).get("rule_category") or ""
                            ),
                        },
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "rules",
                        event,
                        rules[rule_id],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "world_rule_broken":
                rule_id = _atom(
                    payload.get("rule_id")
                    or payload.get("target_rule_id"),
                    180,
                )
                rule_descriptor = rule_id or _atom(
                    "|".join(
                        item
                        for item in (
                            str(subject or ""),
                            str(payload.get("domain") or ""),
                            str(payload.get("field") or ""),
                        )
                        if item
                    ),
                    180,
                )
                violation = _text(
                    payload.get("violation")
                    or payload.get("description")
                    or payload.get("proposed_value")
                    or event.get("subject"),
                    max_chars=600,
                )
                if rule_descriptor and violation:
                    story_facts[event_id] = {
                        "id": event_id,
                        "category": "world_rule_broken",
                        "subject": rule_descriptor,
                        "field": "violation",
                        "value": violation,
                        "payload": {
                            "rule_id": rule_id,
                            "base_value": _text(payload.get("base_value")),
                        },
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "rule_violations",
                        event,
                        story_facts[event_id],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "open_loop_created":
                loop_id = _atom(
                    payload.get("loop_id") or payload.get("open_loop_id") or event_id,
                    180,
                )
                content = _loop_content(payload, event)
                if loop_id and content:
                    loops[loop_id] = {
                        "id": loop_id,
                        "category": "open_loop",
                        "subject": "",
                        "field": "status",
                        "value": content,
                        "payload": {
                            "lifecycle_id": loop_id,
                            "urgency": payload.get("urgency") or 0,
                            "expected_payoff": _text(payload.get("expected_payoff")),
                        },
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    lifecycle_history.append(copy.deepcopy(loops[loop_id]))
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "lifecycle_history",
                        event,
                        lifecycle_history[-1],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "open_loop_closed":
                loop_id = _atom(
                    _first(
                        payload,
                        "loop_id",
                        "target_loop_id",
                        "open_loop_id",
                        "target_id",
                        "resolves_event_id",
                    ),
                    180,
                )
                prior_loop = loops.get(loop_id) if loop_id else None
                if loop_id and prior_loop is not None:
                    loops.pop(loop_id, None)
                    for historical in lifecycle_history:
                        if str((historical.get("payload") or {}).get("lifecycle_id") or "") == loop_id:
                            historical["status"] = "resolved"
                    lifecycle_history.append(
                        {
                            "id": event_id,
                            "category": "open_loop_closed",
                            "subject": "",
                            "field": "status",
                            "value": _text(
                                payload.get("resolution")
                                or payload.get("description")
                                or "closed"
                            ),
                            "payload": {
                                "lifecycle_id": loop_id,
                                "loop": str((prior_loop or {}).get("value") or ""),
                            },
                            "status": "resolved",
                            "source_chapter": chapter,
                            "source_event_id": event_id,
                            "evidence_quote": _text(
                                payload.get("evidence_quote"), max_chars=600
                            ),
                            "verification": event_verification,
                        }
                    )
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "lifecycle_history",
                        event,
                        lifecycle_history[-1],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "promise_created":
                promise_id = _atom(payload.get("promise_id") or event_id, 180)
                content = _text(
                    payload.get("content")
                    or payload.get("description")
                    or event.get("subject")
                )
                if promise_id and content:
                    promises[promise_id] = {
                        "id": promise_id,
                        "category": "reader_promise",
                        "subject": "",
                        "field": "promise",
                        "value": content,
                        "payload": {
                            "lifecycle_id": promise_id,
                            "urgency": payload.get("urgency") or 0,
                            "expected_payoff": _text(payload.get("expected_payoff")),
                        },
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    lifecycle_history.append(copy.deepcopy(promises[promise_id]))
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "lifecycle_history",
                        event,
                        lifecycle_history[-1],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "promise_paid_off":
                promise_id = _atom(
                    _first(
                        payload,
                        "promise_id",
                        "target_promise_id",
                        "target_id",
                        "resolves_event_id",
                    ),
                    180,
                )
                prior_promise = promises.get(promise_id) if promise_id else None
                if promise_id and prior_promise is not None:
                    promises.pop(promise_id, None)
                    for historical in lifecycle_history:
                        if str((historical.get("payload") or {}).get("lifecycle_id") or "") == promise_id:
                            historical["status"] = "resolved"
                    lifecycle_history.append(
                        {
                            "id": event_id,
                            "category": "promise_paid_off",
                            "subject": "",
                            "field": "status",
                            "value": _text(
                                payload.get("resolution")
                                or payload.get("outcome")
                                or "paid_off"
                            ),
                            "payload": {
                                "lifecycle_id": promise_id,
                                "promise": str(
                                    (prior_promise or {}).get("value") or ""
                                ),
                            },
                            "status": "resolved",
                            "source_chapter": chapter,
                            "source_event_id": event_id,
                            "evidence_quote": _text(
                                payload.get("evidence_quote"), max_chars=600
                            ),
                            "verification": event_verification,
                        }
                    )
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "lifecycle_history",
                        event,
                        lifecycle_history[-1],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "entity_observed":
                linked_entities: List[Dict[str, Any]] = []
                linked_entity_ids: List[str] = []
                for linked in strict_entity_rows_by_source.get(event_id, []):
                    linked_id = _atom(
                        linked.get("entity_id") or linked.get("id"), 180
                    )
                    if linked_id and linked_id in entities:
                        if linked_id not in linked_entity_ids:
                            linked_entity_ids.append(linked_id)
                            linked_entities.append(entities[linked_id])
                requested_entity_id = _atom(
                    payload.get("entity_id")
                    or payload.get("canonical_id")
                    or subject,
                    180,
                )
                if linked_entity_ids and requested_entity_id not in linked_entity_ids:
                    if len(linked_entity_ids) == 1:
                        entity_id = linked_entity_ids[0]
                    else:
                        result.omitted_fact_ids.append(event_id)
                        continue
                else:
                    entity_id = requested_entity_id
                existing_entity = entities.get(entity_id) or {}
                explicit_namespace = str(payload.get("namespace") or "").strip().lower()
                typed_namespace = _namespace_from_entity_type(
                    payload.get("entity_type") or payload.get("type")
                )
                existing_namespace = str(
                    existing_entity.get("namespace")
                    or _namespace_from_entity_type(existing_entity.get("type"))
                    or ""
                ).strip().lower()
                namespace_candidates = {
                    value
                    for value in (
                        explicit_namespace,
                        typed_namespace,
                        existing_namespace,
                    )
                    if value
                }
                namespace_conflict = (
                    any(value not in _ENTITY_NAMESPACES for value in namespace_candidates)
                    or len(namespace_candidates) > 1
                )
                namespace = (
                    explicit_namespace
                    or typed_namespace
                    or existing_namespace
                    or "actor"
                )
                display_name = _text(
                    payload.get("name")
                    or existing_entity.get("name")
                    or subject
                    or entity_id,
                    max_chars=180,
                )
                raw_aliases = payload.get("aliases") or []
                aliases = [
                    _text(alias, max_chars=180)
                    for alias in raw_aliases
                    if _text(alias, max_chars=180)
                ] if isinstance(raw_aliases, list) else []
                if (
                    not namespace_conflict
                    and namespace in _ENTITY_NAMESPACES
                    and entity_id
                    and display_name
                    and event_evidence_in_chapter(event, chapter_text)
                ):
                    entity = entities.setdefault(
                        entity_id,
                        {
                            "id": entity_id,
                            "name": display_name,
                            "type": {
                                "actor": "角色",
                                "item": "物品",
                                "location": "地点",
                            }[namespace],
                        },
                    )
                    entity["namespace"] = namespace
                    prior_display_name = _text(entity.get("name"), max_chars=180)
                    entity["name"] = display_name
                    entity["aliases"] = sorted(
                        {
                            *[str(item) for item in entity.get("aliases") or []],
                            *aliases,
                            *(
                                [prior_display_name]
                                if prior_display_name
                                and prior_display_name != display_name
                                else []
                            ),
                        }
                    )
                    entity.setdefault("first_appearance", chapter)
                    entity["first_appearance"] = min(
                        int(entity.get("first_appearance") or chapter), chapter
                    )
                    entity["last_appearance"] = max(
                        int(entity.get("last_appearance") or 0), chapter
                    )
                    entity["source_chapter"] = chapter
                    entity["source_event_id"] = event_id
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "entities",
                        event,
                        [entity, *linked_entities],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "timeline_observed":
                if strict_timeline_rows_by_source.get(event_id):
                    # The linked row is the strict timeline authority and is
                    # materialized/audited in the common timeline pass below.
                    continue
                event_text = _text(
                    payload.get("event")
                    or payload.get("content")
                    or subject,
                    max_chars=600,
                )
                time_anchor = _text(
                    payload.get("time_anchor")
                    or payload.get("time_hint")
                    or payload.get("time"),
                    max_chars=240,
                )
                if (
                    event_text
                    and time_anchor
                    and event_evidence_in_chapter(event, chapter_text)
                ):
                    # During v3 cutover a timeline ID remains provenance only;
                    # each admitted event is its own occurrence slot.
                    timeline_id = (
                        event_id
                        if cutover_strict
                        else _atom(payload.get("timeline_id"), 180) or event_id
                    )
                    timeline[timeline_id] = {
                        "id": timeline_id,
                        "category": "timeline",
                        "subject": _atom(
                            payload.get("event_type") or event_type, 80
                        ),
                        "field": "event",
                        "value": event_text,
                        "payload": {
                            "sequence": int(event.get("sequence") or _sequence),
                            "time_hint": time_anchor,
                            "event_type": _atom(
                                payload.get("event_type") or event_type, 80
                            ),
                        },
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "timeline",
                        event,
                        timeline[timeline_id],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
            elif event_type == "knowledge_state_changed":
                information_id = _atom(payload.get("information_id"), 180)
                raw_claim = payload.get("canonical_claim") or payload.get("content")
                canonical_claim = _text(raw_claim, max_chars=600)
                raw_fragment = payload.get("evidence_fragment") or payload.get("content")
                evidence_fragment = _text(raw_fragment, max_chars=600)
                evidence_quote = str(payload.get("evidence_quote") or "").strip()
                state = str(payload.get("state") or "").strip().lower()
                source_kind = str(payload.get("source_kind") or "").strip().lower()
                source_entity = _atom(payload.get("source_entity"), 180)
                existing_information = information.get(information_id)
                content = str(
                    (existing_information or {}).get("canonical_claim")
                    or (existing_information or {}).get("content")
                    or canonical_claim
                )
                if (
                    existing_information
                    and event_verification == "verified"
                    and canonical_claim
                    and canonical_claim != content
                ):
                    # 人工裁决过的表述是最高优先级来源：replace/confirm 之后的
                    # verified 事件可以更正既往表述，未经裁决的模型换述不行。
                    content = canonical_claim
                knowledge_actor = (
                    _ensure_entity_namespace(entities, subject, "actor")
                    if subject
                    else None
                )
                knowledge_source_actor = (
                    _ensure_entity_namespace(entities, source_entity, "actor")
                    if source_entity
                    else True
                )
                valid_information = (
                    bool(subject)
                    and bool(information_id)
                    and bool(content)
                    and isinstance(raw_fragment, str)
                    and raw_fragment.strip() in evidence_quote
                    and state in _KNOWLEDGE_STATES
                    and source_kind in _KNOWLEDGE_SOURCE_KINDS
                    and bool(knowledge_actor)
                    and bool(knowledge_source_actor)
                    and event_evidence_in_chapter(event, chapter_text)
                )
                if valid_information:
                    information_row = information.setdefault(
                        information_id,
                        {
                            "id": information_id,
                            "canonical_claim": content,
                            "content": content,
                            "verification": event_verification,
                            "source_chapter": chapter,
                            "source_event_id": event_id,
                        },
                    )
                    if event_verification == "verified":
                        information_row["verification"] = "verified"
                        if content and str(
                            information_row.get("canonical_claim") or ""
                        ) != content:
                            information_row["canonical_claim"] = content
                            information_row["content"] = content
                    knowledge_by_entity.setdefault(subject, {})[information_id] = {
                        "event_id": event_id,
                        "sequence": int(event.get("sequence") or _sequence),
                        "entity_id": subject,
                        "information_id": information_id,
                        "content": content,
                        "evidence_fragment": evidence_fragment,
                        "state": state,
                        "source_kind": source_kind,
                        "source_entity": source_entity,
                        "verification": event_verification,
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    knowledge_audit_facts = [
                        knowledge_by_entity[subject][information_id]
                    ]
                    if str(information_row.get("source_event_id") or "") == event_id:
                        knowledge_audit_facts.insert(0, information_row)
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "knowledge_by_entity",
                        event,
                        knowledge_audit_facts,
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
                    coverage_failures.add("knowledge")
            elif event_type == "presence_observed":
                location_id = _atom(payload.get("location_id"), 180)
                presence_kind = str(payload.get("presence_kind") or "").strip().lower()
                evidence_quote = str(payload.get("evidence_quote") or "").strip()
                raw_scene_index = payload.get("scene_index")
                transition_explicit = payload.get("transition_explicit")
                try:
                    scene_index = (
                        int(raw_scene_index)
                        if raw_scene_index not in (None, "")
                        and not isinstance(raw_scene_index, bool)
                        else None
                    )
                except (TypeError, ValueError):
                    scene_index = -1
                presence_actor = (
                    _ensure_entity_namespace(entities, subject, "actor")
                    if subject
                    else None
                )
                presence_location = (
                    _ensure_entity_namespace(entities, location_id, "location")
                    if location_id
                    else None
                )
                if (
                    subject
                    and location_id
                    and bool(presence_actor)
                    and bool(presence_location)
                    and presence_kind in _PRESENCE_KINDS
                    and (scene_index is None or scene_index >= 1)
                    and (
                        transition_explicit is None
                        or isinstance(transition_explicit, bool)
                    )
                    and event_evidence_in_chapter(event, chapter_text)
                ):
                    row = {
                        "event_id": event_id,
                        "sequence": int(event.get("sequence") or _sequence),
                        "entity_id": subject,
                        "location_id": location_id,
                        "scene_index": scene_index,
                        "time_anchor": _text(payload.get("time_anchor"), max_chars=240),
                        "presence_kind": presence_kind,
                        "transition_explicit": transition_explicit,
                        "verification": event_verification,
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    presence_history.append(row)
                    if presence_kind == "physical":
                        presence[subject] = dict(row)
                    entity = presence_actor
                    assert isinstance(entity, dict)
                    entity.setdefault("first_appearance", chapter)
                    entity["first_appearance"] = min(
                        int(entity.get("first_appearance") or chapter), chapter
                    )
                    entity["last_appearance"] = max(
                        int(entity.get("last_appearance") or 0), chapter
                    )
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "presence_history",
                        event,
                        row,
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
                    coverage_failures.add("presence")
            elif event_type == "custody_changed":
                artifact_id = subject
                from_holder = _atom(payload.get("from_holder"), 180)
                to_holder = _atom(payload.get("to_holder"), 180)
                location_id = _atom(payload.get("location_id"), 180)
                evidence_quote = str(payload.get("evidence_quote") or "").strip()
                prior_holder = str(
                    (custody.get(artifact_id) or {}).get("holder_id") or ""
                )
                prior_recorded = artifact_id in custody
                custody_item = (
                    _ensure_entity_namespace(entities, artifact_id, "item")
                    if artifact_id
                    else None
                )
                custody_holders_ok = all(
                    _ensure_entity_namespace(entities, holder, "actor") is not None
                    for holder in (from_holder, to_holder)
                    if holder
                )
                custody_location_ok = (
                    _ensure_entity_namespace(entities, location_id, "location")
                    is not None
                    if location_id
                    else True
                )
                if (
                    artifact_id
                    and (from_holder or to_holder)
                    and bool(custody_item)
                    and custody_holders_ok
                    and custody_location_ok
                    and event_evidence_in_chapter(event, chapter_text)
                ):
                    transition_consistent = (
                        not prior_recorded or prior_holder == from_holder
                    )
                    row = {
                        "event_id": event_id,
                        "sequence": int(event.get("sequence") or _sequence),
                        "artifact_id": artifact_id,
                        "from_holder": from_holder,
                        "to_holder": to_holder,
                        "holder_id": to_holder,
                        "prior_holder": prior_holder,
                        "location_id": location_id,
                        "transition_consistent": transition_consistent,
                        "verification": event_verification,
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    custody_history.append(row)
                    if transition_consistent:
                        custody[artifact_id] = dict(row)
                        audit_long_term_event(
                            chapter,
                            event_id,
                            event_type,
                            "custody_history",
                            event,
                            row,
                        )
                    else:
                        result.omitted_fact_ids.append(event_id)
                        coverage_failures.add("custody")
                else:
                    result.omitted_fact_ids.append(event_id)
                    coverage_failures.add("custody")
            elif event_type == "artifact_obtained":
                artifact = _atom(
                    payload.get("artifact_id")
                    or event.get("subject")
                    or payload.get("name"),
                    180,
                )
                owner = _atom(payload.get("owner") or payload.get("holder"), 180)
                from_holder = _atom(payload.get("from_holder"), 180)
                location_id = _atom(payload.get("location_id"), 180)
                evidence_quote = str(payload.get("evidence_quote") or "").strip()
                display_name = _text(payload.get("name"), max_chars=180)
                artifact_entity = (
                    _ensure_entity_namespace(
                        entities,
                        artifact,
                        "item",
                        name=display_name or artifact,
                    )
                    if artifact
                    else None
                )
                owner_entity = (
                    _ensure_entity_namespace(entities, owner, "actor")
                    if owner
                    else None
                )
                from_entity = (
                    _ensure_entity_namespace(entities, from_holder, "actor")
                    if from_holder
                    else True
                )
                location_entity = (
                    _ensure_entity_namespace(entities, location_id, "location")
                    if location_id
                    else True
                )
                prior_custody = custody.get(artifact)
                prior_holder = str((prior_custody or {}).get("holder_id") or "")
                transition_consistent = (
                    prior_custody is None or prior_holder == from_holder
                )
                if (
                    artifact
                    and owner
                    and bool(artifact_entity)
                    and bool(owner_entity)
                    and bool(from_entity)
                    and bool(location_entity)
                    and transition_consistent
                    and event_evidence_in_chapter(event, chapter_text)
                ):
                    custody_row = {
                        "event_id": event_id,
                        "sequence": int(event.get("sequence") or _sequence),
                        "artifact_id": artifact,
                        "from_holder": from_holder,
                        "to_holder": owner,
                        "holder_id": owner,
                        "prior_holder": prior_holder,
                        "location_id": location_id,
                        "transition_consistent": transition_consistent,
                        "verification": event_verification,
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    custody_history.append(dict(custody_row))
                    custody[artifact] = dict(custody_row)
                    story_facts[event_id] = {
                        "id": event_id,
                        "category": "artifact_obtained",
                        "subject": artifact,
                        "field": "holder",
                        "value": owner,
                        "payload": {
                            "artifact": artifact,
                            "owner": owner,
                            "from_holder": from_holder,
                        },
                        "status": "active",
                        "source_chapter": chapter,
                        "source_event_id": event_id,
                        "evidence_quote": _text(
                            payload.get("evidence_quote"), max_chars=600
                        ),
                        "verification": event_verification,
                    }
                    assert isinstance(artifact_entity, dict)
                    if display_name:
                        artifact_entity["name"] = display_name
                        aliases = artifact_entity.setdefault("aliases", [])
                        if display_name != artifact and display_name not in aliases:
                            aliases.append(display_name)
                    artifact_entity["source_chapter"] = chapter
                    artifact_entity["source_event_id"] = event_id
                    audit_long_term_event(
                        chapter,
                        event_id,
                        event_type,
                        "custody_history",
                        event,
                        [custody_row, story_facts[event_id], artifact_entity],
                    )
                else:
                    result.omitted_fact_ids.append(event_id)
                    coverage_failures.add("custody")

        timeline_rows = (
            strict_records.get("timeline_events", [])
            if cutover_evidence
            else extraction_list(commit, "timeline_events")
        )
        for index, row in enumerate(timeline_rows):
            if not isinstance(row, dict):
                continue
            source_event_id = _atom(row.get("source_event_id"), 180)
            timeline_id = (
                source_event_id
                if cutover_strict and source_event_id
                else _atom(row.get("timeline_id"), 180)
                or f"timeline-{chapter}-{index}"
            )
            event_text = _text(row.get("event"))
            try:
                source_chapter = int(row.get("chapter") or chapter)
                sequence = int(row.get("sequence") or index + 1)
            except (TypeError, ValueError):
                result.omitted_fact_ids.append(timeline_id)
                continue
            if source_chapter > as_of or not event_text:
                continue
            timeline[timeline_id] = {
                "id": timeline_id,
                "category": "timeline",
                "subject": _atom(row.get("event_type"), 80),
                "field": "event",
                "value": event_text,
                "status": "active",
                "source_chapter": source_chapter,
                "source_event_id": _atom(row.get("source_event_id"), 180),
                "evidence_quote": _text(
                    row.get("evidence_quote"), max_chars=600
                ),
                "payload": {
                    "sequence": sequence,
                    "time_hint": _text(row.get("time_hint"), max_chars=240),
                    "event_type": _atom(row.get("event_type"), 80),
                },
            }
            source_event = accepted_event_by_id.get(source_event_id)
            if source_event is not None:
                audit_long_term_event(
                    chapter,
                    source_event_id,
                    str(source_event.get("event_type") or ""),
                    "timeline",
                    source_event,
                    timeline[timeline_id],
                )

    # Initialization is canon too, but it is schema-owned rather than a
    # fictional chapter.  World settings constrain all chapters; other setup
    # facts are exposed with the general canonical facts.
    for key, value in (result.initial_canon.get("world") or {}).items():
        rules[f"setup-world-{key}"] = {
            "id": f"setup-world-{key}",
            "category": "world_rule",
            "subject": "initial_world",
            "field": key,
            "value": value,
            "payload": {"scope": "global"},
            "status": "active",
            "source_chapter": 0,
        }
    for section in ("project", "protagonist", "golden_finger", "characters"):
        for key, value in (result.initial_canon.get(section) or {}).items():
            category = "character_state" if section == "protagonist" else "story_fact"
            fact_id = f"setup-{section}-{key}"
            story_facts[fact_id] = {
                "id": fact_id,
                "category": category,
                "subject": section,
                "field": key,
                "value": value,
                "status": "active",
                "source_chapter": 0,
            }

    # 规划或作者增量写回的设定只从已绑定哈希的闭合快照进入 canon。
    # 文件变化后旧快照不会继续注入，读取端同时报告 stale blocker。
    for fact in result.setting_canon.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("id") or "")
        category = str(fact.get("category") or "")
        row = {
            "id": fact_id,
            "category": category,
            "subject": str(fact.get("subject") or ""),
            "field": str(fact.get("field") or ""),
            "value": str(fact.get("value") or ""),
            "payload": {
                "source": str(fact.get("source") or ""),
                "section": str(fact.get("section") or ""),
                "line": int(fact.get("line") or 0),
            },
            "status": "active",
            "source_chapter": 0,
        }
        if category == "world_rule":
            rules[fact_id] = row
        else:
            story_facts[fact_id] = row

    hard = [*rules.values(), *loops.values(), *promises.values(), *relationships.values()]
    category_order = {"world_rule": 0, "open_loop": 1, "reader_promise": 2, "relationship": 3}
    hard.sort(
        key=lambda item: (
            category_order.get(str(item.get("category")), 99),
            int(item.get("source_chapter") or 0),
            str(item.get("id") or ""),
        )
    )
    timeline_rows = sorted(
        timeline.values(),
        key=lambda row: (
            int(row.get("source_chapter") or 0),
            int((row.get("payload") or {}).get("sequence") or 0),
            str(row.get("id") or ""),
        ),
    )
    canonical_facts = [*states.values(), *story_facts.values(), *timeline_rows]
    canonical_facts.sort(
        key=lambda item: (
            int(item.get("source_chapter") or 0),
            str(item.get("category") or ""),
            str(item.get("id") or ""),
        )
    )

    for (entity_id, field_name), state in states.items():
        entity = entities.setdefault(
            entity_id, {"id": entity_id, "name": entity_id, "type": "角色"}
        )
        attributes = entity.setdefault("attributes", {})
        attributes[field_name] = state.get("value")
    for entity in entities.values():
        entity.setdefault("tier", "核心")
        entity.setdefault("aliases", [])
        entity.setdefault("attributes", {})
        entity.setdefault("first_appearance", 0)
        entity.setdefault("last_appearance", 0)

    expected_chapters = list(range(1, as_of + 1))
    history_is_contiguous = result.valid_chapters == expected_chapters
    coverage: Dict[str, str] = {}
    verification: Dict[str, str] = {}
    for dimension in _FACT_COVERAGE_DIMENSIONS:
        coverage_values = [
            str(claim.get(dimension) or "") for claim in coverage_claims
        ]
        verification_values = [
            str(claim.get(dimension) or "") for claim in verification_claims
        ]
        if not commits:
            coverage[dimension] = "partial" if result.invalid_sources else "none"
        elif (
            not result.invalid_sources
            and history_is_contiguous
            and dimension not in coverage_failures
            and coverage_values
            and all(claim == "complete" for claim in coverage_values)
        ):
            coverage[dimension] = "complete"
        else:
            coverage[dimension] = "partial"

        if not commits:
            verification[dimension] = (
                "legacy" if result.invalid_sources else "unknown"
            )
        elif (
            dimension in legacy_dimensions
            or any(not claim for claim in coverage_values)
            or any(not claim for claim in verification_values)
        ):
            verification[dimension] = "legacy"
        elif (
            result.invalid_sources
            or not history_is_contiguous
            or dimension in coverage_failures
            or any(claim == "partial" for claim in coverage_values)
            or any(
                claim in {"pending", "unknown"}
                for claim in verification_values
            )
        ):
            verification[dimension] = "pending"
        elif coverage_values and all(
            claim == "complete" for claim in coverage_values
        ):
            # Chapter-level fact_verification is extractor output and is
            # never allowed to self-claim ``verified``.  The as-of dimension
            # lights up once every prefix chapter was extracted completely
            # and no chapter is still pending a human decision.
            verification[dimension] = "verified"
        else:
            verification[dimension] = "supported"

    result.hard_constraints = hard
    result.canonical_facts = canonical_facts
    result.entities = entities
    result.state_changes = sorted(
        state_changes,
        key=lambda row: (int(row.get("chapter") or 0), str(row.get("id") or "")),
    )
    result.rules = list(rules.values())
    result.obligations = [*loops.values(), *promises.values()]
    result.lifecycle_history = lifecycle_history
    result.timeline = timeline_rows
    result.information = dict(sorted(information.items()))
    result.knowledge_by_entity = {
        entity_id: dict(sorted(facts.items()))
        for entity_id, facts in sorted(knowledge_by_entity.items())
    }
    result.presence = dict(sorted(presence.items()))
    result.presence_history = sorted(
        presence_history,
        key=lambda row: (
            int(row.get("source_chapter") or 0),
            int(row.get("sequence") or 0),
            str(row.get("event_id") or ""),
        ),
    )
    result.custody = dict(sorted(custody.items()))
    result.custody_history = sorted(
        custody_history,
        key=lambda row: (
            int(row.get("source_chapter") or 0),
            int(row.get("sequence") or 0),
            str(row.get("event_id") or ""),
        ),
    )
    result.coverage = coverage
    result.verification = verification
    result.invalid_sources = list(dict.fromkeys(result.invalid_sources))
    result.omitted_fact_ids = sorted(set(result.omitted_fact_ids))
    return result


def latest_canonical_chapter(project_root: Path) -> int:
    """Return the highest trusted canonical chapter without creating files."""
    root = Path(project_root)
    if (root / ".story-system" / "v3" / "CURRENT").is_file():
        history = load_canonical_history(root, 2**31 - 1)
        return max(history.valid_chapters, default=0)
    commits, _invalid = _trusted_commits(root, 2**31 - 1)
    return max((int(row["meta"]["chapter"]) for row in commits), default=0)


ASOF_SNAPSHOT_SCHEMA = "canon-ledger-asof-snapshot/v3"


def history_to_asof_snapshot(
    history: CanonicalHistory,
    *,
    chapter: int,
) -> Dict[str, Any]:
    """Serialize an immutable as-of view for reviewer / data-agent."""
    alias_index: Dict[str, List[str]] = {}
    for entity in history.entities.values():
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("id") or "").strip()
        names = [entity.get("name"), *(entity.get("aliases") or [])]
        for name in names:
            key = str(name or "").strip()
            if not key or not entity_id:
                continue
            bucket = alias_index.setdefault(key, [])
            if entity_id not in bucket:
                bucket.append(entity_id)
    return {
        "schema_version": ASOF_SNAPSHOT_SCHEMA,
        "chapter": int(chapter),
        "as_of_chapter": int(history.as_of_chapter),
        "initial_canon": history.initial_canon,
        "setting_canon": history.setting_canon,
        "valid_chapters": list(history.valid_chapters),
        "invalid_sources": list(history.invalid_sources),
        "omitted_fact_ids": list(history.omitted_fact_ids),
        "entities": history.entities,
        "alias_index": alias_index,
        "state_changes": history.state_changes,
        "rules": history.rules,
        "obligations": history.obligations,
        "lifecycle_history": history.lifecycle_history,
        "timeline": history.timeline,
        "information": history.information,
        "knowledge_by_entity": history.knowledge_by_entity,
        "presence": history.presence,
        "presence_history": history.presence_history,
        "custody": history.custody,
        "custody_history": history.custody_history,
        "long_term_event_audit": history.long_term_event_audit,
        "coverage": history.coverage,
        "verification": history.verification,
        "canonical_facts": history.canonical_facts,
        "hard_constraints": history.hard_constraints,
    }


def export_asof_snapshot(
    project_root: str | Path,
    *,
    chapter: int | None = None,
    as_of_chapter: int | None = None,
) -> Dict[str, Any]:
    """Export canonical history at N-1 for reviewing or extracting chapter N."""
    if as_of_chapter is None:
        if chapter is None:
            raise ValueError("chapter_or_as_of_required")
        target = int(chapter)
        as_of = max(0, target - 1)
    else:
        as_of = max(0, int(as_of_chapter))
        target = int(chapter) if chapter is not None else as_of + 1
    history = load_canonical_history(Path(project_root), as_of)
    return history_to_asof_snapshot(history, chapter=target)

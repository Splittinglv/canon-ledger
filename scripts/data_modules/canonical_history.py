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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .chapter_content_binding import verify_commit_content_binding
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
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    information: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    knowledge_by_entity: Dict[str, Dict[str, Dict[str, Any]]] = field(
        default_factory=dict
    )
    presence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    presence_history: List[Dict[str, Any]] = field(default_factory=list)
    custody: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    custody_history: List[Dict[str, Any]] = field(default_factory=list)
    coverage: Dict[str, str] = field(default_factory=dict)


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
        trusted, code = verify_commit_content_binding(project_root, chapter, payload)
        if not trusted:
            invalid.append(f"chapter_commit:{chapter}:{code}")
            continue
        commits.append(payload)
    return commits, invalid


def load_canonical_history(project_root: Path, as_of_chapter: int) -> CanonicalHistory:
    """Return all consistency facts visible at ``as_of_chapter``.

    No fact from a later chapter, rejected commit, or commit whose bound prose
    has changed can enter the result.
    """
    project_root = Path(project_root).expanduser().resolve()
    as_of = max(0, int(as_of_chapter or 0))
    result = CanonicalHistory(as_of_chapter=as_of)

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
    coverage_failures: set[str] = set()

    def remember_state(
        *, chapter: int, entity_id: Any, field_name: Any, value: Any,
        source_id: str, old_value: Any = None, reason: Any = "",
    ) -> None:
        entity = _atom(entity_id)
        field_name_clean = _atom(field_name)
        safe_value, rejected = _safe_value(value)
        if not entity or not field_name_clean or rejected:
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
            }
        )
        entities.setdefault(entity, {"id": entity, "name": entity, "type": "角色"})

    for commit in commits:
        chapter = int(commit["meta"]["chapter"])
        chapter_text = bound_chapter_text_for_commit(project_root, commit) or ""
        coverage_claims.append(extraction_dict(commit, "fact_coverage"))

        # Entity appearances are useful identity evidence, but they never
        # imply physical presence. Only presence_observed may update location.
        for appeared in extraction_list(commit, "entities_appeared"):
            if not isinstance(appeared, dict):
                continue
            entity_id = _atom(appeared.get("id") or appeared.get("entity_id"))
            if not entity_id:
                continue
            current = entities.setdefault(
                entity_id,
                {"id": entity_id, "name": entity_id, "type": "角色"},
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
            current.setdefault("first_appearance", chapter)
            current["first_appearance"] = min(
                int(current.get("first_appearance") or chapter), chapter
            )
            current["last_appearance"] = max(
                int(current.get("last_appearance") or 0), chapter
            )

        for scene in extraction_list(commit, "scenes"):
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

        for index, delta in enumerate(extraction_list(commit, "entity_deltas")):
            if not isinstance(delta, dict):
                continue
            from_entity = _atom(delta.get("from_entity") or delta.get("from"))
            to_entity = _atom(delta.get("to_entity") or delta.get("to"))
            if from_entity and to_entity:
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
                {"id": entity_id, "name": entity_id, "type": "角色"},
            )
            name = _text(delta.get("canonical_name") or delta.get("name"), 240)
            entity_type = _atom(delta.get("entity_type") or delta.get("type"), 80)
            tier = _atom(delta.get("tier"), 80)
            if name:
                current["name"] = name
            if entity_type:
                current["type"] = entity_type
            if tier:
                current["tier"] = tier
            current.setdefault("first_appearance", chapter)
            current["first_appearance"] = min(
                int(current.get("first_appearance") or chapter), chapter
            )
            current["last_appearance"] = max(
                int(current.get("last_appearance") or 0), chapter
            )

        for index, delta in enumerate(extraction_list(commit, "state_deltas")):
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

        for index, event in enumerate(extraction_list(commit, "accepted_events")):
            if not isinstance(event, dict):
                continue
            event_id = _atom(event.get("event_id"), 180) or f"event-{chapter}-{index}"
            event_type = str(event.get("event_type") or "").strip()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            subject = _atom(event.get("subject"))

            if event_type in {"character_state_changed", "power_breakthrough"}:
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
                )
            elif event_type == "relationship_changed":
                from_entity = _atom(payload.get("from_entity") or subject)
                to_entity = _atom(payload.get("to_entity") or payload.get("to"))
                relation = _atom(
                    _first(payload, "relationship_type", "relation_type", "type")
                )
                if from_entity and to_entity and relation:
                    relationships[(from_entity, to_entity)] = {
                        "id": event_id,
                        "category": "relationship",
                        "subject": from_entity,
                        "field": to_entity,
                        "value": relation,
                        "payload": {},
                        "status": "active",
                        "source_chapter": chapter,
                    }
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
                    }
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
                    }
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
                if loop_id:
                    loops.pop(loop_id, None)
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
                    }
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
                if promise_id:
                    promises.pop(promise_id, None)
            elif event_type == "knowledge_state_changed":
                information_id = _atom(payload.get("information_id"), 180)
                raw_content = payload.get("content")
                content = _text(raw_content, max_chars=600)
                evidence_quote = str(payload.get("evidence_quote") or "").strip()
                state = str(payload.get("state") or "").strip().lower()
                source_kind = str(payload.get("source_kind") or "").strip().lower()
                source_entity = _atom(payload.get("source_entity"), 180)
                existing_information = information.get(information_id)
                valid_information = (
                    bool(subject)
                    and bool(information_id)
                    and bool(content)
                    and isinstance(raw_content, str)
                    and raw_content.strip() in evidence_quote
                    and state in _KNOWLEDGE_STATES
                    and source_kind in _KNOWLEDGE_SOURCE_KINDS
                    and event_evidence_in_chapter(event, chapter_text)
                    and (
                        existing_information is None
                        or existing_information.get("content") == content
                    )
                )
                if valid_information:
                    information.setdefault(
                        information_id,
                        {
                            "id": information_id,
                            "content": content,
                            "source_chapter": chapter,
                            "source_event_id": event_id,
                        },
                    )
                    knowledge_by_entity.setdefault(subject, {})[information_id] = {
                        "event_id": event_id,
                        "entity_id": subject,
                        "information_id": information_id,
                        "content": content,
                        "state": state,
                        "source_kind": source_kind,
                        "source_entity": source_entity,
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    entities.setdefault(
                        subject,
                        {"id": subject, "name": subject, "type": "角色"},
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
                if (
                    subject
                    and location_id
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
                        "entity_id": subject,
                        "location_id": location_id,
                        "scene_index": scene_index,
                        "time_anchor": _text(payload.get("time_anchor"), max_chars=240),
                        "presence_kind": presence_kind,
                        "transition_explicit": transition_explicit,
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    presence_history.append(row)
                    if presence_kind == "physical":
                        presence[subject] = dict(row)
                    entity = entities.setdefault(
                        subject,
                        {"id": subject, "name": subject, "type": "角色"},
                    )
                    entity.setdefault("first_appearance", chapter)
                    entity["first_appearance"] = min(
                        int(entity.get("first_appearance") or chapter), chapter
                    )
                    entity["last_appearance"] = max(
                        int(entity.get("last_appearance") or 0), chapter
                    )
                    entities.setdefault(
                        location_id,
                        {"id": location_id, "name": location_id, "type": "地点"},
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
                if (
                    artifact_id
                    and (from_holder or to_holder)
                    and event_evidence_in_chapter(event, chapter_text)
                ):
                    row = {
                        "event_id": event_id,
                        "artifact_id": artifact_id,
                        "from_holder": from_holder,
                        "to_holder": to_holder,
                        "holder_id": to_holder,
                        "prior_holder": prior_holder,
                        "location_id": location_id,
                        "transition_consistent": (
                            not prior_holder
                            or not from_holder
                            or prior_holder == from_holder
                        ),
                        "evidence_quote": evidence_quote,
                        "source_chapter": chapter,
                    }
                    custody_history.append(row)
                    custody[artifact_id] = dict(row)
                    entities.setdefault(
                        artifact_id,
                        {"id": artifact_id, "name": artifact_id, "type": "物品"},
                    )
                    for holder in (from_holder, to_holder):
                        if holder:
                            entities.setdefault(
                                holder,
                                {"id": holder, "name": holder, "type": "角色"},
                            )
                else:
                    result.omitted_fact_ids.append(event_id)
                    coverage_failures.add("custody")
            elif event_type == "artifact_obtained":
                artifact = _text(
                    payload.get("name")
                    or payload.get("artifact_id")
                    or event.get("subject")
                )
                owner = _text(payload.get("owner") or payload.get("holder"))
                value = f"{owner}获得{artifact}" if owner else artifact
                if value:
                    story_facts[event_id] = {
                        "id": event_id,
                        "category": "story_fact",
                        "subject": _atom(owner) or subject,
                        "field": "artifact_obtained",
                        "value": value,
                        "status": "active",
                        "source_chapter": chapter,
                    }

        for index, row in enumerate(extraction_list(commit, "timeline_events")):
            if not isinstance(row, dict):
                continue
            timeline_id = _atom(row.get("timeline_id"), 180) or f"timeline-{chapter}-{index}"
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
                "payload": {
                    "sequence": sequence,
                    "time_hint": _text(row.get("time_hint"), max_chars=240),
                    "event_type": _atom(row.get("event_type"), 80),
                },
            }

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
    for dimension in _FACT_COVERAGE_DIMENSIONS:
        if not commits:
            coverage[dimension] = "partial" if result.invalid_sources else "none"
        elif (
            not result.invalid_sources
            and history_is_contiguous
            and dimension not in coverage_failures
            and all(claim.get(dimension) == "complete" for claim in coverage_claims)
        ):
            coverage[dimension] = "complete"
        else:
            coverage[dimension] = "partial"

    result.hard_constraints = hard
    result.canonical_facts = canonical_facts
    result.entities = entities
    result.state_changes = sorted(
        state_changes,
        key=lambda row: (int(row.get("chapter") or 0), str(row.get("id") or "")),
    )
    result.rules = list(rules.values())
    result.obligations = [*loops.values(), *promises.values()]
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
            int(row.get("scene_index") or 0),
            str(row.get("event_id") or ""),
        ),
    )
    result.custody = dict(sorted(custody.items()))
    result.custody_history = sorted(
        custody_history,
        key=lambda row: (
            int(row.get("source_chapter") or 0),
            str(row.get("event_id") or ""),
        ),
    )
    result.coverage = coverage
    result.invalid_sources = list(dict.fromkeys(result.invalid_sources))
    result.omitted_fact_ids = sorted(set(result.omitted_fact_ids))
    return result


def latest_canonical_chapter(project_root: Path) -> int:
    """Return the highest trusted canonical chapter without creating files."""
    commits, _invalid = _trusted_commits(Path(project_root), 2**31 - 1)
    return max((int(row["meta"]["chapter"]) for row in commits), default=0)


ASOF_SNAPSHOT_SCHEMA = "canon-ledger-asof-snapshot/v2"


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
        "valid_chapters": list(history.valid_chapters),
        "invalid_sources": list(history.invalid_sources),
        "omitted_fact_ids": list(history.omitted_fact_ids),
        "entities": history.entities,
        "alias_index": alias_index,
        "state_changes": history.state_changes,
        "rules": history.rules,
        "obligations": history.obligations,
        "timeline": history.timeline,
        "information": history.information,
        "knowledge_by_entity": history.knowledge_by_entity,
        "presence": history.presence,
        "presence_history": history.presence_history,
        "custody": history.custody,
        "custody_history": history.custody_history,
        "coverage": history.coverage,
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

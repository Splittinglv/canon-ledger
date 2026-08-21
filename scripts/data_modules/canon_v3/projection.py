#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Disposable Canon v3 read model bound to one exact CURRENT generation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json

from .repository import (
    CanonIntegrityError,
    CanonV3Repository,
    ProjectionBinding,
    ProjectionStaleError,
    content_hash,
)


PROJECTION_SCHEMA = "canon-v3/canon-projection/v1"
PROJECTION_RELATIVE_PATH = Path(".story-system/v3/projections/canon.json")


class CanonProjectionError(RuntimeError):
    pass


def _decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_pointer(document: Any, pointer: str) -> Any:
    current = document
    for raw in pointer.lstrip("/").split("/") if pointer else ():
        token = _decode_pointer_token(raw)
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _remove_pointer(document: Any, pointer: str) -> None:
    parts = pointer.lstrip("/").split("/") if pointer else []
    if not parts:
        raise CanonIntegrityError("canon_v3_cannot_remove_projection_root")
    parent = document
    for raw in parts[:-1]:
        token = _decode_pointer_token(raw)
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    leaf = _decode_pointer_token(parts[-1])
    if isinstance(parent, list):
        parent.pop(int(leaf))
    else:
        parent.pop(leaf, None)


def _apply_genesis_axiom_supersessions(
    legacy_base: dict[str, Any],
    superseded: set[str],
) -> dict[str, Any]:
    if not superseded:
        return legacy_base
    result = copy.deepcopy(legacy_base)
    admissions = result.get("cutover_fact_admissions") or []
    selected = [
        item
        for item in admissions
        if isinstance(item, dict)
        and str(item.get("admission_digest") or "") in superseded
    ]
    if {str(item.get("admission_digest") or "") for item in selected} != superseded:
        raise CanonIntegrityError(
            "canon_v3_superseded_genesis_admission_missing"
        )
    removals: list[str] = []
    facts: list[dict[str, Any]] = []
    for admission in selected:
        matched_fact: dict[str, Any] | None = None
        for location in admission.get("locations") or []:
            pointer = str(location)
            try:
                candidate = _resolve_pointer(result, pointer)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise CanonIntegrityError(
                    "canon_v3_superseded_genesis_location_invalid"
                ) from exc
            if (
                isinstance(candidate, dict)
                and content_hash(candidate)
                == str(admission.get("fact_content_sha256") or "")
            ):
                matched_fact = copy.deepcopy(candidate)
            removals.append(pointer)
        if matched_fact is None:
            raise CanonIntegrityError(
                "canon_v3_superseded_genesis_fact_mismatch"
            )
        facts.append(matched_fact)
    # Remove higher list indexes first so paths remain exact.
    for pointer in sorted(
        set(removals),
        key=lambda value: (
            value.rsplit("/", 1)[0],
            -int(value.rsplit("/", 1)[1])
            if value.rsplit("/", 1)[1].isdigit()
            else 0,
        ),
    ):
        _remove_pointer(result, pointer)
    result["cutover_fact_admissions"] = [
        item
        for item in admissions
        if not isinstance(item, dict)
        or str(item.get("admission_digest") or "") not in superseded
    ]
    result["superseded_cutover_fact_admissions"] = selected
    initial = result.get("initial_canon")
    if isinstance(initial, dict):
        for fact in facts:
            subject = str(fact.get("subject") or "")
            section = "world" if subject == "initial_world" else subject
            field = str(fact.get("field") or "")
            target = initial.get(section)
            if isinstance(target, dict) and field:
                target.pop(field, None)
                if not target:
                    initial.pop(section, None)
    return result


def projection_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / PROJECTION_RELATIVE_PATH


def _projection_from_head(
    repository: CanonV3Repository,
    head_hash: str,
) -> dict[str, Any]:
    manifest = repository.read_manifest(head_hash, validate_references=True)
    axiom_entries = repository._author_axiom_manifest_entries(manifest)
    active_axiom_commit_hash: str | None = None
    active_axiom_commit: dict[str, Any] | None = None
    superseded_genesis_admissions: set[str] = set()
    if axiom_entries:
        active_axiom_commit_hash = str(
            axiom_entries[-1].get("commit_hash") or ""
        )
        active_axiom_commit = repository.read_author_axiom_commit(
            active_axiom_commit_hash
        )
        superseded_genesis_admissions = {
            str(item)
            for item in active_axiom_commit.get(
                "superseded_legacy_admission_digests"
            )
            or ()
        }
    cursor_hash = head_hash
    cursor = manifest
    seen: set[str] = set()
    while int(cursor.get("generation") or 0) > 0:
        if cursor_hash in seen:
            raise CanonIntegrityError("canon_v3_manifest_parent_cycle")
        seen.add(cursor_hash)
        parent = cursor.get("parent_head_hash")
        if not isinstance(parent, str) or not parent:
            raise CanonIntegrityError("canon_v3_manifest_missing_parent")
        cursor_hash = parent
        cursor = repository.read_manifest(parent, validate_references=True)
    metadata = cursor.get("genesis_metadata")
    if not isinstance(metadata, dict):
        raise CanonIntegrityError("canon_v3_genesis_metadata_invalid")
    genesis_schema = str(metadata.get("schema_version") or "")
    recertified_genesis = genesis_schema == "canon-v3/legacy-genesis/v2"
    legacy_snapshot = metadata.get("legacy_snapshot")
    if legacy_snapshot is None:
        legacy_base: dict[str, Any] = {}
    elif not isinstance(legacy_snapshot, dict):
        raise CanonIntegrityError("canon_v3_legacy_snapshot_invalid")
    else:
        expected_snapshot_hash = str(metadata.get("legacy_snapshot_sha256") or "")
        if expected_snapshot_hash and content_hash(legacy_snapshot) != expected_snapshot_hash:
            raise CanonIntegrityError("canon_v3_legacy_snapshot_hash_mismatch")
        raw_facts = legacy_snapshot.get("facts")
        if not isinstance(raw_facts, dict):
            raise CanonIntegrityError("canon_v3_legacy_snapshot_facts_invalid")
        if recertified_genesis and legacy_snapshot.get("schema_version") != (
            "canon-v3/legacy-fact-snapshot/v2"
        ):
            raise CanonIntegrityError("canon_v3_legacy_snapshot_schema_invalid")
        legacy_base = _apply_genesis_axiom_supersessions(
            copy.deepcopy(raw_facts), superseded_genesis_admissions
        )
    legacy_fact_records: list[dict[str, Any]] = []
    snapshot_digest = str(metadata.get("legacy_snapshot_sha256") or "")
    admission_index: dict[str, dict[str, Any]] = {}
    if recertified_genesis:
        raw_admissions = legacy_base.get("cutover_fact_admissions")
        if not isinstance(raw_admissions, list):
            raise CanonIntegrityError("canon_v3_legacy_fact_admissions_missing")
        for raw_admission in raw_admissions:
            if not isinstance(raw_admission, dict):
                raise CanonIntegrityError("canon_v3_legacy_fact_admission_invalid")
            if raw_admission.get("schema_version") != (
                "canon-v3/legacy-fact-admission/v2"
            ):
                raise CanonIntegrityError("canon_v3_legacy_fact_admission_schema_invalid")
            digest = str(raw_admission.get("fact_content_sha256") or "")
            admission_digest = str(raw_admission.get("admission_digest") or "")
            unsigned = {
                key: value
                for key, value in raw_admission.items()
                if key != "admission_digest"
            }
            if (
                not digest
                or not admission_digest
                or content_hash(unsigned) != admission_digest
                or digest in admission_index
            ):
                raise CanonIntegrityError("canon_v3_legacy_fact_admission_digest_invalid")
            admission_index[digest] = copy.deepcopy(raw_admission)
    grouped_legacy: dict[str, dict[str, Any]] = {}
    initial_canon = legacy_base.get("initial_canon")
    protagonist = (
        initial_canon.get("protagonist")
        if isinstance(initial_canon, dict)
        and isinstance(initial_canon.get("protagonist"), dict)
        else {}
    )
    protagonist_name = str(protagonist.get("name") or "").strip()

    def remember_legacy_fact(channel: str, path: str, row: Any) -> None:
        if not isinstance(row, dict):
            raise CanonIntegrityError(
                f"canon_v3_legacy_{channel}_fact_not_mapping"
            )
        if recertified_genesis and not str(row.get("slot_id") or "").strip():
            raise CanonIntegrityError(
                f"canon_v3_legacy_{channel}_stable_slot_missing"
            )
        if str(row.get("category") or "") == "world_rule" and not row.get(
            "slot_id"
        ):
            row["slot_id"] = content_hash(
                {
                    "kind_family": "world_rule",
                    "legacy_id": str(row.get("id") or ""),
                    "subject": str(row.get("subject") or ""),
                    "field": str(row.get("field") or ""),
                }
            )
        category = str(row.get("category") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if (
            protagonist_name
            and category in {"character_state", "character_state_changed", "power_breakthrough"}
            and str(row.get("subject") or payload.get("subject") or "")
            == "protagonist"
        ):
            row["subject"] = protagonist_name
        if category == "power_breakthrough" and not (
            row.get("field") or payload.get("system")
        ):
            row["field"] = "realm"
        if category in {
            "character_state",
            "character_state_changed",
            "power_breakthrough",
        } and not row.get("slot_id"):
            state_subject = str(
                row.get("subject") or payload.get("subject") or ""
            ).strip()
            state_field = str(
                payload.get("attribute")
                or payload.get("system")
                or row.get("field")
                or ("realm" if category == "power_breakthrough" else "")
            ).strip()
            if not state_subject or not state_field:
                raise CanonIntegrityError(
                    "canon_v3_legacy_character_state_stable_identity_missing"
                )
            row["slot_id"] = content_hash(
                {
                    "kind_family": "character_state",
                    "subject": state_subject,
                    "field": state_field,
                }
            )
        if category in {"open_loop", "open_loop_created", "open_loop_closed"}:
            family = "open_loop"
        elif category in {
            "reader_promise",
            "promise_created",
            "promise_paid",
            "promise_paid_off",
        }:
            family = "promise"
        else:
            family = ""
        if family and not row.get("slot_id"):
            legacy_id = str(
                payload.get("lifecycle_id")
                or payload.get("loop_id")
                or payload.get("promise_id")
                or row.get("lifecycle_id")
                or row.get("id")
                or ""
            ).strip()
            semantic_text = str(
                payload.get("loop")
                or payload.get("promise")
                or payload.get("content")
                or payload.get("description")
                or row.get("value")
                or row.get("subject")
                or ""
            ).strip()
            if not legacy_id and not semantic_text:
                raise CanonIntegrityError(
                    f"canon_v3_legacy_{family}_stable_identity_missing"
                )
            row["slot_id"] = content_hash(
                {
                    "kind_family": family,
                    "legacy_id": legacy_id,
                    "semantic_text": "" if legacy_id else semantic_text,
                }
            )
        if category in {"timeline", "timeline_observed"} and not row.get(
            "slot_id"
        ):
            legacy_id = str(
                payload.get("timeline_id")
                or payload.get("event_id")
                or row.get("id")
                or ""
            ).strip()
            event_text = str(
                payload.get("event")
                or row.get("subject")
                or row.get("value")
                or ""
            ).strip()
            if not legacy_id and not event_text:
                raise CanonIntegrityError(
                    "canon_v3_legacy_timeline_stable_identity_missing"
                )
            row["slot_id"] = content_hash(
                {
                    "kind_family": "timeline",
                    "legacy_id": legacy_id,
                    "event": "" if legacy_id else event_text,
                }
            )
        unsigned = {key: value for key, value in row.items() if key != "fact_digest"}
        identity = content_hash(unsigned)
        admission = admission_index.get(identity) if recertified_genesis else None
        if recertified_genesis and admission is None:
            raise CanonIntegrityError(
                f"canon_v3_legacy_{channel}_fact_without_admission"
            )
        group = grouped_legacy.setdefault(
            identity,
            {
                "fact": copy.deepcopy(unsigned),
                "locations": [],
                "refs": [],
                "admission_digest": (
                    str(admission.get("admission_digest") or "")
                    if admission is not None
                    else ""
                ),
            },
        )
        if group["fact"] != unsigned:
            raise CanonIntegrityError("canon_v3_legacy_fact_identity_collision")
        group["locations"].append({"channel": channel, "path": path})
        group["refs"].append(row)

    for channel in (
        "canonical_facts",
        "hard_constraints",
        "rules",
        "obligations",
        "lifecycle_history",
        "state_changes",
        "timeline",
        "presence_history",
        "custody_history",
    ):
        rows = legacy_base.get(channel) or []
        if not isinstance(rows, list):
            raise CanonIntegrityError(f"canon_v3_legacy_{channel}_not_list")
        for index, row in enumerate(rows):
            remember_legacy_fact(channel, f"/{channel}/{index}", row)
    for channel in ("information", "presence", "custody"):
        rows = legacy_base.get(channel) or {}
        if not isinstance(rows, dict):
            raise CanonIntegrityError(f"canon_v3_legacy_{channel}_not_mapping")
        for key, row in sorted(rows.items()):
            if not isinstance(row, dict):
                raise CanonIntegrityError(f"canon_v3_legacy_{channel}_fact_not_mapping")
            if channel == "presence":
                row.setdefault("category", "presence")
                row.setdefault(
                    "subject",
                    str(row.get("entity_id") or row.get("subject") or key),
                )
            elif channel == "custody":
                row.setdefault("category", "custody")
                row.setdefault(
                    "subject",
                    str(
                        row.get("artifact_id")
                        or row.get("item")
                        or row.get("subject")
                        or key
                    ),
                )
            remember_legacy_fact(channel, f"/{channel}/{key}", row)
    knowledge = legacy_base.get("knowledge_by_entity") or {}
    if not isinstance(knowledge, dict):
        raise CanonIntegrityError("canon_v3_legacy_knowledge_not_mapping")
    for entity_key, facts_by_key in sorted(knowledge.items()):
        if not isinstance(facts_by_key, dict):
            raise CanonIntegrityError("canon_v3_legacy_entity_knowledge_not_mapping")
        for fact_key, row in sorted(facts_by_key.items()):
            if not isinstance(row, dict):
                raise CanonIntegrityError(
                    "canon_v3_legacy_entity_knowledge_fact_not_mapping"
                )
            if recertified_genesis and not str(row.get("slot_id") or "").strip():
                raise CanonIntegrityError(
                    "canon_v3_legacy_knowledge_stable_slot_missing"
                )
            # v2 already had the right stable identity (information_id / map
            # key); retain it as compiler metadata instead of using mutable
            # proposition wording as the v3 fact key.
            row.setdefault("category", "knowledge")
            row.setdefault("subject", str(entity_key))
            row.setdefault("information_id", str(fact_key))
            row.setdefault(
                "slot_id",
                content_hash(
                    {
                        "kind_family": "knowledge",
                        "subject": str(entity_key),
                        "legacy_information_id": str(fact_key),
                    }
                ),
            )
            remember_legacy_fact(
                "knowledge_by_entity",
                f"/knowledge_by_entity/{entity_key}/{fact_key}",
                row,
            )
    for identity in sorted(grouped_legacy):
        group = grouped_legacy[identity]
        record = {
            "record_type": "legacy_fact",
            "legacy_snapshot_sha256": snapshot_digest,
            "fact_content_sha256": identity,
            "locations": sorted(
                group["locations"],
                key=lambda item: (item["channel"], item["path"]),
            ),
            "fact": group["fact"],
        }
        if recertified_genesis:
            record["admission_digest"] = str(
                group.get("admission_digest") or ""
            )
        record["fact_digest"] = content_hash(record)
        legacy_fact_records.append(record)
        for row in group["refs"]:
            row["fact_digest"] = record["fact_digest"]
    legacy_entities = legacy_base.get("entities") or {}
    if not isinstance(legacy_entities, dict):
        raise CanonIntegrityError("canon_v3_legacy_entities_not_mapping")
    for entity_key, raw_entity in sorted(legacy_entities.items()):
        if not isinstance(raw_entity, dict):
            raise CanonIntegrityError("canon_v3_legacy_entity_not_mapping")
        entity_content_digest = content_hash(raw_entity)
        entity_admission = (
            admission_index.get(entity_content_digest)
            if recertified_genesis
            else None
        )
        if recertified_genesis and entity_admission is None:
            raise CanonIntegrityError(
                "canon_v3_legacy_identity_without_admission"
            )
        identity_record = {
            "record_type": "legacy_identity",
            "legacy_snapshot_sha256": snapshot_digest,
            "entity_key": str(entity_key),
            "entity": copy.deepcopy(raw_entity),
        }
        if recertified_genesis:
            identity_record["fact_content_sha256"] = entity_content_digest
            identity_record["admission_digest"] = str(
                entity_admission.get("admission_digest") or ""
            )
        identity_record["fact_digest"] = content_hash(identity_record)
        legacy_fact_records.append(identity_record)
    facts: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    available_fact_records: dict[str, dict[str, Any]] = {
        str(record["fact_digest"]): record
        for record in legacy_fact_records
        if record.get("record_type") == "legacy_fact"
    }
    chapter_ledger: list[dict[str, Any]] = []
    for entry in manifest.get("chapters") or []:
        commit_hash = str(entry.get("commit_hash") or "")
        commit = repository.read_commit(commit_hash)
        chapter = int(commit.get("chapter") or 0)
        revision = int(commit.get("revision") or 0)
        chapter_ledger.append(
            {
                "chapter": chapter,
                "revision": revision,
                "commit_hash": commit_hash,
                "transaction_hash": str(commit.get("transaction_hash") or ""),
            }
        )
        commit_effects_by_id: dict[str, dict[str, Any]] = {}
        for effect_index, raw_effect in enumerate(commit.get("canon_effects") or []):
            if not isinstance(raw_effect, dict):
                raise CanonIntegrityError("canon_v3_projection_effect_not_mapping")
            effect = copy.deepcopy(raw_effect)
            fact_key = str(effect.get("fact_key") or "").strip()
            claim = effect.get("claim")
            if not fact_key or not isinstance(claim, dict):
                raise CanonIntegrityError("canon_v3_projection_effect_invalid")
            prior_fact_digest = str(effect.get("prior_fact_digest") or "")
            if prior_fact_digest:
                prior_record = available_fact_records.get(prior_fact_digest)
                if prior_record is None:
                    raise CanonIntegrityError(
                        "canon_v3_effect_prior_fact_not_in_parent_prefix"
                    )
                if (
                    prior_record.get("record_type") == "v3_effect"
                    and int(prior_record.get("chapter") or 0) >= chapter
                ):
                    raise CanonIntegrityError(
                        "canon_v3_effect_prior_fact_not_n_minus_one"
                    )
            prior_effect_id = str(effect.get("prior_effect_id") or "")
            if prior_effect_id:
                prior_effect = commit_effects_by_id.get(prior_effect_id)
                if prior_effect is None:
                    raise CanonIntegrityError(
                        "canon_v3_effect_prior_effect_not_earlier_in_chapter"
                    )
                if str(prior_effect.get("fact_key") or "") != fact_key:
                    raise CanonIntegrityError(
                        "canon_v3_effect_prior_effect_slot_mismatch"
                    )
            record = {
                "record_type": "v3_effect",
                "fact_key": fact_key,
                "claim": copy.deepcopy(claim),
                "effect_id": str(effect.get("effect_id") or ""),
                "candidate_digest": str(effect.get("candidate_digest") or ""),
                "prior_fact_digest": (
                    str(effect.get("prior_fact_digest"))
                    if effect.get("prior_fact_digest")
                    else None
                ),
                "prior_effect_id": (
                    str(effect.get("prior_effect_id"))
                    if effect.get("prior_effect_id")
                    else None
                ),
                "inherited_fields": copy.deepcopy(
                    effect.get("inherited_fields") or {}
                ),
                "source_digests": list(effect.get("source_digests") or []),
                "support_map": copy.deepcopy(effect.get("support_map") or {}),
                "chapter": chapter,
                "revision": revision,
                "commit_hash": commit_hash,
                "effect_index": effect_index,
            }
            record["fact_digest"] = content_hash(record)
            history.append(record)
            available_fact_records[record["fact_digest"]] = record
            commit_effects_by_id[str(record.get("effect_id") or "")] = record
            # Canon effects are deterministic set operations on a semantic
            # fact slot.  Rebuilding the whole active manifest means an old
            # revision or truncated suffix can never leave residual state.
            facts[fact_key] = record
    author_axioms: dict[str, Any] = {
        "commit_hash": None,
        "axiom_set_digest": None,
        "records": [],
        "superseded_genesis_admission_digests": [],
    }
    if active_axiom_commit is not None and active_axiom_commit_hash is not None:
        author_axioms = {
            "commit_hash": active_axiom_commit_hash,
            "axiom_set_digest": str(
                active_axiom_commit.get("axiom_set_digest") or ""
            ),
            "records": copy.deepcopy(active_axiom_commit.get("records") or []),
            "superseded_genesis_admission_digests": sorted(
                superseded_genesis_admissions
            ),
        }
    return {
        "schema_version": PROJECTION_SCHEMA,
        "binding": {
            "schema_version": "canon-v3/projection-binding/v1",
            "generation": int(manifest.get("generation") or 0),
            "head_hash": head_hash,
        },
        "legacy_base": legacy_base,
        "legacy_fact_records": legacy_fact_records,
        "chapters": chapter_ledger,
        "author_axioms": author_axioms,
        "facts": [facts[key] for key in sorted(facts)],
        "history": history,
    }


def fact_record_index(
    repository: CanonV3Repository,
    head_hash: str,
) -> dict[str, dict[str, Any]]:
    """Return exact, digest-addressed prior facts for one immutable HEAD."""

    payload = _projection_from_head(repository, head_hash)
    rows = [
        *(payload.get("legacy_fact_records") or []),
        *(payload.get("history") or []),
    ]
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise CanonIntegrityError("canon_v3_fact_record_not_mapping")
        digest = str(row.get("fact_digest") or "")
        if not digest:
            raise CanonIntegrityError("canon_v3_fact_record_missing_digest")
        unsigned = {key: value for key, value in row.items() if key != "fact_digest"}
        if content_hash(unsigned) != digest:
            raise CanonIntegrityError("canon_v3_fact_record_digest_mismatch")
        if digest in result and result[digest] != row:
            raise CanonIntegrityError("canon_v3_fact_record_digest_collision")
        result[digest] = copy.deepcopy(row)
    return result


def rebuild_projection(project_root: str | Path) -> dict[str, Any]:
    """Rebuild from CURRENT while holding the same publication lock."""

    repository = CanonV3Repository(project_root)
    with repository.locked():
        head = repository.current_head(validate=True)
        if head is None:
            raise CanonProjectionError("canon_v3_not_initialized")
        payload = _projection_from_head(repository, head)
        atomic_write_json(
            projection_path(project_root),
            payload,
            use_lock=False,
            backup=False,
        )
    return payload


def read_projection(
    project_root: str | Path,
    *,
    require_fresh: bool = True,
) -> dict[str, Any]:
    path = projection_path(project_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectionStaleError("canon_v3_projection_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CanonProjectionError("canon_v3_projection_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != PROJECTION_SCHEMA:
        raise CanonProjectionError("canon_v3_projection_schema_invalid")
    binding = payload.get("binding")
    legacy_base = payload.get("legacy_base")
    legacy_fact_records = payload.get("legacy_fact_records")
    chapters = payload.get("chapters")
    author_axioms = payload.get("author_axioms")
    facts = payload.get("facts")
    history = payload.get("history")
    if (
        not isinstance(binding, dict)
        or not isinstance(legacy_base, dict)
        or not isinstance(legacy_fact_records, list)
        or not isinstance(chapters, list)
        or not isinstance(author_axioms, dict)
        or not isinstance(author_axioms.get("records"), list)
        or not isinstance(facts, list)
        or not isinstance(history, list)
    ):
        raise CanonProjectionError("canon_v3_projection_shape_invalid")
    if require_fresh:
        repository = CanonV3Repository(project_root)
        with repository.locked():
            repository.assert_projection_fresh(binding)
            head = repository.current_head(validate=True)
            if head is None:
                raise ProjectionStaleError("canon_v3_projection_without_head")
            expected = _projection_from_head(repository, head)
            if payload != expected:
                raise CanonProjectionError("canon_v3_projection_content_mismatch")
    return payload


def projection_binding(project_root: str | Path) -> ProjectionBinding | None:
    try:
        payload = read_projection(project_root, require_fresh=False)
    except (CanonProjectionError, ProjectionStaleError):
        return None
    binding = payload.get("binding") or {}
    try:
        generation = int(binding.get("generation") or 0)
    except (TypeError, ValueError):
        return None
    raw_head = binding.get("head_hash")
    head = str(raw_head).strip() if raw_head is not None else None
    return ProjectionBinding(generation=generation, head_hash=head)


def projection_is_fresh(project_root: str | Path) -> bool:
    try:
        read_projection(project_root, require_fresh=True)
    except (CanonIntegrityError, CanonProjectionError, ProjectionStaleError):
        return False
    return True


__all__ = [
    "CanonProjectionError",
    "PROJECTION_RELATIVE_PATH",
    "PROJECTION_SCHEMA",
    "projection_binding",
    "fact_record_index",
    "projection_is_fresh",
    "projection_path",
    "read_projection",
    "rebuild_projection",
]

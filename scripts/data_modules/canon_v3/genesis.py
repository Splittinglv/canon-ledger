#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native Canon v3 genesis creation for brand-new book projects."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from ..canonical_history import history_to_asof_snapshot, load_canonical_history
from .projection import rebuild_projection
from .repository import CanonRepositoryError, CanonV3Repository, content_hash


GENESIS_SCHEMA = "canon-v3/genesis/v1"
GENESIS_SNAPSHOT_SCHEMA = "canon-v3/genesis-fact-snapshot/v1"
GENESIS_ADMISSION_SCHEMA = "canon-v3/genesis-fact-admission/v1"
GENESIS_RESULT_SCHEMA = "canon-v3/genesis-result/v1"

_LIST_FACT_CHANNELS = (
    "canonical_facts",
    "hard_constraints",
    "rules",
    "obligations",
    "lifecycle_history",
    "state_changes",
    "timeline",
    "presence_history",
    "custody_history",
)
_MAP_FACT_CHANNELS = ("information", "presence", "custody")


class GenesisError(RuntimeError):
    """A clean project cannot be converted into a native Canon genesis."""


def _root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve()


def _row_slot(row: Mapping[str, Any]) -> str:
    category = str(row.get("category") or "")
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
    if category in {
        "character_state",
        "character_state_changed",
        "power_breakthrough",
    }:
        descriptor = {
            "family": "character_state",
            "subject": str(row.get("subject") or row.get("entity_id") or ""),
            "field": str(row.get("field") or payload.get("system") or "realm"),
        }
    elif category == "relationship":
        descriptor = {
            "family": "relationship",
            "subject": str(row.get("subject") or ""),
            "object": str(row.get("field") or ""),
        }
    elif category == "world_rule":
        descriptor = {
            "family": "world_rule",
            "domain": str(row.get("subject") or ""),
            "field": str(row.get("field") or ""),
        }
    elif category in {"knowledge", "knowledge_state_changed"}:
        descriptor = {
            "family": "knowledge",
            "subject": str(row.get("subject") or row.get("entity_id") or ""),
            "claim": str(
                row.get("canonical_claim")
                or row.get("content")
                or row.get("value")
                or ""
            ),
        }
    elif category in {"presence", "presence_observed"}:
        descriptor = {
            "family": "presence",
            "subject": str(row.get("subject") or row.get("entity_id") or ""),
        }
    elif category in {"custody", "custody_changed", "artifact_obtained"}:
        descriptor = {
            "family": "custody",
            "item": str(
                row.get("artifact_id")
                or row.get("subject")
                or payload.get("artifact")
                or ""
            ),
        }
    else:
        descriptor = {
            "family": "genesis_fact",
            "category": category,
            "subject": str(row.get("subject") or ""),
            "field": str(row.get("field") or ""),
            "value": row.get("value"),
        }
    return content_hash(descriptor)


def _normalize_facts(raw: Mapping[str, Any]) -> dict[str, Any]:
    facts = copy.deepcopy(dict(raw))
    initial = facts.get("initial_canon")
    protagonist = (
        initial.get("protagonist")
        if isinstance(initial, Mapping)
        and isinstance(initial.get("protagonist"), Mapping)
        else {}
    )
    protagonist_name = str(protagonist.get("name") or "").strip()

    for channel in _LIST_FACT_CHANNELS:
        rows = facts.get(channel) or []
        if not isinstance(rows, list):
            raise GenesisError(f"canon_v3_genesis_{channel}_not_list")
        for row in rows:
            if not isinstance(row, dict):
                raise GenesisError(f"canon_v3_genesis_{channel}_fact_not_mapping")
            if (
                protagonist_name
                and str(row.get("subject") or "") == "protagonist"
            ):
                row["subject"] = protagonist_name
            row.setdefault("slot_id", _row_slot(row))

    for channel in _MAP_FACT_CHANNELS:
        rows = facts.get(channel) or {}
        if not isinstance(rows, dict):
            raise GenesisError(f"canon_v3_genesis_{channel}_not_mapping")
        for key, row in rows.items():
            if not isinstance(row, dict):
                raise GenesisError(f"canon_v3_genesis_{channel}_fact_not_mapping")
            row.setdefault("subject", str(row.get("entity_id") or key))
            row.setdefault("slot_id", _row_slot(row))

    knowledge = facts.get("knowledge_by_entity") or {}
    if not isinstance(knowledge, dict):
        raise GenesisError("canon_v3_genesis_knowledge_not_mapping")
    for entity_key, rows in knowledge.items():
        if not isinstance(rows, dict):
            raise GenesisError("canon_v3_genesis_entity_knowledge_not_mapping")
        for key, row in rows.items():
            if not isinstance(row, dict):
                raise GenesisError(
                    "canon_v3_genesis_entity_knowledge_fact_not_mapping"
                )
            row.setdefault("category", "knowledge")
            row.setdefault("subject", str(entity_key))
            row.setdefault("information_id", str(key))
            row.setdefault("slot_id", _row_slot(row))
    return facts


def _fact_locations(facts: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for channel in _LIST_FACT_CHANNELS:
        for index, row in enumerate(facts.get(channel) or []):
            if isinstance(row, dict):
                result.append((f"/{channel}/{index}", row))
    for channel in _MAP_FACT_CHANNELS:
        rows = facts.get(channel) or {}
        if isinstance(rows, Mapping):
            for key, row in sorted(rows.items()):
                if isinstance(row, dict):
                    result.append((f"/{channel}/{key}", row))
    knowledge = facts.get("knowledge_by_entity") or {}
    if isinstance(knowledge, Mapping):
        for entity_key, rows in sorted(knowledge.items()):
            if not isinstance(rows, Mapping):
                continue
            for key, row in sorted(rows.items()):
                if isinstance(row, dict):
                    result.append(
                        (f"/knowledge_by_entity/{entity_key}/{key}", row)
                    )
    entities = facts.get("entities") or {}
    if isinstance(entities, Mapping):
        for key, row in sorted(entities.items()):
            if isinstance(row, dict):
                result.append((f"/entities/{key}", row))
    return result


def _build_admissions(facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for location, fact in _fact_locations(facts):
        digest = content_hash(fact)
        entry = grouped.setdefault(
            digest,
            {
                "schema_version": GENESIS_ADMISSION_SCHEMA,
                "fact_content_sha256": digest,
                "locations": [],
            },
        )
        entry["locations"].append(location)
    result: list[dict[str, Any]] = []
    for digest in sorted(grouped):
        row = grouped[digest]
        row["locations"] = sorted(set(row["locations"]))
        row["admission_digest"] = content_hash(row)
        result.append(row)
    return result


def build_genesis_snapshot(project_root: str | Path) -> dict[str, Any]:
    """Build the deterministic fact snapshot for a clean chapter-zero project."""

    root = _root(project_root)
    history = load_canonical_history(
        root,
        0,
        prefer_v3=False,
        cutover_strict=True,
    )
    if history.valid_chapters or history.invalid_sources or history.omitted_fact_ids:
        raise GenesisError("canon_v3_genesis_requires_clean_chapter_zero_project")
    facts = _normalize_facts(history_to_asof_snapshot(history, chapter=1))
    facts["genesis_fact_admissions"] = _build_admissions(facts)
    return {
        "schema_version": GENESIS_SNAPSHOT_SCHEMA,
        "source_schema_version": str(facts.get("schema_version") or ""),
        "facts": facts,
    }


def build_genesis_metadata(project_root: str | Path) -> dict[str, Any]:
    snapshot = build_genesis_snapshot(project_root)
    return {
        "schema_version": GENESIS_SCHEMA,
        "source": "new_project",
        "snapshot": snapshot,
        "snapshot_sha256": content_hash(snapshot),
    }


def initialize_genesis(project_root: str | Path) -> dict[str, Any]:
    root = _root(project_root)
    repository = CanonV3Repository(root)
    try:
        existing_head = repository.current_head(validate=True)
    except CanonRepositoryError as exc:
        raise GenesisError("canon_v3_current_invalid") from exc
    if existing_head is not None:
        projection = rebuild_projection(root)
        return {
            "schema_version": GENESIS_RESULT_SCHEMA,
            "created": False,
            "head_hash": existing_head,
            "projection_binding": dict(projection.get("binding") or {}),
        }

    first = build_genesis_metadata(root)
    second = build_genesis_metadata(root)
    if first != second:
        raise GenesisError("canon_v3_genesis_sources_changed_during_initialize")
    try:
        head = repository.initialize(expected_head=None, genesis_metadata=second)
        projection = rebuild_projection(root)
    except CanonRepositoryError as exc:
        raise GenesisError("canon_v3_initialize_failed") from exc
    return {
        "schema_version": GENESIS_RESULT_SCHEMA,
        "created": True,
        "head_hash": head,
        "snapshot_sha256": second["snapshot_sha256"],
        "projection_binding": dict(projection.get("binding") or {}),
    }


__all__ = [
    "GENESIS_ADMISSION_SCHEMA",
    "GENESIS_RESULT_SCHEMA",
    "GENESIS_SCHEMA",
    "GENESIS_SNAPSHOT_SCHEMA",
    "GenesisError",
    "build_genesis_metadata",
    "build_genesis_snapshot",
    "initialize_genesis",
]

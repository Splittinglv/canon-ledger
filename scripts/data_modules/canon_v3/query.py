#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HEAD-bound public fact queries for Canon v3.

This module is deliberately projection/history based.  Public callers must
never consult the legacy SQLite index, state cache, memory store or RAG data to
answer factual questions used for writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..canonical_history import export_asof_snapshot
from ..workflow_authority import CanonReadModelUnavailable, WorkflowAuthority
from .author_axiom import record_digest
from .schema import AuthorAxiomRecord, AuthorAxiomSource, canonical_digest


QUERY_RESULT_SCHEMA = "canon-v3/query-result/v1"
ASOF_AUTHOR_AXIOMS_SCHEMA = "canon-v3/asof-author-axioms/v1"


class CanonQueryError(ValueError):
    """A public Canon query could not produce one stable authoritative view."""


def bound_workflow_unchanged(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> bool:
    """Return whether two reads describe one exact public authority view.

    A staging operation can change ``workflow_digest`` without advancing HEAD.
    Reviewer/data-agent artifacts bind that workflow token too, so a HEAD-only
    comparison is insufficient for their as-of input.
    """

    try:
        before_generation = int(before.get("generation") or 0)
        after_generation = int(after.get("generation") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        after.get("head_hash") == before.get("head_hash")
        and after_generation == before_generation
        and after.get("workflow_digest") == before.get("workflow_digest")
        and after.get("author_axiom_digest")
        == before.get("author_axiom_digest")
        and after.get("projection_fresh")
    )


def public_author_axiom_view(
    workflow: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the sanitized active-axiom view for public as-of consumers.

    Projection records retain the original managed-draft span as immutable
    publication evidence. Reviewer/data-agent need the approved semantics and
    an active ``author_axiom`` candidate source, not the draft quote/span or an
    opaque digest alone. This serializer validates every immutable record,
    converts its evidence to the public active-source shape, and binds the
    result to the same exact HEAD/generation as the projection.
    """

    binding = projection.get("binding")
    raw_axioms = projection.get("author_axioms")
    if not isinstance(binding, Mapping) or not isinstance(raw_axioms, Mapping):
        raise CanonQueryError("canon_v3_public_author_axioms_shape_invalid")
    try:
        binding_generation = int(binding.get("generation") or 0)
        workflow_generation = int(workflow.get("generation") or 0)
    except (TypeError, ValueError) as exc:
        raise CanonQueryError(
            "canon_v3_public_author_axioms_binding_invalid"
        ) from exc
    if (
        binding.get("head_hash") != workflow.get("head_hash")
        or binding_generation != workflow_generation
    ):
        raise CanonQueryError(
            "canon_v3_public_author_axioms_binding_mismatch"
        )

    raw_records = raw_axioms.get("records")
    if not isinstance(raw_records, list):
        raise CanonQueryError("canon_v3_public_author_axioms_records_invalid")

    records: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    try:
        for raw_record in raw_records:
            record = AuthorAxiomRecord.model_validate(raw_record)
            if record.axiom_key in seen_keys:
                raise CanonQueryError(
                    "canon_v3_public_author_axiom_key_duplicate"
                )
            seen_keys.add(record.axiom_key)
            active_source = AuthorAxiomSource(
                source_id=record.source.source_id,
                document_path=record.source.document_path,
                document_sha256=record.source.document_sha256,
                json_pointer=record.source.json_pointer,
                value=record.source.value,
                value_sha256=record.source.value_sha256,
            )
            records.append(
                {
                    "axiom_key": record.axiom_key,
                    "category": record.category.value,
                    "value": record.source.value,
                    "value_sha256": record.source.value_sha256,
                    "record_digest": record_digest(record),
                    "source": active_source.model_dump(mode="json"),
                }
            )
    except CanonQueryError:
        raise
    except Exception as exc:
        raise CanonQueryError(
            "canon_v3_public_author_axiom_record_invalid"
        ) from exc

    records.sort(key=lambda item: str(item["axiom_key"]))
    return {
        "schema_version": ASOF_AUTHOR_AXIOMS_SCHEMA,
        "head_hash": workflow.get("head_hash"),
        "generation": workflow_generation,
        "author_axiom_digest": workflow.get("author_axiom_digest"),
        "active_commit_hash": raw_axioms.get("commit_hash"),
        "active_record_set_digest": raw_axioms.get("axiom_set_digest"),
        "records": records,
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _entity_identifiers(stable_key: str, row: Mapping[str, Any]) -> set[str]:
    values = {
        _text(stable_key),
        _text(row.get("id")),
        _text(row.get("name")),
        *(_text(alias) for alias in row.get("aliases") or ()),
    }
    for value in tuple(values):
        if ":" in value:
            values.add(value.split(":", 1)[1])
    return {value for value in values if value}


@dataclass(frozen=True, slots=True)
class _BoundSnapshot:
    workflow: dict[str, Any]
    projection: dict[str, Any]
    as_of: dict[str, Any]


class CanonQueryFacade:
    """Return facts from exactly one fresh CURRENT/HEAD projection."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.authority = WorkflowAuthority(self.project_root)

    def _bound_snapshot(self, as_of_chapter: int | None) -> _BoundSnapshot:
        try:
            workflow, projection = self.authority.require_fresh_projection()
        except CanonReadModelUnavailable as exc:
            raise CanonQueryError(str(exc)) from exc

        latest = max(0, int(workflow.get("latest_chapter") or 0))
        requested = latest if as_of_chapter is None else int(as_of_chapter)
        if requested < 0:
            raise CanonQueryError("canon_v3_query_as_of_negative")
        if requested > latest:
            raise CanonQueryError(
                f"canon_v3_query_as_of_after_head:requested={requested};latest={latest}"
            )

        as_of = export_asof_snapshot(
            self.project_root,
            chapter=requested + 1,
            as_of_chapter=requested,
        )
        as_of["author_axioms"] = public_author_axiom_view(
            workflow, projection
        )
        invalid_sources = tuple(
            sorted(str(item) for item in as_of.get("invalid_sources") or ())
        )
        if invalid_sources:
            raise CanonQueryError(
                "canon_v3_query_invalid_sources:" + ",".join(invalid_sources)
            )
        after = self.authority.snapshot()
        if not bound_workflow_unchanged(workflow, after):
            raise CanonQueryError("canon_v3_authority_changed_during_query")
        return _BoundSnapshot(
            workflow=dict(workflow),
            projection=dict(projection),
            as_of=dict(as_of),
        )

    @staticmethod
    def _binding(bound: _BoundSnapshot, as_of_chapter: int) -> dict[str, Any]:
        return {
            "schema_version": QUERY_RESULT_SCHEMA,
            "authority": "canon_v3",
            "head_hash": bound.workflow.get("head_hash"),
            "generation": int(bound.workflow.get("generation") or 0),
            "workflow_digest": bound.workflow.get("workflow_digest"),
            "author_axiom_digest": bound.workflow.get(
                "author_axiom_digest"
            ),
            "projection_digest": canonical_digest(bound.projection),
            "as_of_chapter": int(as_of_chapter),
            "invalid_sources": list(bound.as_of.get("invalid_sources") or []),
        }

    @staticmethod
    def _matching_entities(
        entities: Mapping[str, Any], query: str
    ) -> list[tuple[str, Mapping[str, Any]]]:
        token = _text(query)
        if not token:
            return []
        matches: list[tuple[str, Mapping[str, Any]]] = []
        for stable_key, raw in entities.items():
            if not isinstance(raw, Mapping):
                continue
            if token in _entity_identifiers(_text(stable_key), raw):
                matches.append((_text(stable_key), raw))
        return sorted(matches, key=lambda item: item[0])

    def snapshot(self, *, as_of_chapter: int | None = None) -> dict[str, Any]:
        bound = self._bound_snapshot(as_of_chapter)
        actual = int(bound.as_of.get("as_of_chapter") or 0)
        return {
            **self._binding(bound, actual),
            "query": "snapshot",
            "data": bound.as_of,
        }

    def entity_state(
        self,
        entity: str,
        *,
        as_of_chapter: int | None = None,
    ) -> dict[str, Any]:
        bound = self._bound_snapshot(as_of_chapter)
        actual = int(bound.as_of.get("as_of_chapter") or 0)
        entities = bound.as_of.get("entities") or {}
        matches = self._matching_entities(entities, entity)
        resolved = matches[0] if len(matches) == 1 else None
        stable_key = _text(entity)
        entity_row: Mapping[str, Any] = {}
        identifiers = {stable_key}
        if resolved is not None:
            stable_key, entity_row = resolved
            identifiers = _entity_identifiers(stable_key, entity_row)

        state: dict[str, Any] = dict(entity_row.get("attributes") or {})
        for row in bound.as_of.get("state_changes") or []:
            if not isinstance(row, Mapping):
                continue
            subject = _text(
                row.get("entity_id")
                or row.get("subject")
                or (row.get("payload") or {}).get("subject")
            )
            if subject not in identifiers:
                continue
            field = _text(
                row.get("field")
                or (row.get("payload") or {}).get("attribute")
                or (row.get("payload") or {}).get("canonical_field")
            )
            if not field:
                continue
            state[field] = (
                row.get("new_value")
                if row.get("new_value") is not None
                else row.get("value")
                if row.get("value") is not None
                else (row.get("payload") or {}).get("after")
            )
        return {
            **self._binding(bound, actual),
            "query": "entity_state",
            "data": {
                "requested_entity": _text(entity),
                "entity_id": _text(entity_row.get("id")) or stable_key,
                "resolved": resolved is not None,
                "resolution": (
                    "resolved"
                    if resolved is not None
                    else "ambiguous"
                    if len(matches) > 1
                    else "not_found"
                ),
                "requires_human_resolution": len(matches) > 1,
                "matching_entity_ids": [
                    _text(row.get("id")) or key for key, row in matches
                ],
                "state": state,
            },
        }

    def relationships(
        self,
        entity: str,
        *,
        as_of_chapter: int | None = None,
    ) -> dict[str, Any]:
        bound = self._bound_snapshot(as_of_chapter)
        actual = int(bound.as_of.get("as_of_chapter") or 0)
        entities = bound.as_of.get("entities") or {}
        matches = self._matching_entities(entities, entity)
        resolved = matches[0] if len(matches) == 1 else None
        identifiers = {_text(entity)}
        stable_key = _text(entity)
        if resolved is not None:
            stable_key, entity_row = resolved
            identifiers = _entity_identifiers(stable_key, entity_row)

        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for row in bound.as_of.get("hard_constraints") or []:
            if not isinstance(row, Mapping):
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
            category = _text(row.get("category") or payload.get("kind"))
            if category not in {"relationship", "relationship_changed"}:
                continue
            subject = _text(row.get("subject") or payload.get("subject"))
            object_name = _text(payload.get("object") or row.get("field"))
            if subject not in identifiers and object_name not in identifiers:
                continue
            # Relationship slots are directional in the compiler
            # (subject, object).  A->B and B->A may carry different facts and
            # must never overwrite one another in the public query view.
            key = (subject, object_name)
            latest[key] = {
                "subject": subject,
                "object": object_name,
                "relationship": payload.get("after") or row.get("value"),
                "source_chapter": int(row.get("source_chapter") or 0),
                "fact_digest": _text(row.get("fact_digest")),
            }
        return {
            **self._binding(bound, actual),
            "query": "relationships",
            "data": {
                "requested_entity": _text(entity),
                "entity_id": stable_key,
                "resolved": resolved is not None,
                "resolution": (
                    "resolved"
                    if resolved is not None
                    else "ambiguous"
                    if len(matches) > 1
                    else "not_found"
                ),
                "requires_human_resolution": len(matches) > 1,
                "matching_entity_ids": [
                    _text(row.get("id")) or key for key, row in matches
                ],
                "relationships": [latest[key] for key in sorted(latest)],
            },
        }


__all__ = [
    "ASOF_AUTHOR_AXIOMS_SCHEMA",
    "CanonQueryError",
    "CanonQueryFacade",
    "QUERY_RESULT_SCHEMA",
    "bound_workflow_unchanged",
    "public_author_axiom_view",
]

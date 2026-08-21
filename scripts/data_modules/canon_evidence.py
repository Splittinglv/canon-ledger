#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence binding for records that can change chapter canon.

The extractor may summarize prose, but every writable canon record must point
back to one accepted event whose verbatim quote is present in the bound chapter.
This module is deliberately deterministic: it validates identity and structured
state fields, while semantic uncertainty is routed to human review elsewhere.
"""
from __future__ import annotations

import json
import hashlib
import unicodedata
from typing import Any, Iterable, Literal, Mapping

from .fact_text import event_evidence_in_chapter, normalize_event_evidence_quote


EVIDENCE_CONTRACT_VERSION = "canon-evidence/v1"
LEGACY_QUOTE_SPAN_SCHEMA = "canon-v3/legacy-quote-span/v2"
CHAPTER_COMMIT_SCHEMA_V1 = "story-system/v1"
CHAPTER_COMMIT_SCHEMA_V2 = "story-system/v2"
EvidenceContractClassification = Literal["strict", "legacy", "invalid"]
CANON_MUTATING_EVENT_TYPES = frozenset(
    {
        "character_state_changed",
        "relationship_changed",
        "world_rule_revealed",
        "world_rule_broken",
        "power_breakthrough",
        "artifact_obtained",
        "entity_observed",
        "timeline_observed",
        "knowledge_state_changed",
        "presence_observed",
        "custody_changed",
        "promise_created",
        "promise_paid_off",
        "open_loop_created",
        "open_loop_closed",
    }
)
LINKED_CANON_FIELDS = ("state_deltas", "entity_deltas", "timeline_events")


def bind_legacy_event_quote_span(
    event: Mapping[str, Any],
    chapter_text: str,
) -> dict[str, Any]:
    """Bind a legacy event quote to exact UTF-8 byte spans in bound prose.

    Historical v1 envelopes did not carry the strict evidence marker.  Their
    free-form ``evidence_quote`` must therefore be re-proved against the exact
    manuscript bytes during cutover; retaining just a quote digest would let a
    fabricated quote become its own evidence.
    """

    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    quote = normalize_event_evidence_quote(payload.get("evidence_quote"))
    if not quote or not isinstance(chapter_text, str):
        raise ValueError("legacy_event_quote_invalid")
    chapter_bytes = chapter_text.encode("utf-8")
    quote_bytes = quote.encode("utf-8")
    spans: list[dict[str, int]] = []
    offset = 0
    while True:
        start = chapter_bytes.find(quote_bytes, offset)
        if start < 0:
            break
        spans.append({"start": start, "end": start + len(quote_bytes)})
        offset = start + max(1, len(quote_bytes))
    if not spans:
        raise ValueError("legacy_event_quote_not_in_bound_chapter")
    return {
        "schema_version": LEGACY_QUOTE_SPAN_SCHEMA,
        "quote_sha256": hashlib.sha256(quote_bytes).hexdigest(),
        "chapter_text_sha256": hashlib.sha256(chapter_bytes).hexdigest(),
        "encoding": "utf-8",
        "spans": spans,
    }


def classify_evidence_contract(commit: Any) -> EvidenceContractClassification:
    """Classify a chapter commit without permitting marker downgrade.

    ``story-system/v2`` is the current write envelope and is strict-only.
    Historical ``story-system/v1`` envelopes may either carry both strict
    evidence markers or neither marker. Any partial, unknown, or mismatched
    combination is invalid rather than silently falling back to legacy.
    """
    if not isinstance(commit, dict):
        return "invalid"
    meta = commit.get("meta")
    extraction = commit.get("extraction_result")
    provenance = commit.get("provenance")
    schema_version = (
        str(meta.get("schema_version") or "").strip()
        if isinstance(meta, dict)
        else ""
    )
    extraction_marker = (
        str(extraction.get("evidence_contract") or "").strip()
        if isinstance(extraction, dict)
        else ""
    )
    provenance_marker = (
        str(provenance.get("evidence_contract") or "").strip()
        if isinstance(provenance, dict)
        else ""
    )
    has_strict_markers = (
        extraction_marker == EVIDENCE_CONTRACT_VERSION
        and provenance_marker == EVIDENCE_CONTRACT_VERSION
    )
    has_no_markers = not extraction_marker and not provenance_marker

    if schema_version == CHAPTER_COMMIT_SCHEMA_V2:
        return "strict" if has_strict_markers else "invalid"
    if schema_version == CHAPTER_COMMIT_SCHEMA_V1:
        if has_strict_markers:
            return "strict"
        if has_no_markers:
            return "legacy"
    return "invalid"


def commit_uses_evidence_contract(commit: Any) -> bool:
    """Compatibility predicate; new consumers should use the classifier."""
    return classify_evidence_contract(commit) == "strict"


def validate_event_evidence(
    events: Iterable[Any],
    chapter_text: str,
    *,
    field_name: str = "accepted_events",
) -> dict[str, dict[str, Any]]:
    """Validate every accepted event and return a unique event-id index."""
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            raise ValueError(f"{field_name}[{index}] must be a JSON object")
        event = dict(raw)
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(f"{field_name}[{index}].event_id must be non-empty")
        if event_id in by_id:
            raise ValueError(f"{field_name}[{index}].event_id is duplicated: {event_id}")
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in CANON_MUTATING_EVENT_TYPES:
            raise ValueError(f"{field_name}[{index}].event_type is unsupported: {event_type}")
        payload = event.get("payload")
        quote = normalize_event_evidence_quote(
            payload.get("evidence_quote") if isinstance(payload, dict) else None
        )
        if not quote or not event_evidence_in_chapter(event, chapter_text):
            raise ValueError(
                f"{field_name}[{index}].payload.evidence_quote "
                "is not present in the bound chapter"
            )
        by_id[event_id] = event
    return by_id


def merge_withheld_records(extraction: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Rehydrate records withheld while their source event awaited a verdict."""
    withheld = extraction.get("withheld_canon_records")
    withheld = withheld if isinstance(withheld, dict) else {}
    result: dict[str, list[dict[str, Any]]] = {}
    for field in LINKED_CANON_FIELDS:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for bucket in (extraction.get(field), withheld.get(field)):
            if not isinstance(bucket, list):
                continue
            for raw in bucket:
                if not isinstance(raw, dict):
                    raise ValueError(f"extraction_result.{field} must contain objects")
                row = dict(raw)
                fingerprint = json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
                )
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                rows.append(row)
        result[field] = rows
    return result


def _present(row: dict[str, Any], *keys: str) -> tuple[bool, Any]:
    for key in keys:
        if key in row:
            return True, row.get(key)
    return False, None


def _same_value(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, default=str
    )


def _state_signature(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_type = str(event.get("event_type") or "")
    field_name = str(payload.get("field") or payload.get("field_path") or "").strip()
    if event_type == "power_breakthrough" and not field_name:
        field_name = "realm"
    _has_old, old = _present(payload, "old", "old_value", "from", "previous_state")
    _has_new, new = _present(payload, "new", "new_value", "to", "new_state")
    return {
        "entity_id": str(payload.get("entity_id") or event.get("subject") or "").strip(),
        "field": field_name,
        "old": old,
        "new": new,
    }


def _normalize_entity_delta(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    for key in (
        "canonical_name",
        "name",
        "aliases",
        "mentions",
        "entity_type",
        "type",
        "tier",
        "is_protagonist",
    ):
        if key not in normalized and key in payload:
            normalized[key] = payload[key]
    return normalized


def _source_participants(event: dict[str, Any]) -> set[str]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    values = {
        event.get("subject"),
        payload.get("entity_id"),
        payload.get("artifact_id"),
        payload.get("from_entity"),
        payload.get("to_entity"),
        payload.get("from_holder"),
        payload.get("to_holder"),
        payload.get("source_entity"),
    }
    return {str(value).strip() for value in values if str(value or "").strip()}


def _normalized_contains(evidence: Any, value: Any) -> bool:
    haystack = " ".join(
        unicodedata.normalize("NFKC", str(evidence or "")).casefold().split()
    )
    needle = " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )
    return bool(needle) and needle in haystack


class LegacyCutoverEvidenceError(ValueError):
    """A v2 event cannot be promoted without an exact human decision."""

    def __init__(self, fields: Iterable[str]) -> None:
        self.fields = tuple(sorted({str(field) for field in fields if str(field)}))
        super().__init__("legacy_cutover_fields_unproved:" + ",".join(self.fields))


def _stable_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _flatten_semantic_atoms(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, Mapping):
        atoms: list[Any] = []
        for key in sorted(value):
            atoms.extend(_flatten_semantic_atoms(value[key]))
        return atoms
    if isinstance(value, (list, tuple)):
        atoms = []
        for item in value:
            atoms.extend(_flatten_semantic_atoms(item))
        return atoms
    return [value]


def _legacy_first(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return ""


def validate_legacy_cutover_event(
    event: Mapping[str, Any],
    linked_records: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    *,
    event_fields_human_approved: bool = False,
) -> dict[str, Any]:
    """Prove every v2 semantic atom that the cutover reader can retain.

    A v2 ``supported`` flag only proves that an evidence quote exists.  This
    admission validator is deliberately stricter and is used by migration:
    every value promoted into durable fact/history must itself occur in that
    quote (or the caller must supply an independently verified human proof).
    Opaque IDs may be supported by a linked entity row whose every display
    name/type is itself quote-bound.
    """

    payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
    quote = normalize_event_evidence_quote(payload.get("evidence_quote"))
    if not quote:
        raise LegacyCutoverEvidenceError(("payload.evidence_quote",))
    event_type = str(event.get("event_type") or "").strip()
    source_event_id = str(event.get("event_id") or "").strip()
    linked_records = linked_records or {}
    entity_rows = [
        dict(row)
        for row in linked_records.get("entity_deltas", ())
        if isinstance(row, Mapping)
        and str(row.get("source_event_id") or "").strip() == source_event_id
        and not (
            (row.get("from_entity") or row.get("from"))
            and (row.get("to_entity") or row.get("to"))
        )
    ]
    timeline_rows = [
        dict(row)
        for row in linked_records.get("timeline_events", ())
        if isinstance(row, Mapping)
        and str(row.get("source_event_id") or "").strip() == source_event_id
    ]

    linked_identity_names: dict[str, list[str]] = {}
    for row in entity_rows:
        entity_id = str(row.get("entity_id") or row.get("id") or "").strip()
        names: list[str] = []
        for key in ("canonical_name", "name"):
            value = str(row.get(key) or "").strip()
            if value:
                names.append(value)
        for key in ("aliases", "mentions"):
            values = row.get(key)
            if isinstance(values, list):
                names.extend(str(value).strip() for value in values if str(value).strip())
        if entity_id and names:
            linked_identity_names.setdefault(entity_id, []).extend(names)

    requirements: list[tuple[str, Any, bool]] = []

    def semantic(field: str, value: Any) -> None:
        for atom in _flatten_semantic_atoms(value):
            requirements.append((field, atom, False))

    def identity(field: str, value: Any) -> None:
        for atom in _flatten_semantic_atoms(value):
            requirements.append((field, atom, True))

    # Linked v2 rows are projection inputs, not independent evidence.  During
    # normal v2 reads they remain backwards compatible; cutover is where every
    # projected semantic field is closed against the source quote.
    for row in entity_rows:
        for key in ("canonical_name", "name", "entity_type", "type", "tier"):
            semantic(f"linked.entity.{key}", row.get(key))
        semantic("linked.entity.aliases", row.get("aliases"))
        semantic("linked.entity.mentions", row.get("mentions"))
    for row in timeline_rows:
        semantic(
            "linked.timeline.event",
            _legacy_first(row, "event", "content", "description"),
        )
        semantic(
            "linked.timeline.time_anchor",
            _legacy_first(row, "time_anchor", "time_hint", "time_label"),
        )
        semantic("linked.timeline.event_type", row.get("event_type"))

    subject = str(event.get("subject") or "").strip()
    if event_type in {"character_state_changed", "power_breakthrough"}:
        identity("subject", payload.get("entity_id") or subject)
        field_name = payload.get("field") or payload.get("field_path")
        if field_name and not (
            event_type == "power_breakthrough"
            and str(field_name).strip().lower() == "realm"
        ):
            semantic("payload.field", field_name)
        semantic("payload.old", _legacy_first(payload, "old", "old_value", "from", "previous_state"))
        semantic("payload.new", _legacy_first(payload, "new", "new_value", "to", "new_state"))
    elif event_type == "relationship_changed":
        identity("payload.from_entity", payload.get("from_entity") or subject)
        identity("payload.to_entity", payload.get("to_entity") or payload.get("to"))
        semantic(
            "payload.relationship_type",
            _legacy_first(payload, "relationship_type", "relation_type", "type"),
        )
    elif event_type == "world_rule_revealed":
        semantic("payload.domain", payload.get("domain") or subject)
        semantic("payload.field", payload.get("field"))
        semantic(
            "payload.rule_content",
            _legacy_first(payload, "rule_content", "content", "rule", "value"),
        )
        semantic("payload.scope", payload.get("scope"))
        semantic("payload.rule_category", payload.get("rule_category"))
    elif event_type == "world_rule_broken":
        if not (payload.get("rule_id") or payload.get("target_rule_id")):
            semantic("subject", subject)
            semantic("payload.domain", payload.get("domain"))
            semantic("payload.field", payload.get("field"))
        semantic(
            "payload.violation",
            _legacy_first(payload, "violation", "description", "proposed_value")
            or subject,
        )
        semantic("payload.base_value", payload.get("base_value"))
    elif event_type in {"open_loop_created", "promise_created"}:
        semantic(
            "payload.content",
            _legacy_first(payload, "content", "unanswered_question", "description")
            or subject,
        )
        semantic("payload.expected_payoff", payload.get("expected_payoff"))
    elif event_type in {"open_loop_closed", "promise_paid_off"}:
        semantic(
            "payload.resolution",
            _legacy_first(payload, "resolution", "description", "outcome"),
        )
    elif event_type == "entity_observed":
        entity_id = payload.get("entity_id") or payload.get("canonical_id") or subject
        identity("payload.entity_id", entity_id)
        semantic("payload.name", payload.get("name"))
        semantic("payload.aliases", payload.get("aliases"))
        namespace = str(payload.get("namespace") or "").strip()
        type_value = payload.get("entity_type") or payload.get("type")
        if namespace:
            semantic("payload.namespace", namespace)
        if type_value:
            semantic("payload.entity_type", type_value)
        if not namespace and not type_value:
            linked_types = [
                row.get("entity_type") or row.get("type")
                for row in entity_rows
                if row.get("entity_type") or row.get("type")
            ]
            if linked_types:
                semantic("linked.entity_type", linked_types)
            else:
                requirements.append(("payload.namespace", "", False))
    elif event_type == "timeline_observed":
        own_event = _legacy_first(payload, "event", "content") or subject
        own_anchor = _legacy_first(payload, "time_anchor", "time_hint", "time")
        if own_event and own_anchor:
            semantic("payload.event", own_event)
            semantic("payload.time_anchor", own_anchor)
            if timeline_rows and not any(
                str(_legacy_first(row, "event", "content", "description")).strip()
                == str(own_event).strip()
                and str(
                    _legacy_first(row, "time_anchor", "time_hint", "time_label")
                ).strip()
                == str(own_anchor).strip()
                for row in timeline_rows
            ):
                requirements.append(("linked.timeline.mismatch", "", False))
        elif timeline_rows:
            for row in timeline_rows:
                semantic("linked.event", _legacy_first(row, "event", "content", "description"))
                semantic(
                    "linked.time_anchor",
                    _legacy_first(row, "time_anchor", "time_hint", "time_label"),
                )
                semantic("linked.event_type", row.get("event_type"))
        else:
            requirements.append(("payload.event", "", False))
            requirements.append(("payload.time_anchor", "", False))
    elif event_type == "knowledge_state_changed":
        identity("subject", subject)
        semantic(
            "payload.canonical_claim",
            _legacy_first(payload, "canonical_claim", "content"),
        )
        semantic("payload.evidence_fragment", payload.get("evidence_fragment"))
        semantic("payload.state", payload.get("state"))
        semantic("payload.source_kind", payload.get("source_kind"))
        identity("payload.source_entity", payload.get("source_entity"))
    elif event_type == "presence_observed":
        identity("subject", subject)
        identity("payload.location_id", payload.get("location_id"))
        semantic("payload.presence_kind", payload.get("presence_kind"))
        semantic("payload.time_anchor", payload.get("time_anchor"))
    elif event_type == "custody_changed":
        identity("subject", subject)
        identity("payload.from_holder", payload.get("from_holder"))
        identity("payload.to_holder", payload.get("to_holder"))
        identity("payload.location_id", payload.get("location_id"))
    elif event_type == "artifact_obtained":
        identity(
            "payload.artifact_id",
            payload.get("artifact_id") or subject or payload.get("name"),
        )
        semantic("payload.name", payload.get("name"))
        identity("payload.owner", payload.get("owner") or payload.get("holder"))
        identity("payload.from_holder", payload.get("from_holder"))
        identity("payload.location_id", payload.get("location_id"))
    else:
        raise LegacyCutoverEvidenceError(("event_type",))

    unsupported: list[str] = []
    supported_fields: set[str] = set()
    for field, atom, is_identity in requirements:
        if event_fields_human_approved and not field.startswith("linked."):
            continue
        if atom in (None, ""):
            unsupported.append(field)
            continue
        if _normalized_contains(quote, atom):
            supported_fields.add(field)
            continue
        if is_identity:
            names = linked_identity_names.get(str(atom).strip(), [])
            if names and all(_normalized_contains(quote, name) for name in names):
                supported_fields.add(field)
                continue
        unsupported.append(field)

    negation_sensitive = {
        "character_state_changed",
        "power_breakthrough",
        "relationship_changed",
        "artifact_obtained",
        "entity_observed",
        "presence_observed",
        "custody_changed",
    }
    normalized_quote = unicodedata.normalize("NFKC", quote).casefold()
    if not event_fields_human_approved and event_type in negation_sensitive and any(
        marker in normalized_quote
        for marker in ("并非", "不是", "没有", "并未", "未曾", "从未", " not ", "never")
    ):
        unsupported.append("semantic_polarity")
    if unsupported:
        raise LegacyCutoverEvidenceError(unsupported)
    return {
        "mode": (
            "linked_field_evidence"
            if event_fields_human_approved
            else "field_evidence"
        ),
        "event_digest": _stable_digest(event),
        "evidence_quote_digest": _stable_digest(quote),
        "supported_fields": sorted(supported_fields),
    }


def _validate_state_delta(
    row: dict[str, Any], source: dict[str, Any], index: int
) -> dict[str, Any]:
    if str(source.get("event_type") or "") not in {
        "character_state_changed",
        "power_breakthrough",
    }:
        raise ValueError(
            f"state_deltas[{index}].source_event_id must reference a state event"
        )
    signature = _state_signature(source)
    entity_id = str(row.get("entity_id") or row.get("subject") or "").strip()
    field_name = str(row.get("field") or row.get("field_path") or "").strip()
    has_old, old = _present(row, "old", "old_value", "from")
    has_new, new = _present(row, "new", "new_value", "to")
    if not entity_id or not field_name or not has_new:
        raise ValueError(f"state_deltas[{index}] must declare entity_id, field and new")
    if entity_id != signature["entity_id"] or field_name != signature["field"]:
        raise ValueError(f"state_deltas[{index}] does not match its source event")
    if not _same_value(new, signature["new"]):
        raise ValueError(f"state_deltas[{index}].new does not match its source event")
    if has_old and not _same_value(old, signature["old"]):
        raise ValueError(f"state_deltas[{index}].old does not match its source event")
    return dict(row)


def _validate_entity_delta(
    row: dict[str, Any], source: dict[str, Any], index: int
) -> dict[str, Any]:
    normalized = _normalize_entity_delta(row)
    payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    from_entity = str(normalized.get("from_entity") or normalized.get("from") or "").strip()
    to_entity = str(normalized.get("to_entity") or normalized.get("to") or "").strip()
    if from_entity and to_entity:
        relation = str(
            normalized.get("relationship_type")
            or normalized.get("relation_type")
            or normalized.get("type")
            or ""
        ).strip()
        source_relation = str(
            payload.get("relationship_type")
            or payload.get("relation_type")
            or payload.get("type")
            or ""
        ).strip()
        if (
            str(source.get("event_type") or "") != "relationship_changed"
            or from_entity
            != str(payload.get("from_entity") or source.get("subject") or "").strip()
            or to_entity != str(payload.get("to_entity") or payload.get("to") or "").strip()
            or not relation
            or relation != source_relation
        ):
            raise ValueError(f"entity_deltas[{index}] relationship does not match source event")
        return normalized

    entity_id = str(normalized.get("entity_id") or normalized.get("id") or "").strip()
    if not entity_id or entity_id not in _source_participants(source):
        raise ValueError(f"entity_deltas[{index}].entity_id does not match source event")
    quote = str(payload.get("evidence_quote") or "")
    names: list[str] = []
    for key in ("canonical_name", "name"):
        if str(normalized.get(key) or "").strip():
            names.append(str(normalized[key]).strip())
    for key in ("aliases", "mentions"):
        if isinstance(normalized.get(key), list):
            names.extend(str(value).strip() for value in normalized[key] if str(value).strip())
    if names and not any(name in quote for name in names):
        raise ValueError(f"entity_deltas[{index}] name is not supported by source quote")
    for key in ("tier",):
        value = str(normalized.get(key) or "").strip()
        if value and value not in quote:
            raise ValueError(f"entity_deltas[{index}].{key} is not supported by source quote")
    return normalized


def _validate_timeline_row(
    row: dict[str, Any], source: dict[str, Any], index: int
) -> dict[str, Any]:
    source_payload = source.get("payload") if isinstance(source.get("payload"), dict) else {}
    source_quote = normalize_event_evidence_quote(source_payload.get("evidence_quote"))
    fragment = normalize_event_evidence_quote(row.get("evidence_fragment"))
    if not fragment or fragment not in source_quote:
        raise ValueError(
            f"timeline_events[{index}].evidence_fragment must be a verbatim "
            "fragment of its source event quote"
        )
    own_quote = normalize_event_evidence_quote(row.get("evidence_quote"))
    if own_quote and own_quote != source_quote:
        raise ValueError(
            f"timeline_events[{index}].evidence_quote must equal its source event quote"
        )
    return {**row, "evidence_quote": source_quote}


def validate_mutation_source_bindings(
    records: dict[str, list[dict[str, Any]]],
    event_index: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Validate source references and return normalized linked records."""
    normalized: dict[str, list[dict[str, Any]]] = {}
    validators = {
        "state_deltas": _validate_state_delta,
        "entity_deltas": _validate_entity_delta,
        "timeline_events": _validate_timeline_row,
    }
    for field in LINKED_CANON_FIELDS:
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(records.get(field) or []):
            row = dict(raw)
            source_event_id = str(row.get("source_event_id") or "").strip()
            if not source_event_id:
                raise ValueError(f"{field}[{index}].source_event_id must be non-empty")
            source = event_index.get(source_event_id)
            if source is None:
                raise ValueError(
                    f"{field}[{index}].source_event_id does not reference an accepted event"
                )
            own_quote = normalize_event_evidence_quote(row.get("evidence_quote"))
            source_payload = (
                source.get("payload") if isinstance(source.get("payload"), dict) else {}
            )
            source_quote = normalize_event_evidence_quote(source_payload.get("evidence_quote"))
            if own_quote and own_quote != source_quote:
                raise ValueError(f"{field}[{index}].evidence_quote must equal source event quote")
            row["source_event_id"] = source_event_id
            rows.append(validators[field](row, source, index))
        normalized[field] = rows
    return normalized


def partition_linked_records(
    records: dict[str, list[dict[str, Any]]],
    accepted_event_ids: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Separate active rows from rows whose candidate event is awaiting review."""
    active: dict[str, list[dict[str, Any]]] = {}
    withheld: dict[str, list[dict[str, Any]]] = {}
    for field in LINKED_CANON_FIELDS:
        active[field] = []
        withheld[field] = []
        for row in records.get(field) or []:
            target = (
                active
                if str(row.get("source_event_id") or "") in accepted_event_ids
                else withheld
            )
            target[field].append(dict(row))
    return active, withheld


def strict_commit_linked_records(
    commit: dict[str, Any], chapter_text: str
) -> dict[str, list[dict[str, Any]]]:
    """Revalidate a persisted evidence-contract commit before canon replay."""
    extraction = commit.get("extraction_result")
    extraction = extraction if isinstance(extraction, dict) else {}
    events = extraction.get("accepted_events")
    events = events if isinstance(events, list) else []
    event_index = validate_event_evidence(events, chapter_text)
    records = {
        field: list(extraction.get(field) or [])
        if isinstance(extraction.get(field), list)
        else []
        for field in LINKED_CANON_FIELDS
    }
    return validate_mutation_source_bindings(records, event_index)


def cutover_commit_linked_records(
    commit: Mapping[str, Any],
    chapter_text: str,
) -> dict[str, list[dict[str, Any]]]:
    """Close every legacy projection row against one admitted event.

    This intentionally applies the modern source-binding rules to markerless
    v1 envelopes as well.  ``entities_appeared`` and ``scenes`` were derived
    model views with no row-level event reference, so they are not Canon input
    during cutover.  If they are the only record of a real fact, the project
    must be recertified instead of silently promoting them.
    """

    extraction = commit.get("extraction_result")
    extraction = extraction if isinstance(extraction, Mapping) else {}
    events = extraction.get("accepted_events")
    events = events if isinstance(events, list) else []
    event_index: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            raise ValueError(f"accepted_events[{index}] must be a JSON object")
        event = dict(raw)
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(f"accepted_events[{index}].event_id must be non-empty")
        if event_id in event_index:
            raise ValueError(
                f"accepted_events[{index}].event_id is duplicated: {event_id}"
            )
        event_type = str(event.get("event_type") or "").strip()
        if event_type not in CANON_MUTATING_EVENT_TYPES:
            raise ValueError(
                f"accepted_events[{index}].event_type is unsupported: {event_type}"
            )
        bind_legacy_event_quote_span(event, chapter_text)
        event_index[event_id] = event

    records = {
        field: list(extraction.get(field) or [])
        if isinstance(extraction.get(field), list)
        else []
        for field in LINKED_CANON_FIELDS
    }
    normalized = validate_mutation_source_bindings(records, event_index)

    for field in ("entities_appeared", "scenes"):
        rows = extraction.get(field)
        if isinstance(rows, list) and rows:
            raise ValueError(
                f"extraction_result.{field} has no source_event_id admission"
            )
    return normalized

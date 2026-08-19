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


_HARD_EVIDENCE_EVENT_TYPES = {
    "knowledge_state_changed",
    "presence_observed",
    "custody_changed",
    "open_loop_created",
    "promise_created",
    "relationship_changed",
    "world_rule_broken",
}
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
        delta: dict[str, Any] = {"entity_id": entity, "field": field_name}
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


class ChapterCommitService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

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
        review_items = review_manual_check_items_from_review(review)
        pending_input = [
            dict(item) if isinstance(item, dict) else item
            for item in disambiguation.pending
        ]
        queued_event_ids = {
            str(item.get("candidate_event_id") or item.get("event_id") or "").strip()
            for item in pending_input
            if isinstance(item, dict)
        }
        merged_pending = pending_input + [
            item
            for item in conflict_items + inferred_items + collision_items + review_items
            if item.get("candidate_event_id") not in queued_event_ids
        ]

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
        blocking_pending = [
            item for item in unresolved if bool(item.get("blocking", False))
        ]
        outline_strict = fulfillment.enforcement == "strict"
        rejected = (
            bool(review.blocking_count)
            or bool(blocking_pending)
            or (outline_strict and bool(fulfillment.missed_nodes))
        )
        status = "rejected" if rejected else "accepted"
        volume = volume_num_for_chapter_from_state(self.project_root, chapter) or 1
        extraction_payload = extraction.model_dump()
        accepted_events = _apply_identity_actions(
            extraction_payload,
            list(human_review["events"]),
            collision_items,
            human_review.get("identity_actions") or {},
        )
        accepted_events = EventLogStore(self.project_root).normalize_events(
            chapter, accepted_events
        )
        evidence_envelope = {
            "meta": {"chapter": chapter},
            "chapter_binding": chapter_binding,
        }
        if status == "accepted":
            bound_chapter_text = bound_chapter_text_for_commit(
                self.project_root,
                evidence_envelope,
            )
            for index, event in enumerate(accepted_events):
                event_payload = (
                    event.get("payload") if isinstance(event.get("payload"), dict) else {}
                )
                quote = str(event_payload.get("evidence_quote") or "").strip()
                event_type = str(event.get("event_type") or "")
                requires_quote = event_type in _HARD_EVIDENCE_EVENT_TYPES
                # Hard-constraint and consistency events must bind to the
                # prose; any other event that claims a quote must also
                # actually quote this chapter.
                if (requires_quote or quote) and not event_evidence_in_chapter(
                    event, bound_chapter_text
                ):
                    raise ValueError(
                        f"accepted_events[{index}].payload.evidence_quote "
                        "is not present in the bound chapter"
                    )
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
            if dimension in coverage:
                coverage[dimension] = "partial"
            if verification and dimension in {
                "knowledge",
                "presence",
                "custody",
            }:
                verification[dimension] = "pending"
        extraction_payload["fact_coverage"] = coverage
        extraction_payload["fact_verification"] = verification
        extraction_payload["timeline_events"] = normalize_timeline_events(
            chapter, extraction.timeline_events
        )
        from .commit_lineage import (
            VALIDATION_VALID,
            predecessor_context_hash_for_chapter,
        )

        commit_payload = {
            "meta": {
                "schema_version": "story-system/v1",
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
                "chapter_binding": chapter_binding,
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
            "review_result": review.model_dump(),
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

    def persist_commit(
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
    ) -> None:
        commit_path = self.persist_commit(payload)
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

    def _block_invalid_lifecycle(self, payload: Dict[str, Any]) -> bool:
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
        self._persist_projection_run(payload, writer_results)
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
        self._persist_projection_run(payload, writer_results)
        return True

    def apply_projection_writers(
        self,
        payload: Dict[str, Any],
        *,
        only_writers: set[str] | None = None,
        persist_run: bool = True,
        writer_results_out: dict[str, dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}

        if self._block_changed_chapter_content(payload):
            return payload
        if self._block_invalid_lifecycle(payload):
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
                    self._persist_projection_run(payload, writer_results)
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
            self._persist_projection_run(payload, writer_results)
        return payload

    def apply_projections(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        if self._block_changed_chapter_content(payload):
            return payload

        # Persist the canonical source before deciding between the append
        # fast-path and a corpus rebuild.  The rebuild runs in an isolated
        # project root and only installs a read model that was produced from
        # this exact ordered commit set.
        self.persist_commit(payload)
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
            self._persist_projection_run(payload, writer_results)
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
            if self._block_invalid_lifecycle(payload):
                return payload
            event_store.write_events(chapter, extraction["accepted_events"])

            proposals = AmendProposalTrigger().check(chapter, extraction["accepted_events"])
            if proposals:
                manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
                with manager._get_conn() as conn:
                    ensure_override_ledger_columns(conn)
                    persist_amend_proposals(conn, chapter, proposals)
                    conn.commit()

        projected = self.apply_projection_writers(payload)
        projection_status = projected.get("projection_status") or {}
        if not any(str(value).startswith("failed") for value in projection_status.values()):
            record_projection_snapshot(self.project_root, projected)
        return projected

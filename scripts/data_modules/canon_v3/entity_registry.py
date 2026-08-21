#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Approved entity aliases and deterministic claim normalization for Canon v3.

The registry is derived only from ``entity_observed`` effects reachable from a
specific parent HEAD whose latest exact review decision approved the candidate.
Model-proposed aliases in the current transaction remain proposals; they can
help describe a pending resolution, but never become registry authority before
the transaction is finalized.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .evidence import candidate_digest
from .review import ReviewAction, decision_from_dict
from .schema import (
    CanonEffect,
    EntityResolution,
    EntityResolutionStatus,
    FactCandidate,
    FactKind,
    IdentityNamespace,
    ObservationKind,
    PreparedTransaction,
    ReviewLevel,
    ReviewObservation,
    canonical_digest,
)


ENTITY_REGISTRY_SCHEMA = "canon-v3/entity-registry/v1"
ENTITY_RESOLUTION_SCHEMA = "canon-v3/entity-resolution-plan/v1"


class EntityRegistryError(ValueError):
    pass


class EntityRegistryIntegrityError(EntityRegistryError):
    pass


class EntityRegistryConflict(EntityRegistryError):
    pass


def normalize_alias_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise EntityRegistryError("canon_v3_entity_alias_empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AliasBinding:
    namespace: IdentityNamespace
    alias_key: str
    canonical_entity: str
    effect_ids: tuple[str, ...]
    candidate_digests: tuple[str, ...]
    fact_digests: tuple[str, ...]
    approval_decision_hashes: tuple[str, ...]
    legacy_snapshot_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", IdentityNamespace(self.namespace))
        if normalize_alias_key(self.alias_key) != self.alias_key:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_alias_key_not_normalized"
            )
        if not str(self.canonical_entity or "").strip():
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_canonical_name_empty"
            )
        for name in (
            "effect_ids",
            "candidate_digests",
            "fact_digests",
            "approval_decision_hashes",
            "legacy_snapshot_digests",
        ):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise EntityRegistryIntegrityError(
                    f"canon_v3_entity_binding_{name}_not_canonical"
                )
            if any(
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in values
            ):
                raise EntityRegistryIntegrityError(
                    f"canon_v3_entity_binding_{name}_invalid"
                )
        v3_proof = bool(
            self.effect_ids
            and self.candidate_digests
            and self.fact_digests
            and self.approval_decision_hashes
        )
        legacy_proof = bool(self.legacy_snapshot_digests and self.fact_digests)
        if not (v3_proof or legacy_proof):
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_binding_missing_approval_proof"
            )

    def payload(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace.value,
            "alias_key": self.alias_key,
            "canonical_entity": self.canonical_entity,
            "effect_ids": list(self.effect_ids),
            "candidate_digests": list(self.candidate_digests),
            "fact_digests": list(self.fact_digests),
            "approval_decision_hashes": list(self.approval_decision_hashes),
            "legacy_snapshot_digests": list(self.legacy_snapshot_digests),
        }

    @property
    def binding_digest(self) -> str:
        return canonical_digest(
            {"schema_version": f"{ENTITY_REGISTRY_SCHEMA}/binding", **self.payload()}
        )


@dataclass(frozen=True, slots=True)
class EntityRegistry:
    parent_head: str
    bindings: tuple[AliasBinding, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.parent_head or "").strip():
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_registry_parent_head_missing"
            )
        keys = tuple(
            (
                binding.namespace.value,
                binding.alias_key,
                binding.canonical_entity,
            )
            for binding in self.bindings
        )
        if keys != tuple(sorted(set(keys))):
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_registry_aliases_not_unique"
            )

    @property
    def by_alias(self) -> dict[tuple[str, str], tuple[AliasBinding, ...]]:
        grouped: dict[tuple[str, str], list[AliasBinding]] = {}
        for binding in self.bindings:
            grouped.setdefault(
                (binding.namespace.value, binding.alias_key), []
            ).append(binding)
        return {
            key: tuple(
                sorted(value, key=lambda item: item.canonical_entity)
            )
            for key, value in grouped.items()
        }

    def resolve_all(
        self,
        value: str,
        namespace: IdentityNamespace | str = IdentityNamespace.ACTOR,
    ) -> tuple[AliasBinding, ...]:
        return self.by_alias.get(
            (IdentityNamespace(namespace).value, normalize_alias_key(value)),
            (),
        )

    def resolve(
        self,
        value: str,
        namespace: IdentityNamespace | str = IdentityNamespace.ACTOR,
    ) -> AliasBinding | None:
        bindings = self.resolve_all(value, namespace)
        targets = {binding.canonical_entity for binding in bindings}
        return bindings[0] if len(targets) == 1 else None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": ENTITY_REGISTRY_SCHEMA,
            "parent_head": self.parent_head,
            "entries": [binding.payload() for binding in self.bindings],
        }

    @property
    def registry_digest(self) -> str:
        return canonical_digest(self.payload())


@dataclass(frozen=True, slots=True)
class EntityResolutionPlan:
    registry: EntityRegistry
    normalized_candidates: tuple[FactCandidate, ...]
    candidate_digest_by_id: tuple[tuple[str, str], ...]
    resolutions: tuple[EntityResolution, ...]
    observations: tuple[ReviewObservation, ...]

    @property
    def candidates_by_digest(self) -> dict[str, FactCandidate]:
        raw_digests = dict(self.candidate_digest_by_id)
        return {
            raw_digests[candidate.candidate_id]: candidate
            for candidate in self.normalized_candidates
        }


def _latest_approved_candidates(
    repository: Any,
    commit: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    v1_schema = "canon-v3/decision-envelope/v1"
    v2_schema = "canon-v3/decision-envelope/v2"
    v1_fields = {"schema_version", "transaction_hash", "chapter", "decision"}
    v2_fields = {
        "schema_version",
        "transaction_hash",
        "chapter",
        "stage_digest_before",
        "target_digest",
        "material_digest",
        "expected_decision_head_hash",
        "lineage_key",
        "decision",
    }

    def valid_hash(value: Any, *, optional: bool = False) -> bool:
        if optional and value is None:
            return True
        text = str(value or "")
        return len(text) == 64 and all(
            char in "0123456789abcdef" for char in text
        )

    transaction_hash = str(commit.get("transaction_hash") or "")
    chapter = int(commit.get("chapter") or 0)
    heads: dict[str, tuple[Any, str]] = {}
    for object_hash in commit.get("decision_hashes") or ():
        wrapper = repository.read_decision(str(object_hash))
        schema = str(wrapper.get("schema_version") or "")
        if schema not in {v1_schema, v2_schema}:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_approval_envelope_schema_invalid"
            )
        expected_fields = v1_fields if schema == v1_schema else v2_fields
        if set(wrapper) != expected_fields:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_approval_envelope_fields_invalid"
            )
        if str(wrapper.get("transaction_hash") or "") != transaction_hash:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_approval_transaction_mismatch"
            )
        if int(wrapper.get("chapter") or 0) != chapter:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_approval_chapter_mismatch"
            )
        decision = decision_from_dict(wrapper.get("decision"))
        if schema == v2_schema:
            for field in (
                "stage_digest_before",
                "target_digest",
                "material_digest",
                "lineage_key",
            ):
                if not valid_hash(wrapper.get(field)):
                    raise EntityRegistryIntegrityError(
                        f"canon_v3_entity_approval_{field}_invalid"
                    )
            if not valid_hash(
                wrapper.get("expected_decision_head_hash"), optional=True
            ):
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_approval_decision_head_invalid"
                )
            expected_previous = decision.supersedes or None
            if wrapper.get("expected_decision_head_hash") != expected_previous:
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_approval_decision_head_mismatch"
                )
        previous = heads.get(decision.case_key)
        if previous is None or decision.revision > previous[0].revision:
            heads[decision.case_key] = (decision, str(object_hash))
        elif (
            decision.revision == previous[0].revision
            and decision.decision_hash != previous[0].decision_hash
        ):
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_approval_decision_fork"
            )

    approved: dict[str, set[str]] = {}
    for decision, object_hash in heads.values():
        if decision.action is not ReviewAction.APPROVE:
            continue
        digest = str(decision.context.candidate_digest or "")
        if digest:
            approved.setdefault(digest, set()).add(object_hash)
    return {
        digest: tuple(sorted(hashes)) for digest, hashes in approved.items()
    }


def build_approved_entity_registry(
    repository: Any,
    parent_head: str,
    *,
    target_chapter: int | None = None,
) -> EntityRegistry:
    """Fold only the target chapter's immutable N-1 identity registry."""

    manifest = repository.read_manifest(parent_head, validate_references=True)
    from .projection import fact_record_index

    prior_facts = fact_record_index(repository, parent_head)
    fact_digest_by_effect = {
        str(record.get("effect_id") or ""): digest
        for digest, record in prior_facts.items()
        if str(record.get("effect_id") or "")
    }
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}

    def merge_binding(
        *,
        namespace: IdentityNamespace | str,
        token: str,
        canonical: str,
        effect_ids: Iterable[str] = (),
        candidate_digests: Iterable[str] = (),
        fact_digests: Iterable[str] = (),
        approval_hashes: Iterable[str] = (),
        legacy_snapshot_digests: Iterable[str] = (),
    ) -> None:
        namespace_value = IdentityNamespace(namespace)
        canonical_name = str(canonical or "").strip()
        if not canonical_name:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_registry_canonical_empty"
            )
        key = (
            namespace_value.value,
            normalize_alias_key(token),
            canonical_name,
        )
        current = merged.get(key)
        if current is None:
            current = {
                "canonical_entity": canonical_name,
                "effect_ids": set(),
                "candidate_digests": set(),
                "fact_digests": set(),
                "approval_decision_hashes": set(),
                "legacy_snapshot_digests": set(),
            }
            merged[key] = current
        current["effect_ids"].update(effect_ids)
        current["candidate_digests"].update(candidate_digests)
        current["fact_digests"].update(fact_digests)
        current["approval_decision_hashes"].update(approval_hashes)
        current["legacy_snapshot_digests"].update(legacy_snapshot_digests)

    # The cutover snapshot is itself a content-addressed author/migration
    # approval. Seed all legacy aliases before reading v3 chapters.
    for fact_digest, record in sorted(prior_facts.items()):
        record_type = str(record.get("record_type") or "")
        snapshot_digest = str(record.get("legacy_snapshot_sha256") or "")
        if record_type == "legacy_identity":
            entity = record.get("entity")
            if not isinstance(entity, Mapping):
                raise EntityRegistryIntegrityError(
                    "canon_v3_legacy_identity_payload_invalid"
                )
            key = str(record.get("entity_key") or "").strip()
            canonical = str(
                entity.get("id") or entity.get("name") or key
            ).strip()
            explicit_namespace = str(entity.get("namespace") or "").strip()
            if explicit_namespace:
                try:
                    namespace = IdentityNamespace(explicit_namespace)
                except ValueError as exc:
                    raise EntityRegistryIntegrityError(
                        "canon_v3_legacy_identity_namespace_invalid"
                    ) from exc
                expected_type = {
                    IdentityNamespace.ACTOR: "角色",
                    IdentityNamespace.ITEM: "物品",
                    IdentityNamespace.LOCATION: "地点",
                }[namespace]
                if str(entity.get("type") or "") != expected_type:
                    raise EntityRegistryIntegrityError(
                        "canon_v3_legacy_identity_namespace_type_mismatch"
                    )
            else:
                # v1 snapshots remain readable only for recertification
                # diagnostics.  New cutovers always persist namespace as the
                # sole authority and derive type from it.
                legacy_type = str(entity.get("type") or "")
                if any(marker in legacy_type for marker in ("地点", "场所", "location")):
                    namespace = IdentityNamespace.LOCATION
                elif any(marker in legacy_type for marker in ("物品", "法宝", "item")):
                    namespace = IdentityNamespace.ITEM
                else:
                    namespace = IdentityNamespace.ACTOR
            aliases = entity.get("aliases") or ()
            if not isinstance(aliases, (list, tuple)) or any(
                not isinstance(alias, str) for alias in aliases
            ):
                raise EntityRegistryIntegrityError(
                    "canon_v3_legacy_identity_aliases_invalid"
                )
            tokens = {
                token
                for token in (
                    key,
                    str(entity.get("id") or ""),
                    str(entity.get("name") or ""),
                    *(str(alias) for alias in aliases),
                )
                if token.strip()
            }
            for token in tokens:
                merge_binding(
                    namespace=namespace,
                    token=token,
                    canonical=canonical,
                    fact_digests=(fact_digest,),
                    legacy_snapshot_digests=(snapshot_digest,),
                )
        elif record_type == "legacy_fact":
            fact = record.get("fact")
            if not isinstance(fact, Mapping):
                continue
            # New-project initialization represents the protagonist's explicit
            # name as a setup fact even before an entity row exists.
            if (
                str(fact.get("category") or "") == "character_state"
                and str(fact.get("field") or "") == "name"
            ):
                canonical = str(fact.get("value") or "").strip()
                if canonical:
                    merge_binding(
                        namespace=IdentityNamespace.ACTOR,
                        token=canonical,
                        canonical=canonical,
                        fact_digests=(fact_digest,),
                        legacy_snapshot_digests=(snapshot_digest,),
                    )

    for entry in manifest.get("chapters") or ():
        entry_chapter = int(entry.get("chapter") or 0)
        if target_chapter is not None and entry_chapter >= int(target_chapter):
            continue
        commit = repository.read_commit(str(entry.get("commit_hash") or ""))
        approvals = _latest_approved_candidates(repository, commit)
        effects_by_candidate: dict[str, list[Mapping[str, Any]]] = {}
        for raw_effect in commit.get("canon_effects") or ():
            if not isinstance(raw_effect, Mapping):
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_effect_not_mapping"
                )
            effect_candidate = str(raw_effect.get("candidate_digest") or "")
            effects_by_candidate.setdefault(effect_candidate, []).append(raw_effect)
            claim = raw_effect.get("claim")
            if not isinstance(claim, Mapping) or claim.get("kind") != (
                FactKind.ENTITY_OBSERVED.value
            ):
                continue
            effect_id = str(raw_effect.get("effect_id") or "")
            digest = str(raw_effect.get("candidate_digest") or "")
            approval_hashes = approvals.get(digest, ())
            if not approval_hashes:
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_effect_without_exact_human_approval:"
                    + digest
                )
            fact_digest = fact_digest_by_effect.get(effect_id)
            if not fact_digest:
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_effect_missing_fact_record:" + effect_id
                )
            canonical = str(
                claim.get("canonical_entity") or claim.get("entity") or ""
            ).strip()
            namespace = IdentityNamespace(
                claim.get("namespace") or IdentityNamespace.ACTOR.value
            )
            raw_aliases = claim.get("aliases") or ()
            if not isinstance(raw_aliases, (list, tuple)) or any(
                not isinstance(alias, str) for alias in raw_aliases
            ):
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_effect_aliases_invalid"
                )
            tokens = (
                canonical,
                str(claim.get("entity") or ""),
                *(str(alias) for alias in raw_aliases),
            )
            for token in tokens:
                if not token.strip():
                    continue
                merge_binding(
                    namespace=namespace,
                    token=token,
                    canonical=canonical,
                    effect_ids=(effect_id,),
                    candidate_digests=(digest,),
                    fact_digests=(fact_digest,),
                    approval_hashes=approval_hashes,
                )

        # A human-approved first use of an otherwise unknown identity is also
        # a stable registration. This covers people, holders, items and places
        # without silently treating a raw nickname as canonical.
        transaction = repository.read_transaction(
            str(commit.get("transaction_hash") or "")
        )
        prepared = transaction.get("prepared_transaction")
        resolutions = (
            prepared.get("entity_resolutions")
            if isinstance(prepared, Mapping)
            else None
        )
        if not isinstance(resolutions, list):
            resolutions = []
        for resolution in resolutions:
            if not isinstance(resolution, Mapping):
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_resolution_not_mapping"
                )
            status = str(resolution.get("status") or "")
            if status not in {
                EntityResolutionStatus.UNREGISTERED.value,
                EntityResolutionStatus.REGISTRATION.value,
                EntityResolutionStatus.PENDING_REGISTRATION.value,
            }:
                continue
            digest = str(resolution.get("candidate_digest") or "")
            approval_hashes = approvals.get(digest, ())
            candidate_effects = effects_by_candidate.get(digest, [])
            if not approval_hashes or not candidate_effects:
                continue
            effect_ids = tuple(
                sorted(str(effect.get("effect_id") or "") for effect in candidate_effects)
            )
            fact_digests = tuple(
                sorted(
                    fact_digest_by_effect[effect_id]
                    for effect_id in effect_ids
                    if effect_id in fact_digest_by_effect
                )
            )
            if len(fact_digests) != len(effect_ids):
                raise EntityRegistryIntegrityError(
                    "canon_v3_entity_resolution_missing_fact_record"
                )
            merge_binding(
                namespace=IdentityNamespace(
                    resolution.get("namespace") or IdentityNamespace.ACTOR.value
                ),
                token=str(resolution.get("raw_value") or ""),
                canonical=str(resolution.get("canonical_entity") or ""),
                effect_ids=effect_ids,
                candidate_digests=(digest,),
                fact_digests=fact_digests,
                approval_hashes=approval_hashes,
            )

    bindings = tuple(
        AliasBinding(
            namespace=IdentityNamespace(key[0]),
            alias_key=key[1],
            canonical_entity=key[2],
            effect_ids=tuple(sorted(value["effect_ids"])),
            candidate_digests=tuple(sorted(value["candidate_digests"])),
            fact_digests=tuple(sorted(value["fact_digests"])),
            approval_decision_hashes=tuple(
                sorted(value["approval_decision_hashes"])
            ),
            legacy_snapshot_digests=tuple(
                sorted(value["legacy_snapshot_digests"])
            ),
        )
        for key, value in sorted(merged.items())
    )
    return EntityRegistry(parent_head=parent_head, bindings=bindings)


_IDENTITY_FIELDS: dict[FactKind, dict[str, IdentityNamespace]] = {
    FactKind.CHARACTER_STATE_CHANGED: {"subject": IdentityNamespace.ACTOR},
    FactKind.RELATIONSHIP_CHANGED: {
        "subject": IdentityNamespace.ACTOR,
        "object": IdentityNamespace.ACTOR,
    },
    FactKind.POWER_BREAKTHROUGH: {"subject": IdentityNamespace.ACTOR},
    FactKind.ARTIFACT_OBTAINED: {
        "owner": IdentityNamespace.ACTOR,
        "artifact": IdentityNamespace.ITEM,
        "from_holder": IdentityNamespace.ACTOR,
    },
    FactKind.KNOWLEDGE_STATE_CHANGED: {"subject": IdentityNamespace.ACTOR},
    FactKind.PRESENCE_OBSERVED: {
        "subject": IdentityNamespace.ACTOR,
        "location": IdentityNamespace.LOCATION,
    },
    FactKind.CUSTODY_CHANGED: {
        "item": IdentityNamespace.ITEM,
        "from_holder": IdentityNamespace.ACTOR,
        "to_holder": IdentityNamespace.ACTOR,
    },
    FactKind.PROMISE_CREATED: {
        "promisor": IdentityNamespace.ACTOR,
        "promisee": IdentityNamespace.ACTOR,
    },
    FactKind.PROMISE_PAID_OFF: {"promisor": IdentityNamespace.ACTOR},
}


def _make_resolution(
    *,
    candidate: FactCandidate,
    digest: str,
    field: str,
    namespace: IdentityNamespace,
    raw_value: str,
    canonical_entity: str,
    status: EntityResolutionStatus,
    binding_digests: Iterable[str] = (),
) -> EntityResolution:
    payload = {
        "candidate_digest": digest,
        "candidate_id": candidate.candidate_id,
        "claim_kind": candidate.claim.kind,
        "field": field,
        "namespace": namespace.value,
        "raw_value": raw_value,
        "canonical_entity": canonical_entity,
        "status": status.value,
        "registry_binding_digests": sorted(set(binding_digests)),
    }
    return EntityResolution(
        **payload,
        resolution_digest=canonical_digest(payload),
    )


def _make_observation(
    *,
    candidate: FactCandidate,
    field: str,
    kind: ObservationKind,
    reason: str,
    prior_fact_digests: Iterable[str] = (),
) -> ReviewObservation:
    identity = canonical_digest(
        {
            "candidate_id": candidate.candidate_id,
            "field": field,
            "kind": kind.value,
            "reason": reason,
        }
    )[:24]
    return ReviewObservation(
        observation_id=f"entity-resolution-{identity}",
        candidate_id=candidate.candidate_id,
        kind=kind,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason=reason,
        prior_fact_digests=tuple(sorted(set(prior_fact_digests))),
    )


def _pending_binding_digest(
    *,
    namespace: IdentityNamespace,
    candidate_digest_value: str,
    alias_key: str,
    canonical_entity: str,
) -> str:
    return canonical_digest(
        {
            "schema_version": f"{ENTITY_RESOLUTION_SCHEMA}/pending-binding",
            "candidate_digest": candidate_digest_value,
            "namespace": namespace.value,
            "alias_key": alias_key,
            "canonical_entity": canonical_entity,
        }
    )


def plan_entity_resolutions(
    candidates: Iterable[FactCandidate],
    registry: EntityRegistry,
) -> EntityResolutionPlan:
    """Normalize actor/item/location identity fields within their namespace."""

    candidate_list = tuple(candidates)
    digests = {candidate.candidate_id: candidate_digest(candidate) for candidate in candidate_list}
    registration_targets: dict[tuple[str, str], set[str]] = {}
    registration_proofs: dict[tuple[str, str, str], str] = {}
    registration_candidates: dict[tuple[str, str], set[str]] = {}
    target_by_candidate: dict[str, str] = {}
    target_by_runtime_id: dict[str, str] = {}
    registration_namespace: dict[str, IdentityNamespace] = {}
    pending_target_namespace: dict[str, IdentityNamespace] = {}
    conflicting_candidates: set[str] = set()
    conflict_bindings: dict[str, set[str]] = {}
    conflict_prior_facts: dict[str, set[str]] = {}
    link_prior_facts: dict[str, set[str]] = {}

    # First determine the target identity of every current registration using
    # only the approved parent registry. Multiple existing identities in one
    # alias claim are a real conflict, never a first-match choice.
    for candidate in candidate_list:
        if candidate.claim.kind != FactKind.ENTITY_OBSERVED.value:
            continue
        digest = digests[candidate.candidate_id]
        claim = candidate.claim
        if claim.canonical_entity is not None:
            raise EntityRegistryConflict(
                "canon_v3_entity_canonical_entity_is_compiler_owned"
            )
        namespace = IdentityNamespace(claim.namespace)
        tokens = (claim.entity, *claim.aliases)
        existing = [
            binding
            for token in tokens
            for binding in registry.resolve_all(token, namespace)
        ]
        link_bindings = (
            registry.resolve_all(claim.link_to, namespace)
            if claim.link_to is not None
            else ()
        )
        link_targets = {
            binding.canonical_entity for binding in link_bindings
        }
        if claim.link_to is not None and len(link_targets) != 1:
            raise EntityRegistryConflict(
                "canon_v3_entity_link_target_not_unique_in_parent_registry:"
                + claim.link_to
            )
        if claim.new_instance and claim.link_to is not None:
            raise EntityRegistryConflict(
                "canon_v3_new_entity_instance_cannot_link_existing"
            )
        if link_bindings:
            link_prior_facts[digest] = {
                fact_digest
                for binding in (*link_bindings, *existing)
                for fact_digest in binding.fact_digests
            }
        targets = {binding.canonical_entity for binding in existing}
        if claim.new_instance:
            target = canonical_digest(
                {
                    "kind_family": "entity_instance",
                    "namespace": namespace.value,
                    "registration_candidate_digest": digest,
                }
            )
        elif link_targets:
            target = next(iter(link_targets))
        elif len(targets) > 1:
            conflicting_candidates.add(digest)
            conflict_bindings[digest] = {
                binding.binding_digest for binding in existing
            }
            conflict_prior_facts[digest] = {
                fact_digest
                for binding in existing
                for fact_digest in binding.fact_digests
            }
            target = claim.entity
        elif targets:
            target = next(iter(targets))
        else:
            target = claim.entity
        target_by_candidate[digest] = target
        target_by_runtime_id[candidate.candidate_id] = target
        registration_namespace[digest] = namespace
        pending_target_namespace[target] = namespace
        for token in tokens:
            key = (namespace.value, normalize_alias_key(token))
            registration_candidates.setdefault(key, set()).add(digest)
            registration_targets.setdefault(key, set()).add(target)
            registration_proofs[(key[0], key[1], target)] = _pending_binding_digest(
                namespace=namespace,
                candidate_digest_value=digest,
                alias_key=key[1],
                canonical_entity=target,
            )

    candidates_by_digest = {
        digests[candidate.candidate_id]: candidate
        for candidate in candidate_list
    }
    for key, targets in registration_targets.items():
        if len(targets) <= 1:
            continue
        participants = registration_candidates.get(key, set())
        if not participants or not all(
            bool(candidates_by_digest[digest].claim.new_instance)
            for digest in participants
        ):
            raise EntityRegistryConflict(
                "canon_v3_entity_registration_batch_conflict:"
                f"alias={key}:targets={','.join(sorted(targets))}"
            )

    normalized: list[FactCandidate] = []
    resolutions: list[EntityResolution] = []
    observations: list[ReviewObservation] = []

    for candidate in candidate_list:
        digest = digests[candidate.candidate_id]
        kind = FactKind(candidate.claim.kind)
        updates: dict[str, Any] = {}

        if kind is FactKind.ENTITY_OBSERVED:
            if candidate.identity_links:
                raise EntityRegistryConflict(
                    "canon_v3_entity_observed_uses_link_to_not_identity_links"
                )
            namespace = IdentityNamespace(candidate.claim.namespace)
            target = target_by_candidate[digest]
            updates["canonical_entity"] = target
            tokens = (("entity", candidate.claim.entity),) + tuple(
                (f"aliases.{index}", alias)
                for index, alias in enumerate(candidate.claim.aliases)
            )
            if digest in conflicting_candidates:
                reason = "当前实体别名提议同时指向多个已批准身份或与同批注册冲突"
                observations.append(
                    _make_observation(
                        candidate=candidate,
                        field="entity",
                        kind=ObservationKind.CONFIRMED_CONFLICT,
                        reason=reason,
                        prior_fact_digests=conflict_prior_facts.get(digest, ()),
                    )
                )
            elif candidate.claim.link_to is not None:
                observations.append(
                    _make_observation(
                        candidate=candidate,
                        field="link_to",
                        kind=ObservationKind.CHECKPOINT,
                        reason=(
                            f"将本章称呼 {candidate.claim.entity!r} 链接到 N-1 "
                            f"已批准身份 {target!r}"
                        ),
                        prior_fact_digests=link_prior_facts.get(digest, ()),
                    )
                )
            elif candidate.claim.new_instance:
                existing_for_name = [
                    binding
                    for token in (
                        candidate.claim.entity,
                        *candidate.claim.aliases,
                    )
                    for binding in registry.resolve_all(token, namespace)
                ]
                observations.append(
                    _make_observation(
                        candidate=candidate,
                        field="new_instance",
                        kind=ObservationKind.CHECKPOINT,
                        reason=(
                            f"将称呼 {candidate.claim.entity!r} 注册为独立新实例，"
                            "不会复用同名的既有实体"
                        ),
                        prior_fact_digests={
                            fact_digest
                            for binding in existing_for_name
                            for fact_digest in binding.fact_digests
                        },
                    )
                )
            for field, raw_value in tokens:
                bindings = registry.resolve_all(raw_value, namespace)
                target_bindings = [
                    binding
                    for binding in bindings
                    if binding.canonical_entity == target
                ]
                if digest in conflicting_candidates:
                    status = EntityResolutionStatus.CONFLICT
                    binding_digests = conflict_bindings.get(digest, ())
                elif target_bindings and not candidate.claim.new_instance:
                    status = EntityResolutionStatus.RESOLVED
                    binding_digests = tuple(
                        sorted(
                            binding.binding_digest
                            for binding in target_bindings
                        )
                    )
                else:
                    status = EntityResolutionStatus.REGISTRATION
                    binding_digests = (
                        registration_proofs[
                            (
                                namespace.value,
                                normalize_alias_key(raw_value),
                                target,
                            )
                        ],
                    )
                resolutions.append(
                    _make_resolution(
                        candidate=candidate,
                        digest=digest,
                        field=field,
                        namespace=namespace,
                        raw_value=raw_value,
                        canonical_entity=target,
                        status=status,
                        binding_digests=binding_digests,
                    )
                )
        else:
            identity_fields = _IDENTITY_FIELDS.get(kind, {})
            unknown_links = set(candidate.identity_links) - set(identity_fields)
            if unknown_links:
                raise EntityRegistryConflict(
                    "canon_v3_identity_link_field_invalid:"
                    + ",".join(sorted(unknown_links))
                )
            for field, namespace in identity_fields.items():
                raw = getattr(candidate.claim, field, None)
                if raw is None:
                    continue
                raw_value = str(raw)
                bindings = registry.resolve_all(raw_value, namespace)
                binding_targets = {
                    item.canonical_entity for item in bindings
                }
                key = (namespace.value, normalize_alias_key(raw_value))
                explicit_target = candidate.identity_links.get(field)
                if explicit_target is not None:
                    pending_candidate_id = (
                        explicit_target[len("candidate:") :]
                        if explicit_target.startswith("candidate:")
                        else ""
                    )
                    pending_target = target_by_runtime_id.get(
                        pending_candidate_id
                    )
                    if pending_target is None and explicit_target in {
                        target
                        for targets in registration_targets.values()
                        for target in targets
                    }:
                        pending_target = explicit_target
                    target_bindings = registry.resolve_all(
                        explicit_target, namespace
                    )
                    parent_targets = {
                        item.canonical_entity for item in target_bindings
                    }
                    if pending_target is not None:
                        pending_digest = digests.get(pending_candidate_id)
                        if (
                            (
                                pending_digest is not None
                                and registration_namespace.get(pending_digest)
                                != namespace
                            )
                            or pending_target_namespace.get(pending_target)
                            != namespace
                        ):
                            raise EntityRegistryConflict(
                                "canon_v3_identity_link_pending_namespace_mismatch"
                            )
                        canonical = pending_target
                    elif len(parent_targets) == 1:
                        canonical = next(iter(parent_targets))
                    else:
                        raise EntityRegistryConflict(
                            "canon_v3_identity_link_target_not_unique:"
                            f"field={field}:target={explicit_target}"
                        )
                    matching_raw = [
                        item
                        for item in bindings
                        if item.canonical_entity == canonical
                    ]
                    status = (
                        EntityResolutionStatus.PENDING_REGISTRATION
                        if pending_target is not None
                        else EntityResolutionStatus.RESOLVED
                        if matching_raw
                        else EntityResolutionStatus.REGISTRATION
                    )
                    selected = [*target_bindings, *bindings]
                    proof = registration_proofs.get(
                        (key[0], key[1], canonical)
                    )
                    binding_digests = tuple(
                        sorted(
                            {
                                *(item.binding_digest for item in selected),
                                *((proof,) if proof else ()),
                            }
                        )
                    )
                    observations.append(
                        _make_observation(
                            candidate=candidate,
                            field=field,
                            kind=ObservationKind.CHECKPOINT,
                            reason=(
                                f"身份字段 {field} 的正文称呼 {raw_value!r} "
                                f"显式绑定到 N-1 身份 {canonical!r}；"
                                f"同名备选={sorted(binding_targets)!r}"
                            ),
                            prior_fact_digests={
                                fact_digest
                                for item in (*target_bindings, *bindings)
                                for fact_digest in item.fact_digests
                            },
                        )
                    )
                elif len(
                    binding_targets | registration_targets.get(key, set())
                ) == 1:
                    canonical = next(
                        iter(binding_targets | registration_targets.get(key, set()))
                    )
                    if canonical in registration_targets.get(key, set()):
                        status = EntityResolutionStatus.PENDING_REGISTRATION
                        proof = registration_proofs[(key[0], key[1], canonical)]
                        binding_digests = (proof,)
                        observations.append(
                            _make_observation(
                                candidate=candidate,
                                field=field,
                                kind=ObservationKind.AMBIGUITY,
                                reason=(
                                    f"身份字段 {field} 依赖同批次尚未批准的实例注册"
                                ),
                            )
                        )
                    else:
                        status = EntityResolutionStatus.RESOLVED
                        binding_digests = tuple(
                            sorted(item.binding_digest for item in bindings)
                        )
                elif registration_targets.get(key):
                    raise EntityRegistryConflict(
                        "canon_v3_same_batch_identity_ambiguous_requires_identity_links:"
                        f"field={field}:value={raw_value}"
                    )
                elif len(binding_targets) == 1:
                    canonical = next(iter(binding_targets))
                    status = EntityResolutionStatus.RESOLVED
                    binding_digests = tuple(
                        sorted(item.binding_digest for item in bindings)
                    )
                elif len(binding_targets) > 1:
                    canonical = raw_value
                    status = EntityResolutionStatus.CONFLICT
                    binding_digests = tuple(
                        sorted(item.binding_digest for item in bindings)
                    )
                    observations.append(
                        _make_observation(
                            candidate=candidate,
                            field=field,
                            kind=ObservationKind.CONFIRMED_CONFLICT,
                            reason=(
                                f"身份字段 {field} 的称呼 {raw_value!r} 在 "
                                "N-1 同一命名空间内对应多个实例；必须通过 "
                                "identity_links 显式选择"
                            ),
                            prior_fact_digests={
                                fact_digest
                                for item in bindings
                                for fact_digest in item.fact_digests
                            },
                        )
                    )
                else:
                    # Unknown identities never become Canon by a machine-only
                    # first-match/default. Human approval of this exact use is
                    # the registration proof folded by the next transaction.
                    canonical = raw_value
                    status = EntityResolutionStatus.UNREGISTERED
                    binding_digests = ()
                    observations.append(
                        _make_observation(
                            candidate=candidate,
                            field=field,
                            kind=ObservationKind.AMBIGUITY,
                            reason=(
                                f"身份字段 {field} 的值 {raw_value!r} 尚未在 N-1 "
                                "批准的身份注册表中，需要人工确认首次注册"
                            ),
                        )
                    )
                updates[field] = canonical
                resolutions.append(
                    _make_resolution(
                        candidate=candidate,
                        digest=digest,
                        field=field,
                        namespace=namespace,
                        raw_value=raw_value,
                        canonical_entity=canonical,
                        status=status,
                        binding_digests=binding_digests,
                    )
                )

        normalized_claim = candidate.claim.model_copy(update=updates)
        normalized.append(candidate.model_copy(update={"claim": normalized_claim}))

    unique_observations = {
        canonical_digest(
            observation.model_dump(mode="json", exclude={"observation_id"})
        ): observation
        for observation in observations
    }
    return EntityResolutionPlan(
        registry=registry,
        normalized_candidates=tuple(normalized),
        candidate_digest_by_id=tuple(sorted(digests.items())),
        resolutions=tuple(
            sorted(resolutions, key=lambda item: item.resolution_digest)
        ),
        observations=tuple(
            unique_observations[key] for key in sorted(unique_observations)
        ),
    )


def bind_entity_registry_to_transaction(
    prepared: PreparedTransaction,
    plan: EntityResolutionPlan,
) -> PreparedTransaction:
    """Bind normalized claims and exact registry inputs into a transaction."""

    if prepared.parent_head != plan.registry.parent_head:
        raise EntityRegistryIntegrityError(
            "canon_v3_entity_registry_parent_head_mismatch"
        )
    normalized = plan.candidates_by_digest
    resolution_digests: dict[str, tuple[str, ...]] = {}
    for resolution in plan.resolutions:
        resolution_digests.setdefault(resolution.candidate_digest, ())
        resolution_digests[resolution.candidate_digest] = tuple(
            sorted(
                {
                    *resolution_digests[resolution.candidate_digest],
                    resolution.resolution_digest,
                }
            )
        )

    # Import lazily so compiler.py remains independent of the registry module
    # and its concurrently evolving source-order code is untouched.
    from .compiler import _fact_slot_payload, claim_with_semantic_slot

    effects: list[CanonEffect] = []
    for effect in prepared.effects:
        candidate = normalized.get(effect.candidate_digest)
        if candidate is None:
            raise EntityRegistryIntegrityError(
                "canon_v3_entity_resolution_candidate_missing"
            )
        # Entity normalization must happen before deterministic slot creation.
        # Otherwise an alias in ``promisor``/``subject`` would create a second
        # slot for the same approved identity.  Recompute compiler-owned slot
        # metadata from the normalized claim while preserving an explicit slot.
        claim_source = claim_with_semantic_slot(
            candidate.claim,
            instance_seed=effect.candidate_digest,
        )
        claim_payload = claim_source.model_dump(mode="python")
        if "aliases" in claim_payload:
            claim_payload["aliases"] = tuple(sorted(claim_payload["aliases"]))
        claim = type(candidate.claim).model_validate(claim_payload)
        normalized_candidate = candidate.model_copy(update={"claim": claim})
        candidate_resolution_digests = resolution_digests.get(
            effect.candidate_digest, ()
        )
        effect_id = canonical_digest(
            {
                "candidate_digest": effect.candidate_digest,
                "parent_head": prepared.parent_head,
                "effect": "prepare",
                "entity_registry_digest": plan.registry.registry_digest,
                "entity_resolution_digests": list(
                    candidate_resolution_digests
                ),
            }
        )
        effects.append(
            CanonEffect(
                effect_id=effect_id,
                source_order=effect.source_order,
                candidate_digest=effect.candidate_digest,
                fact_key=canonical_digest(_fact_slot_payload(normalized_candidate)),
                claim=claim,
                source_digests=effect.source_digests,
                support_map=effect.support_map,
            )
        )
    effects.sort(key=lambda item: (item.source_order, item.effect_id))

    payload = prepared.model_dump(mode="json", exclude={"transaction_digest"})
    payload["effects"] = [effect.model_dump(mode="json") for effect in effects]
    payload["entity_registry_digest"] = plan.registry.registry_digest
    payload["entity_resolutions"] = [
        resolution.model_dump(mode="json") for resolution in plan.resolutions
    ]
    payload["transaction_digest"] = canonical_digest(payload)
    return PreparedTransaction.model_validate(payload)


__all__ = [
    "AliasBinding",
    "EntityRegistry",
    "EntityRegistryConflict",
    "EntityRegistryError",
    "EntityRegistryIntegrityError",
    "EntityResolutionPlan",
    "bind_entity_registry_to_transaction",
    "build_approved_entity_registry",
    "normalize_alias_key",
    "plan_entity_resolutions",
]

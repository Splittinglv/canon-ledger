#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic, side-effect-free compiler for prepared canon transactions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .evidence import (
    candidate_digest,
    source_digest,
    validate_candidate_evidence,
)
from .schema import (
    COMPILER_VERSION,
    POLICY_VERSION,
    SCHEMA_VERSION,
    CanonEffect,
    FactCandidate,
    FactKind,
    ObservationKind,
    PreparedTransaction,
    RequirementMode,
    ReviewAction,
    ReviewLevel,
    ReviewObservation,
    ReviewRequirement,
    ScanAttestation,
    TransactionState,
    canonical_digest,
)


class CompileError(ValueError):
    pass


LEVEL_STRENGTH = {
    ReviewLevel.AUDIT_ONLY: 0,
    ReviewLevel.ADVISORY: 1,
    ReviewLevel.HUMAN_REQUIRED: 2,
}

# v3 deliberately errs toward review at structurally important nodes.  The
# model may add a requirement but can never lower one of these policy floors.
CHECKPOINT_KINDS = frozenset(
    {
        FactKind.CHARACTER_STATE_CHANGED,
        FactKind.RELATIONSHIP_CHANGED,
        FactKind.WORLD_RULE_REVEALED,
        FactKind.WORLD_RULE_BROKEN,
        FactKind.POWER_BREAKTHROUGH,
        FactKind.ARTIFACT_OBTAINED,
        # A quote can contain two names without proving that one is an alias
        # of the other.  New identity/alias registration therefore remains a
        # checkpoint until a stronger entity resolver can prove the relation.
        FactKind.ENTITY_OBSERVED,
        FactKind.TIMELINE_OBSERVED,
        FactKind.KNOWLEDGE_STATE_CHANGED,
        FactKind.CUSTODY_CHANGED,
        FactKind.PROMISE_CREATED,
        FactKind.PROMISE_PAID_OFF,
    }
)


# Narrative order must come from the part of a claim that expresses the new
# value/outcome, not from a shared subject span.  For example, two presence
# observations for ``苏月`` may both cite the same early subject span while
# their location spans occur at different positions in the chapter.
_ORDER_FIELDS: dict[FactKind, tuple[str, ...]] = {
    FactKind.CHARACTER_STATE_CHANGED: ("after",),
    FactKind.RELATIONSHIP_CHANGED: ("after",),
    FactKind.WORLD_RULE_REVEALED: ("rule",),
    FactKind.WORLD_RULE_BROKEN: ("violation",),
    FactKind.POWER_BREAKTHROUGH: ("after",),
    FactKind.ARTIFACT_OBTAINED: ("owner", "artifact"),
    FactKind.ENTITY_OBSERVED: ("entity", "aliases"),
    FactKind.TIMELINE_OBSERVED: ("time_anchor",),
    FactKind.KNOWLEDGE_STATE_CHANGED: ("knowledge", "state"),
    FactKind.PRESENCE_OBSERVED: ("location", "presence"),
    FactKind.CUSTODY_CHANGED: ("to_holder",),
    FactKind.PROMISE_CREATED: ("promise",),
    FactKind.PROMISE_PAID_OFF: ("outcome",),
    FactKind.OPEN_LOOP_CREATED: ("loop",),
    FactKind.OPEN_LOOP_CLOSED: ("resolution",),
}


def _kind(claim: Any) -> FactKind:
    return FactKind(claim.kind)


def _strongest(levels: Iterable[ReviewLevel]) -> ReviewLevel:
    return max(levels, key=LEVEL_STRENGTH.__getitem__)


def _observation_payload(
    observation: ReviewObservation, *, bound_candidate_digest: str
) -> dict[str, Any]:
    return {
        "candidate_digest": bound_candidate_digest,
        "kind": observation.kind.value,
        "level": observation.level.value,
        "reason": observation.reason,
        "prior_fact_digests": sorted(observation.prior_fact_digests),
    }


def _observation_digest(
    observation: ReviewObservation, *, bound_candidate_digest: str
) -> str:
    return canonical_digest(
        _observation_payload(
            observation, bound_candidate_digest=bound_candidate_digest
        )
    )


def _scan_attestation_digest(attestation: ScanAttestation) -> str:
    payload = attestation.model_dump(mode="json", exclude={"attestation_id"})
    payload["dimensions"] = sorted(payload["dimensions"])
    payload["checked_candidate_digests"] = sorted(
        payload["checked_candidate_digests"]
    )
    return canonical_digest(payload)


def _fact_slot_payload(candidate: FactCandidate) -> dict[str, Any]:
    claim = candidate.claim
    kind = _kind(claim)
    data = claim.model_dump(mode="json", exclude_none=True)
    if kind == FactKind.CHARACTER_STATE_CHANGED:
        slot = {"slot_id": data["slot_id"]}
    elif kind == FactKind.RELATIONSHIP_CHANGED:
        slot = {"subject": data["subject"], "object": data["object"]}
    elif kind == FactKind.WORLD_RULE_REVEALED:
        slot = {"slot_id": data["slot_id"]}
    elif kind == FactKind.WORLD_RULE_BROKEN:
        slot = {"slot_id": data["slot_id"]}
    elif kind == FactKind.POWER_BREAKTHROUGH:
        slot = {"slot_id": data["slot_id"]}
    elif kind == FactKind.ARTIFACT_OBTAINED:
        slot = {"item": data["artifact"]}
    elif kind == FactKind.ENTITY_OBSERVED:
        slot = {
            "namespace": data["namespace"],
            "entity": data.get("canonical_entity") or data["entity"],
        }
    elif kind == FactKind.TIMELINE_OBSERVED:
        slot = {"slot_id": data["slot_id"]}
    elif kind == FactKind.KNOWLEDGE_STATE_CHANGED:
        slot = {"slot_id": data["slot_id"]}
    elif kind == FactKind.PRESENCE_OBSERVED:
        slot = {"subject": data["subject"]}
    elif kind == FactKind.CUSTODY_CHANGED:
        slot = {"item": data["item"]}
    elif kind in {FactKind.PROMISE_CREATED, FactKind.PROMISE_PAID_OFF}:
        slot = {"slot_id": data["slot_id"]}
    elif kind in {FactKind.OPEN_LOOP_CREATED, FactKind.OPEN_LOOP_CLOSED}:
        slot = {"slot_id": data["slot_id"]}
    else:  # pragma: no cover - closed FactKind makes this defensive only
        raise CompileError(f"unsupported fact kind: {kind.value}")
    return {"kind_family": _fact_family(kind), "slot": slot}


def default_semantic_slot_id(
    claim: Any, *, instance_seed: str | None = None
) -> str | None:
    """Return the deterministic slot for a newly introduced semantic fact.

    A caller may copy an existing ``slot_id`` to update a fact whose display
    wording changed.  When no explicit identity is supplied, only these
    canonical inputs may create one; the service separately rejects arbitrary
    unknown caller-supplied slots.
    """

    kind = _kind(claim)
    data = claim.model_dump(mode="python")
    if kind == FactKind.CHARACTER_STATE_CHANGED:
        return canonical_digest(
            {
                "kind_family": "character_state",
                "subject": data["subject"],
                "field": data["attribute"],
            }
        )
    if kind == FactKind.POWER_BREAKTHROUGH:
        return canonical_digest(
            {
                "kind_family": "character_state",
                "subject": data["subject"],
                "field": data.get("system") or "realm",
            }
        )
    if kind == FactKind.WORLD_RULE_REVEALED:
        if not data.get("rule"):
            return None
        return canonical_digest(
            {"kind_family": "world_rule", "rule": data["rule"]}
        )
    if kind in {
        FactKind.WORLD_RULE_BROKEN,
        FactKind.PROMISE_CREATED,
        FactKind.OPEN_LOOP_CREATED,
        FactKind.TIMELINE_OBSERVED,
    }:
        if kind == FactKind.TIMELINE_OBSERVED and not data.get("event"):
            return None
        if not instance_seed:
            return None
        return canonical_digest(
            {
                "kind_family": (
                    "rule_violation"
                    if kind == FactKind.WORLD_RULE_BROKEN
                    else _fact_family(kind)
                ),
                "instance_candidate_digest": instance_seed,
            }
        )
    if kind in {FactKind.PROMISE_PAID_OFF, FactKind.OPEN_LOOP_CLOSED}:
        return None
    if kind == FactKind.KNOWLEDGE_STATE_CHANGED:
        if not data.get("knowledge"):
            return None
        return canonical_digest(
            {
                "kind_family": "knowledge",
                "subject": data["subject"],
                "knowledge": data["knowledge"],
            }
        )
    return None


def claim_with_semantic_slot(
    claim: Any, *, instance_seed: str | None = None
) -> Any:
    """Canonicalize compiler-owned slot metadata without changing evidence."""

    payload = claim.model_dump(mode="python")
    if "slot_id" not in payload or payload.get("slot_id"):
        return claim
    slot_id = default_semantic_slot_id(claim, instance_seed=instance_seed)
    if slot_id is None:
        kind = _kind(claim)
        if kind == FactKind.PROMISE_PAID_OFF:
            raise CompileError("promise payoff requires an existing slot_id")
        if kind == FactKind.OPEN_LOOP_CLOSED:
            raise CompileError("open-loop closure requires an existing slot_id")
        if kind == FactKind.WORLD_RULE_BROKEN:
            raise CompileError("world-rule violation requires candidate-bound identity")
        if kind == FactKind.KNOWLEDGE_STATE_CHANGED:
            raise CompileError("knowledge update requires text or an existing slot_id")
        if kind == FactKind.TIMELINE_OBSERVED:
            raise CompileError("timeline update requires event text or an existing slot_id")
        raise CompileError(f"{kind.value} requires an existing slot_id")
    payload["slot_id"] = slot_id
    return type(claim).model_validate(payload)


def _fact_family(kind: FactKind) -> str:
    if kind in {FactKind.CHARACTER_STATE_CHANGED, FactKind.POWER_BREAKTHROUGH}:
        return "character_state"
    if kind == FactKind.WORLD_RULE_REVEALED:
        return "world_rule"
    if kind == FactKind.WORLD_RULE_BROKEN:
        return "rule_violation"
    if kind in {FactKind.PROMISE_CREATED, FactKind.PROMISE_PAID_OFF}:
        return "promise"
    if kind in {FactKind.OPEN_LOOP_CREATED, FactKind.OPEN_LOOP_CLOSED}:
        return "open_loop"
    if kind == FactKind.KNOWLEDGE_STATE_CHANGED:
        return "knowledge"
    if kind == FactKind.TIMELINE_OBSERVED:
        return "timeline"
    if kind in {FactKind.ARTIFACT_OBTAINED, FactKind.CUSTODY_CHANGED}:
        return "custody"
    return kind.value


def _build_effect(
    candidate: FactCandidate, *, digest: str, parent_head: str
) -> CanonEffect:
    sources = {source.source_id: source_digest(source) for source in candidate.sources}
    support_map = {
        field: tuple(sorted(sources[source_id] for source_id in source_ids))
        for field, source_ids in sorted(candidate.support_map.items())
    }
    sources_by_id = {source.source_id: source for source in candidate.sources}
    # Use the latest start among result-bearing support fields.  This marks the
    # point by which the whole new value has appeared and prevents a shared,
    # early subject citation from collapsing multiple events onto one offset.
    result_offsets = [
        int(source.start)
        for field in _ORDER_FIELDS[_kind(candidate.claim)]
        for source_id in candidate.support_map.get(field, ())
        if getattr((source := sources_by_id[source_id]), "source_type", "")
        == "manuscript_span"
    ]
    if not result_offsets:
        # Author axioms have no manuscript position.  A lone axiom-derived
        # value is deterministic; competing values for the same slot are
        # rejected below as ambiguous instead of being ordered by hash.
        result_offsets = [
            int(source.start)
            for source in candidate.sources
            if getattr(source, "source_type", "") == "manuscript_span"
        ]
    source_order = max(result_offsets, default=0)
    effect_id = canonical_digest(
        {
            "candidate_digest": digest,
            "parent_head": parent_head,
            "effect": "prepare",
        }
    )
    normalized_claim = claim_with_semantic_slot(
        candidate.claim, instance_seed=digest
    )
    claim_payload = normalized_claim.model_dump(mode="python")
    if "aliases" in claim_payload:
        claim_payload["aliases"] = tuple(sorted(claim_payload["aliases"]))
    normalized_claim = type(normalized_claim).model_validate(claim_payload)
    normalized_candidate = candidate.model_copy(update={"claim": normalized_claim})
    return CanonEffect(
        effect_id=effect_id,
        source_order=source_order,
        candidate_digest=digest,
        fact_key=canonical_digest(_fact_slot_payload(normalized_candidate)),
        claim=normalized_claim,
        source_digests=tuple(sorted(sources.values())),
        support_map=support_map,
    )


def _build_requirement(
    *,
    digest: str,
    kind: FactKind,
    observations: list[ReviewObservation],
) -> ReviewRequirement | None:
    checkpoint = kind in CHECKPOINT_KINDS or any(
        observation.kind == ObservationKind.CHECKPOINT
        for observation in observations
    )
    factual_observations = [
        observation
        for observation in observations
        if observation.kind not in {ObservationKind.STYLE, ObservationKind.PROSE}
    ]
    if not checkpoint and not factual_observations:
        return None

    levels = [observation.level for observation in factual_observations]
    if checkpoint:
        levels.append(ReviewLevel.HUMAN_REQUIRED)
    level = _strongest(levels)
    rewrite_required = any(
        observation.kind == ObservationKind.CONFIRMED_CONFLICT
        for observation in factual_observations
    )
    if rewrite_required:
        level = ReviewLevel.HUMAN_REQUIRED
    mode = RequirementMode.REWRITE if rewrite_required else RequirementMode.REVIEW

    reason_codes = {
        f"observation:{observation.kind.value}"
        for observation in factual_observations
    }
    if checkpoint:
        reason_codes.add(f"checkpoint:{kind.value}")
    observation_digests = tuple(
        sorted(
            {
                _observation_digest(
                    observation, bound_candidate_digest=digest
                )
                for observation in factual_observations
            }
        )
    )
    prior_fact_digests = tuple(
        sorted(
            {
                fact_digest
                for observation in factual_observations
                for fact_digest in observation.prior_fact_digests
            }
        )
    )

    if rewrite_required:
        actions = (ReviewAction.REWRITE,)
    elif checkpoint:
        actions = (ReviewAction.APPROVE, ReviewAction.REWRITE)
    elif level == ReviewLevel.HUMAN_REQUIRED:
        actions = (
            ReviewAction.APPROVE,
            ReviewAction.OMIT,
            ReviewAction.CORRECT,
            ReviewAction.REWRITE,
        )
    else:
        actions = (ReviewAction.DISMISS,)

    case_key = canonical_digest(
        {
            "candidate_digest": digest,
            "mode": mode.value,
            "checkpoint": checkpoint,
            "prior_fact_digests": prior_fact_digests,
        }
    )
    return ReviewRequirement(
        case_key=case_key,
        candidate_digest=digest,
        level=level,
        mode=mode,
        checkpoint=checkpoint,
        reason_codes=tuple(sorted(reason_codes)),
        observation_digests=observation_digests,
        prior_fact_digests=prior_fact_digests,
        allowed_actions=actions,
    )


def compile_transaction(
    candidates: Iterable[FactCandidate],
    observations: Iterable[ReviewObservation],
    parent_head: str,
    *,
    scan_attestations: Iterable[ScanAttestation] = (),
) -> PreparedTransaction:
    """Compile proposals into a deterministic, non-active transaction.

    The signature intentionally has no delta/timeline/entity input.  Those are
    reducer outputs represented by ``CanonEffect`` only.
    """

    by_runtime_id: dict[str, str] = {}
    by_digest: dict[str, FactCandidate] = {}
    for candidate in candidates:
        validate_candidate_evidence(candidate)
        digest = candidate_digest(candidate)
        previous = by_runtime_id.get(candidate.candidate_id)
        if previous is not None and previous != digest:
            raise CompileError(
                f"candidate_id {candidate.candidate_id!r} identifies multiple facts"
            )
        by_runtime_id[candidate.candidate_id] = digest
        by_digest.setdefault(digest, candidate)

    observations_by_digest: dict[str, list[ReviewObservation]] = defaultdict(list)
    for observation in observations:
        if observation.kind in {ObservationKind.STYLE, ObservationKind.PROSE}:
            continue
        assert observation.candidate_id is not None  # schema invariant
        digest = by_runtime_id.get(observation.candidate_id)
        if digest is None:
            raise CompileError(
                f"observation {observation.observation_id!r} references unknown candidate "
                f"{observation.candidate_id!r}"
            )
        observations_by_digest[digest].append(observation)

    candidate_digests = tuple(sorted(by_digest))
    effects = tuple(
        sorted(
            (
                _build_effect(
                    by_digest[digest], digest=digest, parent_head=parent_head
                )
                for digest in candidate_digests
            ),
            key=lambda effect: (effect.source_order, effect.effect_id),
        )
    )
    order_owner: dict[tuple[str, int], str] = {}
    for effect in effects:
        order_key = (effect.fact_key, effect.source_order)
        previous = order_owner.get(order_key)
        if previous is not None and previous != effect.candidate_digest:
            raise CompileError(
                "ambiguous narrative order for one fact slot at manuscript byte "
                f"{effect.source_order}; refine the result-field evidence spans "
                "and request human intervention instead of resolving by hash"
            )
        order_owner[order_key] = effect.candidate_digest
    requirements = tuple(
        sorted(
            (
                requirement
                for digest in candidate_digests
                if (
                    requirement := _build_requirement(
                        digest=digest,
                        kind=_kind(by_digest[digest].claim),
                        observations=observations_by_digest.get(digest, []),
                    )
                )
                is not None
            ),
            key=lambda requirement: requirement.case_key,
        )
    )
    attestation_digest_set: set[str] = set()
    candidate_digest_set = set(candidate_digests)
    for attestation in scan_attestations:
        unknown = set(attestation.checked_candidate_digests) - candidate_digest_set
        if unknown:
            raise CompileError(
                "scan attestation references candidates outside the transaction: "
                + ", ".join(sorted(unknown))
            )
        attestation_digest_set.add(_scan_attestation_digest(attestation))
    attestation_digests = tuple(sorted(attestation_digest_set))

    state = TransactionState.READY
    if any(req.mode == RequirementMode.REWRITE for req in requirements):
        state = TransactionState.REWRITE_REQUIRED
    elif any(req.level == ReviewLevel.HUMAN_REQUIRED for req in requirements):
        state = TransactionState.AWAITING_HUMAN

    payload = {
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "policy_version": POLICY_VERSION,
        "parent_head": parent_head,
        "state": state.value,
        "candidate_digests": candidate_digests,
        "effects": [effect.model_dump(mode="json") for effect in effects],
        "requirements": [
            requirement.model_dump(mode="json") for requirement in requirements
        ],
        "scan_attestation_digests": attestation_digests,
    }
    transaction_digest = canonical_digest(payload)
    return PreparedTransaction(
        transaction_digest=transaction_digest,
        parent_head=parent_head,
        state=state,
        candidate_digests=candidate_digests,
        effects=effects,
        requirements=requirements,
        scan_attestation_digests=attestation_digests,
    )

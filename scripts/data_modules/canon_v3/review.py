#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure v3 human-review domain model.

This module deliberately has no dependency on the v2 review queue, commit
files, or projection writers.  A review result is always derived from an
immutable candidate set, the current merged cases, and append-only decisions.
Nothing is patched in place.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


REVIEW_SCHEMA_VERSION = "canon-v3/review/v1"
UNBOUND_ENTITY_REGISTRY_DIGEST = "0" * 64


class ReviewDomainError(ValueError):
    """Base error for an invalid v3 review transition."""


class CaseMergeConflict(ReviewDomainError):
    """Two observations claim the same case key with incompatible identity."""


class InvalidDecision(ReviewDomainError):
    """A decision is not legal for the case it targets."""


class DecisionConflict(ReviewDomainError):
    """The decision ledger has two incompatible current heads."""


class ReviewLevel(str, Enum):
    AUDIT_ONLY = "audit_only"
    ADVISORY = "advisory"
    HUMAN_REQUIRED = "human_required"
    # Domain-facing spelling used throughout this module.  It aliases the
    # storage/compiler spelling instead of creating a fourth lattice value.
    REQUIRED = "human_required"


_LEVEL_RANK = {
    ReviewLevel.AUDIT_ONLY: 0,
    ReviewLevel.ADVISORY: 1,
    ReviewLevel.HUMAN_REQUIRED: 2,
}


class ReviewCaseKind(str, Enum):
    CHECKPOINT = "checkpoint"
    AMBIGUITY = "ambiguity"
    UNBOUND = "unbound"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    OMIT = "omit"
    CORRECT = "correct"
    REWRITE = "rewrite"
    NO_CONFLICT = "no_conflict"
    DISMISS = "dismiss"


ACTION_MATRIX: Mapping[ReviewCaseKind, frozenset[ReviewAction]] = {
    ReviewCaseKind.CHECKPOINT: frozenset(
        {ReviewAction.APPROVE, ReviewAction.REWRITE}
    ),
    ReviewCaseKind.AMBIGUITY: frozenset(
        {
            ReviewAction.APPROVE,
            ReviewAction.OMIT,
            ReviewAction.CORRECT,
            ReviewAction.REWRITE,
        }
    ),
    ReviewCaseKind.UNBOUND: frozenset(
        {ReviewAction.NO_CONFLICT, ReviewAction.REWRITE}
    ),
}


class WorkflowState(str, Enum):
    READY = "ready"
    AWAITING_HUMAN = "awaiting_human"
    RECOMPILE_REQUIRED = "recompile_required"
    REWRITE_REQUIRED = "rewrite_required"


def _fallback_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _fallback_jsonable(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _fallback_jsonable(model_dump(mode="json"))
        except TypeError:
            return _fallback_jsonable(model_dump())
    if isinstance(value, Mapping):
        return {
            str(key): _fallback_jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        converted = [_fallback_jsonable(item) for item in value]
        return sorted(
            converted,
            key=lambda item: json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    if isinstance(value, (tuple, list)):
        return [_fallback_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    """Use the v3 schema serializer when available, with a local fallback.

    Keeping the import lazy prevents a schema/compiler/review import cycle.
    """

    try:
        from . import schema as v3_schema

        schema_serializer = getattr(v3_schema, "canonical_json", None)
        if callable(schema_serializer):
            return str(schema_serializer(value))
    except (ImportError, AttributeError):
        pass
    return json.dumps(
        _fallback_jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_digest(value: Any) -> str:
    """Return the shared canonical SHA-256 digest for a domain value."""

    try:
        from . import schema as v3_schema

        schema_digest = getattr(v3_schema, "canonical_digest", None)
        if callable(schema_digest):
            return str(schema_digest(value))
    except (ImportError, AttributeError):
        pass
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_strings(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _coerce_level(value: ReviewLevel | str) -> ReviewLevel:
    if isinstance(value, ReviewLevel):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"audit", "audit-only"}:
        normalized = ReviewLevel.AUDIT_ONLY.value
    if normalized in {"human_required", "human-required"}:
        normalized = ReviewLevel.REQUIRED.value
    return ReviewLevel(normalized)


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """All mutable-world inputs to which an author decision is bound."""

    chapter: int
    chapter_digest: str
    candidate_digest: str
    evidence_digests: tuple[str, ...]
    source_digests: tuple[str, ...]
    parent_head: str
    prior_fact_hashes: tuple[str, ...]
    policy_version: str
    transaction_digest: str
    effect_digests: tuple[str, ...]
    entity_registry_digest: str = UNBOUND_ENTITY_REGISTRY_DIGEST
    entity_resolution_digests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "chapter", int(self.chapter))
        if self.chapter < 1:
            raise ReviewDomainError("review_context_requires_positive_chapter")
        for name in (
            "chapter_digest",
            "candidate_digest",
            "parent_head",
            "policy_version",
            "transaction_digest",
            "entity_registry_digest",
        ):
            object.__setattr__(self, name, str(getattr(self, name) or "").strip())
        object.__setattr__(
            self, "evidence_digests", _normalized_strings(self.evidence_digests)
        )
        object.__setattr__(
            self, "source_digests", _normalized_strings(self.source_digests)
        )
        object.__setattr__(
            self, "prior_fact_hashes", _normalized_strings(self.prior_fact_hashes)
        )
        object.__setattr__(
            self, "effect_digests", _normalized_strings(self.effect_digests)
        )
        object.__setattr__(
            self,
            "entity_resolution_digests",
            _normalized_strings(self.entity_resolution_digests),
        )
        if not self.chapter_digest:
            raise ReviewDomainError("review_context_requires_chapter_digest")
        if not self.parent_head:
            raise ReviewDomainError("review_context_requires_parent_head")
        if not self.policy_version:
            raise ReviewDomainError("review_context_requires_policy_version")
        if not self.transaction_digest:
            raise ReviewDomainError("review_context_requires_transaction_digest")
        if (
            len(self.entity_registry_digest) != 64
            or any(
                char not in "0123456789abcdef"
                for char in self.entity_registry_digest
            )
        ):
            raise ReviewDomainError("review_context_entity_registry_digest_invalid")

    def payload(self) -> dict[str, Any]:
        payload = {
            "chapter": self.chapter,
            "chapter_digest": self.chapter_digest,
            "candidate_digest": self.candidate_digest,
            "evidence_digests": list(self.evidence_digests),
            "source_digests": list(self.source_digests),
            "parent_head": self.parent_head,
            "prior_fact_hashes": list(self.prior_fact_hashes),
            "policy_version": self.policy_version,
            "transaction_digest": self.transaction_digest,
            "effect_digests": list(self.effect_digests),
        }
        if (
            self.entity_registry_digest != UNBOUND_ENTITY_REGISTRY_DIGEST
            or self.entity_resolution_digests
        ):
            payload["entity_registry_digest"] = self.entity_registry_digest
            payload["entity_resolution_digests"] = list(
                self.entity_resolution_digests
            )
        return payload


@dataclass(frozen=True, slots=True)
class ReviewCase:
    """A normalized review case after policy observations are collected."""

    case_key: str
    kind: ReviewCaseKind
    level: ReviewLevel
    context: DecisionContext
    candidate_id: str = ""
    reasons: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()
    trigger_ids: tuple[str, ...] = ()
    requires_rewrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_key", str(self.case_key or "").strip())
        object.__setattr__(self, "kind", ReviewCaseKind(self.kind))
        object.__setattr__(self, "level", _coerce_level(self.level))
        object.__setattr__(self, "candidate_id", str(self.candidate_id or "").strip())
        object.__setattr__(self, "reasons", _normalized_strings(self.reasons))
        object.__setattr__(
            self, "observation_ids", _normalized_strings(self.observation_ids)
        )
        object.__setattr__(self, "trigger_ids", _normalized_strings(self.trigger_ids))
        object.__setattr__(self, "requires_rewrite", bool(self.requires_rewrite))
        if not self.case_key:
            raise ReviewDomainError("review_case_requires_case_key")
        if self.kind is not ReviewCaseKind.UNBOUND and not self.context.candidate_digest:
            raise ReviewDomainError("bound_review_case_requires_candidate_digest")
        if self.kind is ReviewCaseKind.UNBOUND and self.context.candidate_digest:
            raise ReviewDomainError("unbound_review_case_forbids_candidate_digest")

    @property
    def allowed_actions(self) -> frozenset[ReviewAction]:
        if self.requires_rewrite:
            return frozenset({ReviewAction.REWRITE})
        if self.level is not ReviewLevel.HUMAN_REQUIRED:
            # Advisory/audit cases are presentation-only.  They can be hidden
            # without gaining authority to change, omit, or approve canon.
            return frozenset({ReviewAction.DISMISS})
        return ACTION_MATRIX[self.kind]

    @property
    def target_digest(self) -> str:
        """Digest only decision semantics, never presentation text."""

        return stable_digest(
            {
                "schema": REVIEW_SCHEMA_VERSION,
                "case_key": self.case_key,
                "kind": self.kind.value,
                "level": self.level.value,
                "requires_rewrite": self.requires_rewrite,
                "context": self.context.payload(),
            }
        )


def _merge_context(left: DecisionContext, right: DecisionContext) -> DecisionContext:
    identity_fields = (
        "chapter",
        "chapter_digest",
        "candidate_digest",
        "parent_head",
        "policy_version",
        "transaction_digest",
    )
    conflicts = [
        name for name in identity_fields if getattr(left, name) != getattr(right, name)
    ]
    if conflicts:
        raise CaseMergeConflict(
            "review_case_context_conflict:" + ",".join(sorted(conflicts))
        )
    return DecisionContext(
        chapter=left.chapter,
        chapter_digest=left.chapter_digest,
        candidate_digest=left.candidate_digest,
        evidence_digests=left.evidence_digests + right.evidence_digests,
        source_digests=left.source_digests + right.source_digests,
        parent_head=left.parent_head,
        prior_fact_hashes=left.prior_fact_hashes + right.prior_fact_hashes,
        policy_version=left.policy_version,
        transaction_digest=left.transaction_digest,
        effect_digests=left.effect_digests + right.effect_digests,
    )


def merge_review_case(left: ReviewCase, right: ReviewCase) -> ReviewCase:
    """Join two observations using a commutative/idempotent policy lattice."""

    if left.case_key != right.case_key:
        raise CaseMergeConflict("cannot_merge_different_review_case_keys")
    if left.kind is right.kind:
        kind = left.kind
    elif {left.kind, right.kind} == {
        ReviewCaseKind.AMBIGUITY,
        ReviewCaseKind.CHECKPOINT,
    }:
        # A runtime checkpoint is a policy floor.  It must win regardless of
        # whether a weaker model ambiguity observation was encountered first.
        kind = ReviewCaseKind.CHECKPOINT
    else:
        raise CaseMergeConflict("review_case_kind_conflict")
    candidate_ids = {value for value in (left.candidate_id, right.candidate_id) if value}
    if len(candidate_ids) > 1:
        raise CaseMergeConflict("review_case_candidate_id_conflict")
    level = max((left.level, right.level), key=_LEVEL_RANK.__getitem__)
    return ReviewCase(
        case_key=left.case_key,
        kind=kind,
        level=level,
        context=_merge_context(left.context, right.context),
        candidate_id=next(iter(candidate_ids), ""),
        reasons=left.reasons + right.reasons,
        observation_ids=left.observation_ids + right.observation_ids,
        trigger_ids=left.trigger_ids + right.trigger_ids,
        requires_rewrite=left.requires_rewrite or right.requires_rewrite,
    )


def merge_review_cases(cases: Iterable[ReviewCase]) -> tuple[ReviewCase, ...]:
    """Normalize any case multiset; output is independent of input order."""

    merged: dict[str, ReviewCase] = {}
    for item in cases:
        current = merged.get(item.case_key)
        merged[item.case_key] = item if current is None else merge_review_case(current, item)
    return tuple(merged[key] for key in sorted(merged))


def candidate_digest(candidate: Any) -> str:
    """Return semantic candidate identity, excluding runtime ``candidate_id``."""

    try:
        from .evidence import candidate_digest as evidence_candidate_digest
        from .schema import FactCandidate

        if isinstance(candidate, FactCandidate):
            return str(evidence_candidate_digest(candidate))
    except (ImportError, AttributeError):
        pass
    return stable_digest(candidate)


def _candidate_source_digests(candidate: Any) -> tuple[str, ...]:
    try:
        from .evidence import source_digest
        from .schema import FactCandidate

        if isinstance(candidate, FactCandidate):
            return _normalized_strings(source_digest(source) for source in candidate.sources)
    except (ImportError, AttributeError):
        pass
    raw_sources: Sequence[Any] = ()
    if isinstance(candidate, Mapping):
        value = candidate.get("sources", ())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            raw_sources = value
    normalized_sources: list[Any] = []
    for source in raw_sources:
        if isinstance(source, Mapping):
            normalized_sources.append(
                {key: value for key, value in source.items() if key != "source_id"}
            )
        else:
            normalized_sources.append(source)
    return _normalized_strings(stable_digest(source) for source in normalized_sources)


def _candidate_digest_from_json(candidate_json: str) -> str:
    try:
        payload = json.loads(candidate_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReviewDomainError("candidate_requires_canonical_json") from exc
    if isinstance(payload, Mapping) and {
        "candidate_id",
        "claim",
        "sources",
        "support_map",
    }.issubset(payload):
        try:
            from .schema import FactCandidate
        except (ImportError, AttributeError) as exc:
            raise ReviewDomainError("candidate_schema_unavailable") from exc
        try:
            parsed = FactCandidate.model_validate(payload)
        except Exception as exc:
            raise ReviewDomainError("candidate_revision_schema_invalid") from exc
        try:
            return candidate_digest(parsed)
        except Exception as exc:
            raise ReviewDomainError("candidate_revision_evidence_invalid") from exc
    return stable_digest(payload)


def _candidate_source_digests_from_json(candidate_json: str) -> tuple[str, ...]:
    payload = json.loads(candidate_json)
    if isinstance(payload, Mapping) and {
        "candidate_id",
        "claim",
        "sources",
        "support_map",
    }.issubset(payload):
        try:
            from .schema import FactCandidate

            parsed = FactCandidate.model_validate(payload)
        except Exception as exc:
            raise ReviewDomainError("candidate_revision_schema_invalid") from exc
        return _candidate_source_digests(parsed)
    return _candidate_source_digests(payload)


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    candidate_digest: str
    candidate_json: str

    @classmethod
    def freeze(cls, candidate: Any) -> "CandidateRecord":
        return cls(
            candidate_digest=candidate_digest(candidate),
            candidate_json=canonical_json(candidate),
        )

    def payload(self) -> Any:
        return json.loads(self.candidate_json)


@dataclass(frozen=True, slots=True)
class CandidateRevision:
    parent_candidate_digest: str
    candidate_digest: str
    candidate_json: str
    source_digests: tuple[str, ...] = ()

    @classmethod
    def from_candidate(
        cls, parent_candidate_digest: str, candidate: Any
    ) -> "CandidateRevision":
        return cls(
            parent_candidate_digest=str(parent_candidate_digest or "").strip(),
            candidate_digest=candidate_digest(candidate),
            candidate_json=canonical_json(candidate),
            source_digests=_candidate_source_digests(candidate),
        )

    def __post_init__(self) -> None:
        if not self.parent_candidate_digest:
            raise ReviewDomainError("candidate_revision_requires_parent_digest")
        if not self.candidate_digest:
            raise ReviewDomainError("candidate_revision_requires_candidate_digest")
        object.__setattr__(
            self, "source_digests", _normalized_strings(self.source_digests)
        )
        actual_digest = _candidate_digest_from_json(self.candidate_json)
        if actual_digest != self.candidate_digest:
            raise ReviewDomainError("candidate_revision_digest_mismatch")
        if _candidate_source_digests_from_json(self.candidate_json) != self.source_digests:
            raise ReviewDomainError("candidate_revision_source_digest_mismatch")
        if self.candidate_digest == self.parent_candidate_digest:
            raise ReviewDomainError("candidate_correction_must_change_candidate")

    @property
    def revision_digest(self) -> str:
        return stable_digest(
            {
                "schema": f"{REVIEW_SCHEMA_VERSION}/candidate-revision",
                "parent_candidate_digest": self.parent_candidate_digest,
                "candidate_digest": self.candidate_digest,
                "candidate_payload_digest": hashlib.sha256(
                    self.candidate_json.encode("utf-8")
                ).hexdigest(),
                "source_digests": list(self.source_digests),
            }
        )

    def payload(self) -> Any:
        return json.loads(self.candidate_json)


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    case_key: str
    case_digest: str
    context: DecisionContext
    action: ReviewAction
    revision: int = 1
    supersedes: str = ""
    correction: CandidateRevision | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_key", str(self.case_key or "").strip())
        object.__setattr__(self, "case_digest", str(self.case_digest or "").strip())
        object.__setattr__(self, "action", ReviewAction(self.action))
        object.__setattr__(self, "supersedes", str(self.supersedes or "").strip())
        if not self.case_key or not self.case_digest:
            raise InvalidDecision("decision_requires_case_identity")
        if int(self.revision) < 1:
            raise InvalidDecision("decision_revision_must_be_positive")
        if int(self.revision) == 1 and self.supersedes:
            raise InvalidDecision("first_decision_cannot_supersede")
        if int(self.revision) > 1 and not self.supersedes:
            raise InvalidDecision("revised_decision_requires_supersedes_hash")
        if self.action is ReviewAction.CORRECT and self.correction is None:
            raise InvalidDecision("correct_decision_requires_candidate_revision")
        if self.action is not ReviewAction.CORRECT and self.correction is not None:
            raise InvalidDecision("only_correct_decision_accepts_candidate_revision")
        if (
            self.correction is not None
            and self.correction.parent_candidate_digest != self.context.candidate_digest
        ):
            raise InvalidDecision("correction_parent_does_not_match_review_candidate")

    @property
    def decision_hash(self) -> str:
        """Content address containing every world/context binding."""

        payload = {
            "schema": f"{REVIEW_SCHEMA_VERSION}/decision",
            "case_key": self.case_key,
            "case_digest": self.case_digest,
            "chapter": self.context.chapter,
            "chapter_digest": self.context.chapter_digest,
            "candidate_digest": self.context.candidate_digest,
            "evidence_digests": list(self.context.evidence_digests),
            "source_digests": list(self.context.source_digests),
            "parent_head": self.context.parent_head,
            "prior_fact_hashes": list(self.context.prior_fact_hashes),
            "policy_version": self.context.policy_version,
            "transaction_digest": self.context.transaction_digest,
            "effect_digests": list(self.context.effect_digests),
            "action": self.action.value,
            "revision": int(self.revision),
            "supersedes": self.supersedes,
            "correction_digest": (
                self.correction.revision_digest if self.correction else ""
            ),
        }
        if (
            self.context.entity_registry_digest
            != UNBOUND_ENTITY_REGISTRY_DIGEST
            or self.context.entity_resolution_digests
        ):
            payload["entity_registry_digest"] = (
                self.context.entity_registry_digest
            )
            payload["entity_resolution_digests"] = list(
                self.context.entity_resolution_digests
            )
        return stable_digest(payload)


def make_decision(
    case: ReviewCase,
    action: ReviewAction | str,
    *,
    corrected_candidate: Any | None = None,
    previous: ReviewDecision | None = None,
) -> ReviewDecision:
    """Validate an author action and construct a content-addressed decision."""

    normalized_action = ReviewAction(action)
    if normalized_action not in case.allowed_actions:
        raise InvalidDecision(
            f"action_{normalized_action.value}_not_allowed_for_{case.kind.value}"
        )
    if normalized_action is ReviewAction.CORRECT:
        if corrected_candidate is None:
            raise InvalidDecision("correct_decision_requires_corrected_candidate")
        correction = CandidateRevision.from_candidate(
            case.context.candidate_digest, corrected_candidate
        )
    else:
        if corrected_candidate is not None:
            raise InvalidDecision("only_correct_action_accepts_corrected_candidate")
        correction = None
    if previous is not None:
        if previous.case_key != case.case_key or previous.case_digest != case.target_digest:
            raise InvalidDecision("previous_decision_targets_different_case")
        if previous.context != case.context:
            raise InvalidDecision("previous_decision_context_changed")
    return ReviewDecision(
        case_key=case.case_key,
        case_digest=case.target_digest,
        context=case.context,
        action=normalized_action,
        revision=(previous.revision + 1) if previous else 1,
        supersedes=previous.decision_hash if previous else "",
        correction=correction,
    )


def _source_digests_from_revision(revision: CandidateRevision) -> tuple[str, ...]:
    return revision.source_digests


def _revision_case(case: ReviewCase, decision: ReviewDecision) -> ReviewCase:
    assert decision.correction is not None
    correction = decision.correction
    source_digests = _source_digests_from_revision(correction)
    return ReviewCase(
        case_key=f"{case.case_key}:revision:{correction.revision_digest}",
        kind=ReviewCaseKind.AMBIGUITY,
        level=ReviewLevel.REQUIRED,
        context=DecisionContext(
            chapter=case.context.chapter,
            chapter_digest=case.context.chapter_digest,
            candidate_digest=correction.candidate_digest,
            evidence_digests=source_digests,
            source_digests=source_digests,
            parent_head=case.context.parent_head,
            prior_fact_hashes=case.context.prior_fact_hashes,
            policy_version=case.context.policy_version,
            transaction_digest=case.context.transaction_digest,
            effect_digests=case.context.effect_digests,
            entity_registry_digest=case.context.entity_registry_digest,
            entity_resolution_digests=case.context.entity_resolution_digests,
        ),
        candidate_id=case.candidate_id,
        reasons=("corrected_candidate_requires_recompile_and_review",),
        observation_ids=(decision.decision_hash,),
        trigger_ids=("human_correction",),
        requires_rewrite=False,
    )


def _current_decisions(
    decisions: Iterable[ReviewDecision],
) -> tuple[dict[str, ReviewDecision], tuple[ReviewDecision, ...]]:
    grouped: dict[str, list[ReviewDecision]] = {}
    for decision in decisions:
        grouped.setdefault(decision.case_key, []).append(decision)
    current: dict[str, ReviewDecision] = {}
    superseded: list[ReviewDecision] = []
    for key, items in grouped.items():
        unique = {item.decision_hash: item for item in items}
        by_revision: dict[int, list[ReviewDecision]] = {}
        for item in unique.values():
            by_revision.setdefault(item.revision, []).append(item)
        forked = [revision for revision, peers in by_revision.items() if len(peers) > 1]
        if forked:
            raise DecisionConflict(
                f"multiple_decision_heads:{key}:revision:{min(forked)}"
            )
        revisions = sorted(by_revision)
        if revisions != list(range(1, revisions[-1] + 1)):
            raise DecisionConflict(f"decision_chain_has_gap:{key}")
        ordered = [by_revision[revision][0] for revision in revisions]
        for previous, amended in zip(ordered, ordered[1:]):
            if amended.supersedes != previous.decision_hash:
                raise DecisionConflict(
                    f"decision_supersedes_mismatch:{key}:revision:{amended.revision}"
                )
        head = ordered[-1]
        current[key] = head
        superseded.extend(item for item in ordered if item.decision_hash != head.decision_hash)
    return current, tuple(sorted(superseded, key=lambda item: item.decision_hash))


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    """The only workflow/gate view exposed by the v3 review reducer."""

    state: WorkflowState
    can_finalize: bool
    can_write_next: bool
    required_cases: tuple[ReviewCase, ...]
    advisory_cases: tuple[ReviewCase, ...]
    audit_cases: tuple[ReviewCase, ...]
    rewrite_cases: tuple[ReviewCase, ...]
    revision_cases: tuple[ReviewCase, ...]
    stale_decision_hashes: tuple[str, ...]
    recovery_action: str

    @property
    def required_count(self) -> int:
        return len(self.required_cases)

    @property
    def advisory_count(self) -> int:
        return len(self.advisory_cases)

    @property
    def audit_count(self) -> int:
        return len(self.audit_cases)


@dataclass(frozen=True, slots=True)
class ReviewReduction:
    base_candidates: tuple[CandidateRecord, ...]
    active_candidates: tuple[CandidateRecord, ...]
    omitted_candidate_digests: tuple[str, ...]
    approved_candidate_digests: tuple[str, ...]
    corrections: tuple[CandidateRevision, ...]
    applied_decision_hashes: tuple[str, ...]
    stale_decision_hashes: tuple[str, ...]
    superseded_decision_hashes: tuple[str, ...]
    resolved_case_keys: tuple[str, ...]
    snapshot: WorkflowSnapshot


def _build_snapshot(
    *,
    required: Iterable[ReviewCase],
    advisory: Iterable[ReviewCase],
    audit: Iterable[ReviewCase],
    rewrite: Iterable[ReviewCase],
    revisions: Iterable[ReviewCase],
    stale_decision_hashes: Iterable[str],
) -> WorkflowSnapshot:
    required_cases = tuple(sorted(required, key=lambda item: item.case_key))
    advisory_cases = tuple(sorted(advisory, key=lambda item: item.case_key))
    audit_cases = tuple(sorted(audit, key=lambda item: item.case_key))
    rewrite_cases = tuple(sorted(rewrite, key=lambda item: item.case_key))
    revision_cases = tuple(sorted(revisions, key=lambda item: item.case_key))
    if rewrite_cases:
        state = WorkflowState.REWRITE_REQUIRED
        recovery_action = "rewrite_chapter"
    elif revision_cases:
        state = WorkflowState.RECOMPILE_REQUIRED
        recovery_action = "recompile_corrected_candidates"
    elif required_cases:
        state = WorkflowState.AWAITING_HUMAN
        recovery_action = "review_required_cases"
    else:
        state = WorkflowState.READY
        recovery_action = "finalize_transaction"
    ready = state is WorkflowState.READY
    return WorkflowSnapshot(
        state=state,
        can_finalize=ready,
        # Review can only make a prepared transaction finalizable.  Writing
        # the next chapter also requires the service to publish Canon HEAD and
        # verify projections, neither of which belongs in this pure module.
        can_write_next=False,
        required_cases=required_cases,
        advisory_cases=advisory_cases,
        audit_cases=audit_cases,
        rewrite_cases=rewrite_cases,
        revision_cases=revision_cases,
        stale_decision_hashes=_normalized_strings(stale_decision_hashes),
        recovery_action=recovery_action,
    )


def reduce_review(
    base_candidates: Iterable[Any],
    cases: Iterable[ReviewCase],
    decisions: Iterable[ReviewDecision] = (),
) -> ReviewReduction:
    """Recompute review state from immutable inputs.

    No previous reduction is accepted as input, which makes replay idempotent
    and ensures an amended decision cannot leave an old replacement behind.
    """

    frozen_by_digest: dict[str, CandidateRecord] = {}
    for candidate in base_candidates:
        frozen = CandidateRecord.freeze(candidate)
        previous = frozen_by_digest.get(frozen.candidate_digest)
        if previous is None or frozen.candidate_json < previous.candidate_json:
            frozen_by_digest[frozen.candidate_digest] = frozen
    merged_cases = merge_review_cases(cases)
    cases_by_key = {case.case_key: case for case in merged_cases}
    current_decisions, superseded = _current_decisions(decisions)

    required: list[ReviewCase] = []
    advisory: list[ReviewCase] = []
    audit: list[ReviewCase] = []
    rewrites: list[ReviewCase] = []
    revision_cases: list[ReviewCase] = []
    corrections: list[CandidateRevision] = []
    omitted: set[str] = set()
    blocked: set[str] = set()
    approved: set[str] = set()
    resolved: set[str] = set()
    applied: set[str] = set()
    stale: set[str] = set()

    for key, decision in current_decisions.items():
        case = cases_by_key.get(key)
        if (
            case is None
            or decision.case_digest != case.target_digest
            or decision.context != case.context
        ):
            stale.add(decision.decision_hash)

    for case in merged_cases:
        decision = current_decisions.get(case.case_key)
        if decision is not None and decision.decision_hash in stale:
            decision = None
        if case.requires_rewrite and decision is None:
            rewrites.append(case)
            if case.context.candidate_digest:
                blocked.add(case.context.candidate_digest)
            continue
        if decision is None:
            if case.level is ReviewLevel.REQUIRED:
                required.append(case)
                if case.context.candidate_digest:
                    blocked.add(case.context.candidate_digest)
            elif case.level is ReviewLevel.ADVISORY:
                advisory.append(case)
            else:
                audit.append(case)
            continue

        if decision.action not in case.allowed_actions:
            raise InvalidDecision(
                f"action_{decision.action.value}_not_allowed_for_{case.kind.value}"
            )
        applied.add(decision.decision_hash)
        resolved.add(case.case_key)
        candidate_key = case.context.candidate_digest
        if decision.action in {ReviewAction.APPROVE, ReviewAction.NO_CONFLICT}:
            if candidate_key:
                approved.add(candidate_key)
        elif decision.action is ReviewAction.DISMISS:
            pass
        elif decision.action is ReviewAction.OMIT:
            if candidate_key:
                omitted.add(candidate_key)
        elif decision.action is ReviewAction.REWRITE:
            rewrites.append(case)
            if candidate_key:
                blocked.add(candidate_key)
        elif decision.action is ReviewAction.CORRECT:
            assert decision.correction is not None
            omitted.add(candidate_key)
            corrections.append(decision.correction)
            revision_cases.append(_revision_case(case, decision))

    corrections_by_parent: dict[str, set[str]] = {}
    for correction in corrections:
        corrections_by_parent.setdefault(correction.parent_candidate_digest, set()).add(
            correction.revision_digest
        )
    conflicting_parents = [
        parent for parent, revisions in corrections_by_parent.items() if len(revisions) > 1
    ]
    if conflicting_parents:
        raise DecisionConflict(
            "multiple_candidate_corrections:" + ",".join(sorted(conflicting_parents))
        )

    excluded = omitted | blocked
    active = tuple(
        frozen_by_digest[digest]
        for digest in sorted(frozen_by_digest)
        if digest not in excluded
    )
    stale_hashes = tuple(sorted(stale))
    snapshot = _build_snapshot(
        required=required,
        advisory=advisory,
        audit=audit,
        rewrite=rewrites,
        revisions=revision_cases,
        stale_decision_hashes=stale_hashes,
    )
    return ReviewReduction(
        base_candidates=tuple(frozen_by_digest[key] for key in sorted(frozen_by_digest)),
        active_candidates=active,
        omitted_candidate_digests=tuple(sorted(omitted)),
        approved_candidate_digests=tuple(sorted(approved - excluded)),
        corrections=tuple(
            sorted(corrections, key=lambda item: item.revision_digest)
        ),
        applied_decision_hashes=tuple(sorted(applied)),
        stale_decision_hashes=stale_hashes,
        superseded_decision_hashes=tuple(
            sorted(item.decision_hash for item in superseded)
        ),
        resolved_case_keys=tuple(sorted(resolved)),
        snapshot=snapshot,
    )


def workflow_snapshot(
    base_candidates: Iterable[Any],
    cases: Iterable[ReviewCase],
    decisions: Iterable[ReviewDecision] = (),
) -> WorkflowSnapshot:
    """Return the single gate/report snapshot for the current transaction."""

    return reduce_review(base_candidates, cases, decisions).snapshot


def review_case_from_requirement(
    requirement: Any,
    *,
    chapter: int,
    chapter_digest: str,
    parent_head: str,
    policy_version: str,
    transaction_digest: str,
    effect_digests: Iterable[str],
    candidate: Any | None = None,
    evidence_digests: Iterable[str] = (),
    source_digests: Iterable[str] = (),
    entity_registry_digest: str = UNBOUND_ENTITY_REGISTRY_DIGEST,
    entity_resolution_digests: Iterable[str] = (),
) -> ReviewCase:
    """Bind a compiler ``ReviewRequirement`` to its exact transaction.

    ``candidate`` is optional for unmaterialized service adapters, but when it
    is supplied its semantic digest and source evidence are verified here.
    """

    expected_candidate_digest = str(requirement.candidate_digest)
    candidate_id = ""
    derived_sources: tuple[str, ...] = ()
    if candidate is not None:
        actual_candidate_digest = candidate_digest(candidate)
        if actual_candidate_digest != expected_candidate_digest:
            raise ReviewDomainError("requirement_candidate_digest_mismatch")
        candidate_id = str(getattr(candidate, "candidate_id", "") or "")
        derived_sources = _candidate_source_digests(candidate)
    checkpoint = bool(requirement.checkpoint)
    mode = str(getattr(requirement.mode, "value", requirement.mode))
    return ReviewCase(
        case_key=str(requirement.case_key),
        kind=(
            ReviewCaseKind.CHECKPOINT if checkpoint else ReviewCaseKind.AMBIGUITY
        ),
        level=_coerce_level(getattr(requirement.level, "value", requirement.level)),
        context=DecisionContext(
            chapter=chapter,
            chapter_digest=chapter_digest,
            candidate_digest=expected_candidate_digest,
            evidence_digests=tuple(evidence_digests) + derived_sources,
            source_digests=tuple(source_digests) + derived_sources,
            parent_head=parent_head,
            prior_fact_hashes=tuple(requirement.prior_fact_digests),
            policy_version=policy_version,
            transaction_digest=transaction_digest,
            effect_digests=tuple(effect_digests),
            entity_registry_digest=entity_registry_digest,
            entity_resolution_digests=tuple(entity_resolution_digests),
        ),
        candidate_id=candidate_id,
        reasons=tuple(requirement.reason_codes),
        observation_ids=tuple(requirement.observation_digests),
        trigger_ids=("compiler_requirement",),
        requires_rewrite=mode == "rewrite",
    )


_CONTEXT_KEYS = frozenset(
    {
        "chapter",
        "chapter_digest",
        "candidate_digest",
        "evidence_digests",
        "source_digests",
        "parent_head",
        "prior_fact_hashes",
        "policy_version",
        "transaction_digest",
        "effect_digests",
        "entity_registry_digest",
        "entity_resolution_digests",
    }
)
_LEGACY_CONTEXT_KEYS = _CONTEXT_KEYS - {
    "entity_registry_digest",
    "entity_resolution_digests",
}


def _strict_mapping(value: Any, expected: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewDomainError(f"{label}_must_be_object")
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise ReviewDomainError(
            f"{label}_fields_invalid:missing={missing}:extra={extra}"
        )
    return value


def _string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReviewDomainError(f"{label}_must_be_string_array")
    return tuple(value)


def _strict_string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ReviewDomainError(f"{label}_must_be_string")
    if not allow_empty and not value:
        raise ReviewDomainError(f"{label}_must_not_be_empty")
    return value


def _context_from_dict(value: Any) -> DecisionContext:
    if not isinstance(value, Mapping):
        raise ReviewDomainError("decision_context_must_be_object")
    actual = set(value)
    if actual == set(_CONTEXT_KEYS):
        raw = value
        entity_registry_digest = _strict_string(
            raw["entity_registry_digest"],
            "decision_context.entity_registry_digest",
        )
        entity_resolution_digests = _string_tuple(
            raw["entity_resolution_digests"],
            "decision_context.entity_resolution_digests",
        )
    elif actual == set(_LEGACY_CONTEXT_KEYS):
        raw = value
        entity_registry_digest = UNBOUND_ENTITY_REGISTRY_DIGEST
        entity_resolution_digests = ()
    else:
        _strict_mapping(value, _CONTEXT_KEYS, "decision_context")
        raise AssertionError("unreachable")
    if not isinstance(raw["chapter"], int) or isinstance(raw["chapter"], bool):
        raise ReviewDomainError("decision_context_chapter_must_be_integer")
    return DecisionContext(
        chapter=raw["chapter"],
        chapter_digest=_strict_string(
            raw["chapter_digest"], "decision_context.chapter_digest"
        ),
        candidate_digest=_strict_string(
            raw["candidate_digest"],
            "decision_context.candidate_digest",
            allow_empty=True,
        ),
        evidence_digests=_string_tuple(
            raw["evidence_digests"], "decision_context.evidence_digests"
        ),
        source_digests=_string_tuple(
            raw["source_digests"], "decision_context.source_digests"
        ),
        parent_head=_strict_string(raw["parent_head"], "decision_context.parent_head"),
        prior_fact_hashes=_string_tuple(
            raw["prior_fact_hashes"], "decision_context.prior_fact_hashes"
        ),
        policy_version=_strict_string(
            raw["policy_version"], "decision_context.policy_version"
        ),
        transaction_digest=_strict_string(
            raw["transaction_digest"], "decision_context.transaction_digest"
        ),
        effect_digests=_string_tuple(
            raw["effect_digests"], "decision_context.effect_digests"
        ),
        entity_registry_digest=entity_registry_digest,
        entity_resolution_digests=entity_resolution_digests,
    )


def case_to_dict(case: ReviewCase) -> dict[str, Any]:
    """Strict storage payload for an immutable review case."""

    return {
        "schema": f"{REVIEW_SCHEMA_VERSION}/case",
        "case_key": case.case_key,
        "kind": case.kind.value,
        "level": case.level.value,
        "context": case.context.payload(),
        "candidate_id": case.candidate_id,
        "reasons": list(case.reasons),
        "observation_ids": list(case.observation_ids),
        "trigger_ids": list(case.trigger_ids),
        "requires_rewrite": case.requires_rewrite,
        "target_digest": case.target_digest,
    }


def case_from_dict(value: Any) -> ReviewCase:
    """Strictly reconstruct and content-verify a persisted case."""

    expected = frozenset(
        {
            "schema",
            "case_key",
            "kind",
            "level",
            "context",
            "candidate_id",
            "reasons",
            "observation_ids",
            "trigger_ids",
            "requires_rewrite",
            "target_digest",
        }
    )
    raw = _strict_mapping(value, expected, "review_case")
    if raw["schema"] != f"{REVIEW_SCHEMA_VERSION}/case":
        raise ReviewDomainError("review_case_schema_mismatch")
    if not isinstance(raw["requires_rewrite"], bool):
        raise ReviewDomainError("review_case_requires_rewrite_must_be_boolean")
    case = ReviewCase(
        case_key=_strict_string(raw["case_key"], "review_case.case_key"),
        kind=ReviewCaseKind(_strict_string(raw["kind"], "review_case.kind")),
        level=_coerce_level(_strict_string(raw["level"], "review_case.level")),
        context=_context_from_dict(raw["context"]),
        candidate_id=_strict_string(
            raw["candidate_id"], "review_case.candidate_id", allow_empty=True
        ),
        reasons=_string_tuple(raw["reasons"], "review_case.reasons"),
        observation_ids=_string_tuple(
            raw["observation_ids"], "review_case.observation_ids"
        ),
        trigger_ids=_string_tuple(raw["trigger_ids"], "review_case.trigger_ids"),
        requires_rewrite=raw["requires_rewrite"],
    )
    if _strict_string(raw["target_digest"], "review_case.target_digest") != case.target_digest:
        raise ReviewDomainError("review_case_target_digest_mismatch")
    return case


def decision_to_dict(decision: ReviewDecision) -> dict[str, Any]:
    """Strict content-addressed payload used by the decision repository."""

    correction: dict[str, Any] | None = None
    if decision.correction is not None:
        correction = {
            "parent_candidate_digest": decision.correction.parent_candidate_digest,
            "candidate_digest": decision.correction.candidate_digest,
            "candidate_json": decision.correction.candidate_json,
            "source_digests": list(decision.correction.source_digests),
            "revision_digest": decision.correction.revision_digest,
        }
    return {
        "schema": f"{REVIEW_SCHEMA_VERSION}/decision",
        "case_key": decision.case_key,
        "case_digest": decision.case_digest,
        "context": decision.context.payload(),
        "action": decision.action.value,
        "revision": decision.revision,
        "supersedes": decision.supersedes,
        "correction": correction,
        "decision_hash": decision.decision_hash,
    }


def decision_from_dict(value: Any) -> ReviewDecision:
    """Strictly reconstruct and hash-verify a persisted author decision."""

    expected = frozenset(
        {
            "schema",
            "case_key",
            "case_digest",
            "context",
            "action",
            "revision",
            "supersedes",
            "correction",
            "decision_hash",
        }
    )
    raw = _strict_mapping(value, expected, "review_decision")
    if raw["schema"] != f"{REVIEW_SCHEMA_VERSION}/decision":
        raise ReviewDomainError("review_decision_schema_mismatch")
    if not isinstance(raw["revision"], int) or isinstance(raw["revision"], bool):
        raise ReviewDomainError("review_decision_revision_must_be_integer")
    correction: CandidateRevision | None = None
    if raw["correction"] is not None:
        correction_keys = frozenset(
            {
                "parent_candidate_digest",
                "candidate_digest",
                "candidate_json",
                "source_digests",
                "revision_digest",
            }
        )
        correction_raw = _strict_mapping(
            raw["correction"], correction_keys, "candidate_revision"
        )
        correction = CandidateRevision(
            parent_candidate_digest=_strict_string(
                correction_raw["parent_candidate_digest"],
                "candidate_revision.parent_candidate_digest",
            ),
            candidate_digest=_strict_string(
                correction_raw["candidate_digest"],
                "candidate_revision.candidate_digest",
            ),
            candidate_json=_strict_string(
                correction_raw["candidate_json"], "candidate_revision.candidate_json"
            ),
            source_digests=_string_tuple(
                correction_raw["source_digests"],
                "candidate_revision.source_digests",
            ),
        )
        if _strict_string(
            correction_raw["revision_digest"], "candidate_revision.revision_digest"
        ) != correction.revision_digest:
            raise ReviewDomainError("candidate_revision_digest_mismatch")
    decision = ReviewDecision(
        case_key=_strict_string(raw["case_key"], "review_decision.case_key"),
        case_digest=_strict_string(
            raw["case_digest"], "review_decision.case_digest"
        ),
        context=_context_from_dict(raw["context"]),
        action=ReviewAction(_strict_string(raw["action"], "review_decision.action")),
        revision=raw["revision"],
        supersedes=_strict_string(
            raw["supersedes"], "review_decision.supersedes", allow_empty=True
        ),
        correction=correction,
    )
    if _strict_string(
        raw["decision_hash"], "review_decision.decision_hash"
    ) != decision.decision_hash:
        raise ReviewDomainError("review_decision_hash_mismatch")
    return decision


__all__ = [
    "ACTION_MATRIX",
    "CandidateRecord",
    "CandidateRevision",
    "CaseMergeConflict",
    "DecisionConflict",
    "DecisionContext",
    "InvalidDecision",
    "REVIEW_SCHEMA_VERSION",
    "ReviewAction",
    "ReviewCase",
    "ReviewCaseKind",
    "ReviewDecision",
    "ReviewDomainError",
    "ReviewLevel",
    "ReviewReduction",
    "WorkflowSnapshot",
    "WorkflowState",
    "candidate_digest",
    "canonical_json",
    "case_from_dict",
    "case_to_dict",
    "decision_from_dict",
    "decision_to_dict",
    "make_decision",
    "merge_review_case",
    "merge_review_cases",
    "reduce_review",
    "review_case_from_requirement",
    "stable_digest",
    "workflow_snapshot",
]

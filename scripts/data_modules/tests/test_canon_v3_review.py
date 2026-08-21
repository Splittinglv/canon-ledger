#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
import itertools
import json
from dataclasses import replace

import pytest

from data_modules.canon_v3.compiler import compile_transaction
from data_modules.canon_v3.evidence import candidate_digest as evidence_candidate_digest
from data_modules.canon_v3.evidence import source_digest
from data_modules.canon_v3.review import (
    CandidateRevision,
    CaseMergeConflict,
    DecisionConflict,
    DecisionContext,
    InvalidDecision,
    ReviewAction,
    ReviewCase,
    ReviewCaseKind,
    ReviewDomainError,
    ReviewLevel,
    WorkflowState,
    candidate_digest,
    case_from_dict,
    case_to_dict,
    decision_from_dict,
    decision_to_dict,
    make_decision,
    merge_review_case,
    merge_review_cases,
    reduce_review,
    review_case_from_requirement,
    workflow_snapshot,
)
from data_modules.canon_v3.schema import (
    FactCandidate,
    ManuscriptSpanSource,
    ObservationKind,
    ReviewLevel as SchemaReviewLevel,
    ReviewObservation,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate(
    *,
    candidate_id: str = "presence-1",
    location: str = "北城",
) -> FactCandidate:
    quote = f"林舟抵达{location}，确为在场。"
    source = ManuscriptSpanSource(
        source_id="chapter-span",
        document_sha256=_sha("chapter"),
        chapter=3,
        start=0,
        end=len(quote.encode("utf-8")),
        quote=quote,
        quote_sha256=_sha(quote),
    )
    return FactCandidate.model_validate(
        {
            "candidate_id": candidate_id,
            "claim": {
                "kind": "presence_observed",
                "subject": "林舟",
                "location": location,
                "presence": "在场",
            },
            "sources": [source],
            "support_map": {
                "subject": ["chapter-span"],
                "location": ["chapter-span"],
                "presence": ["chapter-span"],
            },
        }
    )


def _context(
    candidate: object | None = None,
    *,
    chapter: int = 3,
    chapter_digest: str = "chapter-sha",
    candidate_hash: str | None = None,
    evidence: tuple[str, ...] = ("evidence-a",),
    sources: tuple[str, ...] = ("source-a",),
    parent_head: str = "parent-head",
    prior: tuple[str, ...] = ("prior-a",),
    policy: str = "policy-v1",
    transaction: str = "transaction-a",
    effects: tuple[str, ...] = ("effect-a",),
) -> DecisionContext:
    if candidate_hash is None:
        candidate_hash = candidate_digest(candidate) if candidate is not None else ""
    return DecisionContext(
        chapter=chapter,
        chapter_digest=chapter_digest,
        candidate_digest=candidate_hash,
        evidence_digests=evidence,
        source_digests=sources,
        parent_head=parent_head,
        prior_fact_hashes=prior,
        policy_version=policy,
        transaction_digest=transaction,
        effect_digests=effects,
    )


def _case(
    candidate: object,
    *,
    key: str = "case-a",
    kind: ReviewCaseKind = ReviewCaseKind.AMBIGUITY,
    level: ReviewLevel = ReviewLevel.REQUIRED,
    context: DecisionContext | None = None,
    reason: str = "ambiguous fact",
    observation: str = "observation-a",
    trigger: str = "model",
) -> ReviewCase:
    return ReviewCase(
        case_key=key,
        kind=kind,
        level=level,
        context=context or _context(candidate),
        reasons=(reason,),
        observation_ids=(observation,),
        trigger_ids=(trigger,),
    )


def test_case_merge_is_commutative_associative_idempotent_and_strongest_wins():
    candidate = _candidate()
    base = _context(candidate, evidence=(), sources=(), prior=(), effects=())
    cases = [
        _case(
            candidate,
            level=ReviewLevel.AUDIT_ONLY,
            context=replace(
                base,
                evidence_digests=("e1",),
                source_digests=("s1",),
                prior_fact_hashes=("p1",),
                effect_digests=("x1",),
            ),
            reason="audit",
            observation="o1",
            trigger="audit",
        ),
        _case(
            candidate,
            level=ReviewLevel.ADVISORY,
            context=replace(
                base,
                evidence_digests=("e2",),
                source_digests=("s2",),
                prior_fact_hashes=("p2",),
                effect_digests=("x2",),
            ),
            reason="advisory",
            observation="o2",
            trigger="reviewer",
        ),
        _case(
            candidate,
            kind=ReviewCaseKind.CHECKPOINT,
            level=ReviewLevel.REQUIRED,
            context=replace(
                base,
                evidence_digests=("e3",),
                source_digests=("s3",),
                prior_fact_hashes=("p3",),
                effect_digests=("x3",),
            ),
            reason="checkpoint",
            observation="o3",
            trigger="runtime",
        ),
    ]

    results = {
        merge_review_cases(permutation)[0]
        for permutation in itertools.permutations(cases)
    }
    assert len(results) == 1
    merged = results.pop()
    assert merged.level is ReviewLevel.HUMAN_REQUIRED
    assert merged.kind is ReviewCaseKind.CHECKPOINT
    assert merged.reasons == ("advisory", "audit", "checkpoint")
    assert merged.context.evidence_digests == ("e1", "e2", "e3")
    assert merge_review_case(merged, merged) == merged
    assert merge_review_case(merge_review_case(cases[0], cases[1]), cases[2]) == (
        merge_review_case(cases[0], merge_review_case(cases[1], cases[2]))
    )


def test_case_merge_rejects_same_key_with_different_world_binding():
    candidate = _candidate()
    first = _case(candidate)
    second = _case(
        candidate,
        context=replace(first.context, parent_head="different-parent"),
    )
    with pytest.raises(CaseMergeConflict, match="parent_head"):
        merge_review_case(first, second)


@pytest.mark.parametrize(
    ("kind", "valid", "invalid"),
    [
        (
            ReviewCaseKind.CHECKPOINT,
            {ReviewAction.APPROVE, ReviewAction.REWRITE},
            {ReviewAction.OMIT, ReviewAction.CORRECT, ReviewAction.NO_CONFLICT},
        ),
        (
            ReviewCaseKind.AMBIGUITY,
            {
                ReviewAction.APPROVE,
                ReviewAction.OMIT,
                ReviewAction.CORRECT,
                ReviewAction.REWRITE,
            },
            {ReviewAction.NO_CONFLICT},
        ),
        (
            ReviewCaseKind.UNBOUND,
            {ReviewAction.NO_CONFLICT, ReviewAction.REWRITE},
            {ReviewAction.APPROVE, ReviewAction.OMIT, ReviewAction.CORRECT},
        ),
    ],
)
def test_action_matrix_is_closed(kind, valid, invalid):
    candidate = _candidate()
    context = (
        _context(candidate)
        if kind is not ReviewCaseKind.UNBOUND
        else _context(candidate_hash="")
    )
    case = _case(candidate, kind=kind, context=context)
    corrected = _candidate(candidate_id="presence-corrected", location="南城")
    for action in valid:
        kwargs = {"corrected_candidate": corrected} if action is ReviewAction.CORRECT else {}
        assert make_decision(case, action, **kwargs).action is action
    for action in invalid:
        kwargs = {"corrected_candidate": corrected} if action is ReviewAction.CORRECT else {}
        with pytest.raises(InvalidDecision):
            make_decision(case, action, **kwargs)


def test_checkpoint_never_accepts_omit_even_when_a_weaker_case_was_seen_first():
    candidate = _candidate()
    weak = _case(candidate, level=ReviewLevel.ADVISORY)
    checkpoint = _case(
        candidate,
        kind=ReviewCaseKind.CHECKPOINT,
        level=ReviewLevel.REQUIRED,
    )
    merged_weak_first = merge_review_cases((weak, checkpoint))[0]
    merged_checkpoint_first = merge_review_cases((checkpoint, weak))[0]
    assert merged_weak_first == merged_checkpoint_first
    assert merged_weak_first.kind is ReviewCaseKind.CHECKPOINT
    assert merged_weak_first.level is ReviewLevel.HUMAN_REQUIRED
    with pytest.raises(InvalidDecision):
        make_decision(merged_weak_first, ReviewAction.OMIT)


def test_semantic_candidate_digest_excludes_runtime_candidate_id():
    first = _candidate(candidate_id="runtime-a")
    second = _candidate(candidate_id="runtime-b")
    assert candidate_digest(first) == evidence_candidate_digest(first)
    assert candidate_digest(first) == candidate_digest(second)
    reduction = reduce_review((second, first), ())
    assert len(reduction.base_candidates) == 1
    assert reduction == reduce_review((first, second), ())


@pytest.mark.parametrize(
    "change",
    [
        {"chapter": 4},
        {"chapter_digest": "chapter-b"},
        {"candidate_digest": "candidate-b"},
        {"evidence_digests": ("evidence-b",)},
        {"source_digests": ("source-b",)},
        {"parent_head": "parent-b"},
        {"prior_fact_hashes": ("prior-b",)},
        {"policy_version": "policy-v2"},
        {"transaction_digest": "transaction-b"},
        {"effect_digests": ("effect-b",)},
    ],
)
def test_decision_hash_binds_every_mutable_world_input(change):
    candidate = _candidate()
    first_case = _case(candidate)
    first = make_decision(first_case, ReviewAction.APPROVE)
    second_case = _case(candidate, context=replace(first_case.context, **change))
    second = make_decision(second_case, ReviewAction.APPROVE)
    assert first.decision_hash != second.decision_hash


def test_case_and_decision_storage_roundtrip_is_strict_and_hash_verified():
    candidate = _candidate()
    case = _case(candidate)
    decision = make_decision(case, ReviewAction.APPROVE)
    assert case_from_dict(case_to_dict(case)) == case
    assert decision_from_dict(decision_to_dict(decision)) == decision

    tampered = copy.deepcopy(decision_to_dict(decision))
    tampered["context"]["parent_head"] = "forged-parent"
    with pytest.raises(ReviewDomainError, match="hash_mismatch"):
        decision_from_dict(tampered)

    extra = decision_to_dict(decision)
    extra["model_blocking"] = True
    with pytest.raises(ReviewDomainError, match="fields_invalid"):
        decision_from_dict(extra)


def test_stale_decision_never_applies_after_transaction_or_evidence_changes():
    candidate = _candidate()
    old_case = _case(candidate)
    old_decision = make_decision(old_case, ReviewAction.APPROVE)
    new_case = _case(
        candidate,
        context=replace(
            old_case.context,
            transaction_digest="new-transaction",
            evidence_digests=("new-evidence",),
        ),
    )
    reduction = reduce_review((candidate,), (new_case,), (old_decision,))
    assert reduction.snapshot.state is WorkflowState.AWAITING_HUMAN
    assert reduction.snapshot.required_count == 1
    assert reduction.stale_decision_hashes == (old_decision.decision_hash,)
    assert reduction.applied_decision_hashes == ()


def test_correct_creates_a_new_revision_but_never_patches_active_candidates():
    candidate = _candidate()
    corrected = _candidate(candidate_id="corrected", location="南城")
    case = _case(candidate)
    decision = make_decision(
        case,
        ReviewAction.CORRECT,
        corrected_candidate=corrected,
    )
    reduction = reduce_review((candidate,), (case,), (decision,))
    assert reduction.snapshot.state is WorkflowState.RECOMPILE_REQUIRED
    assert reduction.snapshot.can_finalize is False
    assert reduction.snapshot.can_write_next is False
    assert reduction.active_candidates == ()
    assert reduction.corrections[0].candidate_digest == candidate_digest(corrected)
    revision_case = reduction.snapshot.revision_cases[0]
    assert revision_case.level is ReviewLevel.HUMAN_REQUIRED
    assert revision_case.context.candidate_digest == candidate_digest(corrected)
    assert revision_case.case_key != case.case_key


def test_reducer_replay_is_idempotent_and_decision_amendment_leaves_no_residue():
    candidate = _candidate()
    corrected = _candidate(candidate_id="corrected", location="南城")
    case = _case(candidate)
    first = make_decision(
        case, ReviewAction.CORRECT, corrected_candidate=corrected
    )
    second = make_decision(case, ReviewAction.OMIT, previous=first)
    third = make_decision(case, ReviewAction.APPROVE, previous=second)

    omitted = reduce_review((candidate,), (case,), (first, second))
    assert omitted == reduce_review((candidate,), (case,), (second, first))
    assert omitted.corrections == ()
    assert omitted.snapshot.revision_cases == ()
    assert omitted.omitted_candidate_digests == (candidate_digest(candidate),)

    approved = reduce_review((candidate,), (case,), (third, first, second))
    assert approved.corrections == ()
    assert approved.omitted_candidate_digests == ()
    assert len(approved.active_candidates) == 1
    assert approved.approved_candidate_digests == (candidate_digest(candidate),)
    assert approved == reduce_review((candidate,), (case,), (first, second, third))


def test_append_only_decision_chain_must_be_contiguous_and_unforked():
    candidate = _candidate()
    case = _case(candidate)
    first = make_decision(case, ReviewAction.APPROVE)
    second = make_decision(case, ReviewAction.OMIT, previous=first)
    forged_link = replace(second, supersedes="not-the-previous-decision")
    with pytest.raises(DecisionConflict, match="supersedes_mismatch"):
        reduce_review((candidate,), (case,), (first, forged_link))

    fork = make_decision(case, ReviewAction.REWRITE, previous=first)
    with pytest.raises(DecisionConflict, match="multiple_decision_heads"):
        reduce_review((candidate,), (case,), (first, second, fork))

    with pytest.raises(DecisionConflict, match="chain_has_gap"):
        reduce_review((candidate,), (case,), (second,))


def test_workflow_snapshot_is_the_only_gate_and_never_publishes_next_chapter():
    candidate = _candidate()
    advisory = _case(candidate, level=ReviewLevel.ADVISORY)
    audit = _case(
        candidate,
        key="case-b",
        level=ReviewLevel.AUDIT_ONLY,
        reason="low probability",
    )
    snapshot = workflow_snapshot((candidate,), (advisory, audit))
    assert snapshot.state is WorkflowState.READY
    assert snapshot.can_finalize is True
    assert snapshot.can_write_next is False
    assert snapshot.advisory_count == 1
    assert snapshot.audit_count == 1

    dismissed = make_decision(advisory, ReviewAction.DISMISS)
    dismissed_snapshot = workflow_snapshot(
        (candidate,), (advisory, audit), (dismissed,)
    )
    assert dismissed_snapshot.state is WorkflowState.READY
    with pytest.raises(InvalidDecision):
        make_decision(advisory, ReviewAction.OMIT)


def test_rewrite_and_unbound_transitions_are_explicit():
    candidate = _candidate()
    rewrite_case = _case(candidate)
    rewrite = make_decision(rewrite_case, ReviewAction.REWRITE)
    assert reduce_review((candidate,), (rewrite_case,), (rewrite,)).snapshot.state is (
        WorkflowState.REWRITE_REQUIRED
    )

    unbound = _case(
        candidate,
        key="unbound",
        kind=ReviewCaseKind.UNBOUND,
        context=_context(candidate_hash=""),
    )
    no_conflict = make_decision(unbound, ReviewAction.NO_CONFLICT)
    snapshot = workflow_snapshot((candidate,), (unbound,), (no_conflict,))
    assert snapshot.state is WorkflowState.READY
    assert snapshot.can_finalize is True


def test_compiler_requirement_adapter_binds_candidate_sources_and_transaction():
    candidate = _candidate()
    observation = ReviewObservation(
        observation_id="ambiguous-presence",
        candidate_id=candidate.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=SchemaReviewLevel.HUMAN_REQUIRED,
        reason="是否是现实中的抵达需要确认",
        prior_fact_digests=(_sha("prior"),),
    )
    transaction = compile_transaction((candidate,), (observation,), "GENESIS")
    requirement = transaction.requirements[0]
    effect_ids = tuple(effect.effect_id for effect in transaction.effects)
    case = review_case_from_requirement(
        requirement,
        chapter=3,
        chapter_digest=_sha("chapter"),
        parent_head=transaction.parent_head,
        policy_version=transaction.policy_version,
        transaction_digest=transaction.transaction_digest,
        effect_digests=effect_ids,
        candidate=candidate,
    )
    expected_source = source_digest(candidate.sources[0])
    assert case.context.candidate_digest == evidence_candidate_digest(candidate)
    assert case.context.source_digests == (expected_source,)
    assert case.context.evidence_digests == (expected_source,)
    assert case.context.transaction_digest == transaction.transaction_digest
    assert case.context.effect_digests == effect_ids
    assert case.level is ReviewLevel.HUMAN_REQUIRED


def test_candidate_revision_readback_rejects_runtime_payload_tampering():
    candidate = _candidate()
    corrected = _candidate(candidate_id="corrected", location="南城")
    case = _case(candidate)
    decision = make_decision(
        case, ReviewAction.CORRECT, corrected_candidate=corrected
    )
    raw = decision_to_dict(decision)
    assert isinstance(decision.correction, CandidateRevision)
    json_payload = copy.deepcopy(raw)
    candidate_payload = json.loads(json_payload["correction"]["candidate_json"])
    # Runtime IDs do not alter semantic candidate_digest, but the immutable
    # correction payload is still content-bound by revision_digest.
    candidate_payload["candidate_id"] = "tampered-runtime-id"
    json_payload["correction"]["candidate_json"] = json.dumps(
        candidate_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(ReviewDomainError, match="revision_digest_mismatch"):
        decision_from_dict(json_payload)

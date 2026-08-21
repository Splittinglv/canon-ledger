from __future__ import annotations

import hashlib

import pytest
from pydantic import TypeAdapter, ValidationError

from scripts.data_modules.canon_v3.compiler import CompileError, compile_transaction
from scripts.data_modules.canon_v3.evidence import EvidenceValidationError
from scripts.data_modules.canon_v3.schema import (
    canonical_json,
    canonical_digest,
    AuthorAxiomSource,
    FactCandidate,
    FactClaim,
    EntityObservedClaim,
    FactKind,
    ObservationKind,
    EntityObservedClaim,
    PowerBreakthroughClaim,
    PresenceObservedClaim,
    ReviewLevel,
    ReviewObservation,
    TransactionState,
)


DOC_HASH = "d" * 64


def _span(source_id: str, quote: str, *, start: int = 0) -> dict[str, object]:
    return {
        "source_type": "manuscript_span",
        "source_id": source_id,
        "document_sha256": DOC_HASH,
        "chapter": 3,
        "start": start,
        "end": start + len(quote.encode("utf-8")),
        "quote": quote,
        "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
    }


def _power_candidate(candidate_id: str = "candidate-power") -> FactCandidate:
    quote = "林舟的境界从炼气突破到了筑基。"
    return FactCandidate(
        candidate_id=candidate_id,
        claim=PowerBreakthroughClaim(
            subject="林舟", system="境界", before="炼气", after="筑基"
        ),
        sources=(_span("chapter-span", quote),),
        support_map={
            "subject": ("chapter-span",),
            "system": ("chapter-span",),
            "before": ("chapter-span",),
            "after": ("chapter-span",),
        },
    )


def _presence_candidate(candidate_id: str = "candidate-presence") -> FactCandidate:
    quote = "苏月仍在青云殿内。"
    return FactCandidate(
        candidate_id=candidate_id,
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿", presence="在"
        ),
        sources=(_span("presence-span", quote),),
        support_map={
            "subject": ("presence-span",),
            "location": ("presence-span",),
            "presence": ("presence-span",),
        },
    )


def test_unrelated_quote_cannot_support_structured_fact() -> None:
    candidate = FactCandidate(
        candidate_id="candidate-forged",
        claim=PowerBreakthroughClaim(
            subject="魔尊", system="境界", before="炼气", after="渡劫"
        ),
        sources=(_span("unrelated", "林舟走进大厅。"),),
        support_map={
            "subject": ("unrelated",),
            "system": ("unrelated",),
            "before": ("unrelated",),
            "after": ("unrelated",),
        },
    )
    with pytest.raises(EvidenceValidationError, match="not supported"):
        compile_transaction([candidate], [], "GENESIS")


def test_negated_presence_cannot_support_positive_presence_claim() -> None:
    quote = "苏月不在青云殿。"
    candidate = FactCandidate(
        candidate_id="candidate-negated-presence",
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿", presence="在"
        ),
        sources=(_span("negated-span", quote),),
        support_map={
            "subject": ("negated-span",),
            "location": ("negated-span",),
            "presence": ("negated-span",),
        },
    )

    with pytest.raises(EvidenceValidationError, match="presence='在'"):
        compile_transaction([candidate], [], "GENESIS")


def test_manuscript_span_uses_utf8_byte_offsets_for_chinese() -> None:
    source = _span("nonzero-span", "林舟突破。", start=17)
    candidate = FactCandidate(
        candidate_id="candidate-nonzero-span",
        claim=PresenceObservedClaim(
            subject="林舟", location="突破", presence="突破"
        ),
        sources=(source,),
        support_map={
            "subject": ("nonzero-span",),
            "location": ("nonzero-span",),
            "presence": ("nonzero-span",),
        },
    )

    assert candidate.sources[0].start == 17
    assert candidate.sources[0].end == 17 + len("林舟突破。".encode("utf-8"))


def test_author_axiom_is_hash_bound_and_can_support_exact_fields() -> None:
    value = {"entity": "林舟", "aliases": ["阿舟"]}
    source = AuthorAxiomSource(
        source_id="author-setting",
        document_path="story/MASTER_SETTING.md",
        document_sha256="a" * 64,
        json_pointer="/characters/lin-zhou",
        value=value,
        value_sha256=canonical_digest(value),
    )
    candidate = FactCandidate(
        candidate_id="candidate-author-axiom",
        claim=EntityObservedClaim(entity="林舟", aliases=("阿舟",)),
        sources=(source,),
        support_map={
            "entity": ("author-setting",),
            "aliases": ("author-setting",),
        },
    )

    transaction = compile_transaction([candidate], [], "GENESIS")

    assert transaction.state == TransactionState.AWAITING_HUMAN
    assert transaction.requirements[0].checkpoint is True


def test_mutated_author_axiom_cannot_change_bound_evidence() -> None:
    mutable_value = {"entity": "林舟"}
    source = AuthorAxiomSource(
        source_id="author-setting-mutable",
        document_path="story/MASTER_SETTING.md",
        document_sha256="a" * 64,
        json_pointer="/characters/lin-zhou",
        value=mutable_value,
        value_sha256=canonical_digest(mutable_value),
    )
    candidate = FactCandidate(
        candidate_id="candidate-author-mutation",
        claim=EntityObservedClaim(entity="林舟"),
        sources=(source,),
        support_map={"entity": ("author-setting-mutable",)},
    )
    # Pydantic owns a nested mapping, but frozen models are shallow; simulate a
    # hostile caller mutating it after initial validation.
    assert isinstance(source.value, dict)
    source.value["entity"] = "魔尊"

    with pytest.raises(ValidationError, match="value_sha256"):
        compile_transaction([candidate], [], "GENESIS")


def test_every_claim_field_requires_a_support_binding() -> None:
    quote = "林舟从炼气突破到了筑基。"
    with pytest.raises(ValidationError, match="system"):
        FactCandidate(
            candidate_id="candidate-missing-support",
            claim=PowerBreakthroughClaim(
                subject="林舟", system="境界", before="炼气", after="筑基"
            ),
            sources=(_span("span", quote),),
            support_map={
                "subject": ("span",),
                "before": ("span",),
                "after": ("span",),
            },
        )


def test_input_order_does_not_change_transaction_digest() -> None:
    power = _power_candidate()
    presence = _presence_candidate()
    weak = ReviewObservation(
        observation_id="obs-weak",
        candidate_id=power.candidate_id,
        kind=ObservationKind.AUDIT,
        level=ReviewLevel.AUDIT_ONLY,
        reason="低概率抽查",
    )
    advisory = ReviewObservation(
        observation_id="obs-presence",
        candidate_id=presence.candidate_id,
        kind=ObservationKind.ADVISORY,
        level=ReviewLevel.ADVISORY,
        reason="位置措辞可复核",
    )

    first = compile_transaction(
        [power, presence], [weak, advisory], "GENESIS"
    )
    second = compile_transaction(
        [presence, power], [advisory, weak], "GENESIS"
    )

    assert first.transaction_digest == second.transaction_digest
    assert first == second


def test_runtime_ids_source_order_and_set_like_alias_order_do_not_change_digest() -> None:
    quote = "林舟又名阿舟，也被称为青衣客。"

    def candidate(candidate_id: str, source_id: str, aliases: tuple[str, ...]) -> FactCandidate:
        return FactCandidate(
            candidate_id=candidate_id,
            claim=EntityObservedClaim(entity="林舟", aliases=aliases),
            sources=(_span(source_id, quote),),
            support_map={"entity": (source_id,), "aliases": (source_id,)},
        )

    first = compile_transaction(
        [candidate("candidate-a", "span-a", ("阿舟", "青衣客"))], [], "GENESIS"
    )
    second = compile_transaction(
        [candidate("candidate-b", "span-b", ("青衣客", "阿舟"))], [], "GENESIS"
    )

    assert first.transaction_digest == second.transaction_digest
    assert first.effects == second.effects


def test_same_slot_same_result_span_never_falls_back_to_hash_order() -> None:
    quote = "苏月先在青云殿，随后在山门。"

    def presence(candidate_id: str, location: str) -> FactCandidate:
        return FactCandidate(
            candidate_id=candidate_id,
            claim=PresenceObservedClaim(
                subject="苏月", location=location, presence="在"
            ),
            sources=(_span(f"span-{candidate_id}", quote),),
            support_map={
                "subject": (f"span-{candidate_id}",),
                "location": (f"span-{candidate_id}",),
                "presence": (f"span-{candidate_id}",),
            },
        )

    with pytest.raises(CompileError, match="ambiguous narrative order"):
        compile_transaction(
            [
                presence("first-presence", "青云殿"),
                presence("second-presence", "山门"),
            ],
            [],
            "GENESIS",
        )


def test_weak_observation_cannot_hide_runtime_checkpoint() -> None:
    power = _power_candidate()
    weak = ReviewObservation(
        observation_id="obs-weak",
        candidate_id=power.candidate_id,
        kind=ObservationKind.AUDIT,
        level=ReviewLevel.AUDIT_ONLY,
        reason="模型认为概率很低",
    )

    transaction = compile_transaction([power], [weak], "GENESIS")

    assert transaction.state == TransactionState.AWAITING_HUMAN
    assert len(transaction.requirements) == 1
    requirement = transaction.requirements[0]
    assert requirement.level == ReviewLevel.HUMAN_REQUIRED
    assert requirement.checkpoint is True
    assert [action.value for action in requirement.allowed_actions] == [
        "approve",
        "rewrite",
    ]


def test_required_unresolved_transaction_cannot_claim_ready() -> None:
    transaction = compile_transaction([_power_candidate()], [], "GENESIS")
    payload = transaction.model_dump(mode="python")
    payload["state"] = TransactionState.READY

    with pytest.raises(ValidationError, match="state must be awaiting_human"):
        type(transaction).model_validate(payload)


def test_style_is_not_a_fact_kind_and_cannot_be_required_review() -> None:
    assert len(FactKind) == 15

    with pytest.raises(ValidationError):
        TypeAdapter(FactClaim).validate_python(
            {"kind": "style", "description": "文风不够紧张"}
        )

    with pytest.raises(ValidationError, match="style/prose"):
        ReviewObservation(
            observation_id="obs-style",
            kind=ObservationKind.STYLE,
            level=ReviewLevel.HUMAN_REQUIRED,
            reason="文风不够紧张",
        )


def test_compiler_has_no_delta_inputs() -> None:
    with pytest.raises(TypeError):
        compile_transaction(  # type: ignore[call-arg]
            [_presence_candidate()],
            [],
            "GENESIS",
            state_deltas=[{"subject": "苏月"}],
        )


def test_extra_model_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        FactCandidate.model_validate(
            {
                **_power_candidate().model_dump(mode="python"),
                "blocking": False,
            }
        )


def test_new_entity_alias_registration_is_always_a_checkpoint() -> None:
    quote = "苏月以月姬之名进入大殿。"
    candidate = FactCandidate(
        candidate_id="candidate-alias",
        claim=EntityObservedClaim(entity="苏月", aliases=("月姬",)),
        sources=(_span("alias-span", quote),),
        support_map={"entity": ("alias-span",), "aliases": ("alias-span",)},
    )

    transaction = compile_transaction([candidate], [], "GENESIS")

    assert transaction.state == TransactionState.AWAITING_HUMAN
    assert transaction.requirements[0].checkpoint is True


def test_canonical_json_does_not_collapse_non_string_object_keys() -> None:
    with pytest.raises(ValueError, match="keys must be strings"):
        canonical_json({1: "not-a-json-object-key"})


def test_candidate_rejects_evidence_source_not_used_by_support_map() -> None:
    base = _presence_candidate()
    with pytest.raises(ValidationError, match="all participate in support_map"):
        FactCandidate(
            candidate_id=base.candidate_id,
            claim=base.claim,
            sources=(
                *base.sources,
                _span("unused-rain", "窗外下着雨。", start=100),
            ),
            support_map=base.support_map,
        )


def test_candidate_rejects_duplicate_evidence_content_under_new_source_id() -> None:
    base = _presence_candidate()
    duplicate = {
        **base.sources[0].model_dump(mode="json"),
        "source_id": "duplicate-presence-span",
    }
    with pytest.raises(ValidationError, match="unique evidence content"):
        FactCandidate(
            candidate_id=base.candidate_id,
            claim=base.claim,
            sources=(*base.sources, duplicate),
            support_map={
                "subject": ("presence-span",),
                "location": ("presence-span",),
                "presence": ("presence-span",),
            },
        )

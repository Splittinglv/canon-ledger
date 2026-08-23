from __future__ import annotations

import copy

import pytest

from scripts.data_modules.canon_v3.public_protocol import (
    HumanActionProfile,
    PublicHumanProtocolError,
    serialize_public_human_case,
)
from scripts.data_modules.canon_v3.review import (
    DecisionContext,
    ReviewCase,
    ReviewCaseKind,
    ReviewLevel,
    case_to_dict,
)
from scripts.data_modules.canon_v3.schema import canonical_digest


def _checkpoint_case() -> ReviewCase:
    return ReviewCase(
        case_key="checkpoint-public-protocol",
        kind=ReviewCaseKind.CHECKPOINT,
        level=ReviewLevel.HUMAN_REQUIRED,
        context=DecisionContext(
            chapter=1,
            chapter_digest="1" * 64,
            candidate_digest="2" * 64,
            evidence_digests=("3" * 64,),
            source_digests=("4" * 64,),
            parent_head="5" * 64,
            prior_fact_hashes=(),
            policy_version="canon-v3/test-policy",
            transaction_digest="6" * 64,
            effect_digests=("7" * 64,),
        ),
        candidate_id="candidate-public-protocol",
        reasons=("permanent_fact_checkpoint",),
    )


def test_public_projection_does_not_change_persisted_case_or_target_digest() -> None:
    case = _checkpoint_case()
    stored = case_to_dict(case)
    stored_before = copy.deepcopy(stored)
    stored_digest = canonical_digest(stored)
    material_digest = "8" * 64

    public = serialize_public_human_case(
        {
            **stored,
            "decision_head_hash": None,
            "review_material": {"material_digest": material_digest},
        },
        profile=HumanActionProfile.CHAPTER_CHECKPOINT,
        target_digest=case.target_digest,
        material_digest=material_digest,
        expected_decision_head_hash=None,
    )

    assert stored == stored_before
    assert canonical_digest(stored) == stored_digest
    assert case_to_dict(case) == stored_before
    assert public["target_digest"] == stored_before["target_digest"]
    assert public["allowed_actions"] == ["approve", "rewrite"]
    assert public["decision_binding"] == {
        "target_digest": case.target_digest,
        "material_digest": material_digest,
        "expected_decision_head_hash": None,
    }
    assert "allowed_actions" not in stored
    assert "decision_binding" not in stored


def test_public_projection_rejects_mixed_case_material() -> None:
    case = _checkpoint_case()
    with pytest.raises(
        PublicHumanProtocolError,
        match="nested_material_digest_mismatch",
    ):
        serialize_public_human_case(
            {
                **case_to_dict(case),
                "review_material": {"material_digest": "9" * 64},
            },
            profile=HumanActionProfile.CHAPTER_CHECKPOINT,
            target_digest=case.target_digest,
            material_digest="8" * 64,
            expected_decision_head_hash=None,
        )

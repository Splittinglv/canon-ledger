#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from data_modules.canon_v3.agent_protocol import (
    CandidateDraft,
    ReviewerOutput,
    assemble_proposal,
    candidate_digest_map,
    protocol_schema,
    validate_agent_artifact,
)


SHA = "a" * 64


def _candidate() -> dict:
    quote = "林舟的生死变为存活"
    return {
        "candidate_id": "cand-life",
        "claim": {
            "kind": "character_state_changed",
            "subject": "林舟",
            "attribute": "生死",
            "after": "存活",
        },
        "sources": [
            {
                "source_type": "manuscript_span",
                "source_id": "src-life",
                "document_sha256": SHA,
                "chapter": 1,
                "start": 0,
                "end": len(quote.encode("utf-8")),
                "quote": quote,
                "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            }
        ],
        "support_map": {
            "subject": ["src-life"],
            "attribute": ["src-life"],
            "after": ["src-life"],
        },
        "identity_links": {},
    }


def _draft() -> dict:
    return {
        "schema_version": "canon-v3/candidate-draft/v1",
        "chapter": 1,
        "chapter_binding": {
            "schema_version": "canon-ledger-chapter-content-binding/v1",
            "chapter": 1,
            "path": "正文/第0001章.md",
            "sha256": SHA,
            "bytes": 21,
        },
        "parent_head": "b" * 64,
        "workflow_digest": "c" * 64,
        "author_axiom_digest": "d" * 64,
        "entity_registry_digest": "e" * 64,
        "expected_stage_digest": None,
        "candidates": [_candidate()],
        "extraction_blockers": [],
    }


def _review(digest: str, *, candidate_id: str = "cand-life") -> dict:
    return {
        "schema_version": "canon-v3/reviewer-output/v3",
        "chapter": 1,
        "chapter_sha256": SHA,
        "parent_head": "b" * 64,
        "author_axiom_digest": "d" * 64,
        "entity_registry_digest": "e" * 64,
        "candidate_digest_map": {candidate_id: digest},
        "candidate_digests": [digest],
        "observations": [],
        "scan_attestations": [
            {
                "attestation_id": "scan-1",
                "scanner": "reviewer",
                "scanner_version": "canon-v3-reviewer-v3",
                "chapter_sha256": SHA,
                "parent_head": "b" * 64,
                "author_axiom_digest": "d" * 64,
                "entity_registry_digest": "e" * 64,
                "dimensions": [
                    "setting",
                    "timeline",
                    "continuity",
                    "character",
                    "logic",
                ],
                "status": "complete",
                "checked_candidate_digests": [digest],
            }
        ],
        "extraction_incomplete": [],
    }


def test_runtime_computes_candidate_digest_and_assembles_nonempty_proposal() -> None:
    draft = CandidateDraft.model_validate(_draft())
    digest = candidate_digest_map(draft)["cand-life"]

    proposal = assemble_proposal(_draft(), _review(digest))

    assert proposal["schema_version"] == "canon-v3/proposal-batch/v2"
    assert proposal["candidates"][0]["candidate_id"] == "cand-life"
    assert proposal["scan_attestations"][0]["checked_candidate_digests"] == [
        digest
    ]


def test_reviewer_cannot_guess_or_replace_candidate_digest() -> None:
    draft = CandidateDraft.model_validate(_draft())
    digest = candidate_digest_map(draft)["cand-life"]
    with pytest.raises(ValueError, match="binding mismatch"):
        assemble_proposal(_draft(), _review("f" * 64))
    assert ReviewerOutput.model_validate(_review(digest)).candidate_digests == (
        digest,
    )


def test_reviewer_binding_rejects_candidate_id_swap_after_review() -> None:
    raw_draft = _draft()
    second = deepcopy(_candidate())
    second["candidate_id"] = "cand-location"
    second["claim"] = {
        "kind": "presence_observed",
        "subject": "林舟",
        "location": "北门",
        "presence": "在场",
    }
    quote = "林舟在北门在场"
    second["sources"][0].update(
        {
            "source_id": "src-location",
            "quote": quote,
            "end": len(quote.encode("utf-8")),
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        }
    )
    second["support_map"] = {
        "subject": ["src-location"],
        "location": ["src-location"],
        "presence": ["src-location"],
    }
    raw_draft["candidates"].append(second)
    draft = CandidateDraft.model_validate(raw_draft)
    exact_map = candidate_digest_map(draft)
    review = _review(exact_map["cand-life"])
    review["candidate_digest_map"] = exact_map
    review["candidate_digests"] = sorted(set(exact_map.values()))
    review["scan_attestations"][0]["checked_candidate_digests"] = sorted(
        set(exact_map.values())
    )

    swapped_draft = deepcopy(raw_draft)
    swapped_draft["candidates"][0]["candidate_id"] = "cand-location"
    swapped_draft["candidates"][1]["candidate_id"] = "cand-life"

    validated = ReviewerOutput.model_validate(review)
    assert validated.candidate_digest_map == exact_map
    with pytest.raises(ValueError, match="binding mismatch"):
        assemble_proposal(swapped_draft, review)


def test_reviewer_validator_rejects_digest_set_without_exact_id_map() -> None:
    draft = CandidateDraft.model_validate(_draft())
    digest = candidate_digest_map(draft)["cand-life"]
    missing_map = _review(digest)
    missing_map.pop("candidate_digest_map")
    with pytest.raises(ValidationError):
        ReviewerOutput.model_validate(missing_map)

    mismatched_map = _review(digest, candidate_id="other-candidate")
    mismatched_map["observations"] = [
        {
            "observation_id": "obs-life",
            "candidate_id": "cand-life",
            "kind": "audit",
            "level": "audit_only",
            "reason": "绑定反例",
            "prior_fact_digests": [],
        }
    ]
    with pytest.raises(ValidationError, match="outside candidate_digest_map"):
        ReviewerOutput.model_validate(mismatched_map)


def test_candidate_validator_rejects_duplicate_semantics_under_different_ids() -> None:
    digest = candidate_digest_map(CandidateDraft.model_validate(_draft()))[
        "cand-life"
    ]
    raw = _draft()
    duplicate = deepcopy(raw["candidates"][0])
    duplicate["candidate_id"] = "cand-life-duplicate"
    raw["candidates"].append(duplicate)
    draft = CandidateDraft.model_validate(raw)

    with pytest.raises(ValueError, match="semantic digests must be unique"):
        candidate_digest_map(draft)

    review = _review(digest)
    review["candidate_digest_map"] = {
        "cand-life": digest,
        "cand-life-duplicate": digest,
    }
    with pytest.raises(ValidationError, match="map values must be unique"):
        ReviewerOutput.model_validate(review)


def test_strict_agent_schema_rejects_extra_fields_and_is_exportable() -> None:
    malformed = {**_draft(), "blocking_count": 0}
    with pytest.raises(ValidationError):
        CandidateDraft.model_validate(malformed)
    schema = protocol_schema("candidate-draft")
    assert schema["additionalProperties"] is False
    assert "FactCandidate" in schema["$defs"]

    reviewer_schema = protocol_schema("reviewer-output")
    assert reviewer_schema["properties"]["schema_version"]["const"] == (
        "canon-v3/reviewer-output/v3"
    )
    assert "candidate_digest_map" in reviewer_schema["required"]
    assert reviewer_schema["properties"]["candidate_digest_map"][
        "patternProperties"
    ]


def test_reviewer_validator_returns_the_exact_candidate_binding_map() -> None:
    draft = CandidateDraft.model_validate(_draft())
    exact_map = candidate_digest_map(draft)
    validated = validate_agent_artifact(
        "reviewer-output", _review(exact_map["cand-life"])
    )

    assert validated["valid"] is True
    assert validated["candidate_digest_map"] == exact_map
    assert validated["artifact"]["candidate_digest_map"] == exact_map


def test_author_axiom_proposal_schema_and_validator_are_public() -> None:
    payload = {
        "schema_version": "canon-v3/author-axiom-proposal/v2",
        "parent_head": "1" * 64,
        "workflow_digest": "2" * 64,
        "active_author_axiom_digest": "3" * 64,
        "expected_stage_digest": None,
        "records": [],
        "genesis_overrides": [],
    }

    schema = protocol_schema("author-axiom-proposal")
    validated = validate_agent_artifact("author-axiom-proposal", payload)

    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["const"] == (
        "canon-v3/author-axiom-proposal/v2"
    )
    assert validated["valid"] is True
    assert validated["record_digests"] == {}
    assert validated["artifact"] == payload

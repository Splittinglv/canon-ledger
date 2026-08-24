#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Executable protocol between data-agent, reviewer and Canon v3 prepare."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..chapter_content_binding import ChapterContentBinding
from .evidence import candidate_digest
from .schema import (
    FactCandidate,
    IDENTIFIER_PATTERN,
    ReviewObservation,
    ScanAttestation,
    ScanStatus,
)
from .service import ChapterProposalBatch, PROPOSAL_SCHEMA, REQUIRED_SCAN_DIMENSIONS


CANDIDATE_DRAFT_SCHEMA = "canon-v3/candidate-draft/v1"
REVIEWER_OUTPUT_SCHEMA = "canon-v3/reviewer-output/v3"
AGENT_VALIDATION_SCHEMA = "canon-v3/agent-validation/v1"


CandidateId = Annotated[str, Field(pattern=IDENTIFIER_PATTERN)]
CandidateDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateDraft(_StrictModel):
    schema_version: Literal[CANDIDATE_DRAFT_SCHEMA] = CANDIDATE_DRAFT_SCHEMA
    chapter: int = Field(ge=1)
    chapter_binding: ChapterContentBinding
    parent_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    author_axiom_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_stage_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    candidates: tuple[FactCandidate, ...] = ()
    extraction_blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def draft_is_closed(self) -> "CandidateDraft":
        if self.chapter_binding.chapter != self.chapter:
            raise ValueError("candidate draft chapter binding mismatch")
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("candidate draft candidate_id values must be unique")
        if any(not item.strip() for item in self.extraction_blockers):
            raise ValueError("extraction blockers must be non-empty strings")
        return self


class ReviewerOutput(_StrictModel):
    schema_version: Literal[REVIEWER_OUTPUT_SCHEMA] = REVIEWER_OUTPUT_SCHEMA
    chapter: int = Field(ge=1)
    chapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    author_axiom_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_digest_map: dict[CandidateId, CandidateDigest]
    candidate_digests: tuple[str, ...] = ()
    observations: tuple[ReviewObservation, ...] = ()
    scan_attestations: tuple[ScanAttestation, ...] = Field(min_length=1)
    extraction_incomplete: tuple[str, ...] = ()

    @field_validator("candidate_digests")
    @classmethod
    def candidate_hashes_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("reviewer candidate digests must be sorted and unique")
        return value

    @field_validator("candidate_digest_map")
    @classmethod
    def candidate_bindings_are_canonical(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        if len(value.values()) != len(set(value.values())):
            raise ValueError(
                "reviewer candidate digest map values must be unique"
            )
        return dict(sorted(value.items()))

    @model_validator(mode="after")
    def reviewer_scan_is_closed(self) -> "ReviewerOutput":
        if any(not item.strip() for item in self.extraction_incomplete):
            raise ValueError("extraction_incomplete values must be non-empty strings")
        complete = [
            item for item in self.scan_attestations if item.status == ScanStatus.COMPLETE
        ]
        if len(complete) != 1:
            raise ValueError("reviewer output requires exactly one complete attestation")
        if tuple(sorted(set(self.candidate_digest_map.values()))) != (
            self.candidate_digests
        ):
            raise ValueError(
                "reviewer candidate digest map must exactly cover candidate_digests"
            )
        unknown_observation_ids = sorted(
            {
                observation.candidate_id
                for observation in self.observations
                if observation.candidate_id is not None
                and observation.candidate_id not in self.candidate_digest_map
            }
        )
        if unknown_observation_ids:
            raise ValueError(
                "review observations reference candidate IDs outside "
                "candidate_digest_map: " + ", ".join(unknown_observation_ids)
            )
        attestation = complete[0]
        if set(attestation.dimensions) != set(REQUIRED_SCAN_DIMENSIONS):
            raise ValueError("complete attestation must cover all required dimensions")
        if (
            attestation.chapter_sha256 != self.chapter_sha256
            or attestation.parent_head != self.parent_head
            or attestation.author_axiom_digest != self.author_axiom_digest
            or attestation.entity_registry_digest != self.entity_registry_digest
            or tuple(sorted(attestation.checked_candidate_digests))
            != self.candidate_digests
        ):
            raise ValueError("complete attestation authority binding mismatch")
        return self


def candidate_digest_map(draft: CandidateDraft) -> dict[str, str]:
    result = {
        candidate.candidate_id: candidate_digest(candidate)
        for candidate in draft.candidates
    }
    if len(result.values()) != len(set(result.values())):
        raise ValueError("candidate draft semantic digests must be unique")
    return result


def validate_agent_artifact(kind: str, raw: Mapping[str, Any]) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip()
    if normalized_kind == "candidate-draft":
        draft = CandidateDraft.model_validate(raw)
        return {
            "schema_version": AGENT_VALIDATION_SCHEMA,
            "kind": normalized_kind,
            "valid": True,
            "candidate_digest_map": candidate_digest_map(draft),
            "artifact": draft.model_dump(mode="json"),
        }
    if normalized_kind == "reviewer-output":
        review = ReviewerOutput.model_validate(raw)
        return {
            "schema_version": AGENT_VALIDATION_SCHEMA,
            "kind": normalized_kind,
            "valid": True,
            "candidate_digest_map": dict(review.candidate_digest_map),
            "candidate_digests": list(review.candidate_digests),
            "artifact": review.model_dump(mode="json"),
        }
    if normalized_kind == "author-axiom-proposal":
        from .author_axiom import AuthorAxiomProposal, record_digest
        from .fact_boundary import (
            FactBoundaryClass,
            classify_author_axiom_leaf,
        )

        proposal = AuthorAxiomProposal.model_validate(raw)
        non_fact_keys = sorted(
            record.axiom_key
            for record in proposal.records
            if classify_author_axiom_leaf(
                axiom_key=record.axiom_key,
                category=record.category,
                value=record.source.value,
            )
            is FactBoundaryClass.KNOWN_SOFT
        )
        if non_fact_keys:
            raise ValueError(
                "canon_v3_author_axiom_non_fact_semantics_forbidden:"
                + ",".join(non_fact_keys)
            )
        return {
            "schema_version": AGENT_VALIDATION_SCHEMA,
            "kind": normalized_kind,
            "valid": True,
            "record_digests": {
                record.axiom_key: record_digest(record)
                for record in proposal.records
            },
            "artifact": proposal.model_dump(mode="json"),
        }
    raise ValueError(f"canon_v3_unknown_agent_artifact_kind:{normalized_kind}")


def assemble_proposal(
    raw_draft: Mapping[str, Any],
    raw_review: Mapping[str, Any],
) -> dict[str, Any]:
    draft = CandidateDraft.model_validate(raw_draft)
    review = ReviewerOutput.model_validate(raw_review)
    draft_digest_map = candidate_digest_map(draft)
    digests = tuple(sorted(draft_digest_map.values()))
    if draft.extraction_blockers:
        raise ValueError("candidate draft has unresolved extraction blockers")
    if review.extraction_incomplete:
        raise ValueError("reviewer output reports incomplete extraction")
    if (
        review.chapter != draft.chapter
        or review.chapter_sha256 != draft.chapter_binding.sha256
        or review.parent_head != draft.parent_head
        or review.author_axiom_digest != draft.author_axiom_digest
        or review.entity_registry_digest != draft.entity_registry_digest
        or review.candidate_digest_map != draft_digest_map
        or review.candidate_digests != digests
    ):
        raise ValueError("candidate draft and reviewer output binding mismatch")

    known_ids = {candidate.candidate_id for candidate in draft.candidates}
    if any(
        observation.candidate_id is not None
        and observation.candidate_id not in known_ids
        for observation in review.observations
    ):
        raise ValueError("review observation references unknown candidate")

    proposal = ChapterProposalBatch(
        schema_version=PROPOSAL_SCHEMA,
        chapter=draft.chapter,
        chapter_binding=draft.chapter_binding,
        parent_head=draft.parent_head,
        workflow_digest=draft.workflow_digest,
        author_axiom_digest=draft.author_axiom_digest,
        entity_registry_digest=draft.entity_registry_digest,
        expected_stage_digest=draft.expected_stage_digest,
        candidates=draft.candidates,
        observations=review.observations,
        scan_attestations=review.scan_attestations,
    )
    return proposal.model_dump(mode="json")


def protocol_schema(kind: str) -> dict[str, Any]:
    normalized_kind = str(kind or "").strip()
    if normalized_kind == "candidate-draft":
        return CandidateDraft.model_json_schema()
    if normalized_kind == "reviewer-output":
        return ReviewerOutput.model_json_schema()
    if normalized_kind == "proposal-batch":
        return ChapterProposalBatch.model_json_schema()
    if normalized_kind == "author-axiom-proposal":
        from .author_axiom import AuthorAxiomProposal

        return AuthorAxiomProposal.model_json_schema()
    raise ValueError(f"canon_v3_unknown_agent_schema_kind:{normalized_kind}")


__all__ = [
    "AGENT_VALIDATION_SCHEMA",
    "CANDIDATE_DRAFT_SCHEMA",
    "CandidateDraft",
    "REVIEWER_OUTPUT_SCHEMA",
    "ReviewerOutput",
    "assemble_proposal",
    "candidate_digest_map",
    "protocol_schema",
    "validate_agent_artifact",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict, storage-agnostic domain models for the v3 canon compiler.

Nothing in this module reads or writes project files.  Models intentionally use
``extra='forbid'`` so a model-produced hint can never silently become a canon
field.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "canon-v3/domain-2"
COMPILER_VERSION = "canon-v3/compiler-2"
POLICY_VERSION = "canon-v3/checkpoint-2"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,191}$"
UNBOUND_ENTITY_REGISTRY_DIGEST = "0" * 64


def _json_compatible(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_compatible(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} JSON object keys must be strings")
            _json_compatible(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains non-JSON value {type(value).__name__}")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    return value


def canonical_json(value: Any) -> str:
    """Return the single canonical JSON representation used by v3 digests."""

    normalized = _canonical_value(value)
    _json_compatible(normalized)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactKind(str, Enum):
    CHARACTER_STATE_CHANGED = "character_state_changed"
    RELATIONSHIP_CHANGED = "relationship_changed"
    WORLD_RULE_REVEALED = "world_rule_revealed"
    WORLD_RULE_BROKEN = "world_rule_broken"
    POWER_BREAKTHROUGH = "power_breakthrough"
    ARTIFACT_OBTAINED = "artifact_obtained"
    ENTITY_OBSERVED = "entity_observed"
    TIMELINE_OBSERVED = "timeline_observed"
    KNOWLEDGE_STATE_CHANGED = "knowledge_state_changed"
    PRESENCE_OBSERVED = "presence_observed"
    CUSTODY_CHANGED = "custody_changed"
    PROMISE_CREATED = "promise_created"
    PROMISE_PAID_OFF = "promise_paid_off"
    OPEN_LOOP_CREATED = "open_loop_created"
    OPEN_LOOP_CLOSED = "open_loop_closed"


class IdentityNamespace(str, Enum):
    ACTOR = "actor"
    ITEM = "item"
    LOCATION = "location"


class ManuscriptSpanSource(StrictModel):
    source_type: Literal["manuscript_span"] = "manuscript_span"
    source_id: str = Field(pattern=IDENTIFIER_PATTERN)
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    chapter: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    quote_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def bind_quote_to_span(self) -> "ManuscriptSpanSource":
        if self.end <= self.start:
            raise ValueError("manuscript span end must be after start")
        if self.end - self.start != len(self.quote.encode("utf-8")):
            raise ValueError("manuscript span byte length must equal UTF-8 quote length")
        expected = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.quote_sha256 != expected:
            raise ValueError("quote_sha256 does not match quote")
        return self


class AuthorAxiomSource(StrictModel):
    source_type: Literal["author_axiom"] = "author_axiom"
    source_id: str = Field(pattern=IDENTIFIER_PATTERN)
    document_path: str = Field(min_length=1)
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    json_pointer: str = Field(pattern=r"^(?:/(?:[^~/]|~0|~1)*)*$")
    value: Any
    value_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("value")
    @classmethod
    def value_must_be_json(cls, value: Any) -> Any:
        _json_compatible(value)
        return value

    @model_validator(mode="after")
    def bind_value_to_hash(self) -> "AuthorAxiomSource":
        if self.value_sha256 != canonical_digest(self.value):
            raise ValueError("value_sha256 does not match author axiom value")
        return self


class AuthorAxiomCategory(str, Enum):
    """Closed fact-only taxonomy for author-declared long-term axioms.

    Style, prose preferences, outline beats, pacing and other planning
    material intentionally have no representable category here.
    """

    WORLD_RULE = "world_rule"
    CHARACTER_IDENTITY = "character_identity"
    CHARACTER_PERMANENT_STATE = "character_permanent_state"
    RELATIONSHIP_BASELINE = "relationship_baseline"
    TIMELINE_ANCHOR = "timeline_anchor"
    KNOWLEDGE_BOUNDARY = "knowledge_boundary"
    ITEM_RULE = "item_rule"
    LOCATION_RULE = "location_rule"


class AuthorAxiomDraftSpanSource(StrictModel):
    """Exact JSON-leaf bytes from a managed, non-active axiom draft.

    Path policy and the containing draft schema are verified against project
    bytes by the author-axiom service.  This model closes the internal binding
    between the byte span, parsed value and their digests.
    """

    source_type: Literal["author_axiom_draft_span"] = (
        "author_axiom_draft_span"
    )
    source_id: str = Field(pattern=IDENTIFIER_PATTERN)
    document_path: str = Field(min_length=1)
    document_sha256: str = Field(pattern=SHA256_PATTERN)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    quote_sha256: str = Field(pattern=SHA256_PATTERN)
    json_pointer: str = Field(pattern=r"^(?:/(?:[^~/]|~0|~1)*)*$")
    value: Any
    value_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("value")
    @classmethod
    def value_must_be_json(cls, value: Any) -> Any:
        _json_compatible(value)
        return value

    @model_validator(mode="after")
    def bind_value_and_span(self) -> "AuthorAxiomDraftSpanSource":
        if self.end <= self.start:
            raise ValueError("author axiom draft span end must be after start")
        quote_raw = self.quote.encode("utf-8")
        if self.end - self.start != len(quote_raw):
            raise ValueError(
                "author axiom draft span byte length must equal UTF-8 quote length"
            )
        if hashlib.sha256(quote_raw).hexdigest() != self.quote_sha256:
            raise ValueError("author axiom draft quote_sha256 mismatch")
        if canonical_digest(self.value) != self.value_sha256:
            raise ValueError("author axiom draft value_sha256 mismatch")
        try:
            parsed_quote = json.loads(self.quote)
        except json.JSONDecodeError as exc:
            raise ValueError("author axiom draft quote must be one JSON value") from exc
        if parsed_quote != self.value or type(parsed_quote) is not type(self.value):
            raise ValueError("author axiom draft quote does not equal value")
        if not self.json_pointer.startswith("/author_axioms/"):
            raise ValueError(
                "author axiom draft pointer must be below /author_axioms"
            )
        return self


class AuthorAxiomRecord(StrictModel):
    """One proposed hard fact; it is not active until axiom finalize."""

    axiom_key: str = Field(pattern=IDENTIFIER_PATTERN)
    category: AuthorAxiomCategory
    source: AuthorAxiomDraftSpanSource

    @model_validator(mode="after")
    def bind_key_to_pointer(self) -> "AuthorAxiomRecord":
        pointer_tail = self.source.json_pointer[len("/author_axioms/") :]
        if not pointer_tail or "/" in pointer_tail:
            raise ValueError(
                "author axiom records must reference a direct /author_axioms leaf"
            )
        decoded = pointer_tail.replace("~1", "/").replace("~0", "~")
        if decoded != self.axiom_key:
            raise ValueError("author axiom key must equal its JSON pointer key")
        return self


SourceRef: TypeAlias = Annotated[
    Union[ManuscriptSpanSource, AuthorAxiomSource],
    Field(discriminator="source_type"),
]


class _Claim(StrictModel):
    pass


class CharacterStateChangedClaim(_Claim):
    kind: Literal["character_state_changed"] = "character_state_changed"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    canonical_field: str | None = Field(default=None, min_length=1)
    subject: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    before: str | None = Field(default=None, min_length=1)
    after: str = Field(min_length=1)


class RelationshipChangedClaim(_Claim):
    kind: Literal["relationship_changed"] = "relationship_changed"
    subject: str = Field(min_length=1)
    object: str = Field(min_length=1)
    before: str | None = Field(default=None, min_length=1)
    after: str = Field(min_length=1)


class WorldRuleRevealedClaim(_Claim):
    kind: Literal["world_rule_revealed"] = "world_rule_revealed"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    rule: str = Field(min_length=1)


class WorldRuleBrokenClaim(_Claim):
    kind: Literal["world_rule_broken"] = "world_rule_broken"
    # Unique occurrence identity; generated from this candidate/evidence.
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # Stable identity of the continuing rule that this occurrence violates.
    rule_slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # May be inherited from the exact parent-slot fact when the prose only
    # describes the exception/violation instead of restating the whole rule.
    rule: str | None = Field(default=None, min_length=1)
    violation: str = Field(min_length=1)


class PowerBreakthroughClaim(_Claim):
    kind: Literal["power_breakthrough"] = "power_breakthrough"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    canonical_field: str | None = Field(default=None, min_length=1)
    subject: str = Field(min_length=1)
    system: str | None = Field(default=None, min_length=1)
    before: str = Field(min_length=1)
    after: str = Field(min_length=1)


class ArtifactObtainedClaim(_Claim):
    kind: Literal["artifact_obtained"] = "artifact_obtained"
    owner: str = Field(min_length=1)
    artifact: str = Field(min_length=1)
    from_holder: str | None = Field(default=None, min_length=1)


class EntityObservedClaim(_Claim):
    kind: Literal["entity_observed"] = "entity_observed"
    namespace: IdentityNamespace = IdentityNamespace.ACTOR
    entity: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    # ``link_to`` is reviewer-selected compiler metadata, never free-form
    # evidence. ``canonical_entity`` is filled by the approved registry plan.
    link_to: str | None = Field(default=None, min_length=1)
    canonical_entity: str | None = Field(default=None, min_length=1)
    new_instance: bool | None = None

    @field_validator("aliases")
    @classmethod
    def aliases_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not alias.strip() for alias in value):
            raise ValueError("entity aliases must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("entity aliases must be unique")
        return value


class TimelineObservedClaim(_Claim):
    kind: Literal["timeline_observed"] = "timeline_observed"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    event: str | None = Field(default=None, min_length=1)
    time_anchor: str = Field(min_length=1)


class KnowledgeStateChangedClaim(_Claim):
    kind: Literal["knowledge_state_changed"] = "knowledge_state_changed"
    # ``knowledge`` is display text and may be paraphrased by a later chapter.
    # The slot is the stable identity of the information state being updated.
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    subject: str = Field(min_length=1)
    # Existing slots may inherit the canonical proposition when the chapter
    # says only “这段记忆/此事”. New slots still require text in the compiler.
    knowledge: str | None = Field(default=None, min_length=1)
    state: str = Field(min_length=1)


class PresenceObservedClaim(_Claim):
    kind: Literal["presence_observed"] = "presence_observed"
    subject: str = Field(min_length=1)
    location: str = Field(min_length=1)
    presence: str = Field(min_length=1)


class CustodyChangedClaim(_Claim):
    kind: Literal["custody_changed"] = "custody_changed"
    item: str = Field(min_length=1)
    from_holder: str | None = Field(default=None, min_length=1)
    to_holder: str = Field(min_length=1)


class PromiseCreatedClaim(_Claim):
    kind: Literal["promise_created"] = "promise_created"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    promisor: str = Field(min_length=1)
    promisee: str | None = Field(default=None, min_length=1)
    promise: str = Field(min_length=1)


class PromisePaidOffClaim(_Claim):
    kind: Literal["promise_paid_off"] = "promise_paid_off"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    promisor: str = Field(min_length=1)
    promise: str | None = Field(default=None, min_length=1)
    outcome: str = Field(min_length=1)


class OpenLoopCreatedClaim(_Claim):
    kind: Literal["open_loop_created"] = "open_loop_created"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    loop: str = Field(min_length=1)


class OpenLoopClosedClaim(_Claim):
    kind: Literal["open_loop_closed"] = "open_loop_closed"
    slot_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    loop: str | None = Field(default=None, min_length=1)
    resolution: str = Field(min_length=1)


FactClaim: TypeAlias = Annotated[
    Union[
        CharacterStateChangedClaim,
        RelationshipChangedClaim,
        WorldRuleRevealedClaim,
        WorldRuleBrokenClaim,
        PowerBreakthroughClaim,
        ArtifactObtainedClaim,
        EntityObservedClaim,
        TimelineObservedClaim,
        KnowledgeStateChangedClaim,
        PresenceObservedClaim,
        CustodyChangedClaim,
        PromiseCreatedClaim,
        PromisePaidOffClaim,
        OpenLoopCreatedClaim,
        OpenLoopClosedClaim,
    ],
    Field(discriminator="kind"),
]


class FactCandidate(StrictModel):
    """A model proposal.  Constructing one does not grant canon authority."""

    candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    claim: FactClaim
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    support_map: dict[str, tuple[str, ...]]
    # Optional exact disambiguation for identity-bearing claim fields. Keys
    # are claim field paths (subject/item/location/...), values are approved
    # canonical entity IDs from the parent registry.
    identity_links: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def support_map_is_complete_and_bound(self) -> "FactCandidate":
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("candidate source_id values must be unique")
        source_content_digests = [
            canonical_digest(
                source.model_dump(mode="json", exclude={"source_id"})
            )
            for source in self.sources
        ]
        if len(source_content_digests) != len(set(source_content_digests)):
            raise ValueError(
                "candidate sources must have unique evidence content"
            )

        claim_data = self.claim.model_dump(mode="python", exclude_none=True)
        expected_fields = {
            key
            for key, value in claim_data.items()
            if key
            not in {
                "kind",
                "namespace",
                "slot_id",
                "rule_slot_id",
                "link_to",
                "canonical_entity",
                "canonical_field",
                "new_instance",
            }
            and value not in ((), [], {})
        }
        actual_fields = set(self.support_map)
        missing = sorted(expected_fields - actual_fields)
        extra = sorted(actual_fields - expected_fields)
        if missing:
            raise ValueError(
                "support_map missing canonical claim fields: " + ", ".join(missing)
            )
        if extra:
            raise ValueError(
                "support_map contains non-claim fields: " + ", ".join(extra)
            )
        known_sources = set(source_ids)
        if any(
            not re.fullmatch(IDENTIFIER_PATTERN, key)
            or not str(value or "").strip()
            for key, value in self.identity_links.items()
        ):
            raise ValueError("identity_links must map claim field names to identities")
        for field_name, refs in self.support_map.items():
            if not refs:
                raise ValueError(f"support_map.{field_name} must not be empty")
            if len(refs) != len(set(refs)):
                raise ValueError(f"support_map.{field_name} source refs must be unique")
            unknown = sorted(set(refs) - known_sources)
            if unknown:
                raise ValueError(
                    f"support_map.{field_name} references unknown sources: "
                    + ", ".join(unknown)
                )
        used_sources = {
            source_id
            for refs in self.support_map.values()
            for source_id in refs
        }
        unused = sorted(known_sources - used_sources)
        if unused:
            raise ValueError(
                "candidate sources must all participate in support_map: "
                + ", ".join(unused)
            )
        return self


class ReviewLevel(str, Enum):
    AUDIT_ONLY = "audit_only"
    ADVISORY = "advisory"
    HUMAN_REQUIRED = "human_required"


class ObservationKind(str, Enum):
    AMBIGUITY = "ambiguity"
    CHECKPOINT = "checkpoint"
    CONFIRMED_CONFLICT = "confirmed_conflict"
    ADVISORY = "advisory"
    AUDIT = "audit"
    STYLE = "style"
    PROSE = "prose"


class ReviewObservation(StrictModel):
    observation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    candidate_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    kind: ObservationKind
    level: ReviewLevel
    reason: str = Field(min_length=1)
    prior_fact_digests: tuple[str, ...] = ()

    @field_validator("prior_fact_digests")
    @classmethod
    def prior_fact_hashes_are_valid(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            if not re.fullmatch(SHA256_PATTERN, digest):
                raise ValueError("prior_fact_digests must contain SHA-256 hex digests")
        if len(set(value)) != len(value):
            raise ValueError("prior_fact_digests must be unique")
        return value

    @model_validator(mode="after")
    def observation_scope_is_safe(self) -> "ReviewObservation":
        factual = {
            ObservationKind.AMBIGUITY,
            ObservationKind.CHECKPOINT,
            ObservationKind.CONFIRMED_CONFLICT,
            ObservationKind.ADVISORY,
            ObservationKind.AUDIT,
        }
        if self.kind in factual and self.candidate_id is None:
            raise ValueError("factual observations must bind a candidate_id")
        if self.kind in {ObservationKind.STYLE, ObservationKind.PROSE}:
            if self.candidate_id is not None:
                raise ValueError("style/prose observations cannot bind canon candidates")
            if self.level == ReviewLevel.HUMAN_REQUIRED:
                raise ValueError("style/prose observations cannot require canon review")
        return self


class ReviewAction(str, Enum):
    APPROVE = "approve"
    OMIT = "omit"
    CORRECT = "correct"
    REWRITE = "rewrite"
    DISMISS = "dismiss"


class RequirementMode(str, Enum):
    REVIEW = "review"
    REWRITE = "rewrite"


class ReviewRequirement(StrictModel):
    case_key: str = Field(pattern=SHA256_PATTERN)
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    level: ReviewLevel
    mode: RequirementMode
    checkpoint: bool
    reason_codes: tuple[str, ...] = Field(min_length=1)
    observation_digests: tuple[str, ...] = ()
    prior_fact_digests: tuple[str, ...] = ()
    allowed_actions: tuple[ReviewAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def requirement_actions_match_policy(self) -> "ReviewRequirement":
        if tuple(sorted(set(self.reason_codes))) != self.reason_codes:
            raise ValueError("reason_codes must be sorted and unique")
        if tuple(sorted(set(self.observation_digests))) != self.observation_digests:
            raise ValueError("observation_digests must be sorted and unique")
        if tuple(sorted(set(self.prior_fact_digests))) != self.prior_fact_digests:
            raise ValueError("prior_fact_digests must be sorted and unique")
        for label, digests in (
            ("observation_digests", self.observation_digests),
            ("prior_fact_digests", self.prior_fact_digests),
        ):
            if any(not re.fullmatch(SHA256_PATTERN, digest) for digest in digests):
                raise ValueError(f"{label} must contain SHA-256 hex digests")
        if self.mode == RequirementMode.REWRITE:
            if self.level != ReviewLevel.HUMAN_REQUIRED:
                raise ValueError("rewrite requirements must be human_required")
            if self.allowed_actions != (ReviewAction.REWRITE,):
                raise ValueError("rewrite requirements only allow rewrite")
        elif self.checkpoint:
            if self.level != ReviewLevel.HUMAN_REQUIRED:
                raise ValueError("checkpoints must be human_required")
            if self.allowed_actions != (
                ReviewAction.APPROVE,
                ReviewAction.REWRITE,
            ):
                raise ValueError("checkpoints only allow approve or rewrite")
        return self


class ScanStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ScanAttestation(StrictModel):
    attestation_id: str = Field(pattern=IDENTIFIER_PATTERN)
    scanner: str = Field(min_length=1)
    scanner_version: str = Field(min_length=1)
    chapter_sha256: str = Field(pattern=SHA256_PATTERN)
    # Optional only for reading immutable v1 prepared envelopes.  Every new
    # proposal/attestation is validated by the v2 service boundary and must
    # provide all three authority bindings.
    parent_head: str | None = Field(default=None, pattern=SHA256_PATTERN)
    author_axiom_digest: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    entity_registry_digest: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    dimensions: tuple[str, ...] = Field(min_length=1)
    status: ScanStatus
    checked_candidate_digests: tuple[str, ...] = ()

    @field_validator("dimensions")
    @classmethod
    def dimensions_are_nonempty_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("scan dimensions must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("scan dimensions must be unique")
        return value

    @field_validator("checked_candidate_digests")
    @classmethod
    def checked_hashes_are_valid(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            if not re.fullmatch(SHA256_PATTERN, digest):
                raise ValueError(
                    "checked_candidate_digests must contain SHA-256 hex digests"
                )
        if len(set(value)) != len(value):
            raise ValueError("checked_candidate_digests must be unique")
        return value


class EntityResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNREGISTERED = "unregistered"
    PENDING_REGISTRATION = "pending_registration"
    REGISTRATION = "registration"
    CONFLICT = "conflict"


class EntityResolution(StrictModel):
    """Exact alias/canonical input used to compile one candidate field."""

    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    claim_kind: FactKind
    namespace: IdentityNamespace
    field: str = Field(pattern=IDENTIFIER_PATTERN)
    raw_value: str = Field(min_length=1)
    canonical_entity: str = Field(min_length=1)
    status: EntityResolutionStatus
    registry_binding_digests: tuple[str, ...] = ()
    resolution_digest: str = Field(pattern=SHA256_PATTERN)

    def digest_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"resolution_digest"})

    @model_validator(mode="after")
    def bind_resolution_to_inputs(self) -> "EntityResolution":
        if self.registry_binding_digests != tuple(
            sorted(set(self.registry_binding_digests))
        ):
            raise ValueError(
                "registry_binding_digests must be sorted and unique"
            )
        if any(
            not re.fullmatch(SHA256_PATTERN, digest)
            for digest in self.registry_binding_digests
        ):
            raise ValueError(
                "registry_binding_digests must contain SHA-256 hex digests"
            )
        if canonical_digest(self.digest_payload()) != self.resolution_digest:
            raise ValueError("resolution_digest does not match resolution inputs")
        return self


class CanonEffect(StrictModel):
    """A prepared reducer operation; it is not active until a commit is sealed."""

    effect_id: str = Field(pattern=SHA256_PATTERN)
    # UTF-8 manuscript byte derived from result-bearing support fields. Effects
    # sharing a fact slot fold in narrative order, never hash order.
    source_order: int = Field(default=0, ge=0)
    candidate_digest: str = Field(pattern=SHA256_PATTERN)
    fact_key: str = Field(pattern=SHA256_PATTERN)
    claim: FactClaim
    # Exact N-1 fact replaced/transitioned by this effect. Presence of this
    # digest is an immutable state-transition proof, independent of whether a
    # human checkpoint is required by policy.
    prior_fact_digest: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # Multiple supported events for the same slot may occur inside one
    # chapter. Later effects bind the immediately preceding compiled effect.
    prior_effect_id: str | None = Field(default=None, pattern=SHA256_PATTERN)
    # Fields copied from that prior fact because the current prose used a
    # pronoun/ellipsis. Values are the same exact prior digest.
    inherited_fields: dict[str, str] = Field(default_factory=dict)
    source_digests: tuple[str, ...] = Field(min_length=1)
    support_map: dict[str, tuple[str, ...]]

    @model_validator(mode="after")
    def evidence_digests_are_closed(self) -> "CanonEffect":
        source_set = set(self.source_digests)
        if tuple(sorted(source_set)) != self.source_digests:
            raise ValueError("source_digests must be sorted and unique")
        if any(not re.fullmatch(SHA256_PATTERN, digest) for digest in source_set):
            raise ValueError("source_digests must contain SHA-256 hex digests")
        for field_name, refs in self.support_map.items():
            if not refs:
                raise ValueError(f"support_map.{field_name} must not be empty")
            if tuple(sorted(set(refs))) != refs:
                raise ValueError(
                    f"support_map.{field_name} digests must be sorted and unique"
                )
            if not set(refs).issubset(source_set):
                raise ValueError(
                    f"support_map.{field_name} must reference effect source_digests"
                )
        if self.inherited_fields:
            prior_reference = self.prior_fact_digest or self.prior_effect_id
            if prior_reference is None:
                raise ValueError("inherited_fields require an exact prior reference")
            if any(
                digest != prior_reference
                for digest in self.inherited_fields.values()
            ):
                raise ValueError(
                    "inherited_fields must reference the exact prior fact/effect"
                )
            claim_fields = set(
                self.claim.model_dump(mode="python", exclude_none=True)
            )
            unknown = set(self.inherited_fields) - claim_fields
            if unknown:
                raise ValueError("inherited_fields must name populated claim fields")
            if set(self.inherited_fields) & set(self.support_map):
                raise ValueError(
                    "a claim field cannot be both current-source and inherited"
                )
        if self.prior_fact_digest and self.prior_effect_id:
            raise ValueError("an effect may have only one immediate prior reference")
        return self


class TransactionState(str, Enum):
    READY = "ready"
    AWAITING_HUMAN = "awaiting_human"
    REWRITE_REQUIRED = "rewrite_required"


class PreparedTransaction(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    compiler_version: Literal[COMPILER_VERSION] = COMPILER_VERSION
    policy_version: Literal[POLICY_VERSION] = POLICY_VERSION
    transaction_digest: str = Field(pattern=SHA256_PATTERN)
    parent_head: str
    state: TransactionState
    candidate_digests: tuple[str, ...]
    effects: tuple[CanonEffect, ...]
    requirements: tuple[ReviewRequirement, ...]
    scan_attestation_digests: tuple[str, ...] = ()
    entity_registry_digest: str = Field(
        default=UNBOUND_ENTITY_REGISTRY_DIGEST,
        pattern=SHA256_PATTERN,
    )
    entity_resolutions: tuple[EntityResolution, ...] = ()

    @field_validator("parent_head")
    @classmethod
    def parent_head_is_valid(cls, value: str) -> str:
        if value != "GENESIS" and not re.fullmatch(SHA256_PATTERN, value):
            raise ValueError("parent_head must be GENESIS or a SHA-256 digest")
        return value

    def digest_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"transaction_digest"})
        # Transactions persisted before the v3 entity-registry binding keep
        # their original digest. New service transactions always bind a real
        # parent-HEAD registry digest, including an empty registry.
        if (
            self.entity_registry_digest == UNBOUND_ENTITY_REGISTRY_DIGEST
            and not self.entity_resolutions
        ):
            payload.pop("entity_registry_digest", None)
            payload.pop("entity_resolutions", None)
        return payload

    @model_validator(mode="after")
    def enforce_transaction_invariants(self) -> "PreparedTransaction":
        if tuple(sorted(set(self.candidate_digests))) != self.candidate_digests:
            raise ValueError("candidate_digests must be sorted and unique")
        effect_candidates = tuple(sorted(effect.candidate_digest for effect in self.effects))
        if effect_candidates != self.candidate_digests:
            raise ValueError("effects must contain exactly one entry per candidate digest")
        effect_order = tuple(
            (effect.source_order, effect.effect_id) for effect in self.effects
        )
        if tuple(sorted(effect_order)) != effect_order:
            raise ValueError("effects must be sorted by source order then effect_id")
        effect_ids = tuple(effect.effect_id for effect in self.effects)
        if len(set(effect_ids)) != len(effect_ids):
            raise ValueError("effect_id values must be unique")
        prior_effects: dict[str, CanonEffect] = {}
        for effect in self.effects:
            if effect.prior_effect_id is not None:
                prior = prior_effects.get(effect.prior_effect_id)
                if prior is None:
                    raise ValueError(
                        "prior_effect_id must reference an earlier transaction effect"
                    )
            prior_effects[effect.effect_id] = effect
        fact_slot_orders = [
            (effect.fact_key, effect.source_order) for effect in self.effects
        ]
        if len(fact_slot_orders) != len(set(fact_slot_orders)):
            raise ValueError(
                "effects for one fact slot must have distinct narrative order"
            )
        requirement_keys = tuple(req.case_key for req in self.requirements)
        if tuple(sorted(set(requirement_keys))) != requirement_keys:
            raise ValueError("requirements must be sorted by unique case_key")
        if tuple(sorted(set(self.scan_attestation_digests))) != (
            self.scan_attestation_digests
        ):
            raise ValueError("scan_attestation_digests must be sorted and unique")
        if any(
            not re.fullmatch(SHA256_PATTERN, digest)
            for digest in self.scan_attestation_digests
        ):
            raise ValueError(
                "scan_attestation_digests must contain SHA-256 hex digests"
            )
        if any(
            requirement.candidate_digest not in self.candidate_digests
            for requirement in self.requirements
        ):
            raise ValueError("review requirements must reference transaction candidates")
        resolution_digests = tuple(
            resolution.resolution_digest for resolution in self.entity_resolutions
        )
        if tuple(sorted(set(resolution_digests))) != resolution_digests:
            raise ValueError("entity_resolutions must be sorted by unique digest")
        if any(
            resolution.candidate_digest not in self.candidate_digests
            for resolution in self.entity_resolutions
        ):
            raise ValueError(
                "entity resolutions must reference transaction candidates"
            )
        if (
            self.entity_registry_digest == UNBOUND_ENTITY_REGISTRY_DIGEST
            and self.entity_resolutions
        ):
            raise ValueError(
                "entity resolutions require a bound entity registry digest"
            )

        expected_state = TransactionState.READY
        if any(req.mode == RequirementMode.REWRITE for req in self.requirements):
            expected_state = TransactionState.REWRITE_REQUIRED
        elif any(req.level == ReviewLevel.HUMAN_REQUIRED for req in self.requirements):
            expected_state = TransactionState.AWAITING_HUMAN
        if self.state != expected_state:
            raise ValueError(
                f"transaction state must be {expected_state.value} for its requirements"
            )
        if canonical_digest(self.digest_payload()) != self.transaction_digest:
            raise ValueError("transaction_digest does not match transaction payload")
        return self

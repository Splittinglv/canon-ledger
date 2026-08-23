#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed, digest-neutral public protocol for human review cases.

The immutable chapter, author-axiom, and legacy recertification objects each
have their own storage schema.  Public callers should not have to infer an
action matrix or assemble optimistic-concurrency bindings from those storage
shapes, though.  This module is the single registry and serializer for that
public projection.

Nothing returned here participates in a persisted object digest.  Callers
must first compute/verify their existing target and material digests, then add
this projection only while producing a workflow/status response.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


SHA256_PATTERN = r"^[0-9a-f]{64}$"


class PublicHumanProtocolError(ValueError):
    """A public case disagrees with the authoritative case it projects."""


class HumanActionProfile(str, Enum):
    """Stable action profiles shared by every public human-review channel."""

    CHAPTER_CHECKPOINT = "chapter_checkpoint"
    CHAPTER_AMBIGUITY = "chapter_ambiguity"
    CHAPTER_UNBOUND = "chapter_unbound"
    CHAPTER_REWRITE = "chapter_rewrite"
    CHAPTER_DISMISS = "chapter_dismiss"
    AUTHOR_AXIOM = "author_axiom"
    LEGACY_RECERTIFICATION = "legacy_recertification"


class PublicHumanAction(str, Enum):
    APPROVE = "approve"
    OMIT = "omit"
    CORRECT = "correct"
    REWRITE = "rewrite"
    NO_CONFLICT = "no_conflict"
    DISMISS = "dismiss"
    CONFIRM = "confirm"


# Tuple order is part of the public protocol.  In particular, sets/frozensets
# must never be serialized directly because their iteration order is not an
# author-facing contract.
_ACTION_REGISTRY: Mapping[
    HumanActionProfile, tuple[PublicHumanAction, ...]
] = {
    HumanActionProfile.CHAPTER_CHECKPOINT: (
        PublicHumanAction.APPROVE,
        PublicHumanAction.REWRITE,
    ),
    HumanActionProfile.CHAPTER_AMBIGUITY: (
        PublicHumanAction.APPROVE,
        PublicHumanAction.OMIT,
        PublicHumanAction.CORRECT,
        PublicHumanAction.REWRITE,
    ),
    HumanActionProfile.CHAPTER_UNBOUND: (
        PublicHumanAction.NO_CONFLICT,
        PublicHumanAction.REWRITE,
    ),
    HumanActionProfile.CHAPTER_REWRITE: (PublicHumanAction.REWRITE,),
    HumanActionProfile.CHAPTER_DISMISS: (PublicHumanAction.DISMISS,),
    HumanActionProfile.AUTHOR_AXIOM: (
        PublicHumanAction.APPROVE,
        PublicHumanAction.OMIT,
        PublicHumanAction.REWRITE,
    ),
    HumanActionProfile.LEGACY_RECERTIFICATION: (
        PublicHumanAction.CONFIRM,
    ),
}


def chapter_action_profile(
    *, kind: str, level: str, requires_rewrite: bool
) -> HumanActionProfile:
    """Choose the chapter profile without importing chapter storage models."""

    if bool(requires_rewrite):
        return HumanActionProfile.CHAPTER_REWRITE
    if str(level) != "human_required":
        return HumanActionProfile.CHAPTER_DISMISS
    normalized_kind = str(kind)
    if normalized_kind == "checkpoint":
        return HumanActionProfile.CHAPTER_CHECKPOINT
    if normalized_kind == "ambiguity":
        return HumanActionProfile.CHAPTER_AMBIGUITY
    if normalized_kind == "unbound":
        return HumanActionProfile.CHAPTER_UNBOUND
    raise PublicHumanProtocolError(
        "public_human_case_unknown_chapter_kind:" + normalized_kind
    )


def requirement_action_profile(
    *, mode: str, checkpoint: bool, level: str
) -> HumanActionProfile:
    """Map a compiler requirement to the same chapter action registry."""

    if str(mode) == "rewrite":
        return HumanActionProfile.CHAPTER_REWRITE
    if bool(checkpoint):
        return HumanActionProfile.CHAPTER_CHECKPOINT
    if str(level) == "human_required":
        return HumanActionProfile.CHAPTER_AMBIGUITY
    return HumanActionProfile.CHAPTER_DISMISS


def ordered_allowed_actions(
    profile: HumanActionProfile | str,
) -> tuple[str, ...]:
    normalized = HumanActionProfile(profile)
    return tuple(action.value for action in _ACTION_REGISTRY[normalized])


def require_allowed_action(
    profile: HumanActionProfile | str,
    action: PublicHumanAction | str,
) -> str:
    """Return a normalized action or reject it against the shared registry."""

    normalized_profile = HumanActionProfile(profile)
    try:
        normalized_action = PublicHumanAction(action)
    except ValueError as exc:
        raise PublicHumanProtocolError(
            "public_human_action_unknown:" + str(action)
        ) from exc
    if normalized_action not in _ACTION_REGISTRY[normalized_profile]:
        raise PublicHumanProtocolError(
            "public_human_action_not_allowed:"
            + normalized_profile.value
            + ":"
            + normalized_action.value
        )
    return normalized_action.value


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PublicDecisionBinding(_StrictModel):
    """Exact optimistic-concurrency inputs echoed into a decision request."""

    target_digest: str = Field(pattern=SHA256_PATTERN)
    material_digest: str = Field(pattern=SHA256_PATTERN)
    expected_decision_head_hash: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )


class PublicHumanCaseProtocol(_StrictModel):
    """Typed overlay added to a channel-specific public case payload."""

    profile: HumanActionProfile
    allowed_actions: tuple[PublicHumanAction, ...] = Field(min_length=1)
    decision_binding: PublicDecisionBinding

    @model_validator(mode="after")
    def actions_match_registry(self) -> "PublicHumanCaseProtocol":
        expected = _ACTION_REGISTRY[self.profile]
        if self.allowed_actions != expected:
            raise ValueError("public human case actions do not match registry")
        return self


def serialize_public_human_case(
    payload: Mapping[str, Any],
    *,
    profile: HumanActionProfile | str,
    target_digest: str,
    material_digest: str,
    expected_decision_head_hash: str | None,
) -> dict[str, Any]:
    """Add a typed public action/binding projection without mutating input.

    Existing top-level and nested compatibility fields are checked before the
    projection is emitted.  This makes the serializer fail closed if a caller
    accidentally combines material from one case with the target of another.
    """

    raw = dict(payload)
    existing_target = raw.get("target_digest")
    if existing_target is not None and existing_target != target_digest:
        raise PublicHumanProtocolError(
            "public_human_case_target_digest_mismatch"
        )
    existing_material = raw.get("material_digest")
    if existing_material is not None and existing_material != material_digest:
        raise PublicHumanProtocolError(
            "public_human_case_material_digest_mismatch"
        )
    review_material = raw.get("review_material")
    if isinstance(review_material, Mapping):
        nested_material = review_material.get("material_digest")
        if nested_material is not None and nested_material != material_digest:
            raise PublicHumanProtocolError(
                "public_human_case_nested_material_digest_mismatch"
            )
    existing_head = raw.get("decision_head_hash")
    if (
        "decision_head_hash" in raw
        and existing_head != expected_decision_head_hash
    ):
        raise PublicHumanProtocolError(
            "public_human_case_decision_head_mismatch"
        )

    normalized_profile = HumanActionProfile(profile)
    binding = PublicDecisionBinding(
        target_digest=target_digest,
        material_digest=material_digest,
        expected_decision_head_hash=expected_decision_head_hash,
    )
    protocol = PublicHumanCaseProtocol(
        profile=normalized_profile,
        allowed_actions=_ACTION_REGISTRY[normalized_profile],
        decision_binding=binding,
    )
    binding_payload = protocol.decision_binding.model_dump(mode="json")

    # Preserve the old compatibility fields while exposing a single binding
    # object that can be copied into chapter/author-axiom decision inputs.
    raw.update(
        {
            "target_digest": binding.target_digest,
            "material_digest": binding.material_digest,
            "decision_head_hash": binding.expected_decision_head_hash,
            "expected_decision_head_hash": (
                binding.expected_decision_head_hash
            ),
            "allowed_actions": [
                action.value for action in protocol.allowed_actions
            ],
            "decision_binding": binding_payload,
        }
    )
    return raw


__all__ = [
    "HumanActionProfile",
    "PublicDecisionBinding",
    "PublicHumanAction",
    "PublicHumanCaseProtocol",
    "PublicHumanProtocolError",
    "chapter_action_profile",
    "ordered_allowed_actions",
    "require_allowed_action",
    "requirement_action_profile",
    "serialize_public_human_case",
]

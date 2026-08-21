#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence validation and semantic digests for canon v3 candidates."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .schema import (
    AuthorAxiomSource,
    FactCandidate,
    ManuscriptSpanSource,
    SourceRef,
    canonical_digest,
)


class EvidenceValidationError(ValueError):
    pass


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"\s+", " ", text).strip()


_NEGATION_PREFIXES = (
    "不",
    "未",
    "无",
    "没",
    "非",
    "并非",
    "不是",
    "从未",
    "尚未",
    "无法",
)


def _contains_unnegated(text: str, needle: str) -> bool:
    """Reject a positive atom when it appears only inside negated wording."""

    cursor = 0
    while True:
        index = text.find(needle, cursor)
        if index < 0:
            return False
        prefix = text[max(0, index - 4) : index]
        if not any(prefix.endswith(marker) for marker in _NEGATION_PREFIXES):
            return True
        cursor = index + max(1, len(needle))


def _claim_atoms(value: Any) -> tuple[Any, ...]:
    if isinstance(value, (tuple, list)):
        atoms: list[Any] = []
        for item in value:
            atoms.extend(_claim_atoms(item))
        return tuple(atoms)
    if isinstance(value, dict):
        atoms = []
        for key in sorted(value):
            atoms.extend(_claim_atoms(value[key]))
        return tuple(atoms)
    return (value,)


def _axiom_atoms(value: Any) -> tuple[Any, ...]:
    if isinstance(value, list):
        atoms: list[Any] = []
        for item in value:
            atoms.extend(_axiom_atoms(item))
        return tuple(atoms)
    if isinstance(value, dict):
        atoms = []
        for key in sorted(value):
            atoms.extend(_axiom_atoms(value[key]))
        return tuple(atoms)
    return (value,)


def _source_supports_atom(source: SourceRef, atom: Any) -> bool:
    if isinstance(source, ManuscriptSpanSource):
        needle = _normalized_text(atom)
        return bool(needle) and _contains_unnegated(
            _normalized_text(source.quote), needle
        )
    if isinstance(source, AuthorAxiomSource):
        for axiom_atom in _axiom_atoms(source.value):
            if type(atom) is type(axiom_atom) and atom == axiom_atom:
                return True
            if isinstance(atom, str) and isinstance(axiom_atom, str):
                needle = _normalized_text(atom)
                if needle and _contains_unnegated(
                    _normalized_text(axiom_atom), needle
                ):
                    return True
        return False
    return False


def source_content_payload(source: SourceRef) -> dict[str, Any]:
    """Stable source identity, deliberately excluding the caller-chosen source_id."""

    # ``frozen=True`` is intentionally rechecked here because JSON values in an
    # author axiom may contain a caller-owned nested list/dict.  A mutation must
    # fail its bound value hash instead of silently producing new evidence.
    type(source).model_validate(source.model_dump(mode="python"))
    payload = source.model_dump(mode="json", exclude={"source_id"})
    return payload


def source_digest(source: SourceRef) -> str:
    return canonical_digest(source_content_payload(source))


def validate_candidate_evidence(candidate: FactCandidate) -> None:
    for source in candidate.sources:
        source_content_payload(source)
    sources_by_id = {source.source_id: source for source in candidate.sources}
    claim = candidate.claim.model_dump(mode="python", exclude_none=True)
    failures: list[str] = []
    for field_name, source_ids in sorted(candidate.support_map.items()):
        atoms = _claim_atoms(claim[field_name])
        bound_sources = [sources_by_id[source_id] for source_id in source_ids]
        for atom in atoms:
            if not _source_supports_atom_any(bound_sources, atom):
                failures.append(f"{field_name}={atom!r}")
    if failures:
        raise EvidenceValidationError(
            "claim fields are not supported by their bound sources: "
            + ", ".join(failures)
        )


def _source_supports_atom_any(sources: list[SourceRef], atom: Any) -> bool:
    return any(_source_supports_atom(source, atom) for source in sources)


def candidate_content_payload(candidate: FactCandidate) -> dict[str, Any]:
    """Candidate identity independent of runtime IDs and input ordering."""

    validate_candidate_evidence(candidate)
    source_digests = {
        source.source_id: source_digest(source) for source in candidate.sources
    }
    claim_payload = candidate.claim.model_dump(mode="json", exclude_none=False)
    if "aliases" in claim_payload:
        claim_payload["aliases"] = sorted(claim_payload["aliases"])
    return {
        "claim": claim_payload,
        "identity_links": dict(sorted(candidate.identity_links.items())),
        "sources": sorted(source_digests.values()),
        "support_map": {
            field_name: sorted(source_digests[source_id] for source_id in source_ids)
            for field_name, source_ids in sorted(candidate.support_map.items())
        },
    }


def candidate_digest(candidate: FactCandidate) -> str:
    return canonical_digest(candidate_content_payload(candidate))


def semantic_claim_payload(candidate: FactCandidate) -> dict[str, Any]:
    """Stable fact meaning, deliberately independent of evidence packaging.

    Candidate IDs, source IDs, byte ranges, quotes, and support-map layout are
    proposal mechanics.  They must not let a previously adjudicated claim
    acquire a new identity merely by repackaging its evidence.
    """

    claim_payload = candidate.claim.model_dump(mode="json", exclude_none=False)
    if "aliases" in claim_payload:
        claim_payload["aliases"] = sorted(claim_payload["aliases"])
    return {
        "claim": claim_payload,
        "identity_links": dict(sorted(candidate.identity_links.items())),
    }


def semantic_claim_digest(candidate: FactCandidate) -> str:
    return canonical_digest(semantic_claim_payload(candidate))


def lineage_key(chapter_digest: str, candidate: FactCandidate) -> str:
    """Bind semantic adjudication lineage to exact chapter bytes."""

    return canonical_digest(
        {
            "schema_version": "canon-v3/semantic-lineage/v1",
            "chapter_digest": str(chapter_digest),
            "semantic_claim_digest": semantic_claim_digest(candidate),
        }
    )


__all__ = [
    "EvidenceValidationError",
    "candidate_content_payload",
    "candidate_digest",
    "lineage_key",
    "semantic_claim_digest",
    "semantic_claim_payload",
    "source_content_payload",
    "source_digest",
    "validate_candidate_evidence",
]

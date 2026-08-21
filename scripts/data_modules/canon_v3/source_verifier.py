#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind v3 source references to the actual project bytes.

The domain schema proves that a source is internally self-consistent.  This
module proves the stronger statement needed at the write boundary: the source
is an exact slice/value of the current project files inspected for this
chapter.  Model-provided hashes are never treated as evidence by themselves.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, AbstractSet

try:
    from security_utils import resolve_inside_project
except ImportError:  # pragma: no cover
    from scripts.security_utils import resolve_inside_project

from ..chapter_content_binding import ChapterContentBinding
from .evidence import validate_candidate_evidence
from .schema import (
    AuthorAxiomSource,
    FactCandidate,
    ManuscriptSpanSource,
    canonical_digest,
)


class SourceVerificationError(ValueError):
    """A proposal source does not match the current author-owned bytes."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode_pointer_token(token: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            output.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise SourceVerificationError("author_axiom_json_pointer_invalid_escape")
        output.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(output)


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve one RFC 6901 pointer without accepting ambiguous list indexes."""

    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise SourceVerificationError("author_axiom_json_pointer_invalid")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = _decode_pointer_token(raw_token)
        if isinstance(current, dict):
            if token not in current:
                raise SourceVerificationError("author_axiom_json_pointer_missing")
            current = current[token]
            continue
        if isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise SourceVerificationError("author_axiom_json_pointer_bad_index")
            index = int(token)
            if index >= len(current):
                raise SourceVerificationError("author_axiom_json_pointer_missing")
            current = current[index]
            continue
        raise SourceVerificationError("author_axiom_json_pointer_traverses_scalar")
    return current


def _verify_manuscript_source(
    project_root: Path,
    binding: ChapterContentBinding,
    source: ManuscriptSpanSource,
) -> None:
    if source.chapter != binding.chapter:
        raise SourceVerificationError("manuscript_source_chapter_mismatch")
    manuscript = resolve_inside_project(
        project_root,
        binding.path,
        reject_leaf_symlink=True,
    )
    if not manuscript.is_file():
        raise SourceVerificationError("manuscript_source_file_missing")
    raw = manuscript.read_bytes()
    actual_hash = _sha256(raw)
    if actual_hash != binding.sha256 or source.document_sha256 != actual_hash:
        raise SourceVerificationError("manuscript_source_document_hash_mismatch")
    if source.end > len(raw):
        raise SourceVerificationError("manuscript_source_span_out_of_range")
    quoted_bytes = raw[source.start : source.end]
    try:
        actual_quote = quoted_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceVerificationError(
            "manuscript_source_span_splits_utf8_character"
        ) from exc
    if actual_quote != source.quote:
        raise SourceVerificationError("manuscript_source_quote_mismatch")
    if _sha256(quoted_bytes) != source.quote_sha256:
        raise SourceVerificationError("manuscript_source_quote_hash_mismatch")


def _is_author_axiom_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    return normalized == ".story-system/MASTER_SETTING.json" or (
        normalized.startswith("设定集/") and normalized.endswith(".json")
    )


def _is_leaf_axiom(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(
            item is None or isinstance(item, (str, bool, int, float))
            for item in value
        )
    return False


def _verify_author_axiom_source(
    project_root: Path,
    source: AuthorAxiomSource,
) -> None:
    path = Path(source.document_path)
    if path.is_absolute() or ".." in path.parts:
        raise SourceVerificationError("author_axiom_path_must_be_project_relative")
    normalized = path.as_posix()
    if not _is_author_axiom_path(normalized):
        raise SourceVerificationError("author_axiom_source_not_author_owned")
    document_path = resolve_inside_project(
        project_root,
        path,
        reject_leaf_symlink=True,
    )
    if not document_path.is_file():
        raise SourceVerificationError("author_axiom_document_missing")
    raw = document_path.read_bytes()
    if _sha256(raw) != source.document_sha256:
        raise SourceVerificationError("author_axiom_document_hash_mismatch")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceVerificationError("author_axiom_document_invalid_json") from exc
    if not source.json_pointer:
        raise SourceVerificationError("author_axiom_must_reference_leaf_pointer")
    actual = resolve_json_pointer(document, source.json_pointer)
    if not _is_leaf_axiom(actual):
        raise SourceVerificationError("author_axiom_value_must_be_leaf")
    if actual != source.value or canonical_digest(actual) != source.value_sha256:
        raise SourceVerificationError("author_axiom_value_mismatch")


def verify_candidate_sources(
    project_root: str | Path,
    chapter_binding: ChapterContentBinding | dict[str, Any],
    candidate: FactCandidate,
    *,
    active_author_axiom_source_keys: AbstractSet[str] | None = None,
) -> None:
    root = Path(project_root).expanduser().resolve()
    binding = (
        chapter_binding
        if isinstance(chapter_binding, ChapterContentBinding)
        else ChapterContentBinding.model_validate(chapter_binding)
    )
    validate_candidate_evidence(candidate)
    for source in candidate.sources:
        if isinstance(source, ManuscriptSpanSource):
            _verify_manuscript_source(root, binding, source)
        elif isinstance(source, AuthorAxiomSource):
            if active_author_axiom_source_keys is None:
                _verify_author_axiom_source(root, source)
            else:
                from .author_axiom import active_candidate_source_key

                if (
                    active_candidate_source_key(source)
                    not in active_author_axiom_source_keys
                ):
                    raise SourceVerificationError(
                        "author_axiom_source_not_active_at_parent_head"
                    )
        else:  # pragma: no cover - SourceRef is a closed discriminator union.
            raise SourceVerificationError("unsupported_source_type")


def verify_all_candidate_sources(
    project_root: str | Path,
    chapter_binding: ChapterContentBinding | dict[str, Any],
    candidates: Iterable[FactCandidate],
    *,
    active_author_axiom_source_keys: AbstractSet[str] | None = None,
) -> None:
    for candidate in candidates:
        verify_candidate_sources(
            project_root,
            chapter_binding,
            candidate,
            active_author_axiom_source_keys=active_author_axiom_source_keys,
        )


__all__ = [
    "SourceVerificationError",
    "resolve_json_pointer",
    "verify_all_candidate_sources",
    "verify_candidate_sources",
]

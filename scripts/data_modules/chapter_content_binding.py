#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind chapter artifacts to the exact final manuscript bytes they inspected."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "webnovel-chapter-content-binding/v1"
_CHAPTER_FILENAME_RE = re.compile(r"^第0*(?P<chapter>\d+)章.*\.md$")

VERIFY_CODES = {
    "ok",
    "chapter_file_missing",
    "chapter_file_empty",
    "chapter_file_ambiguous",
    "chapter_binding_missing",
    "artifact_chapter_mismatch",
    "artifact_path_mismatch",
    "chapter_content_hash_mismatch",
    "artifact_size_mismatch",
    "commit_chapter_mismatch",
    "commit_schema_invalid",
}


class ChapterBindingError(ValueError):
    """Raised when a manuscript binding cannot be built or required."""

    def __init__(self, code: str, message: str = ""):
        if code not in VERIFY_CODES:
            raise ValueError(f"unknown chapter binding error code: {code}")
        self.code = code
        super().__init__(message or code)


class ChapterContentBinding(BaseModel):
    """Canonical, serializable fingerprint of one final chapter manuscript."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    chapter: int = Field(ge=1)
    path: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    bytes: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def validate_relative_posix_path(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        path = Path(normalized)
        if not normalized or path.is_absolute() or ".." in path.parts:
            raise ValueError("path must be a project-relative POSIX path")
        return normalized

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("sha256 must be a 64-character hexadecimal digest")
        return normalized


def _project_relative_path(project_root: Path, chapter_file: Path) -> str:
    root = project_root.expanduser().resolve()
    resolved = chapter_file.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ChapterBindingError(
            "artifact_path_mismatch",
            f"chapter file is outside project root: {resolved}",
        ) from exc


def build_chapter_binding(project_root: str | Path, chapter: int) -> dict[str, Any]:
    """Fingerprint the current chapter file using its exact raw bytes."""
    root = Path(project_root).expanduser().resolve()
    chapter_no = int(chapter)
    chapters_dir = root / "正文"
    candidates: list[Path] = []
    if chapters_dir.is_dir():
        for candidate in chapters_dir.rglob("*.md"):
            match = _CHAPTER_FILENAME_RE.match(candidate.name)
            if match and int(match.group("chapter")) == chapter_no and candidate.is_file():
                candidates.append(candidate)
    if not candidates:
        raise ChapterBindingError(
            "chapter_file_missing",
            f"chapter {chapter_no} manuscript file is missing",
        )
    if len(candidates) > 1:
        raise ChapterBindingError(
            "chapter_file_ambiguous",
            f"chapter {chapter_no} has multiple manuscript files",
        )
    chapter_file = candidates[0]

    raw = chapter_file.read_bytes()
    if not raw:
        raise ChapterBindingError(
            "chapter_file_empty",
            f"chapter {chapter_no} manuscript file is empty",
        )

    binding = ChapterContentBinding(
        chapter=chapter_no,
        path=_project_relative_path(root, chapter_file),
        sha256=hashlib.sha256(raw).hexdigest(),
        bytes=len(raw),
    )
    return binding.model_dump()


def _parse_binding(binding: Any) -> ChapterContentBinding | None:
    if isinstance(binding, ChapterContentBinding):
        return binding
    if not isinstance(binding, dict) or not binding:
        return None
    try:
        return ChapterContentBinding.model_validate(binding)
    except Exception:
        return None


def verify_chapter_binding(
    project_root: str | Path,
    chapter: int,
    binding: Any,
) -> tuple[bool, str]:
    """Verify an artifact binding against the current final manuscript."""
    expected = _parse_binding(binding)
    if expected is None:
        return False, "chapter_binding_missing"

    chapter_no = int(chapter)
    if expected.chapter != chapter_no:
        return False, "artifact_chapter_mismatch"

    try:
        current = ChapterContentBinding.model_validate(
            build_chapter_binding(project_root, chapter_no)
        )
    except ChapterBindingError as exc:
        return False, exc.code

    if current.path != expected.path:
        return False, "artifact_path_mismatch"
    if current.sha256 != expected.sha256:
        return False, "chapter_content_hash_mismatch"
    if current.bytes != expected.bytes:
        return False, "artifact_size_mismatch"
    return True, "ok"


def require_chapter_binding(
    project_root: str | Path,
    chapter: int,
    binding: Any,
) -> dict[str, Any]:
    """Return a normalized binding or raise a stable, machine-readable error."""
    ok, code = verify_chapter_binding(project_root, chapter, binding)
    if not ok:
        raise ChapterBindingError(
            code,
            f"chapter {int(chapter)} content binding verification failed: {code}",
        )
    parsed = _parse_binding(binding)
    if parsed is None:  # Defensive; verify_chapter_binding already checked this.
        raise ChapterBindingError("chapter_binding_missing")
    return parsed.model_dump()


def verify_commit_content_binding(
    project_root: str | Path,
    expected_chapter: int,
    payload: Any,
) -> tuple[bool, str]:
    """Verify a complete commit envelope and its current manuscript binding.

    Readers must not trust only the top-level digest: the four source
    artifacts and provenance are part of the same immutable envelope.
    ``ChapterCommitSchema`` is imported lazily because it references the
    binding model defined in this module.
    """
    if not isinstance(payload, dict):
        return False, "commit_schema_invalid"

    meta = payload.get("meta")
    try:
        declared_chapter = int(meta.get("chapter") or 0) if isinstance(meta, dict) else 0
        chapter = int(expected_chapter)
    except (TypeError, ValueError):
        return False, "commit_chapter_mismatch"
    if chapter <= 0 or declared_chapter != chapter:
        return False, "commit_chapter_mismatch"

    binding_ok, binding_code = verify_chapter_binding(
        project_root,
        chapter,
        payload.get("chapter_binding"),
    )
    if not binding_ok:
        return False, binding_code

    try:
        from .chapter_commit_schema import ChapterCommitSchema

        ChapterCommitSchema.model_validate(payload)
    except Exception:
        return False, "commit_schema_invalid"
    return True, "ok"


def chapter_bindings_equal(left: Any, right: Any) -> bool:
    """Compare two bindings after strict canonical parsing."""
    left_model = _parse_binding(left)
    right_model = _parse_binding(right)
    return bool(
        left_model is not None
        and right_model is not None
        and left_model.model_dump() == right_model.model_dump()
    )

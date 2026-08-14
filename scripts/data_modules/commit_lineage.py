#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Predecessor hashes and revalidation stamps for canonical chapter commits.

A chapter commit records the immutable prefix it was written against.  After
chapter N is replaced, later accepted commits are no longer valid canon until
they are reviewed and extracted again.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


VALIDATION_VALID = "valid"
VALIDATION_NEEDS_REVALIDATION = "needs_revalidation"
EMPTY_PREFIX_MATERIAL = b"canon-ledger-empty-prefix"
_HASH_EXCLUDED_META = frozenset(
    {"validation_status", "predecessor_context_hash"}
)


def canonical_snapshot_hash(payload: dict[str, Any]) -> str:
    """Hash the immutable commit envelope while ignoring derived statuses."""
    snapshot = copy.deepcopy(payload)
    snapshot.pop("projection_status", None)
    meta = snapshot.get("meta")
    if isinstance(meta, dict):
        for key in _HASH_EXCLUDED_META:
            meta.pop(key, None)
    raw = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prefix_context_hash(snapshot_hashes: list[str]) -> str:
    """Hash an ordered prefix of chapter snapshot hashes."""
    if not snapshot_hashes:
        return hashlib.sha256(EMPTY_PREFIX_MATERIAL).hexdigest()
    joined = "\n".join(str(item) for item in snapshot_hashes)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else {}


def _chapter_of(payload: dict[str, Any]) -> int:
    try:
        return int(_meta(payload).get("chapter") or 0)
    except (TypeError, ValueError):
        return 0


def is_accepted_commit(payload: dict[str, Any]) -> bool:
    return str(_meta(payload).get("status") or "") == "accepted"


def is_needs_revalidation(payload: dict[str, Any]) -> bool:
    return (
        is_accepted_commit(payload)
        and str(_meta(payload).get("validation_status") or "")
        == VALIDATION_NEEDS_REVALIDATION
    )


def predecessor_context_hash_from_commits(
    commits: list[dict[str, Any]],
    chapter: int,
) -> str:
    """Return the hash of accepted, currently valid commits before ``chapter``."""
    prefix: list[str] = []
    target = max(1, int(chapter or 1))
    for payload in commits:
        current = _chapter_of(payload)
        if current <= 0 or current >= target:
            continue
        if not is_accepted_commit(payload) or is_needs_revalidation(payload):
            continue
        prefix.append(canonical_snapshot_hash(payload))
    return prefix_context_hash(prefix)


def predecessor_context_hash_for_chapter(
    project_root: str | Path,
    chapter: int,
) -> str:
    """Load on-disk commits and hash the valid prefix before ``chapter``."""
    from .projection_rebuild import ProjectionRebuildError, load_canonical_commits

    try:
        commits = load_canonical_commits(project_root, validate_bindings=True)
    except ProjectionRebuildError:
        commits = []
    return predecessor_context_hash_from_commits(commits, chapter)


def list_needs_revalidation(project_root: str | Path) -> list[int]:
    """Return accepted chapters stamped as needing revalidation."""
    commits_dir = Path(project_root).expanduser().resolve() / ".story-system" / "commits"
    if not commits_dir.is_dir():
        return []
    chapters: list[int] = []
    for path in sorted(commits_dir.glob("chapter_*.commit.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        chapter = _chapter_of(payload)
        if chapter > 0 and is_needs_revalidation(payload):
            chapters.append(chapter)
    return chapters


def prior_chapters_needing_revalidation(
    project_root: str | Path,
    chapter: int,
) -> list[int]:
    """Chapters before ``chapter`` that must be revalidated first."""
    target = max(1, int(chapter or 1))
    return [item for item in list_needs_revalidation(project_root) if item < target]


def stamp_and_partition_commits(
    commits: list[dict[str, Any]],
    *,
    previous_manifest: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    """Stamp lineage metadata and split replayable commits from the rest.

    Returns ``(replayable, stamped, needs_revalidation_chapters)``.
    Replayable commits still contribute to derived read models.  Stamped
    includes every input commit so later chapter files are kept on disk.
    """
    previous = previous_manifest if isinstance(previous_manifest, dict) else {}
    running_hashes: list[str] = []
    replaced_at: int | None = None
    replayable: list[dict[str, Any]] = []
    stamped: list[dict[str, Any]] = []
    stale_chapters: list[int] = []

    for original in commits:
        payload = copy.deepcopy(original)
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            payload["meta"] = meta
        chapter = _chapter_of(payload)
        status = str(meta.get("status") or "")
        content_hash = canonical_snapshot_hash(payload)
        expected_pred = prefix_context_hash(running_hashes)
        stored_pred = str(meta.get("predecessor_context_hash") or "").strip()
        previous_hash = str(previous.get(str(chapter)) or "").strip()
        replaced = bool(previous_hash) and previous_hash != content_hash
        predecessor_mismatch = bool(stored_pred) and stored_pred != expected_pred
        after_rewrite = replaced_at is not None and chapter > replaced_at
        already_stale = (
            status == "accepted"
            and str(meta.get("validation_status") or "") == VALIDATION_NEEDS_REVALIDATION
        )
        stale = status == "accepted" and (
            already_stale or predecessor_mismatch or after_rewrite
        )

        if stale:
            meta["validation_status"] = VALIDATION_NEEDS_REVALIDATION
            if not stored_pred:
                meta["predecessor_context_hash"] = expected_pred
            stamped.append(payload)
            if chapter > 0:
                stale_chapters.append(chapter)
            continue

        if not stored_pred:
            meta["predecessor_context_hash"] = expected_pred
        meta["validation_status"] = VALIDATION_VALID
        stamped.append(payload)
        replayable.append(payload)
        if status == "accepted" and chapter > 0:
            running_hashes.append(content_hash)
        if replaced and chapter > 0:
            replaced_at = chapter

    return replayable, stamped, stale_chapters

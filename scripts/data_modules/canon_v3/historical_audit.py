#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only, HEAD-reachable historical chapter revision exports.

The exporter never consults STAGING, the legacy index, candidate quotations as
a manuscript substitute, or Git history.  A manuscript is available only when
its exact bytes still exist as the current chapter or at the documented
content-addressed local revision-archive path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

from ..chapter_content_binding import (
    ChapterBindingError,
    ChapterContentBinding,
    build_chapter_binding,
)

try:
    from security_utils import resolve_inside_project
except ImportError:  # pragma: no cover - package-style imports in adapters.
    from scripts.security_utils import resolve_inside_project

from .author_axiom import AuthorAxiomChannel, record_digest
from .entity_registry import build_approved_entity_registry
from .repository import CanonIntegrityError, CanonV3Repository, content_hash
from .service import PREPARED_ENVELOPE_SCHEMA, PreparedEnvelope


HISTORICAL_EXPORT_SCHEMA = "canon-v3/historical-revision-export/v1"
REVISION_ARCHIVE_RESULT_SCHEMA = "canon-v3/revision-archive-result/v1"
REVISION_ARCHIVE_RELATIVE_ROOT = Path(
    ".story-system/v3/revision-archive/manuscripts"
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_EXPORT_ATTEMPTS = 3


class HistoricalAuditExportError(RuntimeError):
    """An exact historical revision could not be exported safely."""

    def __init__(self, code: str, **details: Any) -> None:
        self.code = str(code)
        self.details = dict(details)
        suffix = (
            ":" + json.dumps(self.details, ensure_ascii=False, sort_keys=True)
            if self.details
            else ""
        )
        super().__init__(f"{self.code}{suffix}")


def revision_archive_path(project_root: str | Path, chapter_sha256: str) -> Path:
    """Return the sole recognized local archive path for exact manuscript bytes."""

    digest = str(chapter_sha256 or "").strip().lower()
    if not _HASH_RE.fullmatch(digest):
        raise HistoricalAuditExportError(
            "historical_archive_digest_invalid",
            chapter_sha256=chapter_sha256,
        )
    return (
        Path(project_root).expanduser().resolve()
        / REVISION_ARCHIVE_RELATIVE_ROOT
        / f"{digest}.md"
    )


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - filesystem-specific behavior.
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - some filesystems reject directory fsync.
        pass
    finally:
        os.close(descriptor)


def archive_bound_manuscript(
    project_root: str | Path,
    chapter_binding: Mapping[str, Any] | ChapterContentBinding,
) -> dict[str, Any]:
    """Idempotently preserve exact bound bytes without touching Canon HEAD.

    Finalization can call this before its HEAD CAS.  A CAS failure may leave an
    unreachable archive blob, which is harmless; a published commit can never
    be left without its exact source merely because the author later edits the
    current manuscript.
    """

    root = Path(project_root).expanduser().resolve()
    try:
        binding = ChapterContentBinding.model_validate(chapter_binding)
    except Exception as exc:
        raise HistoricalAuditExportError(
            "historical_archive_binding_invalid"
        ) from exc
    source_path = root / binding.path
    try:
        source = resolve_inside_project(
            root, source_path, reject_leaf_symlink=True
        )
        raw = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise HistoricalAuditExportError(
            "historical_archive_source_unreadable",
            path=binding.path,
        ) from exc
    if (
        len(raw) != binding.bytes
        or hashlib.sha256(raw).hexdigest() != binding.sha256
    ):
        raise HistoricalAuditExportError(
            "historical_archive_source_binding_mismatch",
            path=binding.path,
        )

    target = revision_archive_path(root, binding.sha256)
    try:
        resolved_target = resolve_inside_project(
            root, target, reject_leaf_symlink=True
        )
    except ValueError as exc:
        raise HistoricalAuditExportError(
            "historical_archive_path_unsafe"
        ) from exc
    if resolved_target != target.absolute():
        raise HistoricalAuditExportError("historical_archive_path_redirected")
    target.parent.mkdir(parents=True, exist_ok=True)

    def validate_existing() -> None:
        try:
            safe = resolve_inside_project(
                root, target, reject_leaf_symlink=True
            )
            existing = safe.read_bytes()
        except (OSError, ValueError) as exc:
            raise HistoricalAuditExportError(
                "historical_archive_existing_unreadable"
            ) from exc
        if existing != raw:
            raise HistoricalAuditExportError(
                "historical_archive_content_collision",
                chapter_sha256=binding.sha256,
            )

    if target.exists():
        validate_existing()
        created = False
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{binding.sha256}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Same-directory hard-link publication is no-clobber and
                # exposes only the fully fsynced temporary bytes.
                os.link(temporary, target)
                created = True
                _fsync_directory(target.parent)
            except FileExistsError:
                validate_existing()
                created = False
            except OSError as exc:
                raise HistoricalAuditExportError(
                    "historical_archive_atomic_publish_failed"
                ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    return {
        "schema_version": REVISION_ARCHIVE_RESULT_SCHEMA,
        "chapter": binding.chapter,
        "chapter_binding": binding.model_dump(mode="json"),
        "archive_path": target.relative_to(root).as_posix(),
        "chapter_sha256": binding.sha256,
        "bytes": binding.bytes,
        "created": created,
    }


def _source_payload(
    root: Path,
    binding: ChapterContentBinding,
) -> dict[str, Any]:
    """Resolve exact manuscript bytes without reconstructing missing prose."""

    try:
        current = ChapterContentBinding.model_validate(
            build_chapter_binding(root, binding.chapter)
        )
    except ChapterBindingError as exc:
        current = None
        current_reason = exc.code
    except Exception:
        current = None
        current_reason = "current_manuscript_unreadable"
    else:
        current_reason = "current_manuscript_sha_mismatch"

    if (
        current is not None
        and current.sha256 == binding.sha256
        and current.bytes == binding.bytes
    ):
        current_path = root / current.path
        try:
            safe = resolve_inside_project(
                root, current_path, reject_leaf_symlink=True
            )
            raw = safe.read_bytes()
        except (OSError, ValueError):
            raw = b""
        if len(raw) == binding.bytes and hashlib.sha256(raw).hexdigest() == (
            binding.sha256
        ):
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                current_reason = "current_manuscript_invalid_utf8"
            else:
                return {
                    "status": "source_available",
                    "kind": "current_manuscript",
                    "path": safe.relative_to(root).as_posix(),
                    "sha256": binding.sha256,
                    "bytes": binding.bytes,
                    "content": content,
                }
        else:
            current_reason = "current_manuscript_changed_during_read"

    archive = revision_archive_path(root, binding.sha256)
    archive_reason = "revision_archive_missing"
    if archive.exists():
        try:
            safe_archive = resolve_inside_project(
                root, archive, reject_leaf_symlink=True
            )
            raw = safe_archive.read_bytes()
        except (OSError, ValueError):
            archive_reason = "revision_archive_unreadable"
        else:
            if len(raw) != binding.bytes or hashlib.sha256(raw).hexdigest() != (
                binding.sha256
            ):
                archive_reason = "revision_archive_digest_mismatch"
            else:
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    archive_reason = "revision_archive_invalid_utf8"
                else:
                    return {
                        "status": "source_available",
                        "kind": "revision_archive",
                        "path": safe_archive.relative_to(root).as_posix(),
                        "sha256": binding.sha256,
                        "bytes": binding.bytes,
                        "content": content,
                    }

    return {
        "status": "source_unavailable",
        "kind": None,
        "path": None,
        "sha256": binding.sha256,
        "bytes": binding.bytes,
        "content": None,
        "reason": current_reason,
        "archive_reason": archive_reason,
        "expected_archive_path": archive.relative_to(root).as_posix(),
    }


def _reachable_revision_hashes(
    repository: CanonV3Repository,
    head_hash: str,
    *,
    chapter: int,
    revision: int,
) -> tuple[str, ...]:
    """Find unique commit objects referenced by the current manifest ancestry."""

    cursor_hash = head_hash
    seen_manifests: set[str] = set()
    commits: set[str] = set()
    expected_generation: int | None = None
    while True:
        if cursor_hash in seen_manifests:
            raise CanonIntegrityError("canon_v3_manifest_parent_cycle")
        seen_manifests.add(cursor_hash)
        # ``read_manifest`` validates every chapter entry even when reference
        # validation is disabled.  That would make an ancestry walk quadratic
        # for long novels.  ``read_object`` still proves the content-addressed
        # envelope; validate only the chain fields and the one newly published
        # final entry needed by this lookup.
        manifest = repository.read_object("manifest", cursor_hash)
        if manifest.get("schema_version") != "canon-v3/active-manifest/v1":
            raise CanonIntegrityError("canon_v3_manifest_schema_mismatch")
        raw_generation = manifest.get("generation")
        if type(raw_generation) is not int or raw_generation < 0:
            raise CanonIntegrityError("canon_v3_manifest_generation_invalid")
        if expected_generation is not None and raw_generation != expected_generation:
            raise CanonIntegrityError("canon_v3_manifest_generation_not_monotonic")
        entries = manifest.get("chapters")
        if not isinstance(entries, list):
            raise CanonIntegrityError("canon_v3_manifest_chapters_invalid")
        # Every chapter publication appends/replaces the final entry of its
        # new manifest.  Inspecting only that entry finds every historical
        # revision without repeatedly scanning a growing chapter prefix.
        entry = entries[-1] if entries else None
        if (
            isinstance(entry, Mapping)
            and int(entry.get("chapter") or 0) == chapter
            and int(entry.get("revision") or 0) == revision
        ):
            commits.add(str(entry.get("commit_hash") or ""))
        if raw_generation == 0:
            if manifest.get("parent_head_hash") is not None:
                raise CanonIntegrityError("canon_v3_genesis_manifest_invalid")
            break
        parent = str(manifest.get("parent_head_hash") or "")
        if not _HASH_RE.fullmatch(parent):
            raise CanonIntegrityError("canon_v3_manifest_parent_invalid")
        expected_generation = raw_generation - 1
        cursor_hash = parent
    return tuple(sorted(commits))


def _active_author_axioms(
    root: Path,
    repository: CanonV3Repository,
    parent_head: str,
) -> dict[str, Any]:
    channel = AuthorAxiomChannel(root, repository=repository)
    records = channel.active_records(parent_head)
    superseded = channel._superseded_genesis_admission_digests(  # noqa: SLF001
        parent_head
    )
    genesis_admissions = [
        copy.deepcopy(item)
        for item in channel._genesis_axiom_admissions(parent_head)  # noqa: SLF001
        if str(item.get("admission_digest") or "") not in superseded
    ]
    manifest = repository.read_manifest(parent_head, validate_references=True)
    entries = repository._author_axiom_manifest_entries(manifest)  # noqa: SLF001
    commit_hash = str(entries[-1]["commit_hash"]) if entries else None
    return {
        "author_axiom_digest": channel.active_digest(parent_head),
        "active_commit_hash": commit_hash,
        "genesis_admissions": genesis_admissions,
        "superseded_genesis_admission_digests": sorted(superseded),
        "records": [item.model_dump(mode="json") for item in records],
        "record_digests": {
            item.axiom_key: record_digest(item) for item in records
        },
    }


def _prepared_envelope(
    repository: CanonV3Repository,
    transaction_hash: str,
) -> PreparedEnvelope:
    try:
        payload = repository.prepared_envelope_payload(transaction_hash)
        return PreparedEnvelope.model_validate(payload)
    except Exception as exc:
        raise HistoricalAuditExportError(
            "historical_transaction_envelope_invalid",
            transaction_hash=transaction_hash,
        ) from exc


def _decision_objects(
    repository: CanonV3Repository,
    hashes: Any,
) -> list[dict[str, Any]]:
    if not isinstance(hashes, (list, tuple)):
        raise HistoricalAuditExportError("historical_decision_hashes_invalid")
    return [
        {
            "object_hash": str(object_hash),
            "payload": repository.read_decision(str(object_hash)),
        }
        for object_hash in hashes
    ]


def _export_at_head(
    root: Path,
    repository: CanonV3Repository,
    *,
    audited_head: str,
    generation: int,
    chapter: int,
    revision: int,
    commit_hash: str | None,
) -> dict[str, Any]:
    candidates = _reachable_revision_hashes(
        repository,
        audited_head,
        chapter=chapter,
        revision=revision,
    )
    if commit_hash is not None:
        normalized = str(commit_hash or "").strip().lower()
        if not _HASH_RE.fullmatch(normalized):
            raise HistoricalAuditExportError(
                "historical_commit_hash_invalid", commit_hash=commit_hash
            )
        candidates = tuple(item for item in candidates if item == normalized)
    if not candidates:
        raise HistoricalAuditExportError(
            "historical_revision_not_reachable",
            chapter=chapter,
            revision=revision,
        )
    if len(candidates) > 1:
        raise HistoricalAuditExportError(
            "historical_revision_ambiguous",
            chapter=chapter,
            revision=revision,
            commit_hashes=list(candidates),
        )

    selected_commit_hash = candidates[0]
    commit = repository.read_commit(selected_commit_hash)
    transaction_hash = str(commit.get("transaction_hash") or "")
    envelope = _prepared_envelope(repository, transaction_hash)
    parent_head = str(commit.get("base_head_hash") or "")
    if not _HASH_RE.fullmatch(parent_head):
        raise HistoricalAuditExportError(
            "historical_parent_head_invalid",
            commit_hash=selected_commit_hash,
        )
    if envelope.prepared_transaction.parent_head != parent_head:
        raise HistoricalAuditExportError(
            "historical_parent_head_binding_mismatch",
            commit_hash=selected_commit_hash,
        )
    if envelope.chapter != chapter:
        raise HistoricalAuditExportError(
            "historical_transaction_chapter_mismatch",
            commit_hash=selected_commit_hash,
        )

    binding = envelope.chapter_binding
    axioms = _active_author_axioms(root, repository, parent_head)
    if (
        envelope.schema_version == PREPARED_ENVELOPE_SCHEMA
        and envelope.author_axiom_digest != axioms["author_axiom_digest"]
    ):
        raise HistoricalAuditExportError(
            "historical_author_axiom_binding_mismatch",
            commit_hash=selected_commit_hash,
        )
    registry = build_approved_entity_registry(
        repository,
        parent_head,
        target_chapter=chapter,
    )
    prepared_registry_digest = envelope.prepared_transaction.entity_registry_digest
    if (
        envelope.schema_version == PREPARED_ENVELOPE_SCHEMA
        and prepared_registry_digest != registry.registry_digest
    ):
        raise HistoricalAuditExportError(
            "historical_entity_registry_binding_mismatch",
            commit_hash=selected_commit_hash,
        )

    decision_hashes = list(commit.get("decision_hashes") or ())
    lineage_decision_hashes = list(
        commit.get("lineage_decision_hashes") or ()
    )
    payload: dict[str, Any] = {
        "schema_version": HISTORICAL_EXPORT_SCHEMA,
        "mode": "historical_audit_input",
        "disposition": "read_only",
        "authority": {
            "audited_head": audited_head,
            "generation": generation,
            "parent_head": parent_head,
            "commit_hash": selected_commit_hash,
            "transaction_hash": transaction_hash,
            "decision_hashes": decision_hashes,
            "lineage_decision_hashes": lineage_decision_hashes,
        },
        "commit": {
            "object_hash": selected_commit_hash,
            "payload": copy.deepcopy(commit),
        },
        "transaction": {
            "object_hash": transaction_hash,
            "payload": repository.read_transaction(transaction_hash),
        },
        "decisions": _decision_objects(repository, decision_hashes),
        "lineage_decisions": _decision_objects(
            repository, lineage_decision_hashes
        ),
        "chapter": chapter,
        "revision": revision,
        "chapter_binding": binding.model_dump(mode="json"),
        "source": _source_payload(root, binding),
        "candidate_digests": list(
            envelope.prepared_transaction.candidate_digests
        ),
        "candidates": [
            item.model_dump(mode="json") for item in envelope.candidates
        ],
        "effects": copy.deepcopy(commit.get("canon_effects") or []),
        "observations": [
            item.model_dump(mode="json") for item in envelope.observations
        ],
        "scan_attestations": [
            item.model_dump(mode="json") for item in envelope.scan_attestations
        ],
        "scan_attestation_digests": list(
            envelope.prepared_transaction.scan_attestation_digests
        ),
        "author_axioms": axioms,
        "entity_registry": {
            **registry.payload(),
            "registry_digest": registry.registry_digest,
            "transaction_registry_digest": prepared_registry_digest,
        },
        "source_workflow_digest": envelope.source_workflow_digest,
    }
    payload["export_digest"] = content_hash(payload)
    return payload


def export_historical_revision(
    project_root: str | Path,
    chapter: int,
    revision: int,
    *,
    commit_hash: str | None = None,
) -> dict[str, Any]:
    """Export one exact revision reachable from the captured active HEAD.

    ``commit_hash`` is only a disambiguator for the rare case where a suffix
    was truncated and later republished with the same numeric revision.  It is
    never accepted unless the commit is already reachable from CURRENT.
    """

    root = Path(project_root).expanduser().resolve()
    try:
        chapter_no = int(chapter)
        revision_no = int(revision)
    except (TypeError, ValueError) as exc:
        raise HistoricalAuditExportError(
            "historical_revision_number_invalid",
            chapter=chapter,
            revision=revision,
        ) from exc
    if chapter_no <= 0 or revision_no <= 0:
        raise HistoricalAuditExportError(
            "historical_revision_number_invalid",
            chapter=chapter_no,
            revision=revision_no,
        )

    repository = CanonV3Repository(root)
    for _attempt in range(_STABLE_EXPORT_ATTEMPTS):
        audited_head, manifest = repository.current_snapshot()
        if audited_head is None or manifest is None:
            raise HistoricalAuditExportError("historical_canon_not_initialized")
        result = _export_at_head(
            root,
            repository,
            audited_head=audited_head,
            generation=int(manifest.get("generation") or 0),
            chapter=chapter_no,
            revision=revision_no,
            commit_hash=commit_hash,
        )
        if repository.current_head(validate=False) == audited_head:
            return result
    raise HistoricalAuditExportError("historical_head_changed_during_export")


__all__ = [
    "HISTORICAL_EXPORT_SCHEMA",
    "REVISION_ARCHIVE_RESULT_SCHEMA",
    "REVISION_ARCHIVE_RELATIVE_ROOT",
    "HistoricalAuditExportError",
    "archive_bound_manuscript",
    "export_historical_revision",
    "revision_archive_path",
]

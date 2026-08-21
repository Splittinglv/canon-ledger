#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Immutable, content-addressed persistence for Canon v3.

This module deliberately has no dependency on the v2 commit schemas.  The v3
compiler can pass plain dictionaries until its typed domain model is wired in.
Only the manifest named by ``.story-system/v3/CURRENT`` is active canon;
transactions, decisions, commits, and manifests that are not reachable from
that pointer are harmless immutable objects.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

try:  # pragma: no cover - the Windows branch is exercised on Windows CI only.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - the POSIX branch is exercised in normal CI.
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None  # type: ignore[assignment]


V3_SCHEMA_PREFIX = "canon-v3"
TRANSACTION_SCHEMA = f"{V3_SCHEMA_PREFIX}/transaction-object/v1"
DECISION_SCHEMA = f"{V3_SCHEMA_PREFIX}/decision-object/v1"
COMMIT_SCHEMA = f"{V3_SCHEMA_PREFIX}/commit-object/v1"
MANIFEST_SCHEMA = f"{V3_SCHEMA_PREFIX}/manifest-object/v1"
AUTHOR_AXIOM_TRANSACTION_SCHEMA = (
    f"{V3_SCHEMA_PREFIX}/author-axiom-transaction-object/v1"
)
AUTHOR_AXIOM_DECISION_SCHEMA = (
    f"{V3_SCHEMA_PREFIX}/author-axiom-decision-object/v1"
)
AUTHOR_AXIOM_COMMIT_SCHEMA = (
    f"{V3_SCHEMA_PREFIX}/author-axiom-commit-object/v1"
)
PROJECTION_BINDING_SCHEMA = f"{V3_SCHEMA_PREFIX}/projection-binding/v1"
NEW_PROJECT_GENESIS_SCHEMA = f"{V3_SCHEMA_PREFIX}/genesis-metadata/v1"
RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA = (
    f"{V3_SCHEMA_PREFIX}/legacy-recertified-suffix-transaction/v1"
)

V3_RELATIVE_ROOT = Path(".story-system") / "v3"
CURRENT_FILE = "CURRENT"
LOCK_FILE = ".publish.lock"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class CanonRepositoryError(RuntimeError):
    """Base class for v3 persistence failures."""


class CanonIntegrityError(CanonRepositoryError):
    """A content-addressed object or reference failed integrity validation."""


class CanonHeadConflict(CanonRepositoryError):
    """The active Canon HEAD differs from the caller's expected parent."""

    def __init__(self, *, expected: str | None, actual: str | None) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            "canon_v3_head_conflict: "
            f"expected={expected or '<empty>'}, actual={actual or '<empty>'}"
        )


class CanonChapterSequenceError(CanonRepositoryError):
    """A chapter publish would create a gap in the active manifest."""


class ProjectionStaleError(CanonRepositoryError):
    """A derived projection does not bind the exact active Canon HEAD."""


FaultInjector = Callable[[str], None]


@dataclass(frozen=True)
class ProjectionBinding:
    """Exact source generation used by a derived projection."""

    generation: int
    head_hash: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECTION_BINDING_SCHEMA,
            "generation": self.generation,
            "head_hash": self.head_hash,
        }


@dataclass(frozen=True)
class SealResult:
    """Stable identifiers produced by a successful (or idempotent) seal."""

    transaction_hash: str
    decision_hashes: tuple[str, ...]
    commit_hash: str
    manifest_hash: str
    chapter: int
    revision: int
    generation: int
    created: bool

    @property
    def head_hash(self) -> str:
        return self.manifest_hash

    @property
    def projection_binding(self) -> ProjectionBinding:
        return ProjectionBinding(
            generation=self.generation,
            head_hash=self.manifest_hash,
        )


@dataclass(frozen=True)
class AuthorAxiomSealResult:
    transaction_hash: str
    decision_hashes: tuple[str, ...]
    commit_hash: str
    manifest_hash: str
    revision: int
    generation: int
    created: bool

    @property
    def head_hash(self) -> str:
        return self.manifest_hash


def canonical_json_bytes(value: Any) -> bytes:
    """Return the one canonical JSON representation accepted by the store."""
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CanonRepositoryError(f"canon_v3_not_json_serializable: {exc}") from exc
    return rendered.encode("utf-8")


def content_hash(value: Any) -> str:
    """Hash JSON content using the repository's canonical serialization."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _deep_json_copy(value: Any) -> Any:
    """Copy and validate a value without retaining caller-owned references."""
    raw = canonical_json_bytes(value)
    return json.loads(raw.decode("utf-8"))


def _object_envelope(kind: str, payload: Mapping[str, Any], schema: str) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "kind": kind,
        "payload": _deep_json_copy(dict(payload)),
    }


def _validate_hash(value: str, *, label: str = "object_hash") -> str:
    normalized = str(value or "").strip().lower()
    if not _HASH_RE.fullmatch(normalized):
        raise CanonIntegrityError(f"canon_v3_invalid_{label}: {value!r}")
    return normalized


def _fsync_directory(path: Path) -> None:
    """Best-effort directory sync after publishing a file name."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - unusual filesystem/platform behavior.
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover - some filesystems reject directory fsync.
        pass
    finally:
        os.close(descriptor)


def _atomic_replace_bytes(path: Path, raw: bytes) -> None:
    """Durably replace one small file using a same-directory rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class _ProjectFileLock(AbstractContextManager["_ProjectFileLock"]):
    """Cross-process exclusive lock covering HEAD compare-and-swap."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def __enter__(self) -> "_ProjectFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover
            self._handle.seek(0)
            if self._handle.read(1) == b"":
                self._handle.write(b"0")
                self._handle.flush()
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
        else:  # pragma: no cover
            self._handle.close()
            self._handle = None
            raise CanonRepositoryError("canon_v3_project_lock_unavailable")
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._handle.close()
            self._handle = None


class CanonV3Repository:
    """Content-addressed Canon v3 object store and atomic HEAD publisher."""

    _OBJECT_SPECS = {
        "transaction": ("transactions", TRANSACTION_SCHEMA),
        "decision": ("decisions", DECISION_SCHEMA),
        "commit": ("commits", COMMIT_SCHEMA),
        "manifest": ("manifests", MANIFEST_SCHEMA),
        "author_axiom_transaction": (
            "author_axiom_transactions",
            AUTHOR_AXIOM_TRANSACTION_SCHEMA,
        ),
        "author_axiom_decision": (
            "author_axiom_decisions",
            AUTHOR_AXIOM_DECISION_SCHEMA,
        ),
        "author_axiom_commit": (
            "author_axiom_commits",
            AUTHOR_AXIOM_COMMIT_SCHEMA,
        ),
    }

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.root = self.project_root / V3_RELATIVE_ROOT

    @property
    def current_path(self) -> Path:
        return self.root / CURRENT_FILE

    @property
    def lock_path(self) -> Path:
        return self.root / LOCK_FILE

    def locked(self) -> AbstractContextManager[Any]:
        """Return the project publication lock for an explicit compound action."""
        return _ProjectFileLock(self.lock_path)

    def object_path(self, kind: str, object_hash: str) -> Path:
        if kind not in self._OBJECT_SPECS:
            raise CanonRepositoryError(f"canon_v3_unknown_object_kind: {kind}")
        normalized = _validate_hash(object_hash)
        directory, _schema = self._OBJECT_SPECS[kind]
        return self.root / directory / f"{normalized}.json"

    def _put_payload_unlocked(self, kind: str, payload: Mapping[str, Any]) -> str:
        directory, schema = self._OBJECT_SPECS[kind]
        envelope = _object_envelope(kind, payload, schema)
        raw = canonical_json_bytes(envelope)
        object_hash = hashlib.sha256(raw).hexdigest()
        path = self.root / directory / f"{object_hash}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise CanonIntegrityError(
                    f"canon_v3_object_unreadable: {path}"
                ) from exc
            if existing != raw:
                raise CanonIntegrityError(
                    f"canon_v3_hash_collision_or_mutation: {kind}:{object_hash}"
                )
            return object_hash
        _atomic_replace_bytes(path, raw)
        return object_hash

    def put_transaction(self, payload: Mapping[str, Any]) -> str:
        with self.locked():
            return self._put_payload_unlocked("transaction", payload)

    def put_decision(self, payload: Mapping[str, Any]) -> str:
        with self.locked():
            return self._put_payload_unlocked("decision", payload)

    def put_author_axiom_transaction(self, payload: Mapping[str, Any]) -> str:
        with self.locked():
            return self._put_payload_unlocked(
                "author_axiom_transaction", payload
            )

    def put_author_axiom_decision(self, payload: Mapping[str, Any]) -> str:
        with self.locked():
            return self._put_payload_unlocked("author_axiom_decision", payload)

    def _validate_genesis_metadata(
        self,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        raw = dict(metadata or {})
        if not raw:
            return {
                "schema_version": NEW_PROJECT_GENESIS_SCHEMA,
                "source": "new_project",
                "cutover_chapter": 0,
            }
        schema = str(raw.get("schema_version") or "")
        source = str(raw.get("source") or "")
        if schema == NEW_PROJECT_GENESIS_SCHEMA:
            if set(raw) != {"schema_version", "source", "cutover_chapter"}:
                raise CanonRepositoryError("canon_v3_new_genesis_fields_invalid")
            if source != "new_project" or raw.get("cutover_chapter") != 0:
                raise CanonRepositoryError("canon_v3_new_genesis_invalid")
            return _deep_json_copy(raw)
        if schema not in {
            "canon-v3/legacy-genesis/v1",
            "canon-v3/legacy-genesis/v2",
        }:
            raise CanonRepositoryError("canon_v3_genesis_metadata_schema_invalid")
        legacy_v1 = schema == "canon-v3/legacy-genesis/v1"
        required_keys = {
            "schema_version",
            "source",
            "cutover_chapter",
            "v2_commits",
            "legacy_snapshot",
            "legacy_snapshot_sha256",
        }
        allowed_keys = set(required_keys)
        if not legacy_v1:
            allowed_keys.add("recertification")
        if not required_keys.issubset(raw) or not set(raw).issubset(allowed_keys):
            raise CanonRepositoryError("canon_v3_legacy_genesis_fields_invalid")
        try:
            cutover = int(raw.get("cutover_chapter"))
        except (TypeError, ValueError) as exc:
            raise CanonRepositoryError("canon_v3_legacy_cutover_invalid") from exc
        refs = raw.get("v2_commits")
        snapshot = raw.get("legacy_snapshot")
        if not isinstance(refs, list) or not isinstance(snapshot, dict):
            raise CanonRepositoryError("canon_v3_legacy_genesis_shape_invalid")
        if content_hash(snapshot) != str(raw.get("legacy_snapshot_sha256") or ""):
            raise CanonRepositoryError("canon_v3_legacy_snapshot_hash_invalid")
        expected_snapshot_schema = (
            "canon-v3/legacy-fact-snapshot/v1"
            if legacy_v1
            else "canon-v3/legacy-fact-snapshot/v2"
        )
        if snapshot.get("schema_version") != expected_snapshot_schema:
            raise CanonRepositoryError("canon_v3_legacy_snapshot_schema_invalid")
        if int(snapshot.get("cutover_chapter") or 0) != cutover:
            raise CanonRepositoryError("canon_v3_legacy_snapshot_cutover_mismatch")
        if source == "new_project":
            if cutover != 0 or refs:
                raise CanonRepositoryError("canon_v3_legacy_new_project_invalid")
        elif source != "v2_accepted_commits" or cutover <= 0:
            raise CanonRepositoryError("canon_v3_legacy_source_invalid")
        chapters = [
            int(item.get("chapter") or 0) if isinstance(item, dict) else 0
            for item in refs
        ]
        if chapters != list(range(1, cutover + 1)):
            raise CanonRepositoryError("canon_v3_legacy_commit_refs_not_contiguous")
        for chapter, item in zip(chapters, refs):
            assert isinstance(item, dict)
            if set(item) != {
                "schema_version",
                "chapter",
                "path",
                "content_sha256",
                "manuscript_binding",
            }:
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_fields_invalid")
            expected_ref_schema = (
                "canon-v3/legacy-v2-commit-ref/v1"
                if legacy_v1
                else "canon-v3/legacy-v2-commit-ref/v2"
            )
            if item.get("schema_version") != expected_ref_schema:
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_schema_invalid")
            relative = str(item.get("path") or "")
            expected_path = f".story-system/commits/chapter_{chapter:03d}.commit.json"
            if relative != expected_path:
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_path_invalid")
            path = (self.project_root / relative).resolve()
            try:
                path.relative_to(self.project_root)
                source_bytes = path.read_bytes()
            except (ValueError, OSError) as exc:
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_unreadable") from exc
            if hashlib.sha256(source_bytes).hexdigest() != item.get("content_sha256"):
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_hash_mismatch")
            try:
                payload = json.loads(source_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_invalid_json") from exc
            meta = payload.get("meta") if isinstance(payload, dict) else {}
            if (
                not isinstance(meta, dict)
                or meta.get("chapter") != chapter
                or meta.get("status") != "accepted"
                or payload.get("chapter_binding") != item.get("manuscript_binding")
            ):
                raise CanonRepositoryError("canon_v3_legacy_commit_ref_payload_invalid")
            from ..chapter_content_binding import verify_commit_content_binding

            ok, code = verify_commit_content_binding(
                self.project_root,
                chapter,
                payload,
            )
            if not ok:
                raise CanonRepositoryError(
                    f"canon_v3_legacy_commit_binding_invalid:{chapter}:{code}"
                )
        # A self-hash only proves internal consistency; it does not prove that
        # the imported facts came from the verified v2 prefix.  Rebuild the
        # fact-only snapshot from those exact sources and require full equality
        # before a public initialize may publish CURRENT.
        if not legacy_v1:
            try:
                from .migration import _fact_snapshot

                verified_snapshot = _fact_snapshot(self.project_root, cutover)
            except Exception as exc:
                raise CanonRepositoryError(
                    "canon_v3_legacy_snapshot_recompute_failed"
                ) from exc
            if snapshot != verified_snapshot:
                raise CanonRepositoryError(
                    "canon_v3_legacy_snapshot_provenance_mismatch"
                )
        recertification = raw.get("recertification")
        if recertification is not None:
            if legacy_v1 or not isinstance(recertification, dict):
                raise CanonRepositoryError(
                    "canon_v3_legacy_recertification_receipt_invalid"
                )
            receipt_fields = {
                "schema_version",
                "prior_head_hash",
                "detached_plan_digest",
                "publish_token",
                "review_decisions",
                "review_decision_set_digest",
                "review_cases_digest",
                "semantic_negative_lineage",
            }
            if (
                set(recertification) != receipt_fields
                or recertification.get("schema_version")
                != "canon-v3/legacy-recertification-receipt/v1"
            ):
                raise CanonRepositoryError(
                    "canon_v3_legacy_recertification_receipt_invalid"
                )
            digest_fields = (
                "prior_head_hash",
                "detached_plan_digest",
                "publish_token",
                "review_decision_set_digest",
                "review_cases_digest",
            )
            if any(
                not isinstance(recertification.get(field), str)
                or not _HASH_RE.fullmatch(str(recertification.get(field)))
                for field in digest_fields
            ):
                raise CanonRepositoryError(
                    "canon_v3_legacy_recertification_digest_invalid"
                )
            decisions = recertification.get("review_decisions")
            if (
                not isinstance(decisions, list)
                or content_hash(decisions)
                != recertification.get("review_decision_set_digest")
            ):
                raise CanonRepositoryError(
                    "canon_v3_legacy_recertification_decisions_invalid"
                )
            decision_keys: list[str] = []
            for decision in decisions:
                if (
                    not isinstance(decision, dict)
                    or set(decision)
                    != {
                        "schema_version",
                        "case_key",
                        "target_digest",
                        "material_digest",
                        "action",
                    }
                    or decision.get("schema_version")
                    != "canon-v3/legacy-recertification-decision/v1"
                    or decision.get("action") != "confirm"
                ):
                    raise CanonRepositoryError(
                        "canon_v3_legacy_recertification_decision_invalid"
                    )
                hashes = (
                    decision.get("case_key"),
                    decision.get("target_digest"),
                    decision.get("material_digest"),
                )
                if any(
                    not isinstance(value, str) or not _HASH_RE.fullmatch(value)
                    for value in hashes
                ):
                    raise CanonRepositoryError(
                        "canon_v3_legacy_recertification_decision_invalid"
                    )
                decision_keys.append(str(decision["case_key"]))
            if decision_keys != sorted(set(decision_keys)):
                raise CanonRepositoryError(
                    "canon_v3_legacy_recertification_decisions_not_canonical"
                )
            lineage = recertification.get("semantic_negative_lineage")
            if not isinstance(lineage, dict):
                raise CanonRepositoryError(
                    "canon_v3_legacy_recertification_lineage_invalid"
                )
            for chapter, hashes in lineage.items():
                try:
                    chapter_number = int(chapter)
                except (TypeError, ValueError) as exc:
                    raise CanonRepositoryError(
                        "canon_v3_legacy_recertification_lineage_invalid"
                    ) from exc
                if (
                    chapter_number <= 0
                    or str(chapter_number) != str(chapter)
                    or not isinstance(hashes, list)
                    or hashes != sorted(set(hashes))
                    or any(
                        not isinstance(value, str) or not _HASH_RE.fullmatch(value)
                        for value in hashes
                    )
                ):
                    raise CanonRepositoryError(
                        "canon_v3_legacy_recertification_lineage_invalid"
                    )
                for decision_hash in hashes:
                    self.read_decision(decision_hash)
        return _deep_json_copy(raw)

    def initialize(
        self,
        *,
        expected_head: str | None = None,
        genesis_metadata: Mapping[str, Any] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> str:
        """Create the empty generation-0 manifest and publish it as CURRENT.

        The public boundary only accepts a provenance-verified fact snapshot.
        Storage-only empty genesis creation is private and used by repository
        fault tests; production callers must use CanonV3Service/migrate_legacy.
        """
        if (
            genesis_metadata is None
            or genesis_metadata.get("schema_version")
            != "canon-v3/legacy-genesis/v2"
        ):
            raise CanonRepositoryError(
                "canon_v3_public_initialize_requires_verified_fact_snapshot"
            )
        metadata = self._validate_genesis_metadata(genesis_metadata)
        return self._initialize_objects(
            expected_head=expected_head,
            genesis_metadata=metadata,
            fault_injector=fault_injector,
        )

    def _initialize_objects(
        self,
        *,
        expected_head: str | None = None,
        genesis_metadata: Mapping[str, Any] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> str:
        """Low-level deterministic genesis primitive for storage fault tests."""

        expected = (
            _validate_hash(expected_head, label="expected_head")
            if expected_head is not None
            else None
        )
        metadata = _deep_json_copy(dict(genesis_metadata or {}))
        with self.locked():
            actual = self._read_current_hash_unvalidated()
            if actual != expected:
                raise CanonHeadConflict(expected=expected, actual=actual)
            genesis_payload = {
                "schema_version": f"{V3_SCHEMA_PREFIX}/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": metadata,
            }
            if actual is not None:
                current = self.read_manifest(actual, validate_references=True)
                if current == genesis_payload:
                    return actual
                raise CanonRepositoryError("canon_v3_already_initialized")
            manifest_hash = self._put_payload_unlocked("manifest", genesis_payload)
            self._inject(fault_injector, "after_manifest")
            self._inject(fault_injector, "before_head_swap")
            self._write_current_unlocked(manifest_hash)
            self._inject(fault_injector, "after_head_swap")
            return manifest_hash

    def _read_envelope(self, kind: str, object_hash: str) -> dict[str, Any]:
        path = self.object_path(kind, object_hash)
        try:
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise CanonIntegrityError(
                f"canon_v3_missing_object: {kind}:{object_hash}"
            ) from exc
        except OSError as exc:
            raise CanonIntegrityError(
                f"canon_v3_object_unreadable: {kind}:{object_hash}"
            ) from exc
        actual = hashlib.sha256(raw).hexdigest()
        expected = _validate_hash(object_hash)
        if actual != expected:
            raise CanonIntegrityError(
                f"canon_v3_object_hash_mismatch: {kind}:{expected}"
            )
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CanonIntegrityError(
                f"canon_v3_object_invalid_json: {kind}:{expected}"
            ) from exc
        if not isinstance(envelope, dict):
            raise CanonIntegrityError(
                f"canon_v3_object_not_mapping: {kind}:{expected}"
            )
        _directory, schema = self._OBJECT_SPECS[kind]
        if envelope.get("kind") != kind or envelope.get("schema_version") != schema:
            raise CanonIntegrityError(
                f"canon_v3_object_envelope_mismatch: {kind}:{expected}"
            )
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            raise CanonIntegrityError(
                f"canon_v3_object_payload_not_mapping: {kind}:{expected}"
            )
        if canonical_json_bytes(envelope) != raw:
            raise CanonIntegrityError(
                f"canon_v3_object_not_canonical_json: {kind}:{expected}"
            )
        return envelope

    def read_object(self, kind: str, object_hash: str) -> dict[str, Any]:
        return copy.deepcopy(self._read_envelope(kind, object_hash)["payload"])

    def read_transaction(self, object_hash: str) -> dict[str, Any]:
        return self.read_object("transaction", object_hash)

    def recertified_suffix_wrapper(
        self,
        object_hash: str,
    ) -> dict[str, Any] | None:
        """Validate and return a detached-recompiled suffix wrapper."""

        payload = self.read_transaction(object_hash)
        if payload.get("schema_version") != RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA:
            return None
        expected_fields = {
            "schema_version",
            "chapter",
            "parent_head",
            "source_current_head",
            "source_commit_hash",
            "source_transaction_hash",
            "source_transaction_content_sha256",
            "source_canon_effects_digest",
            "source_decision_hashes",
            "source_lineage_decision_hashes",
            "active_source_candidate_digests",
            "recertified_envelope",
            "recertified_canon_effects_digest",
            "entity_registry_digest",
            "recertification_binding",
            "semantic_negative_lineage_hashes",
        }
        if set(payload) != expected_fields:
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_fields_invalid"
            )
        hash_fields = (
            "parent_head",
            "source_current_head",
            "source_commit_hash",
            "source_transaction_hash",
            "source_transaction_content_sha256",
            "source_canon_effects_digest",
            "recertified_canon_effects_digest",
            "entity_registry_digest",
        )
        if any(
            not isinstance(payload.get(field), str)
            or not _HASH_RE.fullmatch(str(payload.get(field)))
            for field in hash_fields
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_digest_invalid"
            )
        try:
            chapter = int(payload.get("chapter"))
        except (TypeError, ValueError) as exc:
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_chapter_invalid"
            ) from exc
        if chapter <= 0:
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_chapter_invalid"
            )

        source_head = str(payload["source_current_head"])
        source_manifest = self.read_manifest(
            source_head, validate_references=True
        )
        source_commit_hash = str(payload["source_commit_hash"])
        if source_commit_hash not in {
            str(entry.get("commit_hash") or "")
            for entry in source_manifest.get("chapters") or ()
        }:
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_source_commit_not_active"
            )
        source_commit = self.read_commit(source_commit_hash)
        source_transaction_hash = str(payload["source_transaction_hash"])
        if (
            int(source_commit.get("chapter") or 0) != chapter
            or source_commit.get("transaction_hash") != source_transaction_hash
            or list(source_commit.get("decision_hashes") or ())
            != payload.get("source_decision_hashes")
            or list(source_commit.get("lineage_decision_hashes") or ())
            != payload.get("source_lineage_decision_hashes")
            or content_hash(source_commit.get("canon_effects") or [])
            != payload.get("source_canon_effects_digest")
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_source_commit_mismatch"
            )
        source_transaction = self.read_transaction(source_transaction_hash)
        if (
            source_transaction.get("schema_version")
            == RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA
            or content_hash(source_transaction)
            != payload.get("source_transaction_content_sha256")
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_source_transaction_invalid"
            )
        try:
            from .service import PreparedEnvelope
            from .evidence import candidate_digest
            from .schema import AuthorAxiomSource
            from .author_axiom import (
                AuthorAxiomChannel,
                active_candidate_source_key,
            )

            source_envelope = PreparedEnvelope.model_validate(
                source_transaction
            )
            recertified_envelope = PreparedEnvelope.model_validate(
                payload.get("recertified_envelope")
            )
        except Exception as exc:
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_envelope_invalid"
            ) from exc
        if (
            source_envelope.chapter != chapter
            or recertified_envelope.chapter != chapter
            or recertified_envelope.chapter_binding
            != source_envelope.chapter_binding
            or recertified_envelope.prepared_transaction.parent_head
            != payload.get("parent_head")
            or recertified_envelope.prepared_transaction.entity_registry_digest
            != payload.get("entity_registry_digest")
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_envelope_binding_mismatch"
            )
        active_digests = payload.get("active_source_candidate_digests")
        if (
            not isinstance(active_digests, list)
            or active_digests != sorted(set(active_digests))
            or any(
                not isinstance(value, str) or not _HASH_RE.fullmatch(value)
                for value in active_digests
            )
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_candidates_invalid"
            )
        source_candidate_digests = {
            candidate_digest(candidate)
            for candidate in source_envelope.candidates
        }
        source_effect_digests = sorted(
            {
                str(effect.get("candidate_digest") or "")
                for effect in source_commit.get("canon_effects") or ()
                if isinstance(effect, Mapping)
            }
        )
        recertified_candidate_digests = sorted(
            candidate_digest(candidate)
            for candidate in recertified_envelope.candidates
        )
        if (
            set(active_digests) - source_candidate_digests
            or active_digests != source_effect_digests
            or active_digests != recertified_candidate_digests
            or content_hash(
                [
                    effect.model_dump(mode="json")
                    for effect in recertified_envelope.prepared_transaction.effects
                ]
            )
            != payload.get("recertified_canon_effects_digest")
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_effect_recompile_mismatch"
            )
        active_axiom_source_keys = AuthorAxiomChannel(
            self.project_root,
            repository=self,
        ).active_candidate_source_keys(str(payload.get("parent_head") or ""))
        if any(
            active_candidate_source_key(source)
            not in active_axiom_source_keys
            for candidate in recertified_envelope.candidates
            for source in candidate.sources
            if isinstance(source, AuthorAxiomSource)
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_author_axiom_membership_invalid"
            )
        binding = payload.get("recertification_binding")
        binding_fields = {
            "prior_head_hash",
            "detached_plan_digest",
            "publish_token",
            "review_decision_set_digest",
            "review_cases_digest",
        }
        if (
            not isinstance(binding, dict)
            or set(binding) != binding_fields
            or any(
                not isinstance(binding.get(field), str)
                or not _HASH_RE.fullmatch(str(binding.get(field)))
                for field in binding_fields
            )
            or binding.get("prior_head_hash") != source_head
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_review_binding_invalid"
            )
        negative = payload.get("semantic_negative_lineage_hashes")
        if (
            not isinstance(negative, list)
            or negative != sorted(set(negative))
            or any(
                not isinstance(value, str) or not _HASH_RE.fullmatch(value)
                for value in negative
            )
        ):
            raise CanonIntegrityError(
                "canon_v3_recertified_suffix_lineage_invalid"
            )
        return copy.deepcopy(payload)

    def prepared_envelope_payload(self, object_hash: str) -> dict[str, Any]:
        """Resolve normal and recertified transactions through one validator."""

        wrapper = self.recertified_suffix_wrapper(object_hash)
        if wrapper is not None:
            return copy.deepcopy(wrapper["recertified_envelope"])
        return self.read_transaction(object_hash)

    def read_decision(self, object_hash: str) -> dict[str, Any]:
        return self.read_object("decision", object_hash)

    def read_author_axiom_transaction(
        self, object_hash: str
    ) -> dict[str, Any]:
        return self.read_object("author_axiom_transaction", object_hash)

    def read_author_axiom_decision(self, object_hash: str) -> dict[str, Any]:
        return self.read_object("author_axiom_decision", object_hash)

    def read_author_axiom_commit(self, object_hash: str) -> dict[str, Any]:
        payload = self.read_object("author_axiom_commit", object_hash)
        self._validate_author_axiom_commit_payload(payload)
        return payload

    def read_commit(self, object_hash: str) -> dict[str, Any]:
        payload = self.read_object("commit", object_hash)
        self._validate_commit_payload(payload)
        return payload

    def read_manifest(
        self,
        object_hash: str,
        *,
        validate_references: bool = True,
    ) -> dict[str, Any]:
        payload = self.read_object("manifest", object_hash)
        self._validate_manifest_payload(payload, validate_references=validate_references)
        return payload

    def _read_current_hash_unvalidated(self) -> str | None:
        try:
            raw = self.current_path.read_text(encoding="ascii")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise CanonIntegrityError("canon_v3_current_unreadable") from exc
        value = raw.strip()
        if not value:
            raise CanonIntegrityError("canon_v3_current_empty")
        return _validate_hash(value, label="current_head")

    def current_head(self, *, validate: bool = True) -> str | None:
        head = self._read_current_hash_unvalidated()
        if head is not None and validate:
            self.read_manifest(head, validate_references=True)
        return head

    def current_manifest(self) -> dict[str, Any] | None:
        head = self.current_head(validate=False)
        if head is None:
            return None
        return self.read_manifest(head, validate_references=True)

    def _write_current_unlocked(self, manifest_hash: str) -> None:
        normalized = _validate_hash(manifest_hash, label="manifest_hash")
        # Validate the entire reachable revision before making it visible.
        self.read_manifest(normalized, validate_references=True)
        _atomic_replace_bytes(self.current_path, f"{normalized}\n".encode("ascii"))

    @staticmethod
    def _chapter_from_payload(payload: Mapping[str, Any]) -> int | None:
        value = payload.get("chapter")
        if value is None:
            meta = payload.get("meta")
            if isinstance(meta, Mapping):
                value = meta.get("chapter")
        if value is None:
            return None
        try:
            chapter = int(value)
        except (TypeError, ValueError) as exc:
            raise CanonRepositoryError(
                f"canon_v3_invalid_transaction_chapter: {value!r}"
            ) from exc
        return chapter

    @staticmethod
    def _normalize_effects(effects: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, effect in enumerate(effects):
            if not isinstance(effect, Mapping):
                raise CanonRepositoryError(
                    f"canon_v3_effect_not_mapping: index={index}"
                )
            normalized.append(_deep_json_copy(dict(effect)))
        return normalized

    def _resolve_transaction_unlocked(
        self,
        transaction: Mapping[str, Any] | str,
    ) -> tuple[str, dict[str, Any]]:
        if isinstance(transaction, str):
            transaction_hash = _validate_hash(transaction, label="transaction_hash")
            return transaction_hash, self.read_transaction(transaction_hash)
        if not isinstance(transaction, Mapping):
            raise CanonRepositoryError("canon_v3_transaction_not_mapping_or_hash")
        payload = _deep_json_copy(dict(transaction))
        return self._put_payload_unlocked("transaction", payload), payload

    def _resolve_decisions_unlocked(
        self,
        decisions: Sequence[Mapping[str, Any] | str],
        *,
        transaction_hash: str,
        chapter: int,
    ) -> tuple[str, ...]:
        hashes: set[str] = set()
        for decision in decisions:
            if isinstance(decision, str):
                decision_hash = _validate_hash(decision, label="decision_hash")
                payload = self.read_decision(decision_hash)
            elif isinstance(decision, Mapping):
                payload = _deep_json_copy(dict(decision))
                decision_hash = self._put_payload_unlocked("decision", payload)
            else:
                raise CanonRepositoryError(
                    "canon_v3_decision_not_mapping_or_hash"
                )
            bound_transaction = str(payload.get("transaction_hash") or "").strip()
            if bound_transaction and bound_transaction != transaction_hash:
                raise CanonRepositoryError(
                    "canon_v3_decision_transaction_mismatch: "
                    f"decision={decision_hash}"
                )
            decision_chapter = self._chapter_from_payload(payload)
            if decision_chapter is not None and decision_chapter != chapter:
                raise CanonRepositoryError(
                    "canon_v3_decision_chapter_mismatch: "
                    f"decision={decision_hash}, expected={chapter}, "
                    f"actual={decision_chapter}"
                )
            hashes.add(decision_hash)
        return tuple(sorted(hashes))

    def _resolve_lineage_decisions_unlocked(
        self,
        decisions: Sequence[Mapping[str, Any] | str],
        *,
        chapter: int,
    ) -> tuple[str, ...]:
        hashes: set[str] = set()
        for decision in decisions:
            if isinstance(decision, str):
                decision_hash = _validate_hash(decision, label="decision_hash")
                payload = self.read_decision(decision_hash)
            elif isinstance(decision, Mapping):
                payload = _deep_json_copy(dict(decision))
                decision_hash = self._put_payload_unlocked("decision", payload)
            else:
                raise CanonRepositoryError(
                    "canon_v3_lineage_decision_not_mapping_or_hash"
                )
            decision_chapter = self._chapter_from_payload(payload)
            if decision_chapter is not None and decision_chapter != chapter:
                raise CanonRepositoryError(
                    "canon_v3_lineage_decision_chapter_mismatch:"
                    f"decision={decision_hash},expected={chapter},"
                    f"actual={decision_chapter}"
                )
            hashes.add(decision_hash)
        return tuple(sorted(hashes))

    @staticmethod
    def _manifest_entries(manifest: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if not manifest:
            return []
        raw = manifest.get("chapters")
        if not isinstance(raw, list):
            raise CanonIntegrityError("canon_v3_manifest_chapters_not_list")
        return [copy.deepcopy(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _author_axiom_manifest_entries(
        manifest: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not manifest:
            return []
        raw = manifest.get("author_axiom_commits", [])
        if not isinstance(raw, list):
            raise CanonIntegrityError(
                "canon_v3_manifest_author_axiom_commits_not_list"
            )
        if any(not isinstance(item, dict) for item in raw):
            raise CanonIntegrityError(
                "canon_v3_manifest_author_axiom_entry_not_mapping"
            )
        return [copy.deepcopy(item) for item in raw]

    @staticmethod
    def _entry_for_chapter(
        entries: Sequence[Mapping[str, Any]],
        chapter: int,
    ) -> Mapping[str, Any] | None:
        return next(
            (entry for entry in entries if int(entry.get("chapter") or 0) == chapter),
            None,
        )

    @staticmethod
    def _inject(fault_injector: FaultInjector | None, stage: str) -> None:
        if fault_injector is not None:
            fault_injector(stage)

    def seal(
        self,
        *,
        chapter: int,
        transaction: Mapping[str, Any] | str,
        expected_head: str | None,
        decisions: Sequence[Mapping[str, Any] | str] = (),
        lineage_decisions: Sequence[Mapping[str, Any] | str] = (),
        canon_effects: Sequence[Mapping[str, Any]] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> SealResult:
        """Publish only a fully recompiled, review-complete v3 transaction."""

        return self._seal_objects(
            chapter=chapter,
            transaction=transaction,
            expected_head=expected_head,
            decisions=decisions,
            lineage_decisions=lineage_decisions,
            canon_effects=canon_effects,
            fault_injector=fault_injector,
            _strict_publication=True,
        )

    def _validate_compiled_publication_unlocked(
        self,
        *,
        chapter: int,
        transaction_hash: str,
        transaction_payload: Mapping[str, Any],
        decision_hashes: tuple[str, ...],
        lineage_decision_hashes: tuple[str, ...],
        effects: list[dict[str, Any]],
        expected_head: str | None,
    ) -> None:
        """Re-run the application invariant inside the HEAD publication lock."""

        try:
            from ..chapter_content_binding import require_chapter_binding
            from .review import WorkflowState
            from .service import (
                CanonV3Service,
                PreparedEnvelope,
                StagingPointer,
            )

            envelope = PreparedEnvelope.model_validate(transaction_payload)
            if envelope.chapter != int(chapter):
                raise ValueError("chapter_mismatch")
            if envelope.prepared_transaction.parent_head != expected_head:
                raise ValueError("parent_head_mismatch")
            require_chapter_binding(
                self.project_root,
                envelope.chapter,
                envelope.chapter_binding,
            )
            service = CanonV3Service(self.project_root)
            service.repository = self
            authoritative = service._read_staging_unlocked()
            if authoritative is None:
                raise ValueError("authoritative_staging_missing")
            if not authoritative.is_v2 or authoritative.stage_digest is None:
                raise ValueError("authoritative_staging_protocol_v2_required")
            if authoritative.transaction_hash != transaction_hash:
                raise ValueError("authoritative_staging_transaction_mismatch")
            if authoritative.decision_hashes != decision_hashes:
                raise ValueError("authoritative_staging_decision_set_mismatch")
            if (
                authoritative.lineage_decision_hashes
                != lineage_decision_hashes
            ):
                raise ValueError("authoritative_staging_lineage_set_mismatch")
            if (
                envelope.prepared_transaction.entity_registry_digest
                == "0" * 64
            ):
                raise ValueError("unbound_entity_registry_forbidden")
            if expected_head is None:
                raise ValueError("missing_expected_head")
            service._assert_chapter_sequence(expected_head, envelope.chapter)
            service._assert_active_chapter_bindings(
                before_chapter=envelope.chapter
            )
            pointer = StagingPointer(
                transaction_hash=transaction_hash,
                decision_hashes=decision_hashes,
                lineage_decision_hashes=lineage_decision_hashes,
            )
            reduction = service._validated_reduction(pointer, envelope)
            if reduction.snapshot.state is not WorkflowState.READY:
                raise ValueError(
                    f"review_state_{reduction.snapshot.state.value}"
                )
            active = {
                record.candidate_digest for record in reduction.active_candidates
            }
            expected_effects = [
                effect.model_dump(mode="json")
                for effect in envelope.prepared_transaction.effects
                if effect.candidate_digest in active
            ]
            if effects != expected_effects:
                raise ValueError("canon_effects_mismatch")
        except Exception as exc:
            raise CanonRepositoryError(
                f"canon_v3_publication_proof_invalid:{exc}"
            ) from exc

    def _seal_objects(
        self,
        *,
        chapter: int,
        transaction: Mapping[str, Any] | str,
        expected_head: str | None,
        decisions: Sequence[Mapping[str, Any] | str] = (),
        lineage_decisions: Sequence[Mapping[str, Any] | str] = (),
        canon_effects: Sequence[Mapping[str, Any]] | None = None,
        fault_injector: FaultInjector | None = None,
        _strict_publication: bool = False,
    ) -> SealResult:
        """Low-level object-store primitive used by repository fault tests.

        Production callers must use :meth:`seal`, which enables the compiled
        publication proof. This private primitive exists so storage fault
        injection can be tested without constructing the whole domain model.

        Seal a chapter revision and atomically compare-and-swap Canon HEAD.

        Replacing an existing or earlier chapter creates a new immutable commit
        and a new manifest containing only the prefix before that chapter plus
        the replacement.  The previous manifest and its suffix remain readable
        but are no longer canon.
        """
        try:
            chapter_number = int(chapter)
        except (TypeError, ValueError) as exc:
            raise CanonRepositoryError(f"canon_v3_invalid_chapter: {chapter!r}") from exc
        if chapter_number <= 0:
            raise CanonRepositoryError(f"canon_v3_invalid_chapter: {chapter!r}")
        expected = (
            _validate_hash(expected_head, label="expected_head")
            if expected_head is not None
            else None
        )

        with self.locked():
            actual = self._read_current_hash_unvalidated()
            if actual != expected:
                raise CanonHeadConflict(expected=expected, actual=actual)
            current_manifest = (
                self.read_manifest(actual, validate_references=True)
                if actual is not None
                else None
            )
            current_entries = self._manifest_entries(current_manifest)

            transaction_hash, transaction_payload = self._resolve_transaction_unlocked(
                transaction
            )
            transaction_chapter = self._chapter_from_payload(transaction_payload)
            if transaction_chapter is not None and transaction_chapter != chapter_number:
                raise CanonRepositoryError(
                    "canon_v3_transaction_chapter_mismatch: "
                    f"expected={chapter_number}, actual={transaction_chapter}"
                )
            self._inject(fault_injector, "after_transaction")

            decision_hashes = self._resolve_decisions_unlocked(
                decisions,
                transaction_hash=transaction_hash,
                chapter=chapter_number,
            )
            lineage_decision_hashes = self._resolve_lineage_decisions_unlocked(
                lineage_decisions,
                chapter=chapter_number,
            )
            self._inject(fault_injector, "after_decisions")

            if canon_effects is None:
                candidate_effects = transaction_payload.get("canon_effects", [])
                if not isinstance(candidate_effects, list):
                    raise CanonRepositoryError("canon_v3_transaction_effects_not_list")
            else:
                candidate_effects = canon_effects
            effects = self._normalize_effects(candidate_effects)
            if _strict_publication:
                self._validate_compiled_publication_unlocked(
                    chapter=chapter_number,
                    transaction_hash=transaction_hash,
                    transaction_payload=transaction_payload,
                    decision_hashes=decision_hashes,
                    lineage_decision_hashes=lineage_decision_hashes,
                    effects=effects,
                    expected_head=expected,
                )

            existing_entry = self._entry_for_chapter(current_entries, chapter_number)
            existing_commit: dict[str, Any] | None = None
            if existing_entry is not None:
                existing_commit = self.read_commit(str(existing_entry.get("commit_hash") or ""))
                if (
                    existing_commit.get("transaction_hash") == transaction_hash
                    and tuple(existing_commit.get("decision_hashes") or ()) == decision_hashes
                    and tuple(
                        existing_commit.get("lineage_decision_hashes") or ()
                    )
                    == lineage_decision_hashes
                    and existing_commit.get("canon_effects") == effects
                ):
                    return SealResult(
                        transaction_hash=transaction_hash,
                        decision_hashes=decision_hashes,
                        commit_hash=str(existing_entry["commit_hash"]),
                        manifest_hash=str(actual),
                        chapter=chapter_number,
                        revision=int(existing_commit.get("revision") or 1),
                        generation=int((current_manifest or {}).get("generation") or 0),
                        created=False,
                    )

            if current_entries:
                last_chapter = int(current_entries[-1].get("chapter") or 0)
                if chapter_number > last_chapter + 1:
                    raise CanonChapterSequenceError(
                        "canon_v3_chapter_gap: "
                        f"last={last_chapter}, requested={chapter_number}"
                    )

            prefix_entries = [
                copy.deepcopy(entry)
                for entry in current_entries
                if int(entry.get("chapter") or 0) < chapter_number
            ]
            predecessor_commit_hash = (
                str(prefix_entries[-1].get("commit_hash") or "")
                if prefix_entries
                else None
            )
            revision = (
                int((existing_commit or {}).get("revision") or 0) + 1
                if existing_commit is not None
                else 1
            )
            commit_payload = {
                "schema_version": f"{V3_SCHEMA_PREFIX}/chapter-commit/v1",
                "chapter": chapter_number,
                "revision": revision,
                "transaction_hash": transaction_hash,
                "decision_hashes": list(decision_hashes),
                "lineage_decision_hashes": list(lineage_decision_hashes),
                "base_head_hash": actual,
                "predecessor_commit_hash": predecessor_commit_hash,
                "canon_effects": effects,
            }
            commit_hash = self._put_payload_unlocked("commit", commit_payload)
            self._inject(fault_injector, "after_commit")

            next_entries = [
                *prefix_entries,
                {
                    "chapter": chapter_number,
                    "revision": revision,
                    "commit_hash": commit_hash,
                },
            ]
            generation = int((current_manifest or {}).get("generation") or 0) + 1
            manifest_payload = {
                "schema_version": f"{V3_SCHEMA_PREFIX}/active-manifest/v1",
                "generation": generation,
                "parent_head_hash": actual,
                "chapters": next_entries,
            }
            author_axiom_entries = self._author_axiom_manifest_entries(
                current_manifest
            )
            if author_axiom_entries:
                manifest_payload["author_axiom_commits"] = (
                    author_axiom_entries
                )
            manifest_hash = self._put_payload_unlocked("manifest", manifest_payload)
            self._inject(fault_injector, "after_manifest")
            self._inject(fault_injector, "before_head_swap")
            self._write_current_unlocked(manifest_hash)
            self._inject(fault_injector, "after_head_swap")
            return SealResult(
                transaction_hash=transaction_hash,
                decision_hashes=decision_hashes,
                commit_hash=commit_hash,
                manifest_hash=manifest_hash,
                chapter=chapter_number,
                revision=revision,
                generation=generation,
                created=True,
            )

    def _validate_commit_payload(self, payload: Mapping[str, Any]) -> None:
        if payload.get("schema_version") != f"{V3_SCHEMA_PREFIX}/chapter-commit/v1":
            raise CanonIntegrityError("canon_v3_commit_schema_mismatch")
        try:
            chapter = int(payload.get("chapter") or 0)
            revision = int(payload.get("revision") or 0)
        except (TypeError, ValueError) as exc:
            raise CanonIntegrityError("canon_v3_commit_number_invalid") from exc
        if chapter <= 0 or revision <= 0:
            raise CanonIntegrityError("canon_v3_commit_number_invalid")
        transaction_hash = _validate_hash(
            str(payload.get("transaction_hash") or ""),
            label="transaction_hash",
        )
        transaction = self.read_transaction(transaction_hash)
        recertified_wrapper = self.recertified_suffix_wrapper(transaction_hash)
        transaction_chapter = self._chapter_from_payload(transaction)
        if transaction_chapter is not None and transaction_chapter != chapter:
            raise CanonIntegrityError("canon_v3_commit_transaction_chapter_mismatch")
        decision_hashes = payload.get("decision_hashes")
        if not isinstance(decision_hashes, list):
            raise CanonIntegrityError("canon_v3_commit_decisions_not_list")
        normalized_decisions = [
            _validate_hash(str(item), label="decision_hash") for item in decision_hashes
        ]
        if normalized_decisions != sorted(set(normalized_decisions)):
            raise CanonIntegrityError("canon_v3_commit_decisions_not_canonical")
        for decision_hash in normalized_decisions:
            decision = self.read_decision(decision_hash)
            bound_transaction = str(decision.get("transaction_hash") or "").strip()
            if bound_transaction and bound_transaction != transaction_hash:
                raise CanonIntegrityError("canon_v3_commit_decision_transaction_mismatch")
            decision_chapter = self._chapter_from_payload(decision)
            if decision_chapter is not None and decision_chapter != chapter:
                raise CanonIntegrityError("canon_v3_commit_decision_chapter_mismatch")
        lineage_hashes = payload.get("lineage_decision_hashes", [])
        if not isinstance(lineage_hashes, list):
            raise CanonIntegrityError("canon_v3_commit_lineage_not_list")
        normalized_lineage = [
            _validate_hash(str(item), label="lineage_decision_hash")
            for item in lineage_hashes
        ]
        if normalized_lineage != sorted(set(normalized_lineage)):
            raise CanonIntegrityError("canon_v3_commit_lineage_not_canonical")
        for decision_hash in normalized_lineage:
            decision = self.read_decision(decision_hash)
            decision_chapter = self._chapter_from_payload(decision)
            if decision_chapter is not None and decision_chapter != chapter:
                raise CanonIntegrityError(
                    "canon_v3_commit_lineage_chapter_mismatch"
                )
        base_head = payload.get("base_head_hash")
        if base_head is not None:
            _validate_hash(str(base_head), label="base_head_hash")
        predecessor = payload.get("predecessor_commit_hash")
        if predecessor is not None:
            _validate_hash(str(predecessor), label="predecessor_commit_hash")
        effects = payload.get("canon_effects")
        if not isinstance(effects, list) or any(
            not isinstance(effect, dict) for effect in effects
        ):
            raise CanonIntegrityError("canon_v3_commit_effects_not_mapping_list")
        if recertified_wrapper is not None:
            envelope = recertified_wrapper["recertified_envelope"]
            prepared = envelope.get("prepared_transaction")
            expected_effects = (
                prepared.get("effects") if isinstance(prepared, Mapping) else None
            )
            required_lineage = set(
                recertified_wrapper.get("semantic_negative_lineage_hashes") or ()
            )
            if (
                payload.get("base_head_hash")
                != recertified_wrapper.get("parent_head")
                or decision_hashes
                or effects != expected_effects
                or not required_lineage.issubset(set(normalized_lineage))
            ):
                raise CanonIntegrityError(
                    "canon_v3_recertified_suffix_commit_binding_invalid"
                )

    def _resolve_author_axiom_decisions_unlocked(
        self,
        decisions: Sequence[Mapping[str, Any] | str],
        *,
        transaction_hash: str,
        require_transaction_match: bool,
    ) -> tuple[str, ...]:
        hashes: set[str] = set()
        for decision in decisions:
            if isinstance(decision, str):
                decision_hash = _validate_hash(
                    decision, label="author_axiom_decision_hash"
                )
                payload = self.read_author_axiom_decision(decision_hash)
            elif isinstance(decision, Mapping):
                payload = _deep_json_copy(dict(decision))
                decision_hash = self._put_payload_unlocked(
                    "author_axiom_decision", payload
                )
            else:
                raise CanonRepositoryError(
                    "canon_v3_author_axiom_decision_not_mapping_or_hash"
                )
            bound = str(payload.get("transaction_hash") or "")
            if require_transaction_match and bound != transaction_hash:
                raise CanonRepositoryError(
                    "canon_v3_author_axiom_decision_transaction_mismatch"
                )
            hashes.add(decision_hash)
        return tuple(sorted(hashes))

    def seal_author_axiom(
        self,
        *,
        transaction: str,
        expected_head: str,
        decisions: Sequence[str],
        lineage_decisions: Sequence[str],
        records: Sequence[Mapping[str, Any]],
        axiom_set_digest: str,
        superseded_legacy_admission_digests: Sequence[str],
        expected_stage_digest: str,
        finalize_token: str,
        fault_injector: FaultInjector | None = None,
    ) -> AuthorAxiomSealResult:
        """Atomically publish a reviewed axiom snapshot without a chapter."""

        transaction_hash = _validate_hash(
            transaction, label="author_axiom_transaction_hash"
        )
        expected = _validate_hash(expected_head, label="expected_head")
        normalized_set_digest = _validate_hash(
            axiom_set_digest, label="author_axiom_set_digest"
        )
        normalized_stage_digest = _validate_hash(
            expected_stage_digest, label="author_axiom_stage_digest"
        )
        normalized_finalize_token = _validate_hash(
            finalize_token, label="author_axiom_finalize_token"
        )
        normalized_records = [
            _deep_json_copy(dict(item)) for item in records
        ]
        if len(normalized_records) != len(records):
            raise CanonRepositoryError("canon_v3_author_axiom_records_invalid")
        requested_decision_hashes = tuple(
            sorted(
                {
                    _validate_hash(
                        str(item), label="author_axiom_decision_hash"
                    )
                    for item in decisions
                }
            )
        )
        requested_lineage_hashes = tuple(
            sorted(
                {
                    _validate_hash(
                        str(item), label="author_axiom_lineage_decision_hash"
                    )
                    for item in lineage_decisions
                }
            )
        )
        requested_superseded_admissions = tuple(
            sorted(
                {
                    _validate_hash(
                        str(item), label="legacy_admission_digest"
                    )
                    for item in superseded_legacy_admission_digests
                }
            )
        )

        with self.locked():
            actual = self._read_current_hash_unvalidated()
            if actual != expected:
                if actual is not None:
                    current_manifest = self.read_manifest(
                        actual, validate_references=True
                    )
                    current_entries = self._author_axiom_manifest_entries(
                        current_manifest
                    )
                    if current_entries:
                        current_commit_hash = str(
                            current_entries[-1].get("commit_hash") or ""
                        )
                        current_commit = self.read_author_axiom_commit(
                            current_commit_hash
                        )
                        exact_retry = (
                            current_commit.get("base_head_hash") == expected
                            and current_commit.get("transaction_hash")
                            == transaction_hash
                            and tuple(
                                current_commit.get("decision_hashes") or ()
                            )
                            == requested_decision_hashes
                            and tuple(
                                current_commit.get(
                                    "lineage_decision_hashes"
                                )
                                or ()
                            )
                            == requested_lineage_hashes
                            and current_commit.get("records")
                            == normalized_records
                            and current_commit.get("axiom_set_digest")
                            == normalized_set_digest
                            and tuple(
                                current_commit.get(
                                    "superseded_legacy_admission_digests"
                                )
                                or ()
                            )
                            == requested_superseded_admissions
                        )
                        if exact_retry:
                            from .author_axiom import validate_publication_proof

                            validate_publication_proof(
                                project_root=self.project_root,
                                repository=self,
                                transaction_hash=transaction_hash,
                                transaction_payload=(
                                    self.read_author_axiom_transaction(
                                        transaction_hash
                                    )
                                ),
                                decision_hashes=requested_decision_hashes,
                                lineage_decision_hashes=(
                                    requested_lineage_hashes
                                ),
                                records=normalized_records,
                                axiom_set_digest=normalized_set_digest,
                                superseded_legacy_admission_digests=(
                                    requested_superseded_admissions
                                ),
                                expected_head=expected,
                                expected_stage_digest=(
                                    normalized_stage_digest
                                ),
                                finalize_token=normalized_finalize_token,
                                verify_draft_sources=False,
                            )
                            return AuthorAxiomSealResult(
                                transaction_hash=transaction_hash,
                                decision_hashes=requested_decision_hashes,
                                commit_hash=current_commit_hash,
                                manifest_hash=actual,
                                revision=int(
                                    current_commit.get("revision") or 0
                                ),
                                generation=int(
                                    current_manifest.get("generation") or 0
                                ),
                                created=False,
                            )
                raise CanonHeadConflict(expected=expected, actual=actual)
            if actual is None:
                raise CanonRepositoryError(
                    "canon_v3_author_axiom_initialize_required"
                )
            manifest = self.read_manifest(actual, validate_references=True)
            transaction_payload = self.read_author_axiom_transaction(
                transaction_hash
            )
            decision_hashes = self._resolve_author_axiom_decisions_unlocked(
                decisions,
                transaction_hash=transaction_hash,
                require_transaction_match=True,
            )
            lineage_hashes = self._resolve_author_axiom_decisions_unlocked(
                lineage_decisions,
                transaction_hash=transaction_hash,
                require_transaction_match=False,
            )
            if decision_hashes != requested_decision_hashes:
                raise CanonRepositoryError(
                    "canon_v3_author_axiom_decision_set_not_canonical"
                )
            if lineage_hashes != requested_lineage_hashes:
                raise CanonRepositoryError(
                    "canon_v3_author_axiom_lineage_set_not_canonical"
                )
            try:
                from .author_axiom import validate_publication_proof

                validate_publication_proof(
                    project_root=self.project_root,
                    repository=self,
                    transaction_hash=transaction_hash,
                    transaction_payload=transaction_payload,
                    decision_hashes=decision_hashes,
                    lineage_decision_hashes=lineage_hashes,
                    records=normalized_records,
                    axiom_set_digest=normalized_set_digest,
                    superseded_legacy_admission_digests=(
                        requested_superseded_admissions
                    ),
                    expected_head=expected,
                    expected_stage_digest=normalized_stage_digest,
                    finalize_token=normalized_finalize_token,
                )
            except Exception as exc:
                raise CanonRepositoryError(
                    f"canon_v3_author_axiom_publication_proof_invalid:{exc}"
                ) from exc

            entries = self._author_axiom_manifest_entries(manifest)
            previous_commit_hash = (
                str(entries[-1].get("commit_hash") or "")
                if entries
                else None
            )
            revision = len(entries) + 1
            commit_payload = {
                "schema_version": "canon-v3/author-axiom-commit/v1",
                "revision": revision,
                "transaction_hash": transaction_hash,
                "decision_hashes": list(decision_hashes),
                "lineage_decision_hashes": list(lineage_hashes),
                "base_head_hash": actual,
                "previous_author_axiom_commit_hash": previous_commit_hash,
                "records": normalized_records,
                "axiom_set_digest": normalized_set_digest,
                "superseded_legacy_admission_digests": list(
                    requested_superseded_admissions
                ),
            }
            commit_hash = self._put_payload_unlocked(
                "author_axiom_commit", commit_payload
            )
            self._inject(fault_injector, "after_author_axiom_commit")
            next_axiom_entries = [
                *entries,
                {"revision": revision, "commit_hash": commit_hash},
            ]
            generation = int(manifest.get("generation") or 0) + 1
            manifest_payload: dict[str, Any] = {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": generation,
                "parent_head_hash": actual,
                "chapters": self._manifest_entries(manifest),
                "author_axiom_commits": next_axiom_entries,
            }
            manifest_hash = self._put_payload_unlocked(
                "manifest", manifest_payload
            )
            self._inject(fault_injector, "after_manifest")
            self._inject(fault_injector, "before_head_swap")
            self._write_current_unlocked(manifest_hash)
            self._inject(fault_injector, "after_head_swap")
            return AuthorAxiomSealResult(
                transaction_hash=transaction_hash,
                decision_hashes=decision_hashes,
                commit_hash=commit_hash,
                manifest_hash=manifest_hash,
                revision=revision,
                generation=generation,
                created=True,
            )

    def _validate_author_axiom_commit_payload(
        self, payload: Mapping[str, Any]
    ) -> None:
        if payload.get("schema_version") != "canon-v3/author-axiom-commit/v1":
            raise CanonIntegrityError(
                "canon_v3_author_axiom_commit_schema_mismatch"
            )
        try:
            revision = int(payload.get("revision") or 0)
        except (TypeError, ValueError) as exc:
            raise CanonIntegrityError(
                "canon_v3_author_axiom_commit_revision_invalid"
            ) from exc
        if revision <= 0:
            raise CanonIntegrityError(
                "canon_v3_author_axiom_commit_revision_invalid"
            )
        transaction_hash = _validate_hash(
            str(payload.get("transaction_hash") or ""),
            label="author_axiom_transaction_hash",
        )
        self.read_author_axiom_transaction(transaction_hash)
        for field in ("decision_hashes", "lineage_decision_hashes"):
            values = payload.get(field)
            if not isinstance(values, list):
                raise CanonIntegrityError(
                    f"canon_v3_author_axiom_commit_{field}_invalid"
                )
            normalized = [
                _validate_hash(str(item), label="author_axiom_decision_hash")
                for item in values
            ]
            if normalized != sorted(set(normalized)):
                raise CanonIntegrityError(
                    f"canon_v3_author_axiom_commit_{field}_not_canonical"
                )
            for decision_hash in normalized:
                self.read_author_axiom_decision(decision_hash)
        _validate_hash(
            str(payload.get("base_head_hash") or ""),
            label="base_head_hash",
        )
        previous = payload.get("previous_author_axiom_commit_hash")
        if previous is not None:
            _validate_hash(
                str(previous), label="previous_author_axiom_commit_hash"
            )
        records = payload.get("records")
        if not isinstance(records, list) or any(
            not isinstance(item, dict) for item in records
        ):
            raise CanonIntegrityError(
                "canon_v3_author_axiom_commit_records_invalid"
            )
        _validate_hash(
            str(payload.get("axiom_set_digest") or ""),
            label="author_axiom_set_digest",
        )
        superseded = payload.get("superseded_legacy_admission_digests", [])
        if not isinstance(superseded, list):
            raise CanonIntegrityError(
                "canon_v3_author_axiom_superseded_admissions_invalid"
            )
        normalized_superseded = [
            _validate_hash(str(item), label="legacy_admission_digest")
            for item in superseded
        ]
        if normalized_superseded != sorted(set(normalized_superseded)):
            raise CanonIntegrityError(
                "canon_v3_author_axiom_superseded_admissions_not_canonical"
            )

    def _validate_manifest_payload(
        self,
        payload: Mapping[str, Any],
        *,
        validate_references: bool,
    ) -> None:
        if payload.get("schema_version") != f"{V3_SCHEMA_PREFIX}/active-manifest/v1":
            raise CanonIntegrityError("canon_v3_manifest_schema_mismatch")
        try:
            generation = int(payload.get("generation") or 0)
        except (TypeError, ValueError) as exc:
            raise CanonIntegrityError("canon_v3_manifest_generation_invalid") from exc
        if generation < 0:
            raise CanonIntegrityError("canon_v3_manifest_generation_invalid")
        parent = payload.get("parent_head_hash")
        if parent is not None:
            parent = _validate_hash(str(parent), label="parent_head_hash")
        entries = payload.get("chapters")
        if not isinstance(entries, list):
            raise CanonIntegrityError("canon_v3_manifest_chapters_invalid")
        if generation == 0:
            if entries or parent is not None:
                raise CanonIntegrityError("canon_v3_genesis_manifest_invalid")
            metadata = payload.get("genesis_metadata", {})
            if not isinstance(metadata, dict):
                raise CanonIntegrityError("canon_v3_genesis_metadata_invalid")
            return
        axiom_entries = self._author_axiom_manifest_entries(payload)
        if not entries and not axiom_entries:
            raise CanonIntegrityError("canon_v3_manifest_has_no_publication")
        parent_entries: list[dict[str, Any]] = []
        parent_axiom_entries: list[dict[str, Any]] = []
        if parent is None:
            raise CanonIntegrityError("canon_v3_manifest_missing_parent")
        if validate_references:
            parent_payload = self.read_object("manifest", parent)
            self._validate_manifest_payload(
                parent_payload,
                validate_references=False,
            )
            parent_generation = int(parent_payload.get("generation") or 0)
            if generation != parent_generation + 1:
                raise CanonIntegrityError("canon_v3_manifest_generation_not_monotonic")
            parent_entries = self._manifest_entries(parent_payload)
            parent_axiom_entries = self._author_axiom_manifest_entries(
                parent_payload
            )
        previous_chapter: int | None = None
        previous_commit_hash: str | None = None
        seen_commits: set[str] = set()
        resolved_commits: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise CanonIntegrityError("canon_v3_manifest_entry_not_mapping")
            try:
                chapter = int(entry.get("chapter") or 0)
                revision = int(entry.get("revision") or 0)
            except (TypeError, ValueError) as exc:
                raise CanonIntegrityError("canon_v3_manifest_entry_number_invalid") from exc
            if chapter <= 0 or revision <= 0:
                raise CanonIntegrityError("canon_v3_manifest_entry_number_invalid")
            if previous_chapter is not None and chapter != previous_chapter + 1:
                raise CanonIntegrityError("canon_v3_manifest_chapters_not_contiguous")
            previous_chapter = chapter
            commit_hash = _validate_hash(
                str(entry.get("commit_hash") or ""),
                label="commit_hash",
            )
            if commit_hash in seen_commits:
                raise CanonIntegrityError("canon_v3_manifest_duplicate_commit")
            seen_commits.add(commit_hash)
            if validate_references:
                commit = self.read_commit(commit_hash)
                if int(commit.get("chapter") or 0) != chapter:
                    raise CanonIntegrityError("canon_v3_manifest_commit_chapter_mismatch")
                if int(commit.get("revision") or 0) != revision:
                    raise CanonIntegrityError("canon_v3_manifest_commit_revision_mismatch")
                if commit.get("predecessor_commit_hash") != previous_commit_hash:
                    raise CanonIntegrityError("canon_v3_manifest_predecessor_mismatch")
                resolved_commits.append(commit)
            previous_commit_hash = commit_hash
        previous_axiom_commit_hash: str | None = None
        resolved_axiom_commits: list[dict[str, Any]] = []
        for index, entry in enumerate(axiom_entries, start=1):
            if set(entry) != {"revision", "commit_hash"}:
                raise CanonIntegrityError(
                    "canon_v3_manifest_author_axiom_entry_fields_invalid"
                )
            try:
                revision = int(entry.get("revision") or 0)
            except (TypeError, ValueError) as exc:
                raise CanonIntegrityError(
                    "canon_v3_manifest_author_axiom_revision_invalid"
                ) from exc
            if revision != index:
                raise CanonIntegrityError(
                    "canon_v3_manifest_author_axiom_revision_not_contiguous"
                )
            commit_hash = _validate_hash(
                str(entry.get("commit_hash") or ""),
                label="author_axiom_commit_hash",
            )
            if validate_references:
                commit = self.read_author_axiom_commit(commit_hash)
                if int(commit.get("revision") or 0) != revision:
                    raise CanonIntegrityError(
                        "canon_v3_manifest_author_axiom_revision_mismatch"
                    )
                if (
                    commit.get("previous_author_axiom_commit_hash")
                    != previous_axiom_commit_hash
                ):
                    raise CanonIntegrityError(
                        "canon_v3_manifest_author_axiom_predecessor_mismatch"
                    )
                resolved_axiom_commits.append(commit)
            previous_axiom_commit_hash = commit_hash

        if validate_references:
            chapter_changed = entries != parent_entries
            axiom_changed = axiom_entries != parent_axiom_entries
            if chapter_changed == axiom_changed:
                raise CanonIntegrityError(
                    "canon_v3_manifest_must_publish_exactly_one_channel"
                )
            if chapter_changed:
                if not entries:
                    raise CanonIntegrityError(
                        "canon_v3_manifest_chapters_empty"
                    )
                replacement_chapter = int(
                    entries[-1].get("chapter") or 0
                )
                expected_prefix = [
                    item
                    for item in parent_entries
                    if int(item.get("chapter") or 0) < replacement_chapter
                ]
                if entries[:-1] != expected_prefix:
                    raise CanonIntegrityError(
                        "canon_v3_manifest_parent_prefix_mismatch"
                    )
                if (
                    not resolved_commits
                    or resolved_commits[-1].get("base_head_hash") != parent
                ):
                    raise CanonIntegrityError(
                        "canon_v3_manifest_commit_base_head_mismatch"
                    )
            else:
                if axiom_entries[:-1] != parent_axiom_entries:
                    raise CanonIntegrityError(
                        "canon_v3_manifest_author_axiom_parent_prefix_mismatch"
                    )
                if (
                    len(axiom_entries) != len(parent_axiom_entries) + 1
                    or not resolved_axiom_commits
                    or resolved_axiom_commits[-1].get("base_head_hash")
                    != parent
                ):
                    raise CanonIntegrityError(
                        "canon_v3_manifest_author_axiom_base_head_mismatch"
                    )

    def current_commits(self) -> list[tuple[str, dict[str, Any]]]:
        manifest = self.current_manifest()
        if manifest is None:
            return []
        result: list[tuple[str, dict[str, Any]]] = []
        for entry in self._manifest_entries(manifest):
            commit_hash = str(entry["commit_hash"])
            result.append((commit_hash, self.read_commit(commit_hash)))
        return result

    def current_author_axiom_commits(
        self,
    ) -> list[tuple[str, dict[str, Any]]]:
        manifest = self.current_manifest()
        if manifest is None:
            return []
        result: list[tuple[str, dict[str, Any]]] = []
        for entry in self._author_axiom_manifest_entries(manifest):
            commit_hash = str(entry["commit_hash"])
            result.append(
                (commit_hash, self.read_author_axiom_commit(commit_hash))
            )
        return result

    def is_commit_canonical(self, commit_hash: str) -> bool:
        normalized = _validate_hash(commit_hash, label="commit_hash")
        manifest = self.current_manifest()
        if manifest is None:
            return False
        return any(
            str(entry.get("commit_hash") or "") == normalized
            for entry in self._manifest_entries(manifest)
        )

    def projection_binding(self) -> ProjectionBinding:
        head = self.current_head(validate=False)
        if head is None:
            return ProjectionBinding(generation=0, head_hash=None)
        manifest = self.read_manifest(head, validate_references=True)
        return ProjectionBinding(
            generation=int(manifest.get("generation") or 0),
            head_hash=head,
        )

    def projection_is_stale(
        self,
        binding: ProjectionBinding | Mapping[str, Any] | None,
    ) -> bool:
        current = self.projection_binding()
        if binding is None:
            return current.head_hash is not None
        if isinstance(binding, ProjectionBinding):
            supplied_generation = binding.generation
            supplied_head = binding.head_hash
        elif isinstance(binding, Mapping):
            try:
                supplied_generation = int(binding.get("generation") or 0)
            except (TypeError, ValueError):
                return True
            raw_head = binding.get("head_hash")
            supplied_head = str(raw_head).strip() if raw_head is not None else None
        else:
            return True
        return (
            supplied_generation != current.generation
            or supplied_head != current.head_hash
        )

    def assert_projection_fresh(
        self,
        binding: ProjectionBinding | Mapping[str, Any] | None,
    ) -> None:
        if self.projection_is_stale(binding):
            current = self.projection_binding()
            raise ProjectionStaleError(
                "canon_v3_projection_stale: "
                f"expected_generation={current.generation}, "
                f"expected_head={current.head_hash or '<empty>'}"
            )


# Short alias for callers that do not need the version in the type name.
CanonRepository = CanonV3Repository


__all__ = [
    "AUTHOR_AXIOM_COMMIT_SCHEMA",
    "AUTHOR_AXIOM_DECISION_SCHEMA",
    "AUTHOR_AXIOM_TRANSACTION_SCHEMA",
    "AuthorAxiomSealResult",
    "CURRENT_FILE",
    "V3_RELATIVE_ROOT",
    "CanonChapterSequenceError",
    "CanonHeadConflict",
    "CanonIntegrityError",
    "CanonRepository",
    "CanonRepositoryError",
    "CanonV3Repository",
    "ProjectionBinding",
    "ProjectionStaleError",
    "RECERTIFIED_SUFFIX_TRANSACTION_SCHEMA",
    "SealResult",
    "canonical_json_bytes",
    "content_hash",
]

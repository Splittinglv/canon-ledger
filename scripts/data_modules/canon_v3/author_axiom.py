#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent, exact-human Canon v3 author-axiom publication channel.

Draft files are evidence, never authority.  Only the latest immutable axiom
commit reachable from CURRENT contributes active author axioms.  The channel
shares the chapter staging lock and repository HEAD CAS, while publishing no
chapter entry.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from security_utils import atomic_write_json, resolve_inside_project
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json, resolve_inside_project

from .projection import projection_is_fresh, rebuild_projection
from .repository import (
    CanonHeadConflict,
    CanonV3Repository,
    content_hash,
)
from .schema import (
    AuthorAxiomDraftSpanSource,
    AuthorAxiomRecord,
    AuthorAxiomSource,
    canonical_digest,
)
from .source_verifier import resolve_json_pointer
from .staging_authority import (
    AUTHORITATIVE_STAGING_LOCK_RELATIVE_PATH,
    AUTHOR_AXIOM_STAGING_RELATIVE_PATH,
    CHAPTER_STAGING_RELATIVE_PATH,
    assert_single_authoritative_staging,
)


DRAFT_SCHEMA = "canon-v3/author-axiom-draft/v1"
PROPOSAL_SCHEMA = "canon-v3/author-axiom-proposal/v2"
PREPARED_SCHEMA = "canon-v3/author-axiom-prepared-envelope/v2"
STAGING_SCHEMA = "canon-v3/author-axiom-staging-pointer/v2"
DECISION_OBJECT_SCHEMA = "canon-v3/author-axiom-decision/v2"
DECISION_REQUEST_SCHEMA = "canon-v3/author-axiom-decision-request/v2"
FINALIZE_REQUEST_SCHEMA = "canon-v3/author-axiom-finalize-request/v2"
FINALIZE_TOKEN_SCHEMA = "canon-v3/author-axiom-finalize-token/v2"
WORKFLOW_SCHEMA = "canon-v3/author-axiom-workflow/v2"
ACTIVE_SET_SCHEMA = "canon-v3/author-axiom-set/v2"
RECORD_SET_SCHEMA = "canon-v3/author-axiom-record-set/v1"
REVIEW_MATERIAL_SCHEMA = "canon-v3/author-axiom-review-material/v2"

_BANNED_NON_FACT_KEY_PARTS = (
    "style",
    "outline",
    "plot",
    "prose",
    "tone",
    "voice",
    "pacing",
    "preference",
    "文风",
    "文笔",
    "大纲",
    "章纲",
    "节奏",
    "口吻",
    "写作偏好",
)


class AuthorAxiomChannelError(RuntimeError):
    pass


class AuthorAxiomStageConflict(AuthorAxiomChannelError):
    pass


class AuthorAxiomEvidenceError(AuthorAxiomChannelError):
    pass


class AuthorAxiomDecisionError(AuthorAxiomChannelError):
    pass


class AuthorAxiomFinalizeBlocked(AuthorAxiomChannelError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorAxiomProposal(_StrictModel):
    schema_version: Literal[PROPOSAL_SCHEMA] = PROPOSAL_SCHEMA
    parent_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_author_axiom_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Required but nullable: omission is not proof that no stage was observed.
    expected_stage_digest: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    records: tuple[AuthorAxiomRecord, ...] = ()
    genesis_overrides: tuple["GenesisAdmissionOverride", ...] = ()

    @model_validator(mode="after")
    def records_are_unique(self) -> "AuthorAxiomProposal":
        keys = tuple(item.axiom_key for item in self.records)
        if len(keys) != len(set(keys)):
            raise ValueError("author axiom proposal keys must be unique")
        source_ids = tuple(item.source.source_id for item in self.records)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("author axiom proposal source_id values must be unique")
        admissions = tuple(
            item.admission_digest for item in self.genesis_overrides
        )
        if len(admissions) != len(set(admissions)):
            raise ValueError("genesis override admission digests must be unique")
        return self


class GenesisAdmissionOverride(_StrictModel):
    """Explicit request to supersede one certified genesis axiom fact."""

    admission_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replacement_axiom_key: str | None = None


class AuthorAxiomOperation(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"
    SUPERSEDE_GENESIS = "supersede_genesis"


class AuthorAxiomCase(_StrictModel):
    case_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    axiom_key: str
    operation: AuthorAxiomOperation
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_record: AuthorAxiomRecord | None = None
    proposed_record: AuthorAxiomRecord | None = None
    prior_genesis_admission: dict[str, Any] | None = None

    @model_validator(mode="after")
    def operation_shape_is_closed(self) -> "AuthorAxiomCase":
        if self.operation is AuthorAxiomOperation.ADD:
            if self.prior_record is not None or self.proposed_record is None:
                raise ValueError("add case shape invalid")
        elif self.operation is AuthorAxiomOperation.UPDATE:
            if self.prior_record is None or self.proposed_record is None:
                raise ValueError("update case shape invalid")
        elif self.operation is AuthorAxiomOperation.REMOVE:
            if self.prior_record is None or self.proposed_record is not None:
                raise ValueError("remove case shape invalid")
        elif (
            self.prior_record is not None
            or self.prior_genesis_admission is None
        ):
            raise ValueError("genesis supersession case shape invalid")
        return self


class AuthorAxiomPreparedEnvelope(_StrictModel):
    schema_version: Literal[PREPARED_SCHEMA] = PREPARED_SCHEMA
    parent_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_author_axiom_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_records: tuple[AuthorAxiomRecord, ...]
    desired_records: tuple[AuthorAxiomRecord, ...]
    prior_superseded_genesis_admission_digests: tuple[str, ...] = ()
    cases: tuple[AuthorAxiomCase, ...] = Field(min_length=1)


class AuthorAxiomStagingPointer(_StrictModel):
    schema_version: Literal[STAGING_SCHEMA] = STAGING_SCHEMA
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_hashes: tuple[str, ...] = ()
    lineage_decision_hashes: tuple[str, ...] = ()
    stage_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def close_digest(self) -> "AuthorAxiomStagingPointer":
        for field_name in ("decision_hashes", "lineage_decision_hashes"):
            values = getattr(self, field_name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must be sorted and unique")
        expected = canonical_digest(self.digest_payload())
        if self.stage_digest is None:
            object.__setattr__(self, "stage_digest", expected)
        elif self.stage_digest != expected:
            raise ValueError("author axiom stage_digest mismatch")
        return self

    def digest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": STAGING_SCHEMA,
            "transaction_hash": self.transaction_hash,
            "decision_hashes": list(self.decision_hashes),
            "lineage_decision_hashes": list(self.lineage_decision_hashes),
        }


class AuthorAxiomDecisionAction(str, Enum):
    APPROVE = "approve"
    OMIT = "omit"
    REWRITE = "rewrite"


class AuthorAxiomDecisionInput(_StrictModel):
    case_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_decision_head_hash: str | None = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    action: AuthorAxiomDecisionAction


class AuthorAxiomDecisionRequest(_StrictModel):
    schema_version: Literal[DECISION_REQUEST_SCHEMA] = DECISION_REQUEST_SCHEMA
    expected_stage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[AuthorAxiomDecisionInput, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> "AuthorAxiomDecisionRequest":
        keys = tuple(item.case_key for item in self.decisions)
        if len(keys) != len(set(keys)):
            raise ValueError("author axiom decision case keys must be unique")
        return self


class AuthorAxiomDecisionObject(_StrictModel):
    schema_version: Literal[DECISION_OBJECT_SCHEMA] = DECISION_OBJECT_SCHEMA
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: AuthorAxiomDecisionAction
    previous_decision_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")


class AuthorAxiomFinalizeRequest(_StrictModel):
    schema_version: Literal[FINALIZE_REQUEST_SCHEMA] = FINALIZE_REQUEST_SCHEMA
    expected_stage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    finalize_token: str = Field(pattern=r"^[0-9a-f]{64}$")


def _record_payload(record: AuthorAxiomRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


def record_digest(record: AuthorAxiomRecord) -> str:
    return canonical_digest(_record_payload(record))


def semantic_record_digest(record: AuthorAxiomRecord) -> str:
    return canonical_digest(
        {
            "schema_version": "canon-v3/author-axiom-semantic/v1",
            "axiom_key": record.axiom_key,
            "category": record.category.value,
            "value": record.source.value,
        }
    )


def active_candidate_source_key(source: AuthorAxiomSource) -> str:
    """Identity accepted when a chapter cites an active axiom as evidence."""

    return canonical_digest(
        {
            "schema_version": "canon-v3/active-author-axiom-source/v1",
            "document_path": source.document_path,
            "document_sha256": source.document_sha256,
            "json_pointer": source.json_pointer,
            "value": source.value,
            "value_sha256": source.value_sha256,
        }
    )


def record_candidate_source_key(record: AuthorAxiomRecord) -> str:
    source = record.source
    return canonical_digest(
        {
            "schema_version": "canon-v3/active-author-axiom-source/v1",
            "document_path": source.document_path,
            "document_sha256": source.document_sha256,
            "json_pointer": source.json_pointer,
            "value": source.value,
            "value_sha256": source.value_sha256,
        }
    )


def _record_set_digest(records: Iterable[AuthorAxiomRecord]) -> str:
    return canonical_digest(
        {
            "schema_version": RECORD_SET_SCHEMA,
            "record_digests": sorted(record_digest(item) for item in records),
        }
    )


def _is_leaf_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    return isinstance(value, list) and all(
        item is None or isinstance(item, (str, bool, int, float))
        for item in value
    )


def _verify_draft_source(
    project_root: Path, source: AuthorAxiomDraftSpanSource
) -> None:
    relative = Path(source.document_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_path_traversal"
        )
    normalized = relative.as_posix()
    if (
        not normalized.startswith(".canon-ledger/tmp/author_axioms/")
        or not normalized.endswith(".json")
    ):
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_source_must_be_managed_draft"
        )
    path = resolve_inside_project(
        project_root, relative, reject_leaf_symlink=True
    )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_unreadable"
        ) from exc
    if hashlib.sha256(raw).hexdigest() != source.document_sha256:
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_document_hash_mismatch"
        )
    if source.end > len(raw) or raw[source.start : source.end] != (
        source.quote.encode("utf-8")
    ):
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_span_mismatch"
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_invalid_json"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "author_axioms"}
        or document.get("schema_version") != DRAFT_SCHEMA
        or not isinstance(document.get("author_axioms"), dict)
    ):
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_schema_invalid"
        )
    canonical_raw = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if raw != canonical_raw:
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_must_be_canonical_json"
        )
    actual = resolve_json_pointer(document, source.json_pointer)
    if (
        not _is_leaf_value(actual)
        or actual != source.value
        or type(actual) is not type(source.value)
        or canonical_digest(actual) != source.value_sha256
    ):
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_draft_value_mismatch"
        )
    pointer_key = source.json_pointer[len("/author_axioms/") :]
    pointer_key = pointer_key.replace("~1", "/").replace("~0", "~")
    key_raw = json.dumps(pointer_key, ensure_ascii=False).encode("utf-8")
    value_raw = json.dumps(
        actual, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    needle = key_raw + b":" + value_raw
    try:
        member_start = raw.index(needle)
    except ValueError as exc:  # pragma: no cover - canonical shape proves it.
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_pointer_span_unresolvable"
        ) from exc
    expected_start = member_start + len(key_raw) + 1
    if (
        source.start != expected_start
        or source.end != expected_start + len(value_raw)
    ):
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_span_not_bound_to_json_pointer"
        )


def _verify_record(project_root: Path, record: AuthorAxiomRecord) -> None:
    normalized_key = record.axiom_key.casefold()
    if any(part in normalized_key for part in _BANNED_NON_FACT_KEY_PARTS):
        raise AuthorAxiomEvidenceError(
            "canon_v3_author_axiom_non_fact_key_forbidden"
        )
    _verify_draft_source(project_root, record.source)


def _review_material(
    *,
    case_key: str,
    operation: AuthorAxiomOperation,
    axiom_key: str,
    target_digest: str,
    prior: AuthorAxiomRecord | None,
    proposed: AuthorAxiomRecord | None,
    prior_genesis_admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact author-facing material covered by material_digest."""

    return {
        "schema_version": REVIEW_MATERIAL_SCHEMA,
        "case_key": case_key,
        "operation": operation.value,
        "axiom_key": axiom_key,
        "target_digest": target_digest,
        "prior_category": prior.category.value if prior is not None else None,
        "prior_value": prior.source.value if prior is not None else None,
        "proposed_category": (
            proposed.category.value if proposed is not None else None
        ),
        "proposed_value": proposed.source.value if proposed is not None else None,
        "prior_record": _record_payload(prior) if prior else None,
        "proposed_record": _record_payload(proposed) if proposed else None,
        "prior_genesis_admission": (
            json.loads(
                json.dumps(
                    prior_genesis_admission,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            if prior_genesis_admission is not None
            else None
        ),
    }


def _case_for(
    operation: AuthorAxiomOperation,
    axiom_key: str,
    prior: AuthorAxiomRecord | None,
    proposed: AuthorAxiomRecord | None,
) -> AuthorAxiomCase:
    semantic = (
        semantic_record_digest(proposed)
        if proposed is not None
        else canonical_digest(
            {
                "schema_version": "canon-v3/author-axiom-removal/v1",
                "axiom_key": axiom_key,
                "prior_semantic_digest": semantic_record_digest(prior),
            }
        )
    )
    target = canonical_digest(
        {
            "schema_version": "canon-v3/author-axiom-target/v1",
            "operation": operation.value,
            "axiom_key": axiom_key,
            "semantic_record_digest": semantic,
        }
    )
    case_key = canonical_digest(
        {
            "schema_version": "canon-v3/author-axiom-case/v1",
            "operation": operation.value,
            "axiom_key": axiom_key,
        }
    )
    material = _review_material(
        case_key=case_key,
        operation=operation,
        axiom_key=axiom_key,
        target_digest=target,
        prior=prior,
        proposed=proposed,
    )
    return AuthorAxiomCase(
        case_key=case_key,
        axiom_key=axiom_key,
        operation=operation,
        target_digest=target,
        material_digest=canonical_digest(material),
        prior_record=prior,
        proposed_record=proposed,
    )


def _genesis_override_case(
    *,
    admission: Mapping[str, Any],
    proposed: AuthorAxiomRecord | None,
) -> AuthorAxiomCase:
    admission_digest = str(admission.get("admission_digest") or "")
    axiom_key = (
        proposed.axiom_key
        if proposed is not None
        else "genesis:" + admission_digest
    )
    target = canonical_digest(
        {
            "schema_version": "canon-v3/genesis-axiom-supersession-target/v1",
            "admission_digest": admission_digest,
            "fact_content_sha256": str(
                admission.get("fact_content_sha256") or ""
            ),
            "replacement_semantic_digest": (
                semantic_record_digest(proposed) if proposed else None
            ),
        }
    )
    case_key = canonical_digest(
        {
            "schema_version": "canon-v3/genesis-axiom-supersession-case/v1",
            "admission_digest": admission_digest,
        }
    )
    prior_payload = json.loads(
        json.dumps(admission, ensure_ascii=False, sort_keys=True)
    )
    material = _review_material(
        case_key=case_key,
        operation=AuthorAxiomOperation.SUPERSEDE_GENESIS,
        axiom_key=axiom_key,
        target_digest=target,
        prior=None,
        proposed=proposed,
        prior_genesis_admission=prior_payload,
    )
    return AuthorAxiomCase(
        case_key=case_key,
        axiom_key=axiom_key,
        operation=AuthorAxiomOperation.SUPERSEDE_GENESIS,
        target_digest=target,
        material_digest=canonical_digest(material),
        prior_genesis_admission=prior_payload,
        proposed_record=proposed,
    )


def _derive_cases(
    prior: Iterable[AuthorAxiomRecord],
    desired: Iterable[AuthorAxiomRecord],
) -> tuple[AuthorAxiomCase, ...]:
    prior_map = {item.axiom_key: item for item in prior}
    desired_map = {item.axiom_key: item for item in desired}
    cases: list[AuthorAxiomCase] = []
    for key in sorted(set(prior_map) | set(desired_map)):
        old = prior_map.get(key)
        new = desired_map.get(key)
        if old is None and new is not None:
            cases.append(_case_for(AuthorAxiomOperation.ADD, key, None, new))
        elif old is not None and new is None:
            cases.append(_case_for(AuthorAxiomOperation.REMOVE, key, old, None))
        elif (
            old is not None
            and new is not None
            and semantic_record_digest(old) != semantic_record_digest(new)
        ):
            cases.append(_case_for(AuthorAxiomOperation.UPDATE, key, old, new))
    return tuple(cases)


class AuthorAxiomChannel:
    def __init__(
        self,
        project_root: str | Path,
        *,
        repository: CanonV3Repository | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.repository = repository or CanonV3Repository(self.project_root)
        self.staging_path = self.project_root / AUTHOR_AXIOM_STAGING_RELATIVE_PATH
        self.staging_path.parent.mkdir(parents=True, exist_ok=True)
        self.staging_lock = FileLock(
            str(
                self.project_root
                / AUTHORITATIVE_STAGING_LOCK_RELATIVE_PATH
            ),
            timeout=10,
        )

    def _read_stage_unlocked(self) -> AuthorAxiomStagingPointer | None:
        try:
            raw = json.loads(self.staging_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthorAxiomChannelError(
                "canon_v3_author_axiom_staging_invalid_json"
            ) from exc
        try:
            return AuthorAxiomStagingPointer.model_validate(raw)
        except Exception as exc:
            raise AuthorAxiomChannelError(
                "canon_v3_author_axiom_staging_invalid"
            ) from exc

    def _write_stage_unlocked(
        self, pointer: AuthorAxiomStagingPointer
    ) -> None:
        atomic_write_json(
            self.staging_path,
            pointer.model_dump(mode="json"),
            use_lock=False,
            backup=False,
        )

    def _clear_stage_unlocked(self) -> None:
        try:
            self.staging_path.unlink()
        except FileNotFoundError:
            pass

    def _load_envelope(self, transaction_hash: str) -> AuthorAxiomPreparedEnvelope:
        try:
            return AuthorAxiomPreparedEnvelope.model_validate(
                self.repository.read_author_axiom_transaction(transaction_hash)
            )
        except Exception as exc:
            raise AuthorAxiomChannelError(
                "canon_v3_author_axiom_envelope_invalid"
            ) from exc

    def _genesis_facts(self, head: str | None) -> Mapping[str, Any]:
        if head is None:
            return {}
        cursor = self.repository.read_manifest(head, validate_references=True)
        seen: set[str] = {head}
        while int(cursor.get("generation") or 0) > 0:
            parent = str(cursor.get("parent_head_hash") or "")
            if not parent or parent in seen:
                raise AuthorAxiomChannelError(
                    "canon_v3_author_axiom_manifest_lineage_invalid"
                )
            seen.add(parent)
            cursor = self.repository.read_manifest(
                parent, validate_references=True
            )
        metadata = cursor.get("genesis_metadata")
        snapshot = (
            metadata.get("legacy_snapshot")
            if isinstance(metadata, Mapping)
            else None
        )
        facts = snapshot.get("facts") if isinstance(snapshot, Mapping) else None
        admissions = (
            facts.get("cutover_fact_admissions")
            if isinstance(facts, Mapping)
            else ()
        )
        return facts if isinstance(facts, Mapping) else {}

    def _genesis_axiom_admissions(
        self, head: str | None
    ) -> tuple[dict[str, Any], ...]:
        facts = self._genesis_facts(head)
        result: list[dict[str, Any]] = []
        for raw in facts.get("cutover_fact_admissions") or ():
            if (
                not isinstance(raw, Mapping)
                or raw.get("mode") != "author_axiom_snapshot"
            ):
                continue
            fact: Any = None
            for location in raw.get("locations") or ():
                try:
                    candidate = resolve_json_pointer(facts, str(location))
                except Exception:
                    continue
                if (
                    isinstance(candidate, Mapping)
                    and content_hash(candidate)
                    == str(raw.get("fact_content_sha256") or "")
                ):
                    fact = dict(candidate)
                    break
            if fact is None:
                raise AuthorAxiomChannelError(
                    "canon_v3_genesis_axiom_admission_fact_missing"
                )
            result.append({**dict(raw), "fact": fact})
        return tuple(
            sorted(
                result,
                key=lambda item: str(item.get("admission_digest") or ""),
            )
        )

    def _superseded_genesis_admission_digests(
        self, head: str | None
    ) -> frozenset[str]:
        if head is None:
            return frozenset()
        manifest = self.repository.read_manifest(
            head, validate_references=True
        )
        entries = self.repository._author_axiom_manifest_entries(manifest)
        if not entries:
            return frozenset()
        commit = self.repository.read_author_axiom_commit(
            str(entries[-1]["commit_hash"])
        )
        return frozenset(
            str(item)
            for item in commit.get(
                "superseded_legacy_admission_digests"
            )
            or ()
        )

    def _genesis_legacy_admission_digests(self, head: str | None) -> list[str]:
        superseded = self._superseded_genesis_admission_digests(head)
        return sorted(
            str(item.get("admission_digest") or "")
            for item in self._genesis_axiom_admissions(head)
            if str(item.get("admission_digest") or "") not in superseded
        )

    def active_records(self, head: str | None = None) -> tuple[AuthorAxiomRecord, ...]:
        resolved_head = (
            self.repository.current_head(validate=True)
            if head is None
            else head
        )
        if resolved_head is None:
            return ()
        manifest = self.repository.read_manifest(
            resolved_head, validate_references=True
        )
        entries = self.repository._author_axiom_manifest_entries(manifest)
        if not entries:
            return ()
        commit = self.repository.read_author_axiom_commit(
            str(entries[-1]["commit_hash"])
        )
        records = tuple(
            AuthorAxiomRecord.model_validate(item)
            for item in commit.get("records") or ()
        )
        if _record_set_digest(records) != commit.get("axiom_set_digest"):
            raise AuthorAxiomChannelError(
                "canon_v3_author_axiom_active_set_digest_mismatch"
            )
        return records

    def active_digest(self, head: str | None = None) -> str:
        resolved_head = (
            self.repository.current_head(validate=True)
            if head is None
            else head
        )
        commit_hash: str | None = None
        set_digest: str | None = None
        if resolved_head is not None:
            manifest = self.repository.read_manifest(
                resolved_head, validate_references=True
            )
            entries = self.repository._author_axiom_manifest_entries(manifest)
            if entries:
                commit_hash = str(entries[-1]["commit_hash"])
                commit = self.repository.read_author_axiom_commit(commit_hash)
                # active_records also proves the stored snapshot digest.
                self.active_records(resolved_head)
                set_digest = str(commit["axiom_set_digest"])
        return canonical_digest(
            {
                "schema_version": ACTIVE_SET_SCHEMA,
                "legacy_admission_digests": self._genesis_legacy_admission_digests(
                    resolved_head
                ),
                "active_author_axiom_commit_hash": commit_hash,
                "active_record_set_digest": set_digest,
            }
        )

    def active_candidate_source_keys(
        self, head: str | None = None
    ) -> frozenset[str]:
        return frozenset(
            record_candidate_source_key(item)
            for item in self.active_records(head)
        )

    def active_snapshot(self) -> dict[str, Any]:
        head = self.repository.current_head(validate=True)
        records = self.active_records(head)
        superseded = self._superseded_genesis_admission_digests(head)
        return {
            "schema_version": "canon-v3/active-author-axioms/v1",
            "head_hash": head,
            "author_axiom_digest": self.active_digest(head),
            "genesis_admissions": [
                item
                for item in self._genesis_axiom_admissions(head)
                if str(item.get("admission_digest") or "")
                not in superseded
            ],
            "superseded_genesis_admission_digests": sorted(superseded),
            "records": [_record_payload(item) for item in records],
            "record_digests": {
                item.axiom_key: record_digest(item) for item in records
            },
            "candidate_sources": {
                item.axiom_key: {
                        "source_type": "author_axiom",
                        "source_id": item.source.source_id,
                        "document_path": item.source.document_path,
                        "document_sha256": item.source.document_sha256,
                        "json_pointer": item.source.json_pointer,
                        "value": item.source.value,
                        "value_sha256": item.source.value_sha256,
                    }
                for item in records
            },
        }

    def _decision_objects(
        self, pointer: AuthorAxiomStagingPointer
    ) -> list[tuple[str, AuthorAxiomDecisionObject]]:
        result: list[tuple[str, AuthorAxiomDecisionObject]] = []
        for object_hash in pointer.decision_hashes:
            try:
                decision = AuthorAxiomDecisionObject.model_validate(
                    self.repository.read_author_axiom_decision(object_hash)
                )
            except Exception as exc:
                raise AuthorAxiomChannelError(
                    "canon_v3_author_axiom_decision_object_invalid"
                ) from exc
            if decision.transaction_hash != pointer.transaction_hash:
                raise AuthorAxiomChannelError(
                    "canon_v3_author_axiom_decision_transaction_mismatch"
                )
            result.append((object_hash, decision))
        return result

    def _decision_heads(
        self, pointer: AuthorAxiomStagingPointer
    ) -> dict[str, tuple[str, AuthorAxiomDecisionObject]]:
        objects = self._decision_objects(pointer)
        by_hash = {object_hash: decision for object_hash, decision in objects}
        referenced: set[str] = set()
        for _object_hash, decision in objects:
            previous = decision.previous_decision_hash
            if previous is not None:
                prior = by_hash.get(previous)
                if prior is None or prior.case_key != decision.case_key:
                    raise AuthorAxiomChannelError(
                        "canon_v3_author_axiom_decision_lineage_invalid"
                    )
                referenced.add(previous)
        heads: dict[str, tuple[str, AuthorAxiomDecisionObject]] = {}
        for object_hash, decision in objects:
            if object_hash in referenced:
                continue
            if decision.case_key in heads:
                raise AuthorAxiomChannelError(
                    "canon_v3_author_axiom_decision_conflict"
                )
            heads[decision.case_key] = (object_hash, decision)
        return heads

    def _negative_lineage_matches(
        self, pointer: AuthorAxiomStagingPointer, target_digest: str
    ) -> list[str]:
        matches: list[str] = []
        for object_hash in pointer.lineage_decision_hashes:
            try:
                decision = AuthorAxiomDecisionObject.model_validate(
                    self.repository.read_author_axiom_decision(object_hash)
                )
            except Exception as exc:
                raise AuthorAxiomChannelError(
                    "canon_v3_author_axiom_lineage_invalid"
                ) from exc
            if (
                decision.target_digest == target_digest
                and decision.action
                in {
                    AuthorAxiomDecisionAction.OMIT,
                    AuthorAxiomDecisionAction.REWRITE,
                }
            ):
                matches.append(object_hash)
        return sorted(matches)

    def _active_after_decisions(
        self,
        envelope: AuthorAxiomPreparedEnvelope,
        heads: Mapping[str, tuple[str, AuthorAxiomDecisionObject]],
    ) -> tuple[AuthorAxiomRecord, ...]:
        active = {item.axiom_key: item for item in envelope.prior_records}
        for case in envelope.cases:
            head = heads.get(case.case_key)
            if head is None:
                continue
            action = head[1].action
            if action is not AuthorAxiomDecisionAction.APPROVE:
                continue
            if case.operation is AuthorAxiomOperation.SUPERSEDE_GENESIS:
                # New v2 envelopes make replacement + supersession one exact
                # author-facing case.  Applying that case is therefore atomic.
                if case.proposed_record is not None:
                    active[case.proposed_record.axiom_key] = (
                        case.proposed_record
                    )
                continue
            if case.operation is AuthorAxiomOperation.REMOVE:
                active.pop(case.axiom_key, None)
            else:
                assert case.proposed_record is not None
                active[case.axiom_key] = case.proposed_record
        return tuple(active[key] for key in sorted(active))

    def _superseded_after_decisions(
        self,
        envelope: AuthorAxiomPreparedEnvelope,
        heads: Mapping[str, tuple[str, AuthorAxiomDecisionObject]],
    ) -> tuple[str, ...]:
        superseded = set(
            envelope.prior_superseded_genesis_admission_digests
        )
        for case in envelope.cases:
            if case.operation is not AuthorAxiomOperation.SUPERSEDE_GENESIS:
                continue
            head = heads.get(case.case_key)
            if (
                head is None
                or head[1].action is not AuthorAxiomDecisionAction.APPROVE
                or case.prior_genesis_admission is None
            ):
                continue
            superseded.add(
                str(
                    case.prior_genesis_admission.get(
                        "admission_digest"
                    )
                    or ""
                )
            )
        return tuple(sorted(item for item in superseded if item))

    def _genesis_replacement_conflicts(
        self,
        envelope: AuthorAxiomPreparedEnvelope,
        heads: Mapping[str, tuple[str, AuthorAxiomDecisionObject]],
    ) -> tuple[str, ...]:
        """Require linked replacement and supersession choices to agree.

        A prepare may expose both an ADD/UPDATE case and a
        SUPERSEDE_GENESIS case so the author can inspect both effects.  They
        are independently version-bound, but publication is atomic: approving
        only supersession would lose the replacement, while approving only the
        replacement would leave two conflicting active facts.

        Envelopes that contain only the supersession case remain valid because
        that case already carries the exact proposed record.
        """

        conflicts: list[str] = []
        for case in envelope.cases:
            if (
                case.operation is not AuthorAxiomOperation.SUPERSEDE_GENESIS
                or case.proposed_record is None
            ):
                continue
            decision = heads.get(case.case_key)
            if decision is None:
                continue
            related = [
                item
                for item in envelope.cases
                if item.axiom_key == case.proposed_record.axiom_key
                and item.operation
                in {AuthorAxiomOperation.ADD, AuthorAxiomOperation.UPDATE}
            ]
            if not related or any(
                heads.get(item.case_key) is None for item in related
            ):
                continue
            supersede_approved = (
                decision[1].action is AuthorAxiomDecisionAction.APPROVE
            )
            replacement_approved = all(
                heads[item.case_key][1].action
                is AuthorAxiomDecisionAction.APPROVE
                for item in related
            )
            if supersede_approved != replacement_approved:
                conflicts.append(case.case_key)
        return tuple(sorted(conflicts))

    def _finalize_token(
        self,
        pointer: AuthorAxiomStagingPointer,
        envelope: AuthorAxiomPreparedEnvelope,
        heads: Mapping[str, tuple[str, AuthorAxiomDecisionObject]],
    ) -> str:
        active = self._active_after_decisions(envelope, heads)
        superseded = self._superseded_after_decisions(envelope, heads)
        return canonical_digest(
            {
                "schema_version": FINALIZE_TOKEN_SCHEMA,
                "parent_head": envelope.parent_head,
                "transaction_hash": pointer.transaction_hash,
                "stage_digest": pointer.stage_digest,
                "decision_hashes": list(pointer.decision_hashes),
                "lineage_decision_hashes": list(
                    pointer.lineage_decision_hashes
                ),
                "active_record_set_digest": _record_set_digest(active),
                "superseded_genesis_admission_digests": list(superseded),
            }
        )

    def status(self) -> dict[str, Any]:
        assert_single_authoritative_staging(self.project_root)
        pointer = self._read_stage_unlocked()
        head = self.repository.current_head(validate=True)
        if pointer is None:
            return {
                "schema_version": WORKFLOW_SCHEMA,
                "state": "ready",
                "head_hash": head,
                "author_axiom_digest": self.active_digest(head),
                "transaction_hash": None,
                "stage_digest": None,
                "finalize_token": None,
                "cases": [],
                "can_finalize": False,
            }
        envelope = self._load_envelope(pointer.transaction_hash)
        heads = self._decision_heads(pointer)
        cases_payload: list[dict[str, Any]] = []
        pending = False
        rewrite = False
        for case in envelope.cases:
            head_decision = heads.get(case.case_key)
            action = head_decision[1].action if head_decision else None
            if action is None:
                pending = True
            elif action is AuthorAxiomDecisionAction.REWRITE:
                rewrite = True
            material = _review_material(
                case_key=case.case_key,
                operation=case.operation,
                axiom_key=case.axiom_key,
                target_digest=case.target_digest,
                prior=case.prior_record,
                proposed=case.proposed_record,
                prior_genesis_admission=case.prior_genesis_admission,
            )
            if canonical_digest(material) != case.material_digest:
                raise AuthorAxiomChannelError(
                    "canon_v3_author_axiom_review_material_digest_mismatch"
                )
            cases_payload.append(
                {
                    "case_key": case.case_key,
                    "operation": case.operation.value,
                    "axiom_key": case.axiom_key,
                    "target_digest": case.target_digest,
                    "decision_head_hash": (
                        head_decision[0] if head_decision else None
                    ),
                    "decision_action": action.value if action else None,
                    "negative_lineage_decision_hashes": (
                        self._negative_lineage_matches(
                            pointer, case.target_digest
                        )
                    ),
                    "review_material": {
                        **material,
                        "material_digest": case.material_digest,
                    },
                }
            )
        stale_head = head != envelope.parent_head
        replacement_conflicts = self._genesis_replacement_conflicts(
            envelope, heads
        )
        if stale_head:
            state = "recompile_required"
        elif rewrite:
            state = "rewrite_required"
        elif pending:
            state = "awaiting_human"
        elif replacement_conflicts:
            state = "awaiting_human"
        else:
            state = "ready_to_finalize"
        token = (
            self._finalize_token(pointer, envelope, heads)
            if state == "ready_to_finalize"
            else None
        )
        return {
            "schema_version": WORKFLOW_SCHEMA,
            "state": state,
            "head_hash": head,
            "parent_head": envelope.parent_head,
            "author_axiom_digest": self.active_digest(head),
            "transaction_hash": pointer.transaction_hash,
            "stage_digest": pointer.stage_digest,
            "finalize_token": token,
            "cases": cases_payload,
            "can_finalize": token is not None,
            "recovery_action": (
                "reprepare_author_axioms"
                if stale_head
                else "resolve_genesis_replacement_decisions"
                if replacement_conflicts
                else None
            ),
            "genesis_replacement_conflict_case_keys": list(
                replacement_conflicts
            ),
        }

    def _historical_negative_decisions(self) -> set[str]:
        result: set[str] = set()
        for _commit_hash, commit in self.repository.current_author_axiom_commits():
            result.update(
                str(item)
                for item in commit.get("lineage_decision_hashes") or ()
            )
            for object_hash in commit.get("decision_hashes") or ():
                decision = AuthorAxiomDecisionObject.model_validate(
                    self.repository.read_author_axiom_decision(str(object_hash))
                )
                if decision.action in {
                    AuthorAxiomDecisionAction.OMIT,
                    AuthorAxiomDecisionAction.REWRITE,
                }:
                    result.add(str(object_hash))
        return result

    def prepare(
        self, payload: Mapping[str, Any] | AuthorAxiomProposal
    ) -> dict[str, Any]:
        try:
            proposal = (
                payload
                if isinstance(payload, AuthorAxiomProposal)
                else AuthorAxiomProposal.model_validate(payload)
            )
        except Exception as exc:
            raise AuthorAxiomEvidenceError(
                "canon_v3_author_axiom_proposal_invalid"
            ) from exc
        from ..workflow_authority import WorkflowAuthority

        workflow = WorkflowAuthority(self.project_root).snapshot()
        if proposal.workflow_digest != workflow.get("workflow_digest"):
            raise AuthorAxiomStageConflict(
                "canon_v3_author_axiom_workflow_digest_mismatch"
            )
        existing_kind = workflow.get("transaction_kind")
        opening_from_ready = (
            existing_kind in {None, "chapter"}
            and workflow.get("state") == "ready"
            and workflow.get("can_write_next") is True
        )
        replacing_axiom_stage = (
            existing_kind == "author_axiom"
            and workflow.get("state")
            in {
                "awaiting_human",
                "rewrite_required",
                "recompile_required",
                "ready_to_finalize",
            }
        )
        if (
            not workflow.get("projection_fresh")
            or not (opening_from_ready or replacing_axiom_stage)
        ):
            raise AuthorAxiomStageConflict(
                "canon_v3_author_axiom_workflow_not_healthy"
            )
        with self.staging_lock:
            assert_single_authoritative_staging(self.project_root)
            if (self.project_root / CHAPTER_STAGING_RELATIVE_PATH).is_file():
                raise AuthorAxiomStageConflict(
                    "canon_v3_chapter_staging_blocks_author_axiom_prepare"
                )
            head = self.repository.current_head(validate=True)
            if head is None:
                raise AuthorAxiomStageConflict(
                    "canon_v3_author_axiom_initialize_required"
                )
            if proposal.parent_head != head:
                raise CanonHeadConflict(
                    expected=proposal.parent_head, actual=head
                )
            if not projection_is_fresh(self.project_root):
                raise AuthorAxiomStageConflict(
                    "canon_v3_author_axiom_projection_not_fresh"
                )
            # Recheck all non-staging health inputs after acquiring the shared
            # lock without recursively taking that lock through status().
            from .service import CanonV3Service

            guard = CanonV3Service(self.project_root)
            guard.repository = self.repository
            guard._legacy_prefix_guard()
            guard._assert_active_chapter_bindings()
            active_digest = self.active_digest(head)
            if proposal.active_author_axiom_digest != active_digest:
                raise AuthorAxiomStageConflict(
                    "canon_v3_author_axiom_active_digest_mismatch"
                )
            existing = self._read_stage_unlocked()
            actual_stage_digest = (
                existing.stage_digest if existing is not None else None
            )
            if proposal.expected_stage_digest != actual_stage_digest:
                raise AuthorAxiomStageConflict(
                    "canon_v3_author_axiom_prepare_stage_precondition_failed"
                )
            prior_records = self.active_records(head)
            prior_superseded = self._superseded_genesis_admission_digests(
                head
            )
            desired_records = tuple(
                sorted(proposal.records, key=lambda item: item.axiom_key)
            )
            cases_list = list(_derive_cases(prior_records, desired_records))
            active_admissions = {
                str(item.get("admission_digest") or ""): item
                for item in self._genesis_axiom_admissions(head)
                if str(item.get("admission_digest") or "")
                not in prior_superseded
            }
            desired_map = {
                item.axiom_key: item for item in desired_records
            }
            for override in proposal.genesis_overrides:
                admission = active_admissions.get(
                    override.admission_digest
                )
                if (
                    admission is None
                    or admission.get("fact_content_sha256")
                    != override.fact_content_sha256
                ):
                    raise AuthorAxiomEvidenceError(
                        "canon_v3_genesis_axiom_override_not_active"
                    )
                replacement = (
                    desired_map.get(override.replacement_axiom_key)
                    if override.replacement_axiom_key is not None
                    else None
                )
                if (
                    override.replacement_axiom_key is not None
                    and replacement is None
                ):
                    raise AuthorAxiomEvidenceError(
                        "canon_v3_genesis_axiom_replacement_missing"
                    )
                cases_list.append(
                    _genesis_override_case(
                        admission=admission,
                        proposed=replacement,
                    )
                )
            cases = tuple(sorted(cases_list, key=lambda item: item.case_key))
            if not cases:
                raise AuthorAxiomEvidenceError(
                    "canon_v3_author_axiom_proposal_has_no_semantic_change"
                )
            for case in cases:
                if case.proposed_record is not None:
                    _verify_record(self.project_root, case.proposed_record)
            envelope = AuthorAxiomPreparedEnvelope(
                parent_head=head,
                prior_author_axiom_digest=active_digest,
                prior_records=prior_records,
                desired_records=desired_records,
                prior_superseded_genesis_admission_digests=tuple(
                    sorted(prior_superseded)
                ),
                cases=cases,
            )
            transaction_hash = self.repository.put_author_axiom_transaction(
                envelope.model_dump(mode="json")
            )
            if existing is not None and existing.transaction_hash == transaction_hash:
                return self.status()
            lineage = self._historical_negative_decisions()
            if existing is not None:
                lineage.update(existing.lineage_decision_hashes)
                for object_hash, decision in self._decision_objects(existing):
                    if decision.action in {
                        AuthorAxiomDecisionAction.OMIT,
                        AuthorAxiomDecisionAction.REWRITE,
                    }:
                        lineage.add(object_hash)
            pointer = AuthorAxiomStagingPointer(
                transaction_hash=transaction_hash,
                lineage_decision_hashes=tuple(sorted(lineage)),
            )
            self._write_stage_unlocked(pointer)
        return self.status()

    def record_decisions(
        self,
        payload: Mapping[str, Any] | AuthorAxiomDecisionRequest,
    ) -> dict[str, Any]:
        try:
            request = (
                payload
                if isinstance(payload, AuthorAxiomDecisionRequest)
                else AuthorAxiomDecisionRequest.model_validate(payload)
            )
        except Exception as exc:
            raise AuthorAxiomDecisionError(
                "canon_v3_author_axiom_decision_request_invalid"
            ) from exc
        with self.staging_lock:
            assert_single_authoritative_staging(self.project_root)
            pointer = self._read_stage_unlocked()
            if pointer is None:
                raise AuthorAxiomDecisionError(
                    "canon_v3_no_author_axiom_staging"
                )
            if request.transaction_hash != pointer.transaction_hash:
                raise AuthorAxiomDecisionError(
                    "canon_v3_author_axiom_decision_transaction_precondition_failed"
                )
            if request.expected_stage_digest != pointer.stage_digest:
                raise AuthorAxiomDecisionError(
                    "canon_v3_author_axiom_decision_stage_precondition_failed"
                )
            envelope = self._load_envelope(pointer.transaction_hash)
            actual_head = self.repository.current_head(validate=True)
            if actual_head != envelope.parent_head:
                raise CanonHeadConflict(
                    expected=envelope.parent_head, actual=actual_head
                )
            try:
                for case in envelope.cases:
                    if case.proposed_record is not None:
                        _verify_record(
                            self.project_root, case.proposed_record
                        )
            except AuthorAxiomEvidenceError as exc:
                raise AuthorAxiomDecisionError(str(exc)) from exc
            cases = {item.case_key: item for item in envelope.cases}
            heads = self._decision_heads(pointer)
            planned: list[AuthorAxiomDecisionObject] = []
            for item in request.decisions:
                case = cases.get(item.case_key)
                if case is None:
                    raise AuthorAxiomDecisionError(
                        "canon_v3_author_axiom_decision_case_not_found"
                    )
                if item.target_digest != case.target_digest:
                    raise AuthorAxiomDecisionError(
                        "canon_v3_author_axiom_decision_target_precondition_failed"
                    )
                if item.material_digest != case.material_digest:
                    raise AuthorAxiomDecisionError(
                        "canon_v3_author_axiom_decision_material_precondition_failed"
                    )
                previous = heads.get(item.case_key)
                previous_hash = previous[0] if previous else None
                if item.expected_decision_head_hash != previous_hash:
                    raise AuthorAxiomDecisionError(
                        "canon_v3_author_axiom_decision_head_precondition_failed"
                    )
                if previous and previous[1].action is item.action:
                    continue
                planned.append(
                    AuthorAxiomDecisionObject(
                        transaction_hash=pointer.transaction_hash,
                        case_key=case.case_key,
                        target_digest=case.target_digest,
                        material_digest=case.material_digest,
                        action=item.action,
                        previous_decision_hash=previous_hash,
                    )
                )
            decision_hashes = set(pointer.decision_hashes)
            for decision in planned:
                decision_hashes.add(
                    self.repository.put_author_axiom_decision(
                        decision.model_dump(mode="json")
                    )
                )
            next_pointer = AuthorAxiomStagingPointer(
                transaction_hash=pointer.transaction_hash,
                decision_hashes=tuple(sorted(decision_hashes)),
                lineage_decision_hashes=pointer.lineage_decision_hashes,
            )
            self._write_stage_unlocked(next_pointer)
        return self.status()

    def finalize(
        self,
        payload: Mapping[str, Any] | AuthorAxiomFinalizeRequest,
    ) -> dict[str, Any]:
        try:
            request = (
                payload
                if isinstance(payload, AuthorAxiomFinalizeRequest)
                else AuthorAxiomFinalizeRequest.model_validate(payload)
            )
        except Exception as exc:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_finalize_request_invalid"
            ) from exc
        with self.staging_lock:
            assert_single_authoritative_staging(self.project_root)
            pointer = self._read_stage_unlocked()
            if pointer is None:
                return self._completed_finalize_retry(request)
            if request.transaction_hash != pointer.transaction_hash:
                raise AuthorAxiomFinalizeBlocked(
                    "canon_v3_author_axiom_finalize_transaction_precondition_failed"
                )
            if request.expected_stage_digest != pointer.stage_digest:
                raise AuthorAxiomFinalizeBlocked(
                    "canon_v3_author_axiom_finalize_stage_precondition_failed"
                )
            envelope = self._load_envelope(pointer.transaction_hash)
            heads = self._decision_heads(pointer)
            if any(case.case_key not in heads for case in envelope.cases):
                raise AuthorAxiomFinalizeBlocked(
                    "canon_v3_author_axiom_finalize_awaiting_human"
                )
            if any(
                heads[case.case_key][1].action
                is AuthorAxiomDecisionAction.REWRITE
                for case in envelope.cases
            ):
                raise AuthorAxiomFinalizeBlocked(
                    "canon_v3_author_axiom_finalize_rewrite_required"
                )
            replacement_conflicts = self._genesis_replacement_conflicts(
                envelope, heads
            )
            if replacement_conflicts:
                raise AuthorAxiomFinalizeBlocked(
                    "canon_v3_author_axiom_genesis_replacement_not_approved:"
                    + ",".join(replacement_conflicts)
                )
            expected_token = self._finalize_token(pointer, envelope, heads)
            if request.finalize_token != expected_token:
                raise AuthorAxiomFinalizeBlocked(
                    "canon_v3_author_axiom_finalize_token_precondition_failed"
                )
            actual_head = self.repository.current_head(validate=True)
            if actual_head == envelope.parent_head:
                try:
                    for case in envelope.cases:
                        if case.proposed_record is not None:
                            _verify_record(
                                self.project_root, case.proposed_record
                            )
                except AuthorAxiomEvidenceError as exc:
                    raise AuthorAxiomFinalizeBlocked(str(exc)) from exc
            active = self._active_after_decisions(envelope, heads)
            superseded = self._superseded_after_decisions(envelope, heads)
            set_digest = _record_set_digest(active)
            result = self.repository.seal_author_axiom(
                transaction=pointer.transaction_hash,
                expected_head=envelope.parent_head,
                decisions=pointer.decision_hashes,
                lineage_decisions=pointer.lineage_decision_hashes,
                records=[_record_payload(item) for item in active],
                axiom_set_digest=set_digest,
                superseded_legacy_admission_digests=superseded,
                expected_stage_digest=str(pointer.stage_digest),
                finalize_token=request.finalize_token,
            )
            self._clear_stage_unlocked()
            projection = rebuild_projection(self.project_root)
            return {
                "schema_version": "canon-v3/author-axiom-finalize-result/v1",
                "created": result.created,
                "transaction_hash": result.transaction_hash,
                "commit_hash": result.commit_hash,
                "head_hash": result.head_hash,
                "publication_head_hash": result.head_hash,
                "current_head": result.head_hash,
                "generation": result.generation,
                "current_generation": result.generation,
                "revision": result.revision,
                "author_axiom_digest": self.active_digest(result.head_hash),
                "projection_binding": projection["binding"],
            }

    def _completed_finalize_retry(
        self, request: AuthorAxiomFinalizeRequest
    ) -> dict[str, Any]:
        current_head = self.repository.current_head(validate=True)
        if current_head is None:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_no_author_axiom_staging"
            )
        current_manifest = self.repository.read_manifest(
            current_head, validate_references=True
        )
        entries = self.repository._author_axiom_manifest_entries(
            current_manifest
        )
        envelope = self._load_envelope(request.transaction_hash)
        matches: list[tuple[str, dict[str, Any]]] = []
        for entry in entries:
            candidate_hash = str(entry.get("commit_hash") or "")
            candidate = self.repository.read_author_axiom_commit(
                candidate_hash
            )
            if (
                candidate.get("transaction_hash")
                == request.transaction_hash
                and candidate.get("base_head_hash") == envelope.parent_head
            ):
                matches.append((candidate_hash, candidate))
        if len(matches) != 1:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_no_matching_author_axiom_commit"
            )
        commit_hash, commit = matches[0]
        pointer = AuthorAxiomStagingPointer(
            transaction_hash=request.transaction_hash,
            decision_hashes=tuple(commit.get("decision_hashes") or ()),
            lineage_decision_hashes=tuple(
                commit.get("lineage_decision_hashes") or ()
            ),
        )
        if pointer.stage_digest != request.expected_stage_digest:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_finalize_stage_precondition_failed"
            )
        if commit.get("base_head_hash") != envelope.parent_head:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_retry_parent_mismatch"
            )
        heads = self._decision_heads(pointer)
        replacement_conflicts = self._genesis_replacement_conflicts(
            envelope, heads
        )
        if replacement_conflicts:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_genesis_replacement_not_approved:"
                + ",".join(replacement_conflicts)
            )
        if self._finalize_token(pointer, envelope, heads) != request.finalize_token:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_finalize_token_precondition_failed"
            )
        active = self._active_after_decisions(envelope, heads)
        superseded = self._superseded_after_decisions(envelope, heads)
        if (
            commit.get("records")
            != [_record_payload(item) for item in active]
            or commit.get("axiom_set_digest") != _record_set_digest(active)
            or tuple(
                commit.get("superseded_legacy_admission_digests") or ()
            )
            != superseded
        ):
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_retry_commit_proof_mismatch"
            )

        # Return the exact manifest that originally published this commit,
        # even if later chapter or axiom transactions have advanced CURRENT.
        publication_head: str | None = None
        publication_manifest: dict[str, Any] | None = None
        cursor = current_head
        seen: set[str] = set()
        while cursor not in seen:
            seen.add(cursor)
            candidate_manifest = self.repository.read_manifest(
                cursor, validate_references=True
            )
            parent = candidate_manifest.get("parent_head_hash")
            candidate_entries = (
                self.repository._author_axiom_manifest_entries(
                    candidate_manifest
                )
            )
            if (
                parent == envelope.parent_head
                and candidate_entries
                and candidate_entries[-1].get("commit_hash") == commit_hash
            ):
                publication_head = cursor
                publication_manifest = candidate_manifest
                break
            if not isinstance(parent, str) or not parent:
                break
            cursor = parent
        if publication_head is None or publication_manifest is None:
            raise AuthorAxiomFinalizeBlocked(
                "canon_v3_author_axiom_retry_publication_head_missing"
            )
        projection = rebuild_projection(self.project_root)
        return {
            "schema_version": "canon-v3/author-axiom-finalize-result/v1",
            "created": False,
            "transaction_hash": request.transaction_hash,
            "commit_hash": commit_hash,
            "head_hash": publication_head,
            "publication_head_hash": publication_head,
            "current_head": current_head,
            "generation": int(publication_manifest.get("generation") or 0),
            "current_generation": int(
                current_manifest.get("generation") or 0
            ),
            "revision": int(commit.get("revision") or 0),
            "author_axiom_digest": self.active_digest(publication_head),
            "projection_binding": projection["binding"],
        }


def validate_publication_proof(
    *,
    project_root: Path,
    repository: CanonV3Repository,
    transaction_hash: str,
    transaction_payload: Mapping[str, Any],
    decision_hashes: tuple[str, ...],
    lineage_decision_hashes: tuple[str, ...],
    records: list[dict[str, Any]],
    axiom_set_digest: str,
    superseded_legacy_admission_digests: tuple[str, ...],
    expected_head: str,
    expected_stage_digest: str,
    finalize_token: str,
    verify_draft_sources: bool = True,
) -> None:
    """Re-run the exact stage/material/human proof inside the HEAD lock."""

    channel = AuthorAxiomChannel(project_root, repository=repository)
    pointer = channel._read_stage_unlocked()
    if pointer is None:
        raise ValueError("authoritative_author_axiom_staging_missing")
    if pointer.transaction_hash != transaction_hash:
        raise ValueError("author_axiom_staging_transaction_mismatch")
    if pointer.decision_hashes != decision_hashes:
        raise ValueError("author_axiom_staging_decision_set_mismatch")
    if pointer.lineage_decision_hashes != lineage_decision_hashes:
        raise ValueError("author_axiom_staging_lineage_set_mismatch")
    if pointer.stage_digest != expected_stage_digest:
        raise ValueError("author_axiom_staging_digest_mismatch")
    envelope = AuthorAxiomPreparedEnvelope.model_validate(transaction_payload)
    if envelope.parent_head != expected_head:
        raise ValueError("author_axiom_parent_head_mismatch")
    heads = channel._decision_heads(pointer)
    if any(case.case_key not in heads for case in envelope.cases):
        raise ValueError("author_axiom_human_decision_incomplete")
    if any(
        heads[case.case_key][1].action is AuthorAxiomDecisionAction.REWRITE
        for case in envelope.cases
    ):
        raise ValueError("author_axiom_rewrite_decision_active")
    replacement_conflicts = channel._genesis_replacement_conflicts(
        envelope, heads
    )
    if replacement_conflicts:
        raise ValueError(
            "author_axiom_genesis_replacement_not_approved:"
            + ",".join(replacement_conflicts)
        )
    if channel._finalize_token(pointer, envelope, heads) != finalize_token:
        raise ValueError("author_axiom_finalize_token_mismatch")
    if verify_draft_sources:
        for case in envelope.cases:
            if case.proposed_record is not None:
                _verify_record(project_root, case.proposed_record)
    expected_records = channel._active_after_decisions(envelope, heads)
    expected_superseded = channel._superseded_after_decisions(
        envelope, heads
    )
    if [_record_payload(item) for item in expected_records] != records:
        raise ValueError("author_axiom_active_records_mismatch")
    if _record_set_digest(expected_records) != axiom_set_digest:
        raise ValueError("author_axiom_set_digest_mismatch")
    if expected_superseded != superseded_legacy_admission_digests:
        raise ValueError("author_axiom_genesis_supersession_mismatch")


__all__ = [
    "ACTIVE_SET_SCHEMA",
    "AuthorAxiomChannel",
    "AuthorAxiomChannelError",
    "AuthorAxiomDecisionError",
    "AuthorAxiomEvidenceError",
    "AuthorAxiomFinalizeBlocked",
    "AuthorAxiomProposal",
    "AuthorAxiomStageConflict",
    "active_candidate_source_key",
    "record_candidate_source_key",
    "record_digest",
    "semantic_record_digest",
    "validate_publication_proof",
]

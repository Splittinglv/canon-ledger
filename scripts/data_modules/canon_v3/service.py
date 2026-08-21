#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Application service for the one Canon v3 chapter transaction path."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json

from ..chapter_content_binding import (
    ChapterBindingError,
    ChapterContentBinding,
    require_chapter_binding,
)
from .compiler import (
    CHECKPOINT_KINDS,
    compile_transaction,
    default_semantic_slot_id,
)
from .entity_registry import (
    bind_entity_registry_to_transaction,
    build_approved_entity_registry,
    plan_entity_resolutions,
)
from .evidence import (
    candidate_digest,
    lineage_key,
    semantic_claim_digest,
    source_digest,
)
from .projection import fact_record_index, projection_is_fresh, rebuild_projection
from .repository import (
    CanonChapterSequenceError,
    CanonHeadConflict,
    CanonIntegrityError,
    CanonRepositoryError,
    CanonV3Repository,
    ProjectionStaleError,
)
from .review import (
    InvalidDecision,
    ReviewAction,
    ReviewCase,
    ReviewDecision,
    WorkflowState as ReviewWorkflowState,
    case_to_dict,
    decision_from_dict,
    decision_to_dict,
    make_decision,
    reduce_review,
    review_case_from_requirement,
)
from .schema import (
    FactCandidate,
    FactKind,
    AuthorAxiomSource,
    CanonEffect,
    ObservationKind,
    PreparedTransaction,
    ReviewLevel,
    ReviewObservation,
    ScanAttestation,
    ScanStatus,
    canonical_digest,
)
from .source_verifier import (
    SourceVerificationError,
    verify_all_candidate_sources,
    verify_candidate_sources,
)


PROPOSAL_SCHEMA_V1 = "canon-v3/proposal-batch/v1"
PROPOSAL_SCHEMA = "canon-v3/proposal-batch/v2"
PREPARED_ENVELOPE_SCHEMA_V1 = "canon-v3/prepared-envelope/v1"
PREPARED_ENVELOPE_SCHEMA = "canon-v3/prepared-envelope/v2"
STAGING_SCHEMA_V1 = "canon-v3/staging-pointer/v1"
STAGING_SCHEMA = "canon-v3/staging-pointer/v2"
DECISION_ENVELOPE_SCHEMA_V1 = "canon-v3/decision-envelope/v1"
DECISION_ENVELOPE_SCHEMA = "canon-v3/decision-envelope/v2"
DECISION_REQUEST_SCHEMA = "canon-v3/decision-request/v2"
FINALIZE_REQUEST_SCHEMA = "canon-v3/finalize-request/v2"
FINALIZE_TOKEN_SCHEMA = "canon-v3/finalize-token/v2"
WORKFLOW_SCHEMA = "canon-v3/workflow-snapshot/v2"
STAGING_RELATIVE_PATH = Path(".story-system/v3/STAGING.json")
REQUIRED_SCAN_DIMENSIONS = frozenset(
    {"setting", "timeline", "continuity", "character", "logic"}
)
AUTHOR_AXIOM_SET_SCHEMA = "canon-v3/author-axiom-set/v2"
EMPTY_AUTHOR_AXIOM_DIGEST = canonical_digest(
    {
        "schema_version": AUTHOR_AXIOM_SET_SCHEMA,
        "legacy_admission_digests": [],
        "active_author_axiom_commit_hash": None,
        "active_record_set_digest": None,
    }
)


class CanonV3ServiceError(RuntimeError):
    pass


class MigrationRequiredError(CanonV3ServiceError):
    pass


class ActiveTransactionError(CanonV3ServiceError):
    pass


class PreparedTransactionInvalid(CanonV3ServiceError):
    pass


class ScanAttestationError(CanonV3ServiceError):
    pass


class FinalizeBlockedError(CanonV3ServiceError):
    pass


class ActiveCanonBindingError(CanonV3ServiceError):
    def __init__(self, chapter: int, code: str) -> None:
        self.chapter = int(chapter)
        self.code = str(code)
        super().__init__(
            f"canon_v3_active_chapter_binding_stale:{self.chapter}:{self.code}"
        )


class ChapterProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[PROPOSAL_SCHEMA] = PROPOSAL_SCHEMA
    chapter: int = Field(ge=1)
    chapter_binding: ChapterContentBinding
    parent_head: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    author_axiom_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_registry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: tuple[FactCandidate, ...] = ()
    observations: tuple[ReviewObservation, ...] = ()
    scan_attestations: tuple[ScanAttestation, ...] = Field(min_length=1)
    # A replacement prepare is a compare-and-swap operation.  These fields
    # are client preconditions only and are never persisted in the immutable
    # PreparedEnvelope.
    expected_stage_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def binding_matches_chapter(self) -> "ChapterProposalBatch":
        if self.chapter_binding.chapter != self.chapter:
            raise ValueError("proposal chapter_binding chapter mismatch")
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal candidate_id values must be unique")
        return self


class PreparedEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[
        PREPARED_ENVELOPE_SCHEMA_V1,
        PREPARED_ENVELOPE_SCHEMA,
    ] = PREPARED_ENVELOPE_SCHEMA
    chapter: int = Field(ge=1)
    chapter_binding: ChapterContentBinding
    prepared_transaction: PreparedTransaction
    candidates: tuple[FactCandidate, ...]
    observations: tuple[ReviewObservation, ...]
    scan_attestations: tuple[ScanAttestation, ...]
    source_workflow_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    author_axiom_digest: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def envelope_is_closed(self) -> "PreparedEnvelope":
        if self.chapter_binding.chapter != self.chapter:
            raise ValueError("prepared envelope chapter binding mismatch")
        actual = tuple(sorted(candidate_digest(item) for item in self.candidates))
        if actual != self.prepared_transaction.candidate_digests:
            raise ValueError("prepared envelope candidate set mismatch")
        if self.schema_version == PREPARED_ENVELOPE_SCHEMA:
            if self.source_workflow_digest is None:
                raise ValueError("v2 prepared envelope requires workflow digest")
            if self.author_axiom_digest is None:
                raise ValueError("v2 prepared envelope requires author axiom digest")
            for attestation in self.scan_attestations:
                if (
                    attestation.parent_head
                    != self.prepared_transaction.parent_head
                    or attestation.author_axiom_digest
                    != self.author_axiom_digest
                    or attestation.entity_registry_digest
                    != self.prepared_transaction.entity_registry_digest
                ):
                    raise ValueError("prepared envelope attestation binding mismatch")
        elif self.source_workflow_digest is not None or self.author_axiom_digest is not None:
            raise ValueError("v1 prepared envelope forbids v2 authority bindings")
        return self


class StagingPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[STAGING_SCHEMA_V1, STAGING_SCHEMA] = STAGING_SCHEMA
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_hashes: tuple[str, ...] = ()
    # Negative adjudications from superseded preparations remain immutable
    # tombstones until the chapter binding changes or the candidate is truly
    # rewritten. They are not decisions for the current transaction.
    lineage_decision_hashes: tuple[str, ...] = ()
    stage_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def decision_hashes_are_canonical(self) -> "StagingPointer":
        for name in ("decision_hashes", "lineage_decision_hashes"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"staging {name} must be sorted and unique")
            if any(
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in values
            ):
                raise ValueError(f"staging {name} must be SHA-256 digests")
        if self.schema_version == STAGING_SCHEMA_V1:
            if self.stage_digest is not None:
                raise ValueError("v1 staging pointer cannot carry stage_digest")
            return self
        expected = canonical_digest(self.digest_payload())
        if self.stage_digest is None:
            object.__setattr__(self, "stage_digest", expected)
        elif self.stage_digest != expected:
            raise ValueError("staging stage_digest mismatch")
        return self

    def digest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": STAGING_SCHEMA,
            "transaction_hash": self.transaction_hash,
            "decision_hashes": list(self.decision_hashes),
            "lineage_decision_hashes": list(self.lineage_decision_hashes),
        }

    @property
    def is_v2(self) -> bool:
        return self.schema_version == STAGING_SCHEMA


class DecisionInputV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Required-but-nullable.  ``null`` proves that the caller observed no
    # prior decision head; omission is not accepted as that proof.
    expected_decision_head_hash: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    action: ReviewAction
    corrected_candidate: FactCandidate | None = None


class DecisionRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[DECISION_REQUEST_SCHEMA] = DECISION_REQUEST_SCHEMA
    expected_stage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decisions: tuple[DecisionInputV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def case_keys_are_unique(self) -> "DecisionRequestV2":
        keys = tuple(item.case_key for item in self.decisions)
        if len(keys) != len(set(keys)):
            raise ValueError("decision request case_key values must be unique")
        return self


class FinalizeRequestV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[FINALIZE_REQUEST_SCHEMA] = FINALIZE_REQUEST_SCHEMA
    expected_stage_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    transaction_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    finalize_token: str = Field(pattern=r"^[0-9a-f]{64}$")


def _json_payload(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


class CanonV3Service:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.repository = CanonV3Repository(self.project_root)
        self.staging_path = self.project_root / STAGING_RELATIVE_PATH
        self.staging_path.parent.mkdir(parents=True, exist_ok=True)
        self.staging_lock = FileLock(str(self.staging_path) + ".lock", timeout=10)

    def _legacy_commits_exist(self) -> bool:
        return any(
            (self.project_root / ".story-system" / "commits").glob(
                "chapter_*.commit.json"
            )
        )

    def _legacy_prefix_guard(self) -> dict[str, Any] | None:
        """Return status only for a genesis that imported a v2 prefix."""

        head = self.repository.current_head(validate=True)
        if head is None:
            return None
        cursor = self.repository.read_manifest(head, validate_references=True)
        seen: set[str] = set()
        while int(cursor.get("generation") or 0) > 0:
            parent = str(cursor.get("parent_head_hash") or "")
            if not parent or parent in seen:
                raise PreparedTransactionInvalid("canon_v3_genesis_chain_invalid")
            seen.add(parent)
            cursor = self.repository.read_manifest(parent, validate_references=True)
        metadata = cursor.get("genesis_metadata")
        if not isinstance(metadata, dict):
            return None
        if metadata.get("schema_version") == "canon-v3/legacy-genesis/v1":
            raise MigrationRequiredError(
                "canon_v3_legacy_genesis_v1_recertification_required"
            )
        snapshot = metadata.get("legacy_snapshot")
        facts = snapshot.get("facts") if isinstance(snapshot, dict) else None
        omitted = (
            facts.get("omitted_fact_ids")
            if isinstance(facts, dict)
            else ()
        )
        if omitted:
            raise MigrationRequiredError(
                "canon_v3_genesis_contains_unresolved_omitted_facts:"
                + ",".join(sorted(str(item) for item in omitted))
            )
        if metadata.get("source") != "v2_accepted_commits":
            return None
        from .migration import legacy_prefix_status

        status = legacy_prefix_status(self.project_root)
        reasons = {str(item) for item in status.get("reason_codes") or []}
        prefix_reasons = reasons - {"v3_projection_stale"}
        if status.get("migration_required") and prefix_reasons:
            raise MigrationRequiredError(
                "canon_v3_legacy_prefix_stale:"
                + ",".join(sorted(prefix_reasons))
            )
        return status

    def _legacy_recertification_workflow_fields(
        self,
        error: Exception,
    ) -> dict[str, Any]:
        """Expose the one reviewable v1 repair transaction on every surface."""

        message = str(error)
        if "legacy_genesis_v1_recertification_required" not in message:
            return {
                "cases": [],
                "counts": {},
                "recovery_action": "remigrate_legacy_suffix",
            }
        from .migration import audit_cutover

        report = audit_cutover(self.project_root)
        conflicts = list(report.get("conflicting_staging_kinds") or ())
        cases = [] if conflicts else list(report.get("cases") or ())
        recovery = (
            "resolve_recertification_staging_conflict"
            if conflicts
            else "audit_blocked_legacy_recertification"
            if report.get("state") == "blocked"
            else "review_and_publish_legacy_recertification"
        )
        return {
            "cases": cases,
            "counts": {"human_required": len(cases)},
            "recovery_action": recovery,
            "authoritative_transaction": "legacy_recertification",
            "transaction_kind": "legacy_recertification",
            "recertification_state": report.get("state"),
            "recertification_plan_digest": report.get(
                "detached_plan_digest"
            ),
            "recertification_publish_token": report.get("publish_token"),
            "recertification_required_case_count": report.get(
                "required_case_count"
            ),
            "conflicting_staging_kinds": conflicts,
            "recertification_reason_codes": list(
                report.get("reason_codes") or ()
            ),
            "recertification_details": list(report.get("details") or ()),
        }

    def _cutover_chapter(self, head: str) -> int:
        cursor = self.repository.read_manifest(head, validate_references=True)
        seen: set[str] = set()
        while int(cursor.get("generation") or 0) > 0:
            parent = str(cursor.get("parent_head_hash") or "")
            if not parent or parent in seen:
                raise PreparedTransactionInvalid("canon_v3_genesis_chain_invalid")
            seen.add(parent)
            cursor = self.repository.read_manifest(parent, validate_references=True)
        metadata = cursor.get("genesis_metadata")
        if not isinstance(metadata, dict):
            raise PreparedTransactionInvalid("canon_v3_genesis_metadata_invalid")
        try:
            cutover = int(metadata.get("cutover_chapter") or 0)
        except (TypeError, ValueError) as exc:
            raise PreparedTransactionInvalid(
                "canon_v3_cutover_chapter_invalid"
            ) from exc
        if cutover < 0:
            raise PreparedTransactionInvalid("canon_v3_cutover_chapter_invalid")
        return cutover

    def _active_author_axiom_digest(self, head: str | None) -> str:
        """Return only HEAD-reachable immutable axiom authority."""

        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).active_digest(head)

    def _active_author_axiom_source_keys(
        self, head: str | None
    ) -> frozenset[str]:
        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).active_candidate_source_keys(head)

    def _assert_chapter_sequence(self, head: str, chapter: int) -> None:
        manifest = self.repository.read_manifest(head, validate_references=True)
        entries = manifest.get("chapters") or []
        cutover = self._cutover_chapter(head)
        if not entries:
            expected = cutover + 1
            if int(chapter) != expected:
                raise CanonChapterSequenceError(
                    f"canon_v3_first_chapter_must_be_cutover_plus_one:"
                    f"expected={expected},actual={chapter}"
                )
            return
        last = int(entries[-1].get("chapter") or 0)
        if int(chapter) > last + 1:
            raise CanonChapterSequenceError(
                f"canon_v3_chapter_gap:last={last},requested={chapter}"
            )
        if int(chapter) <= cutover:
            raise MigrationRequiredError(
                f"canon_v3_edit_crosses_legacy_cutover:{chapter}<={cutover}"
            )

    def _assert_active_chapter_bindings(
        self,
        *,
        before_chapter: int | None = None,
    ) -> None:
        for _commit_hash, commit in self.repository.current_commits():
            chapter = int(commit.get("chapter") or 0)
            if before_chapter is not None and chapter >= int(before_chapter):
                continue
            transaction_hash = str(commit.get("transaction_hash") or "")
            envelope = self._load_envelope(transaction_hash)
            if envelope.chapter != chapter:
                raise PreparedTransactionInvalid(
                    "canon_v3_commit_transaction_chapter_mismatch"
                )
            try:
                require_chapter_binding(
                    self.project_root,
                    chapter,
                    envelope.chapter_binding,
                )
            except ChapterBindingError as exc:
                raise ActiveCanonBindingError(chapter, exc.code) from exc
            try:
                wrapper = self.repository.recertified_suffix_wrapper(
                    transaction_hash
                )
                verify_all_candidate_sources(
                    self.project_root,
                    envelope.chapter_binding,
                    envelope.candidates,
                    active_author_axiom_source_keys=(
                        self._active_author_axiom_source_keys(
                            envelope.prepared_transaction.parent_head
                        )
                    ),
                )
            except SourceVerificationError as exc:
                raise ActiveCanonBindingError(
                    chapter,
                    "source_reference_changed",
                ) from exc
            if wrapper is not None:
                self._validate_active_recertified_suffix(
                    wrapper=wrapper,
                    envelope=envelope,
                    commit=commit,
                )
                continue
            pointer = StagingPointer(
                transaction_hash=transaction_hash,
                decision_hashes=tuple(commit.get("decision_hashes") or ()),
            )
            reduction = self._validated_reduction(pointer, envelope)
            if reduction.snapshot.state is not ReviewWorkflowState.READY:
                raise PreparedTransactionInvalid(
                    "canon_v3_active_commit_has_unresolved_review"
                )
            active = {
                record.candidate_digest for record in reduction.active_candidates
            }
            expected_effects = [
                effect.model_dump(mode="json")
                for effect in envelope.prepared_transaction.effects
                if effect.candidate_digest in active
            ]
            if commit.get("canon_effects") != expected_effects:
                raise PreparedTransactionInvalid(
                    "canon_v3_active_commit_effects_mismatch"
                )

    def _validate_active_recertified_suffix(
        self,
        *,
        wrapper: Mapping[str, Any],
        envelope: PreparedEnvelope,
        commit: Mapping[str, Any],
    ) -> None:
        """Prove a migration wrapper is bound to this active v2 genesis."""

        head = self.repository.current_head(validate=False)
        if head is None:
            raise PreparedTransactionInvalid(
                "canon_v3_recertified_suffix_without_active_head"
            )
        cursor = self.repository.read_manifest(head, validate_references=True)
        seen: set[str] = set()
        while int(cursor.get("generation") or 0) > 0:
            parent = str(cursor.get("parent_head_hash") or "")
            if not parent or parent in seen:
                raise PreparedTransactionInvalid(
                    "canon_v3_recertified_suffix_manifest_lineage_invalid"
                )
            seen.add(parent)
            cursor = self.repository.read_manifest(
                parent, validate_references=True
            )
        metadata = cursor.get("genesis_metadata")
        receipt = (
            metadata.get("recertification")
            if isinstance(metadata, Mapping)
            else None
        )
        binding = wrapper.get("recertification_binding")
        if not isinstance(receipt, Mapping) or not isinstance(binding, Mapping):
            raise PreparedTransactionInvalid(
                "canon_v3_recertified_suffix_receipt_missing"
            )
        receipt_binding = {
            "prior_head_hash": receipt.get("prior_head_hash"),
            "detached_plan_digest": receipt.get("detached_plan_digest"),
            "publish_token": receipt.get("publish_token"),
            "review_decision_set_digest": receipt.get(
                "review_decision_set_digest"
            ),
            "review_cases_digest": receipt.get("review_cases_digest"),
        }
        if dict(binding) != receipt_binding:
            raise PreparedTransactionInvalid(
                "canon_v3_recertified_suffix_receipt_mismatch"
            )
        parent_head = str(wrapper.get("parent_head") or "")
        expected_registry = build_approved_entity_registry(
            self.repository,
            parent_head,
            target_chapter=envelope.chapter,
        ).registry_digest
        if (
            envelope.prepared_transaction.parent_head != parent_head
            or envelope.prepared_transaction.entity_registry_digest
            != expected_registry
            or envelope.author_axiom_digest
            != self._active_author_axiom_digest(parent_head)
            or commit.get("canon_effects")
            != [
                effect.model_dump(mode="json")
                for effect in envelope.prepared_transaction.effects
            ]
            or commit.get("decision_hashes")
        ):
            raise PreparedTransactionInvalid(
                "canon_v3_recertified_suffix_active_binding_invalid"
            )

    def initialize_new_project(self) -> str:
        if self._legacy_commits_exist():
            raise MigrationRequiredError("canon_v3_legacy_cutover_required")
        head = self.repository.current_head(validate=True)
        if head is None:
            # Bind the author's explicit initialization facts into the same
            # immutable fact snapshot format used at a v2 cutover.  This is a
            # one-time author-approved genesis import; later live file edits do
            # not mutate Canon and must enter as author_axiom candidates.
            from .migration import migrate_legacy

            result = migrate_legacy(self.project_root, cutover_chapter=0)
            head = str(result["head_hash"])
        else:
            rebuild_projection(self.project_root)
        return head

    def _ensure_initialized_for_prepare(self, chapter: int) -> str:
        head = self.repository.current_head(validate=True)
        if head is not None:
            self._legacy_prefix_guard()
            self._assert_active_chapter_bindings(before_chapter=chapter)
            if not projection_is_fresh(self.project_root):
                raise ProjectionStaleError("canon_v3_projection_rebuild_required")
            return head
        if self._legacy_commits_exist():
            raise MigrationRequiredError("canon_v3_legacy_cutover_required")
        raise MigrationRequiredError("canon_v3_initialize_required")

    def _read_staging_unlocked(self) -> StagingPointer | None:
        try:
            raw = json.loads(self.staging_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise PreparedTransactionInvalid("canon_v3_staging_invalid_json") from exc
        try:
            return StagingPointer.model_validate(raw)
        except Exception as exc:
            raise PreparedTransactionInvalid("canon_v3_staging_invalid") from exc

    def _write_staging_unlocked(self, pointer: StagingPointer) -> None:
        atomic_write_json(
            self.staging_path,
            _json_payload(pointer),
            use_lock=False,
            backup=False,
        )

    def _clear_staging_unlocked(self) -> None:
        try:
            self.staging_path.unlink()
        except FileNotFoundError:
            pass

    def _load_envelope(self, transaction_hash: str) -> PreparedEnvelope:
        try:
            payload = self.repository.prepared_envelope_payload(transaction_hash)
            return PreparedEnvelope.model_validate(payload)
        except Exception as exc:
            raise PreparedTransactionInvalid(
                "canon_v3_prepared_envelope_invalid"
            ) from exc

    @staticmethod
    def _candidate_map(
        envelope: PreparedEnvelope,
    ) -> dict[str, FactCandidate]:
        result: dict[str, FactCandidate] = {}
        for candidate in envelope.candidates:
            digest = candidate_digest(candidate)
            if digest in result:
                raise PreparedTransactionInvalid(
                    "canon_v3_duplicate_semantic_candidate"
                )
            result[digest] = candidate
        return result

    @staticmethod
    def _validate_scan_attestations(
        batch: ChapterProposalBatch | PreparedEnvelope,
    ) -> None:
        candidate_digests = {candidate_digest(item) for item in batch.candidates}
        complete = [
            item
            for item in batch.scan_attestations
            if item.status == ScanStatus.COMPLETE
        ]
        if not complete:
            raise ScanAttestationError("canon_v3_complete_fact_scan_required")
        if any(
            item.chapter_sha256 != batch.chapter_binding.sha256
            for item in batch.scan_attestations
        ):
            raise ScanAttestationError("canon_v3_scan_chapter_hash_mismatch")
        if (
            isinstance(batch, PreparedEnvelope)
            and batch.schema_version == PREPARED_ENVELOPE_SCHEMA_V1
        ):
            expected_parent = expected_axiom = expected_registry = None
            enforce_authority_binding = False
        elif isinstance(batch, ChapterProposalBatch):
            expected_parent = batch.parent_head
            expected_axiom = batch.author_axiom_digest
            expected_registry = batch.entity_registry_digest
            enforce_authority_binding = True
        else:
            expected_parent = batch.prepared_transaction.parent_head
            expected_axiom = batch.author_axiom_digest
            expected_registry = (
                batch.prepared_transaction.entity_registry_digest
            )
            enforce_authority_binding = True
        if enforce_authority_binding and any(
            item.parent_head != expected_parent
            or item.author_axiom_digest != expected_axiom
            or item.entity_registry_digest != expected_registry
            for item in batch.scan_attestations
        ):
            raise ScanAttestationError(
                "canon_v3_scan_authority_binding_mismatch"
            )
        fully_bound = [
            item
            for item in complete
            if REQUIRED_SCAN_DIMENSIONS.issubset(set(item.dimensions))
            and candidate_digests.issubset(set(item.checked_candidate_digests))
        ]
        if not fully_bound:
            raise ScanAttestationError(
                "canon_v3_single_complete_scan_must_cover_all_dimensions_and_candidates"
            )

    def _recompile(self, envelope: PreparedEnvelope) -> PreparedTransaction:
        verify_all_candidate_sources(
            self.project_root,
            envelope.chapter_binding,
            envelope.candidates,
            active_author_axiom_source_keys=(
                self._active_author_axiom_source_keys(
                    envelope.prepared_transaction.parent_head
                )
            ),
        )
        self._validate_scan_attestations(envelope)
        self._validate_prior_fact_references(
            envelope.prepared_transaction.parent_head,
            envelope.chapter,
            envelope.observations,
        )
        return self._compile_with_entity_registry(
            envelope.candidates,
            envelope.observations,
            envelope.prepared_transaction.parent_head,
            envelope.scan_attestations,
            chapter=envelope.chapter,
        )

    def _compile_with_entity_registry(
        self,
        candidates: Iterable[FactCandidate],
        observations: Iterable[ReviewObservation],
        parent_head: str,
        scan_attestations: Iterable[ScanAttestation],
        *,
        chapter: int,
    ) -> PreparedTransaction:
        candidate_tuple = tuple(candidates)
        observation_tuple = tuple(observations)
        scan_tuple = tuple(scan_attestations)
        registry = build_approved_entity_registry(
            self.repository,
            parent_head,
            target_chapter=chapter,
        )
        plan = plan_entity_resolutions(candidate_tuple, registry)
        base_observations = (
            *observation_tuple,
            *plan.observations,
        )
        # Compile once to obtain normalized semantic fact keys.  This
        # provisional value is never persisted; it is only the typed input to
        # the parent-HEAD Active Slot Registry below.
        provisional = bind_entity_registry_to_transaction(
            compile_transaction(
                candidate_tuple,
                base_observations,
                parent_head,
                scan_attestations=scan_tuple,
            ),
            plan,
        )
        slot_observations, prior_bindings = self._slot_transition_plan(
            provisional,
            plan.candidates_by_digest,
            parent_head=parent_head,
            chapter=chapter,
        )
        combined_observations = (*base_observations, *slot_observations)
        self._validate_prior_fact_references(
            parent_head,
            chapter,
            combined_observations,
        )
        prepared = compile_transaction(
            candidate_tuple,
            combined_observations,
            parent_head,
            scan_attestations=scan_tuple,
        )
        prepared = bind_entity_registry_to_transaction(prepared, plan)
        if tuple(effect.fact_key for effect in prepared.effects) != tuple(
            effect.fact_key for effect in provisional.effects
        ):
            raise PreparedTransactionInvalid(
                "canon_v3_slot_plan_changed_during_full_recompile"
            )
        return self._bind_prior_facts(prepared, prior_bindings)

    @staticmethod
    def _legacy_fact_key(record: Mapping[str, Any]) -> str | None:
        fact = record.get("fact")
        if not isinstance(fact, Mapping):
            return None
        payload = fact.get("payload") if isinstance(fact.get("payload"), Mapping) else {}
        category = str(fact.get("category") or payload.get("kind") or "")
        subject = str(fact.get("subject") or payload.get("subject") or "")
        field_name = str(fact.get("field") or payload.get("attribute") or "")
        slot_id = str(fact.get("slot_id") or payload.get("slot_id") or "")
        family = ""
        slot: dict[str, str]
        if category in {"character_state", "character_state_changed"}:
            family = "character_state"
            slot = (
                {"slot_id": slot_id}
                if slot_id
                else {"subject": subject, "field": field_name}
            )
        elif category == "power_breakthrough":
            family = "character_state"
            slot = (
                {"slot_id": slot_id}
                if slot_id
                else {
                    "subject": subject,
                    "field": str(payload.get("system") or field_name or "realm"),
                }
            )
        elif category in {"relationship", "relationship_changed"}:
            family = FactKind.RELATIONSHIP_CHANGED.value
            slot = {
                "subject": subject,
                "object": str(payload.get("object") or field_name),
            }
        elif category == "world_rule_broken":
            if not slot_id:
                return None
            family = "rule_violation"
            slot = {"slot_id": slot_id}
        elif category in {"world_rule", "world_rule_revealed"}:
            if not slot_id:
                return None
            family = "world_rule"
            slot = {"slot_id": slot_id}
        elif category in {"artifact_obtained", "custody", "custody_changed"}:
            family = "custody"
            slot = {
                "item": str(
                    payload.get("item")
                    or payload.get("artifact")
                    or subject
                )
            }
        elif category in {"timeline", "timeline_observed"}:
            if not slot_id:
                return None
            family = "timeline"
            slot = {"slot_id": slot_id}
        elif category in {"knowledge", "knowledge_state_changed"}:
            if not slot_id:
                return None
            family = "knowledge"
            slot = {"slot_id": slot_id}
        elif category in {"presence", "presence_observed"}:
            family = FactKind.PRESENCE_OBSERVED.value
            slot = {"subject": subject}
        elif category in {
            "reader_promise",
            "promise_created",
            "promise_paid",
            "promise_paid_off",
        }:
            if not slot_id:
                return None
            family = "promise"
            slot = {"slot_id": slot_id}
        elif category in {"open_loop", "open_loop_created", "open_loop_closed"}:
            if not slot_id:
                return None
            family = "open_loop"
            slot = {"slot_id": slot_id}
        else:
            return None
        if not family or any(not value for value in slot.values()):
            return None
        return canonical_digest({"kind_family": family, "slot": slot})

    def _active_fact_slots(
        self,
        parent_head: str,
        chapter: int,
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        """Fold the exact parent HEAD into one current record per fact slot."""

        records = fact_record_index(self.repository, parent_head)
        channel_rank = {
            "knowledge_by_entity": 0,
            "presence": 0,
            "custody": 0,
            "rules": 0,
            "obligations": 0,
            "timeline": 0,
            "canonical_facts": 1,
            "hard_constraints": 2,
            "state_changes": 3,
            "lifecycle_history": 4,
            "presence_history": 4,
            "custody_history": 4,
            "information": 5,
        }
        legacy: dict[str, tuple[int, int, str, dict[str, Any]]] = {}
        chronology: list[tuple[int, int, int, str, dict[str, Any]]] = []
        lifecycle_categories = {
            "reader_promise",
            "promise_created",
            "promise_paid",
            "promise_paid_off",
            "open_loop",
            "open_loop_created",
            "open_loop_closed",
        }
        for digest, record in records.items():
            record_type = str(record.get("record_type") or "")
            if record_type == "legacy_fact":
                fact_key = self._legacy_fact_key(record)
                fact = record.get("fact")
                if fact_key is None or not isinstance(fact, Mapping):
                    continue
                locations = record.get("locations") or ()
                channels = {
                    str(item.get("channel") or "")
                    for item in locations
                    if isinstance(item, Mapping)
                }
                category = str(fact.get("category") or "")
                if category in lifecycle_categories and not channels.intersection(
                    {"obligations", "hard_constraints"}
                ):
                    continue
                if str(fact.get("status") or "active") == "resolved":
                    continue
                rank = min((channel_rank.get(item, 99) for item in channels), default=99)
                source_chapter = int(fact.get("source_chapter") or 0)
                candidate = (rank, -source_chapter, digest, dict(record))
                previous = legacy.get(fact_key)
                if previous is None or candidate[:3] < previous[:3]:
                    legacy[fact_key] = candidate
            elif record_type == "v3_effect":
                record_chapter = int(record.get("chapter") or 0)
                fact_key = str(record.get("fact_key") or "")
                if fact_key and record_chapter < int(chapter):
                    chronology.append(
                        (
                            record_chapter,
                            int(record.get("revision") or 0),
                            int(record.get("effect_index") or 0),
                            digest,
                            dict(record),
                        )
                    )
        active = {
            fact_key: (candidate[2], candidate[3])
            for fact_key, candidate in legacy.items()
        }
        for _ch, _rev, _index, digest, record in sorted(chronology):
            active[str(record["fact_key"])] = (digest, record)
        return active

    @staticmethod
    def _record_claim(record: Mapping[str, Any]) -> Mapping[str, Any]:
        payload = (
            record.get("fact")
            if str(record.get("record_type") or "") == "legacy_fact"
            else record.get("claim")
        )
        return payload if isinstance(payload, Mapping) else {}

    @staticmethod
    def _prior_display_field(
        record: Mapping[str, Any], field: str
    ) -> str | None:
        payload = CanonV3Service._record_claim(record)
        nested = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else {}
        candidates: dict[str, tuple[Any, ...]] = {
            "rule": (payload.get("rule"), nested.get("rule"), payload.get("value")),
            "knowledge": (
                payload.get("knowledge"),
                payload.get("canonical_claim"),
                payload.get("content"),
                nested.get("knowledge"),
                nested.get("canonical_claim"),
                nested.get("content"),
                payload.get("value"),
            ),
            "event": (
                payload.get("event"),
                nested.get("event"),
                payload.get("value"),
                payload.get("subject"),
            ),
            "promise": (
                payload.get("promise"),
                nested.get("promise"),
                nested.get("content"),
                payload.get("value"),
            ),
            "loop": (
                payload.get("loop"),
                nested.get("loop"),
                nested.get("content"),
                nested.get("description"),
                payload.get("value"),
            ),
            "canonical_field": (
                payload.get("canonical_field"),
                payload.get("attribute"),
                payload.get("system"),
                nested.get("canonical_field"),
                nested.get("attribute"),
                nested.get("system"),
                payload.get("field"),
            ),
        }
        for value in candidates.get(field, (payload.get(field),)):
            text = str(value or "").strip()
            if text:
                return text
        return None

    @staticmethod
    def _prior_transition_value(record: Mapping[str, Any]) -> str | None:
        payload = CanonV3Service._record_claim(record)
        nested = payload.get("payload") if isinstance(payload.get("payload"), Mapping) else {}
        for key in (
            "after",
            "to_holder",
            "owner",
            "holder_id",
            "state",
            "presence",
        ):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for key in (
            "after",
            "to_holder",
            "owner",
            "holder_id",
            "state",
            "presence",
        ):
            value = nested.get(key)
            if value not in (None, ""):
                return str(value)
        value = payload.get("value")
        return str(value) if value not in (None, "") else None

    def _slot_transition_plan(
        self,
        provisional: PreparedTransaction,
        normalized_candidates: Mapping[str, FactCandidate],
        *,
        parent_head: str,
        chapter: int,
    ) -> tuple[
        tuple[ReviewObservation, ...],
        dict[str, tuple[str, str, dict[str, Any]]],
    ]:
        current: dict[str, tuple[str, str, dict[str, Any]]] = {
            fact_key: ("fact", digest, record)
            for fact_key, (digest, record) in self._active_fact_slots(
                parent_head, chapter
            ).items()
        }
        observations: list[ReviewObservation] = []
        bindings: dict[str, tuple[str, str, dict[str, Any]]] = {}
        slot_controlled = {
            FactKind.CHARACTER_STATE_CHANGED,
            FactKind.POWER_BREAKTHROUGH,
            FactKind.WORLD_RULE_REVEALED,
            FactKind.WORLD_RULE_BROKEN,
            FactKind.TIMELINE_OBSERVED,
            FactKind.KNOWLEDGE_STATE_CHANGED,
            FactKind.PROMISE_CREATED,
            FactKind.PROMISE_PAID_OFF,
            FactKind.OPEN_LOOP_CREATED,
            FactKind.OPEN_LOOP_CLOSED,
        }
        terminal = {
            FactKind.PROMISE_PAID_OFF,
            FactKind.OPEN_LOOP_CLOSED,
        }
        lifecycle_created = {
            FactKind.PROMISE_CREATED,
            FactKind.OPEN_LOOP_CREATED,
        }
        lifecycle_terminal = {
            FactKind.PROMISE_PAID_OFF,
            FactKind.OPEN_LOOP_CLOSED,
        }
        for effect in provisional.effects:
            candidate = normalized_candidates.get(effect.candidate_digest)
            if candidate is None:
                raise PreparedTransactionInvalid(
                    "canon_v3_slot_candidate_missing"
                )
            kind = FactKind(effect.claim.kind)
            if (
                kind
                in {
                    FactKind.CHARACTER_STATE_CHANGED,
                    FactKind.POWER_BREAKTHROUGH,
                }
                and getattr(candidate.claim, "canonical_field", None) is not None
            ):
                raise PreparedTransactionInvalid(
                    "canon_v3_state_canonical_field_is_compiler_owned"
                )
            prior = current.get(effect.fact_key)
            explicit_slot = getattr(candidate.claim, "slot_id", None)
            default_slot = (
                default_semantic_slot_id(
                    candidate.claim,
                    instance_seed=effect.candidate_digest,
                )
                if kind in slot_controlled
                else None
            )
            if kind == FactKind.WORLD_RULE_BROKEN:
                if explicit_slot is not None and explicit_slot != default_slot:
                    raise PreparedTransactionInvalid(
                        "canon_v3_rule_violation_slot_must_be_candidate_bound"
                    )
                rule_slot_id = str(
                    getattr(effect.claim, "rule_slot_id", None) or ""
                )
                if not rule_slot_id:
                    raise PreparedTransactionInvalid(
                        "canon_v3_rule_violation_requires_rule_slot_id"
                    )
                rule_fact_key = canonical_digest(
                    {
                        "kind_family": "world_rule",
                        "slot": {"slot_id": rule_slot_id},
                    }
                )
                prior = current.get(rule_fact_key)
                if prior is None:
                    raise PreparedTransactionInvalid(
                        "canon_v3_rule_slot_not_in_parent_head:" + rule_slot_id
                    )
            if kind in lifecycle_created and explicit_slot != default_slot:
                if explicit_slot is not None:
                    raise PreparedTransactionInvalid(
                        "canon_v3_lifecycle_created_slot_must_be_deterministic:"
                        + str(explicit_slot)
                    )
            elif kind in slot_controlled and prior is None:
                if kind in terminal:
                    raise PreparedTransactionInvalid(
                        "canon_v3_terminal_slot_not_active_in_parent_head:"
                        + str(getattr(effect.claim, "slot_id", ""))
                    )
                if explicit_slot is not None and explicit_slot != default_slot:
                    code = (
                        "canon_v3_rule_slot_not_in_parent_head:"
                        if kind == FactKind.WORLD_RULE_REVEALED
                        else "canon_v3_slot_not_in_parent_head:"
                    )
                    raise PreparedTransactionInvalid(
                        code + str(explicit_slot)
                    )
            if prior is None:
                if kind in {
                    FactKind.CHARACTER_STATE_CHANGED,
                    FactKind.POWER_BREAKTHROUGH,
                }:
                    subject = str(getattr(effect.claim, "subject", "") or "")
                    related: set[str] = set()
                    for prior_type, digest, record in current.values():
                        if prior_type != "fact":
                            continue
                        prior_payload = self._record_claim(record)
                        prior_category = str(
                            prior_payload.get("kind")
                            or prior_payload.get("category")
                            or ""
                        )
                        prior_subject = str(
                            prior_payload.get("subject")
                            or (
                                prior_payload.get("payload", {}).get("subject")
                                if isinstance(prior_payload.get("payload"), Mapping)
                                else ""
                            )
                            or ""
                        )
                        if (
                            prior_subject == subject
                            and prior_category
                            in {
                                "character_state",
                                FactKind.CHARACTER_STATE_CHANGED.value,
                                FactKind.POWER_BREAKTHROUGH.value,
                            }
                        ):
                            related.add(digest)
                    if related:
                        observations.append(
                            ReviewObservation(
                                observation_id=(
                                    "state-slot-choice-"
                                    + canonical_digest(
                                        {
                                            "candidate_digest": effect.candidate_digest,
                                            "related_prior_facts": sorted(related),
                                        }
                                    )[:24]
                                ),
                                candidate_id=candidate.candidate_id,
                                kind=ObservationKind.CHECKPOINT,
                                level=ReviewLevel.HUMAN_REQUIRED,
                                reason=(
                                    "该人物在 N-1 已有其它状态槽；请确认这是独立字段，"
                                    "若只是同一状态的不同措辞，应复制对应 slot_id"
                                ),
                                prior_fact_digests=tuple(sorted(related)),
                            )
                        )
                current[effect.fact_key] = (
                    "effect",
                    effect.effect_id,
                    effect.model_dump(mode="json"),
                )
                continue
            prior_type, prior_digest, prior_record = prior
            prior_payload = self._record_claim(prior_record)
            prior_kind = str(
                prior_payload.get("kind") or prior_payload.get("category") or ""
            )
            if kind == FactKind.WORLD_RULE_BROKEN and prior_kind not in {
                FactKind.WORLD_RULE_REVEALED.value,
                "world_rule",
            }:
                raise PreparedTransactionInvalid(
                    "canon_v3_rule_violation_prior_is_not_rule"
                )
            if kind in lifecycle_terminal:
                expected = (
                    {FactKind.PROMISE_CREATED.value, "reader_promise"}
                    if kind == FactKind.PROMISE_PAID_OFF
                    else {FactKind.OPEN_LOOP_CREATED.value, "open_loop"}
                )
                if prior_kind not in expected:
                    raise PreparedTransactionInvalid(
                        "canon_v3_lifecycle_slot_not_active_in_parent_head:"
                        + str(getattr(effect.claim, "slot_id", ""))
                    )
            if kind in {FactKind.PROMISE_CREATED, FactKind.PROMISE_PAID_OFF}:
                prior_promisor = str(
                    prior_payload.get("promisor")
                    or prior_payload.get("subject")
                    or ""
                ).strip()
                current_promisor = str(getattr(effect.claim, "promisor", "") or "")
                if prior_promisor and current_promisor != prior_promisor:
                    raise PreparedTransactionInvalid(
                        "canon_v3_promise_promisor_mismatch"
                    )
            if kind == FactKind.KNOWLEDGE_STATE_CHANGED:
                prior_subject = str(
                    prior_payload.get("subject")
                    or (
                        prior_payload.get("payload", {}).get("subject")
                        if isinstance(prior_payload.get("payload"), Mapping)
                        else ""
                    )
                    or ""
                ).strip()
                if prior_subject and prior_subject != str(effect.claim.subject):
                    raise PreparedTransactionInvalid(
                        "canon_v3_knowledge_subject_mismatch"
                    )
            if kind in {
                FactKind.CHARACTER_STATE_CHANGED,
                FactKind.POWER_BREAKTHROUGH,
            }:
                prior_subject = str(
                    prior_payload.get("subject")
                    or (
                        prior_payload.get("payload", {}).get("subject")
                        if isinstance(prior_payload.get("payload"), Mapping)
                        else ""
                    )
                    or ""
                ).strip()
                if prior_subject and prior_subject != str(effect.claim.subject):
                    raise PreparedTransactionInvalid(
                        "canon_v3_character_state_subject_mismatch"
                    )
            bindings[effect.candidate_digest] = (
                prior_type,
                prior_digest,
                prior_record,
            )
            reason = "该候选将替换或转移当前事实链中同一稳定事实槽"
            expected_before = None
            supplied_before = None
            if kind in {
                FactKind.CHARACTER_STATE_CHANGED,
                FactKind.RELATIONSHIP_CHANGED,
                FactKind.POWER_BREAKTHROUGH,
            }:
                supplied_before = getattr(effect.claim, "before", None)
                expected_before = self._prior_transition_value(prior_record)
            elif kind in {FactKind.ARTIFACT_OBTAINED, FactKind.CUSTODY_CHANGED}:
                supplied_before = getattr(effect.claim, "from_holder", None)
                expected_before = self._prior_transition_value(prior_record)
            if (
                supplied_before not in (None, "")
                and expected_before not in (None, "")
                and str(supplied_before) != str(expected_before)
            ):
                reason += (
                    f"；候选前态 {supplied_before!r} 与当前链前值 "
                    f"{expected_before!r} 不一致，需人工确认是否存在未记录过渡"
                )
            if (
                kind in CHECKPOINT_KINDS
                or kind in lifecycle_terminal
                or kind in lifecycle_created
            ):
                observations.append(
                    ReviewObservation(
                        observation_id=(
                            "slot-transition-"
                            + canonical_digest(
                                {
                                    "candidate_digest": effect.candidate_digest,
                                    "fact_key": effect.fact_key,
                                    "prior_fact_digest": prior_digest,
                                    "reason": reason,
                                }
                            )[:24]
                        ),
                        candidate_id=candidate.candidate_id,
                        kind=ObservationKind.CHECKPOINT,
                        level=ReviewLevel.HUMAN_REQUIRED,
                        reason=reason,
                        prior_fact_digests=(
                            (prior_digest,) if prior_type == "fact" else ()
                        ),
                    )
                )
            current[effect.fact_key] = (
                "effect",
                effect.effect_id,
                effect.model_dump(mode="json"),
            )
        return tuple(observations), bindings

    def _bind_prior_facts(
        self,
        prepared: PreparedTransaction,
        bindings: Mapping[str, tuple[str, str, dict[str, Any]]],
    ) -> PreparedTransaction:
        inherit_by_kind = {
            FactKind.CHARACTER_STATE_CHANGED: "canonical_field",
            FactKind.POWER_BREAKTHROUGH: "canonical_field",
            FactKind.WORLD_RULE_BROKEN: "rule",
            FactKind.TIMELINE_OBSERVED: "event",
            FactKind.KNOWLEDGE_STATE_CHANGED: "knowledge",
            FactKind.PROMISE_PAID_OFF: "promise",
            FactKind.OPEN_LOOP_CLOSED: "loop",
        }
        effects: list[CanonEffect] = []
        final_id_by_provisional_id: dict[str, str] = {}
        for effect in prepared.effects:
            prior = bindings.get(effect.candidate_digest)
            claim_payload = effect.claim.model_dump(mode="python")
            inherited_fields: dict[str, str] = {}
            prior_digest: str | None = None
            prior_effect_id: str | None = None
            if prior is not None:
                prior_type, prior_reference, prior_record = prior
                if prior_type == "fact":
                    prior_digest = prior_reference
                    exact_prior_reference = prior_reference
                elif prior_type == "effect":
                    prior_effect_id = final_id_by_provisional_id.get(
                        prior_reference
                    )
                    if prior_effect_id is None:
                        raise PreparedTransactionInvalid(
                            "canon_v3_intra_transaction_prior_effect_order_invalid"
                        )
                    exact_prior_reference = prior_effect_id
                else:  # pragma: no cover - internal closed set
                    raise PreparedTransactionInvalid(
                        "canon_v3_prior_reference_type_invalid"
                    )
                field = inherit_by_kind.get(FactKind(effect.claim.kind))
                if field and (
                    field == "canonical_field" or not claim_payload.get(field)
                ):
                    inherited = self._prior_display_field(prior_record, field)
                    if not inherited:
                        raise PreparedTransactionInvalid(
                            "canon_v3_prior_display_field_missing:" + field
                        )
                    claim_payload[field] = inherited
                    inherited_fields[field] = exact_prior_reference
            claim = type(effect.claim).model_validate(claim_payload)
            payload = {
                "source_order": effect.source_order,
                "candidate_digest": effect.candidate_digest,
                "fact_key": effect.fact_key,
                "claim": claim.model_dump(mode="json"),
                "prior_fact_digest": prior_digest,
                "prior_effect_id": prior_effect_id,
                "inherited_fields": inherited_fields,
                "source_digests": list(effect.source_digests),
                "support_map": {
                    key: list(value) for key, value in effect.support_map.items()
                },
            }
            effect_id = canonical_digest(
                {"schema_version": "canon-v3/canon-effect/v2", **payload}
            )
            effects.append(CanonEffect(effect_id=effect_id, **payload))
            final_id_by_provisional_id[effect.effect_id] = effect_id
        effects.sort(key=lambda item: (item.source_order, item.effect_id))
        transaction_payload = prepared.model_dump(
            mode="json", exclude={"transaction_digest"}
        )
        transaction_payload["effects"] = [
            effect.model_dump(mode="json") for effect in effects
        ]
        transaction_payload["transaction_digest"] = canonical_digest(
            transaction_payload
        )
        return PreparedTransaction.model_validate(transaction_payload)

    def _cases(self, envelope: PreparedEnvelope) -> tuple[ReviewCase, ...]:
        candidates = self._candidate_map(envelope)
        effects: dict[str, list[str]] = {}
        for effect in envelope.prepared_transaction.effects:
            effects.setdefault(effect.candidate_digest, []).append(effect.effect_id)
        resolutions: dict[str, list[str]] = {}
        for resolution in envelope.prepared_transaction.entity_resolutions:
            resolutions.setdefault(resolution.candidate_digest, []).append(
                resolution.resolution_digest
            )
        cases: list[ReviewCase] = []
        for requirement in envelope.prepared_transaction.requirements:
            candidate = candidates.get(requirement.candidate_digest)
            if candidate is None:
                raise PreparedTransactionInvalid(
                    "canon_v3_requirement_candidate_missing"
                )
            cases.append(
                review_case_from_requirement(
                    requirement,
                    chapter=envelope.chapter,
                    chapter_digest=envelope.chapter_binding.sha256,
                    parent_head=envelope.prepared_transaction.parent_head,
                    policy_version=envelope.prepared_transaction.policy_version,
                    transaction_digest=envelope.prepared_transaction.transaction_digest,
                    effect_digests=effects.get(requirement.candidate_digest, ()),
                    candidate=candidate,
                    entity_registry_digest=(
                        envelope.prepared_transaction.entity_registry_digest
                    ),
                    entity_resolution_digests=resolutions.get(
                        requirement.candidate_digest, ()
                    ),
                )
            )
        return tuple(cases)

    def _validate_prior_fact_references(
        self,
        parent_head: str,
        chapter: int,
        observations: Iterable[ReviewObservation],
    ) -> dict[str, dict[str, Any]]:
        index = {
            digest: record
            for digest, record in fact_record_index(
                self.repository,
                parent_head,
            ).items()
            if str(record.get("record_type") or "").startswith("legacy_")
            or int(record.get("chapter") or 0) < int(chapter)
        }
        for observation in observations:
            if (
                observation.kind == ObservationKind.CONFIRMED_CONFLICT
                and not observation.prior_fact_digests
            ):
                raise PreparedTransactionInvalid(
                    "canon_v3_confirmed_conflict_requires_prior_fact"
                )
            unknown = sorted(set(observation.prior_fact_digests) - set(index))
            if unknown:
                raise PreparedTransactionInvalid(
                    "canon_v3_prior_fact_not_in_parent_head:" + ",".join(unknown)
                )
        return index

    def _workflow_case_payloads(
        self,
        *,
        envelope: PreparedEnvelope,
        transaction_hash: str,
        cases: Iterable[ReviewCase],
        candidate_revisions: Iterable[Any] = (),
        stage_digest: str | None = None,
        decision_heads: Mapping[str, ReviewDecision] | None = None,
    ) -> list[dict[str, Any]]:
        candidates = self._candidate_map(envelope)
        for revision in candidate_revisions:
            candidate = FactCandidate.model_validate(revision.payload())
            digest = candidate_digest(candidate)
            if digest != revision.candidate_digest:
                raise PreparedTransactionInvalid(
                    "canon_v3_review_material_revision_digest_mismatch"
                )
            candidates[digest] = candidate
        prior_records = self._validate_prior_fact_references(
            envelope.prepared_transaction.parent_head,
            envelope.chapter,
            envelope.observations,
        )
        effects_by_id = {
            effect.effect_id: effect
            for effect in envelope.prepared_transaction.effects
        }
        payloads: list[dict[str, Any]] = []
        for case in cases:
            candidate = candidates.get(case.context.candidate_digest)
            if candidate is None:
                raise PreparedTransactionInvalid(
                    "canon_v3_review_material_candidate_missing"
                )
            material = {
                "schema_version": "canon-v3/review-material/v1",
                "transaction_hash": transaction_hash,
                "transaction_digest": envelope.prepared_transaction.transaction_digest,
                "parent_head": envelope.prepared_transaction.parent_head,
                "chapter": envelope.chapter,
                "chapter_binding": envelope.chapter_binding.model_dump(mode="json"),
                "candidate_digest": case.context.candidate_digest,
                "candidate": candidate.model_dump(mode="json"),
                "compiled_effects": [
                    effect.model_dump(mode="json")
                    for effect in envelope.prepared_transaction.effects
                    if effect.candidate_digest == case.context.candidate_digest
                ],
                "prior_effects": [
                    effects_by_id[effect.prior_effect_id].model_dump(mode="json")
                    for effect in envelope.prepared_transaction.effects
                    if effect.candidate_digest == case.context.candidate_digest
                    and effect.prior_effect_id is not None
                ],
                "entity_registry_digest": case.context.entity_registry_digest,
                "entity_resolution_digests": list(
                    case.context.entity_resolution_digests
                ),
                "prior_facts": [
                    prior_records[digest]
                    for digest in case.context.prior_fact_hashes
                ],
            }
            material["material_digest"] = canonical_digest(material)
            decision_head = (decision_heads or {}).get(case.case_key)
            payloads.append(
                {
                    **case_to_dict(case),
                    "stage_digest": stage_digest,
                    "decision_head_hash": (
                        decision_head.decision_hash
                        if decision_head is not None
                        else None
                    ),
                    "semantic_claim_digest": semantic_claim_digest(candidate),
                    "lineage_key": lineage_key(
                        envelope.chapter_binding.sha256,
                        candidate,
                    ),
                    "review_material": material,
                }
            )
        return payloads

    def _load_decisions(
        self,
        pointer: StagingPointer,
        envelope: PreparedEnvelope,
    ) -> tuple[ReviewDecision, ...]:
        decisions: list[ReviewDecision] = []
        cases = {case.case_key: case for case in self._cases(envelope)}
        materials = {
            item["case_key"]: item
            for item in self._workflow_case_payloads(
                envelope=envelope,
                transaction_hash=pointer.transaction_hash,
                cases=cases.values(),
            )
        }
        for object_hash in pointer.decision_hashes:
            payload = self.repository.read_decision(object_hash)
            schema_version = payload.get("schema_version")
            v1_fields = {
                "schema_version", "transaction_hash", "chapter", "decision"
            }
            v2_fields = {
                "schema_version",
                "transaction_hash",
                "chapter",
                "stage_digest_before",
                "target_digest",
                "material_digest",
                "expected_decision_head_hash",
                "lineage_key",
                "decision",
            }
            expected_fields = (
                v1_fields
                if schema_version == DECISION_ENVELOPE_SCHEMA_V1
                else v2_fields
            )
            if set(payload) != expected_fields:
                raise PreparedTransactionInvalid(
                    "canon_v3_decision_envelope_fields_invalid"
                )
            if schema_version not in {
                DECISION_ENVELOPE_SCHEMA_V1,
                DECISION_ENVELOPE_SCHEMA,
            }:
                raise PreparedTransactionInvalid(
                    "canon_v3_decision_envelope_schema_invalid"
                )
            if payload.get("transaction_hash") != pointer.transaction_hash:
                raise PreparedTransactionInvalid(
                    "canon_v3_decision_transaction_mismatch"
                )
            if payload.get("chapter") != envelope.chapter:
                raise PreparedTransactionInvalid("canon_v3_decision_chapter_mismatch")
            decision = decision_from_dict(payload.get("decision"))
            if schema_version == DECISION_ENVELOPE_SCHEMA:
                expected_previous = decision.supersedes or None
                if payload.get("expected_decision_head_hash") != expected_previous:
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_head_binding_mismatch"
                    )
                stage_before = str(payload.get("stage_digest_before") or "")
                if (
                    len(stage_before) != 64
                    or any(
                        char not in "0123456789abcdef"
                        for char in stage_before
                    )
                ):
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_stage_binding_invalid"
                    )
                case = cases.get(decision.case_key)
                material = materials.get(decision.case_key)
                if case is None or material is None:
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_case_material_missing"
                    )
                candidate = self._candidate_map(envelope).get(
                    case.context.candidate_digest
                )
                if candidate is None:
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_candidate_missing"
                    )
                if payload.get("target_digest") != case.target_digest:
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_target_digest_mismatch"
                    )
                if payload.get("material_digest") != material[
                    "review_material"
                ]["material_digest"]:
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_material_digest_mismatch"
                    )
                if payload.get("lineage_key") != lineage_key(
                    envelope.chapter_binding.sha256,
                    candidate,
                ):
                    raise PreparedTransactionInvalid(
                        "canon_v3_decision_lineage_key_mismatch"
                    )
            decisions.append(decision)
        return tuple(decisions)

    @staticmethod
    def _decision_heads(
        decisions: Iterable[ReviewDecision],
    ) -> dict[str, ReviewDecision]:
        heads: dict[str, ReviewDecision] = {}
        for decision in decisions:
            previous = heads.get(decision.case_key)
            if previous is None or decision.revision > previous.revision:
                heads[decision.case_key] = decision
            elif decision.revision == previous.revision and (
                decision.decision_hash != previous.decision_hash
            ):
                raise PreparedTransactionInvalid(
                    "canon_v3_multiple_decision_heads"
                )
        return heads

    def _negative_head_hashes(
        self, object_hashes: Iterable[str]
    ) -> tuple[str, ...]:
        """Return immutable object hashes of authoritative negative heads."""

        heads: dict[tuple[str, str], tuple[ReviewDecision, str]] = {}
        for object_hash in object_hashes:
            wrapper = self.repository.read_decision(str(object_hash))
            decision = decision_from_dict(wrapper.get("decision"))
            key = (str(wrapper.get("transaction_hash") or ""), decision.case_key)
            previous = heads.get(key)
            if previous is None or decision.revision > previous[0].revision:
                heads[key] = (decision, str(object_hash))
            elif (
                decision.revision == previous[0].revision
                and decision.decision_hash != previous[0].decision_hash
            ):
                raise PreparedTransactionInvalid(
                    "canon_v3_lineage_decision_head_fork"
                )
        negative = {
            ReviewAction.OMIT,
            ReviewAction.REWRITE,
            ReviewAction.CORRECT,
        }
        return tuple(
            sorted(
                object_hash
                for decision, object_hash in heads.values()
                if decision.action in negative
            )
        )

    def _enforce_negative_lineage(
        self,
        lineage_hashes: Iterable[str],
        *,
        chapter: int,
        chapter_digest: str,
        candidates: Iterable[FactCandidate],
    ) -> tuple[ReviewObservation, ...]:
        supplied = tuple(candidates)
        supplied_digests = {
            candidate_digest(candidate): candidate for candidate in supplied
        }
        supplied_by_id = {
            candidate.candidate_id: candidate for candidate in supplied
        }
        reconsiderations: dict[str, ReviewObservation] = {}
        for object_hash in self._negative_head_hashes(lineage_hashes):
            wrapper = self.repository.read_decision(object_hash)
            if int(wrapper.get("chapter") or 0) != int(chapter):
                continue
            decision = decision_from_dict(wrapper.get("decision"))
            if decision.context.chapter_digest != chapter_digest:
                continue
            original_digest = decision.context.candidate_digest
            if original_digest in supplied_digests:
                raise PreparedTransactionInvalid(
                    "canon_v3_negative_adjudication_candidate_reintroduced:"
                    + original_digest
                )
            old_transaction = str(wrapper.get("transaction_hash") or "")
            old_envelope = self._load_envelope(old_transaction)
            original = next(
                (
                    candidate
                    for candidate in old_envelope.candidates
                    if candidate_digest(candidate) == original_digest
                ),
                None,
            )
            if original is None:
                raise PreparedTransactionInvalid(
                    "canon_v3_lineage_original_candidate_missing"
                )
            replacement = supplied_by_id.get(original.candidate_id)
            semantic_matches = [
                candidate
                for candidate in supplied
                if semantic_claim_digest(candidate)
                == semantic_claim_digest(original)
            ]
            if decision.action is ReviewAction.REWRITE:
                raise PreparedTransactionInvalid(
                    "canon_v3_rewrite_lineage_requires_changed_chapter_bytes:"
                    + decision.context.chapter_digest
                )
            if decision.action is ReviewAction.CORRECT:
                expected = (
                    decision.correction.candidate_digest
                    if decision.correction is not None
                    else ""
                )
                if replacement is None or candidate_digest(replacement) != expected:
                    raise PreparedTransactionInvalid(
                        "canon_v3_correction_lineage_replacement_mismatch:"
                        + original.candidate_id
                    )
            if decision.action is ReviewAction.OMIT:
                # Repackaging the same claim with a different span/source set
                # cannot erase an author's omission.  We conservatively create
                # a new human checkpoint; it may be approved, but it is never
                # machine-ready.
                for candidate in semantic_matches:
                    semantic = semantic_claim_digest(candidate)
                    observation_id = (
                        "lineage-reconsideration-"
                        + canonical_digest(
                            {
                                "negative_decision": object_hash,
                                "semantic_claim_digest": semantic,
                                "candidate_digest": candidate_digest(candidate),
                            }
                        )[:24]
                    )
                    reconsiderations[observation_id] = ReviewObservation(
                        observation_id=observation_id,
                        candidate_id=candidate.candidate_id,
                        kind=ObservationKind.CHECKPOINT,
                        level=ReviewLevel.HUMAN_REQUIRED,
                        reason=(
                            "lineage_reconsideration: previously omitted semantic "
                            "claim has changed evidence"
                        ),
                    )
        return tuple(
            reconsiderations[key] for key in sorted(reconsiderations)
        )

    def _transaction_is_current_commit(
        self,
        transaction_hash: str,
    ) -> bool:
        return any(
            commit.get("transaction_hash") == transaction_hash
            for _commit_hash, commit in self.repository.current_commits()
        )

    def _historical_chapter_lineage(self, chapter: int) -> tuple[str, ...]:
        """Collect negative tombstones from every reachable manifest ancestor."""

        head = self.repository.current_head(validate=True)
        seen: set[str] = set()
        lineage: set[str] = set()
        while head is not None:
            if head in seen:
                raise PreparedTransactionInvalid(
                    "canon_v3_manifest_lineage_cycle"
                )
            seen.add(head)
            manifest = self.repository.read_manifest(
                head, validate_references=True
            )
            for entry in manifest.get("chapters") or ():
                if int(entry.get("chapter") or 0) != int(chapter):
                    continue
                commit = self.repository.read_commit(
                    str(entry.get("commit_hash") or "")
                )
                lineage.update(
                    str(item)
                    for item in commit.get("lineage_decision_hashes") or ()
                )
                lineage.update(
                    self._negative_head_hashes(
                        commit.get("decision_hashes") or ()
                    )
                )
            metadata = manifest.get("genesis_metadata")
            receipt = (
                metadata.get("recertification")
                if isinstance(metadata, Mapping)
                else None
            )
            recertified_lineage = (
                receipt.get("semantic_negative_lineage")
                if isinstance(receipt, Mapping)
                and receipt.get("schema_version")
                == "canon-v3/legacy-recertification-receipt/v1"
                else None
            )
            if isinstance(recertified_lineage, Mapping):
                hashes = recertified_lineage.get(str(int(chapter))) or ()
                lineage.update(self._negative_head_hashes(hashes))
            parent = manifest.get("parent_head_hash")
            head = str(parent) if parent else None
        return tuple(sorted(lineage))

    def prepare(self, raw_batch: Mapping[str, Any] | ChapterProposalBatch) -> dict[str, Any]:
        batch = (
            raw_batch
            if isinstance(raw_batch, ChapterProposalBatch)
            else ChapterProposalBatch.model_validate(raw_batch)
        )
        binding = ChapterContentBinding.model_validate(
            require_chapter_binding(
                self.project_root,
                batch.chapter,
                batch.chapter_binding,
            )
        )
        if binding != batch.chapter_binding:
            raise PreparedTransactionInvalid("canon_v3_binding_not_canonical")
        verify_all_candidate_sources(
            self.project_root,
            binding,
            batch.candidates,
            active_author_axiom_source_keys=(
                self._active_author_axiom_source_keys(batch.parent_head)
            ),
        )
        self._validate_scan_attestations(batch)

        with self.staging_lock:
            from .staging_authority import (
                AUTHOR_AXIOM_STAGING_RELATIVE_PATH,
                assert_single_authoritative_staging,
            )

            assert_single_authoritative_staging(self.project_root)
            if (
                self.project_root / AUTHOR_AXIOM_STAGING_RELATIVE_PATH
            ).is_file():
                raise ActiveTransactionError(
                    "canon_v3_author_axiom_staging_blocks_chapter_prepare"
                )
            # Proposal clients consume the same public authority used by CLI,
            # gates, reports, context, Skills, and Dashboard.  Import lazily to
            # avoid a module cycle (WorkflowAuthority itself lazily imports
            # this engine service).
            from ..workflow_authority import normalize_workflow_snapshot

            authority = normalize_workflow_snapshot(
                self.workflow_snapshot()
            )
            if batch.parent_head != authority.get("head_hash"):
                raise PreparedTransactionInvalid(
                    "canon_v3_proposal_parent_head_mismatch"
                )
            if batch.workflow_digest != authority.get("workflow_digest"):
                raise PreparedTransactionInvalid(
                    "canon_v3_proposal_workflow_digest_mismatch"
                )
            if (
                batch.author_axiom_digest
                != authority.get("author_axiom_digest")
            ):
                raise PreparedTransactionInvalid(
                    "canon_v3_proposal_author_axiom_digest_mismatch"
                )
            authoritative_registry_digest = build_approved_entity_registry(
                self.repository,
                batch.parent_head,
                target_chapter=batch.chapter,
            ).registry_digest
            if batch.entity_registry_digest != authoritative_registry_digest:
                raise PreparedTransactionInvalid(
                    "canon_v3_proposal_entity_registry_digest_mismatch"
                )
            if batch.expected_stage_digest != authority.get("stage_digest"):
                raise ActiveTransactionError(
                    "canon_v3_prepare_expected_stage_mismatch"
                )
            parent_head = self._ensure_initialized_for_prepare(batch.chapter)
            self._assert_chapter_sequence(parent_head, batch.chapter)
            self._validate_prior_fact_references(
                parent_head,
                batch.chapter,
                batch.observations,
            )
            existing = self._read_staging_unlocked()
            lineage_hashes: set[str] = set(
                self._historical_chapter_lineage(batch.chapter)
            )
            active_commit = next(
                (
                    commit
                    for _commit_hash, commit in self.repository.current_commits()
                    if int(commit.get("chapter") or 0) == batch.chapter
                ),
                None,
            )
            if active_commit is not None:
                lineage_hashes.update(
                    str(item)
                    for item in active_commit.get("lineage_decision_hashes") or ()
                )
                lineage_hashes.update(
                    self._negative_head_hashes(
                        active_commit.get("decision_hashes") or ()
                    )
                )
            if existing is not None:
                previous = self._load_envelope(existing.transaction_hash)
                if (
                    previous.chapter != batch.chapter
                    and not self._transaction_is_current_commit(
                        existing.transaction_hash
                    )
                ):
                    raise ActiveTransactionError(
                        f"canon_v3_active_transaction_chapter:{previous.chapter}"
                    )
                if previous.chapter == batch.chapter:
                    lineage_hashes.update(existing.lineage_decision_hashes)
                    lineage_hashes.update(
                        self._negative_head_hashes(existing.decision_hashes)
                    )
                    try:
                        previous_reduction = reduce_review(
                            previous.candidates,
                            self._cases(previous),
                            self._load_decisions(existing, previous),
                        )
                    except (PreparedTransactionInvalid, ValueError):
                        previous_reduction = None
                    if previous_reduction is not None and previous_reduction.corrections:
                        supplied = {
                            candidate_digest(candidate) for candidate in batch.candidates
                        }
                        required_corrections = {
                            correction.candidate_digest
                            for correction in previous_reduction.corrections
                        }
                        if not required_corrections.issubset(supplied):
                            raise PreparedTransactionInvalid(
                                "canon_v3_corrected_candidates_missing_from_reprepare"
                            )

            lineage_observations = self._enforce_negative_lineage(
                lineage_hashes,
                chapter=batch.chapter,
                chapter_digest=binding.sha256,
                candidates=batch.candidates,
            )
            observations_by_id = {
                observation.observation_id: observation
                for observation in batch.observations
            }
            for observation in lineage_observations:
                previous = observations_by_id.get(observation.observation_id)
                if previous is not None and previous != observation:
                    raise PreparedTransactionInvalid(
                        "canon_v3_lineage_observation_id_conflict"
                    )
                observations_by_id[observation.observation_id] = observation
            effective_observations = tuple(
                observations_by_id[key] for key in sorted(observations_by_id)
            )

            prepared = self._compile_with_entity_registry(
                batch.candidates,
                effective_observations,
                parent_head,
                batch.scan_attestations,
                chapter=batch.chapter,
            )
            if prepared.entity_registry_digest != batch.entity_registry_digest:
                raise PreparedTransactionInvalid(
                    "canon_v3_compiled_entity_registry_digest_mismatch"
                )
            envelope = PreparedEnvelope(
                chapter=batch.chapter,
                chapter_binding=binding,
                prepared_transaction=prepared,
                candidates=batch.candidates,
                observations=effective_observations,
                scan_attestations=batch.scan_attestations,
                source_workflow_digest=batch.workflow_digest,
                author_axiom_digest=batch.author_axiom_digest,
            )
            transaction_hash = self.repository.put_transaction(
                _json_payload(envelope)
            )
            if existing is not None:
                same_v2_transaction = bool(
                    existing.is_v2
                    and existing.transaction_hash == transaction_hash
                )
                if same_v2_transaction:
                    # Exact prepare replay is read-only and preserves every
                    # decision already attached to the stage.
                    return self.workflow_snapshot()
                if existing.is_v2:
                    pass
                else:
                    # v1 stage is read-only and must go through the explicit
                    # recertification path; a normal proposal cannot replace
                    # its unpublished positive decisions.
                    raise ActiveTransactionError(
                        "canon_v3_prepare_legacy_stage_recertification_required"
                    )
            pointer = StagingPointer(
                transaction_hash=transaction_hash,
                lineage_decision_hashes=tuple(sorted(lineage_hashes)),
            )
            self._write_staging_unlocked(pointer)
        return self.workflow_snapshot()

    @staticmethod
    def _same_requested_decision(
        previous: ReviewDecision,
        action: ReviewAction,
        corrected: FactCandidate | None,
    ) -> bool:
        if previous.action != action:
            return False
        if action is not ReviewAction.CORRECT:
            return corrected is None
        return bool(
            corrected is not None
            and previous.correction is not None
            and previous.correction.candidate_digest == candidate_digest(corrected)
        )

    def record_decisions(
        self,
        payload: Mapping[str, Any] | DecisionRequestV2,
    ) -> dict[str, Any]:
        try:
            request = (
                payload
                if isinstance(payload, DecisionRequestV2)
                else DecisionRequestV2.model_validate(payload)
            )
        except Exception as exc:
            raise InvalidDecision("canon_v3_decision_request_v2_invalid") from exc
        with self.staging_lock:
            self._legacy_prefix_guard()
            pointer = self._read_staging_unlocked()
            if pointer is None:
                raise ActiveTransactionError("canon_v3_no_staged_transaction")
            if not pointer.is_v2 or pointer.stage_digest is None:
                raise ActiveTransactionError(
                    "canon_v3_staging_protocol_upgrade_required"
                )
            if request.transaction_hash != pointer.transaction_hash:
                raise InvalidDecision(
                    "canon_v3_decision_transaction_precondition_failed"
                )
            if request.expected_stage_digest != pointer.stage_digest:
                raise InvalidDecision(
                    "canon_v3_decision_stage_precondition_failed"
                )
            envelope = self._load_envelope(pointer.transaction_hash)
            self._assert_active_chapter_bindings(
                before_chapter=envelope.chapter
            )
            if self.repository.current_head(validate=True) != (
                envelope.prepared_transaction.parent_head
            ):
                raise CanonHeadConflict(
                    expected=envelope.prepared_transaction.parent_head,
                    actual=self.repository.current_head(validate=False),
                )
            require_chapter_binding(
                self.project_root,
                envelope.chapter,
                envelope.chapter_binding,
            )
            cases = {case.case_key: case for case in self._cases(envelope)}
            existing = list(self._load_decisions(pointer, envelope))
            heads = self._decision_heads(existing)
            object_hashes = set(pointer.decision_hashes)
            candidates = self._candidate_map(envelope)
            material_payloads = {
                item["case_key"]: item
                for item in self._workflow_case_payloads(
                    envelope=envelope,
                    transaction_hash=pointer.transaction_hash,
                    cases=cases.values(),
                    stage_digest=pointer.stage_digest,
                    decision_heads=heads,
                )
            }
            planned: list[tuple[ReviewDecision, dict[str, Any]]] = []

            # Validate the complete batch before adding even an immutable
            # decision object.  The single STAGING replacement below is the
            # only authoritative state transition.
            for item in request.decisions:
                case_key = item.case_key
                case = cases.get(case_key)
                if case is None:
                    raise InvalidDecision(
                        f"canon_v3_decision_case_not_found:{case_key}"
                    )
                material = material_payloads[case_key]
                if item.target_digest != case.target_digest:
                    raise InvalidDecision(
                        "canon_v3_decision_target_precondition_failed"
                    )
                if item.material_digest != material["review_material"][
                    "material_digest"
                ]:
                    raise InvalidDecision(
                        "canon_v3_decision_material_precondition_failed"
                    )
                corrected = item.corrected_candidate
                if corrected is not None:
                    original = candidates.get(case.context.candidate_digest)
                    if original is None or corrected.candidate_id != original.candidate_id:
                        raise InvalidDecision(
                            "canon_v3_correction_must_keep_candidate_id"
                        )
                    verify_candidate_sources(
                        self.project_root,
                        envelope.chapter_binding,
                        corrected,
                        active_author_axiom_source_keys=(
                            self._active_author_axiom_source_keys(
                                envelope.prepared_transaction.parent_head
                            )
                        ),
                    )
                previous = heads.get(case_key)
                actual_decision_head = (
                    previous.decision_hash if previous is not None else None
                )
                if item.expected_decision_head_hash != actual_decision_head:
                    raise InvalidDecision(
                        "canon_v3_decision_head_precondition_failed"
                    )
                if previous is not None and self._same_requested_decision(
                    previous, item.action, corrected
                ):
                    continue
                decision = make_decision(
                    case,
                    item.action,
                    corrected_candidate=corrected,
                    previous=previous,
                )
                wrapper = {
                    "schema_version": DECISION_ENVELOPE_SCHEMA,
                    "transaction_hash": pointer.transaction_hash,
                    "chapter": envelope.chapter,
                    "stage_digest_before": pointer.stage_digest,
                    "target_digest": case.target_digest,
                    "material_digest": material["review_material"][
                        "material_digest"
                    ],
                    "expected_decision_head_hash": actual_decision_head,
                    "lineage_key": material["lineage_key"],
                    "decision": decision_to_dict(decision),
                }
                planned.append((decision, wrapper))
                heads[case_key] = decision

            for decision, wrapper in planned:
                object_hash = self.repository.put_decision(wrapper)
                object_hashes.add(object_hash)
                existing.append(decision)

            pointer = StagingPointer(
                transaction_hash=pointer.transaction_hash,
                decision_hashes=tuple(sorted(object_hashes)),
                lineage_decision_hashes=pointer.lineage_decision_hashes,
            )
            self._write_staging_unlocked(pointer)
        return self.workflow_snapshot()

    def _validated_reduction(
        self,
        pointer: StagingPointer,
        envelope: PreparedEnvelope,
    ):
        recompiled = self._recompile(envelope)
        if recompiled != envelope.prepared_transaction:
            raise PreparedTransactionInvalid("canon_v3_recompile_digest_mismatch")
        cases = self._cases(envelope)
        decisions = self._load_decisions(pointer, envelope)
        reduction = reduce_review(envelope.candidates, cases, decisions)
        active = {
            record.candidate_digest for record in reduction.active_candidates
        }
        included_effect_ids: set[str] = set()
        for effect in envelope.prepared_transaction.effects:
            if effect.candidate_digest not in active:
                continue
            if (
                effect.prior_effect_id is not None
                and effect.prior_effect_id not in included_effect_ids
            ):
                raise PreparedTransactionInvalid(
                    "canon_v3_active_effect_missing_intra_chapter_prior:"
                    + effect.effect_id
                )
            included_effect_ids.add(effect.effect_id)
        return reduction

    def _finalize_token(
        self,
        pointer: StagingPointer,
        envelope: PreparedEnvelope,
        reduction: Any,
    ) -> str:
        if not pointer.is_v2 or pointer.stage_digest is None:
            raise PreparedTransactionInvalid(
                "canon_v3_finalize_requires_v2_stage"
            )
        if reduction.snapshot.state is not ReviewWorkflowState.READY:
            raise FinalizeBlockedError(
                "canon_v3_finalize_token_requires_ready_reduction"
            )
        active = tuple(
            sorted(
                record.candidate_digest
                for record in reduction.active_candidates
            )
        )
        active_set = set(active)
        effects = [
            effect.model_dump(mode="json")
            for effect in envelope.prepared_transaction.effects
            if effect.candidate_digest in active_set
        ]
        return canonical_digest(
            {
                "schema_version": FINALIZE_TOKEN_SCHEMA,
                "stage_digest": pointer.stage_digest,
                "transaction_hash": pointer.transaction_hash,
                "transaction_digest": (
                    envelope.prepared_transaction.transaction_digest
                ),
                "parent_head": envelope.prepared_transaction.parent_head,
                "chapter": envelope.chapter,
                "chapter_binding": envelope.chapter_binding.model_dump(
                    mode="json"
                ),
                "decision_hashes": list(pointer.decision_hashes),
                "lineage_decision_hashes": list(
                    pointer.lineage_decision_hashes
                ),
                "active_candidate_digests": list(active),
                "canon_effects_digest": canonical_digest(effects),
            }
        )

    def _completed_finalize_retry(
        self,
        request: FinalizeRequestV2,
    ) -> dict[str, Any]:
        matched = next(
            (
                (commit_hash, commit)
                for commit_hash, commit in self.repository.current_commits()
                if commit.get("transaction_hash") == request.transaction_hash
            ),
            None,
        )
        if matched is None:
            raise ActiveTransactionError("canon_v3_no_staged_transaction")
        commit_hash, commit = matched
        pointer = StagingPointer(
            transaction_hash=request.transaction_hash,
            decision_hashes=tuple(commit.get("decision_hashes") or ()),
            lineage_decision_hashes=tuple(
                commit.get("lineage_decision_hashes") or ()
            ),
        )
        if request.expected_stage_digest != pointer.stage_digest:
            raise FinalizeBlockedError(
                "canon_v3_finalize_stage_precondition_failed"
            )
        envelope = self._load_envelope(request.transaction_hash)
        require_chapter_binding(
            self.project_root,
            envelope.chapter,
            envelope.chapter_binding,
        )
        reduction = self._validated_reduction(pointer, envelope)
        if request.finalize_token != self._finalize_token(
            pointer, envelope, reduction
        ):
            raise FinalizeBlockedError(
                "canon_v3_finalize_token_precondition_failed"
            )
        active = {
            record.candidate_digest for record in reduction.active_candidates
        }
        expected_effects = [
            effect.model_dump(mode="json")
            for effect in envelope.prepared_transaction.effects
            if effect.candidate_digest in active
        ]
        if commit.get("canon_effects") != expected_effects:
            raise PreparedTransactionInvalid(
                "canon_v3_idempotent_finalize_commit_proof_mismatch"
            )
        head = self.repository.current_head(validate=True)
        manifest = self.repository.current_manifest() or {}
        projection = rebuild_projection(self.project_root)
        return {
            "schema_version": "canon-v3/finalize-result/v2",
            "created": False,
            "chapter": envelope.chapter,
            "revision": int(commit.get("revision") or 0),
            "generation": int(manifest.get("generation") or 0),
            "transaction_hash": request.transaction_hash,
            "commit_hash": commit_hash,
            "head_hash": head,
            "decision_hashes": list(pointer.decision_hashes),
            "projection_binding": projection["binding"],
        }

    def finalize(
        self,
        payload: Mapping[str, Any] | FinalizeRequestV2 | None = None,
    ) -> dict[str, Any]:
        if payload is None:
            raise FinalizeBlockedError(
                "canon_v3_finalize_request_v2_required"
            )
        try:
            request = (
                payload
                if isinstance(payload, FinalizeRequestV2)
                else FinalizeRequestV2.model_validate(payload)
            )
        except Exception as exc:
            raise FinalizeBlockedError(
                "canon_v3_finalize_request_v2_invalid"
            ) from exc
        with self.staging_lock:
            self._legacy_prefix_guard()
            pointer = self._read_staging_unlocked()
            if pointer is None:
                return self._completed_finalize_retry(request)
            if not pointer.is_v2 or pointer.stage_digest is None:
                raise FinalizeBlockedError(
                    "canon_v3_staging_protocol_upgrade_required"
                )
            if request.transaction_hash != pointer.transaction_hash:
                raise FinalizeBlockedError(
                    "canon_v3_finalize_transaction_precondition_failed"
                )
            if request.expected_stage_digest != pointer.stage_digest:
                raise FinalizeBlockedError(
                    "canon_v3_finalize_stage_precondition_failed"
                )
            envelope = self._load_envelope(pointer.transaction_hash)
            self._assert_active_chapter_bindings(
                before_chapter=envelope.chapter
            )
            parent = envelope.prepared_transaction.parent_head
            actual_head = self.repository.current_head(validate=True)
            reduction = self._validated_reduction(pointer, envelope)
            if reduction.snapshot.state is not ReviewWorkflowState.READY:
                raise FinalizeBlockedError(
                    "canon_v3_finalize_blocked:"
                    + reduction.snapshot.state.value
                )
            if request.finalize_token != self._finalize_token(
                pointer, envelope, reduction
            ):
                raise FinalizeBlockedError(
                    "canon_v3_finalize_token_precondition_failed"
                )
            if actual_head != parent:
                if self._transaction_is_current_commit(pointer.transaction_hash):
                    active = {
                        record.candidate_digest
                        for record in reduction.active_candidates
                    }
                    expected_effects = [
                        effect.model_dump(mode="json")
                        for effect in envelope.prepared_transaction.effects
                        if effect.candidate_digest in active
                    ]
                    current_commit = next(
                        (
                            commit
                            for _hash, commit in self.repository.current_commits()
                            if commit.get("transaction_hash")
                            == pointer.transaction_hash
                        ),
                        None,
                    )
                    if (
                        current_commit is None
                        or tuple(current_commit.get("decision_hashes") or ())
                        != pointer.decision_hashes
                        or tuple(
                            current_commit.get("lineage_decision_hashes") or ()
                        )
                        != pointer.lineage_decision_hashes
                        or current_commit.get("canon_effects") != expected_effects
                    ):
                        raise PreparedTransactionInvalid(
                            "canon_v3_idempotent_finalize_commit_proof_mismatch"
                        )
                    self._clear_staging_unlocked()
                    projection = rebuild_projection(self.project_root)
                    return {
                        "schema_version": "canon-v3/finalize-result/v2",
                        "created": False,
                        "transaction_hash": pointer.transaction_hash,
                        "head_hash": self.repository.current_head(validate=True),
                        "projection_binding": projection["binding"],
                    }
                raise CanonHeadConflict(expected=parent, actual=actual_head)
            require_chapter_binding(
                self.project_root,
                envelope.chapter,
                envelope.chapter_binding,
            )
            active = {
                record.candidate_digest for record in reduction.active_candidates
            }
            effects = [
                effect.model_dump(mode="json")
                for effect in envelope.prepared_transaction.effects
                if effect.candidate_digest in active
            ]
            result = self.repository.seal(
                chapter=envelope.chapter,
                transaction=pointer.transaction_hash,
                expected_head=parent,
                decisions=pointer.decision_hashes,
                lineage_decisions=pointer.lineage_decision_hashes,
                canon_effects=effects,
            )
            self._clear_staging_unlocked()
            projection = rebuild_projection(self.project_root)
            return {
                "schema_version": "canon-v3/finalize-result/v2",
                "created": result.created,
                "chapter": result.chapter,
                "revision": result.revision,
                "generation": result.generation,
                "transaction_hash": result.transaction_hash,
                "commit_hash": result.commit_hash,
                "head_hash": result.head_hash,
                "decision_hashes": list(result.decision_hashes),
                "projection_binding": projection["binding"],
            }

    # Independent long-term hard-setting channel.  These wrappers keep the
    # public service surface discoverable without coupling the chapter models
    # to a fake chapter/body binding.
    def prepare_author_axioms(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).prepare(payload)

    def record_author_axiom_decisions(
        self, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).record_decisions(payload)

    def finalize_author_axioms(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).finalize(payload)

    def author_axiom_status(self) -> dict[str, Any]:
        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).status()

    def active_author_axioms(self) -> dict[str, Any]:
        from .author_axiom import AuthorAxiomChannel

        return AuthorAxiomChannel(
            self.project_root, repository=self.repository
        ).active_snapshot()

    def _snapshot_without_stage(self) -> dict[str, Any]:
        head = self.repository.current_head(validate=True)
        if head is None:
            return {
                "schema_version": WORKFLOW_SCHEMA,
                "state": "migration_required",
                "head_hash": None,
                "generation": 0,
                "chapter": None,
                "transaction_hash": None,
                "can_finalize": False,
                "can_write_next": False,
                "projection_fresh": False,
                "cases": [],
                "counts": {},
                "recovery_action": (
                    "migrate_legacy" if self._legacy_commits_exist() else "initialize_v3"
                ),
            }
        try:
            legacy_status = self._legacy_prefix_guard()
        except MigrationRequiredError as exc:
            manifest = self.repository.current_manifest() or {}
            repair = self._legacy_recertification_workflow_fields(exc)
            return {
                "schema_version": WORKFLOW_SCHEMA,
                "state": "migration_required",
                "head_hash": head,
                "generation": int(manifest.get("generation") or 0),
                "chapter": None,
                "transaction_hash": None,
                "can_finalize": False,
                "can_write_next": False,
                "projection_fresh": projection_is_fresh(self.project_root),
                **repair,
                "error": str(exc),
            }
        try:
            self._assert_active_chapter_bindings()
        except ActiveCanonBindingError as exc:
            manifest = self.repository.current_manifest() or {}
            return {
                "schema_version": WORKFLOW_SCHEMA,
                "state": "recompile_required",
                "head_hash": head,
                "generation": int(manifest.get("generation") or 0),
                "chapter": exc.chapter,
                "transaction_hash": None,
                "can_finalize": False,
                "can_write_next": False,
                "projection_fresh": projection_is_fresh(self.project_root),
                "cases": [],
                "counts": {},
                "recovery_action": "reprepare_changed_canonical_chapter",
                "error": str(exc),
            }
        manifest = self.repository.current_manifest() or {}
        fresh = projection_is_fresh(self.project_root)
        return {
            "schema_version": WORKFLOW_SCHEMA,
            "state": "ready" if fresh else "projection_rebuild_required",
            "head_hash": head,
            "generation": int(manifest.get("generation") or 0),
            "chapter": None,
            "transaction_hash": None,
            "can_finalize": False,
            "can_write_next": fresh,
            "projection_fresh": fresh,
            "cases": [],
            "counts": {},
            "recovery_action": "write_next_chapter" if fresh else "rebuild_projection",
            "legacy_prefix": legacy_status,
        }

    def _workflow_snapshot_core(self) -> dict[str, Any]:
        try:
            with self.staging_lock:
                pointer = self._read_staging_unlocked()
                if pointer is None:
                    return self._snapshot_without_stage()
                envelope = self._load_envelope(pointer.transaction_hash)
                if (
                    not pointer.is_v2
                    and not self._transaction_is_current_commit(
                        pointer.transaction_hash
                    )
                ):
                    return {
                        "schema_version": WORKFLOW_SCHEMA,
                        "state": "recompile_required",
                        "head_hash": self.repository.current_head(validate=False),
                        "generation": int(
                            (self.repository.current_manifest() or {}).get(
                                "generation"
                            )
                            or 0
                        ),
                        "chapter": envelope.chapter,
                        "transaction_hash": pointer.transaction_hash,
                        "stage_digest": None,
                        "can_finalize": False,
                        "can_write_next": False,
                        "projection_fresh": projection_is_fresh(
                            self.project_root
                        ),
                        "cases": [],
                        "counts": {},
                        "recovery_action": "reprepare_transaction_protocol_v2",
                        "error": "canon_v3_unpublished_v1_stage_not_authoritative",
                    }
                try:
                    self._legacy_prefix_guard()
                except MigrationRequiredError as exc:
                    repair = self._legacy_recertification_workflow_fields(exc)
                    return {
                        "schema_version": WORKFLOW_SCHEMA,
                        "state": "migration_required",
                        "head_hash": self.repository.current_head(validate=False),
                        "generation": int(
                            (self.repository.current_manifest() or {}).get("generation")
                            or 0
                        ),
                        "chapter": envelope.chapter,
                        "transaction_hash": pointer.transaction_hash,
                        "can_finalize": False,
                        "can_write_next": False,
                        "projection_fresh": projection_is_fresh(self.project_root),
                        **repair,
                        "error": str(exc),
                    }
                try:
                    self._assert_active_chapter_bindings(
                        before_chapter=envelope.chapter
                    )
                except ActiveCanonBindingError as exc:
                    return {
                        "schema_version": WORKFLOW_SCHEMA,
                        "state": "recompile_required",
                        "head_hash": self.repository.current_head(validate=False),
                        "generation": int(
                            (self.repository.current_manifest() or {}).get("generation")
                            or 0
                        ),
                        "chapter": exc.chapter,
                        "transaction_hash": pointer.transaction_hash,
                        "can_finalize": False,
                        "can_write_next": False,
                        "projection_fresh": projection_is_fresh(self.project_root),
                        "cases": [],
                        "counts": {},
                        "recovery_action": "reprepare_changed_canonical_chapter",
                        "error": str(exc),
                    }
                if self._transaction_is_current_commit(pointer.transaction_hash):
                    try:
                        reduction = self._validated_reduction(pointer, envelope)
                        active = {
                            record.candidate_digest
                            for record in reduction.active_candidates
                        }
                        expected_effects = [
                            effect.model_dump(mode="json")
                            for effect in envelope.prepared_transaction.effects
                            if effect.candidate_digest in active
                        ]
                        current_commit = next(
                            (
                                commit
                                for _hash, commit in self.repository.current_commits()
                                if commit.get("transaction_hash")
                                == pointer.transaction_hash
                            ),
                            None,
                        )
                        exact = bool(
                            reduction.snapshot.state is ReviewWorkflowState.READY
                            and current_commit is not None
                            and tuple(
                                current_commit.get("decision_hashes") or ()
                            )
                            == pointer.decision_hashes
                            and tuple(
                                current_commit.get("lineage_decision_hashes")
                                or ()
                            )
                            == pointer.lineage_decision_hashes
                            and current_commit.get("canon_effects")
                            == expected_effects
                        )
                    except Exception:
                        exact = False
                    if exact:
                        return self._snapshot_without_stage()
                    return {
                        "schema_version": WORKFLOW_SCHEMA,
                        "state": "recompile_required",
                        "head_hash": self.repository.current_head(validate=False),
                        "generation": int(
                            (self.repository.current_manifest() or {}).get(
                                "generation"
                            )
                            or 0
                        ),
                        "chapter": envelope.chapter,
                        "transaction_hash": pointer.transaction_hash,
                        "can_finalize": False,
                        "can_write_next": False,
                        "projection_fresh": projection_is_fresh(self.project_root),
                        "cases": [],
                        "counts": {},
                        "recovery_action": "repair_authoritative_review_lineage",
                        "error": "canon_v3_current_commit_stage_proof_mismatch",
                    }
                current_head = self.repository.current_head(validate=True)
                if current_head != envelope.prepared_transaction.parent_head:
                    return {
                        "schema_version": WORKFLOW_SCHEMA,
                        "state": "recompile_required",
                        "head_hash": current_head,
                        "generation": int(
                            (self.repository.current_manifest() or {}).get("generation")
                            or 0
                        ),
                        "chapter": envelope.chapter,
                        "transaction_hash": pointer.transaction_hash,
                        "can_finalize": False,
                        "can_write_next": False,
                        "projection_fresh": projection_is_fresh(self.project_root),
                        "cases": [],
                        "counts": {},
                        "recovery_action": "reprepare_against_current_head",
                    }
                try:
                    require_chapter_binding(
                        self.project_root,
                        envelope.chapter,
                        envelope.chapter_binding,
                    )
                    reduction = self._validated_reduction(pointer, envelope)
                except ChapterBindingError:
                    return {
                        "schema_version": WORKFLOW_SCHEMA,
                        "state": "recompile_required",
                        "head_hash": current_head,
                        "generation": int(
                            (self.repository.current_manifest() or {}).get("generation")
                            or 0
                        ),
                        "chapter": envelope.chapter,
                        "transaction_hash": pointer.transaction_hash,
                        "can_finalize": False,
                        "can_write_next": False,
                        "projection_fresh": projection_is_fresh(self.project_root),
                        "cases": [],
                        "counts": {},
                        "recovery_action": "reprepare_changed_chapter",
                    }
                state_map = {
                    ReviewWorkflowState.READY: "ready_to_finalize",
                    ReviewWorkflowState.AWAITING_HUMAN: "awaiting_human",
                    ReviewWorkflowState.RECOMPILE_REQUIRED: "recompile_required",
                    ReviewWorkflowState.REWRITE_REQUIRED: "rewrite_required",
                }
                snapshot = reduction.snapshot
                decision_heads = self._decision_heads(
                    self._load_decisions(pointer, envelope)
                )
                # Resolved cases remain visible as immutable authorization
                # targets so a later author amendment can echo the exact
                # target/material and current decision head.  Pending counts
                # still come exclusively from the reducer snapshot below.
                cases_by_key = {
                    case.case_key: case for case in self._cases(envelope)
                }
                for case in snapshot.revision_cases:
                    cases_by_key[case.case_key] = case
                all_cases = [
                    cases_by_key[key] for key in sorted(cases_by_key)
                ]
                finalize_token = (
                    self._finalize_token(pointer, envelope, reduction)
                    if snapshot.state is ReviewWorkflowState.READY
                    else None
                )
                return {
                    "schema_version": WORKFLOW_SCHEMA,
                    "state": state_map[snapshot.state],
                    "head_hash": current_head,
                    "generation": int(
                        (self.repository.current_manifest() or {}).get("generation")
                        or 0
                    ),
                    "chapter": envelope.chapter,
                    "transaction_hash": pointer.transaction_hash,
                    "stage_digest": pointer.stage_digest,
                    "finalize_token": finalize_token,
                    "can_finalize": snapshot.can_finalize,
                    "can_write_next": False,
                    "projection_fresh": projection_is_fresh(self.project_root),
                    "cases": self._workflow_case_payloads(
                        envelope=envelope,
                        transaction_hash=pointer.transaction_hash,
                        cases=all_cases,
                        candidate_revisions=reduction.corrections,
                        stage_digest=pointer.stage_digest,
                        decision_heads=decision_heads,
                    ),
                    "counts": {
                        "required": len(snapshot.required_cases),
                        "advisory": len(snapshot.advisory_cases),
                        "audit": len(snapshot.audit_cases),
                        "rewrite": len(snapshot.rewrite_cases),
                        "revision": len(snapshot.revision_cases),
                        "stale_decisions": len(snapshot.stale_decision_hashes),
                    },
                    "recovery_action": snapshot.recovery_action,
                    "applied_decision_hashes": list(
                        reduction.applied_decision_hashes
                    ),
                    "stale_decision_hashes": list(
                        reduction.stale_decision_hashes
                    ),
                }
        except (
            CanonIntegrityError,
            PreparedTransactionInvalid,
            ScanAttestationError,
            ProjectionStaleError,
            ValueError,
        ) as exc:
            return {
                "schema_version": WORKFLOW_SCHEMA,
                "state": "invalid",
                "head_hash": None,
                "generation": 0,
                "chapter": None,
                "transaction_hash": None,
                "can_finalize": False,
                "can_write_next": False,
                "projection_fresh": False,
                "cases": [],
                "counts": {},
                "recovery_action": "run_canon_v3_doctor",
                "error": str(exc),
            }

    def workflow_snapshot(self) -> dict[str, Any]:
        """Return workflow state plus the one authoritative chapter sequence."""

        snapshot = self._workflow_snapshot_core()
        head = snapshot.get("head_hash")
        cutover: int | None = None
        active_chapters: list[int] = []
        if isinstance(head, str) and head:
            try:
                manifest = self.repository.read_manifest(
                    head,
                    validate_references=True,
                )
                cutover = self._cutover_chapter(head)
                active_chapters = [
                    int(entry.get("chapter") or 0)
                    for entry in manifest.get("chapters") or []
                    if int(entry.get("chapter") or 0) > 0
                ]
            except (CanonRepositoryError, ValueError):
                # The core snapshot already reports the integrity failure.  Do
                # not invent sequence authority when HEAD cannot be validated.
                cutover = None
                active_chapters = []
        latest = (
            max([cutover or 0, *active_chapters])
            if cutover is not None
            else None
        )
        expected_next = latest + 1 if latest is not None else None
        allowed = sorted(
            {
                *active_chapters,
                *([expected_next] if expected_next is not None else []),
            }
        )
        result = {
            **snapshot,
            "cutover_chapter": cutover,
            "active_chapters": active_chapters,
            "latest_chapter": latest,
            "expected_next_chapter": expected_next,
            "allowed_write_chapters": allowed,
        }
        result.setdefault("stage_digest", None)
        result.setdefault("finalize_token", None)
        try:
            result["author_axiom_digest"] = self._active_author_axiom_digest(
                head if isinstance(head, str) and head else None
            )
        except Exception as exc:
            result["author_axiom_digest"] = EMPTY_AUTHOR_AXIOM_DIGEST
            result["state"] = "invalid"
            result["can_finalize"] = False
            result["can_write_next"] = False
            result["recovery_action"] = "run_canon_v3_doctor"
            result["error"] = (
                "canon_v3_author_axiom_authority_invalid:" + str(exc)
            )
        entity_registry_digest = "0" * 64
        transaction_hash = result.get("transaction_hash")
        try:
            if isinstance(transaction_hash, str) and transaction_hash:
                entity_registry_digest = (
                    self._load_envelope(
                        transaction_hash
                    ).prepared_transaction.entity_registry_digest
                )
            elif (
                isinstance(head, str)
                and head
                and expected_next is not None
            ):
                entity_registry_digest = build_approved_entity_registry(
                    self.repository,
                    head,
                    target_chapter=expected_next,
                ).registry_digest
        except Exception:
            # The core snapshot already exposes the integrity/recovery state;
            # never invent a non-zero registry binding on an invalid path.
            entity_registry_digest = "0" * 64
        result["entity_registry_digest"] = entity_registry_digest
        try:
            axiom_workflow = self.author_axiom_status()
            result["author_axiom_workflow"] = axiom_workflow
            if axiom_workflow.get("transaction_hash"):
                # There is exactly one project-wide authoritative staging
                # transaction.  Surface the axiom case/tokens through the same
                # workflow fields so confirm cannot choose the wrong channel.
                result.update(
                    {
                        "state": axiom_workflow.get("state"),
                        "chapter": None,
                        "transaction_kind": "author_axiom",
                        "transaction_hash": axiom_workflow.get(
                            "transaction_hash"
                        ),
                        "stage_digest": axiom_workflow.get("stage_digest"),
                        "finalize_token": axiom_workflow.get(
                            "finalize_token"
                        ),
                        "cases": axiom_workflow.get("cases") or [],
                        "can_finalize": bool(
                            axiom_workflow.get("can_finalize")
                        ),
                        "can_write_next": False,
                        "recovery_action": axiom_workflow.get(
                            "recovery_action"
                        ),
                    }
                )
            else:
                result.setdefault("transaction_kind", "chapter")
        except Exception as exc:
            result["state"] = "invalid"
            result["can_finalize"] = False
            result["can_write_next"] = False
            result["transaction_kind"] = "invalid"
            result["recovery_action"] = "run_canon_v3_doctor"
            result["error"] = (
                "canon_v3_author_axiom_workflow_invalid:" + str(exc)
            )
        unsigned = dict(result)
        unsigned.pop("workflow_digest", None)
        result["workflow_digest"] = canonical_digest(unsigned)
        # Return the exact public authority shape from every direct service
        # caller as well.  The normalizer is pure/idempotent and imports this
        # service only inside WorkflowAuthority.snapshot(), so the late import
        # does not create a module initialization cycle.
        from ..workflow_authority import normalize_workflow_snapshot

        return normalize_workflow_snapshot(result)


__all__ = [
    "ActiveTransactionError",
    "ActiveCanonBindingError",
    "CanonV3Service",
    "CanonV3ServiceError",
    "ChapterProposalBatch",
    "EMPTY_AUTHOR_AXIOM_DIGEST",
    "DECISION_REQUEST_SCHEMA",
    "DECISION_ENVELOPE_SCHEMA",
    "DecisionInputV2",
    "DecisionRequestV2",
    "FINALIZE_REQUEST_SCHEMA",
    "FINALIZE_TOKEN_SCHEMA",
    "FinalizeRequestV2",
    "FinalizeBlockedError",
    "MigrationRequiredError",
    "PREPARED_ENVELOPE_SCHEMA",
    "PREPARED_ENVELOPE_SCHEMA_V1",
    "PROPOSAL_SCHEMA",
    "PROPOSAL_SCHEMA_V1",
    "PreparedEnvelope",
    "PreparedTransactionInvalid",
    "REQUIRED_SCAN_DIMENSIONS",
    "STAGING_RELATIVE_PATH",
    "STAGING_SCHEMA",
    "STAGING_SCHEMA_V1",
    "StagingPointer",
    "ScanAttestationError",
]

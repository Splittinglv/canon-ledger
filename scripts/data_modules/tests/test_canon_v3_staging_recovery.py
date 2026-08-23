from __future__ import annotations

from pathlib import Path

import pytest

from scripts.data_modules.chapter_content_binding import build_chapter_binding
from scripts.data_modules.canon_v3.migration import (
    LEGACY_RECERTIFICATION_DECISION_SCHEMA,
    LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA,
    _build_material,
    publish_recertification,
    repair_cutover_dry_run,
)
from scripts.data_modules.canon_v3.repository import CanonV3Repository
from scripts.data_modules.canon_v3.author_axiom import (
    AuthorAxiomChannel,
    AuthorAxiomStagingPointer,
)
from scripts.data_modules.canon_v3.service import (
    ARCHIVE_STAGING_REQUEST_SCHEMA,
    ActiveTransactionError,
    CanonV3Service,
    FinalizeBlockedError,
    StagingArchiveConflict,
    StagingPointer,
)
from scripts.data_modules.tests.canon_v3_protocol_helpers import (
    finalize,
    proposal_authority,
)
from scripts.data_modules.workflow_authority import WorkflowAuthority


SCAN_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]


def _empty_batch(service: CanonV3Service, binding: dict) -> dict:
    authority = proposal_authority(service, int(binding["chapter"]))
    return {
        **authority,
        "chapter": int(binding["chapter"]),
        "chapter_binding": binding,
        "candidates": [],
        "observations": [],
        "scan_attestations": [
            {
                "attestation_id": "staging-recovery-complete-scan",
                "scanner": "reviewer",
                "scanner_version": "v3-recovery-test",
                "chapter_sha256": binding["sha256"],
                "parent_head": authority["parent_head"],
                "author_axiom_digest": authority["author_axiom_digest"],
                "entity_registry_digest": authority["entity_registry_digest"],
                "dimensions": SCAN_DIMENSIONS,
                "status": "complete",
                "checked_candidate_digests": [],
            }
        ],
    }


def _archive_request(stage_digest: str, kind: str = "chapter") -> dict:
    return {
        "schema_version": ARCHIVE_STAGING_REQUEST_SCHEMA,
        "transaction_kind": kind,
        "expected_stage_digest": stage_digest,
    }


def _install_v1_genesis(project_root: Path) -> str:
    material = _build_material(project_root, 0)
    metadata = material.genesis_metadata()
    metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    return CanonV3Repository(project_root)._initialize_objects(  # noqa: SLF001
        expected_head=None,
        genesis_metadata=metadata,
    )


def _recertification_request(report: dict) -> dict:
    return {
        "schema_version": LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA,
        "expected_current_head": report["current_head"],
        "detached_plan_digest": report["detached_plan_digest"],
        "publish_token": report["publish_token"],
        "decisions": [
            {
                "schema_version": LEGACY_RECERTIFICATION_DECISION_SCHEMA,
                "case_key": case["case_key"],
                **case["decision_binding"],
                "action": case["allowed_actions"][0],
            }
            for case in report["cases"]
        ],
    }


def test_archive_staging_is_exact_idempotent_and_preserves_objects(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book"
    root.mkdir()
    service = CanonV3Service(root)
    service.initialize_new_project()
    transaction_hash = service.repository.put_transaction(
        {"fixture": "exact-staging-archive"}
    )
    pointer = StagingPointer(transaction_hash=transaction_hash)
    assert pointer.stage_digest is not None
    with service.staging_lock:
        service._write_staging_unlocked(pointer)  # noqa: SLF001

    first = service.archive_staging(_archive_request(pointer.stage_digest))
    replay = service.cancel_staging(_archive_request(pointer.stage_digest))

    assert first["created"] is True
    assert replay["created"] is False
    assert replay["idempotent_replay"] is True
    assert first["archive_path"] == replay["archive_path"]
    assert not service.staging_path.exists()
    assert (root / first["archive_path"]).is_file()
    assert service.repository.object_path(
        "transaction", transaction_hash
    ).is_file()

    decision_hash = service.repository.put_decision({"fixture": "newer-stage"})
    newer = StagingPointer(
        transaction_hash=transaction_hash,
        decision_hashes=(decision_hash,),
    )
    assert newer.stage_digest != pointer.stage_digest
    with service.staging_lock:
        service._write_staging_unlocked(newer)  # noqa: SLF001
    with pytest.raises(StagingArchiveConflict, match="digest_conflict"):
        service.archive_staging(_archive_request(pointer.stage_digest))
    assert service._read_staging_unlocked() == newer  # noqa: SLF001


def test_archive_staging_uses_same_exact_protocol_for_author_axioms(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book"
    root.mkdir()
    service = CanonV3Service(root)
    service.initialize_new_project()
    transaction_hash = service.repository.put_author_axiom_transaction(
        {"fixture": "author-axiom-archive"}
    )
    pointer = AuthorAxiomStagingPointer(transaction_hash=transaction_hash)
    assert pointer.stage_digest is not None
    channel = AuthorAxiomChannel(root, repository=service.repository)
    with channel.staging_lock:
        channel._write_stage_unlocked(pointer)  # noqa: SLF001

    result = service.archive_staging(
        _archive_request(pointer.stage_digest, "author_axiom")
    )

    assert result["transaction_kind"] == "author_axiom"
    assert result["created"] is True
    assert not channel.staging_path.exists()
    assert (root / result["archive_path"]).is_file()
    assert service.repository.object_path(
        "author_axiom_transaction", transaction_hash
    ).is_file()


def test_recertification_staging_deadlock_recovers_then_requires_reprepare(
    tmp_path: Path,
) -> None:
    root = tmp_path / "book"
    chapter = root / "正文" / "第0001章.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text("林舟走进青云殿。\n", encoding="utf-8")
    binding = build_chapter_binding(root, 1)
    service = CanonV3Service(root)
    service.initialize_new_project()
    old_stage = service.prepare(_empty_batch(service, binding))
    old_head = str(old_stage["head_hash"])

    # Model an installed v1 project which already had a valid unpublished
    # chapter pointer at upgrade time.  Only CURRENT changes in this fixture;
    # the old prepared/decision objects stay immutable in the common store.
    service.repository.current_path.unlink()
    v1_head = _install_v1_genesis(root)
    assert v1_head != old_head

    blocked = WorkflowAuthority(root).snapshot()
    assert blocked["state"] == "migration_required"
    assert blocked["primary_action"]["id"] == "archive_conflicting_staging"
    assert blocked["primary_action"]["code"] == (
        "resolve_recertification_staging_conflict"
    )
    assert blocked["primary_action"]["parameters"] == {
        "transaction_kind": "chapter",
        "expected_stage_digest": old_stage["stage_digest"],
    }

    archived = service.archive_staging(
        _archive_request(old_stage["stage_digest"])
    )
    assert archived["requires_reprepare"] is True
    assert archived["workflow"]["primary_action"]["code"] == (
        "review_and_publish_legacy_recertification"
    )

    report = repair_cutover_dry_run(root)
    published = publish_recertification(
        root,
        _recertification_request(report),
    )
    assert published["head_hash"] != v1_head

    with pytest.raises((ActiveTransactionError, FinalizeBlockedError)):
        finalize(service, snapshot=old_stage)

    prepared_again = service.prepare(_empty_batch(service, binding))
    assert prepared_again["transaction_hash"] != old_stage["transaction_hash"]
    assert prepared_again["head_hash"] == published["head_hash"]

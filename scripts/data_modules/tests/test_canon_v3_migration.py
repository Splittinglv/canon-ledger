#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.data_modules.canon_v3.migration import (
    LEGACY_ADMISSION_SCHEMA,
    LEGACY_GENESIS_SCHEMA,
    LEGACY_SNAPSHOT_SCHEMA,
    LEGACY_RECERTIFICATION_DECISION_SCHEMA,
    LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA,
    LegacyMigrationError,
    _build_material,
    audit_cutover,
    legacy_prefix_status,
    migrate_legacy,
    publish_recertification,
    repair_cutover_dry_run,
)
from scripts.data_modules.canon_v3.projection import (
    projection_is_fresh,
    read_projection,
)
from scripts.data_modules.canon_v3.entity_registry import (
    build_approved_entity_registry,
)
from scripts.data_modules.canon_v3.schema import IdentityNamespace
from scripts.data_modules.canon_v3.schema import (
    FactCandidate,
    ObservationKind,
    OpenLoopCreatedClaim,
    PresenceObservedClaim,
    ReviewLevel,
    ReviewObservation,
    ScanAttestation,
)
from scripts.data_modules.canon_v3.evidence import candidate_digest
from scripts.data_modules.canon_v3.service import (
    CanonV3Service,
    PreparedEnvelope,
    PreparedTransactionInvalid,
)
from scripts.data_modules.canon_v3.repository import (
    CanonV3Repository,
    content_hash,
)
from scripts.data_modules.chapter_commit_service import ChapterCommitService
from scripts.data_modules.chapter_content_binding import build_chapter_binding
from scripts.data_modules.canonical_history import load_canonical_history
from scripts.data_modules.config import DataModulesConfig
from scripts.data_modules.memory_contract_adapter import MemoryContractAdapter
from scripts.data_modules.canon_evidence import (
    LegacyCutoverEvidenceError,
    validate_legacy_cutover_event,
)
from scripts.data_modules.human_review import (
    HumanReviewService,
    human_decision_receipt_sha256,
    verified_event_content_sha256,
)
from scripts.data_modules.tests.canon_v3_protocol_helpers import (
    finalize as finalize_v3,
    proposal_authority,
    record_decisions as record_decisions_v3,
)
from scripts.data_modules.workflow_authority import WorkflowAuthority
from .review_test_helpers import standard_review


def _persist_accepted_commit(
    project_root: Path,
    chapter: int,
    *,
    body: str | None = None,
    extraction_result: dict | None = None,
) -> tuple[Path, Path, dict]:
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(body or f"第{chapter}章最终正文。", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)

    contract = (
        project_root
        / ".story-system"
        / "chapters"
        / f"chapter_{chapter:03d}.json"
    )
    contract.parent.mkdir(parents=True, exist_ok=True)
    contract.write_text(
        json.dumps(
            {
                "meta": {"chapter": chapter},
                "chapter_directive": {
                    "goal": f"完成第{chapter}章事实推进",
                    "must_cover_nodes": [],
                    "forbidden_zones": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    extraction_payload = dict(extraction_result or {})
    extraction_payload.setdefault("accepted_events", [])
    extraction_payload.setdefault("state_deltas", [])
    extraction_payload.setdefault("entity_deltas", [])
    extraction_payload["chapter_binding"] = dict(binding)
    # This helper manufactures a pre-v3 historical prefix. Production legacy
    # mutations are intentionally disabled by WorkflowAuthority; migration
    # tests bypass only that write guard on this isolated fixture instance.
    service = ChapterCommitService(project_root)
    service._assert_v2_write_allowed = lambda: None  # type: ignore[method-assign]
    with patch.object(HumanReviewService, "_assert_v2_write_allowed", lambda self: None):
        payload = service.build_commit(
            chapter=chapter,
            review_result=standard_review(binding),
            fulfillment_result={
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
                "chapter_binding": dict(binding),
            },
            disambiguation_result={
                "pending": [],
                "chapter_binding": dict(binding),
            },
            extraction_result=extraction_payload,
        )
    assert payload["meta"]["status"] == "accepted"
    commit_path = service.persist_commit(payload)
    return chapter_path, commit_path, payload


def _genesis_metadata(project_root: Path) -> dict:
    repository = CanonV3Repository(project_root)
    head = repository.current_head()
    assert head is not None
    manifest = repository.read_manifest(head)
    assert manifest["generation"] == 0
    return manifest["genesis_metadata"]


def _downgrade_to_markerless_v1(commit_path: Path, payload: dict) -> dict:
    """Persist a historical envelope without the v2 evidence markers."""

    downgraded = json.loads(json.dumps(payload, ensure_ascii=False))
    downgraded["meta"]["schema_version"] = "story-system/v1"
    downgraded["extraction_result"].pop("evidence_contract", None)
    downgraded["provenance"].pop("evidence_contract", None)
    commit_path.write_text(
        json.dumps(downgraded, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return downgraded


def _install_v1_genesis(project_root: Path, cutover_chapter: int = 0) -> str:
    material = _build_material(project_root, cutover_chapter)
    metadata = material.genesis_metadata()
    metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    # The private primitive intentionally accepts historical repository bytes;
    # audit/publish must never trust this v1 snapshot as the recertified base.
    return CanonV3Repository(project_root)._initialize_objects(  # noqa: SLF001
        expected_head=None,
        genesis_metadata=metadata,
    )


def _publish_request(report: dict, *, drop_last: bool = False) -> dict:
    cases = list(report["cases"])
    if drop_last and cases:
        cases = cases[:-1]
    return {
        "schema_version": LEGACY_RECERTIFICATION_PUBLISH_REQUEST_SCHEMA,
        "expected_current_head": report["current_head"],
        "detached_plan_digest": report["detached_plan_digest"],
        "publish_token": report["publish_token"],
        "decisions": [
            {
                "schema_version": LEGACY_RECERTIFICATION_DECISION_SCHEMA,
                "case_key": case["case_key"],
                "target_digest": case["target_digest"],
                "material_digest": case["material_digest"],
                "action": "confirm",
            }
            for case in cases
        ],
    }


def test_new_project_initialization_is_deterministic_and_rebuilds_projection(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_result = migrate_legacy(first)
    second_result = migrate_legacy(second)

    assert first_result["source"] == "new_project"
    assert first_result["cutover_chapter"] == 0
    assert first_result["head_hash"] == second_result["head_hash"]
    assert projection_is_fresh(first) is True
    assert read_projection(first)["facts"] == []
    metadata = _genesis_metadata(first)
    assert metadata["schema_version"] == LEGACY_GENESIS_SCHEMA
    assert metadata["source"] == "new_project"
    assert metadata["v2_commits"] == []
    assert metadata["legacy_snapshot"]["schema_version"] == LEGACY_SNAPSHOT_SCHEMA
    assert metadata["legacy_snapshot_sha256"] == content_hash(
        metadata["legacy_snapshot"]
    )


def test_migration_records_exact_commit_bytes_binding_and_fact_snapshot(
    tmp_path: Path,
) -> None:
    _first_chapter, first_commit, first_payload = _persist_accepted_commit(
        tmp_path, 1
    )
    _second_chapter, second_commit, second_payload = _persist_accepted_commit(
        tmp_path, 2
    )
    expected_hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (first_commit, second_commit)
    ]

    result = migrate_legacy(tmp_path)

    assert result["migrated"] is True
    assert result["cutover_chapter"] == 2
    metadata = _genesis_metadata(tmp_path)
    assert metadata["source"] == "v2_accepted_commits"
    assert [item["chapter"] for item in metadata["v2_commits"]] == [1, 2]
    assert [item["content_sha256"] for item in metadata["v2_commits"]] == expected_hashes
    assert metadata["v2_commits"][0]["manuscript_binding"] == first_payload[
        "chapter_binding"
    ]
    assert metadata["v2_commits"][1]["manuscript_binding"] == second_payload[
        "chapter_binding"
    ]
    assert metadata["legacy_snapshot"]["facts"]["valid_chapters"] == [1, 2]
    assert metadata["legacy_snapshot_sha256"] == content_hash(
        metadata["legacy_snapshot"]
    )
    assert legacy_prefix_status(tmp_path)["state"] == "current"

    repeated = migrate_legacy(tmp_path)
    assert repeated["migrated"] is False
    assert repeated["head_hash"] == result["head_hash"]


def test_invalid_v2_binding_fails_closed_without_publishing_current(
    tmp_path: Path,
) -> None:
    chapter_path, _commit_path, _payload = _persist_accepted_commit(tmp_path, 1)
    chapter_path.write_text("第1章迁移前已被改写。", encoding="utf-8")

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_commit_binding_invalid"
    assert CanonV3Repository(tmp_path).current_head(validate=False) is None


def test_missing_v2_prefix_chapter_fails_closed(tmp_path: Path) -> None:
    _persist_accepted_commit(tmp_path, 2)

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_commit_prefix_not_contiguous"
    assert CanonV3Repository(tmp_path).current_head(validate=False) is None


def test_edit_before_cutover_marks_legacy_prefix_stale_and_requires_migration(
    tmp_path: Path,
) -> None:
    first_chapter, _first_commit, _payload = _persist_accepted_commit(tmp_path, 1)
    _persist_accepted_commit(tmp_path, 2)
    migrate_legacy(tmp_path)
    assert legacy_prefix_status(tmp_path)["state"] == "current"

    first_chapter.write_text("第一章在 cutover 后被改写。", encoding="utf-8")
    status = legacy_prefix_status(tmp_path)

    assert status["state"] == "stale"
    assert status["migration_required"] is True
    assert "legacy_commit_binding_invalid" in status["reason_codes"]
    from scripts.data_modules.canon_v3.service import CanonV3Service

    workflow = CanonV3Service(tmp_path).workflow_snapshot()
    assert workflow["state"] == "migration_required"
    assert workflow["can_write_next"] is False
    history = load_canonical_history(tmp_path, 2)
    assert history.canonical_facts == []
    assert any("legacy_prefix" in item for item in history.invalid_sources)
    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)
    assert raised.value.code == "legacy_migration_required"


def test_commit_content_change_after_cutover_is_detected_even_with_same_binding(
    tmp_path: Path,
) -> None:
    _chapter_path, commit_path, payload = _persist_accepted_commit(tmp_path, 1)
    migrate_legacy(tmp_path)

    payload["projection_status"] = {"state": "changed-after-cutover"}
    commit_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    status = legacy_prefix_status(tmp_path)

    assert status["state"] == "stale"
    assert status["migration_required"] is True
    assert "legacy_commit_refs_changed" in status["reason_codes"]


def test_explicit_cutover_binds_only_the_selected_prefix(tmp_path: Path) -> None:
    _persist_accepted_commit(tmp_path, 1)
    _chapter, suffix_commit, _payload = _persist_accepted_commit(tmp_path, 2)
    # Sources after explicit K are suffix work, not part of the imported base.
    suffix_commit.write_text("{unfinished-suffix", encoding="utf-8")

    result = migrate_legacy(tmp_path, cutover_chapter=1)

    assert result["cutover_chapter"] == 1
    metadata = _genesis_metadata(tmp_path)
    assert [item["chapter"] for item in metadata["v2_commits"]] == [1]
    assert legacy_prefix_status(tmp_path)["state"] == "current"


def test_malformed_v2_source_fails_closed(tmp_path: Path) -> None:
    commit_path = (
        tmp_path
        / ".story-system"
        / "commits"
        / "chapter_001.commit.json"
    )
    commit_path.parent.mkdir(parents=True)
    commit_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_commit_invalid_json"
    assert not CanonV3Repository(tmp_path).current_path.exists()


@pytest.mark.parametrize(
    ("body", "extraction", "unsupported_field"),
    [
        (
            "陌生人出现在门口。",
            {
                "accepted_events": [
                    {
                        "event_id": "entity-alias-smuggle",
                        "event_type": "entity_observed",
                        "subject": "陌生人",
                        "payload": {
                            "entity_id": "陌生人",
                            "name": "陌生人",
                            "aliases": ["皇帝"],
                            "namespace": "actor",
                            "evidence_quote": "陌生人出现在门口。",
                        },
                    }
                ]
            },
            "payload.aliases",
        ),
        (
            "城门在黎明开启。",
            {
                "accepted_events": [
                    {
                        "event_id": "timeline-smuggle",
                        "event_type": "timeline_observed",
                        "subject": "城门",
                        "payload": {
                            "event": "皇帝驾崩",
                            "time_anchor": "百年后",
                            "evidence_quote": "城门在黎明开启。",
                        },
                    }
                ]
            },
            "payload.event",
        ),
        (
            "林舟回家。",
            {
                "accepted_events": [
                    {
                        "event_id": "state-smuggle",
                        "event_type": "character_state_changed",
                        "subject": "林舟",
                        "payload": {
                            "field": "愿望",
                            "new": "皇帝",
                            "evidence_quote": "林舟回家。",
                        },
                    }
                ]
            },
            "payload.new",
        ),
    ],
)
def test_cutover_requires_field_level_evidence_for_every_legacy_fact_kind(
    tmp_path: Path,
    body: str,
    extraction: dict,
    unsupported_field: str,
) -> None:
    _persist_accepted_commit(
        tmp_path,
        1,
        body=body,
        extraction_result=extraction,
    )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_event_requires_human_verification"
    assert unsupported_field in ":".join(raised.value.details)
    assert CanonV3Repository(tmp_path).current_head(validate=False) is None


def test_cutover_validates_every_linked_alias_and_timeline_field(
    tmp_path: Path,
) -> None:
    body = "陌生人出现在门口。城门在黎明开启。"
    _persist_accepted_commit(
        tmp_path,
        1,
        body=body,
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "entity-linked-smuggle",
                    "event_type": "entity_observed",
                    "subject": "陌生人",
                    "payload": {
                        "entity_id": "陌生人",
                        "name": "陌生人",
                        "namespace": "actor",
                        "evidence_quote": "陌生人出现在门口。",
                    },
                },
                {
                    "event_id": "timeline-linked-smuggle",
                    "event_type": "timeline_observed",
                    "subject": "城门",
                    "payload": {
                        "event": "城门在黎明开启",
                        "time_anchor": "黎明",
                        "evidence_quote": "城门在黎明开启。",
                    },
                },
            ],
            "entity_deltas": [
                {
                    "entity_id": "陌生人",
                    "canonical_name": "陌生人",
                    "aliases": ["皇帝"],
                    "source_event_id": "entity-linked-smuggle",
                }
            ],
            "timeline_events": [
                {
                    "timeline_id": "bad-linked-timeline",
                    "event": "皇帝驾崩",
                    "time_hint": "百年后",
                    "source_event_id": "timeline-linked-smuggle",
                    "evidence_fragment": "城门在黎明开启。",
                }
            ],
        },
    )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_event_requires_human_verification"
    details = ":".join(raised.value.details)
    assert "linked.entity.aliases" in details or "linked.timeline.event" in details


def test_cutover_keeps_overwritten_relationships_as_sanitized_fact_history(
    tmp_path: Path,
) -> None:
    _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟与苏月成为朋友。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "relationship-friends",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {
                        "from_entity": "林舟",
                        "to_entity": "苏月",
                        "relationship_type": "朋友",
                        "evidence_quote": "林舟与苏月成为朋友。",
                    },
                }
            ]
        },
    )
    _persist_accepted_commit(
        tmp_path,
        2,
        body="林舟与苏月变成敌人。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "relationship-enemies",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {
                        "from_entity": "林舟",
                        "to_entity": "苏月",
                        "relationship_type": "敌人",
                        "evidence_quote": "林舟与苏月变成敌人。",
                    },
                }
            ]
        },
    )

    migrate_legacy(tmp_path)
    history = load_canonical_history(tmp_path, 2)
    audit_values = {
        fact.get("value")
        for audit in history.long_term_event_audit
        for fact in audit.get("normalized_facts") or []
        if isinstance(fact, dict)
    }
    assert {"朋友", "敌人"}.issubset(audit_values)
    assert all("source_event" not in row for row in history.long_term_event_audit)

    snapshot = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).export_asof_snapshot(chapter=3)
    context_values = {
        fact.get("value")
        for audit in snapshot["long_term_event_audit"]
        for fact in audit.get("normalized_facts") or []
        if isinstance(fact, dict)
    }
    assert {"朋友", "敌人"}.issubset(context_values)


def test_exact_v2_human_decision_can_admit_nonliteral_legacy_semantics(
    tmp_path: Path,
) -> None:
    _chapter, commit_path, commit = _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟回家。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "human-state",
                    "event_type": "character_state_changed",
                    "subject": "林舟",
                    "payload": {
                        "field": "愿望",
                        "new": "皇帝",
                        "evidence_quote": "林舟回家。",
                    },
                }
            ]
        },
    )
    event = commit["extraction_result"]["accepted_events"][0]
    event["verification"] = "verified"
    decision_id = "ch0001-human-state"
    event_sha = verified_event_content_sha256(1, event)
    decision = {
        "decision_id": decision_id,
        "chapter": 1,
        "chapter_sha256": commit["chapter_binding"]["sha256"],
        "candidate_fingerprint": "exact-candidate",
        "action": "confirm",
        "replacement_event": None,
        "verified_event_id": "human-state",
        "verified_event_sha256": event_sha,
    }
    receipt = human_decision_receipt_sha256(decision)
    commit["provenance"]["human_review"] = {
        "resolved_decision_ids": [decision_id],
        "decision_receipts": [
            {"decision_id": decision_id, "decision_sha256": receipt}
        ],
        "verified_event_ids": ["human-state"],
        "unresolved_count": 0,
    }
    commit_path.write_text(
        json.dumps(commit, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    ledger = tmp_path / ".canon-ledger" / "human-review" / "decisions.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"schema_version": "test", "decisions": [decision]}, ensure_ascii=False),
        encoding="utf-8",
    )

    migrate_legacy(tmp_path)

    metadata = _genesis_metadata(tmp_path)
    admissions = metadata["legacy_snapshot"]["facts"]["cutover_admissions"]
    assert len(admissions) == 1
    assert admissions[0]["chapter"] == 1
    assert admissions[0]["event_id"] == "human-state"
    assert admissions[0]["event_type"] == "character_state_changed"
    assert admissions[0]["mode"] == "exact_human_decision"
    assert admissions[0]["chapter_sha256"] == commit["chapter_binding"]["sha256"]
    assert admissions[0]["decision_id"] == decision_id
    assert admissions[0]["decision_receipt_sha256"] == receipt
    assert admissions[0]["verified_event_sha256"] == event_sha
    assert admissions[0]["linked_field_evidence"]["mode"] == (
        "linked_field_evidence"
    )


def test_artifact_fact_kind_overrides_untyped_generic_entity_delta_namespace(
    tmp_path: Path,
) -> None:
    _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟获得铜铃。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "obtain-bell",
                    "event_type": "artifact_obtained",
                    "subject": "item-bell",
                    "payload": {
                        "artifact_id": "item-bell",
                        "name": "铜铃",
                        "owner": "林舟",
                        "evidence_quote": "林舟获得铜铃。",
                    },
                }
            ],
            "entity_deltas": [
                {
                    "entity_id": "item-bell",
                    "canonical_name": "铜铃",
                    "source_event_id": "obtain-bell",
                }
            ],
        },
    )

    result = migrate_legacy(tmp_path)
    metadata = _genesis_metadata(tmp_path)
    admissions = metadata["legacy_snapshot"]["facts"]["cutover_admissions"]
    assert admissions[0]["quote_binding"]["encoding"] == "utf-8"
    assert admissions[0]["quote_binding"]["spans"] == [
        {"start": 0, "end": len("林舟获得铜铃。".encode("utf-8"))}
    ]
    assert admissions[0]["admission_digest"] == content_hash(
        {
            key: value
            for key, value in admissions[0].items()
            if key != "admission_digest"
        }
    )
    history = load_canonical_history(tmp_path, 1)
    item_entity = history.entities["item:item-bell"]
    assert item_entity["namespace"] == "item"
    assert item_entity["type"] == "物品"
    assert history.custody["item-bell"]["holder_id"] == "林舟"

    repository = CanonV3Repository(tmp_path)
    registry = build_approved_entity_registry(
        repository,
        result["head_hash"],
        target_chapter=2,
    )
    assert registry.resolve_all("铜铃", IdentityNamespace.ITEM)
    assert registry.resolve_all("铜铃", IdentityNamespace.ACTOR) == ()


def test_exact_human_event_does_not_approve_unbound_linked_projection_fields() -> None:
    event = {
        "event_id": "human-entity",
        "event_type": "entity_observed",
        "subject": "陌生人",
        "payload": {
            "entity_id": "陌生人",
            "name": "陌生人",
            "namespace": "actor",
            "evidence_quote": "陌生人出现在门口。",
        },
    }
    linked = {
        "entity_deltas": [
            {
                "entity_id": "陌生人",
                "canonical_name": "陌生人",
                "aliases": ["皇帝"],
                "source_event_id": "human-entity",
            }
        ]
    }

    with pytest.raises(LegacyCutoverEvidenceError) as raised:
        validate_legacy_cutover_event(
            event,
            linked,
            event_fields_human_approved=True,
        )

    assert "linked.entity.aliases" in raised.value.fields


def test_markerless_v1_event_quote_must_bind_a_real_manuscript_span(
    tmp_path: Path,
) -> None:
    _chapter, commit_path, commit = _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟回家。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "markerless-fake-quote",
                    "event_type": "character_state_changed",
                    "subject": "林舟",
                    "payload": {
                        "field": "行踪",
                        "new": "回家",
                        "evidence_quote": "林舟回家。",
                    },
                }
            ]
        },
    )
    downgraded = _downgrade_to_markerless_v1(commit_path, commit)
    payload = downgraded["extraction_result"]["accepted_events"][0]["payload"]
    payload.update(
        {"field": "身份", "new": "皇帝", "evidence_quote": "林舟成为皇帝。"}
    )
    commit_path.write_text(
        json.dumps(downgraded, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_event_quote_not_in_bound_chapter"
    assert CanonV3Repository(tmp_path).current_head(validate=False) is None


@pytest.mark.parametrize(
    ("field", "row"),
    [
        (
            "state_deltas",
            {"entity_id": "林舟", "field": "身份", "new": "皇帝"},
        ),
        (
            "entity_deltas",
            {"entity_id": "林舟", "canonical_name": "皇帝"},
        ),
        (
            "timeline_events",
            {"timeline_id": "future", "event": "百年后皇帝驾崩"},
        ),
    ],
)
def test_markerless_non_event_canon_channels_require_an_admitted_source_event(
    tmp_path: Path,
    field: str,
    row: dict,
) -> None:
    _chapter, commit_path, commit = _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟回家。",
        extraction_result={},
    )
    downgraded = _downgrade_to_markerless_v1(commit_path, commit)
    downgraded["extraction_result"][field] = [row]
    commit_path.write_text(
        json.dumps(downgraded, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_linked_fact_evidence_invalid"
    assert "source_event_id" in ":".join(raised.value.details)


def test_cutover_rejects_terminal_opaque_id_that_points_at_the_wrong_promise(
    tmp_path: Path,
) -> None:
    _chapter, first_path, first = _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟承诺带苏月进京，也承诺把灵剑送给她。",
        extraction_result={},
    )
    first = _downgrade_to_markerless_v1(first_path, first)
    first["extraction_result"]["accepted_events"] = [
                {
                    "event_id": "promise-capital-event",
                    "event_type": "promise_created",
                    "subject": "林舟",
                    "payload": {
                        "promise_id": "promise-capital",
                        "content": "带苏月进京",
                        "evidence_quote": "林舟承诺带苏月进京，也承诺把灵剑送给她。",
                    },
                },
                {
                    "event_id": "promise-sword-event",
                    "event_type": "promise_created",
                    "subject": "林舟",
                    "payload": {
                        "promise_id": "promise-sword",
                        "content": "把灵剑送给她",
                        "evidence_quote": "林舟承诺带苏月进京，也承诺把灵剑送给她。",
                    },
                },
            ]
    first_path.write_text(
        json.dumps(first, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    _chapter, terminal_path, terminal = _persist_accepted_commit(
        tmp_path,
        2,
        body="林舟终于兑现带苏月进京的承诺。",
        extraction_result={},
    )
    terminal = _downgrade_to_markerless_v1(terminal_path, terminal)
    terminal["extraction_result"]["accepted_events"] = [
                {
                    "event_id": "pay-wrong-promise",
                    "event_type": "promise_paid_off",
                    "subject": "林舟",
                    "payload": {
                        "target_promise_id": "promise-sword",
                        "resolution": "带苏月进京",
                        "evidence_quote": "林舟终于兑现带苏月进京的承诺。",
                    },
                }
            ]
    terminal_path.write_text(
        json.dumps(terminal, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_terminal_target_not_supported"
    assert "promise-sword" in ":".join(raised.value.details)


def test_cutover_rejects_information_id_reused_for_a_different_claim(
    tmp_path: Path,
) -> None:
    for chapter, body, claim in (
        (1, "苏月 known witnessed 密道在东边。", "密道在东边"),
        (2, "苏月 known witnessed 皇帝已经死亡。", "皇帝已经死亡"),
    ):
        _chapter, commit_path, commit = _persist_accepted_commit(
            tmp_path,
            chapter,
            body=body,
            extraction_result={
                "accepted_events": [
                    {
                        "event_id": f"knowledge-{chapter}",
                        "sequence": 1,
                        "event_type": "knowledge_state_changed",
                        "subject": "苏月",
                        "payload": {
                            "information_id": f"info-{chapter}",
                            "canonical_claim": claim,
                            "evidence_fragment": claim,
                            "state": "known",
                            "source_kind": "witnessed",
                            "evidence_quote": body,
                        },
                    }
                ]
            },
        )
        if chapter == 2:
            commit["extraction_result"]["accepted_events"][0]["payload"][
                "information_id"
            ] = "info-1"
            commit_path.write_text(
                json.dumps(commit, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_opaque_id_semantic_conflict"
    assert "information_id=info-1" in ":".join(raised.value.details)


@pytest.mark.parametrize("family", ["rule", "timeline"])
def test_cutover_rejects_opaque_id_reused_for_different_semantics(
    tmp_path: Path,
    family: str,
) -> None:
    for chapter in (1, 2):
        if family == "rule":
            domain, field, value = (
                ("天门", "入门", "天门禁止凡人入内")
                if chapter == 1
                else ("玄门", "门禁", "玄门只许长老入内")
            )
            body = f"制度：{domain}{field}规则是{value}。"
            event = {
                "event_id": f"rule-event-{chapter}",
                "event_type": "world_rule_revealed",
                "subject": domain,
                "payload": {
                    "rule_id": "opaque-reused",
                    "domain": domain,
                    "field": field,
                    "rule_category": "制度",
                    "rule_content": value,
                    "evidence_quote": body,
                },
            }
            linked = {}
        else:
            event_text, anchor = (
                ("城门开启", "黎明")
                if chapter == 1
                else ("城门关闭", "午夜")
            )
            body = f"{anchor}{event_text}。"
            event = {
                "event_id": f"timeline-event-{chapter}",
                "event_type": "timeline_observed",
                "subject": "城门",
                "payload": {
                    "event": event_text,
                    "time_anchor": anchor,
                    "evidence_quote": body,
                },
            }
            linked = {
                "timeline_events": [
                    {
                        "timeline_id": "opaque-reused",
                        "event": event_text,
                        "time_hint": anchor,
                        "source_event_id": f"timeline-event-{chapter}",
                        "evidence_fragment": body,
                    }
                ]
            }
        _chapter, path, commit = _persist_accepted_commit(
            tmp_path, chapter, body=body, extraction_result={}
        )
        commit = _downgrade_to_markerless_v1(path, commit)
        commit["extraction_result"]["accepted_events"] = [event]
        commit["extraction_result"].update(linked)
        path.write_text(
            json.dumps(commit, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    with pytest.raises(LegacyMigrationError) as raised:
        migrate_legacy(tmp_path)

    assert raised.value.code == "legacy_opaque_id_semantic_conflict"
    assert family in ":".join(raised.value.details)


def test_cutover_normalizes_item_alias_before_folding_custody_slots(
    tmp_path: Path,
) -> None:
    _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟获得铜铃。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "bell-obtained",
                    "event_type": "artifact_obtained",
                    "subject": "item-bell",
                    "payload": {
                        "artifact_id": "item-bell",
                        "name": "铜铃",
                        "owner": "林舟",
                        "evidence_quote": "林舟获得铜铃。",
                    },
                }
            ],
            "entity_deltas": [
                {
                    "entity_id": "item-bell",
                    "canonical_name": "铜铃",
                    "source_event_id": "bell-obtained",
                }
            ],
        },
    )
    _persist_accepted_commit(
        tmp_path,
        2,
        body="林舟把铜铃交给苏月。",
        extraction_result={
            "accepted_events": [
                    {
                        "event_id": "bell-transferred",
                        "sequence": 1,
                        "event_type": "custody_changed",
                    "subject": "铜铃",
                    "payload": {
                        "from_holder": "林舟",
                        "to_holder": "苏月",
                        "evidence_quote": "林舟把铜铃交给苏月。",
                    },
                }
            ]
        },
    )

    result = migrate_legacy(tmp_path)
    metadata = _genesis_metadata(tmp_path)
    facts = metadata["legacy_snapshot"]["facts"]

    assert list(facts["custody"]) == ["item-bell"]
    assert facts["custody"]["item-bell"]["holder_id"] == "苏月"
    assert {
        row["artifact_id"] for row in facts["custody_history"]
    } == {"item-bell"}
    assert all(
        row["schema_version"] == LEGACY_ADMISSION_SCHEMA
        for row in facts["cutover_fact_admissions"]
    )
    registry = build_approved_entity_registry(
        CanonV3Repository(tmp_path),
        result["head_hash"],
        target_chapter=3,
    )
    assert registry.resolve("铜铃", IdentityNamespace.ITEM).canonical_entity == (
        "item-bell"
    )


def test_cutover_audit_is_read_only_and_returns_a_deterministic_detached_plan(
    tmp_path: Path,
) -> None:
    _persist_accepted_commit(tmp_path, 1)
    repository = CanonV3Repository(tmp_path)
    assert repository.current_head(validate=False) is None

    first = audit_cutover(tmp_path)
    second = repair_cutover_dry_run(tmp_path)

    assert first["state"] == "ready"
    assert first["writes_performed"] is False
    assert first["invariants"] == {
        "source_prefix": "pass",
        "evidence": "pass",
        "target": "pass",
        "identity": "pass",
    }
    assert first["detached_plan_digest"] == second["detached_plan_digest"]
    assert second["would_switch_current"] is False
    assert repository.current_head(validate=False) is None


def test_v1_genesis_audit_requires_recertification_without_switching_current(
    tmp_path: Path,
) -> None:
    repository = CanonV3Repository(tmp_path)
    old_head = repository._initialize_objects(  # noqa: SLF001 - legacy fixture
        expected_head=None,
        genesis_metadata={
            "schema_version": "canon-v3/legacy-genesis/v1",
            "source": "new_project",
            "cutover_chapter": 0,
            "v2_commits": [],
            "legacy_snapshot": {
                "schema_version": "canon-v3/legacy-fact-snapshot/v1",
                "source_schema_version": "canon-ledger-asof-snapshot/v3",
                "cutover_chapter": 0,
                "facts": {},
            },
            "legacy_snapshot_sha256": content_hash(
                {
                    "schema_version": "canon-v3/legacy-fact-snapshot/v1",
                    "source_schema_version": "canon-ledger-asof-snapshot/v3",
                    "cutover_chapter": 0,
                    "facts": {},
                }
            ),
        },
    )

    report = repair_cutover_dry_run(tmp_path)
    status = legacy_prefix_status(tmp_path)

    assert report["state"] == "needs_recertification"
    assert report["requires_recertification"] is True
    assert "legacy_genesis_needs_recertification" in report["reason_codes"]
    assert report["detached_plan_digest"]
    assert status["migration_required"] is True
    assert status["reason_codes"] == ["legacy_genesis_needs_recertification"]
    assert repository.current_head(validate=True) == old_head


def test_v1_recertification_publishes_v2_genesis_and_exact_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    old_head = _install_v1_genesis(tmp_path)
    report = repair_cutover_dry_run(tmp_path)
    request = _publish_request(report)

    first = publish_recertification(tmp_path, request)
    second = publish_recertification(tmp_path, request)

    assert first["published"] is True
    assert first["prior_head_hash"] == old_head
    assert first["head_hash"] != old_head
    assert second["published"] is False
    assert second["idempotent_replay"] is True
    assert second["head_hash"] == first["head_hash"]
    repository = CanonV3Repository(tmp_path)
    genesis = repository.read_manifest(first["head_hash"])["genesis_metadata"]
    assert genesis["schema_version"] == LEGACY_GENESIS_SCHEMA
    receipt = genesis["recertification"]
    assert receipt["prior_head_hash"] == old_head
    assert receipt["detached_plan_digest"] == report["detached_plan_digest"]
    assert receipt["publish_token"] == report["publish_token"]
    assert legacy_prefix_status(tmp_path)["state"] == "current"


def test_delayed_idempotent_retry_reports_exact_recertification_terminal_head(
    tmp_path: Path,
) -> None:
    _install_v1_genesis(tmp_path)
    report = repair_cutover_dry_run(tmp_path)
    request = _publish_request(report)
    first = publish_recertification(tmp_path, request)
    terminal_head = first["head_hash"]
    repository = CanonV3Repository(tmp_path)
    later = repository._seal_objects(  # noqa: SLF001 - descendant fixture
        chapter=1,
        transaction={"chapter": 1, "canon_effects": []},
        expected_head=terminal_head,
        canon_effects=[],
    )

    replay = publish_recertification(tmp_path, request)

    assert replay["idempotent_replay"] is True
    assert replay["head_hash"] == terminal_head
    assert replay["recertification_terminal_head"] == terminal_head
    assert replay["current_head"] == later.head_hash
    assert replay["generation"] == 0
    assert replay["current_generation"] == 1
    assert repository.current_head() == later.head_hash


def test_v1_recertification_partial_decisions_never_switch_current(
    tmp_path: Path,
) -> None:
    _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟与苏月成为朋友。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "recert-relationship",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {
                        "from_entity": "林舟",
                        "to_entity": "苏月",
                        "relationship_type": "朋友",
                        "evidence_quote": "林舟与苏月成为朋友。",
                    },
                }
            ]
        },
    )
    old_head = _install_v1_genesis(tmp_path, 1)
    report = repair_cutover_dry_run(tmp_path)

    assert report["required_case_count"] > 4
    families = {case["family"] for case in report["cases"]}
    assert {
        "positive_event_admission",
        "positive_fact_admission",
        "identity_resolution",
        "target_resolution",
    }.issubset(families)
    for case in report["cases"]:
        assert case["material_digest"] == content_hash(case["review_material"])
    with pytest.raises(
        LegacyMigrationError,
        match="legacy_recertification_decisions_incomplete",
    ):
        publish_recertification(
            tmp_path,
            _publish_request(report, drop_last=True),
        )
    assert CanonV3Repository(tmp_path).current_head() == old_head


def test_v1_recertification_stale_plan_never_switches_current(
    tmp_path: Path,
) -> None:
    chapter_path, _commit, _payload = _persist_accepted_commit(tmp_path, 1)
    old_head = _install_v1_genesis(tmp_path, 1)
    report = repair_cutover_dry_run(tmp_path)
    request = _publish_request(report)
    chapter_path.write_text("第一章在人工确认后发生变化。", encoding="utf-8")

    with pytest.raises(LegacyMigrationError) as raised:
        publish_recertification(tmp_path, request)

    assert raised.value.code in {
        "legacy_recertification_not_publishable",
        "legacy_recertification_plan_stale",
    }
    assert CanonV3Repository(tmp_path).current_head(validate=False) == old_head


def test_v1_recertification_locked_second_material_cannot_publish_different_source(
    tmp_path: Path,
) -> None:
    _chapter, commit_path, _payload = _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟与苏月成为朋友。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "race-relationship",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {
                        "from_entity": "林舟",
                        "to_entity": "苏月",
                        "relationship_type": "朋友",
                        "evidence_quote": "林舟与苏月成为朋友。",
                    },
                }
            ]
        },
    )
    old_head = _install_v1_genesis(tmp_path, 1)
    report = repair_cutover_dry_run(tmp_path)
    request = _publish_request(report)

    import scripts.data_modules.canon_v3.migration as migration_module

    original = migration_module._build_material  # noqa: SLF001
    calls = 0

    def race_material(project_root: Path, cutover_chapter: int | None):
        nonlocal calls
        calls += 1
        if calls == 3:
            payload = json.loads(commit_path.read_text(encoding="utf-8"))
            payload["projection_status"] = {"race_revision": "B"}
            commit_path.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        return original(project_root, cutover_chapter)

    with patch.object(migration_module, "_build_material", race_material):
        with pytest.raises(
            LegacyMigrationError,
            match="legacy_recertification_plan_stale",
        ):
            publish_recertification(tmp_path, request)

    assert calls >= 3
    assert CanonV3Repository(tmp_path).current_head(validate=False) == old_head


def test_v1_recertification_concurrent_current_cas_loses_safely(
    tmp_path: Path,
) -> None:
    old_head = _install_v1_genesis(tmp_path)
    report = repair_cutover_dry_run(tmp_path)
    repository = CanonV3Repository(tmp_path)
    concurrent = repository._seal_objects(  # noqa: SLF001 - race fixture
        chapter=1,
        transaction={"chapter": 1, "canon_effects": []},
        expected_head=old_head,
        canon_effects=[],
    )

    with pytest.raises(
        LegacyMigrationError,
        match="legacy_recertification_head_conflict",
    ):
        publish_recertification(tmp_path, _publish_request(report))
    assert repository.current_head() == concurrent.head_hash


def test_v1_recertification_fault_before_swap_keeps_old_head_and_response_loss_retries(
    tmp_path: Path,
) -> None:
    old_head = _install_v1_genesis(tmp_path)
    report = repair_cutover_dry_run(tmp_path)
    request = _publish_request(report)

    def fail_before(stage: str) -> None:
        if stage == "before_head_swap":
            raise RuntimeError("simulated-before-swap")

    with pytest.raises(RuntimeError, match="simulated-before-swap"):
        publish_recertification(
            tmp_path,
            request,
            fault_injector=fail_before,
        )
    assert CanonV3Repository(tmp_path).current_head() == old_head

    def lose_response(stage: str) -> None:
        if stage == "after_head_swap":
            raise RuntimeError("simulated-response-loss")

    with pytest.raises(RuntimeError, match="simulated-response-loss"):
        publish_recertification(
            tmp_path,
            request,
            fault_injector=lose_response,
        )
    current = CanonV3Repository(tmp_path).current_head()
    assert current != old_head
    replay = publish_recertification(tmp_path, request)
    assert replay["idempotent_replay"] is True
    assert replay["head_hash"] == current


def test_v1_recertification_rejects_competing_authoritative_staging(
    tmp_path: Path,
) -> None:
    old_head = _install_v1_genesis(tmp_path)
    report = repair_cutover_dry_run(tmp_path)
    staging = tmp_path / ".story-system" / "v3" / "STAGING.json"
    staging.write_text("{}", encoding="utf-8")

    with pytest.raises(
        LegacyMigrationError,
        match="legacy_recertification_not_publishable",
    ):
        publish_recertification(tmp_path, _publish_request(report))
    assert CanonV3Repository(tmp_path).current_head() == old_head


def test_v1_recertification_is_one_public_workflow_with_one_next_action(
    tmp_path: Path,
) -> None:
    _persist_accepted_commit(
        tmp_path,
        1,
        body="林舟与苏月成为朋友。",
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "workflow-relationship",
                    "event_type": "relationship_changed",
                    "subject": "林舟",
                    "payload": {
                        "from_entity": "林舟",
                        "to_entity": "苏月",
                        "relationship_type": "朋友",
                        "evidence_quote": "林舟与苏月成为朋友。",
                    },
                }
            ]
        },
    )
    _install_v1_genesis(tmp_path, 1)
    audit = audit_cutover(tmp_path)
    workflow = WorkflowAuthority(tmp_path).snapshot()

    assert workflow["state"] == "migration_required"
    assert workflow["bootstrap_mode"] == "recertification"
    assert workflow["authoritative_transaction"] == "legacy_recertification"
    assert workflow["transaction_kind"] == "legacy_recertification"
    assert workflow["cases"] == audit["cases"]
    assert workflow["recertification_plan_digest"] == audit[
        "detached_plan_digest"
    ]
    assert workflow["recertification_publish_token"] == audit["publish_token"]
    assert workflow["primary_action"]["code"] == (
        "review_and_publish_legacy_recertification"
    )
    assert workflow["can_write_next"] is False


def test_v1_suffix_is_recompiled_against_each_new_detached_parent(
    tmp_path: Path,
) -> None:
    first_quote = "谁偷走了灵钥，仍是谜。"
    second_quote = "密室为何自行开启，仍无人知晓。"
    chapter_text = first_quote + "\n" + second_quote
    manuscript = tmp_path / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(chapter_text, encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 1)
    raw = manuscript.read_bytes()

    def candidate_for(
        candidate_id: str,
        source_id: str,
        loop: str,
        quote: str,
    ) -> FactCandidate:
        quote_raw = quote.encode("utf-8")
        start = raw.index(quote_raw)
        return FactCandidate(
            candidate_id=candidate_id,
            claim=OpenLoopCreatedClaim(loop=loop),
            sources=(
                {
                    "source_type": "manuscript_span",
                    "source_id": source_id,
                    "document_sha256": binding["sha256"],
                    "chapter": 1,
                    "start": start,
                    "end": start + len(quote_raw),
                    "quote": quote,
                    "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
                },
            ),
            support_map={"loop": (source_id,)},
        )

    candidates = (
        candidate_for(
            "recert-open-loop-key",
            "loop-key-span",
            "谁偷走了灵钥",
            first_quote,
        ),
        candidate_for(
            "recert-open-loop-room",
            "loop-room-span",
            "密室为何自行开启",
            second_quote,
        ),
    )
    digests = {candidate_digest(candidate) for candidate in candidates}
    service = CanonV3Service(tmp_path)
    authority = proposal_authority(service, 1)
    prepared = service.prepare(
        {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [
                candidate.model_dump(mode="json") for candidate in candidates
            ],
            "observations": [],
            "scan_attestations": [
                {
                    "attestation_id": "recert-empty-scan",
                    "scanner": "reviewer",
                    "scanner_version": "test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority["author_axiom_digest"],
                    "entity_registry_digest": authority[
                        "entity_registry_digest"
                    ],
                    "dimensions": [
                        "setting",
                        "timeline",
                        "continuity",
                        "character",
                        "logic",
                    ],
                    "status": "complete",
                    "checked_candidate_digests": sorted(digests),
                }
            ],
        }
    )
    source_result = finalize_v3(service, snapshot=prepared)
    repository = CanonV3Repository(tmp_path)
    source_head = str(source_result["head_hash"])
    source_manifest = repository.read_manifest(source_head)
    source_entry = source_manifest["chapters"][0]
    source_commit = repository.read_commit(source_entry["commit_hash"])
    source_transaction_hash = source_commit["transaction_hash"]
    source_effects = {
        effect["candidate_digest"]: effect
        for effect in source_commit["canon_effects"]
    }
    assert set(source_effects) == digests

    material = _build_material(tmp_path, 0)
    v1_metadata = material.genesis_metadata()
    v1_metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    with repository.locked():
        v1_genesis = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": v1_metadata,
            },
        )
        v1_commit_payload = {
            **source_commit,
            "base_head_hash": v1_genesis,
            "predecessor_commit_hash": None,
        }
        v1_commit = repository._put_payload_unlocked(  # noqa: SLF001
            "commit", v1_commit_payload
        )
        v1_head = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 1,
                "parent_head_hash": v1_genesis,
                "chapters": [
                    {"chapter": 1, "revision": 1, "commit_hash": v1_commit}
                ],
            },
        )
        repository._write_current_unlocked(v1_head)  # noqa: SLF001

    report = repair_cutover_dry_run(tmp_path)
    suffix_fact_cases = [
        case
        for case in report["cases"]
        if case["family"] == "suffix_fact_carry_forward"
    ]
    assert len(suffix_fact_cases) == 2
    assert {
        case["review_material"]["material"]["candidate_digest"]
        for case in suffix_fact_cases
    } == digests
    assert any(
        case["family"] == "suffix_commit_envelope"
        for case in report["cases"]
    )
    result = publish_recertification(tmp_path, _publish_request(report))
    active = repository.read_manifest(result["head_hash"])
    recertified_commit = repository.read_commit(
        active["chapters"][0]["commit_hash"]
    )
    assert recertified_commit["transaction_hash"] != source_transaction_hash
    wrapper = repository.recertified_suffix_wrapper(
        recertified_commit["transaction_hash"]
    )
    assert wrapper is not None
    assert wrapper["source_transaction_hash"] == source_transaction_hash
    assert wrapper["parent_head"] == recertified_commit["base_head_hash"]
    assert wrapper["recertified_envelope"]["prepared_transaction"][
        "parent_head"
    ] == recertified_commit["base_head_hash"]
    assert wrapper["recertified_envelope"]["prepared_transaction"][
        "entity_registry_digest"
    ] == wrapper["entity_registry_digest"]
    recertified_effects = {
        effect["candidate_digest"]: effect
        for effect in recertified_commit["canon_effects"]
    }
    assert set(recertified_effects) == digests
    assert {
        digest: effect["fact_key"]
        for digest, effect in recertified_effects.items()
    } == {
        digest: effect["fact_key"]
        for digest, effect in source_effects.items()
    }
    source_envelope = repository.read_transaction(source_transaction_hash)
    assert source_envelope["prepared_transaction"]["parent_head"] != (
        wrapper["recertified_envelope"]["prepared_transaction"]["parent_head"]
    )
    assert recertified_commit["canon_effects"] == wrapper[
        "recertified_envelope"
    ]["prepared_transaction"]["effects"]
    ready = WorkflowAuthority(tmp_path).snapshot()
    assert ready["state"] == "ready"
    assert ready["can_write_next"] is True


def test_v1_negative_decision_becomes_reviewed_semantic_lineage_and_cannot_revive(
    tmp_path: Path,
) -> None:
    quote = "苏月仍在青云殿内。"
    manuscript = tmp_path / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(quote, encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 1)
    raw = quote.encode("utf-8")
    candidate = FactCandidate(
        candidate_id="presence-suyue-recert",
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿", presence="在"
        ),
        sources=(
            {
                "source_type": "manuscript_span",
                "source_id": "presence-span",
                "document_sha256": binding["sha256"],
                "chapter": 1,
                "start": 0,
                "end": len(raw),
                "quote": quote,
                "quote_sha256": hashlib.sha256(raw).hexdigest(),
            },
        ),
        support_map={
            "subject": ("presence-span",),
            "location": ("presence-span",),
            "presence": ("presence-span",),
        },
    )
    ambiguity = ReviewObservation(
        observation_id="presence-ambiguous",
        candidate_id=candidate.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="旧模型无法确定该出现是否应进入长期正史",
    )
    service = CanonV3Service(tmp_path)
    authority = proposal_authority(service, 1)
    digest = candidate_digest(candidate)
    staged = service.prepare(
        {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [candidate.model_dump(mode="json")],
            "observations": [ambiguity.model_dump(mode="json")],
            "scan_attestations": [
                {
                    "attestation_id": "negative-lineage-scan",
                    "scanner": "reviewer",
                    "scanner_version": "test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority["author_axiom_digest"],
                    "entity_registry_digest": authority[
                        "entity_registry_digest"
                    ],
                    "dimensions": [
                        "setting",
                        "timeline",
                        "continuity",
                        "character",
                        "logic",
                    ],
                    "status": "complete",
                    "checked_candidate_digests": [digest],
                }
            ],
        }
    )
    decided = record_decisions_v3(
        service,
        [{"case_key": staged["cases"][0]["case_key"], "action": "omit"}],
        snapshot=staged,
    )
    source_result = finalize_v3(service, snapshot=decided)
    repository = CanonV3Repository(tmp_path)
    source_head = str(source_result["head_hash"])
    source_manifest = repository.read_manifest(source_head)
    source_entry = source_manifest["chapters"][0]
    source_commit = repository.read_commit(source_entry["commit_hash"])
    negative_hash = source_commit["decision_hashes"][0]

    material = _build_material(tmp_path, 0)
    v1_metadata = material.genesis_metadata()
    v1_metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    with repository.locked():
        v1_genesis = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": v1_metadata,
            },
        )
        v1_commit = repository._put_payload_unlocked(  # noqa: SLF001
            "commit",
            {
                **source_commit,
                "base_head_hash": v1_genesis,
                "predecessor_commit_hash": None,
            },
        )
        v1_head = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 1,
                "parent_head_hash": v1_genesis,
                "chapters": [
                    {"chapter": 1, "revision": 1, "commit_hash": v1_commit}
                ],
            },
        )
        repository._write_current_unlocked(v1_head)  # noqa: SLF001

    report = repair_cutover_dry_run(tmp_path)
    negative_cases = [
        case
        for case in report["cases"]
        if case["family"] == "semantic_negative_lineage"
    ]
    assert len(negative_cases) == 1
    negative_material = negative_cases[0]["review_material"]["material"]
    assert negative_material["decision_hash"] == negative_hash
    assert negative_material["candidate_digest"] == digest
    assert negative_material["action"] == "omit"
    result = publish_recertification(tmp_path, _publish_request(report))
    active = repository.read_manifest(result["head_hash"])
    recertified_commit = repository.read_commit(
        active["chapters"][0]["commit_hash"]
    )
    assert negative_hash in recertified_commit["lineage_decision_hashes"]
    wrapper = repository.recertified_suffix_wrapper(
        recertified_commit["transaction_hash"]
    )
    assert wrapper is not None
    assert wrapper["semantic_negative_lineage_hashes"] == [negative_hash]
    assert negative_hash in service._historical_chapter_lineage(1)  # noqa: SLF001

    next_authority = proposal_authority(service, 1)
    replay_proposal = {
        **next_authority,
        "chapter": 1,
        "chapter_binding": binding,
        "candidates": [candidate.model_dump(mode="json")],
        "observations": [],
        "scan_attestations": [
            {
                "attestation_id": "negative-replay-scan",
                "scanner": "reviewer",
                "scanner_version": "test",
                "chapter_sha256": binding["sha256"],
                "parent_head": next_authority["parent_head"],
                "author_axiom_digest": next_authority[
                    "author_axiom_digest"
                ],
                "entity_registry_digest": next_authority[
                    "entity_registry_digest"
                ],
                "dimensions": [
                    "setting",
                    "timeline",
                    "continuity",
                    "character",
                    "logic",
                ],
                "status": "complete",
                "checked_candidate_digests": [digest],
            }
        ],
    }
    with pytest.raises(
        PreparedTransactionInvalid,
        match="negative_adjudication_candidate_reintroduced",
    ):
        service.prepare(replay_proposal)


def test_v1_suffix_cannot_carry_effect_omitted_by_negative_decision(
    tmp_path: Path,
) -> None:
    from scripts.data_modules.tests.test_canon_v3_service import (
        _batch,
        _presence,
        _project,
    )

    root, manuscript, binding = _project(
        tmp_path, "苏月仍在青云殿内。\n"
    )
    candidate = _presence(manuscript, binding)
    ambiguity = ReviewObservation(
        observation_id="attack-negative-effect",
        candidate_id=candidate.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="该事实必须由人确认",
    )
    service = CanonV3Service(root)
    staged = service.prepare(
        _batch(service, binding, [candidate], [ambiguity])
    )
    decided = record_decisions_v3(
        service,
        [{"case_key": staged["cases"][0]["case_key"], "action": "omit"}],
        snapshot=staged,
    )
    result = finalize_v3(service, snapshot=decided)
    repository = CanonV3Repository(root)
    source_manifest = repository.read_manifest(result["head_hash"])
    entry = source_manifest["chapters"][0]
    source_commit = repository.read_commit(entry["commit_hash"])
    assert source_commit["canon_effects"] == []
    source_transaction = repository.read_transaction(
        source_commit["transaction_hash"]
    )
    forbidden_effects = source_transaction["prepared_transaction"]["effects"]
    assert len(forbidden_effects) == 1

    material = _build_material(root, 0)
    v1_metadata = material.genesis_metadata()
    v1_metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    with repository.locked():
        v1_genesis = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": v1_metadata,
            },
        )
        attacked_commit = repository._put_payload_unlocked(  # noqa: SLF001
            "commit",
            {
                **source_commit,
                "base_head_hash": v1_genesis,
                "predecessor_commit_hash": None,
                "canon_effects": forbidden_effects,
            },
        )
        attacked_head = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 1,
                "parent_head_hash": v1_genesis,
                "chapters": [
                    {
                        "chapter": 1,
                        "revision": 1,
                        "commit_hash": attacked_commit,
                    }
                ],
            },
        )
        repository._write_current_unlocked(attacked_head)  # noqa: SLF001

    report = audit_cutover(root)
    assert report["state"] == "blocked"
    assert "legacy_recertification_suffix_effects_review_mismatch" in report[
        "reason_codes"
    ]
    assert repository.current_head() == attacked_head


def test_v1_suffix_cannot_reactivate_negative_semantics_with_repacked_evidence(
    tmp_path: Path,
) -> None:
    quote = "谁偷走了灵钥，仍是谜。"
    text = f"{quote}\n{quote}"
    manuscript = tmp_path / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(text, encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 1)
    raw = manuscript.read_bytes()
    quote_raw = quote.encode("utf-8")
    first_start = raw.index(quote_raw)
    second_start = raw.index(quote_raw, first_start + len(quote_raw))

    def loop_candidate(candidate_id: str, source_id: str, start: int) -> FactCandidate:
        return FactCandidate(
            candidate_id=candidate_id,
            claim=OpenLoopCreatedClaim(loop="谁偷走了灵钥"),
            sources=(
                {
                    "source_type": "manuscript_span",
                    "source_id": source_id,
                    "document_sha256": binding["sha256"],
                    "chapter": 1,
                    "start": start,
                    "end": start + len(quote_raw),
                    "quote": quote,
                    "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
                },
            ),
            support_map={"loop": (source_id,)},
        )

    rejected = loop_candidate("loop-negative", "loop-first", first_start)
    repacked = loop_candidate("loop-active", "loop-second", second_start)
    assert candidate_digest(rejected) != candidate_digest(repacked)
    from scripts.data_modules.canon_v3.evidence import semantic_claim_digest

    assert semantic_claim_digest(rejected) == semantic_claim_digest(repacked)

    service = CanonV3Service(tmp_path)
    authority = proposal_authority(service, 1)
    rejected_digest = candidate_digest(rejected)
    ambiguity = ReviewObservation(
        observation_id="semantic-negative",
        candidate_id=rejected.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="作者明确不把该语义写入正史",
    )

    def scan(digest: str, attestation_id: str) -> dict:
        return {
            "attestation_id": attestation_id,
            "scanner": "reviewer",
            "scanner_version": "test",
            "chapter_sha256": binding["sha256"],
            "parent_head": authority["parent_head"],
            "author_axiom_digest": authority["author_axiom_digest"],
            "entity_registry_digest": authority["entity_registry_digest"],
            "dimensions": [
                "setting",
                "timeline",
                "continuity",
                "character",
                "logic",
            ],
            "status": "complete",
            "checked_candidate_digests": [digest],
        }

    staged = service.prepare(
        {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [rejected.model_dump(mode="json")],
            "observations": [ambiguity.model_dump(mode="json")],
            "scan_attestations": [scan(rejected_digest, "negative-scan")],
        }
    )
    decided = record_decisions_v3(
        service,
        [{"case_key": staged["cases"][0]["case_key"], "action": "omit"}],
        snapshot=staged,
    )
    negative_hash = service._read_staging_unlocked().decision_hashes[0]  # noqa: SLF001
    repository = service.repository
    parent_head = repository.current_head(validate=True)
    assert parent_head == authority["parent_head"]

    repacked_digest = candidate_digest(repacked)
    attestation = ScanAttestation.model_validate(
        scan(repacked_digest, "repacked-scan")
    )
    prepared = service._compile_with_entity_registry(  # noqa: SLF001
        (repacked,),
        (),
        parent_head,
        (attestation,),
        chapter=1,
    )
    envelope = PreparedEnvelope(
        chapter=1,
        chapter_binding=binding,
        prepared_transaction=prepared,
        candidates=(repacked,),
        observations=(),
        scan_attestations=(attestation,),
        source_workflow_digest=authority["workflow_digest"],
        author_axiom_digest=authority["author_axiom_digest"],
    )
    with service.staging_lock:
        service._clear_staging_unlocked()  # noqa: SLF001
    active = repository._seal_objects(  # noqa: SLF001
        chapter=1,
        transaction=envelope.model_dump(mode="json"),
        expected_head=parent_head,
        lineage_decisions=[negative_hash],
        canon_effects=[
            effect.model_dump(mode="json") for effect in prepared.effects
        ],
    )
    source_manifest = repository.read_manifest(active.head_hash)
    source_entry = source_manifest["chapters"][0]
    source_commit = repository.read_commit(source_entry["commit_hash"])

    material = _build_material(tmp_path, 0)
    v1_metadata = material.genesis_metadata()
    v1_metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    with repository.locked():
        v1_genesis = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": v1_metadata,
            },
        )
        v1_commit = repository._put_payload_unlocked(  # noqa: SLF001
            "commit",
            {
                **source_commit,
                "base_head_hash": v1_genesis,
                "predecessor_commit_hash": None,
            },
        )
        v1_head = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 1,
                "parent_head_hash": v1_genesis,
                "chapters": [
                    {"chapter": 1, "revision": 1, "commit_hash": v1_commit}
                ],
            },
        )
        repository._write_current_unlocked(v1_head)  # noqa: SLF001

    report = audit_cutover(tmp_path)
    assert report["state"] == "blocked"
    assert "legacy_recertification_negative_lineage_reactivated" in report[
        "reason_codes"
    ]
    assert repository.current_head() == v1_head


def test_v1_recertification_fails_closed_instead_of_dropping_active_author_axioms(
    tmp_path: Path,
) -> None:
    from scripts.data_modules.tests.test_canon_v3_author_axiom import (
        _decide_all,
        _draft_record,
        _finalize,
        _proposal,
        _service,
    )
    from scripts.data_modules.canon_v3.author_axiom import (
        AuthorAxiomStageConflict,
    )

    # The supported workflow cannot create this mixed state: v1 immediately
    # owns the single recertification transaction and blocks axiom prepare.
    plain_root = tmp_path / "plain-v1"
    _install_v1_genesis(plain_root)
    plain_service = CanonV3Service(plain_root)
    plain_record = _draft_record(
        plain_root,
        name="must-block",
        key="blocked_rule",
        value="不会发布",
    )
    with pytest.raises(
        AuthorAxiomStageConflict,
        match="workflow_not_healthy",
    ):
        plain_service.prepare_author_axioms(
            _proposal(plain_service, [plain_record])
        )

    service = _service(tmp_path)
    root = service.project_root
    record = _draft_record(
        root,
        name="recert-preserve",
        key="death_is_irreversible",
        value="死亡不可逆",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    _decide_all(service)
    _finalize(service)
    repository = CanonV3Repository(root)
    active_manifest = repository.current_manifest()
    assert active_manifest is not None
    axiom_entry = active_manifest["author_axiom_commits"][0]
    axiom_commit = repository.read_author_axiom_commit(
        axiom_entry["commit_hash"]
    )

    material = _build_material(root, 0)
    v1_metadata = material.genesis_metadata()
    v1_metadata["schema_version"] = "canon-v3/legacy-genesis/v1"
    with repository.locked():
        v1_genesis = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 0,
                "parent_head_hash": None,
                "chapters": [],
                "genesis_metadata": v1_metadata,
            },
        )
        rebound_axiom_commit = repository._put_payload_unlocked(  # noqa: SLF001
            "author_axiom_commit",
            {**axiom_commit, "base_head_hash": v1_genesis},
        )
        v1_head = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": 1,
                "parent_head_hash": v1_genesis,
                "chapters": [],
                "author_axiom_commits": [
                    {"revision": 1, "commit_hash": rebound_axiom_commit}
                ],
            },
        )
        repository._write_current_unlocked(v1_head)  # noqa: SLF001

    report = audit_cutover(root)
    assert report["state"] == "blocked"
    assert "legacy_recertification_active_author_axioms_require_rebind" in (
        report["reason_codes"]
    )
    assert rebound_axiom_commit in report["details"]
    workflow = WorkflowAuthority(root).snapshot()
    assert workflow["bootstrap_mode"] == "recertification"
    assert workflow["recertification_state"] == "blocked"
    assert (
        "legacy_recertification_active_author_axioms_require_rebind"
        in workflow["recertification_reason_codes"]
    )
    assert workflow["primary_action"]["code"] == (
        "audit_blocked_legacy_recertification"
    )
    assert repository.current_head() == v1_head

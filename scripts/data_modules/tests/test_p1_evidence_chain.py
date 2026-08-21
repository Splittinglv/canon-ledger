from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import get_args
from unittest.mock import patch

import pytest

from data_modules.canon_evidence import (
    CANON_MUTATING_EVENT_TYPES,
    CHAPTER_COMMIT_SCHEMA_V1,
    CHAPTER_COMMIT_SCHEMA_V2,
    EVIDENCE_CONTRACT_VERSION,
    classify_evidence_contract,
    validate_event_evidence,
    validate_mutation_source_bindings,
)
from data_modules.canonical_history import load_canonical_history
from data_modules.chapter_commit_schema import (
    ChapterCommitSchema,
    FACT_COVERAGE_DIMENSIONS,
    normalize_accepted_events,
)
from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.story_event_schema import StoryEvent

from .review_test_helpers import standard_review, write_current_chapter_contract


class _LegacyFixtureCommitService(ChapterCommitService):
    """Test-only builder for frozen v1/v2 migration fixtures.

    Production entry points intentionally reject every legacy fact mutation.
    These tests still exercise hostile on-disk legacy envelopes that the
    read-only migration parser must diagnose, so their fixture builder bypasses
    only the public retirement guard.
    """

    def _assert_v2_write_allowed(self) -> None:
        return None

    def build_commit(self, **kwargs):
        # Legacy commit assembly internally asks the retired human-review
        # service to materialize its frozen envelope. Keep that bypass scoped
        # to this fixture call; no production object receives a compatibility
        # switch.
        with patch(
            "data_modules.workflow_authority."
            "WorkflowAuthority.assert_legacy_fact_mutation_disabled",
            return_value=None,
        ):
            return super().build_commit(**kwargs)


def _write_strict_empty_commit(
    root: Path,
    *,
    extraction_overrides: dict[str, object] | None = None,
    persist: bool = True,
) -> tuple[Path, dict[str, object]]:
    chapter = 1
    chapter_path = root / "正文" / "第0001章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("第1章\n林舟走进大厅。", encoding="utf-8")
    write_current_chapter_contract(root, chapter)
    binding = build_chapter_binding(root, chapter)

    extraction: dict[str, object] = {
        "accepted_events": [],
        "state_deltas": [],
        "entity_deltas": [],
        "timeline_events": [],
        "entities_appeared": [],
        "scenes": [],
        "fact_coverage": {
            field: "complete" for field in FACT_COVERAGE_DIMENSIONS
        },
        "fact_verification": {
            field: "supported" for field in FACT_COVERAGE_DIMENSIONS
        },
    }
    extraction.update(extraction_overrides or {})
    extraction["chapter_binding"] = binding

    service = _LegacyFixtureCommitService(root)
    commit = service.build_commit(
        chapter=chapter,
        review_result=standard_review(binding),
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        disambiguation_result={
            "pending": [],
            "chapter_binding": binding,
        },
        extraction_result=extraction,
    )
    assert (
        commit["extraction_result"]["evidence_contract"]
        == EVIDENCE_CONTRACT_VERSION
    )
    assert commit["meta"]["schema_version"] == CHAPTER_COMMIT_SCHEMA_V2
    commit_path = (
        service.persist_commit(commit)
        if persist
        else root / ".story-system" / "commits" / "chapter_001.commit.json"
    )
    return commit_path, commit


def _source_events() -> tuple[dict[str, object], str]:
    chapter_text = "\n".join(
        (
            "林舟从炼气突破到筑基。",
            "林舟走进大厅。",
            "黎明时，城门开启。",
        )
    )
    events = normalize_accepted_events(
        1,
        [
            {
                "event_id": "evt-state",
                "chapter": 1,
                "event_type": "character_state_changed",
                "subject": "hero",
                "payload": {
                    "field": "realm",
                    "old": "炼气",
                    "new": "筑基",
                    "evidence_quote": "林舟从炼气突破到筑基。",
                },
            },
            {
                "event_id": "evt-entity",
                "chapter": 1,
                "event_type": "entity_observed",
                "subject": "hero",
                "payload": {"evidence_quote": "林舟走进大厅。"},
            },
            {
                "event_id": "evt-time",
                "chapter": 1,
                "event_type": "timeline_observed",
                "subject": "clock",
                "payload": {"evidence_quote": "黎明时，城门开启。"},
            },
        ],
    )
    return validate_event_evidence(events, chapter_text), chapter_text


def _linked_records(field: str, row: dict[str, object]) -> dict[str, object]:
    records: dict[str, object] = {
        "state_deltas": [],
        "entity_deltas": [],
        "timeline_events": [],
    }
    records[field] = [row]
    return records


@pytest.mark.parametrize("event_type", sorted(CANON_MUTATING_EVENT_TYPES))
def test_every_canon_mutating_event_rejects_missing_evidence_quote(
    event_type: str,
) -> None:
    event = {
        "event_id": f"evt-{event_type}",
        "chapter": 1,
        "event_type": event_type,
        "subject": "subject",
        "payload": {},
    }

    with pytest.raises(ValueError, match="evidence_quote"):
        normalize_accepted_events(1, [event])


def test_evidence_contract_covers_all_fifteen_story_event_types() -> None:
    declared_types = frozenset(
        get_args(StoryEvent.model_fields["event_type"].annotation)
    )
    assert len(declared_types) == 15
    assert CANON_MUTATING_EVENT_TYPES == declared_types


@pytest.mark.parametrize(
    ("field", "row"),
    [
        (
            "state_deltas",
            {
                "entity_id": "hero",
                "field": "realm",
                "old": "炼气",
                "new": "筑基",
            },
        ),
        (
            "entity_deltas",
            {"entity_id": "hero", "canonical_name": "林舟"},
        ),
        (
            "timeline_events",
            {
                "timeline_id": "tl-1",
                "sequence": 1,
                "event": "城门开启",
                "evidence_fragment": "黎明时，城门开启",
            },
        ),
    ],
)
def test_linked_canon_record_rejects_missing_source_event_id(
    field: str,
    row: dict[str, object],
) -> None:
    event_index, _ = _source_events()

    with pytest.raises(ValueError, match="source_event_id must be non-empty"):
        validate_mutation_source_bindings(_linked_records(field, row), event_index)


@pytest.mark.parametrize(
    ("field", "row"),
    [
        (
            "state_deltas",
            {
                "entity_id": "hero",
                "field": "realm",
                "old": "炼气",
                "new": "筑基",
                "source_event_id": "evt-state",
            },
        ),
        (
            "entity_deltas",
            {
                "entity_id": "hero",
                "canonical_name": "林舟",
                "source_event_id": "evt-entity",
            },
        ),
        (
            "timeline_events",
            {
                "timeline_id": "tl-1",
                "sequence": 1,
                "event": "城门开启",
                "source_event_id": "evt-time",
                "evidence_fragment": "黎明时，城门开启",
            },
        ),
    ],
)
def test_linked_canon_record_rejects_dangling_source_event_id(
    field: str,
    row: dict[str, object],
) -> None:
    event_index, _ = _source_events()
    dangling = deepcopy(row)
    dangling["source_event_id"] = "evt-does-not-exist"

    with pytest.raises(ValueError, match="does not reference an accepted event"):
        validate_mutation_source_bindings(
            _linked_records(field, dangling), event_index
        )


@pytest.mark.parametrize(
    ("field", "row", "message"),
    [
        (
            "state_deltas",
            {
                "entity_id": "hero",
                "field": "realm",
                "old": "炼气",
                "new": "金丹",
                "source_event_id": "evt-state",
            },
            "new does not match",
        ),
        (
            "entity_deltas",
            {
                "entity_id": "villain",
                "canonical_name": "林舟",
                "source_event_id": "evt-entity",
            },
            "entity_id does not match",
        ),
        (
            "timeline_events",
            {
                "timeline_id": "tl-1",
                "sequence": 1,
                "event": "城门开启",
                "source_event_id": "evt-time",
                "evidence_fragment": "午夜时，城门关闭",
            },
            "verbatim fragment",
        ),
    ],
)
def test_linked_canon_record_rejects_semantically_mismatched_source_event(
    field: str,
    row: dict[str, object],
    message: str,
) -> None:
    event_index, _ = _source_events()

    with pytest.raises(ValueError, match=message):
        validate_mutation_source_bindings(_linked_records(field, row), event_index)


def test_strict_commit_with_manually_injected_unbound_delta_is_not_trusted(
    tmp_path: Path,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path)
    tampered = deepcopy(commit)
    tampered["extraction_result"]["state_deltas"].append(
        {
            "entity_id": "hero",
            "field": "realm",
            "old": "炼气",
            "new": "筑基",
            "source_event_id": "evt-injected",
        }
    )
    commit_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    history = load_canonical_history(tmp_path, as_of_chapter=1)

    assert history.valid_chapters == []
    assert history.state_changes == []
    assert any(
        "evidence_contract_invalid" in source for source in history.invalid_sources
    )


def test_strict_commit_display_metadata_does_not_create_canon_entities(
    tmp_path: Path,
) -> None:
    _write_strict_empty_commit(
        tmp_path,
        extraction_overrides={
            "entities_appeared": [
                {"id": "ghost", "type": "角色", "mentions": ["幽灵"]}
            ],
            "scenes": [{"characters": ["specter"]}],
        },
    )

    history = load_canonical_history(tmp_path, as_of_chapter=1)

    assert "ghost" not in history.entities
    assert "specter" not in history.entities


def test_illegal_verification_claim_never_aggregates_to_verified(
    tmp_path: Path,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path)
    tampered = deepcopy(commit)
    tampered["extraction_result"]["fact_verification"]["knowledge"] = (
        "definitely"
    )
    commit_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    history = load_canonical_history(tmp_path, as_of_chapter=1)

    assert history.verification["knowledge"] != "verified"
    assert history.coverage["knowledge"] != "complete"


def test_persist_revalidates_tampered_strict_mutation_and_writes_nothing(
    tmp_path: Path,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path, persist=False)
    commit["extraction_result"]["state_deltas"].append(
        {
            "entity_id": "hero",
            "field": "realm",
            "new": "筑基",
            "source_event_id": "evt-injected-after-build",
        }
    )

    with pytest.raises(ValueError, match="does not reference an accepted event"):
        _LegacyFixtureCommitService(tmp_path).persist_commit(commit)

    assert not commit_path.exists()


@pytest.mark.parametrize("marker_owner", ["extraction_result", "provenance"])
def test_persist_requires_both_current_evidence_contract_markers(
    tmp_path: Path,
    marker_owner: str,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path, persist=False)
    del commit[marker_owner]["evidence_contract"]

    with pytest.raises(ValueError, match="invalid_or_downgraded"):
        _LegacyFixtureCommitService(tmp_path).persist_commit(commit)

    assert not commit_path.exists()


def test_evidence_contract_classifier_enforces_v2_anti_downgrade_matrix(
    tmp_path: Path,
) -> None:
    _, strict_v2 = _write_strict_empty_commit(tmp_path, persist=False)
    strict_v1 = deepcopy(strict_v2)
    strict_v1["meta"]["schema_version"] = CHAPTER_COMMIT_SCHEMA_V1
    legacy_v1 = deepcopy(strict_v1)
    legacy_v1["extraction_result"].pop("evidence_contract")
    legacy_v1["provenance"].pop("evidence_contract")
    markerless_v2 = deepcopy(legacy_v1)
    markerless_v2["meta"]["schema_version"] = CHAPTER_COMMIT_SCHEMA_V2
    partial_v1 = deepcopy(strict_v1)
    partial_v1["provenance"].pop("evidence_contract")
    unknown_marker_v1 = deepcopy(strict_v1)
    unknown_marker_v1["extraction_result"]["evidence_contract"] = "canon-evidence/v9"
    unknown_marker_v1["provenance"]["evidence_contract"] = "canon-evidence/v9"

    assert classify_evidence_contract(strict_v2) == "strict"
    assert classify_evidence_contract(strict_v1) == "strict"
    assert classify_evidence_contract(legacy_v1) == "legacy"
    assert classify_evidence_contract(markerless_v2) == "invalid"
    assert classify_evidence_contract(partial_v1) == "invalid"
    assert classify_evidence_contract(unknown_marker_v1) == "invalid"
    with pytest.raises(ValueError, match="evidence envelope is invalid"):
        ChapterCommitSchema.model_validate(markerless_v2)
    assert ChapterCommitSchema.model_validate(legacy_v1).meta.schema_version == (
        CHAPTER_COMMIT_SCHEMA_V1
    )


def test_markerless_v1_is_readable_but_public_legacy_replay_is_retired(
    tmp_path: Path,
) -> None:
    commit_path, strict = _write_strict_empty_commit(tmp_path, persist=False)
    legacy = deepcopy(strict)
    legacy["meta"]["schema_version"] = CHAPTER_COMMIT_SCHEMA_V1
    legacy["extraction_result"].pop("evidence_contract")
    legacy["provenance"].pop("evidence_contract")
    service = _LegacyFixtureCommitService(tmp_path)

    with pytest.raises(ValueError, match="legacy_commit_requires_explicit_replay"):
        service.persist_commit(legacy)
    assert not commit_path.exists()

    path = service.persist_commit(legacy, allow_legacy_replay=True)
    assert path.is_file()
    assert load_canonical_history(tmp_path, 1).valid_chapters == [1]

    from data_modules.projection_rebuild import rebuild_all_projections

    report = rebuild_all_projections(tmp_path, reason="legacy_replay_test")
    assert report["ok"] is False
    assert "legacy_fact_mutation_disabled" in report["detail"]


@pytest.mark.parametrize(
    "method_name",
    ["persist_commit", "apply_projections", "apply_projection_writers"],
)
def test_v2_double_marker_deletion_and_mutation_has_no_write_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path, persist=False)
    commit["extraction_result"].pop("evidence_contract")
    commit["provenance"].pop("evidence_contract")
    commit["extraction_result"]["state_deltas"].append(
        {
            "entity_id": "hero",
            "field": "realm",
            "new": "金丹",
            "source_event_id": "evt-injected-after-downgrade",
        }
    )
    writer_calls: list[bool] = []
    service = _LegacyFixtureCommitService(tmp_path)
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    assert classify_evidence_contract(commit) == "invalid"
    with pytest.raises(ValueError, match="invalid_or_downgraded"):
        getattr(service, method_name)(commit)

    assert writer_calls == []
    assert not commit_path.exists()
    assert not (
        tmp_path / ".story-system" / "events" / "chapter_001.events.json"
    ).exists()


def test_history_rejects_persisted_v2_downgrade_and_injected_mutation(
    tmp_path: Path,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path)
    tampered = deepcopy(commit)
    tampered["extraction_result"].pop("evidence_contract")
    tampered["provenance"].pop("evidence_contract")
    tampered["extraction_result"]["state_deltas"].append(
        {
            "entity_id": "hero",
            "field": "realm",
            "new": "金丹",
            "source_event_id": "evt-injected-on-disk",
        }
    )
    commit_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    history = load_canonical_history(tmp_path, 1)

    assert history.valid_chapters == []
    assert history.state_changes == []
    assert any(
        "evidence_contract_invalid:invalid_envelope" in source
        for source in history.invalid_sources
    )

    from data_modules.projection_rebuild import rebuild_all_projections

    rebuild = rebuild_all_projections(tmp_path, reason="downgrade_rebuild_test")
    assert rebuild["ok"] is False
    assert rebuild["error"] == "invalid_evidence_contract_envelope"


def test_persist_revalidates_strict_schema_and_current_chapter_binding(
    tmp_path: Path,
) -> None:
    commit_path, commit = _write_strict_empty_commit(tmp_path, persist=False)
    malformed = deepcopy(commit)
    malformed["extraction_result"]["fact_verification"]["knowledge"] = "illegal"

    with pytest.raises(ValueError, match="commit_schema_invalid"):
        _LegacyFixtureCommitService(tmp_path).persist_commit(malformed)
    assert not commit_path.exists()

    (tmp_path / "正文" / "第0001章.md").write_text(
        "第1章\n正文在 build 后被修改。",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="chapter_content_hash_mismatch"):
        _LegacyFixtureCommitService(tmp_path).persist_commit(commit)
    assert not commit_path.exists()


@pytest.mark.parametrize(
    "method_name",
    ["apply_projections", "apply_projection_writers"],
)
def test_projection_boundaries_reject_tampered_event_before_loading_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
) -> None:
    commit_path, commit = _write_strict_empty_commit(
        tmp_path,
        persist=False,
        extraction_overrides={
            "accepted_events": [
                {
                    "event_id": "evt-hero-observed",
                    "chapter": 1,
                    "event_type": "entity_observed",
                    "subject": "hero",
                    "payload": {"evidence_quote": "林舟走进大厅。"},
                }
            ]
        },
    )
    del commit["extraction_result"]["accepted_events"][0]["payload"][
        "evidence_quote"
    ]
    writer_calls: list[bool] = []
    service = _LegacyFixtureCommitService(tmp_path)
    monkeypatch.setattr(
        service,
        "_projection_writers",
        lambda: writer_calls.append(True) or {},
    )

    with pytest.raises(ValueError, match="evidence_quote"):
        getattr(service, method_name)(commit)

    assert writer_calls == []
    assert not commit_path.exists()
    assert not (
        tmp_path / ".story-system" / "events" / "chapter_001.events.json"
    ).exists()

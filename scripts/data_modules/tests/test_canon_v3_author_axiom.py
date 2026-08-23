from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.data_modules.canon_v3.author_axiom import (
    AuthorAxiomChannel,
    AuthorAxiomDecisionError,
    AuthorAxiomEvidenceError,
    AuthorAxiomFinalizeBlocked,
    AuthorAxiomStageConflict,
    _record_set_digest,
)
from scripts.data_modules.canon_v3.projection import read_projection, rebuild_projection
from scripts.data_modules.canon_v3.repository import CanonHeadConflict
from scripts.data_modules.canon_v3.schema import AuthorAxiomRecord, canonical_digest
from scripts.data_modules.canon_v3.migration import LegacyMigrationError
from scripts.data_modules.canon_v3.service import (
    ActiveTransactionError,
    CanonV3Service,
)
from scripts.data_modules.config import DataModulesConfig
from scripts.data_modules.memory_contract_adapter import MemoryContractAdapter
from scripts.data_modules.workflow_authority import WorkflowAuthority


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _service(tmp_path: Path) -> CanonV3Service:
    root = tmp_path / "book"
    service = CanonV3Service(root)
    service.initialize_new_project()
    return service


def _draft_record(
    root: Path,
    *,
    name: str,
    key: str,
    value: object,
    category: str = "world_rule",
) -> dict:
    relative = Path(".canon-ledger/tmp/author_axioms") / f"{name}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "canon-v3/author-axiom-draft/v1",
        "author_axioms": {key: value},
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(raw)
    quote = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    return {
        "axiom_key": key,
        "category": category,
        "source": {
            "source_type": "author_axiom_draft_span",
            "source_id": f"draft-{name}-{key}",
            "document_path": relative.as_posix(),
            "document_sha256": _sha(raw),
            "start": start,
            "end": start + len(quote_raw),
            "quote": quote,
            "quote_sha256": _sha(quote_raw),
            "json_pointer": f"/author_axioms/{key}",
            "value": value,
            "value_sha256": canonical_digest(value),
        },
    }


def _proposal(
    service: CanonV3Service,
    records: list[dict],
    *,
    genesis_overrides: list[dict] | None = None,
) -> dict:
    workflow = service.workflow_snapshot()
    return {
        "schema_version": "canon-v3/author-axiom-proposal/v2",
        "parent_head": workflow["head_hash"],
        "workflow_digest": workflow["workflow_digest"],
        "active_author_axiom_digest": workflow["author_axiom_digest"],
        "expected_stage_digest": workflow.get("stage_digest"),
        "records": records,
        "genesis_overrides": genesis_overrides or [],
    }


def _decide_all(
    service: CanonV3Service, *, action: str = "approve"
) -> dict:
    status = service.author_axiom_status()
    assert all(action in case["allowed_actions"] for case in status["cases"])
    return service.record_author_axiom_decisions(
        {
            "schema_version": "canon-v3/author-axiom-decision-request/v2",
            "expected_stage_digest": status["stage_digest"],
            "transaction_hash": status["transaction_hash"],
            "decisions": [
                {
                    "case_key": case["case_key"],
                    **case["decision_binding"],
                    "action": action,
                }
                for case in status["cases"]
            ],
        }
    )


def _finalize(service: CanonV3Service) -> dict:
    status = service.author_axiom_status()
    return service.finalize_author_axioms(
        {
            "schema_version": "canon-v3/author-axiom-finalize-request/v2",
            "expected_stage_digest": status["stage_digest"],
            "transaction_hash": status["transaction_hash"],
            "finalize_token": status["finalize_token"],
        }
    )


def test_axiom_publish_is_head_bound_and_does_not_advance_chapters(tmp_path) -> None:
    service = _service(tmp_path)
    before = service.workflow_snapshot()
    record = _draft_record(
        service.project_root,
        name="world",
        key="death_is_irreversible",
        value="死亡不可逆",
    )

    staged = service.prepare_author_axioms(_proposal(service, [record]))
    assert staged["state"] == "awaiting_human"
    public_case = staged["cases"][0]
    assert public_case["allowed_actions"] == [
        "approve",
        "omit",
        "rewrite",
    ]
    assert public_case["decision_binding"] == {
        "target_digest": public_case["target_digest"],
        "material_digest": public_case["review_material"][
            "material_digest"
        ],
        "expected_decision_head_hash": None,
    }
    assert service.workflow_snapshot()["transaction_kind"] == "author_axiom"
    decided = _decide_all(service)
    assert decided["state"] == "ready_to_finalize"
    result = _finalize(service)

    after = service.workflow_snapshot()
    assert result["head_hash"] != before["head_hash"]
    assert after["latest_chapter"] == before["latest_chapter"] == 0
    assert after["allowed_write_chapters"] == before["allowed_write_chapters"] == [1]
    assert after["author_axiom_digest"] != before["author_axiom_digest"]
    projection = read_projection(service.project_root, require_fresh=True)
    assert projection["binding"]["head_hash"] == result["head_hash"]
    assert projection["author_axioms"]["records"][0]["axiom_key"] == (
        "death_is_irreversible"
    )


@pytest.mark.parametrize(
    ("key", "category", "value"),
    [
        ("core_motivation", "character_permanent_state", "复仇是永恒驱动力"),
        ("hero_personality", "character_identity", "人格冷漠且寡言"),
        ("immutable_law", "world_rule", "全书文风冷峻，短句为主"),
        ("rule_17", "world_rule", "每段最多三句话"),
    ],
)
def test_soft_design_cannot_enter_author_axiom_prepare(
    tmp_path, key: str, category: str, value: str
) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name=f"soft-{key}",
        key=key,
        value=value,
        category=category,
    )

    with pytest.raises(
        AuthorAxiomEvidenceError,
        match="canon_v3_author_axiom_non_fact_semantics_forbidden",
    ):
        service.prepare_author_axioms(_proposal(service, [record]))

    assert service.active_author_axioms()["records"] == []


def test_legacy_active_soft_axiom_blocks_writing_until_human_supersession(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    raw_record = _draft_record(
        service.project_root,
        name="legacy-soft-active",
        key="core_motivation",
        value="复仇是永恒驱动力",
        category="character_permanent_state",
    )
    record = AuthorAxiomRecord.model_validate(raw_record)
    repository = service.repository
    head = repository.current_head(validate=True)
    assert head is not None
    manifest = repository.read_manifest(head, validate_references=True)
    with repository.locked():
        transaction_hash = repository._put_payload_unlocked(  # noqa: SLF001
            "author_axiom_transaction",
            {"schema_version": "canon-v3/legacy-soft-transaction/v1"},
        )
        commit_hash = repository._put_payload_unlocked(  # noqa: SLF001
            "author_axiom_commit",
            {
                "schema_version": "canon-v3/author-axiom-commit/v1",
                "revision": 1,
                "transaction_hash": transaction_hash,
                "decision_hashes": [],
                "lineage_decision_hashes": [],
                "base_head_hash": head,
                "previous_author_axiom_commit_hash": None,
                "records": [record.model_dump(mode="json")],
                "axiom_set_digest": _record_set_digest((record,)),
                "superseded_legacy_admission_digests": [],
            },
        )
        polluted_head = repository._put_payload_unlocked(  # noqa: SLF001
            "manifest",
            {
                "schema_version": "canon-v3/active-manifest/v1",
                "generation": int(manifest["generation"]) + 1,
                "parent_head_hash": head,
                "chapters": list(manifest.get("chapters") or []),
                "author_axiom_commits": [
                    {"revision": 1, "commit_hash": commit_hash}
                ],
            },
        )
        repository._write_current_unlocked(polluted_head)  # noqa: SLF001
    rebuild_projection(service.project_root)

    blocked = WorkflowAuthority(service.project_root).snapshot()
    assert blocked["state"] == "migration_required"
    assert blocked["can_write_next"] is False
    assert blocked["bootstrap_mode"] == "author_axiom_fact_boundary"
    assert blocked["primary_action"]["code"] == (
        "supersede_active_soft_author_axioms"
    )
    assert blocked["non_fact_author_axiom_keys"] == ["core_motivation"]

    unrelated = _draft_record(
        service.project_root,
        name="unrelated-during-cleanup",
        key="death_is_irreversible",
        value="死者不能复生",
    )
    with pytest.raises(
        AuthorAxiomEvidenceError,
        match="canon_v3_active_author_axiom_cleanup_not_exact",
    ):
        service.prepare_author_axioms(_proposal(service, [unrelated]))

    staged = service.prepare_author_axioms(_proposal(service, []))
    assert staged["state"] == "awaiting_human"
    _decide_all(service)
    _finalize(service)

    ready = WorkflowAuthority(service.project_root).snapshot()
    assert ready["state"] == "ready"
    assert ready["can_write_next"] is True
    assert service.active_author_axioms()["records"] == []


def test_author_axiom_public_binding_rejects_stale_material(tmp_path) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="stale-material",
        key="moon_gate_rule",
        value="月门只能在夜间开启",
    )
    staged = service.prepare_author_axioms(_proposal(service, [record]))
    case = staged["cases"][0]
    stale_binding = dict(case["decision_binding"])
    stale_binding["material_digest"] = "0" * 64

    with pytest.raises(
        AuthorAxiomDecisionError,
        match="material_precondition",
    ):
        service.record_author_axiom_decisions(
            {
                "schema_version": (
                    "canon-v3/author-axiom-decision-request/v2"
                ),
                "expected_stage_digest": staged["stage_digest"],
                "transaction_hash": staged["transaction_hash"],
                "decisions": [
                    {
                        "case_key": case["case_key"],
                        **stale_binding,
                        "action": case["allowed_actions"][0],
                    }
                ],
            }
        )

    assert service.author_axiom_status()["cases"][0][
        "decision_head_hash"
    ] is None
    assert _decide_all(service)["state"] == "ready_to_finalize"


def test_unpublished_draft_never_enters_projection_or_context(tmp_path) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="draft-only",
        key="moon_rule",
        value="月亮永不落下",
    )
    service.prepare_author_axioms(_proposal(service, [record]))

    projection = read_projection(service.project_root, require_fresh=True)
    assert projection["author_axioms"]["records"] == []
    pack = MemoryContractAdapter(
        DataModulesConfig(project_root=service.project_root)
    ).load_context(1)
    assert pack.sections["author_axioms"] == []
    assert "月亮永不落下" not in json.dumps(
        pack.to_dict(), ensure_ascii=False
    )


def test_published_axiom_survives_draft_deletion_and_is_in_context(tmp_path) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="durable",
        key="sun_rule",
        value="太阳每天升起",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    _decide_all(service)
    _finalize(service)
    (service.project_root / record["source"]["document_path"]).unlink()

    active = service.active_author_axioms()
    assert active["records"][0]["source"]["value"] == "太阳每天升起"
    pack = MemoryContractAdapter(
        DataModulesConfig(project_root=service.project_root)
    ).load_context(1)
    assert pack.sections["author_axioms"][0]["axiom_key"] == "sun_rule"


def test_asof_export_includes_sanitized_active_axiom_not_staging_or_legacy(
    tmp_path,
) -> None:
    """Regression: an axiom digest alone is insufficient review context."""

    from scripts.data_modules.canon_v3.query import CanonQueryFacade

    service = _service(tmp_path)
    active_record = _draft_record(
        service.project_root,
        name="asof-active",
        key="moon_gate_rule",
        value="月门只能在夜间开启",
    )
    service.prepare_author_axioms(_proposal(service, [active_record]))
    _decide_all(service)
    published = _finalize(service)

    # A second proposal exists only in STAGING. It must not leak into an
    # active-as-of view even though the workflow token now reflects staging.
    active_records = service.active_author_axioms()["records"]
    staged_record = _draft_record(
        service.project_root,
        name="asof-staging",
        key="unpublished_sun_rule",
        value="太阳永不落下（未发布）",
    )
    staged = service.prepare_author_axioms(
        _proposal(service, [*active_records, staged_record])
    )
    assert staged["state"] == "awaiting_human"

    # Poison a legacy cache with a conflicting assertion. Canon v3 as-of
    # export must continue to use only CURRENT/projection authority.
    (service.project_root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {"author_axioms": {"moon_gate_rule": "月门白昼开启（伪 legacy）"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    adapter = MemoryContractAdapter(
        DataModulesConfig(project_root=service.project_root)
    )
    snapshot = adapter.export_asof_snapshot(chapter=1)
    public_axioms = snapshot["author_axioms"]

    assert public_axioms["schema_version"] == (
        "canon-v3/asof-author-axioms/v1"
    )
    assert public_axioms["head_hash"] == published["head_hash"]
    assert public_axioms["head_hash"] == snapshot["canon_binding"]["head_hash"]
    assert public_axioms["author_axiom_digest"] == (
        snapshot["author_axiom_digest"]
    )
    assert [item["axiom_key"] for item in public_axioms["records"]] == [
        "moon_gate_rule"
    ]
    record = public_axioms["records"][0]
    assert record["value"] == "月门只能在夜间开启"
    assert record["source"]["source_type"] == "author_axiom"
    assert record["source"]["value"] == record["value"]
    assert record["record_digest"] == (
        service.active_author_axioms()["record_digests"]["moon_gate_rule"]
    )
    assert {"start", "end", "quote", "quote_sha256"}.isdisjoint(
        record["source"]
    )

    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert "太阳永不落下（未发布）" not in encoded
    assert "月门白昼开启（伪 legacy）" not in encoded

    query_view = CanonQueryFacade(service.project_root).snapshot(
        as_of_chapter=0
    )
    assert query_view["data"]["author_axioms"] == public_axioms


def test_draft_mutation_and_path_traversal_fail_closed(tmp_path) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="mutable",
        key="gate_rule",
        value="城门日落关闭",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    path = service.project_root / record["source"]["document_path"]
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(AuthorAxiomDecisionError, match="hash_mismatch"):
        _decide_all(service)

    other = _service(tmp_path / "other")
    traversal = _draft_record(
        other.project_root,
        name="safe",
        key="safe_rule",
        value="安全",
    )
    traversal["source"]["document_path"] = "../outside.json"
    with pytest.raises(AuthorAxiomEvidenceError):
        other.prepare_author_axioms(_proposal(other, [traversal]))


def test_equal_value_span_must_belong_to_its_exact_json_pointer(tmp_path) -> None:
    service = _service(tmp_path)
    relative = Path(".canon-ledger/tmp/author_axioms/equal.json")
    path = service.project_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "canon-v3/author-axiom-draft/v1",
        "author_axioms": {"first_rule": "相同", "second_rule": "相同"},
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(raw)
    quote_raw = '"相同"'.encode("utf-8")
    wrong_start = raw.rindex(quote_raw)
    record = {
        "axiom_key": "first_rule",
        "category": "world_rule",
        "source": {
            "source_type": "author_axiom_draft_span",
            "source_id": "wrong-equal-span",
            "document_path": relative.as_posix(),
            "document_sha256": _sha(raw),
            "start": wrong_start,
            "end": wrong_start + len(quote_raw),
            "quote": '"相同"',
            "quote_sha256": _sha(quote_raw),
            "json_pointer": "/author_axioms/first_rule",
            "value": "相同",
            "value_sha256": canonical_digest("相同"),
        },
    }
    with pytest.raises(
        AuthorAxiomEvidenceError, match="span_not_bound_to_json_pointer"
    ):
        service.prepare_author_axioms(_proposal(service, [record]))


def test_negative_lineage_survives_same_value_new_source(tmp_path) -> None:
    service = _service(tmp_path)
    first = _draft_record(
        service.project_root,
        name="first",
        key="blood_rule",
        value="血契不可解除",
    )
    service.prepare_author_axioms(_proposal(service, [first]))
    _decide_all(service, action="omit")
    _finalize(service)

    second = _draft_record(
        service.project_root,
        name="second",
        key="blood_rule",
        value="血契不可解除",
    )
    staged = service.prepare_author_axioms(_proposal(service, [second]))
    assert staged["state"] == "awaiting_human"
    assert staged["cases"][0]["negative_lineage_decision_hashes"]
    with pytest.raises(AuthorAxiomFinalizeBlocked):
        service.finalize_author_axioms(
            {
                "schema_version": "canon-v3/author-axiom-finalize-request/v2",
                "expected_stage_digest": staged["stage_digest"],
                "transaction_hash": staged["transaction_hash"],
                "finalize_token": "0" * 64,
            }
        )


def test_unchanged_active_record_needs_no_live_draft_when_adding_next(tmp_path) -> None:
    service = _service(tmp_path)
    first = _draft_record(
        service.project_root,
        name="first-active",
        key="first_rule",
        value="第一规则",
    )
    service.prepare_author_axioms(_proposal(service, [first]))
    _decide_all(service)
    _finalize(service)
    (service.project_root / first["source"]["document_path"]).unlink()
    active_first = service.active_author_axioms()["records"][0]

    second = _draft_record(
        service.project_root,
        name="second-active",
        key="second_rule",
        value="第二规则",
    )
    staged = service.prepare_author_axioms(
        _proposal(service, [active_first, second])
    )
    assert [item["axiom_key"] for item in staged["cases"]] == ["second_rule"]
    _decide_all(service)
    _finalize(service)
    assert [
        item["axiom_key"] for item in service.active_author_axioms()["records"]
    ] == ["first_rule", "second_rule"]


def test_full_snapshot_omission_creates_explicit_remove_case(tmp_path) -> None:
    service = _service(tmp_path)
    first = _draft_record(
        service.project_root,
        name="keep",
        key="keep_rule",
        value="保留",
    )
    second = _draft_record(
        service.project_root,
        name="remove",
        key="remove_rule",
        value="删除",
    )
    # Each managed draft contains one leaf; full snapshot may combine files.
    service.prepare_author_axioms(_proposal(service, [first, second]))
    _decide_all(service)
    _finalize(service)
    active = service.active_author_axioms()["records"]

    staged = service.prepare_author_axioms(_proposal(service, [active[0]]))
    remove = next(
        item for item in staged["cases"] if item["operation"] == "remove"
    )
    assert remove["review_material"]["prior_record"]["axiom_key"] == (
        "remove_rule"
    )
    assert staged["state"] == "awaiting_human"


def test_chapter_and_axiom_staging_are_mutually_exclusive(tmp_path) -> None:
    from scripts.data_modules.chapter_content_binding import build_chapter_binding
    from scripts.data_modules.tests.canon_v3_protocol_helpers import (
        proposal_authority,
    )

    def empty_chapter_batch(service: CanonV3Service) -> dict:
        manuscript = service.project_root / "正文/第0001章.md"
        manuscript.parent.mkdir(parents=True, exist_ok=True)
        manuscript.write_text("只有气氛。", encoding="utf-8")
        binding = build_chapter_binding(service.project_root, 1)
        authority = proposal_authority(service, 1)
        return {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [],
            "observations": [],
            "scan_attestations": [
                {
                    "attestation_id": "empty-full-scan",
                    "scanner": "reviewer",
                    "scanner_version": "test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority[
                        "author_axiom_digest"
                    ],
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
                    "checked_candidate_digests": [],
                }
            ],
        }

    service = _service(tmp_path / "axiom-first")
    chapter_batch = empty_chapter_batch(service)
    record = _draft_record(
        service.project_root,
        name="mutex",
        key="mutex_rule",
        value="互斥",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    assert service.workflow_snapshot()["transaction_kind"] == "author_axiom"
    with pytest.raises(
        ActiveTransactionError,
        match="author_axiom_staging_blocks_chapter_prepare",
    ):
        service.prepare(chapter_batch)

    other = _service(tmp_path / "chapter-first")
    other.prepare(empty_chapter_batch(other))
    other_record = _draft_record(
        other.project_root,
        name="mutex",
        key="mutex_rule",
        value="互斥",
    )
    with pytest.raises(AuthorAxiomStageConflict):
        other.prepare_author_axioms(_proposal(other, [other_record]))


def test_stale_head_rejects_decision_and_exact_retry_is_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="retry",
        key="retry_rule",
        value="仅发布一次",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    ready = _decide_all(service)
    channel = __import__(
        "scripts.data_modules.canon_v3.author_axiom",
        fromlist=["AuthorAxiomChannel"],
    ).AuthorAxiomChannel(service.project_root, repository=service.repository)
    pointer = channel._read_stage_unlocked()
    assert pointer is not None
    envelope = channel._load_envelope(pointer.transaction_hash)
    heads = channel._decision_heads(pointer)
    active = channel._active_after_decisions(envelope, heads)
    from scripts.data_modules.canon_v3.author_axiom import (
        _record_payload,
        _record_set_digest,
    )

    first = service.repository.seal_author_axiom(
        transaction=pointer.transaction_hash,
        expected_head=envelope.parent_head,
        decisions=pointer.decision_hashes,
        lineage_decisions=pointer.lineage_decision_hashes,
        records=[_record_payload(item) for item in active],
        axiom_set_digest=_record_set_digest(active),
        superseded_legacy_admission_digests=(),
        expected_stage_digest=str(pointer.stage_digest),
        finalize_token=ready["finalize_token"],
    )
    finalize_request = {
        "schema_version": "canon-v3/author-axiom-finalize-request/v2",
        "expected_stage_digest": ready["stage_digest"],
        "transaction_hash": ready["transaction_hash"],
        "finalize_token": ready["finalize_token"],
    }
    # Simulate a lost response: CURRENT changed but AUTHOR_AXIOM_STAGING remains.
    retried = service.finalize_author_axioms(finalize_request)
    assert first.created is True
    assert retried["created"] is False
    assert retried["head_hash"] == first.head_hash
    retried_after_stage_clear = service.finalize_author_axioms(
        finalize_request
    )
    assert retried_after_stage_clear["created"] is False
    assert retried_after_stage_clear["head_hash"] == first.head_hash

    # A much later retry still identifies the exact publication terminal
    # instead of presenting a descendant CURRENT as this transaction's HEAD.
    descendant = service.repository._seal_objects(
        chapter=1,
        transaction={"chapter": 1, "canon_effects": []},
        expected_head=first.head_hash,
        decisions=(),
        canon_effects=(),
    )
    late_retry = service.finalize_author_axioms(finalize_request)
    assert late_retry["head_hash"] == first.head_hash
    assert late_retry["publication_head_hash"] == first.head_hash
    assert late_retry["current_head"] == descendant.head_hash
    assert late_retry["generation"] == first.generation
    assert late_retry["current_generation"] == descendant.generation


def test_author_axiom_decision_rejects_stale_head(tmp_path) -> None:
    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="stale",
        key="stale_rule",
        value="旧版本不能批准",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    staged_parent = service.repository.current_head(validate=True)
    service.repository._seal_objects(
        chapter=1,
        transaction={"chapter": 1, "canon_effects": []},
        expected_head=staged_parent,
        decisions=(),
        canon_effects=(),
    )
    with pytest.raises(CanonHeadConflict):
        _decide_all(service)


def test_concurrent_chapter_and_axiom_prepare_leave_exactly_one_stage(
    tmp_path,
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from scripts.data_modules.chapter_content_binding import build_chapter_binding
    from scripts.data_modules.tests.canon_v3_protocol_helpers import (
        proposal_authority,
    )
    from scripts.data_modules.canon_v3.staging_authority import (
        authoritative_staging_kinds,
    )

    service = _service(tmp_path)
    manuscript = service.project_root / "正文/第0001章.md"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text("并发测试。", encoding="utf-8")
    binding = build_chapter_binding(service.project_root, 1)
    authority = proposal_authority(service, 1)
    chapter_batch = {
        **authority,
        "chapter": 1,
        "chapter_binding": binding,
        "candidates": [],
        "observations": [],
        "scan_attestations": [
            {
                "attestation_id": "race-scan",
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
                "checked_candidate_digests": [],
            }
        ],
    }
    axiom_record = _draft_record(
        service.project_root,
        name="race",
        key="race_rule",
        value="并发互斥",
    )
    axiom_proposal = _proposal(service, [axiom_record])
    barrier = threading.Barrier(2)

    def run_chapter():
        barrier.wait()
        return CanonV3Service(service.project_root).prepare(chapter_batch)

    def run_axiom():
        barrier.wait()
        return CanonV3Service(service.project_root).prepare_author_axioms(
            axiom_proposal
        )

    successes = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_chapter), pool.submit(run_axiom)]
        for future in futures:
            try:
                future.result()
                successes += 1
            except (ActiveTransactionError, AuthorAxiomStageConflict):
                pass
    assert successes == 1
    assert len(authoritative_staging_kinds(service.project_root)) == 1


def test_author_axiom_workflow_primary_actions_never_route_to_chapter_write(
    tmp_path,
) -> None:
    from scripts.data_modules.workflow_authority import WorkflowAuthority

    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="primary-action",
        key="primary_rule",
        value="需要人工",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    awaiting = WorkflowAuthority(service.project_root).snapshot()
    assert awaiting["transaction_kind"] == "author_axiom"
    assert awaiting["primary_action"]["command"] == "/canon-ledger-confirm"
    _decide_all(service, action="rewrite")
    rewrite = WorkflowAuthority(service.project_root).snapshot()
    assert rewrite["primary_action"]["command"] == "/canon-ledger-plan"


def test_chapter_rewrite_preserves_author_axiom_manifest_entries(tmp_path) -> None:
    from scripts.data_modules.chapter_content_binding import build_chapter_binding
    from scripts.data_modules.tests.canon_v3_protocol_helpers import (
        finalize as finalize_chapter,
        proposal_authority,
    )

    service = _service(tmp_path)
    record = _draft_record(
        service.project_root,
        name="preserved",
        key="preserved_rule",
        value="不得丢失",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    _decide_all(service)
    _finalize(service)
    before = service.repository.current_manifest()["author_axiom_commits"]
    manuscript = service.project_root / "正文/第0001章.md"
    manuscript.parent.mkdir(parents=True, exist_ok=True)

    for text in ("第一版。", "第二版。"):
        manuscript.write_text(text, encoding="utf-8")
        binding = build_chapter_binding(service.project_root, 1)
        authority = proposal_authority(service, 1)
        service.prepare(
            {
                **authority,
                "chapter": 1,
                "chapter_binding": binding,
                "candidates": [],
                "observations": [],
                "scan_attestations": [
                    {
                        "attestation_id": "rewrite-scan",
                        "scanner": "reviewer",
                        "scanner_version": "test",
                        "chapter_sha256": binding["sha256"],
                        "parent_head": authority["parent_head"],
                        "author_axiom_digest": authority[
                            "author_axiom_digest"
                        ],
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
                        "checked_candidate_digests": [],
                    }
                ],
            }
        )
        finalize_chapter(service)
    after = service.repository.current_manifest()["author_axiom_commits"]
    assert after == before
    assert service.active_author_axioms()["records"][0]["axiom_key"] == (
        "preserved_rule"
    )


def test_forged_non_active_author_axiom_source_is_rejected(tmp_path) -> None:
    # Authority-key verification is covered at the chapter service boundary;
    # no live MASTER_SETTING fallback is permitted there.
    service = _service(tmp_path)
    from scripts.data_modules.canon_v3.source_verifier import (
        SourceVerificationError,
        verify_candidate_sources,
    )
    from scripts.data_modules.canon_v3.schema import (
        EntityObservedClaim,
        FactCandidate,
    )
    from scripts.data_modules.chapter_content_binding import build_chapter_binding

    manuscript = service.project_root / "正文/第0001章.md"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text("林舟登场。", encoding="utf-8")
    binding = build_chapter_binding(service.project_root, 1)
    fake_value = "林舟"
    candidate = FactCandidate(
        candidate_id="forged-axiom",
        claim=EntityObservedClaim(entity=fake_value),
        sources=(
            {
                "source_type": "author_axiom",
                "source_id": "forged",
                "document_path": ".canon-ledger/tmp/author_axioms/fake.json",
                "document_sha256": "a" * 64,
                "json_pointer": "/author_axioms/hero",
                "value": fake_value,
                "value_sha256": canonical_digest(fake_value),
            },
        ),
        support_map={"entity": ("forged",)},
    )
    with pytest.raises(
        SourceVerificationError, match="not_active_at_parent_head"
    ):
        verify_candidate_sources(
            service.project_root,
            binding,
            candidate,
            active_author_axiom_source_keys=frozenset(),
        )


def test_certified_genesis_axiom_requires_exact_override_to_update(tmp_path) -> None:
    root = tmp_path / "book"
    master = root / ".story-system/MASTER_SETTING.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        json.dumps(
            {
                "initial_canon": {
                    "world": {"cultivation_chain": "炼气→筑基"}
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    active_before = service.active_author_axioms()
    assert active_before["genesis_admissions"]
    admission = active_before["genesis_admissions"][0]
    assert admission["fact"]["value"] == "炼气→筑基"

    replacement = _draft_record(
        root,
        name="genesis-update",
        key="cultivation_chain",
        value="炼气→筑基→金丹",
    )
    staged = service.prepare_author_axioms(
        _proposal(
            service,
            [replacement],
            genesis_overrides=[
                {
                    "admission_digest": admission["admission_digest"],
                    "fact_content_sha256": admission[
                        "fact_content_sha256"
                    ],
                    "replacement_axiom_key": "cultivation_chain",
                }
            ],
        )
    )
    assert {item["operation"] for item in staged["cases"]} == {
        "add",
        "supersede_genesis",
    }
    for case in staged["cases"]:
        material = case["review_material"]
        assert material["schema_version"] == (
            "canon-v3/author-axiom-review-material/v2"
        )
        assert material["proposed_category"] == "world_rule"
        assert material["proposed_value"] == "炼气→筑基→金丹"
    _decide_all(service)
    _finalize(service)

    active_after = service.active_author_axioms()
    assert active_after["genesis_admissions"] == []
    assert active_after["records"][0]["source"]["value"] == (
        "炼气→筑基→金丹"
    )
    projection = read_projection(root, require_fresh=True)
    assert all(
        item.get("value") != "炼气→筑基"
        for item in projection["legacy_base"].get("hard_constraints") or []
    )
    pack = MemoryContractAdapter(DataModulesConfig(project_root=root)).load_context(1)
    rendered = json.dumps(pack.to_dict(), ensure_ascii=False)
    assert "炼气→筑基→金丹" in rendered
    assert '"value": "炼气→筑基"' not in rendered


@pytest.mark.parametrize(
    ("schema_version", "is_certified"),
    [
        ("canon-v3/legacy-genesis/v1", False),
        ("canon-v3/legacy-genesis/v2", True),
        ("canon-v3/legacy-genesis/v3", True),
    ],
)
def test_only_certified_genesis_schemas_expose_author_axiom_facts(
    tmp_path, schema_version: str, is_certified: bool
) -> None:
    facts = {
        "cutover_fact_admissions": [
            {
                "mode": "author_axiom_snapshot",
                "admission_digest": "a" * 64,
            }
        ]
    }

    class _GenesisRepository:
        def read_manifest(self, _head, *, validate_references):
            assert validate_references is True
            return {
                "generation": 0,
                "genesis_metadata": {
                    "schema_version": schema_version,
                    "legacy_snapshot": {"facts": facts},
                },
            }

    channel = AuthorAxiomChannel(
        tmp_path / "book",
        repository=_GenesisRepository(),  # type: ignore[arg-type]
    )
    assert bool(channel._genesis_facts("b" * 64)) is is_certified


def test_genesis_override_prepare_rechecks_downstream_dependencies(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "book"
    master = root / ".story-system/MASTER_SETTING.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        json.dumps(
            {"initial_canon": {"world": {"cultivation_chain": "炼气→筑基"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    admission = service.active_author_axioms()["genesis_admissions"][0]
    replacement = _draft_record(
        root,
        name="blocked-genesis-update",
        key="cultivation_chain",
        value="炼气→筑基→金丹",
    )

    def _reject(*_args, **_kwargs) -> None:
        raise LegacyMigrationError(
            "legacy_genesis_supersession_has_downstream_dependencies",
            admission["admission_digest"],
        )

    monkeypatch.setattr(
        "scripts.data_modules.canon_v3.migration."
        "require_legacy_genesis_supersession_safe",
        _reject,
    )
    with pytest.raises(
        AuthorAxiomEvidenceError,
        match="legacy_genesis_supersession_has_downstream_dependencies",
    ):
        service.prepare_author_axioms(
            _proposal(
                service,
                [replacement],
                genesis_overrides=[
                    {
                        "admission_digest": admission["admission_digest"],
                        "fact_content_sha256": admission[
                            "fact_content_sha256"
                        ],
                        "replacement_axiom_key": "cultivation_chain",
                    }
                ],
            )
        )


def test_genesis_override_finalize_rechecks_downstream_dependencies(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "book"
    master = root / ".story-system/MASTER_SETTING.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        json.dumps(
            {"initial_canon": {"world": {"cultivation_chain": "炼气→筑基"}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    admission = service.active_author_axioms()["genesis_admissions"][0]
    replacement = _draft_record(
        root,
        name="late-blocked-genesis-update",
        key="cultivation_chain",
        value="炼气→筑基→金丹",
    )
    service.prepare_author_axioms(
        _proposal(
            service,
            [replacement],
            genesis_overrides=[
                {
                    "admission_digest": admission["admission_digest"],
                    "fact_content_sha256": admission["fact_content_sha256"],
                    "replacement_axiom_key": "cultivation_chain",
                }
            ],
        )
    )
    _decide_all(service)
    before_head = service.workflow_snapshot()["head_hash"]
    before_stage = service.author_axiom_status()["stage_digest"]

    def _reject(*_args, **_kwargs) -> None:
        raise LegacyMigrationError(
            "legacy_genesis_supersession_has_downstream_dependencies",
            admission["admission_digest"],
        )

    monkeypatch.setattr(
        "scripts.data_modules.canon_v3.migration."
        "require_legacy_genesis_supersession_safe",
        _reject,
    )
    with pytest.raises(
        AuthorAxiomFinalizeBlocked,
        match="legacy_genesis_supersession_has_downstream_dependencies",
    ):
        _finalize(service)

    assert service.workflow_snapshot()["head_hash"] == before_head
    assert service.author_axiom_status()["stage_digest"] == before_stage


@pytest.mark.parametrize(
    ("add_action", "supersede_action"),
    [("omit", "approve"), ("approve", "omit")],
)
def test_genesis_replacement_decisions_must_publish_atomically(
    tmp_path, add_action: str, supersede_action: str
) -> None:
    root = tmp_path / "book"
    master = root / ".story-system/MASTER_SETTING.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        json.dumps(
            {
                "initial_canon": {
                    "world": {"cultivation_chain": "炼气→筑基"}
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    admission = service.active_author_axioms()["genesis_admissions"][0]
    replacement = _draft_record(
        root,
        name="atomic-genesis-update",
        key="cultivation_chain",
        value="炼气→筑基→金丹",
    )
    staged = service.prepare_author_axioms(
        _proposal(
            service,
            [replacement],
            genesis_overrides=[
                {
                    "admission_digest": admission["admission_digest"],
                    "fact_content_sha256": admission["fact_content_sha256"],
                    "replacement_axiom_key": "cultivation_chain",
                }
            ],
        )
    )
    by_operation = {case["operation"]: case for case in staged["cases"]}
    assert set(by_operation) == {"add", "supersede_genesis"}

    inconsistent = service.record_author_axiom_decisions(
        {
            "schema_version": "canon-v3/author-axiom-decision-request/v2",
            "expected_stage_digest": staged["stage_digest"],
            "transaction_hash": staged["transaction_hash"],
            "decisions": [
                {
                    "case_key": case["case_key"],
                    "target_digest": case["target_digest"],
                    "material_digest": case["review_material"][
                        "material_digest"
                    ],
                    "expected_decision_head_hash": case[
                        "decision_head_hash"
                    ],
                    "action": (
                        add_action
                        if operation == "add"
                        else supersede_action
                    ),
                }
                for operation, case in by_operation.items()
            ],
        }
    )
    assert inconsistent["state"] == "awaiting_human"
    assert inconsistent["can_finalize"] is False
    assert inconsistent["finalize_token"] is None
    assert inconsistent["recovery_action"] == (
        "resolve_genesis_replacement_decisions"
    )
    with pytest.raises(
        AuthorAxiomFinalizeBlocked,
        match="genesis_replacement_not_approved",
    ):
        service.finalize_author_axioms(
            {
                "schema_version": (
                    "canon-v3/author-axiom-finalize-request/v2"
                ),
                "expected_stage_digest": inconsistent["stage_digest"],
                "transaction_hash": inconsistent["transaction_hash"],
                "finalize_token": "0" * 64,
            }
        )
    active = service.active_author_axioms()
    assert active["genesis_admissions"][0]["admission_digest"] == (
        admission["admission_digest"]
    )
    assert active["records"] == []


def test_omitted_atomic_genesis_replacement_preserves_old_fact(tmp_path) -> None:
    root = tmp_path / "book"
    master = root / ".story-system/MASTER_SETTING.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        json.dumps(
            {
                "initial_canon": {
                    "world": {"cultivation_chain": "炼气→筑基"}
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    admission = service.active_author_axioms()["genesis_admissions"][0]
    replacement = _draft_record(
        root,
        name="realm-update",
        key="cultivation_chain",
        value="炼气→筑基→金丹",
    )

    staged = service.prepare_author_axioms(
        _proposal(
            service,
            [replacement],
            genesis_overrides=[
                {
                    "admission_digest": admission["admission_digest"],
                    "fact_content_sha256": admission["fact_content_sha256"],
                    "replacement_axiom_key": "cultivation_chain",
                }
            ],
        )
    )
    assert {case["operation"] for case in staged["cases"]} == {
        "add",
        "supersede_genesis",
    }
    _decide_all(service, action="omit")
    _finalize(service)

    active = service.active_author_axioms()
    assert [item["admission_digest"] for item in active["genesis_admissions"]] == [
        admission["admission_digest"]
    ]
    assert active["records"] == []


def test_chapter_uses_parent_head_axiom_after_draft_deleted_and_later_update(
    tmp_path,
) -> None:
    from scripts.data_modules.canon_v3.evidence import candidate_digest
    from scripts.data_modules.canon_v3.schema import (
        EntityObservedClaim,
        FactCandidate,
    )
    from scripts.data_modules.chapter_content_binding import build_chapter_binding
    from scripts.data_modules.tests.canon_v3_protocol_helpers import (
        finalize as finalize_chapter,
        proposal_authority,
        record_decisions as decide_chapter,
    )

    service = _service(tmp_path)
    first = _draft_record(
        service.project_root,
        name="hero-v1",
        key="hero_name",
        value="林舟",
        category="character_identity",
    )
    service.prepare_author_axioms(_proposal(service, [first]))
    _decide_all(service)
    _finalize(service)
    (service.project_root / first["source"]["document_path"]).unlink()
    candidate_source = service.active_author_axioms()["candidate_sources"][
        "hero_name"
    ]

    manuscript = service.project_root / "正文/第0001章.md"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text("他走进山门。", encoding="utf-8")
    binding = build_chapter_binding(service.project_root, 1)
    candidate = FactCandidate(
        candidate_id="hero-from-active-axiom",
        claim=EntityObservedClaim(entity="林舟"),
        sources=({**candidate_source, "source_id": "active-hero"},),
        support_map={"entity": ("active-hero",)},
    )
    authority = proposal_authority(service, 1)
    digest = candidate_digest(candidate)
    chapter_stage = service.prepare(
        {
            **authority,
            "chapter": 1,
            "chapter_binding": binding,
            "candidates": [candidate.model_dump(mode="json")],
            "observations": [],
            "scan_attestations": [
                {
                    "attestation_id": "full-scan",
                    "scanner": "reviewer",
                    "scanner_version": "test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority[
                        "author_axiom_digest"
                    ],
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
    decide_chapter(
        service,
        [
            {"case_key": case["case_key"], "action": "approve"}
            for case in chapter_stage["cases"]
        ],
        snapshot=chapter_stage,
    )
    finalize_chapter(service)

    active_record = service.active_author_axioms()["records"][0]
    updated = _draft_record(
        service.project_root,
        name="hero-v2",
        key="hero_name",
        value="林舟本名",
        category="character_identity",
    )
    service.prepare_author_axioms(_proposal(service, [updated]))
    _decide_all(service)
    _finalize(service)
    workflow = service.workflow_snapshot()
    assert workflow["state"] == "ready"
    assert workflow["latest_chapter"] == 1
    assert active_record["source"]["value"] == "林舟"

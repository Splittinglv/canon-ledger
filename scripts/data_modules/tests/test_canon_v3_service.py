from __future__ import annotations

import hashlib
import json

import pytest

from scripts.data_modules.tests.canon_v3_protocol_helpers import (
    authorize_reprepare,
    decision_request,
    finalize as finalize_v2,
    proposal_authority,
    record_decisions as record_decisions_v2,
)

from scripts.data_modules.canon_v3.evidence import candidate_digest
from scripts.data_modules.canon_v3.projection import read_projection
from scripts.data_modules.canon_v3.repository import CanonHeadConflict
from scripts.data_modules.canon_v3.repository import CanonChapterSequenceError
from scripts.data_modules.canon_v3.service import (
    ActiveTransactionError,
    CanonV3Service,
    FinalizeBlockedError,
    MigrationRequiredError,
    PreparedTransactionInvalid,
    ScanAttestationError,
    STAGING_SCHEMA_V1,
    StagingPointer,
)
from scripts.data_modules.canon_v3.review import InvalidDecision
from scripts.data_modules.canon_v3.schema import (
    CharacterStateChangedClaim,
    EntityObservedClaim,
    FactCandidate,
    ObservationKind,
    OpenLoopCreatedClaim,
    PowerBreakthroughClaim,
    PresenceObservedClaim,
    ReviewLevel,
    ReviewObservation,
    WorldRuleRevealedClaim,
    canonical_digest,
)
from scripts.data_modules.chapter_content_binding import (
    ChapterBindingError,
    build_chapter_binding,
)


SCAN_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _project(tmp_path, text: str):
    root = tmp_path / "book"
    manuscript = root / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text(text, encoding="utf-8")
    return root, manuscript, build_chapter_binding(root, 1)


def _span(manuscript, binding, source_id: str, quote: str) -> dict:
    raw = manuscript.read_bytes()
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    return {
        "source_type": "manuscript_span",
        "source_id": source_id,
        "document_sha256": binding["sha256"],
        "chapter": int(binding["chapter"]),
        "start": start,
        "end": start + len(quote_raw),
        "quote": quote,
        "quote_sha256": _sha(quote_raw),
    }


def _presence(manuscript, binding) -> FactCandidate:
    quote = "苏月仍在青云殿内。"
    return FactCandidate(
        candidate_id="presence-suyue",
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿", presence="在"
        ),
        sources=(_span(manuscript, binding, "span-presence", quote),),
        support_map={
            "subject": ("span-presence",),
            "location": ("span-presence",),
            "presence": ("span-presence",),
        },
    )


def _power(manuscript, binding) -> FactCandidate:
    quote = "林舟的境界从炼气突破到了筑基。"
    return FactCandidate(
        candidate_id="power-linzhou",
        claim=PowerBreakthroughClaim(
            subject="林舟", system="境界", before="炼气", after="筑基"
        ),
        sources=(_span(manuscript, binding, "span-power", quote),),
        support_map={
            "subject": ("span-power",),
            "system": ("span-power",),
            "before": ("span-power",),
            "after": ("span-power",),
        },
    )


def _batch(service, binding, candidates, observations=(), *, dimensions=SCAN_DIMENSIONS):
    digests = sorted(candidate_digest(candidate) for candidate in candidates)
    authority = proposal_authority(service, int(binding["chapter"]))
    return {
        **authority,
        "chapter": int(binding["chapter"]),
        "chapter_binding": binding,
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "observations": [item.model_dump(mode="json") for item in observations],
        "scan_attestations": [
            {
                "attestation_id": "complete-fact-scan",
                "scanner": "reviewer",
                "scanner_version": "v3-test",
                "chapter_sha256": binding["sha256"],
                "parent_head": authority["parent_head"],
                "author_axiom_digest": authority["author_axiom_digest"],
                "entity_registry_digest": authority["entity_registry_digest"],
                "dimensions": list(dimensions),
                "status": "complete",
                "checked_candidate_digests": digests,
            }
        ],
    }


def _case_key(snapshot: dict) -> str:
    assert snapshot["cases"]
    return str(snapshot["cases"][0]["case_key"])


def _approve_all_required(service: CanonV3Service, snapshot: dict) -> dict:
    decisions = [
        {"case_key": case["case_key"], "action": "approve"}
        for case in snapshot["cases"]
        if case.get("level") == "human_required"
        and not case.get("requires_rewrite")
    ]
    assert decisions
    return record_decisions_v2(service, decisions, snapshot=snapshot)


def test_machine_supported_non_checkpoint_fact_publishes_only_at_finalize(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "开场。\n谁偷走了灵钥，仍是谜。\n"
    )
    candidate = FactCandidate(
        candidate_id="loop-stolen-key",
        claim=OpenLoopCreatedClaim(loop="谁偷走了灵钥"),
        sources=(
            _span(
                manuscript,
                binding,
                "span-open-loop",
                "谁偷走了灵钥，仍是谜。",
            ),
        ),
        support_map={"loop": ("span-open-loop",)},
    )
    service = CanonV3Service(root)

    prepared = service.prepare(_batch(service, binding, [candidate]))

    assert prepared["state"] == "ready_to_finalize"
    assert prepared["can_finalize"] is True
    assert prepared["can_write_next"] is False
    assert service.repository.current_manifest()["chapters"] == []

    result = finalize_v2(service)
    workflow = service.workflow_snapshot()
    projection = read_projection(root)

    assert result["created"] is True
    assert workflow["state"] == "ready"
    assert workflow["can_write_next"] is True
    assert projection["binding"]["head_hash"] == result["head_hash"]
    assert projection["facts"][0]["claim"]["kind"] == "open_loop_created"
    from scripts.data_modules.canonical_history import load_canonical_history

    history = load_canonical_history(root, as_of_chapter=1)
    assert history.valid_chapters == [1]
    assert history.obligations[0]["payload"]["loop"] == "谁偷走了灵钥"
    assert history.canonical_facts[0]["candidate_digest"] == candidate_digest(
        candidate
    )
    from scripts.data_modules.story_runtime_sources import load_runtime_sources

    runtime = load_runtime_sources(root, chapter=2, history_as_of_chapter=1)
    assert runtime.primary_write_source == "canon_v3_head"
    assert runtime.latest_accepted_commit["meta"]["chapter"] == 1
    assert runtime.latest_accepted_commit["meta"]["schema_version"] == "story-system/v3"
    before_first = load_runtime_sources(root, chapter=1, history_as_of_chapter=0)
    assert before_first.latest_accepted_commit is None


def test_empty_fact_chapter_still_advances_canonical_chapter_ledger(tmp_path) -> None:
    root, _manuscript, binding = _project(tmp_path, "只有气氛描写。\n")
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, []))
    assert prepared["state"] == "ready_to_finalize"
    finalize_v2(service)

    projection = read_projection(root)
    assert projection["facts"] == []
    assert projection["history"] == []
    assert projection["chapters"] == [
        {
            "chapter": 1,
            "revision": 1,
            "commit_hash": projection["chapters"][0]["commit_hash"],
            "transaction_hash": projection["chapters"][0]["transaction_hash"],
        }
    ]
    from scripts.data_modules.canonical_history import (
        latest_canonical_chapter,
        load_canonical_history,
    )

    history = load_canonical_history(root, as_of_chapter=1)
    assert history.valid_chapters == [1]
    assert latest_canonical_chapter(root) == 1
    assert service.workflow_snapshot()["expected_next_chapter"] == 2


def test_same_slot_effects_fold_in_manuscript_order_not_hash_order(tmp_path) -> None:
    text = "苏月先在青云殿，随后在山门。\n"
    root, manuscript, binding = _project(tmp_path, text)
    subject_quote = "苏月"
    first_quote = "先在青云殿"
    second_quote = "随后在山门"
    first = FactCandidate(
        candidate_id="presence-first",
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿", presence="在"
        ),
        sources=(
            _span(manuscript, binding, "span-subject-first", subject_quote),
            _span(manuscript, binding, "span-first", first_quote),
        ),
        support_map={
            "subject": ("span-subject-first",),
            "location": ("span-first",),
            "presence": ("span-first",),
        },
    )
    second = FactCandidate(
        candidate_id="presence-second",
        claim=PresenceObservedClaim(
            subject="苏月", location="山门", presence="在"
        ),
        sources=(
            _span(manuscript, binding, "span-subject-second", subject_quote),
            _span(manuscript, binding, "span-second", second_quote),
        ),
        support_map={
            "subject": ("span-subject-second",),
            "location": ("span-second",),
            "presence": ("span-second",),
        },
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [second, first]))
    assert _approve_all_required(service, prepared)["state"] == "ready_to_finalize"
    finalize_v2(service)

    projection = read_projection(root)
    assert [row["claim"]["location"] for row in projection["history"]] == [
        "青云殿",
        "山门",
    ]
    assert projection["facts"][0]["claim"]["location"] == "山门"


def test_live_legacy_setting_facts_never_bypass_v3_head(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "林舟登场。\n")
    candidate = FactCandidate(
        candidate_id="entity-linzhou",
        claim=EntityObservedClaim(entity="林舟"),
        sources=(_span(manuscript, binding, "span-linzhou", "林舟登场。"),),
        support_map={"entity": ("span-linzhou",)},
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [candidate]))
    record_decisions_v2(service,
        {"decisions": [{"case_key": _case_key(prepared), "action": "approve"}]}
    )
    finalize_v2(service)

    master = root / ".story-system" / "MASTER_SETTING.json"
    master.write_text(
        json.dumps(
            {
                "meta": {"contract_type": "MASTER_SETTING"},
                "initial_canon": {"protagonist": {"name": "魔尊"}},
                "setting_canon": {"facts": [{"value": "林舟不存在"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from scripts.data_modules.canonical_history import load_canonical_history
    from scripts.data_modules.story_runtime_sources import load_runtime_sources

    history = load_canonical_history(root, as_of_chapter=1)
    runtime = load_runtime_sources(root, chapter=2, history_as_of_chapter=1)
    assert service.workflow_snapshot()["state"] == "ready"
    assert history.initial_canon == {}
    assert history.setting_canon == {}
    assert history.entities["林舟"]["name"] == "林舟"
    assert "initial_canon" not in runtime.contracts["master"]
    assert "setting_canon" not in runtime.contracts["master"]


def test_checkpoint_cannot_publish_before_exact_approval(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))

    assert prepared["state"] == "awaiting_human"
    assert prepared["counts"]["required"] == 1
    with pytest.raises(FinalizeBlockedError, match="awaiting_human"):
        finalize_v2(service)

    approved = record_decisions_v2(service,
        {"decisions": [{"case_key": _case_key(prepared), "action": "approve"}]}
    )
    assert approved["state"] == "ready_to_finalize"
    assert approved["can_write_next"] is False

    result = finalize_v2(service)
    assert result["decision_hashes"]
    assert read_projection(root)["facts"][0]["claim"]["after"] == "筑基"


def test_power_and_character_state_share_one_current_value_slot(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    first = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    _approve_all_required(service, first)
    finalize_v2(service)

    manuscript = root / "正文" / "第0002章.md"
    quote = "林舟的境界从筑基变为金丹。"
    manuscript.write_text(quote + "\n", encoding="utf-8")
    binding = build_chapter_binding(root, 2)
    changed = FactCandidate(
        candidate_id="state-realm-ch2",
        claim=CharacterStateChangedClaim(
            subject="林舟",
            attribute="境界",
            before="筑基",
            after="金丹",
        ),
        sources=(_span(manuscript, binding, "span-realm-ch2", quote),),
        support_map={
            "subject": ("span-realm-ch2",),
            "attribute": ("span-realm-ch2",),
            "before": ("span-realm-ch2",),
            "after": ("span-realm-ch2",),
        },
    )
    second = service.prepare(_batch(service, binding, [changed]))
    _approve_all_required(service, second)
    finalize_v2(service)

    projection = read_projection(root)
    assert len(projection["facts"]) == 1
    assert projection["facts"][0]["claim"]["kind"] == "character_state_changed"
    assert projection["facts"][0]["claim"]["after"] == "金丹"
    from scripts.data_modules.canonical_history import load_canonical_history

    history = load_canonical_history(root, as_of_chapter=2)
    realm_facts = [
        row
        for row in history.canonical_facts
        if (row.get("payload") or {}).get("after") in {"筑基", "金丹"}
    ]
    assert [row["payload"]["after"] for row in realm_facts] == ["金丹"]
    assert history.entities["林舟"]["attributes"]["境界"] == "金丹"


def test_new_world_rule_without_slot_gets_deterministic_reviewed_identity(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "月门只能在夜间开启。\n")
    candidate = FactCandidate(
        candidate_id="rule-moon-gate",
        claim=WorldRuleRevealedClaim(rule="月门只能在夜间开启"),
        sources=(_span(manuscript, binding, "span-rule", "月门只能在夜间开启。"),),
        support_map={"rule": ("span-rule",)},
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [candidate]))
    material = prepared["cases"][0]["review_material"]
    assert material["candidate"]["claim"]["slot_id"] is None
    compiled_slot = material["compiled_effects"][0]["claim"]["slot_id"]
    assert compiled_slot == canonical_digest(
        {"kind_family": "world_rule", "rule": "月门只能在夜间开启"}
    )


def test_unknown_caller_supplied_rule_slot_is_rejected(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "月门只能在夜间开启。\n")
    service = CanonV3Service(root)
    candidate = FactCandidate(
        candidate_id="rule-forged-slot",
        claim=WorldRuleRevealedClaim(
            slot_id="f" * 64,
            rule="月门只能在夜间开启",
        ),
        sources=(_span(manuscript, binding, "span-rule", "月门只能在夜间开启。"),),
        support_map={"rule": ("span-rule",)},
    )

    with pytest.raises(PreparedTransactionInvalid, match="rule_slot_not_in_parent_head"):
        service.prepare(_batch(service, binding, [candidate]))


def test_legacy_rule_slot_update_replaces_old_hard_rule_and_binds_prior(tmp_path) -> None:
    from scripts.data_modules.canon_v3.projection import rebuild_projection
    from scripts.data_modules.canon_v3.repository import CanonV3Repository, content_hash

    root, manuscript, binding = _project(tmp_path, "异火数量改为二十四种。\n")
    legacy_rule = {
        "id": "legacy-rule-flame-count",
        "category": "world_rule",
        "subject": "力量体系",
        "field": "异火数量",
        "value": "二十三种",
        "status": "active",
        "source_chapter": 0,
    }
    legacy_facts = {
        "canonical_facts": [],
        "hard_constraints": [dict(legacy_rule)],
        "rules": [dict(legacy_rule)],
        "entities": {},
    }
    snapshot = {
        "schema_version": "canon-v3/legacy-fact-snapshot/v1",
        "source_schema_version": "canon-ledger-asof-snapshot/v3",
        "cutover_chapter": 0,
        "facts": legacy_facts,
    }
    repository = CanonV3Repository(root)
    repository._initialize_objects(
        genesis_metadata={
            "schema_version": "canon-v3/legacy-genesis/v1",
            "source": "new_project",
            "cutover_chapter": 0,
            "v2_commits": [],
            "legacy_snapshot": snapshot,
            "legacy_snapshot_sha256": content_hash(snapshot),
        }
    )
    projection = rebuild_projection(root)
    prior = projection["legacy_base"]["rules"][0]
    assert len(prior["slot_id"]) == 64
    assert len(prior["fact_digest"]) == 64

    candidate = FactCandidate(
        candidate_id="rule-flame-count-update",
        claim=WorldRuleRevealedClaim(
            slot_id=prior["slot_id"],
            rule="异火数量改为二十四种",
        ),
        sources=(
            _span(
                manuscript,
                binding,
                "span-rule-update",
                "异火数量改为二十四种。",
            ),
        ),
        support_map={"rule": ("span-rule-update",)},
    )
    service = CanonV3Service(root)
    with pytest.raises(
        MigrationRequiredError,
        match="legacy_genesis_v1_recertification_required",
    ):
        service.prepare(_batch(service, binding, [candidate]))


def test_confirmed_conflict_only_allows_rewrite_and_never_publishes(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    first = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    record_decisions_v2(service,
        {"decisions": [{"case_key": _case_key(first), "action": "approve"}]}
    )
    finalize_v2(service)
    prior_digest = read_projection(root)["facts"][0]["fact_digest"]

    manuscript = root / "正文" / "第0002章.md"
    manuscript.write_text("林舟的境界从筑基突破到了金丹。\n", encoding="utf-8")
    binding = build_chapter_binding(root, 2)
    quote = "林舟的境界从筑基突破到了金丹。"
    candidate = FactCandidate(
        candidate_id="power-linzhou-ch2",
        claim=PowerBreakthroughClaim(
            subject="林舟", system="境界", before="筑基", after="金丹"
        ),
        sources=(_span(manuscript, binding, "span-power-ch2", quote),),
        support_map={
            "subject": ("span-power-ch2",),
            "system": ("span-power-ch2",),
            "before": ("span-power-ch2",),
            "after": ("span-power-ch2",),
        },
    )
    conflict = ReviewObservation(
        observation_id="conflict-existing-realm",
        candidate_id=candidate.candidate_id,
        kind=ObservationKind.CONFIRMED_CONFLICT,
        level=ReviewLevel.AUDIT_ONLY,
        reason="前章已明确其仍为凡人且本章没有突破条件",
        prior_fact_digests=(prior_digest,),
    )
    prepared = service.prepare(_batch(service, binding, [candidate], [conflict]))
    case_key = _case_key(prepared)

    assert prepared["state"] == "rewrite_required"
    with pytest.raises(Exception, match="not_allowed"):
        record_decisions_v2(service,
            {"decisions": [{"case_key": case_key, "action": "approve"}]}
        )
    rewritten = record_decisions_v2(service,
        {"decisions": [{"case_key": case_key, "action": "rewrite"}]}
    )
    assert rewritten["state"] == "rewrite_required"
    with pytest.raises(FinalizeBlockedError, match="rewrite_required"):
        finalize_v2(service)
    assert [
        row[1]["chapter"] for row in service.repository.current_commits()
    ] == [1]


def test_prior_fact_reference_must_exist_in_exact_parent_head(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    candidate = _power(manuscript, binding)
    service = CanonV3Service(root)
    conflict = ReviewObservation(
        observation_id="forged-prior",
        candidate_id=candidate.candidate_id,
        kind=ObservationKind.CONFIRMED_CONFLICT,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="伪造的前史引用",
        prior_fact_digests=("a" * 64,),
    )

    with pytest.raises(PreparedTransactionInvalid, match="prior_fact_not_in_parent_head"):
        service.prepare(_batch(service, binding, [candidate], [conflict]))


def test_review_status_embeds_immutable_candidate_instead_of_mutable_tmp(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    material = prepared["cases"][0]["review_material"]
    unsigned = {key: value for key, value in material.items() if key != "material_digest"}
    assert material["candidate"]["claim"]["after"] == "筑基"
    assert material["material_digest"] == canonical_digest(unsigned)

    mutable_tmp = root / ".canon-ledger" / "tmp" / "canon_v3_proposal.json"
    mutable_tmp.parent.mkdir(parents=True)
    mutable_tmp.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "power-linzhou",
                        "claim": {"kind": "power_breakthrough", "after": "金丹"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    shown = service.workflow_snapshot()["cases"][0]["review_material"]
    assert shown == material
    assert shown["candidate"]["claim"]["after"] == "筑基"


def test_redecision_recomputes_from_base_without_residual_fact(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "苏月仍在青云殿内。\n"
    )
    candidate = _presence(manuscript, binding)
    ambiguity = ReviewObservation(
        observation_id="ambiguous-presence",
        candidate_id=candidate.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="可能是回忆片段，需要作者确认",
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [candidate], [ambiguity]))
    key = _case_key(prepared)

    first = record_decisions_v2(service,
        {"decisions": [{"case_key": key, "action": "approve"}]}
    )
    assert first["state"] == "ready_to_finalize"
    second = record_decisions_v2(service,
        {"decisions": [{"case_key": key, "action": "omit"}]}
    )
    assert second["state"] == "ready_to_finalize"

    result = finalize_v2(service)
    assert len(result["decision_hashes"]) == 2
    assert read_projection(root)["facts"] == []
    assert read_projection(root)["history"] == []


def test_decision_cannot_apply_after_chapter_bytes_change(tmp_path) -> None:
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    manuscript.write_text(
        "林舟的境界从炼气突破到了金丹。\n", encoding="utf-8"
    )

    with pytest.raises(ChapterBindingError, match="hash_mismatch"):
        record_decisions_v2(service,
            {
                "decisions": [
                    {"case_key": _case_key(prepared), "action": "approve"}
                ]
            },
            snapshot=prepared,
        )
    snapshot = service.workflow_snapshot()
    assert snapshot["state"] == "recompile_required"
    assert snapshot["can_write_next"] is False


def test_editing_an_active_v3_chapter_blocks_next_write_until_replaced(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "苏月仍在青云殿内。\n")
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [_presence(manuscript, binding)]))
    _approve_all_required(service, prepared)
    first = finalize_v2(service)

    manuscript.write_text("苏月仍在青云殿偏殿内。\n", encoding="utf-8")
    stale = service.workflow_snapshot()
    assert stale["state"] == "recompile_required"
    assert stale["chapter"] == 1
    assert stale["can_write_next"] is False

    updated_binding = build_chapter_binding(root, 1)
    updated = FactCandidate(
        candidate_id="presence-suyue",
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿偏殿", presence="在"
        ),
        sources=(
            _span(
                manuscript,
                updated_binding,
                "span-presence-updated",
                "苏月仍在青云殿偏殿内。",
            ),
        ),
        support_map={
            "subject": ("span-presence-updated",),
            "location": ("span-presence-updated",),
            "presence": ("span-presence-updated",),
        },
    )
    prepared = service.prepare(_batch(service, updated_binding, [updated]))
    assert prepared["state"] == "awaiting_human"
    assert _approve_all_required(service, prepared)["state"] == "ready_to_finalize"
    replacement = finalize_v2(service)

    assert replacement["revision"] == 2
    assert replacement["head_hash"] != first["head_hash"]
    assert service.workflow_snapshot()["can_write_next"] is True
    assert read_projection(root)["facts"][0]["claim"]["location"] == "青云殿偏殿"


def test_unpublished_live_master_setting_cannot_be_author_axiom_source(tmp_path) -> None:
    import json

    root, _manuscript, binding = _project(tmp_path, "林舟登场。\n")
    master = root / ".story-system" / "MASTER_SETTING.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    document = {"characters": {"hero": {"name": "林舟", "alias": "阿舟"}}}
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    master.write_bytes(raw)
    candidate = FactCandidate(
        candidate_id="entity-linzhou",
        claim=EntityObservedClaim(entity="林舟", aliases=("阿舟",)),
        sources=(
            {
                "source_type": "author_axiom",
                "source_id": "axiom-name",
                "document_path": ".story-system/MASTER_SETTING.json",
                "document_sha256": _sha(raw),
                "json_pointer": "/characters/hero/name",
                "value": "林舟",
                "value_sha256": canonical_digest("林舟"),
            },
            {
                "source_type": "author_axiom",
                "source_id": "axiom-alias",
                "document_path": ".story-system/MASTER_SETTING.json",
                "document_sha256": _sha(raw),
                "json_pointer": "/characters/hero/alias",
                "value": "阿舟",
                "value_sha256": canonical_digest("阿舟"),
            },
        ),
        support_map={"entity": ("axiom-name",), "aliases": ("axiom-alias",)},
    )
    service = CanonV3Service(root)
    from scripts.data_modules.canon_v3.source_verifier import (
        SourceVerificationError,
    )

    with pytest.raises(
        SourceVerificationError, match="not_active_at_parent_head"
    ):
        service.prepare(_batch(service, binding, [candidate]))
    assert service.active_author_axioms()["records"] == []


def test_correction_must_be_recompiled_and_cannot_silently_revert(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "苏月仍在青云殿内。\n")
    original = _presence(manuscript, binding)
    ambiguity = ReviewObservation(
        observation_id="presence-wording",
        candidate_id=original.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="地点粒度需要作者确认",
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [original], [ambiguity]))
    corrected = FactCandidate(
        candidate_id=original.candidate_id,
        claim=PresenceObservedClaim(
            subject="苏月", location="青云殿内", presence="在"
        ),
        sources=original.sources,
        support_map=original.support_map,
    )
    corrected_snapshot = record_decisions_v2(service,
        {
            "decisions": [
                {
                    "case_key": _case_key(prepared),
                    "action": "correct",
                    "corrected_candidate": corrected.model_dump(mode="json"),
                }
            ]
        }
    )
    assert corrected_snapshot["state"] == "recompile_required"

    with pytest.raises(PreparedTransactionInvalid, match="corrected_candidates_missing"):
        service.prepare(_batch(service, binding, [original], [ambiguity]))

    reparsed = service.prepare(_batch(service, binding, [corrected], [ambiguity]))
    assert reparsed["state"] == "awaiting_human"
    record_decisions_v2(service,
        {"decisions": [{"case_key": _case_key(reparsed), "action": "approve"}]}
    )
    finalize_v2(service)
    assert read_projection(root)["facts"][0]["claim"]["location"] == "青云殿内"


def test_incomplete_scan_never_creates_staging_or_canon(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "苏月仍在青云殿内。\n")
    service = CanonV3Service(root)

    with pytest.raises(ScanAttestationError, match="single_complete_scan"):
        service.prepare(
            _batch(service, binding, [_presence(manuscript, binding)], dimensions=["timeline"])
        )

    assert not service.staging_path.exists()
    assert service.repository.current_head(validate=False) is not None
    assert service.repository.current_manifest()["chapters"] == []


def test_scan_coverage_cannot_be_spliced_across_incomplete_attestations(tmp_path) -> None:
    root, manuscript, binding = _project(tmp_path, "苏月仍在青云殿内。\n")
    service = CanonV3Service(root)
    candidate = _presence(manuscript, binding)
    proposal = _batch(service, binding, [candidate])
    digest = candidate_digest(candidate)
    proposal["scan_attestations"] = [
        {
            "attestation_id": "dimensions-only",
            "scanner": "reviewer-a",
            "scanner_version": "v3-test",
            "chapter_sha256": binding["sha256"],
            "parent_head": proposal["parent_head"],
            "author_axiom_digest": proposal["author_axiom_digest"],
            "entity_registry_digest": proposal["entity_registry_digest"],
            "dimensions": SCAN_DIMENSIONS,
            "status": "complete",
            "checked_candidate_digests": [],
        },
        {
            "attestation_id": "candidate-only",
            "scanner": "reviewer-b",
            "scanner_version": "v3-test",
            "chapter_sha256": binding["sha256"],
            "parent_head": proposal["parent_head"],
            "author_axiom_digest": proposal["author_axiom_digest"],
            "entity_registry_digest": proposal["entity_registry_digest"],
            "dimensions": ["setting"],
            "status": "complete",
            "checked_candidate_digests": [digest],
        },
    ]

    with pytest.raises(ScanAttestationError, match="single_complete_scan"):
        CanonV3Service(root).prepare(proposal)


def test_new_project_cannot_skip_the_first_canonical_chapter(tmp_path) -> None:
    root = tmp_path / "book"
    service = CanonV3Service(root)
    manuscript = root / "正文" / "第0002章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("苏月仍在青云殿内。\n", encoding="utf-8")
    binding = build_chapter_binding(root, 2)
    quote = "苏月仍在青云殿内。"
    raw = manuscript.read_bytes()
    quote_raw = quote.encode("utf-8")
    candidate = FactCandidate.model_validate(
        {
            "candidate_id": "presence-suyue-ch2",
            "claim": {
                "kind": "presence_observed",
                "subject": "苏月",
                "location": "青云殿",
                "presence": "在",
            },
            "sources": [
                {
                    "source_type": "manuscript_span",
                    "source_id": "span-ch2",
                    "document_sha256": binding["sha256"],
                    "chapter": 2,
                    "start": raw.index(quote_raw),
                    "end": raw.index(quote_raw) + len(quote_raw),
                    "quote": quote,
                    "quote_sha256": _sha(quote_raw),
                }
            ],
            "support_map": {
                "subject": ["span-ch2"],
                "location": ["span-ch2"],
                "presence": ["span-ch2"],
            },
        }
    )
    proposal = _batch(service, binding, [candidate])
    proposal["chapter"] = 2

    with pytest.raises(CanonChapterSequenceError, match="cutover_plus_one"):
        service.prepare(proposal)


def test_all_write_gates_consume_the_same_v3_workflow_snapshot(tmp_path) -> None:
    from scripts.data_modules.write_gates import run_write_gate

    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    assert run_write_gate(root, chapter=1, stage="prewrite")["ok"] is True

    prepared = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    blocked_prewrite = run_write_gate(root, chapter=2, stage="prewrite")
    blocked_precommit = run_write_gate(root, chapter=1, stage="precommit")
    assert blocked_prewrite["ok"] is False
    assert blocked_precommit["ok"] is False
    assert (
        blocked_prewrite["details"]["workflow_snapshot"]["transaction_hash"]
        == blocked_precommit["details"]["workflow_snapshot"]["transaction_hash"]
    )

    record_decisions_v2(service,
        {"decisions": [{"case_key": _case_key(prepared), "action": "approve"}]}
    )
    assert run_write_gate(root, chapter=1, stage="precommit")["ok"] is True
    finalize_v2(service)
    assert run_write_gate(root, chapter=1, stage="postcommit")["ok"] is True
    assert run_write_gate(root, chapter=2, stage="prewrite")["ok"] is True
    from scripts.data_modules.project_status import build_project_status

    project_status = build_project_status(root, chapter=2)
    assert project_status["phase"] == "canon_v3:ready"
    assert project_status["blocking"] == []
    from scripts.data_modules.workflow_authority import WorkflowAuthority

    authority = WorkflowAuthority(root).snapshot()
    assert project_status["evidence"]["workflow_snapshot"] == authority
    from scripts.data_modules.user_report import build_user_report

    report = build_user_report(root, stage="write", chapter=1)
    assert report["overall_status"] == "completed"
    assert report["workflow_snapshot"] == authority


def test_context_pack_consumes_same_v3_workflow_and_target_chapter_gate(tmp_path) -> None:
    from scripts.data_modules.config import DataModulesConfig
    from scripts.data_modules.memory_contract_adapter import MemoryContractAdapter

    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    service.initialize_new_project()
    adapter = MemoryContractAdapter(DataModulesConfig(project_root=root))

    wrong_target = adapter.load_context(2, budget_tokens=20_000)
    assert wrong_target.completeness["status"] == "blocked"
    assert any(
        "canon_v3_workflow_blocked" in item
        for item in wrong_target.completeness["missing_sources"]
    )

    service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    pending = adapter.load_context(1, budget_tokens=20_000)
    workflow = pending.sections["runtime_status"]["workflow_snapshot"]
    assert workflow["state"] == "awaiting_human"
    assert workflow["can_write_next"] is False
    assert pending.completeness["status"] == "blocked"
    assert pending.completeness["source_status"]["canon_v3_workflow"][
        "status"
    ] == "error"


def test_v2_commit_and_review_writers_are_read_only_after_cutover(tmp_path) -> None:
    root, _manuscript, binding = _project(tmp_path, "正文。\n")
    CanonV3Service(root).initialize_new_project()
    from scripts.data_modules.chapter_commit_service import ChapterCommitService
    from scripts.data_modules.human_review import HumanReviewService

    with pytest.raises(ValueError, match="v2_write_disabled"):
        ChapterCommitService(root).persist_commit({})
    with pytest.raises(ValueError, match="v2_human_review_disabled"):
        HumanReviewService(root).persist_queue(1, binding, [])
    from scripts.data_modules.projection_rebuild import (
        ProjectionRebuildError,
        rebuild_all_projections,
    )
    from scripts.data_modules.projections import retry_projection

    retry = retry_projection(root, chapter=1)
    assert retry["ok"] is False
    assert retry["error"] == "canon_v3_active_v2_projection_retry_disabled"
    with pytest.raises(ProjectionRebuildError, match="v2_projection_rebuild_disabled"):
        rebuild_all_projections(root)


def test_v2_stage_and_case_material_are_explicit_authorization_inputs(tmp_path):
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    snapshot = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))

    assert len(snapshot["stage_digest"]) == 64
    case = snapshot["cases"][0]
    assert case["stage_digest"] == snapshot["stage_digest"]
    assert len(case["target_digest"]) == 64
    assert len(case["review_material"]["material_digest"]) == 64
    assert case["decision_head_hash"] is None

    with pytest.raises(InvalidDecision, match="request_v2_invalid"):
        service.record_decisions(
            {"decisions": [{"case_key": case["case_key"], "action": "approve"}]}
        )


def test_decision_request_is_cas_bound_and_batch_validation_is_atomic(tmp_path):
    root, manuscript, binding = _project(
        tmp_path,
        "林舟的境界从炼气突破到了筑基。\n苏月仍在青云殿内。\n",
    )
    service = CanonV3Service(root)
    snapshot = service.prepare(
        _batch(service, binding, [_power(manuscript, binding), _presence(manuscript, binding)])
    )
    actions = [
        {"case_key": case["case_key"], "action": "approve"}
        for case in snapshot["cases"]
    ]
    request = decision_request(snapshot, actions)
    request["decisions"][1]["material_digest"] = "0" * 64

    before = service._read_staging_unlocked()
    assert before is not None and before.decision_hashes == ()
    with pytest.raises(InvalidDecision, match="material_precondition"):
        service.record_decisions(request)
    after = service._read_staging_unlocked()
    assert after is not None and after.decision_hashes == ()

    valid = decision_request(snapshot, actions)
    decided = service.record_decisions(valid)
    assert decided["state"] == "ready_to_finalize"
    with pytest.raises(InvalidDecision, match="stage_precondition"):
        service.record_decisions(valid)


def test_prepare_replacement_requires_exact_observed_stage(tmp_path):
    root, manuscript, binding = _project(
        tmp_path, "苏月仍在青云殿内。林舟也在青云殿内。\n"
    )
    service = CanonV3Service(root)
    first = service.prepare(_batch(service, binding, [_presence(manuscript, binding)]))
    replacement = FactCandidate(
        candidate_id="presence-linzhou",
        claim=PresenceObservedClaim(
            subject="林舟", location="青云殿", presence="在"
        ),
        sources=(
            _span(
                manuscript, binding, "span-presence-linzhou", "林舟也在青云殿内。"
            ),
        ),
        support_map={
            "subject": ("span-presence-linzhou",),
            "location": ("span-presence-linzhou",),
            "presence": ("span-presence-linzhou",),
        },
    )
    proposal = _batch(service, binding, [replacement])
    stale_proposal = {**proposal, "expected_stage_digest": None}

    with pytest.raises(ActiveTransactionError, match="expected_stage_mismatch"):
        service.prepare(stale_proposal)
    assert service.workflow_snapshot()["transaction_hash"] == first[
        "transaction_hash"
    ]

    replaced = service.prepare(proposal)
    assert replaced["transaction_hash"] != first["transaction_hash"]


def test_omit_semantic_claim_with_repackaged_evidence_requires_human(tmp_path):
    root, manuscript, binding = _project(
        tmp_path, "也许，谁偷走了灵钥仍是谜。\n"
    )
    original = FactCandidate(
        candidate_id="loop-key",
        claim=OpenLoopCreatedClaim(loop="谁偷走了灵钥"),
        sources=(
            _span(manuscript, binding, "short-span", "谁偷走了灵钥仍是谜。"),
        ),
        support_map={"loop": ("short-span",)},
    )
    ambiguity = ReviewObservation(
        observation_id="possible-memory",
        candidate_id=original.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="可能是回忆",
    )
    service = CanonV3Service(root)
    staged = service.prepare(_batch(service, binding, [original], [ambiguity]))
    record_decisions_v2(
        service,
        [{"case_key": _case_key(staged), "action": "omit"}],
        snapshot=staged,
    )
    finalize_v2(service)

    repackaged = FactCandidate(
        candidate_id="renamed-runtime-id",
        claim=original.claim,
        sources=(
            _span(manuscript, binding, "larger-span", "也许，谁偷走了灵钥仍是谜。"),
        ),
        support_map={"loop": ("larger-span",)},
    )
    reconsideration = service.prepare(_batch(service, binding, [repackaged]))
    assert reconsideration["state"] == "awaiting_human"
    assert any(
        reason == "observation:checkpoint"
        for case in reconsideration["cases"]
        for reason in case["reasons"]
    )


def test_finalize_requires_exact_token_and_exact_retry_is_idempotent(tmp_path):
    root, manuscript, binding = _project(tmp_path, "谁偷走了灵钥，仍是谜。\n")
    candidate = FactCandidate(
        candidate_id="loop-key",
        claim=OpenLoopCreatedClaim(loop="谁偷走了灵钥"),
        sources=(
            _span(manuscript, binding, "loop-span", "谁偷走了灵钥，仍是谜。"),
        ),
        support_map={"loop": ("loop-span",)},
    )
    service = CanonV3Service(root)
    ready = service.prepare(_batch(service, binding, [candidate]))
    assert ready["finalize_token"]

    with pytest.raises(FinalizeBlockedError, match="request_v2_required"):
        service.finalize()
    bad = {
        "schema_version": "canon-v3/finalize-request/v2",
        "expected_stage_digest": ready["stage_digest"],
        "transaction_hash": ready["transaction_hash"],
        "finalize_token": "0" * 64,
    }
    with pytest.raises(FinalizeBlockedError, match="token_precondition"):
        service.finalize(bad)

    request = {**bad, "finalize_token": ready["finalize_token"]}
    created = service.finalize(request)
    retried = service.finalize(request)
    assert created["created"] is True
    assert retried["created"] is False
    assert retried["transaction_hash"] == created["transaction_hash"]


def test_unpublished_v1_positive_stage_is_read_only_and_cannot_finalize(tmp_path):
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    service = CanonV3Service(root)
    staged = service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    decided = record_decisions_v2(
        service,
        [{"case_key": _case_key(staged), "action": "approve"}],
        snapshot=staged,
    )
    pointer = service._read_staging_unlocked()
    assert pointer is not None
    v2_wrapper = service.repository.read_decision(pointer.decision_hashes[0])
    v1_hash = service.repository.put_decision(
        {
            "schema_version": "canon-v3/decision-envelope/v1",
            "transaction_hash": pointer.transaction_hash,
            "chapter": 1,
            "decision": v2_wrapper["decision"],
        }
    )
    with service.staging_lock:
        service._write_staging_unlocked(
            StagingPointer(
                schema_version=STAGING_SCHEMA_V1,
                transaction_hash=pointer.transaction_hash,
                decision_hashes=(v1_hash,),
            )
        )

    legacy = service.workflow_snapshot()
    assert legacy["state"] == "recompile_required"
    assert legacy["can_finalize"] is False
    with pytest.raises(FinalizeBlockedError, match="protocol_upgrade"):
        service.finalize(
            {
                "schema_version": "canon-v3/finalize-request/v2",
                "expected_stage_digest": decided["stage_digest"],
                "transaction_hash": pointer.transaction_hash,
                "finalize_token": decided["finalize_token"],
            }
        )

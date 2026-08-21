from __future__ import annotations

import copy
import hashlib
import json

import pytest

from scripts.data_modules.tests.canon_v3_protocol_helpers import (
    finalize as finalize_v2,
    proposal_authority,
    record_decisions as record_decisions_v2,
)

from scripts.data_modules.canon_v3.entity_registry import (
    EntityRegistryConflict,
    EntityRegistryIntegrityError,
    _latest_approved_candidates,
    build_approved_entity_registry,
)
from scripts.data_modules.canon_v3.evidence import candidate_digest
from scripts.data_modules.canon_v3.projection import read_projection, rebuild_projection
from scripts.data_modules.canon_v3.repository import CanonV3Repository, content_hash
from scripts.data_modules.canon_v3.review import (
    DecisionContext,
    ReviewAction,
    ReviewCase,
    ReviewCaseKind,
    decision_from_dict,
    decision_to_dict,
    make_decision,
)
from scripts.data_modules.canon_v3.schema import (
    ArtifactObtainedClaim,
    CustodyChangedClaim,
    EntityObservedClaim,
    FactCandidate,
    IdentityNamespace,
    ObservationKind,
    OpenLoopCreatedClaim,
    PowerBreakthroughClaim,
    PresenceObservedClaim,
    ReviewLevel,
    ReviewObservation,
)
from scripts.data_modules.canon_v3.service import (
    CanonV3Service,
    PreparedTransactionInvalid,
)
from scripts.data_modules.chapter_content_binding import build_chapter_binding
from scripts.data_modules.canonical_history import (
    history_to_asof_snapshot,
    load_canonical_history,
)
from scripts.data_modules.config import DataModulesConfig
from scripts.data_modules.memory_contract_adapter import MemoryContractAdapter


SCAN_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]


def test_latest_approved_candidates_reads_published_v1_and_v2_decisions() -> None:
    candidate_hash = "c" * 64
    transaction_hash = "t" * 64
    context = DecisionContext(
        chapter=2,
        chapter_digest="d" * 64,
        candidate_digest=candidate_hash,
        evidence_digests=("e" * 64,),
        source_digests=("s" * 64,),
        parent_head="p" * 64,
        prior_fact_hashes=(),
        policy_version="policy-v2",
        transaction_digest=transaction_hash,
        effect_digests=("f" * 64,),
    )
    case = ReviewCase(
        case_key="entity-alias-checkpoint",
        kind=ReviewCaseKind.CHECKPOINT,
        level="human_required",
        context=context,
        candidate_id="entity-alias",
    )
    decision = make_decision(case, ReviewAction.APPROVE)
    v1_hash = "1" * 64
    v2_hash = "2" * 64
    wrappers = {
        v1_hash: {
            "schema_version": "canon-v3/decision-envelope/v1",
            "transaction_hash": transaction_hash,
            "chapter": 2,
            "decision": decision_to_dict(decision),
        },
        v2_hash: {
            "schema_version": "canon-v3/decision-envelope/v2",
            "transaction_hash": transaction_hash,
            "chapter": 2,
            "stage_digest_before": "a" * 64,
            "target_digest": case.target_digest,
            "material_digest": "b" * 64,
            "expected_decision_head_hash": None,
            "lineage_key": "e" * 64,
            "decision": decision_to_dict(decision),
        },
    }

    class FakeRepository:
        def read_decision(self, object_hash):
            return wrappers[object_hash]

    base_commit = {"transaction_hash": transaction_hash, "chapter": 2}
    approved_v1 = _latest_approved_candidates(
        FakeRepository(), {**base_commit, "decision_hashes": [v1_hash]}
    )
    approved_v2 = _latest_approved_candidates(
        FakeRepository(), {**base_commit, "decision_hashes": [v2_hash]}
    )

    assert approved_v1 == {candidate_hash: (v1_hash,)}
    assert approved_v2 == {candidate_hash: (v2_hash,)}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_chapter(root, chapter: int, text: str):
    manuscript = root / "正文" / f"第{chapter:04d}章.md"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    manuscript.write_text(text, encoding="utf-8")
    return manuscript, build_chapter_binding(root, chapter)


def _span(manuscript, binding, source_id: str, quote: str) -> dict:
    raw = manuscript.read_bytes()
    quoted = quote.encode("utf-8")
    start = raw.index(quoted)
    return {
        "source_type": "manuscript_span",
        "source_id": source_id,
        "document_sha256": binding["sha256"],
        "chapter": binding["chapter"],
        "start": start,
        "end": start + len(quoted),
        "quote": quote,
        "quote_sha256": _sha(quoted),
    }


def _candidate(
    manuscript,
    binding,
    *,
    candidate_id: str,
    claim,
    quote: str,
) -> FactCandidate:
    source_id = f"source-{candidate_id}"
    claim_data = claim.model_dump(mode="python", exclude_none=True)
    support_map = {
        field: (source_id,)
        for field, value in claim_data.items()
        if field not in {"kind", "namespace"} and value not in ((), [], {})
    }
    return FactCandidate(
        candidate_id=candidate_id,
        claim=claim,
        sources=(_span(manuscript, binding, source_id, quote),),
        support_map=support_map,
    )


def _batch(service, binding, candidates, observations=()) -> dict:
    digests = sorted(candidate_digest(candidate) for candidate in candidates)
    authority = proposal_authority(service, int(binding["chapter"]))
    return {
        **authority,
        "chapter": binding["chapter"],
        "chapter_binding": binding,
        "candidates": [
            candidate.model_dump(mode="json") for candidate in candidates
        ],
        "observations": [
            observation.model_dump(mode="json") for observation in observations
        ],
        "scan_attestations": [
            {
                "attestation_id": f"scan-{binding['chapter']}",
                "scanner": "entity-registry-test",
                "scanner_version": "v1",
                "chapter_sha256": binding["sha256"],
                "parent_head": authority["parent_head"],
                "author_axiom_digest": authority["author_axiom_digest"],
                "entity_registry_digest": authority["entity_registry_digest"],
                "dimensions": SCAN_DIMENSIONS,
                "status": "complete",
                "checked_candidate_digests": digests,
            }
        ],
    }


def _approve_all(service: CanonV3Service, snapshot: dict) -> dict:
    required = [
        case
        for case in snapshot["cases"]
        if case.get("level") == "human_required"
        and not case.get("requires_rewrite")
    ]
    assert required
    return record_decisions_v2(
        service,
        {
            "decisions": [
                {"case_key": case["case_key"], "action": "approve"}
                for case in required
            ]
        },
        snapshot=snapshot,
    )


def _seed_two_approved_entities(root) -> CanonV3Service:
    manuscript, binding = _write_chapter(
        root,
        1,
        "林舟也被称作少主。\n白芷也被称作药师。\n",
    )
    first = _candidate(
        manuscript,
        binding,
        candidate_id="entity-linzhou",
        claim=EntityObservedClaim(entity="林舟", aliases=("少主",)),
        quote="林舟也被称作少主。",
    )
    second = _candidate(
        manuscript,
        binding,
        candidate_id="entity-baizhi",
        claim=EntityObservedClaim(entity="白芷", aliases=("药师",)),
        quote="白芷也被称作药师。",
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [first, second]))
    assert prepared["state"] == "awaiting_human"
    assert _approve_all(service, prepared)["state"] == "ready_to_finalize"
    finalize_v2(service)
    return service


def test_approved_alias_normalizes_later_subject_and_binds_transaction(tmp_path) -> None:
    root = tmp_path / "book"
    service = _seed_two_approved_entities(root)
    registry = build_approved_entity_registry(
        service.repository,
        service.repository.current_head(validate=True),
    )
    assert registry.resolve("少主").canonical_entity == "林舟"
    assert registry.resolve("药师").canonical_entity == "白芷"

    manuscript, binding = _write_chapter(root, 2, "少主仍在青云殿内。\n")
    presence = _candidate(
        manuscript,
        binding,
        candidate_id="presence-young-master",
        claim=PresenceObservedClaim(
            subject="少主",
            location="青云殿",
            presence="在",
        ),
        quote="少主仍在青云殿内。",
    )
    snapshot = service.prepare(_batch(service, binding, [presence]))
    assert snapshot["state"] == "awaiting_human"  # 首次 location 注册

    staging = json.loads(service.staging_path.read_text(encoding="utf-8"))
    envelope = service.repository.read_transaction(staging["transaction_hash"])
    transaction = envelope["prepared_transaction"]
    assert transaction["entity_registry_digest"] == registry.registry_digest
    assert transaction["effects"][0]["claim"]["subject"] == "林舟"
    assert envelope["candidates"][0]["claim"]["subject"] == "少主"
    resolutions = {
        (row["namespace"], row["field"]): row
        for row in transaction["entity_resolutions"]
    }
    subject = resolutions[("actor", "subject")]
    assert subject["raw_value"] == "少主"
    assert subject["canonical_entity"] == "林舟"
    assert subject["status"] == "resolved"
    assert resolutions[("location", "location")]["status"] == "unregistered"

    assert _approve_all(service, snapshot)["state"] == "ready_to_finalize"
    finalize_v2(service)
    facts = read_projection(root)["facts"]
    assert any(
        row["claim"].get("kind") == "presence_observed"
        and row["claim"].get("subject") == "林舟"
        for row in facts
    )


def test_identity_namespaces_prevent_person_alias_from_rewriting_item_or_location(
    tmp_path,
) -> None:
    root = tmp_path / "book"
    service = _seed_two_approved_entities(root)
    manuscript, binding = _write_chapter(
        root,
        2,
        "少主在名为少主的地点把名为少主的物品交给药师。\n",
    )
    quote = "少主在名为少主的地点把名为少主的物品交给药师。"
    presence = _candidate(
        manuscript,
        binding,
        candidate_id="presence-non-person-location",
        claim=PresenceObservedClaim(
            subject="少主",
            location="少主",
            presence="在",
        ),
        quote=quote,
    )
    custody = _candidate(
        manuscript,
        binding,
        candidate_id="custody-person-holders",
        claim=CustodyChangedClaim(
            item="少主",
            from_holder="少主",
            to_holder="药师",
        ),
        quote=quote,
    )
    snapshot = service.prepare(_batch(service, binding, [presence, custody]))
    assert snapshot["state"] == "awaiting_human"  # custody is a checkpoint
    staging = json.loads(service.staging_path.read_text(encoding="utf-8"))
    envelope = service.repository.read_transaction(staging["transaction_hash"])
    effects = {
        effect["claim"]["kind"]: effect["claim"]
        for effect in envelope["prepared_transaction"]["effects"]
    }
    assert effects["presence_observed"] == {
        "kind": "presence_observed",
        "subject": "林舟",
        "location": "少主",
        "presence": "在",
    }
    assert effects["custody_changed"] == {
        "kind": "custody_changed",
        "item": "少主",
        "from_holder": "林舟",
        "to_holder": "白芷",
    }
    assert _approve_all(service, snapshot)["state"] == "ready_to_finalize"
    finalize_v2(service)
    registry = build_approved_entity_registry(
        service.repository,
        service.repository.current_head(validate=True),
        target_chapter=3,
    )
    assert registry.resolve("少主", IdentityNamespace.ACTOR).canonical_entity == "林舟"
    assert registry.resolve("少主", IdentityNamespace.ITEM).canonical_entity == "少主"
    assert registry.resolve("少主", IdentityNamespace.LOCATION).canonical_entity == "少主"


def test_alias_claim_spanning_two_approved_entities_requires_rewrite(tmp_path) -> None:
    root = tmp_path / "book"
    service = _seed_two_approved_entities(root)
    manuscript, binding = _write_chapter(root, 2, "林舟其实也叫白芷。\n")
    conflicting = _candidate(
        manuscript,
        binding,
        candidate_id="entity-conflict",
        claim=EntityObservedClaim(entity="林舟", aliases=("白芷",)),
        quote="林舟其实也叫白芷。",
    )
    snapshot = service.prepare(_batch(service, binding, [conflicting]))
    assert snapshot["state"] == "rewrite_required"
    assert snapshot["counts"]["rewrite"] == 1
    case = snapshot["cases"][0]
    assert case["requires_rewrite"] is True
    assert len(case["context"]["prior_fact_hashes"]) == 2
    assert len(case["review_material"]["prior_facts"]) == 2


def test_same_batch_alias_collision_is_never_first_match_resolved(tmp_path) -> None:
    root = tmp_path / "book"
    manuscript, binding = _write_chapter(
        root,
        1,
        "林舟也叫少主。\n白芷也叫少主。\n",
    )
    candidates = [
        _candidate(
            manuscript,
            binding,
            candidate_id="entity-one",
            claim=EntityObservedClaim(entity="林舟", aliases=("少主",)),
            quote="林舟也叫少主。",
        ),
        _candidate(
            manuscript,
            binding,
            candidate_id="entity-two",
            claim=EntityObservedClaim(entity="白芷", aliases=("少主",)),
            quote="白芷也叫少主。",
        ),
    ]
    service = CanonV3Service(root)
    with pytest.raises(EntityRegistryConflict, match="batch_conflict"):
        service.prepare(_batch(service, binding, candidates))
    assert not service.staging_path.exists()


def test_unapproved_entity_effect_cannot_seed_registry(tmp_path) -> None:
    root = tmp_path / "book"
    repository = CanonV3Repository(root)
    head = repository._initialize_objects(
        genesis_metadata={
            "schema_version": "canon-v3/genesis-metadata/v1",
            "source": "new_project",
            "cutover_chapter": 0,
        }
    )
    effect = {
        "effect_id": "1" * 64,
        "source_order": 0,
        "candidate_digest": "2" * 64,
        "fact_key": "3" * 64,
        "claim": {
            "kind": "entity_observed",
            "entity": "林舟",
            "aliases": ["少主"],
        },
        "source_digests": ["4" * 64],
        "support_map": {"entity": ["4" * 64], "aliases": ["4" * 64]},
    }
    result = repository._seal_objects(
        chapter=1,
        transaction={"chapter": 1},
        expected_head=head,
        decisions=(),
        canon_effects=[effect],
    )
    with pytest.raises(EntityRegistryIntegrityError, match="without_exact_human"):
        build_approved_entity_registry(repository, result.head_hash)


def test_checkpoint_decision_binds_registry_and_resolution_digests(tmp_path) -> None:
    root = tmp_path / "book"
    service = _seed_two_approved_entities(root)
    manuscript, binding = _write_chapter(
        root,
        2,
        "少主的境界从炼气突破到了筑基。\n",
    )
    power = _candidate(
        manuscript,
        binding,
        candidate_id="power-alias",
        claim=PowerBreakthroughClaim(
            subject="少主",
            system="境界",
            before="炼气",
            after="筑基",
        ),
        quote="少主的境界从炼气突破到了筑基。",
    )
    snapshot = service.prepare(_batch(service, binding, [power]))
    assert snapshot["state"] == "awaiting_human"
    case = snapshot["cases"][0]
    context = case["context"]
    assert context["entity_registry_digest"] != "0" * 64
    assert context["entity_resolution_digests"]

    decided = record_decisions_v2(service,
        {"decisions": [{"case_key": case["case_key"], "action": "approve"}]}
    )
    assert decided["state"] == "ready_to_finalize"
    staging = json.loads(service.staging_path.read_text(encoding="utf-8"))
    wrapper = service.repository.read_decision(staging["decision_hashes"][0])
    stored = wrapper["decision"]
    assert stored["context"]["entity_registry_digest"] == (
        context["entity_registry_digest"]
    )
    assert stored["context"]["entity_resolution_digests"] == (
        context["entity_resolution_digests"]
    )

    tampered = copy.deepcopy(stored)
    tampered["context"]["entity_registry_digest"] = "f" * 64
    with pytest.raises(ValueError, match="decision_hash_mismatch"):
        decision_from_dict(tampered)


def test_registry_seeds_content_addressed_legacy_aliases(tmp_path) -> None:
    root = tmp_path / "book"
    repository = CanonV3Repository(root)
    legacy_facts = {
        "canonical_facts": [],
        "entities": {
            "linzhou": {
                "id": "linzhou",
                "name": "林舟",
                "type": "角色",
                "aliases": ["少主"],
            }
        },
    }
    snapshot = {
        "schema_version": "canon-v3/legacy-fact-snapshot/v1",
        "source_schema_version": "canon-ledger-asof-snapshot/v3",
        "cutover_chapter": 0,
        "facts": legacy_facts,
    }
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
    rebuild_projection(root)

    registry = build_approved_entity_registry(
        repository,
        repository.current_head(validate=True),
        target_chapter=1,
    )
    binding = registry.resolve("少主", IdentityNamespace.ACTOR)
    assert binding is not None
    assert binding.canonical_entity == "linzhou"
    assert binding.legacy_snapshot_digests == (content_hash(snapshot),)


def test_future_alias_is_not_visible_when_rewriting_earlier_chapter(tmp_path) -> None:
    root = tmp_path / "book"
    service = CanonV3Service(root)
    for chapter in range(1, 4):
        text = f"第{chapter}个谜团仍未解开。\n"
        manuscript, binding = _write_chapter(root, chapter, text)
        loop = _candidate(
            manuscript,
            binding,
            candidate_id=f"loop-{chapter}",
            claim=OpenLoopCreatedClaim(loop=f"第{chapter}个谜团"),
            quote=text.strip(),
        )
        assert service.prepare(_batch(service, binding, [loop]))["state"] == "ready_to_finalize"
        finalize_v2(service)

    manuscript, binding = _write_chapter(root, 4, "林舟也被称作少主。\n")
    alias = _candidate(
        manuscript,
        binding,
        candidate_id="future-alias",
        claim=EntityObservedClaim(entity="林舟", aliases=("少主",)),
        quote="林舟也被称作少主。",
    )
    prepared = service.prepare(_batch(service, binding, [alias]))
    _approve_all(service, prepared)
    finalize_v2(service)

    head = service.repository.current_head(validate=True)
    visible = build_approved_entity_registry(
        service.repository, head, target_chapter=5
    ).resolve("少主", IdentityNamespace.ACTOR)
    assert visible is not None and visible.canonical_entity == "林舟"
    assert build_approved_entity_registry(
        service.repository, head, target_chapter=2
    ).resolve("少主", IdentityNamespace.ACTOR) is None

    manuscript, binding = _write_chapter(root, 2, "少主仍在青云殿内。\n")
    presence = _candidate(
        manuscript,
        binding,
        candidate_id="rewrite-presence",
        claim=PresenceObservedClaim(subject="少主", location="青云殿", presence="在"),
        quote="少主仍在青云殿内。",
    )
    future_digest = next(
        row["fact_digest"]
        for row in read_projection(root)["history"]
        if row["chapter"] == 4
    )
    future_reference = ReviewObservation(
        observation_id="future-prior-reference",
        candidate_id=presence.candidate_id,
        kind=ObservationKind.AMBIGUITY,
        level=ReviewLevel.HUMAN_REQUIRED,
        reason="故意引用未来章节事实",
        prior_fact_digests=(future_digest,),
    )
    with pytest.raises(PreparedTransactionInvalid, match="prior_fact_not_in_parent_head"):
        service.prepare(_batch(service, binding, [presence], [future_reference]))
    rewritten = service.prepare(_batch(service, binding, [presence]))
    assert rewritten["state"] == "awaiting_human"
    staging = json.loads(service.staging_path.read_text(encoding="utf-8"))
    transaction = service.repository.read_transaction(staging["transaction_hash"])
    effect = transaction["prepared_transaction"]["effects"][0]
    assert effect["claim"]["subject"] == "少主"
    subject_resolution = next(
        row
        for row in transaction["prepared_transaction"]["entity_resolutions"]
        if row["field"] == "subject"
    )
    assert subject_resolution["status"] == "unregistered"


def test_artifact_and_custody_share_one_canonical_item_slot(tmp_path) -> None:
    root = tmp_path / "book"
    manuscript, binding = _write_chapter(
        root,
        1,
        "林舟获得玄铁剑。随后林舟把玄铁剑交给苏月。\n",
    )
    obtained = _candidate(
        manuscript,
        binding,
        candidate_id="item-obtained",
        claim=ArtifactObtainedClaim(owner="林舟", artifact="玄铁剑"),
        quote="林舟获得玄铁剑。",
    )
    transferred = _candidate(
        manuscript,
        binding,
        candidate_id="item-transferred",
        claim=CustodyChangedClaim(
            item="玄铁剑", from_holder="林舟", to_holder="苏月"
        ),
        quote="随后林舟把玄铁剑交给苏月。",
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [transferred, obtained]))
    staging = json.loads(service.staging_path.read_text(encoding="utf-8"))
    transaction = service.repository.read_transaction(staging["transaction_hash"])
    effects = transaction["prepared_transaction"]["effects"]
    assert len({effect["fact_key"] for effect in effects}) == 1
    assert effects[0]["prior_effect_id"] is None
    assert effects[1]["prior_effect_id"] == effects[0]["effect_id"]
    _approve_all(service, prepared)
    finalize_v2(service)
    projection = read_projection(root)
    assert [row["claim"]["kind"] for row in projection["history"]] == [
        "artifact_obtained",
        "custody_changed",
    ]
    assert projection["facts"][0]["claim"]["kind"] == "custody_changed"


def test_compat_history_and_query_preserve_identity_namespaces(tmp_path) -> None:
    root = tmp_path / "book"
    manuscript, binding = _write_chapter(
        root,
        1,
        "玄铁是一个人的名字。玄铁也是一把剑的名字。\n",
    )
    actor = _candidate(
        manuscript,
        binding,
        candidate_id="actor-xuantie",
        claim=EntityObservedClaim(
            namespace=IdentityNamespace.ACTOR,
            entity="玄铁",
        ),
        quote="玄铁是一个人的名字。",
    )
    item = _candidate(
        manuscript,
        binding,
        candidate_id="item-xuantie",
        claim=EntityObservedClaim(
            namespace=IdentityNamespace.ITEM,
            entity="玄铁",
        ),
        quote="玄铁也是一把剑的名字。",
    )
    service = CanonV3Service(root)
    prepared = service.prepare(_batch(service, binding, [actor, item]))
    _approve_all(service, prepared)
    finalize_v2(service)

    history = load_canonical_history(root, as_of_chapter=1)
    assert history.entities["玄铁"]["type"] == "角色"
    assert history.entities["玄铁"]["namespace"] == "actor"
    assert history.entities["item:玄铁"]["type"] == "物品"
    assert history.entities["item:玄铁"]["namespace"] == "item"
    adapter = MemoryContractAdapter(DataModulesConfig(project_root=root))
    assert adapter.query_entity("玄铁", as_of_chapter=1) is None
    assert adapter.query_entity("actor:玄铁", as_of_chapter=1).type == "角色"
    assert adapter.query_entity("item:玄铁", as_of_chapter=1).type == "物品"


def test_compat_history_accumulates_approved_alias_registrations(tmp_path) -> None:
    root = tmp_path / "book"
    service = _seed_two_approved_entities(root)
    manuscript, binding = _write_chapter(root, 2, "林舟也被称作宗主。\n")
    alias = _candidate(
        manuscript,
        binding,
        candidate_id="entity-linzhou-second-alias",
        claim=EntityObservedClaim(entity="林舟", aliases=("宗主",)),
        quote="林舟也被称作宗主。",
    )
    prepared = service.prepare(_batch(service, binding, [alias]))
    _approve_all(service, prepared)
    finalize_v2(service)

    history = load_canonical_history(root, as_of_chapter=2)
    assert history.entities["林舟"]["aliases"] == ["少主", "宗主"]
    snapshot = history_to_asof_snapshot(history, chapter=3)
    assert snapshot["alias_index"]["少主"] == ["林舟"]
    assert snapshot["alias_index"]["宗主"] == ["林舟"]

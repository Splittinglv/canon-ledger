from __future__ import annotations

import hashlib

import pytest

from scripts.data_modules.tests.canon_v3_protocol_helpers import (
    finalize as finalize_v2,
    proposal_authority,
    record_decisions as record_decisions_v2,
)

from scripts.data_modules.canon_v3.evidence import candidate_digest
from scripts.data_modules.canon_v3.entity_registry import (
    EntityRegistryConflict,
    build_approved_entity_registry,
)
from scripts.data_modules.canon_v3.projection import read_projection
from scripts.data_modules.canon_v3.repository import CanonRepositoryError
from scripts.data_modules.canon_v3.schema import (
    CharacterStateChangedClaim,
    EntityObservedClaim,
    FactCandidate,
    IdentityNamespace,
    KnowledgeStateChangedClaim,
    OpenLoopClosedClaim,
    OpenLoopCreatedClaim,
    PresenceObservedClaim,
    PromiseCreatedClaim,
    TimelineObservedClaim,
    WorldRuleBrokenClaim,
    WorldRuleRevealedClaim,
)
from scripts.data_modules.canon_v3.service import (
    CanonV3Service,
    PreparedTransactionInvalid,
)
from scripts.data_modules.chapter_content_binding import build_chapter_binding


SCAN_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]


def _write(root, chapter: int, text: str):
    path = root / "正文" / f"第{chapter:04d}章.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path, build_chapter_binding(root, chapter)


def _span(path, binding, source_id: str, quote: str) -> dict:
    raw = path.read_bytes()
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
        "quote_sha256": hashlib.sha256(quoted).hexdigest(),
    }


def _candidate(path, binding, candidate_id: str, claim, quote: str, *, links=None):
    source_id = f"source-{candidate_id}"
    metadata = {
        "kind",
        "namespace",
        "slot_id",
        "rule_slot_id",
        "link_to",
        "canonical_entity",
        "canonical_field",
        "new_instance",
    }
    support = {
        key: (source_id,)
        for key, value in claim.model_dump(mode="python", exclude_none=True).items()
        if key not in metadata and value not in ((), [], {})
    }
    return FactCandidate(
        candidate_id=candidate_id,
        claim=claim,
        sources=(_span(path, binding, source_id, quote),),
        support_map=support,
        identity_links=links or {},
    )


def _batch(service, binding, candidates, observations=()):
    digests = sorted(candidate_digest(item) for item in candidates)
    authority = proposal_authority(service, int(binding["chapter"]))
    return {
        **authority,
        "chapter": binding["chapter"],
        "chapter_binding": binding,
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "observations": list(observations),
        "scan_attestations": [
            {
                "attestation_id": "complete-fact-scan",
                "scanner": "reviewer",
                "scanner_version": "global-invariants",
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


def _approve(service: CanonV3Service, snapshot: dict) -> None:
    decisions = [
        {"case_key": case["case_key"], "action": "approve"}
        for case in snapshot["cases"]
        if case.get("level") == "human_required"
        and not case.get("requires_rewrite")
    ]
    if decisions:
        record_decisions_v2(service, decisions, snapshot=snapshot)


def test_same_wording_creates_distinct_promise_and_timeline_instances(tmp_path):
    root = tmp_path / "book"
    path, binding = _write(
        root,
        1,
        "林舟向苏月说我会保护你。林舟向沈青说我会保护你。\n"
        "清晨林舟击败守卫。夜晚林舟再次击败守卫。\n",
    )
    candidates = [
        _candidate(
            path,
            binding,
            "promise-suyue",
            PromiseCreatedClaim(
                promisor="林舟", promisee="苏月", promise="我会保护你"
            ),
            "林舟向苏月说我会保护你。",
        ),
        _candidate(
            path,
            binding,
            "promise-shenqing",
            PromiseCreatedClaim(
                promisor="林舟", promisee="沈青", promise="我会保护你"
            ),
            "林舟向沈青说我会保护你。",
        ),
        _candidate(
            path,
            binding,
            "timeline-morning",
            TimelineObservedClaim(event="击败守卫", time_anchor="清晨"),
            "清晨林舟击败守卫。",
        ),
        _candidate(
            path,
            binding,
            "timeline-night",
            TimelineObservedClaim(event="击败守卫", time_anchor="夜晚"),
            "夜晚林舟再次击败守卫。",
        ),
    ]
    service = CanonV3Service(root)
    snapshot = service.prepare(_batch(service, binding, candidates))
    _approve(service, snapshot)
    finalize_v2(service)

    projection = read_projection(root)
    promises = [
        row for row in projection["facts"]
        if row["claim"]["kind"] == "promise_created"
    ]
    timeline = [
        row for row in projection["facts"]
        if row["claim"]["kind"] == "timeline_observed"
    ]
    assert len(promises) == 2
    assert len({row["claim"]["slot_id"] for row in promises}) == 2
    assert len(timeline) == 2
    assert len({row["claim"]["slot_id"] for row in timeline}) == 2


def test_lifecycle_terminal_requires_active_slot_and_inherits_prior_text(tmp_path):
    root = tmp_path / "book"
    path1, binding1 = _write(root, 1, "谁偷走了灵钥仍是谜。\n")
    created = _candidate(
        path1,
        binding1,
        "loop-created",
        OpenLoopCreatedClaim(loop="谁偷走了灵钥"),
        "谁偷走了灵钥仍是谜。",
    )
    service = CanonV3Service(root)
    service.prepare(_batch(service, binding1, [created]))
    finalize_v2(service)
    created_record = read_projection(root)["facts"][0]
    slot_id = created_record["claim"]["slot_id"]

    path2, binding2 = _write(root, 2, "谜底揭晓：管家偷走了灵钥。\n")
    closed = _candidate(
        path2,
        binding2,
        "loop-closed",
        OpenLoopClosedClaim(
            slot_id=slot_id,
            resolution="管家偷走了灵钥",
        ),
        "谜底揭晓：管家偷走了灵钥。",
    )
    snapshot = service.prepare(_batch(service, binding2, [closed]))
    material = snapshot["cases"][0]["review_material"]
    assert material["prior_facts"][0]["fact_digest"] == created_record["fact_digest"]
    compiled = material["compiled_effects"][0]
    assert compiled["claim"]["loop"] == "谁偷走了灵钥"
    assert compiled["inherited_fields"]["loop"] == created_record["fact_digest"]
    _approve(service, snapshot)
    finalize_v2(service)

    from scripts.data_modules.canonical_history import load_canonical_history

    history = load_canonical_history(root, 2)
    assert history.obligations == []
    assert {row["category"] for row in history.lifecycle_history} >= {
        "open_loop_created",
        "open_loop_closed",
    }

    path3, binding3 = _write(root, 3, "又有人声称无人知晓。\n")
    forged = _candidate(
        path3,
        binding3,
        "loop-forged",
        OpenLoopClosedClaim(
            slot_id="f" * 64,
            resolution="无人知晓",
        ),
        "又有人声称无人知晓。",
    )
    with pytest.raises(PreparedTransactionInvalid, match="terminal_slot"):
        service.prepare(_batch(service, binding3, [forged]))


def test_rule_remains_active_while_each_violation_is_preserved(tmp_path):
    root = tmp_path / "book"
    path1, binding1 = _write(root, 1, "月门只能在夜间开启。\n")
    rule = _candidate(
        path1,
        binding1,
        "rule-created",
        WorldRuleRevealedClaim(rule="月门只能在夜间开启"),
        "月门只能在夜间开启。",
    )
    service = CanonV3Service(root)
    first = service.prepare(_batch(service, binding1, [rule]))
    _approve(service, first)
    finalize_v2(service)
    rule_slot = read_projection(root)["facts"][0]["claim"]["slot_id"]

    path2, binding2 = _write(root, 2, "林舟在白昼开启月门。苏月也在午后开启月门。\n")
    violations = [
        _candidate(
            path2,
            binding2,
            "violation-linzhou",
            WorldRuleBrokenClaim(
                rule_slot_id=rule_slot,
                violation="林舟在白昼开启月门",
            ),
            "林舟在白昼开启月门。",
        ),
        _candidate(
            path2,
            binding2,
            "violation-suyue",
            WorldRuleBrokenClaim(
                rule_slot_id=rule_slot,
                violation="苏月也在午后开启月门",
            ),
            "苏月也在午后开启月门。",
        ),
    ]
    second = service.prepare(_batch(service, binding2, violations))
    assert all(case["review_material"]["prior_facts"] for case in second["cases"])
    _approve(service, second)
    finalize_v2(service)
    projection = read_projection(root)
    assert sum(row["claim"]["kind"] == "world_rule_revealed" for row in projection["facts"]) == 1
    assert sum(row["claim"]["kind"] == "world_rule_broken" for row in projection["facts"]) == 2


def test_state_slot_keeps_canonical_field_when_display_wording_changes(tmp_path):
    root = tmp_path / "book"
    path1, binding1 = _write(root, 1, "林舟的愿望从归乡变为复仇。\n")
    first_candidate = _candidate(
        path1,
        binding1,
        "state-first",
        CharacterStateChangedClaim(
            subject="林舟", attribute="愿望", before="归乡", after="复仇"
        ),
        "林舟的愿望从归乡变为复仇。",
    )
    service = CanonV3Service(root)
    first = service.prepare(_batch(service, binding1, [first_candidate]))
    _approve(service, first)
    finalize_v2(service)
    prior = read_projection(root)["facts"][0]

    path2, binding2 = _write(root, 2, "林舟的夙愿从复仇变为守护故乡。\n")
    second_candidate = _candidate(
        path2,
        binding2,
        "state-second",
        CharacterStateChangedClaim(
            slot_id=prior["claim"]["slot_id"],
            subject="林舟",
            attribute="夙愿",
            before="复仇",
            after="守护故乡",
        ),
        "林舟的夙愿从复仇变为守护故乡。",
    )
    second = service.prepare(_batch(service, binding2, [second_candidate]))
    compiled = second["cases"][0]["review_material"]["compiled_effects"][0]
    assert compiled["claim"]["canonical_field"] == "愿望"
    assert compiled["prior_fact_digest"] == prior["fact_digest"]
    _approve(service, second)
    finalize_v2(service)
    from scripts.data_modules.canonical_history import load_canonical_history

    history = load_canonical_history(root, 2)
    active_states = [
        row
        for row in history.canonical_facts
        if row.get("category") == "character_state_changed"
    ]
    assert len(active_states) == 1
    assert active_states[0]["field"] == "愿望"
    assert active_states[0]["value"] == "守护故乡"

    path3, binding3 = _write(root, 3, "苏月的愿望从复仇变为归隐。\n")
    hijack = _candidate(
        path3,
        binding3,
        "state-slot-hijack",
        CharacterStateChangedClaim(
            slot_id=read_projection(root)["facts"][0]["claim"]["slot_id"],
            subject="苏月",
            attribute="愿望",
            before="复仇",
            after="归隐",
        ),
        "苏月的愿望从复仇变为归隐。",
    )
    with pytest.raises(
        PreparedTransactionInvalid,
        match="character_state_subject_mismatch",
    ):
        service.prepare(_batch(service, binding3, [hijack]))


def test_knowledge_update_can_inherit_proposition_without_leaving_old_known_fact(tmp_path):
    root = tmp_path / "book"
    path1, binding1 = _write(root, 1, "林舟知道宝藏在北山。\n")
    known = _candidate(
        path1,
        binding1,
        "knowledge-known",
        KnowledgeStateChangedClaim(
            subject="林舟", knowledge="宝藏在北山", state="知道"
        ),
        "林舟知道宝藏在北山。",
    )
    service = CanonV3Service(root)
    first = service.prepare(_batch(service, binding1, [known]))
    _approve(service, first)
    finalize_v2(service)
    prior = read_projection(root)["facts"][0]

    path2, binding2 = _write(root, 2, "林舟已遗忘这段记忆。\n")
    forgotten = _candidate(
        path2,
        binding2,
        "knowledge-forgotten",
        KnowledgeStateChangedClaim(
            slot_id=prior["claim"]["slot_id"],
            subject="林舟",
            state="已遗忘",
        ),
        "林舟已遗忘这段记忆。",
    )
    second = service.prepare(_batch(service, binding2, [forgotten]))
    material = second["cases"][0]["review_material"]
    assert material["compiled_effects"][0]["claim"]["knowledge"] == "宝藏在北山"
    assert material["prior_facts"][0]["fact_digest"] == prior["fact_digest"]
    _approve(service, second)
    finalize_v2(service)

    from scripts.data_modules.canonical_history import load_canonical_history

    history = load_canonical_history(root, 2)
    facts = history.knowledge_by_entity["林舟"]
    assert list(facts) == [prior["claim"]["slot_id"]]
    assert next(iter(facts.values()))["payload"]["state"] == "已遗忘"


def test_same_name_new_instance_can_be_linked_in_same_batch(tmp_path):
    root = tmp_path / "book"
    path1, binding1 = _write(root, 1, "王强走进客栈。\n")
    old = _candidate(
        path1,
        binding1,
        "old-wang",
        EntityObservedClaim(entity="王强"),
        "王强走进客栈。",
    )
    service = CanonV3Service(root)
    snapshot = service.prepare(_batch(service, binding1, [old]))
    _approve(service, snapshot)
    finalize_v2(service)

    path2, binding2 = _write(root, 2, "另一个王强出现在码头，王强仍在码头。\n")
    registration = _candidate(
        path2,
        binding2,
        "new-wang",
        EntityObservedClaim(entity="王强", new_instance=True),
        "另一个王强出现在码头",
    )
    presence = _candidate(
        path2,
        binding2,
        "new-wang-presence",
        PresenceObservedClaim(subject="王强", location="码头", presence="在"),
        "王强仍在码头。",
        links={"subject": "candidate:new-wang"},
    )
    second = service.prepare(_batch(service, binding2, [registration, presence]))
    compiled_presence = next(
        effect
        for case in second["cases"]
        for effect in case["review_material"]["compiled_effects"]
        if effect["claim"]["kind"] == "presence_observed"
    )
    assert compiled_presence["claim"]["subject"] != "王强"
    _approve(service, second)
    finalize_v2(service)

    registry = build_approved_entity_registry(
        service.repository,
        service.repository.current_head(validate=True),
        target_chapter=3,
    )
    assert registry.resolve("王强", IdentityNamespace.ACTOR) is None
    assert len(registry.resolve_all("王强", IdentityNamespace.ACTOR)) == 2

    path3, binding3 = _write(root, 3, "王强离开码头。\n")
    ambiguous = _candidate(
        path3,
        binding3,
        "ambiguous-wang",
        PresenceObservedClaim(subject="王强", location="码头", presence="离开"),
        "王强离开码头。",
    )
    third = service.prepare(_batch(service, binding3, [ambiguous]))
    assert third["state"] == "rewrite_required"


def test_negative_decision_cannot_be_erased_by_reprepare_or_public_seal_subset(tmp_path):
    root = tmp_path / "book"
    path, binding = _write(root, 1, "苏月可能仍在青云殿。\n")
    candidate = _candidate(
        path,
        binding,
        "presence-uncertain",
        PresenceObservedClaim(subject="苏月", location="青云殿", presence="在"),
        "苏月可能仍在青云殿。",
    )
    observation = {
        "observation_id": "presence-ambiguity",
        "candidate_id": candidate.candidate_id,
        "kind": "ambiguity",
        "level": "human_required",
        "reason": "正文带有可能，需作者确认是否进入正史",
        "prior_fact_digests": [],
    }
    service = CanonV3Service(root)
    staged = service.prepare(_batch(service, binding, [candidate], [observation]))
    record_decisions_v2(service,
        {"decisions": [{"case_key": staged["cases"][0]["case_key"], "action": "omit"}]}
    )
    with pytest.raises(
        PreparedTransactionInvalid,
        match="negative_adjudication_candidate_reintroduced",
    ):
        service.prepare(_batch(service, binding, [candidate]))

    # Restore the authoritative staged transaction and finalize the omission;
    # the immutable commit/manifest ancestry must retain the tombstone too.
    snapshot = service.workflow_snapshot()
    assert snapshot["state"] == "ready_to_finalize"
    finalize_v2(service)
    with pytest.raises(
        PreparedTransactionInvalid,
        match="negative_adjudication_candidate_reintroduced",
    ):
        service.prepare(_batch(service, binding, [candidate]))


def test_public_seal_rejects_non_authoritative_old_decision_subset(tmp_path):
    root = tmp_path / "book"
    path, binding = _write(root, 1, "林舟的愿望从归乡变为复仇。\n")
    candidate = _candidate(
        path,
        binding,
        "state-review",
        CharacterStateChangedClaim(
            subject="林舟", attribute="愿望", before="归乡", after="复仇"
        ),
        "林舟的愿望从归乡变为复仇。",
    )
    service = CanonV3Service(root)
    staged = service.prepare(_batch(service, binding, [candidate]))
    case_key = staged["cases"][0]["case_key"]
    record_decisions_v2(service,
        {"decisions": [{"case_key": case_key, "action": "approve"}]}
    )
    after_approve = service._read_staging_unlocked()
    assert after_approve is not None
    old_subset = after_approve.decision_hashes
    record_decisions_v2(service,
        {"decisions": [{"case_key": case_key, "action": "rewrite"}]}
    )
    pointer = service._read_staging_unlocked()
    assert pointer is not None and len(pointer.decision_hashes) > len(old_subset)
    envelope = service._load_envelope(pointer.transaction_hash)
    with pytest.raises(CanonRepositoryError, match="authoritative_staging_decision_set"):
        service.repository.seal(
            chapter=1,
            transaction=pointer.transaction_hash,
            expected_head=envelope.prepared_transaction.parent_head,
            decisions=old_subset,
            canon_effects=[
                effect.model_dump(mode="json")
                for effect in envelope.prepared_transaction.effects
            ],
        )

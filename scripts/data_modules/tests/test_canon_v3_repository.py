#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.canon_v3.history import (  # noqa: E402
    current_canon_effects,
    fold_current_history,
)
from data_modules.canon_v3.repository import (  # noqa: E402
    CanonChapterSequenceError,
    CanonHeadConflict,
    CanonIntegrityError,
    CanonRepository,
    CanonRepositoryError,
    ProjectionStaleError,
    content_hash,
)


def _transaction(chapter: int, value: str) -> dict:
    return {
        "schema_version": "canon-v3/prepared-transaction/v1",
        "chapter": chapter,
        "chapter_sha256": f"chapter-{chapter}-sha",
        "candidate_digest": f"candidate-{chapter}-{value}",
        "canon_effects": [
            {
                "op": "set",
                "fact_key": f"character:hero:chapter:{chapter}",
                "value": value,
            }
        ],
    }


def _list_reducer(state: list[dict], effect: dict) -> list[dict]:
    return [*state, effect]


def _storage_genesis(repo: CanonRepository) -> str:
    return repo._initialize_objects(
        genesis_metadata={
            "schema_version": "canon-v3/genesis-metadata/v1",
            "source": "new_project",
            "cutover_chapter": 0,
        }
    )


def test_private_storage_genesis_is_deterministic_for_fault_tests(tmp_path):
    first = CanonRepository(tmp_path / "first")
    second = CanonRepository(tmp_path / "second")

    first_head = _storage_genesis(first)
    second_head = _storage_genesis(second)

    assert first_head == second_head
    assert first.current_head() == first_head
    assert first.current_manifest() == {
        "schema_version": "canon-v3/active-manifest/v1",
        "generation": 0,
        "parent_head_hash": None,
        "chapters": [],
        "genesis_metadata": {
            "schema_version": "canon-v3/genesis-metadata/v1",
            "source": "new_project",
            "cutover_chapter": 0,
        },
    }
    assert first.projection_binding().as_dict() == {
        "schema_version": "canon-v3/projection-binding/v1",
        "generation": 0,
        "head_hash": first_head,
    }


def test_public_seal_rejects_uncompiled_effects_and_missing_review_proof(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)

    with pytest.raises(CanonRepositoryError, match="publication_proof_invalid"):
        repo.seal(
            chapter=1,
            transaction={"chapter": 1},
            expected_head=genesis,
            decisions=(),
            canon_effects=[
                {
                    "fact_key": "forged",
                    "claim": {
                        "kind": "world_rule_revealed",
                        "rule": "伪造规则",
                    },
                }
            ],
        )

    assert repo.current_head() == genesis
    assert repo.current_commits() == []


def test_public_initialize_rejects_fabricated_cutover_metadata(tmp_path):
    repo = CanonRepository(tmp_path)

    with pytest.raises(CanonRepositoryError, match="requires_verified_fact_snapshot"):
        repo.initialize(
            genesis_metadata={"source": "new_project", "cutover_chapter": 10}
        )

    assert repo.current_head(validate=False) is None


def test_public_empty_initialize_cannot_bypass_service_snapshot_import(tmp_path):
    repo = CanonRepository(tmp_path)
    master = tmp_path / ".story-system" / "MASTER_SETTING.json"
    master.parent.mkdir(parents=True)
    master.write_text('{"initial_canon":{"protagonist":{"name":"林舟"}}}', encoding="utf-8")

    with pytest.raises(CanonRepositoryError, match="requires_verified_fact_snapshot"):
        repo.initialize()

    assert repo.current_head(validate=False) is None


def test_public_initialize_rejects_self_hashed_fabricated_legacy_snapshot(tmp_path):
    from scripts.data_modules.canon_v3.migration import (
        LEGACY_GENESIS_SCHEMA,
        LEGACY_SNAPSHOT_SCHEMA,
    )

    repo = CanonRepository(tmp_path)
    snapshot = {
        "schema_version": LEGACY_SNAPSHOT_SCHEMA,
        "source_schema_version": "story-memory/as-of/v2",
        "cutover_chapter": 0,
        "facts": {
            "schema_version": "story-memory/as-of/v2",
            "as_of_chapter": 0,
            "valid_chapters": [],
            "invalid_sources": [],
            "canonical_facts": [
                {
                    "kind": "world_rule_revealed",
                    "rule": "伪造正史",
                }
            ],
        },
    }

    with pytest.raises(
        CanonRepositoryError,
        match="legacy_snapshot_provenance_mismatch",
    ):
        repo.initialize(
            genesis_metadata={
                "schema_version": LEGACY_GENESIS_SCHEMA,
                "source": "new_project",
                "cutover_chapter": 0,
                "v2_commits": [],
                "legacy_snapshot": snapshot,
                "legacy_snapshot_sha256": content_hash(snapshot),
            }
        )

    assert repo.current_head(validate=False) is None


def test_content_addressed_inputs_are_immutable_and_order_independent(tmp_path):
    repo = CanonRepository(tmp_path)
    transaction = _transaction(1, "刀")
    first_hash = repo.put_transaction(transaction)
    transaction["canon_effects"][0]["value"] = "被调用方篡改"
    second_hash = repo.put_transaction(_transaction(1, "刀"))

    assert first_hash == second_hash
    assert repo.read_transaction(first_hash)["canon_effects"][0]["value"] == "刀"
    assert repo.object_path("transaction", first_hash).is_file()


def test_seal_is_deterministic_across_repositories_and_idempotent_in_place(tmp_path):
    first = CanonRepository(tmp_path / "first")
    second = CanonRepository(tmp_path / "second")
    genesis_first = _storage_genesis(first)
    genesis_second = _storage_genesis(second)
    transaction_hash_first = first.put_transaction(_transaction(1, "刀"))
    transaction_hash_second = second.put_transaction(_transaction(1, "刀"))
    assert transaction_hash_first == transaction_hash_second
    decisions = [
        {"transaction_hash": transaction_hash_first, "chapter": 1, "action": "approve", "case": "a"},
        {"transaction_hash": transaction_hash_first, "chapter": 1, "action": "approve", "case": "b"},
    ]
    decisions_second = [
        {**decisions[1], "transaction_hash": transaction_hash_second},
        {**decisions[0], "transaction_hash": transaction_hash_second},
    ]

    result_first = first._seal_objects(
        chapter=1,
        transaction=transaction_hash_first,
        expected_head=genesis_first,
        decisions=decisions,
    )
    result_second = second._seal_objects(
        chapter=1,
        transaction=transaction_hash_second,
        expected_head=genesis_second,
        decisions=decisions_second,
    )

    assert result_first.transaction_hash == result_second.transaction_hash
    assert result_first.decision_hashes == result_second.decision_hashes
    assert result_first.commit_hash == result_second.commit_hash
    assert result_first.manifest_hash == result_second.manifest_hash
    repeated = first._seal_objects(
        chapter=1,
        transaction=transaction_hash_first,
        expected_head=result_first.head_hash,
        decisions=list(reversed(decisions)),
    )
    assert repeated.created is False
    assert repeated.commit_hash == result_first.commit_hash
    assert repeated.manifest_hash == result_first.manifest_hash
    assert repeated.generation == 1


def test_expected_head_compare_and_swap_rejects_stale_writer(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )

    with pytest.raises(CanonHeadConflict) as caught:
        repo._seal_objects(
            chapter=2,
            transaction=_transaction(2, "钥匙"),
            expected_head=genesis,
        )

    assert caught.value.expected == genesis
    assert caught.value.actual == first.head_hash
    assert repo.current_head() == first.head_hash


def test_project_lock_serializes_competing_expected_head_publishers(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    ready = threading.Barrier(2)

    def publish(value: str):
        ready.wait()
        try:
            return repo._seal_objects(
                chapter=1,
                transaction=_transaction(1, value),
                expected_head=genesis,
            )
        except CanonHeadConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, ["刀", "剑"]))

    winners = [item for item in outcomes if not isinstance(item, CanonHeadConflict)]
    conflicts = [item for item in outcomes if isinstance(item, CanonHeadConflict)]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert repo.current_head() == winners[0].head_hash
    assert len(current_canon_effects(repo)) == 1


def test_sequential_publish_rejects_chapter_gap(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )
    with pytest.raises(CanonChapterSequenceError):
        repo._seal_objects(
            chapter=3,
            transaction=_transaction(3, "钥匙"),
            expected_head=first.head_hash,
        )
    assert repo.current_head() == first.head_hash


def test_history_folds_only_effects_reachable_from_current_manifest(tmp_path):
    repo = CanonRepository(tmp_path)
    head = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=head,
    )
    second = repo._seal_objects(
        chapter=2,
        transaction=_transaction(2, "钥匙"),
        expected_head=first.head_hash,
    )

    snapshot = fold_current_history(repo, initial=[], reducer=_list_reducer)

    assert snapshot.head_hash == second.head_hash
    assert snapshot.generation == 2
    assert [effect["value"] for effect in snapshot.state] == ["刀", "钥匙"]
    assert snapshot.effects == snapshot.state
    assert snapshot.projection_binding == repo.projection_binding()


def test_early_chapter_replacement_creates_revision_and_truncates_suffix(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )
    second = repo._seal_objects(
        chapter=2,
        transaction=_transaction(2, "钥匙"),
        expected_head=first.head_hash,
    )
    old_first_path = repo.object_path("commit", first.commit_hash)
    old_second_path = repo.object_path("commit", second.commit_hash)

    replacement = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "剑"),
        expected_head=second.head_hash,
    )

    manifest = repo.current_manifest()
    assert manifest is not None
    assert [(entry["chapter"], entry["revision"]) for entry in manifest["chapters"]] == [(1, 2)]
    assert replacement.revision == 2
    assert replacement.commit_hash not in {first.commit_hash, second.commit_hash}
    assert old_first_path.is_file() and old_second_path.is_file()
    assert repo.read_commit(first.commit_hash)["canon_effects"][0]["value"] == "刀"
    assert repo.read_commit(second.commit_hash)["canon_effects"][0]["value"] == "钥匙"
    assert repo.is_commit_canonical(first.commit_hash) is False
    assert repo.is_commit_canonical(second.commit_hash) is False
    assert repo.is_commit_canonical(replacement.commit_hash) is True
    assert [effect["value"] for effect in current_canon_effects(repo)] == ["剑"]


@pytest.mark.parametrize(
    "failure_stage",
    [
        "after_transaction",
        "after_decisions",
        "after_commit",
        "after_manifest",
        "before_head_swap",
    ],
)
def test_publish_failure_before_pointer_swap_exposes_complete_old_head_only(
    tmp_path,
    failure_stage,
):
    repo = CanonRepository(tmp_path / failure_stage)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )

    def fail_at(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match=failure_stage):
        repo._seal_objects(
            chapter=2,
            transaction=_transaction(2, "钥匙"),
            expected_head=first.head_hash,
            fault_injector=fail_at,
        )

    assert repo.current_head() == first.head_hash
    assert [effect["value"] for effect in current_canon_effects(repo)] == ["刀"]
    assert [commit[1]["chapter"] for commit in repo.current_commits()] == [1]


def test_failure_after_pointer_swap_exposes_complete_new_head(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )

    def fail_after_swap(stage: str) -> None:
        if stage == "after_head_swap":
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match="after_head_swap"):
        repo._seal_objects(
            chapter=2,
            transaction=_transaction(2, "钥匙"),
            expected_head=first.head_hash,
            fault_injector=fail_after_swap,
        )

    new_head = repo.current_head()
    assert new_head is not None and new_head != first.head_hash
    assert [effect["value"] for effect in current_canon_effects(repo)] == ["刀", "钥匙"]
    assert [commit[1]["chapter"] for commit in repo.current_commits()] == [1, 2]


def test_orphan_commit_written_before_crash_never_becomes_history(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )
    commits_before = set((repo.root / "commits").glob("*.json"))

    def fail_after_commit(stage: str) -> None:
        if stage == "after_commit":
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError):
        repo._seal_objects(
            chapter=2,
            transaction=_transaction(2, "幽灵事实"),
            expected_head=first.head_hash,
            fault_injector=fail_after_commit,
        )

    commits_after = set((repo.root / "commits").glob("*.json"))
    assert len(commits_after - commits_before) == 1
    assert [effect["value"] for effect in current_canon_effects(repo)] == ["刀"]


def test_projection_binding_becomes_stale_after_head_changes(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    genesis_binding = repo.projection_binding()
    assert repo.projection_is_stale(genesis_binding) is False

    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )

    assert repo.projection_is_stale(genesis_binding) is True
    assert repo.projection_is_stale(first.projection_binding.as_dict()) is False
    with pytest.raises(ProjectionStaleError):
        repo.assert_projection_fresh(genesis_binding)


def test_current_validation_detects_mutated_reachable_object(tmp_path):
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, "刀"),
        expected_head=genesis,
    )
    commit_path = repo.object_path("commit", first.commit_hash)
    commit_path.write_bytes(b"{}")

    with pytest.raises(CanonIntegrityError, match="hash_mismatch"):
        repo.current_head()

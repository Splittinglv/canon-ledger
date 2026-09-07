from __future__ import annotations

import json

import pytest

from scripts.data_modules.canon_v3.projection import (
    CanonProjectionError,
    projection_is_fresh,
    projection_path,
    read_projection,
    rebuild_projection,
)
from scripts.data_modules.canon_v3.repository import (
    CanonIntegrityError,
    CanonRepository,
    ProjectionStaleError,
    content_hash,
)


def _effect(fact_key: str, value: str) -> dict:
    return {
        "effect_id": (value.encode("utf-8").hex() + "0" * 64)[:64],
        "candidate_digest": "c" * 64,
        "fact_key": fact_key,
        "claim": {
            "kind": "character_state_changed",
            "subject": "林舟",
            "attribute": "境界",
            "after": value,
        },
        "source_digests": ["s" * 64],
        "support_map": {"after": ["s" * 64]},
    }


def _transaction(chapter: int, effects: list[dict]) -> dict:
    return {
        "schema_version": "canon-v3/test-transaction/v1",
        "chapter": chapter,
        "canon_effects": effects,
    }


def _storage_genesis(repo: CanonRepository) -> str:
    return repo._initialize_objects(
        genesis_metadata={
            "schema_version": "canon-v3/storage-genesis/v1",
        }
    )


def test_projection_is_exactly_bound_to_current_head(tmp_path) -> None:
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    genesis_projection = rebuild_projection(tmp_path)
    assert genesis_projection["facts"] == []
    assert projection_is_fresh(tmp_path) is True

    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, [_effect("realm", "炼气")]),
        expected_head=genesis,
    )
    assert projection_is_fresh(tmp_path) is False
    with pytest.raises(ProjectionStaleError):
        read_projection(tmp_path)

    rebuilt = rebuild_projection(tmp_path)
    assert rebuilt["binding"]["head_hash"] == first.head_hash
    assert rebuilt["facts"][0]["claim"]["after"] == "炼气"
    assert projection_is_fresh(tmp_path) is True


def test_projection_content_tamper_is_detected_even_when_binding_is_unchanged(
    tmp_path,
) -> None:
    repo = CanonRepository(tmp_path)
    _storage_genesis(repo)
    payload = rebuild_projection(tmp_path)
    payload["facts"].append(
        {
            "fact_key": "forged",
            "claim": {"kind": "world_rule_revealed", "rule": "伪造规则"},
        }
    )
    projection_path(tmp_path).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    assert projection_is_fresh(tmp_path) is False
    with pytest.raises(CanonProjectionError, match="content_mismatch"):
        read_projection(tmp_path, require_fresh=True)


def test_full_rebuild_removes_truncated_suffix_and_old_revision(tmp_path) -> None:
    repo = CanonRepository(tmp_path)
    genesis = _storage_genesis(repo)
    first = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, [_effect("realm", "炼气")]),
        expected_head=genesis,
    )
    second = repo._seal_objects(
        chapter=2,
        transaction=_transaction(2, [_effect("item", "旧剑")]),
        expected_head=first.head_hash,
    )
    rebuild_projection(tmp_path)
    assert {row["fact_key"] for row in read_projection(tmp_path)["facts"]} == {
        "realm",
        "item",
    }

    replacement = repo._seal_objects(
        chapter=1,
        transaction=_transaction(1, [_effect("realm", "筑基")]),
        expected_head=second.head_hash,
    )
    assert projection_is_fresh(tmp_path) is False
    rebuilt = rebuild_projection(tmp_path)

    assert rebuilt["binding"]["head_hash"] == replacement.head_hash
    assert [(row["fact_key"], row["claim"]["after"]) for row in rebuilt["facts"]] == [
        ("realm", "筑基")
    ]
    assert len(rebuilt["history"]) == 1

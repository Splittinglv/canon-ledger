#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.commit_lineage import (  # noqa: E402
    VALIDATION_NEEDS_REVALIDATION,
    VALIDATION_VALID,
    canonical_snapshot_hash,
    predecessor_context_hash_from_commits,
    stamp_and_partition_commits,
)


def _commit(chapter: int, weapon: str, **meta) -> dict:
    payload = {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": chapter,
            "status": "accepted",
            **meta,
        },
        "extraction_result": {
            "state_deltas": [{"entity_id": "hero", "field": "weapon", "new": weapon}]
        },
    }
    return payload


def test_stamp_marks_later_commits_when_earlier_snapshot_changes():
    first = _commit(1, "刀")
    second = _commit(
        2,
        "无",
        predecessor_context_hash=predecessor_context_hash_from_commits([first], 2),
        validation_status=VALIDATION_VALID,
    )
    previous = {
        "1": canonical_snapshot_hash(first),
        "2": canonical_snapshot_hash(second),
    }
    revised = _commit(1, "钥匙")
    replayable, stamped, stale = stamp_and_partition_commits(
        [revised, second],
        previous_manifest=previous,
    )

    assert [item["meta"]["chapter"] for item in replayable] == [1]
    assert stale == [2]
    by_chapter = {item["meta"]["chapter"]: item for item in stamped}
    assert by_chapter[1]["meta"]["validation_status"] == VALIDATION_VALID
    assert by_chapter[2]["meta"]["validation_status"] == VALIDATION_NEEDS_REVALIDATION


def test_stamp_keeps_sequential_prefix_valid_without_replacement():
    first = _commit(1, "刀")
    second = _commit(
        2,
        "无",
        predecessor_context_hash=predecessor_context_hash_from_commits([first], 2),
    )
    previous = {
        "1": canonical_snapshot_hash(first),
        "2": canonical_snapshot_hash(second),
    }
    replayable, _stamped, stale = stamp_and_partition_commits(
        [first, second],
        previous_manifest=previous,
    )

    assert stale == []
    assert [item["meta"]["chapter"] for item in replayable] == [1, 2]

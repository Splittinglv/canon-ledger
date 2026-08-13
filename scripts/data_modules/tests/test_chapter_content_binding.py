#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib

import pytest

from data_modules.chapter_content_binding import (
    SCHEMA_VERSION,
    ChapterBindingError,
    build_chapter_binding,
    require_chapter_binding,
    verify_chapter_binding,
    verify_commit_content_binding,
)


def _write_chapter(project_root, chapter: int, raw: bytes = b"final chapter bytes"):
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_bytes(raw)
    return chapter_path


def test_build_chapter_binding_hashes_exact_raw_bytes(tmp_path):
    raw = "第一行\r\n第二行：正文\n".encode("utf-8")
    _write_chapter(tmp_path, 3, raw)

    binding = build_chapter_binding(tmp_path, 3)

    assert binding == {
        "schema_version": SCHEMA_VERSION,
        "chapter": 3,
        "path": "正文/第0003章.md",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    assert verify_chapter_binding(tmp_path, 3, binding) == (True, "ok")
    assert require_chapter_binding(tmp_path, 3, binding) == binding


@pytest.mark.parametrize(
    ("binding_patch", "expected_code"),
    [
        ({"chapter": 4}, "artifact_chapter_mismatch"),
        ({"path": "正文/第0003章-别稿.md"}, "artifact_path_mismatch"),
        ({"sha256": "0" * 64}, "chapter_content_hash_mismatch"),
        ({"bytes": 999}, "artifact_size_mismatch"),
    ],
)
def test_verify_chapter_binding_returns_stable_artifact_codes(
    tmp_path, binding_patch, expected_code
):
    _write_chapter(tmp_path, 3)
    binding = build_chapter_binding(tmp_path, 3)
    binding.update(binding_patch)

    assert verify_chapter_binding(tmp_path, 3, binding) == (False, expected_code)


def test_verify_detects_content_change_even_when_byte_length_is_unchanged(tmp_path):
    chapter_path = _write_chapter(tmp_path, 3, b"old content")
    binding = build_chapter_binding(tmp_path, 3)
    chapter_path.write_bytes(b"new content")

    assert verify_chapter_binding(tmp_path, 3, binding) == (
        False,
        "chapter_content_hash_mismatch",
    )
    with pytest.raises(ChapterBindingError) as exc_info:
        require_chapter_binding(tmp_path, 3, binding)
    assert exc_info.value.code == "chapter_content_hash_mismatch"


def test_build_and_verify_report_missing_empty_and_missing_binding(tmp_path):
    with pytest.raises(ChapterBindingError) as missing:
        build_chapter_binding(tmp_path, 3)
    assert missing.value.code == "chapter_file_missing"

    _write_chapter(tmp_path, 3, b"")
    with pytest.raises(ChapterBindingError) as empty:
        build_chapter_binding(tmp_path, 3)
    assert empty.value.code == "chapter_file_empty"

    _write_chapter(tmp_path, 3, b"now populated")
    assert verify_chapter_binding(tmp_path, 3, None) == (
        False,
        "chapter_binding_missing",
    )


def test_build_rejects_ambiguous_flat_and_volume_chapter_files(tmp_path):
    _write_chapter(tmp_path, 3, b"flat")
    volume = tmp_path / "正文" / "第1卷" / "第003章-别稿.md"
    volume.parent.mkdir(parents=True, exist_ok=True)
    volume.write_bytes(b"volume")

    with pytest.raises(ChapterBindingError) as exc_info:
        build_chapter_binding(tmp_path, 3)

    assert exc_info.value.code == "chapter_file_ambiguous"


def _commit_envelope(binding: dict, *, chapter: int = 3) -> dict:
    return {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": chapter,
            "status": "accepted",
        },
        "chapter_binding": binding,
        "provenance": {"chapter_binding": binding},
        "review_result": {"blocking_count": 0, "chapter_binding": binding},
        "fulfillment_result": {
            "planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": [],
            "chapter_binding": binding,
        },
        "disambiguation_result": {"pending": [], "chapter_binding": binding},
        "extraction_result": {
            "accepted_events": [], "state_deltas": [], "entity_deltas": [],
            "chapter_binding": binding,
        },
        "projection_status": {
            "state": "done", "index": "skipped", "summary": "skipped",
            "memory": "skipped", "vector": "skipped",
        },
    }


def test_verify_commit_content_binding_requires_complete_shared_envelope(tmp_path):
    _write_chapter(tmp_path, 3)
    binding = build_chapter_binding(tmp_path, 3)
    payload = _commit_envelope(binding)

    assert verify_commit_content_binding(tmp_path, 3, payload) == (True, "ok")

    payload["review_result"].pop("chapter_binding")
    assert verify_commit_content_binding(tmp_path, 3, payload) == (
        False,
        "commit_schema_invalid",
    )


def test_verify_commit_content_binding_checks_expected_chapter(tmp_path):
    _write_chapter(tmp_path, 3)
    binding = build_chapter_binding(tmp_path, 3)

    assert verify_commit_content_binding(
        tmp_path,
        4,
        _commit_envelope(binding),
    ) == (False, "commit_chapter_mismatch")

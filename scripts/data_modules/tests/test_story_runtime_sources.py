#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.story_runtime_sources import load_runtime_sources
from .review_test_helpers import inject_hard_evidence_quotes, standard_review


def _write_runtime_contracts(
    project_root: Path,
    chapter: int,
    *,
    planned_nodes: list[str] | None = None,
) -> None:
    story_root = project_root / ".story-system"
    (story_root / "chapters").mkdir(parents=True, exist_ok=True)
    (story_root / "volumes").mkdir(parents=True, exist_ok=True)
    (story_root / "reviews").mkdir(parents=True, exist_ok=True)

    (story_root / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {"contract_type": "MASTER_SETTING"},
                "route": {"primary_genre": "玄幻"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (story_root / "chapters" / f"chapter_{chapter:03d}.json").write_text(
        json.dumps(
            {
                "meta": {"contract_type": "CHAPTER_BRIEF", "chapter": chapter},
                "chapter_directive": {
                    "goal": f"完成第{chapter}章的事实推进",
                    "must_cover_nodes": list(planned_nodes or []),
                    "forbidden_zones": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (story_root / "volumes" / "volume_001.json").write_text(
        json.dumps(
            {"meta": {"contract_type": "VOLUME_BRIEF", "volume": 1}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (story_root / "reviews" / f"chapter_{chapter:03d}.review.json").write_text(
        json.dumps(
            {
                "meta": {"contract_type": "REVIEW_CONTRACT", "chapter": chapter},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _persist_trusted_commit(project_root: Path, chapter: int) -> Path:
    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(f"第{chapter}章最终正文\n", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)

    payload = ChapterCommitService(project_root).build_commit(
        chapter=chapter,
        review_result=standard_review(binding),
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        disambiguation_result={"pending": [], "chapter_binding": binding},
        extraction_result={
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "chapter_binding": binding,
        },
    )
    ChapterCommitService(project_root).persist_commit(payload)
    return chapter_path


def test_load_runtime_sources_prefers_latest_accepted_commit(tmp_path):
    _write_runtime_contracts(tmp_path, chapter=3)
    _persist_trusted_commit(tmp_path, chapter=3)

    snapshot = load_runtime_sources(tmp_path, chapter=3)

    assert snapshot.latest_accepted_commit["meta"]["status"] == "accepted"
    assert snapshot.primary_write_source == "chapter_commit"
    assert snapshot.fallback_sources == []


def test_load_runtime_sources_blocks_unsynchronized_setting_files(tmp_path):
    """设定集已有长期事实但 MASTER 未绑定快照时必须 fail closed。"""
    _write_runtime_contracts(tmp_path, chapter=1)
    settings_dir = tmp_path / "设定集"
    settings_dir.mkdir()
    (settings_dir / "世界观.md").write_text(
        "# 世界观\n\n## 城门规则\n- 通行条件：持黑铜令者只能从北门入城。\n",
        encoding="utf-8",
    )

    snapshot = load_runtime_sources(tmp_path, chapter=1)

    assert "missing_setting_canon" in snapshot.fallback_sources


def test_load_runtime_sources_excludes_accepted_commit_after_prose_changes(tmp_path):
    _write_runtime_contracts(tmp_path, chapter=3)
    chapter_path = _persist_trusted_commit(tmp_path, chapter=3)
    chapter_path.write_text("第3章修改后的正文\n", encoding="utf-8")

    snapshot = load_runtime_sources(tmp_path, chapter=3)

    assert snapshot.latest_commit["meta"]["status"] == "accepted"
    assert snapshot.latest_commit["trust"]["content_binding"] is False
    assert snapshot.latest_commit["trust"]["reason"] == "chapter_content_hash_mismatch"
    assert "extraction_result" not in snapshot.latest_commit
    assert snapshot.latest_accepted_commit is None
    assert "missing_accepted_commit" in snapshot.fallback_sources


def test_load_runtime_sources_exposes_rejected_status_without_rejected_facts(tmp_path):
    planned_node = "揭示未来秘密"
    _write_runtime_contracts(tmp_path, chapter=3, planned_nodes=[planned_node])
    chapter_path = tmp_path / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    extraction, chapter_text = inject_hard_evidence_quotes(
        {
            "accepted_events": [],
            "state_deltas": [
                {
                    "entity_id": "future_secret",
                    "field": "identity",
                    "new": "spoiler",
                }
            ],
            "entity_deltas": [],
        },
        chapter=3,
        chapter_text="第3章被拒正文\n",
    )
    chapter_path.write_text(chapter_text, encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 3)
    service = ChapterCommitService(tmp_path)
    rejected = service.build_commit(
        chapter=3,
        review_result=standard_review(binding),
        fulfillment_result={
            "planned_nodes": [planned_node],
            "covered_nodes": [],
            "missed_nodes": [planned_node],
            "extra_nodes": [],
            "enforcement": "strict",
            "chapter_binding": binding,
        },
        disambiguation_result={"pending": [], "chapter_binding": binding},
        extraction_result={
            **extraction,
            "chapter_binding": binding,
        },
    )
    service.persist_commit(rejected)

    snapshot = load_runtime_sources(tmp_path, chapter=3)

    assert chapter_path.is_file()
    assert snapshot.latest_commit["meta"]["status"] == "rejected"
    assert snapshot.latest_commit["trust"]["reason"] == "commit_status_rejected"
    assert "extraction_result" not in snapshot.latest_commit
    assert snapshot.latest_accepted_commit is None

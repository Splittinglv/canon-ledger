#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_modules.canon_v3.service import CanonV3Service
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.tests.canon_v3_protocol_helpers import proposal_authority
from data_modules.planning_facade import (
    PLANNING_REFRESH_SCHEMA,
    PlanningContractRefreshError,
    refresh_contracts,
)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _project(root: Path) -> Path:
    (root / ".canon-ledger").mkdir(parents=True)
    (root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "project_info": {"title": "规划合同测试"},
                "progress": {
                    "current_chapter": 0,
                    "volumes_planned": [
                        {"volume": 1, "chapters_range": "1-20"}
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    story = root / ".story-system"
    story.mkdir(parents=True)
    (story / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "MASTER_SETTING",
                },
                "route": {},
                "master_constraints": {},
                "base_context": [],
                "source_trace": [],
                "override_policy": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (story / "anti_patterns.json").write_text("[]", encoding="utf-8")
    outline = root / "大纲"
    outline.mkdir()
    (outline / "总纲.md").write_text(
        "\n".join(
            [
                "| 卷号 | 卷名 | 章节范围 | 卷内目标 | 预期结束状态 |",
                "|---|---|---|---|---|",
                "| 1 | 初潮 | 1-20 | 找到账簿 | 确认内鬼仍在城内 |",
            ]
        ),
        encoding="utf-8",
    )
    (outline / "第1章-账簿.md").write_text(
        "\n".join(
            [
                "### 第一章：账簿",
                "- 目标：林舟找到红铜账簿",
                "- 时间锚点：大历三年九月十七日",
                "- 必须覆盖节点：林舟进入账房；林舟看到封蜡",
                "- 本章禁区：不要在本章揭晓内鬼",
            ]
        ),
        encoding="utf-8",
    )
    CanonV3Service(root).initialize_new_project()
    return root


def test_refresh_contracts_dry_run_is_pure_and_binds_outline(tmp_path: Path) -> None:
    project = _project(tmp_path / "book")
    before = _tree(project)
    head = CanonV3Service(project).repository.current_head(validate=True)

    result = refresh_contracts(project, 1, dry_run=True)

    assert result["schema_version"] == PLANNING_REFRESH_SCHEMA
    assert result["mode"] == "dry_run"
    assert result["authority"] == "planning_only"
    assert result["head_before"] == result["head_after"] == head
    assert result["source_outline"]["digest"]
    assert result["source_outline"]["files"]
    assert result["applied"] is False
    assert _tree(project) == before


def test_refresh_contracts_writes_only_three_planning_json_files(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "book")
    before = _tree(project)
    head = CanonV3Service(project).repository.current_head(validate=True)

    result = refresh_contracts(project, 1)

    after = _tree(project)
    changed = {
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    }
    assert changed == {
        ".story-system/chapters/chapter_001.json",
        ".story-system/volumes/volume_001.json",
        ".story-system/reviews/chapter_001.review.json",
    }
    assert result["applied"] is True
    assert result["head_before"] == result["head_after"] == head
    assert CanonV3Service(project).repository.current_head(validate=True) == head
    assert before[".story-system/MASTER_SETTING.json"] == after[
        ".story-system/MASTER_SETTING.json"
    ]
    review = json.loads(
        after[".story-system/reviews/chapter_001.review.json"].decode("utf-8")
    )
    chapter = json.loads(
        after[".story-system/chapters/chapter_001.json"].decode("utf-8")
    )
    assert review["must_check"] == []
    assert review["blocking_rules"] == []
    assert review["review_thresholds"] == {"blocking_count": 0}
    assert chapter["chapter_directive"]["must_cover_nodes"]
    assert chapter["meta"]["chapter"] == 1
    batch_digest = result["planning_batch_digest"]
    assert batch_digest
    assert {
        chapter["meta"]["planning_batch_digest"],
        review["meta"]["planning_batch_digest"],
        json.loads(
            after[".story-system/volumes/volume_001.json"].decode("utf-8")
        )["meta"]["planning_batch_digest"],
    } == {batch_digest}


def test_refresh_contracts_fails_closed_without_written_chapter_outline(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "book")
    (project / "大纲" / "第1章-账簿.md").unlink()
    before = _tree(project)

    with pytest.raises(
        PlanningContractRefreshError,
        match="planning_chapter_outline_missing",
    ):
        refresh_contracts(project, 1)

    assert _tree(project) == before


def test_refresh_contracts_rejects_non_ready_workflow_without_writes(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "book")
    manuscript = project / "正文" / "第0001章.md"
    manuscript.parent.mkdir(parents=True)
    manuscript.write_text("本章没有新的长期事实。\n", encoding="utf-8")
    binding = build_chapter_binding(project, 1)
    service = CanonV3Service(project)
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
                    "attestation_id": "complete-planning-gate-test",
                    "scanner": "reviewer",
                    "scanner_version": "v3-test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority["author_axiom_digest"],
                    "entity_registry_digest": authority["entity_registry_digest"],
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
    before = _tree(project)

    with pytest.raises(
        PlanningContractRefreshError,
        match="planning_workflow_not_ready",
    ):
        refresh_contracts(project, 1)

    assert _tree(project) == before


def test_refresh_contracts_routes_long_novel_volume_from_outline_not_legacy_state(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "book")
    master_outline = project / "大纲" / "总纲.md"
    master_outline.write_text(
        "\n".join(
            [
                "| 卷号 | 卷名 | 章节范围 | 卷内目标 | 预期结束状态 |",
                "|---|---|---|---|---|",
                "| 1 | 初潮 | 1-20 | 找到账簿 | 确认内鬼仍在城内 |",
                "| 2 | 远行 | 51-100 | 进入北境 | 找到失踪使团 |",
            ]
        ),
        encoding="utf-8",
    )
    (project / "大纲" / "第51章-北门.md").write_text(
        "\n".join(
            [
                "### 第五十一章：北门",
                "- 目标：林舟进入北境",
                "- 必须覆盖节点：林舟通过北门",
            ]
        ),
        encoding="utf-8",
    )
    state_path = project / ".canon-ledger" / "state.json"
    assert "51-100" not in state_path.read_text(encoding="utf-8")

    result = refresh_contracts(project, 51, dry_run=True)

    assert result["volume"] == 2
    assert any(
        item["path"] == ".story-system/volumes/volume_002.json"
        for item in result["changes"]
    )
    assert all(
        item["path"] != ".canon-ledger/state.json"
        for item in result["source_inputs"]["files"]
    )


def test_refresh_contracts_rejects_duplicate_split_outlines(tmp_path: Path) -> None:
    project = _project(tmp_path / "book")
    (project / "大纲" / "第01章-重复账簿.md").write_text(
        "### 第一章：重复账簿\n- 目标：另一个互相冲突的目标\n",
        encoding="utf-8",
    )
    before = _tree(project)

    with pytest.raises(
        PlanningContractRefreshError,
        match="planning_chapter_outline_ambiguous",
    ):
        refresh_contracts(project, 1, dry_run=True)

    assert _tree(project) == before

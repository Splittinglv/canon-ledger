#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.canon_v3.service import CanonV3Service
from data_modules.config import DataModulesConfig
from data_modules.human_review import HumanReviewService
from data_modules.memory_contract_adapter import MemoryContractAdapter
from data_modules.project_status import build_project_status
from data_modules.story_runtime_health import build_story_runtime_health
from data_modules.story_runtime_sources import load_runtime_sources
from data_modules.user_report import build_user_report
from data_modules.workflow_authority import WorkflowAuthority
from data_modules.write_gates import STAGES, run_write_gate


def _recognized_book(root: Path, *, legacy_commit: bool = False) -> None:
    state_path = root / ".canon-ledger" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"project_info": {"title": "权威状态测试"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    if legacy_commit:
        commit_path = root / ".story-system" / "commits" / "chapter_001.commit.json"
        commit_path.parent.mkdir(parents=True, exist_ok=True)
        commit_path.write_text("{}", encoding="utf-8")


@pytest.mark.parametrize(
    ("legacy_commit", "bootstrap_mode", "action"),
    [
        (False, "new_project", "initialize_v3"),
        (True, "legacy_cutover", "migrate_legacy"),
    ],
)
def test_no_current_fails_closed_across_all_public_surfaces(
    tmp_path: Path,
    legacy_commit: bool,
    bootstrap_mode: str,
    action: str,
) -> None:
    _recognized_book(tmp_path, legacy_commit=legacy_commit)

    workflow = WorkflowAuthority(tmp_path).snapshot()
    assert workflow == CanonV3Service(tmp_path).workflow_snapshot()
    assert workflow["state"] == "migration_required"
    assert workflow["bootstrap_mode"] == bootstrap_mode
    assert workflow["can_write_next"] is False
    assert workflow["primary_action"]["code"] == action

    for stage in STAGES:
        gate = run_write_gate(tmp_path, chapter=1, stage=stage)
        assert gate["ok"] is False
        assert gate["details"]["workflow_snapshot"]["workflow_digest"] == workflow[
            "workflow_digest"
        ]

    status = build_project_status(tmp_path, chapter=1)
    assert status["phase"] == "canon_v3:migration_required"
    assert status["blocking"] == ["migration_required"]
    assert status["primary_action"]["code"] == action
    assert status["evidence"]["workflow_snapshot"]["workflow_digest"] == workflow[
        "workflow_digest"
    ]

    report = build_user_report(tmp_path, stage="write", chapter=1)
    assert report["overall_status"] == "needs_user"
    assert report["primary_action"]["code"] == action
    assert report["workflow_snapshot"]["workflow_digest"] == workflow[
        "workflow_digest"
    ]

    runtime = load_runtime_sources(tmp_path, 1)
    assert runtime.primary_write_source == "canon_v3_head"
    assert runtime.latest_commit is None
    assert runtime.latest_accepted_commit is None
    assert "canon_v3_workflow_migration_required" in runtime.fallback_sources

    health = build_story_runtime_health(tmp_path, chapter=1)
    assert health["mainline_ready"] is False
    assert health["workflow_digest"] == workflow["workflow_digest"]
    assert health["primary_action"]["code"] == action


def test_uninitialized_context_is_fact_empty_and_legacy_writers_are_disabled(
    tmp_path: Path,
) -> None:
    _recognized_book(tmp_path)

    context = MemoryContractAdapter(
        DataModulesConfig.from_project_root(tmp_path)
    ).load_context(1)
    assert context.sections["canonical_facts"] == []
    assert context.sections["hard_constraints"] == []
    assert context.sections["runtime_status"]["primary_write_source"] == "canon_v3_head"
    assert any(
        item.startswith("canon_v3_workflow_blocked:")
        for item in context.completeness["missing_sources"]
    )
    source_status = context.completeness["source_status"]
    assert source_status["scratchpad"]["status"] == "excluded_legacy"
    assert source_status["rag"]["status"] == "excluded_legacy"

    with pytest.raises(ValueError, match="legacy_fact_mutation_disabled"):
        ChapterCommitService(tmp_path).build_commit(  # guard runs first
            chapter=1,
            review_result={},
            fulfillment_result={},
            disambiguation_result={},
            extraction_result={},
        )
    with pytest.raises(ValueError, match="legacy_fact_mutation_disabled"):
        HumanReviewService(tmp_path).persist_queue(1, {}, [])


def test_contract_availability_is_advisory_once_canon_is_ready(tmp_path: Path) -> None:
    _recognized_book(tmp_path)
    CanonV3Service(tmp_path).initialize_new_project()

    runtime = load_runtime_sources(tmp_path, 1)
    assert runtime.fallback_sources == []
    assert "missing_master_contract" in runtime.advisory_sources
    assert "missing_chapter_contract" in runtime.advisory_sources

    health = build_story_runtime_health(tmp_path, chapter=1)
    assert health["mainline_ready"] is True
    assert health["fallback_sources"] == []
    assert "missing_master_contract" in health["advisory_sources"]

    status = build_project_status(tmp_path, chapter=1)
    assert status["blocking"] == []
    assert status["evidence"]["contract_phase_advisory"]["phase"] != status["phase"]


def test_query_surface_never_falls_back_to_legacy_fact_stores(tmp_path: Path) -> None:
    _recognized_book(tmp_path, legacy_commit=True)
    summary = tmp_path / ".canon-ledger" / "summaries" / "ch0001.md"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("旧摘要声称角色已经知道秘密。", encoding="utf-8")
    adapter = MemoryContractAdapter(DataModulesConfig.from_project_root(tmp_path))

    assert adapter.query_entity("旧角色", as_of_chapter=1) is None
    assert adapter.query_rules(as_of_chapter=1) == []
    assert adapter.get_open_loops(as_of_chapter=1) == []
    assert adapter.get_timeline(1, 10, as_of_chapter=1) == []
    assert adapter.read_summary(1) == ""
    with pytest.raises(ValueError, match="canon_v3_head_projection_unavailable"):
        adapter.export_asof_snapshot(chapter=1, as_of_chapter=0)

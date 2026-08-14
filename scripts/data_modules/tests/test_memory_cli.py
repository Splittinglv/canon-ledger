#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory_cli.py 测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import build_chapter_binding
from .review_test_helpers import standard_review, write_current_chapter_contract

_scripts_dir = str(Path(__file__).resolve().parent.parent.parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)


def _ensure_scripts_on_path():
    scripts_dir = Path(__file__).resolve().parent.parent.parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def _make_project(tmp_path: Path):
    canon_ledger_dir = tmp_path / ".canon-ledger"
    canon_ledger_dir.mkdir(parents=True, exist_ok=True)
    (canon_ledger_dir / "state.json").write_text("{}", encoding="utf-8")
    (canon_ledger_dir / "summaries").mkdir(exist_ok=True)
    return tmp_path


def _persist_current_entity(project: Path) -> None:
    chapter_path = project / "正文" / "第0001章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("萧炎在第一章正式登场。", encoding="utf-8")
    binding = build_chapter_binding(project, 1)
    write_current_chapter_contract(project, 1)
    payload = ChapterCommitService(project).build_commit(
        chapter=1,
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
            "entity_deltas": [
                {
                    "entity_id": "xiaoyan",
                    "canonical_name": "萧炎",
                    "entity_type": "角色",
                    "tier": "核心",
                }
            ],
            "chapter_binding": binding,
        },
    )
    ChapterCommitService(project).persist_commit(payload)


def test_load_context_cli(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "load-context", "--chapter", "1"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output["chapter"] == 1
    assert "sections" in output


def test_load_context_cli_passes_budget_tokens(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = [
        "memory_cli",
        "--project-root",
        str(project),
        "load-context",
        "--chapter",
        "1",
        "--budget-tokens",
        "7",
    ]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output["budget"]["requested_tokens"] == 7
    assert output["budget_used_tokens"] > 0
    assert "context_budget" not in output["sections"]
    assert output["schema_version"] == "canon-ledger-context-pack/v3"


def test_query_entity_not_found(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "query-entity", "--id", "nobody"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "not_found"


def test_query_entity_found(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    _persist_current_entity(project)

    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "query-entity", "--id", "xiaoyan"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output["name"] == "萧炎"


def test_query_rules_empty(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "query-rules"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output == []


def test_read_summary_missing(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "read-summary", "--chapter", "99"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output["chapter"] == 99
    assert output["summary"] == ""


def test_read_summary_exists(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    (project / ".canon-ledger" / "summaries" / "ch0005.md").write_text("第5章摘要", encoding="utf-8")

    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "read-summary", "--chapter", "5"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert "第5章摘要" in output["summary"]


def test_get_open_loops_empty(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "get-open-loops"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output == []


def test_get_obligations_empty(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "get-obligations"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output == []


def test_get_timeline_empty(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    old_argv = sys.argv
    sys.argv = ["memory_cli", "--project-root", str(project), "get-timeline", "--from", "1", "--to", "100"]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output == []


def test_export_asof_empty_project(tmp_path, capsys):
    _ensure_scripts_on_path()
    import memory_cli

    project = _make_project(tmp_path)
    out = project / ".canon-ledger" / "tmp" / "asof_snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    old_argv = sys.argv
    sys.argv = [
        "memory_cli",
        "--project-root",
        str(project),
        "export-asof",
        "--chapter",
        "1",
        "--out",
        str(out),
    ]
    try:
        memory_cli.main()
    finally:
        sys.argv = old_argv

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == "canon-ledger-asof-snapshot/v3"
    assert output["chapter"] == 1
    assert output["as_of_chapter"] == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["as_of_chapter"] == 0
    assert saved["obligations"] == []
    assert saved["coverage"] == {
        "knowledge": "none",
        "presence": "none",
        "custody": "none",
    }

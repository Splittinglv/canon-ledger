#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "canon-ledger-write-gate/v1"
STAGES = ("prewrite", "precommit", "postcommit")


def issue(
    code: str,
    *,
    message: str,
    severity: str = "blocker",
    path: str = "",
    impact: str = "",
    repair: str = "",
    details: Any = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "path": path,
        "impact": impact,
        "repair": repair,
        "details": details,
    }


def gate_report(
    *,
    stage: str,
    project_root: str | Path,
    chapter: int,
    phase: str,
    errors: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors = errors or []
    warnings = warnings or []
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "project_root": str(project_root),
        "chapter": chapter,
        "phase": phase,
        "ok": not any(item.get("severity") == "blocker" for item in errors),
        "errors": errors,
        "warnings": warnings,
        "details": details or {},
    }


def format_gate_report(report: dict[str, Any], output_format: str = "json") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    lines = [
        f"{status} write-gate {report.get('stage')}",
        f"project_root: {report.get('project_root')}",
        f"chapter: {report.get('chapter')}",
        f"phase: {report.get('phase')}",
    ]
    for item in report.get("errors") or []:
        lines.append(f"ERROR {item.get('code')}: {item.get('message')}")
        if item.get("path"):
            lines.append(f"  path: {item.get('path')}")
        if item.get("impact"):
            lines.append(f"  impact: {item.get('impact')}")
        if item.get("repair"):
            lines.append(f"  repair: {item.get('repair')}")
    for item in report.get("warnings") or []:
        lines.append(f"WARNING {item.get('code')}: {item.get('message')}")
    return "\n".join(lines)


def run_write_gate(project_root: str | Path, *, chapter: int, stage: str) -> dict[str, Any]:
    root = Path(project_root)
    # A missing CURRENT is a bootstrap/migration state, never permission to
    # fall back to the retired v2 gates.  Every public boundary consumes the
    # same authority snapshot from the moment a project root is selected.
    return _run_canon_v3_gate(root, chapter=chapter, stage=stage)


def _run_canon_v3_gate(
    project_root: Path,
    *,
    chapter: int,
    stage: str,
) -> dict[str, Any]:
    """Use the single v3 workflow snapshot for every natural boundary."""

    if stage not in STAGES:
        raise ValueError(f"unknown write-gate stage: {stage}")
    from ..canon_v3.repository import CanonV3Repository
    from ..workflow_authority import WorkflowAuthority

    workflow = WorkflowAuthority(project_root).snapshot()
    state = str(workflow.get("state") or "invalid")
    if stage == "prewrite":
        allowed_chapters = {
            int(item) for item in workflow.get("allowed_write_chapters") or []
        }
        allowed = (
            bool(workflow.get("can_write_next"))
            and state == "ready"
            and int(chapter) in allowed_chapters
        )
        repair = str(workflow.get("recovery_action") or "repair_canon_v3")
    elif stage == "precommit":
        allowed = (
            bool(workflow.get("can_finalize"))
            and state == "ready_to_finalize"
            and int(workflow.get("chapter") or 0) == int(chapter)
        )
        repair = str(workflow.get("recovery_action") or "complete_v3_review")
    else:
        repository = CanonV3Repository(project_root)
        commits = repository.current_commits() if workflow.get("head_hash") else []
        current_chapters = {
            int(commit.get("chapter") or 0) for _commit_hash, commit in commits
        }
        allowed = (
            state == "ready"
            and bool(workflow.get("can_write_next"))
            and int(chapter) in current_chapters
        )
        repair = str(workflow.get("recovery_action") or "rebuild_projection")
    errors: list[dict[str, Any]] = []
    if not allowed:
        expected = workflow.get("expected_next_chapter")
        sequence_note = (
            f"；请求章节 {chapter} 不在允许集合 "
            f"{workflow.get('allowed_write_chapters') or []}，下一新章应为 {expected}"
            if stage == "prewrite"
            else ""
        )
        errors.append(
            issue(
                "canon_v3_workflow_blocked",
                message=f"Canon v3 工作流状态 {state} 不允许 {stage}{sequence_note}",
                impact="继续会绕过待裁决事实、错误正文版本或尚未追平 HEAD 的投影。",
                repair=repair,
                details=workflow,
            )
        )
    return gate_report(
        stage=stage,
        project_root=project_root,
        chapter=chapter,
        phase=f"canon_v3:{state}",
        errors=errors,
        details={"workflow_snapshot": workflow},
    )

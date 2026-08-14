#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

try:
    from chapter_paths import find_chapter_file
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import find_chapter_file

from ..artifact_validator import validate_commit_artifact_files
from ..chapter_content_binding import verify_chapter_binding
from ..outline_fulfillment import (
    fulfillment_node_errors,
    load_authoritative_planned_nodes,
)
from ..project_phase import (
    COMMIT_ARTIFACT_FILES,
    PHASE_INIT_READY,
    PHASE_INIT_SCAFFOLDED,
    PHASE_NO_PROJECT,
    PHASE_PLAN_IN_PROGRESS,
    PHASE_PROJECTION_FAILED,
    resolve_project_phase,
)
from . import gate_report, issue


BLOCKED_PRECOMMIT_PHASES = {
    PHASE_NO_PROJECT,
    PHASE_INIT_SCAFFOLDED,
    PHASE_INIT_READY,
    PHASE_PLAN_IN_PROGRESS,
    PHASE_PROJECTION_FAILED,
}


def _artifact_paths(project_root: Path) -> dict[str, Path]:
    return {
        "review_result": project_root / COMMIT_ARTIFACT_FILES[0],
        "fulfillment_result": project_root / COMMIT_ARTIFACT_FILES[1],
        "disambiguation_result": project_root / COMMIT_ARTIFACT_FILES[2],
        "extraction_result": project_root / COMMIT_ARTIFACT_FILES[3],
    }


def _binding_issue_for_artifact(
    project_root: Path,
    chapter: int,
    artifact: str,
    path: Path,
) -> dict | None:
    """Return a blocker when an artifact is not bound to the current manuscript."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # The authoritative artifact validator reports these failures with more
        # specific repair instructions.
        return None
    if not isinstance(payload, dict):
        return None

    binding = payload.get("chapter_binding")
    if not isinstance(binding, dict):
        return issue(
            "artifact.chapter_binding_missing",
            message=f"{artifact} is not bound to chapter {chapter} manuscript",
            path=str(path),
            impact="无法证明审查或事实提取针对当前正文，继续提交可能固化旧稿事实。",
            repair="基于当前正文重新运行 reviewer/data-agent，生成带 chapter_binding 的 artifact。",
            details={"artifact": artifact, "binding_code": "chapter_binding_missing"},
        )

    ok, code = verify_chapter_binding(project_root, chapter, binding)
    if ok:
        return None
    return issue(
        "artifact.chapter_binding_invalid",
        message=f"{artifact} chapter binding is not current: {code}",
        path=str(path),
        impact="artifact 对应的正文与当前磁盘正文不一致，不能作为本次 commit 输入。",
        repair="重新审查当前正文并重新生成全部 data artifacts 后再提交。",
        details={"artifact": artifact, "binding_code": code},
    )


def run_precommit_gate(project_root: Path, chapter: int) -> dict:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    errors: list[dict] = []
    warnings: list[dict] = []

    if snapshot.phase in BLOCKED_PRECOMMIT_PHASES:
        errors.append(
            issue(
                "phase_not_ready_for_precommit",
                message=f"phase {snapshot.phase} is not ready for precommit",
                impact="项目骨架、规划合同或上一轮投影状态不完整，继续提交会固化不可靠事实。",
                repair="先运行 project-status/doctor，并按 next_action 修复当前阶段问题。",
                details=snapshot.to_dict(),
            )
        )

    chapter_file = find_chapter_file(project_root, chapter)
    if chapter_file is None:
        errors.append(
            issue(
                "chapter_file_missing",
                message=f"chapter {chapter} file missing",
                path=str(project_root / "正文"),
                impact="没有可提交的正文文件。",
                repair="先完成正文起草并保存到 正文/。",
            )
        )
    elif not chapter_file.read_text(encoding="utf-8").strip():
        errors.append(
            issue(
                "chapter_file_empty",
                message=f"chapter {chapter} file is empty",
                path=str(chapter_file),
                impact="空正文不能提交为章节事实。",
                repair="补齐正文内容后再提交。",
            )
        )

    paths = _artifact_paths(project_root)
    artifact_report = validate_commit_artifact_files(
        review_result=paths["review_result"],
        fulfillment_result=paths["fulfillment_result"],
        disambiguation_result=paths["disambiguation_result"],
        extraction_result=paths["extraction_result"],
    )
    for item in artifact_report.get("errors") or []:
        errors.append(
            issue(
                f"artifact.{item.get('type')}",
                message=str(item.get("message") or ""),
                path=str(item.get("path") or ""),
                impact=str(item.get("impact") or ""),
                repair=str(item.get("repair") or ""),
                details=item,
            )
        )
    for item in artifact_report.get("warnings") or []:
        warnings.append(
            issue(
                f"artifact.{item.get('type')}",
                message=str(item.get("message") or ""),
                severity="warning",
                path=str(item.get("path") or ""),
                details=item,
            )
        )

    if chapter_file is not None:
        for artifact, path in paths.items():
            binding_issue = _binding_issue_for_artifact(
                project_root,
                chapter,
                artifact,
                path,
            )
            if binding_issue is not None:
                errors.append(binding_issue)

    fulfillment_path = paths["fulfillment_result"]
    contract_error = ""
    try:
        fulfillment_payload = json.loads(
            fulfillment_path.read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # Missing/malformed artifacts are reported by the authoritative
        # artifact validator above.
        node_errors = []
    else:
        try:
            authoritative_nodes = load_authoritative_planned_nodes(
                project_root,
                chapter,
            )
        except ValueError as exc:
            authoritative_nodes = None
            contract_error = str(exc)
        node_errors = fulfillment_node_errors(
            fulfillment_payload,
            authoritative_nodes,
        )
    if contract_error:
        errors.append(
            issue(
                "chapter_contract.must_cover_nodes_invalid",
                message=f"chapter must-cover nodes are invalid: {contract_error}",
                path=str(
                    project_root
                    / ".story-system"
                    / "chapters"
                    / f"chapter_{chapter:03d}.json"
                ),
                impact="损坏的章合同可能把必达节点静默降为空列表。",
                repair="修复 chapter_directive.must_cover_nodes，使其为非空字符串数组后重跑 data-agent。",
                details={"validation_code": contract_error},
            )
        )
    for code in node_errors:
        errors.append(
            issue(
                f"artifact.{code}",
                message="fulfillment_result does not match chapter must-cover nodes",
                path=str(fulfillment_path),
                impact="章纲必达节点可能被空列表或不完整分类绕过。",
                repair="重新运行 data-agent，并将章合同 must_cover_nodes 原样复制到 planned_nodes；每项必须归入 covered_nodes 或 missed_nodes。",
                details={"validation_code": code},
            )
        )

    return gate_report(
        stage="precommit",
        project_root=project_root,
        chapter=chapter,
        phase=snapshot.phase,
        errors=errors,
        warnings=warnings,
        details={
            "phase": snapshot.to_dict(),
            "chapter_file": str(chapter_file) if chapter_file else "",
            "artifact_report": artifact_report,
        },
    )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .project_phase import (
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_CHAPTER_COMMITTED,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_INIT_READY,
    PHASE_INIT_SCAFFOLDED,
    PHASE_NO_PROJECT,
    PHASE_PLAN_IN_PROGRESS,
    PHASE_PROJECTION_FAILED,
    PHASE_READY_TO_COMMIT,
    ProjectPhaseSnapshot,
    resolve_project_phase,
)


SCHEMA_VERSION = "webnovel-project-status/v1"


def _project_title(project_root: Path) -> str:
    state_path = project_root / ".webnovel" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(state, dict):
        return ""
    project_info = state.get("project_info") if isinstance(state.get("project_info"), dict) else {}
    project = state.get("project") if isinstance(state.get("project"), dict) else {}
    raw = str(project_info.get("title") or project.get("title") or "")
    return " ".join(raw.replace("\x00", " ").split())[:120]


def next_action_for_phase(snapshot: ProjectPhaseSnapshot) -> str:
    phase = snapshot.phase
    target = snapshot.target_chapter
    if phase == PHASE_NO_PROJECT:
        return "运行 /webnovel-init，或用 webnovel.py use <项目根目录> 选择项目"
    if phase == PHASE_INIT_SCAFFOLDED:
        return "运行 webnovel.py doctor --format text，并补齐初始化文件"
    if phase == PHASE_INIT_READY:
        return "运行 /webnovel-plan，或刷新 Story System 合同"
    if phase == PHASE_PLAN_IN_PROGRESS:
        return f"完成规划并生成第 {target} 章运行时合同"
    if phase == PHASE_CHAPTER_CONTRACT_READY:
        return f"运行 /webnovel-write {target}"
    if phase == PHASE_DRAFT_IN_PROGRESS:
        return f"完成第 {target} 章的审查与数据产物"
    if phase == PHASE_READY_TO_COMMIT:
        return f"运行 webnovel.py chapter-commit --chapter {target}"
    if phase == PHASE_CHAPTER_COMMITTED:
        return f"继续写第 {snapshot.latest_accepted_chapter + 1} 章"
    if phase == PHASE_PROJECTION_FAILED:
        return "检查 projection_log / projection_status，并修复失败或待处理的投影"
    return "运行 webnovel.py doctor --format text"


def build_project_status(project_root: str | Path | None, chapter: int | None = None) -> dict[str, Any]:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    root = Path(snapshot.project_root) if snapshot.project_root else None
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": snapshot.project_root,
        "project": _project_title(root) if root else "",
        "latest_accepted_chapter": snapshot.latest_accepted_chapter,
        "target_chapter": snapshot.target_chapter,
        "phase": snapshot.phase,
        "blocking": list(snapshot.blocking),
        "warnings": list(snapshot.warnings),
        "next_action": next_action_for_phase(snapshot),
        "evidence": snapshot.to_dict(),
    }


def format_project_status(report: dict[str, Any], output_format: str = "summary") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)

    project = report.get("project") or "(未命名项目)"
    root = report.get("project_root") or "(未解析)"
    lines = [
        f"项目：{project}",
        f"根目录：{root}",
        f"阶段：{report.get('phase')}",
        f"最近已接受章节：{report.get('latest_accepted_chapter')}",
        f"目标章节：{report.get('target_chapter')}",
        f"下一步：{report.get('next_action')}",
    ]
    blocking = report.get("blocking") or []
    warnings = report.get("warnings") or []
    if blocking:
        lines.append("阻断项：")
        lines.extend(f"- {item}" for item in blocking)
    if warnings:
        lines.append("警告：")
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build short machine-readable webnovel project status")
    parser.add_argument("--project-root", default="", help="书项目根目录")
    parser.add_argument("--chapter", type=int, default=None, help="目标章节号")
    parser.add_argument("--format", choices=["summary", "json"], default="summary")
    args = parser.parse_args()

    report = build_project_status(args.project_root or None, chapter=args.chapter)
    print(format_project_status(report, args.format))
    raise SystemExit(0)


if __name__ == "__main__":
    main()

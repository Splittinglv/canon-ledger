#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..prewrite_validator import PrewriteValidator
from ..project_phase import (
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
    resolve_project_phase,
)
from ..story_runtime_sources import load_runtime_sources
from ..outline_fulfillment import (
    load_authoritative_chapter_goal,
    merged_planned_nodes,
)
from . import gate_report, issue


ALLOWED_PREWRITE_PHASES = {
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
}


def _plot_structure(chapter_contract: dict[str, Any], review_contract: dict[str, Any]) -> dict[str, Any]:
    directive = chapter_contract.get("chapter_directive") if isinstance(chapter_contract, dict) else {}
    if not isinstance(directive, dict):
        directive = {}
    planned_nodes = merged_planned_nodes(directive)
    if not planned_nodes:
        planned_nodes = list(
            review_contract.get("must_cover_nodes")
            or review_contract.get("mandatory_nodes")
            or []
        )
    return {
        "mandatory_nodes": planned_nodes,
        "prohibitions": list(
            directive.get("forbidden_zones")
            or directive.get("prohibitions")
            or review_contract.get("blocking_rules")
            or []
        ),
    }


def run_prewrite_gate(project_root: Path, chapter: int) -> dict[str, Any]:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if snapshot.phase not in ALLOWED_PREWRITE_PHASES:
        errors.append(
            issue(
                "phase_not_ready_for_prewrite",
                message=f"phase {snapshot.phase} is not ready for prewrite",
                impact="写前合同或项目骨架不完整，继续写作容易使用旧上下文或缺失约束。",
                repair="先运行 project-status/doctor，根据 next_action 补齐 init、plan 或 Story System 合同。",
                details=snapshot.to_dict(),
            )
        )

    runtime = load_runtime_sources(project_root, chapter)
    contracts = runtime.contracts
    story_contract = {
        "master_setting": contracts.get("master") or {},
        "volume_brief": contracts.get("volume") or {},
        "chapter_brief": contracts.get("chapter") or {},
        "review_contract": contracts.get("review") or {},
    }
    review_contract = contracts.get("review") or {}
    plot_structure = _plot_structure(contracts.get("chapter") or {}, review_contract)

    goal_error = ""
    try:
        authoritative_goal = load_authoritative_chapter_goal(
            project_root,
            chapter,
        )
    except ValueError as exc:
        authoritative_goal = None
        goal_error = str(exc)
    if goal_error:
        errors.append(
            issue(
                "chapter_contract.goal_invalid",
                message=f"章合同目标无效：{goal_error}",
                path=str(
                    project_root
                    / ".story-system"
                    / "chapters"
                    / f"chapter_{chapter:03d}.json"
                ),
                impact="章纲目标缺失或失真，正文可能脱离本章任务。",
                repair="补齐 chapter_directive.goal，并确保它与当前章纲目标一致后重新规划。",
                details={"validation_code": goal_error},
            )
        )

    validation = PrewriteValidator(project_root).build(
        chapter=chapter,
        review_contract=review_contract,
        plot_structure=plot_structure,
        story_contract=story_contract,
    )
    if validation.get("blocking"):
        errors.append(
            issue(
                "prewrite_validator_blocking",
                message="prewrite validator reported blocking issue(s)",
                impact="当前章节写作输入不可信。",
                repair="按 blocking_reasons 补齐合同、消歧 pending 或相关占位符。",
                details=validation,
            )
        )
    elif runtime.fallback_sources:
        warnings.append(
            issue(
                "story_runtime_fallback",
                message="story runtime has fallback sources",
                severity="warning",
                impact="写作上下文可能缺少上一章 accepted commit。",
                repair="确认这是第一章或补齐 accepted commit 后再写。",
                details=list(runtime.fallback_sources),
            )
        )

    return gate_report(
        stage="prewrite",
        project_root=project_root,
        chapter=chapter,
        phase=snapshot.phase,
        errors=errors,
        warnings=warnings,
        details={
            "phase": snapshot.to_dict(),
            "story_runtime": runtime.to_dict(),
            "prewrite_validation": validation,
            "authoritative_chapter_goal": authoritative_goal or "",
        },
    )

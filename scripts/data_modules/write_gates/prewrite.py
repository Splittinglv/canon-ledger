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
from ..commit_lineage import (
    prior_chapters_needing_revalidation,
    prior_chapters_with_stale_binding,
)
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


def _plot_structure(chapter_contract: dict[str, Any]) -> dict[str, Any]:
    directive = chapter_contract.get("chapter_directive") if isinstance(chapter_contract, dict) else {}
    if not isinstance(directive, dict):
        directive = {}
    planned_nodes = merged_planned_nodes(directive)
    forbidden = directive.get("forbidden_zones")
    return {
        "must_cover_nodes": planned_nodes,
        "forbidden_zones": list(forbidden) if isinstance(forbidden, list) else [],
    }


def _directive_list_error(chapter_contract: dict[str, Any], field: str) -> str:
    """返回当前章合同列表字段的校验码。"""
    directive = chapter_contract.get("chapter_directive") if isinstance(chapter_contract, dict) else None
    if not isinstance(directive, dict) or field not in directive:
        return f"chapter_contract_missing_{field}"
    values = directive.get(field)
    if not isinstance(values, list):
        return f"chapter_contract_{field}_must_be_list"
    if any(not isinstance(item, str) or not item.strip() for item in values):
        return f"chapter_contract_{field}_must_contain_nonempty_text"
    return ""


def _human_review_gate_issue(
    project_root: Path,
    chapter: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Block prose advancement until all earlier human decisions take effect."""
    from ..human_review import HumanReviewService

    try:
        summary = HumanReviewService(project_root).gate_summary(
            before_chapter=chapter
        )
    except ValueError as exc:
        return (
            issue(
                "human_review_state_invalid",
                message="人工确认账本或队列无法可靠读取",
                impact="系统无法证明前文章节的人工确认已经完成并生效。",
                repair="先运行 /canon-ledger-doctor 修复人工确认数据，再继续写作。",
                details={"error": str(exc)},
            ),
            {"error": str(exc)},
        )

    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    # 同一章内：确认需要改文 > 已裁决未重放 > 尚未裁决。
    priorities = {
        "rewrite_required": 0,
        "not_replayed": 1,
        "pending": 2,
    }
    for state, priority in priorities.items():
        for item in summary.get(state) or []:
            item_chapter = int(item.get("chapter") or 0)
            if item_chapter > 0:
                candidates.append((item_chapter, priority, state, item))
    if not candidates:
        return None, summary

    earliest_chapter, _priority, state, _item = min(candidates)
    state_rows = [
        row
        for row in (summary.get(state) or [])
        if int(row.get("chapter") or 0) == earliest_chapter
    ]
    if state == "rewrite_required":
        return (
            issue(
                "human_review_rewrite_required",
                message=(
                    f"第 {earliest_chapter} 章已由作者确认存在穿帮，"
                    f"不能直接写第 {chapter} 章"
                ),
                impact="继续写作会把作者已经判定有误的正文当作有效前史。",
                repair=(
                    f"先修改并重新审查第 {earliest_chapter} 章："
                    f" /canon-ledger-write {earliest_chapter}"
                ),
                details={"items": state_rows, "summary": summary},
            ),
            summary,
        )
    if state == "not_replayed":
        return (
            issue(
                "human_review_decisions_not_replayed",
                message=(
                    f"第 {earliest_chapter} 章的人工裁决已保存但尚未生效，"
                    f"不能直接写第 {chapter} 章"
                ),
                impact="当前正史提交还没有消费作者的最新裁决。",
                repair=(
                    f"先重放第 {earliest_chapter} 章裁决："
                    f" /canon-ledger-confirm {earliest_chapter}"
                ),
                details={"items": state_rows, "summary": summary},
            ),
            summary,
        )
    return (
        issue(
            "human_review_pending",
            message=(
                f"第 {earliest_chapter} 章仍有事实疑点等待作者确认，"
                f"不能直接写第 {chapter} 章"
            ),
            impact="未确认项不会进入正史，后续章节也不能据此可靠判断知识、在场或持有边界。",
            repair=(
                f"先完成第 {earliest_chapter} 章人工确认："
                f" /canon-ledger-confirm {earliest_chapter}"
            ),
            details={"items": state_rows, "summary": summary},
        ),
        summary,
    )


def run_prewrite_gate(project_root: Path, chapter: int) -> dict[str, Any]:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    human_review_summary: dict[str, Any] = {}

    if snapshot.phase not in ALLOWED_PREWRITE_PHASES:
        errors.append(
            issue(
                "phase_not_ready_for_prewrite",
                message=f"项目阶段 {snapshot.phase} 尚未达到写前校验条件",
                impact="写前合同或项目骨架不完整，继续写作容易使用旧上下文或缺失约束。",
                repair="先运行 project-status/doctor，根据 next_action 补齐 init、plan 或 Story System 合同。",
                details=snapshot.to_dict(),
            )
        )

    stale_prior = prior_chapters_needing_revalidation(project_root, chapter)
    if stale_prior:
        earliest = stale_prior[0]
        errors.append(
            issue(
                "prior_chapter_needs_revalidation",
                message=(
                    f"第 {earliest} 章及后续已提交事实因前文被改写而失效，"
                    f"不能直接写第 {chapter} 章"
                ),
                impact="后续章节仍按旧前文抽取，长期记忆会保留过期事实。",
                repair=(
                    f"先重新审查并提交第 {earliest} 章："
                    f" /canon-ledger-write {earliest}"
                ),
                details={"chapters": stale_prior},
            )
        )

    stale_bindings = prior_chapters_with_stale_binding(project_root, chapter)
    if stale_bindings:
        earliest_binding = stale_bindings[0]
        earliest_chapter = int(earliest_binding.get("chapter") or 0)
        errors.append(
            issue(
                "prior_chapter_binding_stale",
                message=(
                    f"第 {earliest_chapter} 章正文已改，但该章 commit 仍绑定旧稿纸，"
                    f"不能直接写第 {chapter} 章"
                ),
                impact="后续章节会按已失绑的旧抽取当正史，审查也会对着挖空历史进行。",
                repair=(
                    f"先重新审查并提交第 {earliest_chapter} 章："
                    f" /canon-ledger-write {earliest_chapter}"
                ),
                details={"chapters": stale_bindings},
            )
        )

    human_review_issue, human_review_summary = _human_review_gate_issue(
        project_root,
        chapter,
    )
    if human_review_issue is not None:
        errors.append(human_review_issue)

    from ..projection_rebuild import projection_coverage_gaps

    coverage_gaps = projection_coverage_gaps(project_root, before_chapter=chapter)
    if coverage_gaps:
        first_gap = coverage_gaps[0]
        errors.append(
            issue(
                "projection_coverage_gap",
                message=(
                    f"第 {first_gap['chapter']} 章的已接受提交尚未完整写入读模型"
                    f"（{first_gap['reason']}）"
                ),
                impact=(
                    "正史事实与读模型当前不一致，继续写作会用缺失前文事实的"
                    "旧读模型做审查与抽取。"
                ),
                repair=(
                    f"先补齐投影：canon_ledger.py projections retry "
                    f"--chapter {first_gap['chapter']}"
                ),
                details={"gaps": coverage_gaps},
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
    chapter_contract = contracts.get("chapter") or {}
    review_contract = contracts.get("review") or {}
    plot_structure = _plot_structure(chapter_contract)

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

    for field, label in (
        ("must_cover_nodes", "必达节点"),
        ("forbidden_zones", "禁区"),
    ):
        validation_code = _directive_list_error(chapter_contract, field)
        if not validation_code:
            continue
        errors.append(
            issue(
                f"chapter_contract.{field}_invalid",
                message=f"章合同{label}无效：{validation_code}",
                path=str(
                    project_root
                    / ".story-system"
                    / "chapters"
                    / f"chapter_{chapter:03d}.json"
                ),
                impact=f"章合同{label}可能在写作前被静默丢失。",
                repair=f"补齐 chapter_directive.{field}，使其为非空字符串数组。",
                details={"validation_code": validation_code},
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
                message="写前校验发现阻断问题",
                impact="当前章节写作输入不可信。",
                repair="按 blocking_reasons 补齐合同、处理显式阻断项或相关占位符。",
                details=validation,
            )
        )
    if (
        not human_review_summary.get("error")
        and not any((human_review_summary.get("counts") or {}).values())
        and int(
            (validation.get("disambiguation_domain") or {}).get("pending_count")
            or 0
        )
        > int(
            (validation.get("disambiguation_domain") or {}).get(
                "blocking_pending_count"
            )
            or 0
        )
    ):
        warnings.append(
            issue(
                "disambiguation_pending_advisory",
                message="存在可稍后人工确认的消歧项",
                severity="warning",
                impact="插件不会用这些未决项作否定推断或写入确定事实。",
                repair="需要时查看人工队列；不影响本章继续写作。",
                details=validation.get("disambiguation_domain") or {},
            )
        )
    if runtime.fallback_sources:
        warnings.append(
            issue(
                "story_runtime_fallback",
                message="故事运行时使用了备用事实源",
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
            "human_review": human_review_summary,
            "authoritative_chapter_goal": authoritative_goal or "",
        },
    )

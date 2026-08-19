#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 3 审查结果处理。

读取 reviewer agent 的原始输出 JSON，解析为 ReviewResult，
生成无评分的事实审查审计记录并写入 index.db。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from runtime_compat import enable_windows_utf8_stdio


def _ensure_scripts_path() -> None:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_path()

from data_modules.review_author_view import render_review_author_view
from data_modules.review_schema import ReviewResult, parse_review_output
from data_modules.chapter_content_binding import (
    chapter_bindings_equal,
    require_chapter_binding,
)


def _resolve_report_path(project_root: Path, report_file: str) -> Path:
    root = project_root.expanduser().resolve()
    report_path = Path(report_file).expanduser()
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path = report_path.resolve()
    try:
        report_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("report-file 必须位于 project_root 目录内") from exc
    return report_path


def _format_issue(issue: Dict[str, Any], index: int) -> List[str]:
    description = str(issue.get("description") or "未填写问题描述")
    severity = str(issue.get("severity") or "medium")
    category = str(issue.get("category") or "未填写分类")
    location = str(issue.get("location") or "未标注位置")
    evidence = str(issue.get("evidence") or "未提供证据")
    fix_hint = str(issue.get("fix_hint") or "未提供修复方向")
    blocking = "是" if issue.get("blocking") else "否"

    return [
        f"{index}. **{description}**",
        f"   - 严重级别：{severity}",
        f"   - 分类：{category}",
        f"   - 位置：{location}",
        f"   - 阻断：{blocking}",
        f"   - 证据：{evidence}",
        f"   - 修复方向：{fix_hint}",
    ]


def _format_manual_check(check: Dict[str, Any], index: int) -> List[str]:
    options = [str(item) for item in (check.get("options") or []) if str(item)]
    lines = [
        f"{index}. **{check.get('description') or '需要作者确认'}**",
        f"   - 分类：{check.get('category') or '未填写分类'}",
        f"   - 位置：{check.get('location') or '未标注位置'}",
        f"   - 现有证据：{check.get('evidence') or '证据不足'}",
        f"   - 转人工原因：{check.get('reason') or '无法可靠自动判断'}",
    ]
    if options:
        lines.append(f"   - 可选判断：{' / '.join(options)}")
    return lines


def render_review_report(payload: Dict[str, Any]) -> str:
    result = payload["review_result"]
    audit = payload["review_audit"]
    issues = list(result.get("issues", []))
    manual_checks = list(result.get("manual_checks", []))
    blocking_issues = [issue for issue in issues if issue.get("blocking")]
    non_blocking_issues = [issue for issue in issues if not issue.get("blocking")]
    severity_counts = audit.get("severity_counts", {})

    lines: List[str] = [
        f"# 第{payload['chapter']}章审查报告",
        "",
        render_review_author_view(payload).rstrip(),
        "",
        "## 总览",
        "",
        f"- 问题数：{result.get('issues_count', 0)}",
        f"- 阻断数：{result.get('blocking_count', 0)}",
        f"- 待人工确认：{len(manual_checks)}",
        f"- 审查模式：{result.get('review_mode', '')}",
        f"- 审查状态：{result.get('review_status', '')}",
        f"- 结论：{'需修复后重审' if result.get('has_blocking') else '无阻断问题'}",
    ]
    skipped_dimensions = result.get("skipped_dimensions") or []
    if skipped_dimensions:
        lines.append(f"- 未审维度：{', '.join(skipped_dimensions)}")
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(f"- 摘要：{summary}")
    if severity_counts:
        ordered = [
            f"{level}={severity_counts.get(level, 0)}"
            for level in ("critical", "high", "medium", "low")
        ]
        lines.append(f"- 严重级别统计：{', '.join(ordered)}")

    lines.extend(["", "## 阻断问题", ""])
    if blocking_issues:
        for index, issue in enumerate(blocking_issues, start=1):
            lines.extend(_format_issue(issue, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 其他问题", ""])
    if non_blocking_issues:
        for index, issue in enumerate(non_blocking_issues, start=1):
            lines.extend(_format_issue(issue, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 待作者确认", ""])
    if manual_checks:
        for index, check in enumerate(manual_checks, start=1):
            lines.extend(_format_manual_check(check, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 修复方向", ""])
    if issues:
        ordered_issues = [*blocking_issues, *non_blocking_issues]
        for index, issue in enumerate(ordered_issues, start=1):
            description = str(issue.get("description") or "未填写问题描述")
            fix_hint = str(issue.get("fix_hint") or "未提供修复方向")
            lines.append(f"{index}. {description}：{fix_hint}")
    else:
        lines.append("暂无需要修复的问题。")

    return "\n".join(lines).rstrip() + "\n"


def write_review_report(project_root: Path, report_file: str, payload: Dict[str, Any]) -> Path:
    report_path = _resolve_report_path(project_root, report_file)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_review_report(payload), encoding="utf-8")
    return report_path


def _build_review_audit_record(audit: Dict[str, Any]):
    from data_modules.index_manager import ReviewAudit

    return ReviewAudit(
        chapter=int(audit["chapter"]),
        review_mode=str(audit["review_mode"]),
        review_status=str(audit["review_status"]),
        review_degraded=bool(audit.get("review_degraded")),
        reviewed_dimensions=list(audit.get("reviewed_dimensions", [])),
        skipped_dimensions=list(audit.get("skipped_dimensions", [])),
        dimension_results=list(audit.get("dimension_results", [])),
        severity_counts=dict(audit.get("severity_counts", {})),
        critical_issues=list(audit.get("critical_issues", [])),
        issues_count=int(audit.get("issues_count", 0)),
        blocking_count=int(audit.get("blocking_count", 0)),
        report_file=str(audit.get("report_file", "")),
        notes=str(audit.get("notes", "")),
    )


def build_review_artifacts(
    project_root: Path,
    chapter: int,
    review_results_path: Path,
    report_file: str = "",
    chapter_binding_path: Path | None = None,
) -> Dict[str, Any]:
    if chapter_binding_path is None:
        raise ValueError("必须提供 --chapter-binding")
    expected_binding = json.loads(chapter_binding_path.read_text(encoding="utf-8"))
    expected_binding = require_chapter_binding(
        project_root,
        chapter,
        expected_binding,
    )
    raw = json.loads(review_results_path.read_text(encoding="utf-8"))
    result = parse_review_output(
        chapter=chapter,
        raw=raw,
        expected_binding=expected_binding,
    )
    if not chapter_bindings_equal(result.chapter_binding, expected_binding):
        raise ValueError("review_result.chapter_binding 与预期正文不一致")
    # 在写入报告或审计记录前立即重新校验正文摘要。
    require_chapter_binding(project_root, chapter, expected_binding)
    from data_modules.commit_lineage import prior_chapters_with_stale_binding

    stale_bindings = prior_chapters_with_stale_binding(project_root, chapter)
    if stale_bindings:
        earliest = int(stale_bindings[0].get("chapter") or 0)
        raise ValueError(
            "prior_chapter_binding_stale:"
            f"第 {earliest} 章正文已改但 commit 仍绑定旧稿纸；"
            f"先 /canon-ledger-write {earliest} 重提后再审查第 {chapter} 章"
        )
    from data_modules.human_review import (
        HumanReviewService,
        review_manual_check_items_from_review,
    )

    review_items = review_manual_check_items_from_review(result)
    service = HumanReviewService(project_root)
    queue_path = service.queue_path(chapter)
    existing: list[dict[str, Any]] = []
    if queue_path.is_file():
        try:
            queued = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            queued = {}
        queued_binding = (
            queued.get("chapter_binding")
            if isinstance(queued, dict)
            else None
        )
        if chapter_bindings_equal(queued_binding, expected_binding):
            existing = [
                item
                for item in (queued.get("items") or [])
                if isinstance(item, dict)
                and item.get("source") != "review_manual_check"
            ]
    if review_items or existing or queue_path.is_file():
        service.persist_queue(chapter, expected_binding, existing + review_items)
    review_audit = result.to_audit_dict(report_file=report_file)
    normalized_review = result.to_dict()
    review_results_path.parent.mkdir(parents=True, exist_ok=True)
    review_results_path.write_text(
        json.dumps(normalized_review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "chapter": chapter,
        "review_result": normalized_review,
        "review_audit": review_audit,
    }


def build_minimal_review_artifact(
    project_root: Path,
    chapter: int,
    review_results_path: Path,
    chapter_binding_path: Path,
) -> Dict[str, Any]:
    """为明确选择 minimal 的本章生成可验证的跳过审查凭据。"""
    binding = json.loads(chapter_binding_path.read_text(encoding="utf-8"))
    binding = require_chapter_binding(project_root, chapter, binding)
    result = ReviewResult(
        chapter=chapter,
        review_mode="minimal",
        chapter_binding=binding,
        issues=[],
        dimension_results=[],
        summary="用户选择 minimal 模式，本轮明确跳过事实审查。",
    )
    normalized = result.to_dict()
    review_results_path.parent.mkdir(parents=True, exist_ok=True)
    review_results_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "chapter": chapter,
        "review_result": normalized,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="审查流水线 v6")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--review-results", required=True)
    parser.add_argument("--chapter-binding", required=True)
    parser.add_argument("--audit-out", default="")
    parser.add_argument("--metrics-out", default="", help=argparse.SUPPRESS)
    parser.add_argument("--report-file", default="")
    parser.add_argument("--minimal", action="store_true",
                        help="生成显式跳过审查的 minimal artifact")
    parser.add_argument("--save-audit", action="store_true",
                        help="把事实审查覆盖范围与问题计数写入 index.db")
    parser.add_argument("--save-metrics", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()
    project_root = Path(args.project_root)
    review_results_path = Path(args.review_results)

    if args.minimal:
        if args.audit_out or args.metrics_out or args.report_file or args.save_audit or args.save_metrics:
            parser.error("--minimal 只生成跳过审查凭据，不能同时生成报告或审计记录")
        payload = build_minimal_review_artifact(
            project_root=project_root,
            chapter=args.chapter,
            review_results_path=review_results_path,
            chapter_binding_path=Path(args.chapter_binding),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    payload = build_review_artifacts(
        project_root=project_root,
        chapter=args.chapter,
        review_results_path=review_results_path,
        report_file=args.report_file,
        chapter_binding_path=Path(args.chapter_binding),
    )

    # 即使流水线运行期间正文被编辑，报告和数据库指标也必须对应同一份已审正文。
    require_chapter_binding(
        project_root,
        args.chapter,
        payload["review_result"].get("chapter_binding"),
    )

    audit_out = args.audit_out or args.metrics_out
    if audit_out:
        out_path = Path(audit_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload["review_audit"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.report_file:
        write_review_report(
            project_root=project_root,
            report_file=args.report_file,
            payload=payload,
        )

    if args.save_audit or args.save_metrics:
        from data_modules.config import DataModulesConfig
        from data_modules.index_manager import IndexManager
        config = DataModulesConfig.from_project_root(project_root)
        manager = IndexManager(config)
        manager.save_review_audit(_build_review_audit_record(payload["review_audit"]))

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()

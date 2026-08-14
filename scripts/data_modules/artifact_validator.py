#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .chapter_commit_schema import (
    ChapterCommitSchema,
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
)


SCHEMA_VERSION = "canon-ledger-artifact-validator/v1"

ERROR_SCHEMA = "schema_error"
ERROR_MISSING = "missing_artifact"
ERROR_BLOCKING_REVIEW = "blocking_review"
ERROR_MISSED_OUTLINE_NODE = "missed_outline_node"
ERROR_PENDING_DISAMBIGUATION = "pending_disambiguation"
ERROR_PROJECTION_FAILURE = "projection_failure"
ERROR_PROJECTION_INCOMPLETE = "projection_incomplete"

REQUIRED_PROJECTION_WRITERS = ("state", "index", "summary", "memory", "vector")
OK_PROJECTION_STATUSES = {"done", "skipped"}

ARTIFACT_SCHEMAS = {
    "review_result": ReviewResult,
    "fulfillment_result": FulfillmentResult,
    "disambiguation_result": DisambiguationResult,
    "extraction_result": ExtractionResult,
}


def _issue(
    issue_type: str,
    *,
    message: str,
    severity: str = "blocker",
    path: str = "",
    field: str = "",
    impact: str = "",
    repair: str = "",
) -> dict[str, str]:
    return {
        "type": issue_type,
        "severity": severity,
        "message": message,
        "path": path,
        "field": field,
        "impact": impact,
        "repair": repair,
    }


def _empty_report(artifact: str, path: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": artifact,
        "path": path,
        "ok": True,
        "errors": [],
        "warnings": [],
        "payload": None,
    }


def _read_json_artifact(path: str | Path) -> tuple[Any, dict[str, Any] | None]:
    artifact_path = Path(path)
    if not artifact_path.is_file():
        return None, _issue(
            ERROR_MISSING,
            message=f"缺少产物：{artifact_path}",
            path=str(artifact_path),
            impact="提交前 artifact 不完整，无法可靠生成 chapter commit。",
            repair="重新运行 reviewer/data-agent，或按 schema 补齐该 JSON 文件。",
        )
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, _issue(
            ERROR_SCHEMA,
            message=f"JSON 无效：{exc}",
            path=str(artifact_path),
            impact="artifact 无法被 runtime 读取。",
            repair="修复 JSON 格式，确保文件为 UTF-8。",
        )
    except OSError as exc:
        return None, _issue(
            ERROR_SCHEMA,
            message=f"读取产物失败：{exc}",
            path=str(artifact_path),
            impact="artifact 无法被 runtime 读取。",
            repair="检查文件权限和路径是否正确。",
        )


def _schema_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(str(error.get("msg") or "") for error in exc.errors()) or str(exc)
    return str(exc)


def _policy_issues(artifact: str, payload: dict[str, Any], path: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if artifact == "review_result":
        blocking_count = int(payload.get("blocking_count") or 0)
        if blocking_count > 0:
            issues.append(
                _issue(
                    ERROR_BLOCKING_REVIEW,
                    message=f"review_result 含 {blocking_count} 个阻断问题",
                    path=path,
                    field="blocking_count",
                    impact="存在阻断级审查问题时不应进入提交。",
                    repair="先定点修复 blocking issue，或让用户明确裁决后再继续。",
                )
            )
    elif artifact == "fulfillment_result":
        missed = payload.get("missed_nodes") or []
        if missed:
            strict = str(payload.get("enforcement") or "advisory") == "strict"
            issues.append(
                _issue(
                    ERROR_MISSED_OUTLINE_NODE,
                    message=f"fulfillment_result 遗漏 {len(missed)} 个计划节点",
                    severity="blocker" if strict else "warning",
                    path=path,
                    field="missed_nodes",
                    impact="本章与写作计划存在偏差，但这不是事实穿帮结论。",
                    repair="由作者决定是否补写；只有显式 strict 模式才阻断。",
                )
            )
    elif artifact == "disambiguation_result":
        pending = payload.get("pending") or []
        if pending:
            blocking = [
                item
                for item in pending
                if isinstance(item, dict) and bool(item.get("blocking", False))
            ]
            issues.append(
                _issue(
                    ERROR_PENDING_DISAMBIGUATION,
                    message=f"disambiguation_result 含 {len(pending)} 个待消歧项",
                    severity="blocker" if blocking else "warning",
                    path=path,
                    field="pending",
                    impact="候选事实会留在人工队列，确认前不会进入权威 canon。",
                    repair="运行 human-review 查看并裁决；普通待确认项不阻断提交。",
                )
            )
    return issues


def validate_artifact_payload(artifact: str, payload: Any, *, path: str = "") -> dict[str, Any]:
    if artifact not in ARTIFACT_SCHEMAS:
        raise ValueError(f"未知产物：{artifact}")

    report = _empty_report(artifact, path)
    schema = ARTIFACT_SCHEMAS[artifact]
    try:
        model = schema.model_validate(payload)
    except Exception as exc:
        report["errors"].append(
            _issue(
                ERROR_SCHEMA,
                message=_schema_error_message(exc),
                path=path,
                impact="artifact 字段形状不符合 chapter commit 权威 schema。",
                repair="按 chapter_commit_schema.py 的顶层字段要求修正，不要包 fulfillment/disambiguation/extraction 外层。",
            )
        )
        report["ok"] = False
        return report

    normalized = model.model_dump()
    report["payload"] = normalized
    for issue in _policy_issues(artifact, normalized, path):
        if issue.get("severity") == "blocker":
            report["errors"].append(issue)
        else:
            report["warnings"].append(issue)
    report["ok"] = not report["errors"]
    return report


def validate_artifact_file(artifact: str, path: str | Path) -> dict[str, Any]:
    report = _empty_report(artifact, str(path))
    payload, error = _read_json_artifact(path)
    if error:
        report["errors"].append(error)
        report["ok"] = False
        return report
    return validate_artifact_payload(artifact, payload, path=str(path))


def validate_review_result(path: str | Path) -> dict[str, Any]:
    return validate_artifact_file("review_result", path)


def validate_fulfillment_result(path: str | Path) -> dict[str, Any]:
    return validate_artifact_file("fulfillment_result", path)


def validate_disambiguation_result(path: str | Path) -> dict[str, Any]:
    return validate_artifact_file("disambiguation_result", path)


def validate_extraction_result(path: str | Path) -> dict[str, Any]:
    return validate_artifact_file("extraction_result", path)


def merge_reports(reports: list[dict[str, Any]], *, artifact: str = "chapter_commit_inputs") -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    payloads: dict[str, Any] = {}
    for report in reports:
        errors.extend(report.get("errors") or [])
        warnings.extend(report.get("warnings") or [])
        if report.get("payload") is not None:
            payloads[str(report.get("artifact"))] = report.get("payload")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": artifact,
        "ok": not any(item.get("severity") == "blocker" for item in errors),
        "errors": errors,
        "warnings": warnings,
        "payloads": payloads,
        "reports": reports,
    }


def validate_commit_artifact_files(
    *,
    review_result: str | Path,
    fulfillment_result: str | Path,
    disambiguation_result: str | Path,
    extraction_result: str | Path,
) -> dict[str, Any]:
    return merge_reports(
        [
            validate_review_result(review_result),
            validate_fulfillment_result(fulfillment_result),
            validate_disambiguation_result(disambiguation_result),
            validate_extraction_result(extraction_result),
        ]
    )


def validate_chapter_commit(
    path: str | Path,
    *,
    expected_chapter: int | None = None,
) -> dict[str, Any]:
    commit_path = Path(path)
    report = _empty_report("chapter_commit", str(commit_path))
    payload, error = _read_json_artifact(commit_path)
    if error:
        report["errors"].append(error)
        report["ok"] = False
        return report
    if not isinstance(payload, dict):
        report["errors"].append(
            _issue(
                ERROR_SCHEMA,
                message="chapter_commit 必须是 JSON 对象",
                path=str(commit_path),
                impact="commit 文件无法作为事实主链读取。",
                repair="从备份恢复 commit，或重新执行 chapter-commit。",
            )
        )
        report["ok"] = False
        return report

    try:
        ChapterCommitSchema.model_validate(payload)
    except Exception as exc:
        report["errors"].append(
            _issue(
                ERROR_SCHEMA,
                message=f"chapter commit 信封无效：{_schema_error_message(exc)}",
                path=str(commit_path),
                impact="commit 顶层、provenance 与四份 artifact 必须绑定同一份正文。",
                repair="基于当前正文重新运行 review、data 和 chapter-commit。",
            )
        )

    if expected_chapter is not None:
        meta = payload.get("meta")
        try:
            declared_chapter = int(
                meta.get("chapter") if isinstance(meta, dict) else 0
            )
            expected = int(expected_chapter)
        except (TypeError, ValueError):
            declared_chapter = 0
            expected = int(expected_chapter)
        if declared_chapter != expected:
            report["errors"].append(
                _issue(
                    ERROR_SCHEMA,
                    message=(
                        f"chapter_commit 章节 {declared_chapter or '缺失'} "
                        f"与预期章节 {expected} 不一致"
                    ),
                    path=str(commit_path),
                    field="meta.chapter",
                    impact="不能把其他章节的 commit 当作本章可信事实。",
                    repair="使用本章正文重新生成 chapter commit。",
                )
            )

    nested_reports = []
    for artifact in ARTIFACT_SCHEMAS:
        if artifact not in payload:
            report["errors"].append(
                _issue(
                    ERROR_SCHEMA,
                    message=f"chapter_commit 缺少 {artifact}",
                    path=str(commit_path),
                    field=artifact,
                    impact="commit 文件缺少提交 artifact 快照。",
                    repair="重新执行 chapter-commit 生成完整 commit。",
                )
            )
            continue
        nested_reports.append(validate_artifact_payload(artifact, payload.get(artifact), path=str(commit_path)))

    projection_status = payload.get("projection_status") or {}
    if not isinstance(projection_status, dict):
        projection_status = {}
    for writer in REQUIRED_PROJECTION_WRITERS:
        status = str(projection_status.get(writer) or "").strip()
        if not status:
            report["errors"].append(
                _issue(
                    ERROR_PROJECTION_INCOMPLETE,
                    message=f"投影 {writer} 缺少状态",
                    path=str(commit_path),
                    field=f"projection_status.{writer}",
                    impact="postcommit 必须确认 state/index/summary/memory/vector 五项投影状态。",
                    repair="重新执行 chapter-commit 或补跑 projections retry/replay。",
                )
            )
        elif status.startswith("failed:"):
            report["errors"].append(
                _issue(
                    ERROR_PROJECTION_FAILURE,
                    message=f"投影 {writer} 失败：{status}",
                    path=str(commit_path),
                    field=f"projection_status.{writer}",
                    impact="提交事实已生成，但 read-model 投影不完整。",
                    repair="修复失败原因后补跑 projection retry/replay。",
                )
            )
        elif status not in OK_PROJECTION_STATUSES:
            report["errors"].append(
                _issue(
                    ERROR_PROJECTION_INCOMPLETE,
                    message=f"投影 {writer} 的状态为 {status}",
                    path=str(commit_path),
                    field=f"projection_status.{writer}",
                    impact="postcommit 只接受 projection 状态 done 或 skipped。",
                    repair="等待投影完成或补跑 projections retry/replay。",
                )
            )

    merged = merge_reports(nested_reports, artifact="chapter_commit_nested")
    report["errors"].extend(merged["errors"])
    report["warnings"].extend(merged["warnings"])
    report["payload"] = payload
    report["ok"] = not any(item.get("severity") == "blocker" for item in report["errors"])
    return report

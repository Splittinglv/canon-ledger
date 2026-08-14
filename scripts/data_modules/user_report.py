#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - direct script entry
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

try:
    from chapter_paths import find_chapter_file
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import find_chapter_file

if __package__ in {None, ""}:  # pragma: no cover - direct script entry
    from data_modules.artifact_validator import (
        OK_PROJECTION_STATUSES,
        REQUIRED_PROJECTION_WRITERS,
        validate_artifact_payload,
        validate_disambiguation_result,
        validate_extraction_result,
        validate_fulfillment_result,
        validate_review_result,
    )
    from data_modules.error_catalog import AuthorError, classify_issue
    from data_modules.chapter_content_binding import verify_commit_content_binding
    from data_modules.chapter_content_binding import verify_chapter_binding
    from data_modules.project_phase import (
        COMMIT_ARTIFACT_FILES,
        INIT_REQUIRED_DIRS,
        INIT_REQUIRED_FILES,
        contract_files_for_chapter,
    )
    from data_modules.project_status import build_project_status
    from data_modules.projection_log import (
        commit_hash,
        projection_run_failed,
        projection_run_pending,
        projection_status_from_run,
        read_projection_runs,
    )
    from data_modules.review_author_view import build_review_author_view
    from data_modules.run_ledger import backup_receipt_trusted
    from data_modules.commit_lineage import list_needs_revalidation
else:
    from .artifact_validator import (
        OK_PROJECTION_STATUSES,
        REQUIRED_PROJECTION_WRITERS,
        validate_artifact_payload,
        validate_disambiguation_result,
        validate_extraction_result,
        validate_fulfillment_result,
        validate_review_result,
    )
    from .error_catalog import AuthorError, classify_issue
    from .chapter_content_binding import verify_commit_content_binding
    from .chapter_content_binding import verify_chapter_binding
    from .project_phase import (
        COMMIT_ARTIFACT_FILES,
        INIT_REQUIRED_DIRS,
        INIT_REQUIRED_FILES,
        contract_files_for_chapter,
    )
    from .project_status import build_project_status
    from .projection_log import (
        commit_hash,
        projection_run_failed,
        projection_run_pending,
        projection_status_from_run,
        read_projection_runs,
    )
    from .review_author_view import build_review_author_view
    from .run_ledger import backup_receipt_trusted
    from .commit_lineage import list_needs_revalidation


SCHEMA_VERSION = "canon-ledger-user-report/v1"
VALID_STAGES = ("init", "plan", "write", "review")
VALID_FORMATS = ("text", "json")

STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_NEEDS_USER = "needs_user"
STATUS_FAILED = "failed"

STATUS_TEXT = {
    STATUS_COMPLETED: "已完成",
    STATUS_PARTIAL: "部分完成",
    STATUS_NEEDS_USER: "需要你处理",
    STATUS_FAILED: "未完成",
}


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"invalid_json:{exc}"
    except OSError as exc:
        return {}, f"read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "not_object"
    return payload, ""


def _rel(project_root: Path, path: Path | str) -> str:
    raw = Path(path)
    try:
        return raw.resolve().relative_to(project_root.resolve()).as_posix()
    except Exception:
        return str(path)


def _new_report(
    project_root: Path,
    *,
    stage: str,
    chapter: int | None = None,
    volume: int | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "overall_status": STATUS_COMPLETED,
        "project_root": str(project_root),
        "chapter": int(chapter or 0),
        "volume": int(volume or 0),
        "files": [],
        "issues": {
            "auto_handled": [],
            "needs_confirmation": [],
            "must_handle": [],
        },
        "timing": {
            "total_ms": 0,
            "steps": [],
        },
        "next_actions": [],
    }


def _add_file(
    report: dict[str, Any],
    *,
    label: str,
    path: Path | str,
    status: str,
    note: str = "",
) -> None:
    report["files"].append(
        {
            "label": label,
            "path": str(path),
            "status": status,
            "note": note,
        }
    )


def _issue_from_author_error(
    error: AuthorError,
    *,
    source: str = "",
    path: str = "",
    message: str = "",
) -> dict[str, Any]:
    return {
        "code": error.code,
        "title": error.title,
        "reason": error.reason,
        "impact": error.impact,
        "next_action": error.next_action,
        "command": error.command,
        "source": source,
        "path": path,
        "message": message,
        "auto_handle": error.auto_handle,
        "matched": error.matched,
    }


def _add_classified_issue(
    report: dict[str, Any],
    issue: Any,
    *,
    source: str = "",
    path: str = "",
    message: str = "",
    severity: str | None = None,
) -> None:
    error = classify_issue(issue)
    bucket = severity or error.severity
    if bucket not in report["issues"]:
        bucket = "must_handle"
    entry = _issue_from_author_error(error, source=source, path=path, message=message)
    if entry not in report["issues"][bucket]:
        report["issues"][bucket].append(entry)


def _add_manual_issue(
    report: dict[str, Any],
    bucket: str,
    *,
    code: str,
    title: str,
    reason: str,
    impact: str,
    next_action: str,
    command: str = "",
    source: str = "",
    path: str = "",
    message: str = "",
    auto_handle: bool = False,
) -> None:
    if bucket not in report["issues"]:
        bucket = "must_handle"
    entry = {
        "code": code,
        "title": title,
        "reason": reason,
        "impact": impact,
        "next_action": next_action,
        "command": command,
        "source": source,
        "path": path,
        "message": message,
        "auto_handle": auto_handle,
        "matched": True,
    }
    if entry not in report["issues"][bucket]:
        report["issues"][bucket].append(entry)


def _artifact_paths(project_root: Path) -> dict[str, Path]:
    return {
        "review_result": project_root / COMMIT_ARTIFACT_FILES[0],
        "fulfillment_result": project_root / COMMIT_ARTIFACT_FILES[1],
        "disambiguation_result": project_root / COMMIT_ARTIFACT_FILES[2],
        "extraction_result": project_root / COMMIT_ARTIFACT_FILES[3],
    }


def _validate_artifact_for_report(
    report: dict[str, Any],
    artifact: str,
    path: Path,
) -> dict[str, Any]:
    validators = {
        "review_result": validate_review_result,
        "fulfillment_result": validate_fulfillment_result,
        "disambiguation_result": validate_disambiguation_result,
        "extraction_result": validate_extraction_result,
    }
    validator = validators[artifact]
    result = validator(path)
    return _add_artifact_validation_result(
        report,
        artifact=artifact,
        result=result,
        path=path,
        ok_note="已生成",
        error_note="缺失或格式不完整",
    )


def _validate_commit_artifact_for_report(
    report: dict[str, Any],
    artifact: str,
    commit_payload: dict[str, Any],
    commit_path: Path,
) -> dict[str, Any]:
    if artifact not in commit_payload:
        result = {
            "artifact": artifact,
            "ok": False,
            "errors": [
                {
                    "type": "missing_artifact",
                    "message": f"chapter commit missing {artifact}",
                    "impact": "commit 文件缺少提交 artifact 快照。",
                    "repair": "重新执行 chapter-commit 生成完整 commit。",
                }
            ],
            "payload": None,
        }
    else:
        result = validate_artifact_payload(artifact, commit_payload.get(artifact), path=str(commit_path))
    return _add_artifact_validation_result(
        report,
        artifact=artifact,
        result=result,
        path=commit_path,
        ok_note="已存入本章事实提交",
        error_note="commit 内 artifact 格式不完整",
    )


def _add_artifact_validation_result(
    report: dict[str, Any],
    *,
    artifact: str,
    result: dict[str, Any],
    path: Path,
    ok_note: str,
    error_note: str,
) -> dict[str, Any]:
    file_status = "completed" if result.get("ok") else "failed"
    note = ok_note if result.get("ok") else error_note
    _add_file(report, label=artifact, path=_rel(Path(report["project_root"]), path), status=file_status, note=note)
    for item in result.get("errors") or []:
        issue = {
            "code": item.get("type") or item.get("code") or "",
            "message": item.get("message") or "",
            "impact": item.get("impact") or "",
            "repair": item.get("repair") or "",
        }
        if str(issue["code"]) == "blocking_review":
            _add_classified_issue(
                report,
                {"code": "blocking_review", "message": issue["message"]},
                source=artifact,
                path=_rel(Path(report["project_root"]), path),
                message=str(issue["message"] or ""),
                severity="must_handle",
            )
        else:
            _add_classified_issue(
                report,
                issue,
                source=artifact,
                path=_rel(Path(report["project_root"]), path),
                message=str(issue["message"] or ""),
            )
    for item in result.get("warnings") or []:
        _add_manual_issue(
            report,
            "needs_confirmation",
            code=str(item.get("type") or item.get("code") or "artifact_warning"),
            title=str(item.get("message") or "存在非阻断提示"),
            reason=str(item.get("impact") or "该项需要作者按需确认。"),
            impact="不阻断当前章节提交。",
            next_action=str(item.get("repair") or "需要时查看对应 artifact。"),
            source=artifact,
            path=_rel(Path(report["project_root"]), path),
            message=str(item.get("message") or ""),
        )
    return result


def _review_audit_path(project_root: Path) -> Path:
    return project_root / ".canon-ledger" / "tmp" / "review_audit.json"


def _review_report_path_from_audit(project_root: Path, audit: dict[str, Any]) -> Path | None:
    report_file = str(audit.get("report_file") or "").strip()
    if not report_file:
        return None
    path = Path(report_file)
    if not path.is_absolute():
        path = project_root / path
    return path


def _find_review_report(project_root: Path, chapter: int) -> Path | None:
    audit, _ = _read_json(_review_audit_path(project_root))
    path = _review_report_path_from_audit(project_root, audit)
    if path and path.is_file():
        return path
    reports_dir = project_root / "审查报告"
    if not reports_dir.is_dir():
        return None
    patterns = (f"*第{chapter}章*.md", f"*第{chapter:02d}章*.md", f"*第{chapter:03d}章*.md")
    for pattern in patterns:
        matches = sorted(reports_dir.rglob(pattern))
        for item in matches:
            if item.is_file():
                return item
    return None


def _commit_path(project_root: Path, chapter: int) -> Path:
    return project_root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"


def _projection_status_from_commit(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("projection_status") if isinstance(payload, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _is_projection_ok(statuses: dict[str, str]) -> bool:
    if not statuses:
        return False
    for writer in REQUIRED_PROJECTION_WRITERS:
        if str(statuses.get(writer) or "") not in OK_PROJECTION_STATUSES:
            return False
    return True


def _projection_failed(statuses: dict[str, str]) -> bool:
    return any(str(value).startswith("failed") for value in statuses.values())


def _projection_pending_or_missing(statuses: dict[str, str]) -> bool:
    if not statuses:
        return True
    return any(
        not str(statuses.get(writer) or "").strip()
        or str(statuses.get(writer) or "") == "pending"
        or str(statuses.get(writer) or "") not in OK_PROJECTION_STATUSES
        for writer in REQUIRED_PROJECTION_WRITERS
    )


def _add_projection_issues(
    report: dict[str, Any],
    project_root: Path,
    chapter: int,
    commit_payload: dict[str, Any],
) -> None:
    current_hash = commit_hash(commit_payload)
    runs = [
        run
        for run in read_projection_runs(project_root, chapter=chapter)
        if str(run.get("commit_hash") or "") == current_hash
    ]
    latest_run = runs[-1] if runs else None
    latest_statuses = projection_status_from_run(latest_run) if latest_run else {}
    status_source = "projection_log" if latest_statuses else "commit"
    statuses = latest_statuses or _projection_status_from_commit(commit_payload)

    had_failed_or_pending = any(projection_run_failed(run) or projection_run_pending(run) for run in runs[:-1])
    latest_ok = bool(latest_run and _is_projection_ok(statuses))
    if had_failed_or_pending and latest_ok:
        _add_manual_issue(
            report,
            "auto_handled",
            code="projection retry",
            title="故事资料更新已补跑成功",
            reason="系统曾遇到资料同步失败或等待状态，随后已有成功的 projection 记录。",
            impact="本章事实已经同步到可用的故事资料，不影响继续写下一章。",
            next_action="本次无需处理；如果想核对，可查看 `.canon-ledger/projection_log.jsonl`。",
            source=status_source,
            path=".canon-ledger/projection_log.jsonl",
            auto_handle=True,
        )
        return

    if _projection_failed(statuses):
        _add_classified_issue(
            report,
            {"code": "projection_failure", "message": str(statuses)},
            source=status_source,
            path=".canon-ledger/projection_log.jsonl" if status_source == "projection_log" else _rel(project_root, _commit_path(project_root, chapter)),
            message=str(statuses),
        )
    elif _projection_pending_or_missing(statuses):
        _add_classified_issue(
            report,
            {"code": "projection_status_missing", "message": str(statuses)},
            source=status_source,
            path=".canon-ledger/projection_log.jsonl" if status_source == "projection_log" else _rel(project_root, _commit_path(project_root, chapter)),
            message=str(statuses),
        )


def _backup_evidence(project_root: Path, chapter: int) -> tuple[bool, str]:
    receipt_path = (
        project_root
        / ".canon-ledger"
        / "backups"
        / f"ch{chapter:04d}.receipt.json"
    )
    return backup_receipt_trusted(project_root, chapter), _rel(
        project_root,
        receipt_path,
    )


def _status_from_issues(report: dict[str, Any], *, core_file_count: int = 0) -> str:
    issues = report.get("issues") or {}
    if issues.get("must_handle"):
        return STATUS_NEEDS_USER if core_file_count > 0 else STATUS_FAILED
    if issues.get("needs_confirmation"):
        return STATUS_PARTIAL
    return STATUS_COMPLETED


def _append_project_status_next_action(report: dict[str, Any], project_root: Path, chapter: int | None) -> None:
    try:
        status = build_project_status(project_root, chapter=chapter)
    except Exception:
        status = {}
    next_action = str(status.get("next_action") or "").strip()
    if next_action:
        report["next_actions"].append(
            {
                "label": "继续当前项目",
                "description": next_action,
                "command": next_action if next_action.startswith("/") else "",
            }
        )


def build_write_report(project_root: Path, *, chapter: int, volume: int | None = None) -> dict[str, Any]:
    report = _new_report(project_root, stage="write", chapter=chapter, volume=volume)
    core_files = 0
    commit_path = _commit_path(project_root, chapter)
    commit_payload, commit_error = _read_json(commit_path)

    chapter_file = find_chapter_file(project_root, chapter)
    if chapter_file:
        core_files += 1
        _add_file(report, label="正文", path=_rel(project_root, chapter_file), status="completed", note="已生成")
    else:
        _add_file(report, label="正文", path=_rel(project_root, project_root / "正文"), status="missing", note="未找到本章正文")
        _add_manual_issue(
            report,
            "must_handle",
            code="chapter_file_missing",
            title="正文文件缺失",
            reason="没有找到本章正文文件。",
            impact="当前章节不能提交为故事事实。",
            next_action="重新运行同一条写章命令，让系统从正文步骤继续。",
            command=f"/canon-ledger-write {chapter}",
            path="正文",
        )

    review_report = _find_review_report(project_root, chapter)
    if review_report:
        _add_file(report, label="审查报告", path=_rel(project_root, review_report), status="completed", note="已生成")
    else:
        _add_file(report, label="审查报告", path="审查报告", status="missing", note="未找到审查报告文件")

    artifact_results: dict[str, dict[str, Any]] = {}
    commit_has_artifact_snapshots = not commit_error and any(
        artifact in commit_payload for artifact in _artifact_paths(project_root)
    )
    if commit_has_artifact_snapshots:
        for artifact in _artifact_paths(project_root):
            artifact_results[artifact] = _validate_commit_artifact_for_report(
                report,
                artifact,
                commit_payload,
                commit_path,
            )
    else:
        for artifact, path in _artifact_paths(project_root).items():
            artifact_results[artifact] = _validate_artifact_for_report(report, artifact, path)

    review_payload = artifact_results.get("review_result", {}).get("payload") or {}
    if isinstance(review_payload, dict) and review_payload.get("review_skipped"):
        _add_manual_issue(
            report,
            "needs_confirmation",
            code="review_skipped",
            title="写作检查已按 minimal 模式跳过",
            reason="本轮写章使用了 no-review artifact，未经过完整 reviewer 审查。",
            impact="正文可以继续保存，但质量风险需要你自行决定是否接受。",
            next_action="如果想补审，运行 `/canon-ledger-review` 查看本章问题。",
            command="/canon-ledger-review",
            source="review_result",
            path=COMMIT_ARTIFACT_FILES[0],
        )
    elif isinstance(review_payload, dict) and review_payload.get("review_degraded"):
        skipped = "、".join(review_payload.get("skipped_dimensions") or []) or "未声明"
        _add_manual_issue(
            report,
            "needs_confirmation",
            code="review_degraded",
            title="写作检查使用了快速模式",
            reason=f"本轮只检查部分事实维度，未检查：{skipped}。",
            impact="正文可以继续保存，但当前审查不能代表完整的五维一致性检查。",
            next_action="如需完整检查，运行 `/canon-ledger-review`。",
            command="/canon-ledger-review",
            source="review_result",
            path=COMMIT_ARTIFACT_FILES[0],
        )

    if commit_error:
        _add_file(report, label="本章事实提交", path=_rel(project_root, commit_path), status="missing", note="未生成 commit")
        _add_classified_issue(
            report,
            {"code": "missing_artifact", "message": "chapter commit missing"},
            source="chapter_commit",
            path=_rel(project_root, commit_path),
            message="chapter commit missing",
        )
    else:
        core_files += 1
        status = str((commit_payload.get("meta") or {}).get("status") or "")
        binding_trusted, binding_code = verify_commit_content_binding(
            project_root,
            chapter,
            commit_payload,
        )
        file_status = (
            "completed" if status == "accepted" and binding_trusted else "failed"
        )
        note = f"status={status or 'missing'}"
        if not binding_trusted:
            note += f", binding={binding_code}"
        _add_file(report, label="本章事实提交", path=_rel(project_root, commit_path), status=file_status, note=note)
        if status == "accepted" and not binding_trusted:
            _add_manual_issue(
                report,
                "must_handle",
                code="chapter_commit_stale",
                title="本章事实提交已过期",
                reason=f"accepted commit 与当前正文不一致（{binding_code}）。",
                impact="旧审查和事实提取不能为当前正文背书。",
                next_action="基于当前正文重新运行审查、data 和 chapter-commit。",
                command=f"/canon-ledger-write {chapter}",
                source="chapter_commit",
                path=_rel(project_root, commit_path),
                message=binding_code,
            )
        elif status == "rejected":
            _add_classified_issue(
                report,
                {"code": "chapter-commit rejected", "message": "chapter commit rejected"},
                source="chapter_commit",
                path=_rel(project_root, commit_path),
                message="chapter commit rejected",
            )
        elif status != "accepted":
            _add_manual_issue(
                report,
                "must_handle",
                code="commit_status_unknown",
                title="本章事实提交状态不明确",
                reason=f"commit 状态是 `{status or 'missing'}`，不是 accepted。",
                impact="系统不能确认本章是否已正式进入故事主链。",
                next_action="运行 `/canon-ledger-doctor` 查看详情，必要时重新提交本章事实。",
                command="/canon-ledger-doctor",
                source="chapter_commit",
                path=_rel(project_root, commit_path),
            )
        elif binding_trusted:
            _add_projection_issues(report, project_root, chapter, commit_payload)

    backup_ok, backup_path = _backup_evidence(project_root, chapter)
    _add_file(
        report,
        label="备份",
        path=backup_path,
        status="completed" if backup_ok else "unknown",
        note="已找到备份记录" if backup_ok else "未找到可确认的备份记录",
    )
    if commit_payload and str((commit_payload.get("meta") or {}).get("status") or "") == "accepted" and not backup_ok:
        _add_manual_issue(
            report,
            "needs_confirmation",
            code="backup_unconfirmed",
            title="备份状态未确认",
            reason="没有找到与当前 accepted commit 匹配的备份 receipt 和恢复点。",
            impact="本章事实已生成，但回滚保障需要再确认。",
            next_action="运行备份命令或重新执行写章收尾步骤。",
            command=f"/canon-ledger-write {chapter}",
            source="backup",
            path=backup_path,
        )

    later_stale = [
        item
        for item in list_needs_revalidation(project_root)
        if item > int(chapter)
    ]
    if later_stale:
        earliest = later_stale[0]
        listed = "、".join(str(item) for item in later_stale)
        _add_manual_issue(
            report,
            "must_handle",
            code="later_chapters_need_revalidation",
            title="后续章节需要重新验证",
            reason=(
                f"第 {chapter} 章改写后，第 {listed} 章仍按旧前文抽取，"
                "不能继续当作有效提交。"
            ),
            impact="长期记忆会保留过期事实，例如旧武器、旧伏笔闭合状态。",
            next_action=f"从第 {earliest} 章开始重新审查并提交。",
            command=f"/canon-ledger-write {earliest}",
            source="chapter_commit",
            path=str(
                project_root
                / ".story-system"
                / "commits"
                / f"chapter_{earliest:03d}.commit.json"
            ),
        )

    report["overall_status"] = _status_from_issues(report, core_file_count=core_files)
    if report["overall_status"] == STATUS_COMPLETED:
        report["next_actions"].append(
            {
                "label": "写下一章",
                "description": f"可以继续写第 {chapter + 1} 章。",
                "command": f"/canon-ledger-write {chapter + 1}",
            }
        )
    elif report["issues"]["must_handle"]:
        report["next_actions"].append(
            {
                "label": "先处理阻断项",
                "description": "先处理“必须处理”里的问题，再重新运行同一条写章命令。",
                "command": f"/canon-ledger-write {chapter}",
            }
        )
    else:
        _append_project_status_next_action(report, project_root, chapter)
    return report


def _load_review_result(project_root: Path) -> tuple[dict[str, Any], Path, str]:
    path = project_root / ".canon-ledger" / "tmp" / "review_results.json"
    payload, error = _read_json(path)
    return payload, path, error


def build_review_report(project_root: Path, *, chapter: int, volume: int | None = None) -> dict[str, Any]:
    report = _new_report(project_root, stage="review", chapter=chapter, volume=volume)
    core_files = 0
    review_result, review_path, review_error = _load_review_result(project_root)
    if review_error:
        _add_file(report, label="审查结果", path=_rel(project_root, review_path), status="missing", note="未找到 review_results.json")
        _add_classified_issue(
            report,
            {"code": "missing_artifact", "message": "review_results missing"},
            source="review",
            path=_rel(project_root, review_path),
            message="review_results missing",
        )
    else:
        validated_review = validate_review_result(review_path)
        schema_errors = [
            issue for issue in validated_review.get("errors", [])
            if issue.get("type") != "blocking_review"
        ]
        for issue in schema_errors:
            _add_classified_issue(
                report,
                issue,
                source="review_result",
                path=_rel(project_root, review_path),
                message=str(issue.get("message") or ""),
                severity="must_handle",
            )
        review_schema_valid = bool(validated_review.get("payload")) and not schema_errors
        if review_schema_valid:
            review_result = validated_review.get("payload") or review_result
        try:
            review_chapter = int(review_result.get("chapter") or 0)
        except (TypeError, ValueError):
            review_chapter = 0
        binding_ok, binding_code = verify_chapter_binding(
            project_root,
            chapter,
            review_result.get("chapter_binding"),
        )
        review_trusted = (
            review_schema_valid
            and review_chapter == int(chapter)
            and binding_ok
        )
        blocking_count = int(review_result.get("blocking_count") or 0)
        manual_checks_count = len(review_result.get("manual_checks") or [])
        _add_file(
            report,
            label="审查结果",
            path=_rel(project_root, review_path),
            status="completed" if review_trusted else "failed",
            note=(
                f"阻断数={blocking_count}，待人工确认={manual_checks_count}"
                if review_trusted
                else f"stale={binding_code if review_chapter == int(chapter) else 'artifact_chapter_mismatch'}"
            ),
        )
        if not review_trusted:
            _add_manual_issue(
                report,
                "must_handle",
                code="review_result_stale",
                title="审查结果已过期",
                reason="review_results 与当前章节或正文内容不一致。",
                impact="旧审查不能为当前正文背书。",
                next_action="重新运行本章 reviewer 和 review-pipeline。",
                command="/canon-ledger-review",
                source="review",
                path=_rel(project_root, review_path),
                message=(
                    binding_code
                    if review_chapter == int(chapter)
                    else "artifact_chapter_mismatch"
                ),
            )
        else:
            core_files += 1
            view = build_review_author_view({"review_result": review_result})
            report["review_author_view"] = view.to_dict()
            if review_result.get("review_skipped"):
                _add_manual_issue(
                    report,
                    "needs_confirmation",
                    code="review_skipped",
                    title="本章未执行事实审查",
                    reason="本轮使用 minimal 模式，五个事实维度均未检查。",
                    impact="当前结果只能证明审查被明确跳过，不能证明正文不存在一致性问题。",
                    next_action="如需完整检查，重新运行 `/canon-ledger-review`。",
                    command="/canon-ledger-review",
                    source="review_result",
                    path=_rel(project_root, review_path),
                )
            elif review_result.get("review_degraded"):
                skipped = "、".join(review_result.get("skipped_dimensions") or []) or "未声明"
                _add_manual_issue(
                    report,
                    "needs_confirmation",
                    code="review_degraded",
                    title="本章只完成了快速事实审查",
                    reason=f"未检查的维度为：{skipped}。",
                    impact="当前结论不能代表完整的五维一致性检查。",
                    next_action="如需完整检查，重新运行 `/canon-ledger-review`。",
                    command="/canon-ledger-review",
                    source="review_result",
                    path=_rel(project_root, review_path),
                )
        if review_trusted and blocking_count > 0:
            _add_manual_issue(
                report,
                "must_handle",
                code="blocking_review",
                title="审查发现必须先处理的问题",
                reason=f"本章有 {blocking_count} 个阻断问题。",
                impact="不处理会影响继续写作、提交或事实一致性。",
                next_action="先按审查报告处理阻断问题；如果要保留当前版本，需要用户明确裁决。",
                command="/canon-ledger-review",
                source="review",
                path=_rel(project_root, review_path),
            )
        if review_trusted and manual_checks_count > 0:
            _add_manual_issue(
                report,
                "needs_confirmation",
                code="review_manual_checks",
                title="有些事实疑点需要作者判断",
                reason=f"本章有 {manual_checks_count} 项容易误判的检查内容。",
                impact="这些项目不会自动修改正文，也不阻断当前提交。",
                next_action="需要时查看审查报告中的“待作者确认”。",
                command="/canon-ledger-review",
                source="review",
                path=_rel(project_root, review_path),
            )

    audit_path = _review_audit_path(project_root)
    audit, audit_error = _read_json(audit_path)
    if audit_error:
        _add_file(report, label="审查审计", path=_rel(project_root, audit_path), status="missing", note="未找到 review_audit.json")
        _add_manual_issue(
            report,
            "needs_confirmation",
            code="review_audit_missing",
            title="审查审计记录未落盘",
            reason="没有找到 `.canon-ledger/tmp/review_audit.json`。",
            impact="审查正文可读，但无法核实本轮实际覆盖了哪些维度。",
            next_action="重新运行审查流程并保存审计记录。",
            command="/canon-ledger-review",
            source="review",
            path=_rel(project_root, audit_path),
        )
    else:
        _add_file(report, label="审查审计", path=_rel(project_root, audit_path), status="completed", note="已生成")
        if review_result and any(
            audit.get(key) != review_result.get(key)
            for key in (
                "chapter",
                "review_mode",
                "review_status",
                "review_degraded",
                "reviewed_dimensions",
                "skipped_dimensions",
            )
        ):
            _add_manual_issue(
                report,
                "must_handle",
                code="review_audit_mismatch",
                title="审查结果与审计记录不一致",
                reason="审查模式、状态或覆盖维度在两份记录中不一致。",
                impact="无法判断本轮实际检查范围，不能把报告当作可靠审查凭据。",
                next_action="重新运行本章审查流程，覆盖旧结果和旧审计记录。",
                command="/canon-ledger-review",
                source="review",
                path=_rel(project_root, audit_path),
            )

    report_path = _review_report_path_from_audit(project_root, audit) if audit else None
    if report_path and report_path.is_file():
        _add_file(report, label="审查报告文件", path=_rel(project_root, report_path), status="completed", note="已生成")
    else:
        found = _find_review_report(project_root, chapter)
        if found:
            _add_file(report, label="审查报告文件", path=_rel(project_root, found), status="completed", note="已生成")
        else:
            _add_file(report, label="审查报告文件", path="审查报告", status="missing", note="未找到审查报告文件")
            _add_manual_issue(
                report,
                "needs_confirmation",
                code="review_report_missing",
                title="审查报告文件未找到",
                reason="没有找到面向阅读的审查报告 Markdown 文件。",
                impact="审查 JSON 仍可用，但你不方便直接阅读修改建议。",
                next_action="重新运行审查流程并指定 report file。",
                command="/canon-ledger-review",
                source="review",
                path="审查报告",
            )

    report["overall_status"] = _status_from_issues(report, core_file_count=core_files)
    if report["issues"]["must_handle"]:
        report["next_actions"].append(
            {
                "label": "处理审查问题",
                "description": "先处理审查报告中的阻断问题，再继续写作或提交。",
                "command": "/canon-ledger-review",
            }
        )
    else:
        report["next_actions"].append(
            {
                "label": "继续写作",
                "description": f"如果本章已满意，可以继续写第 {chapter + 1} 章。",
                "command": f"/canon-ledger-write {chapter + 1}",
            }
        )
    return report


def build_init_report(project_root: Path, *, chapter: int | None = None, volume: int | None = None) -> dict[str, Any]:
    report = _new_report(project_root, stage="init", chapter=chapter, volume=volume)
    core_files = 0
    for rel in INIT_REQUIRED_DIRS:
        path = project_root / rel
        if path.is_dir():
            core_files += 1
            _add_file(report, label=rel, path=rel, status="completed", note="已创建")
        else:
            _add_file(report, label=rel, path=rel, status="missing", note="缺少目录")
            _add_classified_issue(report, {"code": "mainline_ready=false", "message": rel}, source="init", path=rel)
    for rel in INIT_REQUIRED_FILES:
        path = project_root / rel
        if path.is_file():
            core_files += 1
            _add_file(report, label=rel, path=rel, status="completed", note="已生成")
        else:
            _add_file(report, label=rel, path=rel, status="missing", note="缺少文件")
            _add_classified_issue(report, {"code": "mainline_ready=false", "message": rel}, source="init", path=rel)
    report["overall_status"] = _status_from_issues(report, core_file_count=core_files)
    _append_project_status_next_action(report, project_root, chapter)
    return report


def build_plan_report(project_root: Path, *, chapter: int | None = None, volume: int | None = None) -> dict[str, Any]:
    target_chapter = int(chapter or 1)
    report = _new_report(project_root, stage="plan", chapter=target_chapter, volume=volume)
    core_files = 0
    outline_path = project_root / "大纲" / "总纲.md"
    if outline_path.is_file():
        core_files += 1
        _add_file(report, label="总纲", path=_rel(project_root, outline_path), status="completed", note="已生成")
    else:
        _add_file(report, label="总纲", path="大纲/总纲.md", status="missing", note="缺少总纲")
        _add_classified_issue(report, {"code": "mainline_ready=false", "message": "missing outline"}, source="plan", path="大纲/总纲.md")

    for label, path in contract_files_for_chapter(project_root, target_chapter).items():
        if path.is_file():
            core_files += 1
            _add_file(report, label=f"Story System {label}", path=_rel(project_root, path), status="completed", note="合同已生成")
        else:
            _add_file(report, label=f"Story System {label}", path=_rel(project_root, path), status="missing", note="合同缺失")
            _add_classified_issue(report, {"code": "mainline_ready=false", "message": f"missing {label} contract"}, source="plan", path=_rel(project_root, path))

    report["overall_status"] = _status_from_issues(report, core_file_count=core_files)
    _append_project_status_next_action(report, project_root, target_chapter)
    return report


def build_user_report(
    project_root: str | Path,
    *,
    stage: str,
    chapter: int | None = None,
    volume: int | None = None,
) -> dict[str, Any]:
    root = Path(project_root)
    stage = str(stage or "").strip()
    if stage not in VALID_STAGES:
        raise ValueError(f"unknown user report stage: {stage}")

    if stage == "write":
        if not chapter:
            status = build_project_status(root)
            chapter = int(status.get("target_chapter") or 0)
        return build_write_report(root, chapter=int(chapter or 0), volume=volume)
    if stage == "review":
        if not chapter:
            status = build_project_status(root)
            chapter = int(status.get("target_chapter") or 0)
        return build_review_report(root, chapter=int(chapter or 0), volume=volume)
    if stage == "init":
        return build_init_report(root, chapter=chapter, volume=volume)
    return build_plan_report(root, chapter=chapter, volume=volume)


def _format_issue_item(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("code") or "问题")
    impact = str(item.get("impact") or "").strip()
    action = str(item.get("next_action") or "").strip()
    command = str(item.get("command") or "").strip()
    parts = [title]
    if impact:
        parts.append(f"影响：{impact}")
    if action:
        parts.append(f"下一步：{action}")
    if command:
        parts.append(f"命令：{command}")
    return "；".join(parts)


def _render_issue_bucket(title: str, items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return [f"- {title}：无。"]
    return [f"- {title}：{_format_issue_item(item)}" for item in items]


def render_user_report_text(report: dict[str, Any]) -> str:
    status = STATUS_TEXT.get(str(report.get("overall_status") or ""), "未完成")
    lines = [
        f"总状态：{status}。",
        "",
        "一、产生的文件与完成情况",
    ]
    files = report.get("files") or []
    if files:
        for item in files:
            label = str(item.get("label") or "文件")
            status_text = str(item.get("status") or "unknown")
            path = str(item.get("path") or "")
            note = str(item.get("note") or "")
            suffix = f"（{note}）" if note else ""
            path_part = f"{path}：" if path else ""
            lines.append(f"- {label}：{path_part}{status_text}{suffix}")
    else:
        lines.append("- 暂无可确认的产物。")

    issues = report.get("issues") or {}
    lines.extend(["", "二、过程中遇到的问题与异常耗时"])
    lines.extend(_render_issue_bucket("已自动处理", list(issues.get("auto_handled") or [])))
    lines.extend(_render_issue_bucket("建议确认", list(issues.get("needs_confirmation") or [])))
    lines.extend(_render_issue_bucket("必须处理", list(issues.get("must_handle") or [])))
    timing = report.get("timing") or {}
    total_ms = int(timing.get("total_ms") or 0)
    if total_ms > 0:
        lines.append(f"- 耗时异常：本次记录耗时约 {total_ms // 1000} 秒。")
    else:
        lines.append("- 耗时异常：无记录。")
    if str(report.get("overall_status") or "") == STATUS_FAILED:
        lines.append("- 技术详情：如需反馈故障，可附上 `.canon-ledger/logs/run_last.log`。")

    lines.extend(["", "三、下一步建议"])
    next_actions = report.get("next_actions") or []
    if next_actions:
        for item in next_actions:
            description = str(item.get("description") or item.get("label") or "").strip()
            command = str(item.get("command") or "").strip()
            if command and command != description:
                lines.append(f"- {description} 可执行：{command}")
            else:
                lines.append(f"- {description}")
    else:
        lines.append("- 暂无下一步建议；可以运行 `/canon-ledger-doctor` 查看项目状态。")
    return "\n".join(lines).rstrip() + "\n"


def format_user_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    return render_user_report_text(report)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成面向作者的 CanonLedger 运行报告")
    parser.add_argument("--project-root", required=True, help="书项目根目录")
    parser.add_argument("--stage", choices=VALID_STAGES, required=True, help="报告阶段")
    parser.add_argument("--chapter", type=int, default=None, help="目标章节号")
    parser.add_argument("--volume", type=int, default=None, help="目标卷号")
    parser.add_argument("--format", choices=VALID_FORMATS, default="text", help="输出格式")
    args = parser.parse_args()

    report = build_user_report(
        args.project_root,
        stage=args.stage,
        chapter=args.chapter,
        volume=args.volume,
    )
    print(format_user_report(report, args.format))


if __name__ == "__main__":
    main()

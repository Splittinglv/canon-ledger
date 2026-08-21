#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

import backup_manager  # noqa: E402
from data_modules.projection_log import append_projection_run  # noqa: E402
from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.chapter_content_binding import build_chapter_binding  # noqa: E402
from data_modules.run_ledger import record_write_step  # noqa: E402
from data_modules.user_report import build_user_report, render_user_report_text  # noqa: E402
from .review_test_helpers import (  # noqa: E402
    minimal_review,
    standard_review,
    write_current_chapter_contract,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_project(project_root: Path) -> None:
    for rel in (
        ".canon-ledger/backups",
        ".canon-ledger/archive",
        ".canon-ledger/summaries",
        "设定集",
        "大纲",
        "正文",
        "审查报告",
    ):
        (project_root / rel).mkdir(parents=True, exist_ok=True)
    _write_json(
        project_root / ".canon-ledger" / "state.json",
        {
            "project_info": {"title": "测试书", "genre": "玄幻"},
            "progress": {"current_chapter": 0},
        },
    )
    for rel in (
        "设定集/世界观.md",
        "设定集/力量体系.md",
        "设定集/主角卡.md",
        "设定集/反派设计.md",
        "大纲/总纲.md",
        ".env.example",
    ):
        path = project_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder\n", encoding="utf-8")


def _write_review(
    project_root: Path,
    *,
    chapter: int = 1,
    blocking_count: int = 0,
    review_skipped: bool = False,
    chapter_binding: dict | None = None,
    manual_checks: list[dict] | None = None,
) -> None:
    if chapter_binding is None:
        chapter_binding = build_chapter_binding(project_root, chapter)
    review = (
        minimal_review(chapter_binding)
        if review_skipped
        else standard_review(chapter_binding, blocking_count=blocking_count)
    )
    review["manual_checks"] = list(manual_checks or [])
    _write_json(project_root / ".canon-ledger" / "tmp" / "review_results.json", review)
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "review_audit.json",
        {
            "chapter": chapter,
            "start_chapter": chapter,
            "end_chapter": chapter,
            "review_mode": review["review_mode"],
            "review_status": review["review_status"],
            "review_degraded": review["review_degraded"],
            "reviewed_dimensions": review["reviewed_dimensions"],
            "skipped_dimensions": review["skipped_dimensions"],
            "issues_count": review["issues_count"],
            "blocking_count": blocking_count,
            "manual_checks_count": len(manual_checks or []),
            "report_file": f"审查报告/第{chapter}章审查报告.md",
        },
    )
    report_path = project_root / "审查报告" / f"第{chapter}章审查报告.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# 审查报告\n", encoding="utf-8")


def _write_data_artifacts(
    project_root: Path,
    *,
    chapter_binding: dict | None = None,
) -> None:
    def _bound(payload: dict) -> dict:
        if chapter_binding is not None:
            payload["chapter_binding"] = dict(chapter_binding)
        return payload

    _write_json(
        project_root / ".canon-ledger" / "tmp" / "fulfillment_result.json",
        _bound(
            {
                "planned_nodes": [],
                "covered_nodes": [],
                "missed_nodes": [],
                "extra_nodes": [],
            }
        ),
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "disambiguation_result.json",
        _bound({"pending": []}),
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "extraction_result.json",
        _bound(
            {
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "summary_text": "摘要",
            }
        ),
    )


def _commit_payload(
    project_root: Path,
    *,
    chapter: int = 1,
    blocking_count: int = 0,
    force_rejected: bool = False,
    projection_status: dict | None = None,
) -> dict:
    """构造绑定当前正文的真实提交，不使用旧版占位数据。"""
    binding = build_chapter_binding(project_root, chapter)
    planned_nodes = ["本章必须完成"] if force_rejected else []
    write_current_chapter_contract(
        project_root,
        chapter,
        planned_nodes=planned_nodes,
    )
    payload = ChapterCommitService(project_root).build_commit(
        chapter=chapter,
        review_result=standard_review(binding, blocking_count=blocking_count),
        fulfillment_result={
            "planned_nodes": planned_nodes,
            "covered_nodes": [],
            "missed_nodes": planned_nodes,
            "extra_nodes": [],
            "enforcement": "strict" if force_rejected else "advisory",
            "chapter_binding": binding,
        },
        disambiguation_result={"pending": [], "chapter_binding": binding},
        extraction_result={
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "摘要",
            "chapter_binding": binding,
        },
    )
    payload["projection_status"] = dict(
        projection_status
        or {
            "state": "done",
            "index": "skipped",
            "summary": "skipped",
            "memory": "skipped",
            "vector": "skipped",
        }
    )
    return payload


def _write_commit(project_root: Path, payload: dict) -> Path:
    chapter = int(payload["meta"]["chapter"])
    path = project_root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    _write_json(path, payload)
    return path


def _write_strict_local_backup(project_root: Path, *, chapter: int) -> Path:
    with patch.object(backup_manager, "is_git_available", return_value=False):
        manager = backup_manager.GitBackupManager(str(project_root))
        assert manager.backup(chapter, require_accepted_binding=True)
    return project_root / ".canon-ledger" / "backups" / f"ch{chapter:04d}.receipt.json"


def _write_success_case(project_root: Path, *, chapter: int = 1) -> dict:
    _make_project(project_root)
    chapter_file = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    _write_review(project_root, chapter=chapter, chapter_binding=binding)
    _write_data_artifacts(project_root, chapter_binding=binding)
    payload = _commit_payload(project_root, chapter=chapter)
    _write_commit(project_root, payload)

    # 用真实严格备份生成带签名的完整快照；运行台账同时绑定当前正文。
    receipt_path = _write_strict_local_backup(project_root, chapter=chapter)
    record_write_step(
        project_root,
        chapter=chapter,
        step="backup",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={"receipt": receipt_path},
    )
    return payload


def test_render_write_report_success(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)

    report = build_user_report(tmp_path, stage="write", chapter=1)
    text = render_user_report_text(report)

    assert report["schema_version"] == "canon-ledger-user-report/v1"
    assert report["overall_status"] == "completed"
    assert report["stage"] == "write"
    assert any(item["label"] == "正文" and item["status"] == "completed" for item in report["files"])
    assert not report["issues"]["must_handle"]
    assert "/canon-ledger-write 2" in text
    assert "总状态：已完成。" in text
    assert "一、产生的文件与完成情况" in text
    assert "二、过程中遇到的问题与异常耗时" in text
    assert "三、下一步建议" in text


def test_render_write_report_uses_commit_snapshots_when_tmp_artifacts_are_cleaned(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    for path in (tmp_path / ".canon-ledger" / "tmp").glob("*_result.json"):
        path.unlink()

    report = build_user_report(tmp_path, stage="write", chapter=1)

    assert report["overall_status"] == "completed"
    assert not report["issues"]["must_handle"]
    artifact_files = [
        item for item in report["files"]
        if item["label"] in {"review_result", "fulfillment_result", "disambiguation_result", "extraction_result"}
    ]
    assert artifact_files
    assert all(item["path"].endswith("chapter_001.commit.json") for item in artifact_files)


def test_render_write_report_commit_rejected(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    payload = _commit_payload(tmp_path, chapter=1, force_rejected=True)
    _write_commit(tmp_path, payload)

    report = build_user_report(tmp_path, stage="write", chapter=1)

    assert report["overall_status"] == "needs_user"
    titles = [item["title"] for item in report["issues"]["must_handle"]]
    assert "本章事实没有通过提交" in titles


def test_render_write_report_projection_failed(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    _write_commit(
        tmp_path,
        _commit_payload(
            tmp_path,
            chapter=1,
            projection_status={"state": "done", "index": "failed:locked", "summary": "skipped", "memory": "skipped", "vector": "skipped"},
        ),
    )

    report = build_user_report(tmp_path, stage="write", chapter=1)

    assert report["overall_status"] == "needs_user"
    assert any(item["title"] == "故事资料更新失败" for item in report["issues"]["must_handle"])


def test_render_write_report_projection_retry_success_is_auto_handled(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    payload = _commit_payload(
        tmp_path,
        chapter=1,
        projection_status={"state": "done", "index": "failed:locked", "summary": "skipped", "memory": "skipped", "vector": "skipped"},
    )
    commit_path = _write_commit(tmp_path, payload)
    append_projection_run(
        tmp_path,
        payload,
        {"index": {"status": "failed:locked"}},
        commit_path=commit_path,
    )
    # commit 快照已变化，按正常流程重新生成严格本地备份。
    _write_strict_local_backup(tmp_path, chapter=1)
    append_projection_run(
        tmp_path,
        payload,
        {
            "state": {"status": "done"},
            "index": {"status": "skipped"},
            "summary": {"status": "skipped"},
            "memory": {"status": "skipped"},
            "vector": {"status": "skipped"},
        },
        commit_path=commit_path,
    )

    report = build_user_report(tmp_path, stage="write", chapter=1)

    assert report["overall_status"] == "completed"
    assert any(item["code"] == "projection retry" for item in report["issues"]["auto_handled"])
    assert not report["issues"]["must_handle"]


def test_render_review_report_blocking(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0004章.md"
    chapter_file.write_text("第4章正文\n", encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 4)
    _write_review(
        tmp_path,
        chapter=4,
        blocking_count=1,
        chapter_binding=binding,
    )

    report = build_user_report(tmp_path, stage="review", chapter=4)

    assert report["overall_status"] == "needs_user"
    assert report["review_author_view"]["status"] == "must_fix"
    assert any(item["code"] == "blocking_review" for item in report["issues"]["must_handle"])


def test_render_review_report_surfaces_manual_checks_without_blocking(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0004章.md"
    chapter_file.write_text("第4章正文\n", encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 4)
    _write_review(
        tmp_path,
        chapter=4,
        chapter_binding=binding,
        manual_checks=[
            {
                "category": "character",
                "location": "第3段",
                "description": "角色是否已知暗门位置",
                "evidence": "前文只有模糊暗示",
                "reason": "插件无法可靠判断代词指向",
                "options": ["确认已知", "改写消歧"],
            }
        ],
    )

    report = build_user_report(tmp_path, stage="review", chapter=4)

    assert report["overall_status"] == "partial"
    assert report["review_author_view"]["status"] == "manual_check"
    assert not report["issues"]["must_handle"]
    assert any(
        item["code"] == "review_manual_checks"
        for item in report["issues"]["needs_confirmation"]
    )
    confirm_commands = [
        str(item.get("command") or "") for item in report["next_actions"]
    ]
    assert any("/canon-ledger-confirm 4" in command for command in confirm_commands)
    write_only = [
        item
        for item in report["next_actions"]
        if str(item.get("command") or "").startswith("/canon-ledger-write")
    ]
    assert not write_only
    assert len(report["next_actions"]) == 1


def test_write_report_pending_confirm_is_only_next_step(tmp_path: Path) -> None:
    from data_modules.human_review import HumanReviewService

    _write_success_case(tmp_path, chapter=1)
    binding = build_chapter_binding(tmp_path, 1)
    HumanReviewService(tmp_path).persist_queue(
        1,
        binding,
        [
            {
                "source": "review_manual_check",
                "category": "timeline",
                "dimension": "presence",
                "candidate_event_id": "timeline-check-1",
                "evidence_quote": "正文",
                "reason": "转场耗时可能不足",
                "options": ["confirm", "ignore"],
            }
        ],
    )

    report = build_user_report(tmp_path, stage="write", chapter=1)
    commands = [str(item.get("command") or "") for item in report["next_actions"]]
    assert any("/canon-ledger-confirm 1" in command for command in commands)
    assert not any(
        command.startswith("/canon-ledger-write") for command in commands
    )


def test_review_report_rejects_review_after_manuscript_edit(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("待审正文 v1\n", encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 1)
    _write_review(tmp_path, chapter=1, chapter_binding=binding)
    chapter_file.write_text("待审正文 v2\n", encoding="utf-8")

    report = build_user_report(tmp_path, stage="review", chapter=1)

    assert report["overall_status"] == "failed"
    assert any(
        item["code"] == "review_result_stale"
        for item in report["issues"]["must_handle"]
    )


def test_missing_artifact_does_not_crash_and_is_not_completed(tmp_path: Path) -> None:
    _make_project(tmp_path)
    (tmp_path / "正文" / "第0001章.md").write_text("正文\n", encoding="utf-8")

    report = build_user_report(tmp_path, stage="write", chapter=1)
    text = render_user_report_text(report)

    assert report["overall_status"] in {"needs_user", "failed"}
    assert report["issues"]["must_handle"]
    assert "总状态：已完成。" not in text


def test_user_report_includes_log_path_only_on_failure(tmp_path: Path) -> None:
    _make_project(tmp_path)

    failed = build_user_report(tmp_path, stage="write", chapter=1)
    failed_text = render_user_report_text(failed)
    assert failed["overall_status"] == "failed"
    assert ".canon-ledger/logs/run_last.log" in failed_text

    _write_success_case(tmp_path, chapter=1)
    completed = build_user_report(tmp_path, stage="write", chapter=1)
    completed_text = render_user_report_text(completed)
    assert completed["overall_status"] == "completed"
    assert ".canon-ledger/logs/run_last.log" not in completed_text


def test_write_report_rejects_accepted_commit_after_manuscript_edit(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    (tmp_path / "正文" / "第0001章.md").write_text("正文已修改\n", encoding="utf-8")

    report = build_user_report(tmp_path, stage="write", chapter=1)

    assert report["overall_status"] == "needs_user"
    assert any(
        item["code"] == "chapter_commit_stale"
        for item in report["issues"]["must_handle"]
    )


def test_write_report_does_not_accept_backup_name_glob_without_receipt(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    backup_dir = tmp_path / ".canon-ledger" / "backups"
    (backup_dir / "ch0001.receipt.json").unlink()
    for snapshot in backup_dir.glob("snapshot_ch0001_*"):
        shutil.rmtree(snapshot)
    (backup_dir / "ch0001_fake").mkdir()

    report = build_user_report(tmp_path, stage="write", chapter=1)

    backup_file = next(item for item in report["files"] if item["label"] == "备份")
    assert backup_file["status"] == "unknown"
    assert any(
        item["code"] == "backup_unconfirmed"
        for item in report["issues"]["needs_confirmation"]
    )


def test_write_report_ignores_projection_log_for_another_commit(tmp_path: Path) -> None:
    current = _write_success_case(tmp_path, chapter=1)
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    old = dict(current)
    old["outline_snapshot"] = {"stale": True}
    append_projection_run(
        tmp_path,
        old,
        {"index": {"status": "failed:old"}},
        commit_path=commit_path,
    )

    report = build_user_report(tmp_path, stage="write", chapter=1)

    assert report["overall_status"] == "completed"
    assert not any(
        item["title"] == "故事资料更新失败"
        for item in report["issues"]["must_handle"]
    )


def _queue_pending_item(project_root: Path, chapter: int = 3) -> None:
    from data_modules.human_review import HumanReviewService

    chapter_path = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("爱丽丝抵达北城。", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    HumanReviewService(project_root).persist_queue(
        chapter,
        binding,
        [
            {
                "decision_id": "alice-location-check",
                "category": "presence",
                "candidate_event_id": "alice-location",
                "reason": "这是否是实际抵达需要作者确认",
            }
        ],
    )


def test_confirm_report_surfaces_pending_queue(tmp_path: Path) -> None:
    _make_project(tmp_path)
    _queue_pending_item(tmp_path, chapter=3)

    report = build_user_report(tmp_path, stage="confirm")

    assert report["overall_status"] == "partial"
    assert any(
        item["code"] == "human_review_pending"
        for item in report["issues"]["needs_confirmation"]
    )
    assert any(
        "/canon-ledger-confirm 3" in str(item.get("command") or "")
        for item in report["next_actions"]
    )


def test_confirm_report_completes_after_all_decisions(tmp_path: Path) -> None:
    from data_modules.human_review import HumanReviewService

    _make_project(tmp_path)
    _queue_pending_item(tmp_path, chapter=3)
    HumanReviewService(tmp_path).record(
        {
            "decisions": [
                {"decision_id": "alice-location-check", "action": "confirm"}
            ]
        }
    )

    # 裁决只落库、还没重放进本章提交时，不得报告完成。
    saved_only = build_user_report(tmp_path, stage="confirm")
    assert saved_only["overall_status"] == "needs_user"
    assert any(
        item["code"] == "human_review_decisions_not_replayed"
        for item in saved_only["issues"]["must_handle"]
    )

    resolved_item = HumanReviewService(tmp_path).list_items(3)[0]
    human_review_provenance = {
        "resolved_decision_ids": ["ch0003-alice-location-check"]
    }
    if resolved_item.get("decision_sha256"):
        human_review_provenance["decision_receipts"] = [
            {
                "decision_id": "ch0003-alice-location-check",
                "decision_sha256": resolved_item["decision_sha256"],
            }
        ]

    commit_path = tmp_path / ".story-system" / "commits" / "chapter_003.commit.json"
    commit_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        commit_path,
        {
            "meta": {"chapter": 3, "status": "accepted"},
            "provenance": {
                "human_review": human_review_provenance
            },
        },
    )

    report = build_user_report(tmp_path, stage="confirm")
    text = render_user_report_text(report)

    assert report["overall_status"] == "completed"
    assert not report["issues"]["needs_confirmation"]
    assert "待确认 0 条" in text


def test_write_report_rewrite_required_is_must_handle_and_only_rewrite_action(
    tmp_path: Path,
) -> None:
    from data_modules.human_review import HumanReviewService

    _write_success_case(tmp_path, chapter=4)
    items = [
        {
            "decision_id": "ch0002-pending",
            "chapter": 2,
            "status": "pending",
        },
        {
            "decision_id": "ch0002-resolved",
            "decision_sha256": "resolved-sha",
            "chapter": 2,
            "status": "resolved",
        },
        {
            "decision_id": "ch0002-rewrite",
            "chapter": 2,
            "status": "rewrite_required",
            "outcome": "rewrite_required",
            "source": "review_manual_check",
        },
        {
            "decision_id": "ch0003-rewrite",
            "chapter": 3,
            "status": "rewrite_required",
        },
    ]

    with patch.object(HumanReviewService, "list_items", return_value=items):
        report = build_user_report(tmp_path, stage="write", chapter=4)

    assert report["overall_status"] == "needs_user"
    assert any(
        item["code"] == "human_review_rewrite_required"
        for item in report["issues"]["must_handle"]
    )
    assert [item["command"] for item in report["next_actions"]] == [
        "/canon-ledger-write 2"
    ]


def test_confirm_report_rewrite_is_not_resolved_or_replayed(tmp_path: Path) -> None:
    from data_modules.human_review import HumanReviewService

    _make_project(tmp_path)
    items = [
        {
            "decision_id": "ch0003-review-check",
            "decision_sha256": "rewrite-sha",
            "chapter": 3,
            "status": "rewrite_required",
            "action": "rewrite",
            "source": "review_manual_check",
        }
    ]
    with patch.object(HumanReviewService, "list_items", return_value=items):
        report = build_user_report(tmp_path, stage="confirm")
    queue_file = next(
        item for item in report["files"] if item["label"] == "人工确认队列"
    )

    assert report["overall_status"] == "needs_user"
    assert "已裁决 0 条" in queue_file["note"]
    assert "需改写 1 条" in queue_file["note"]
    assert any(
        item["code"] == "human_review_rewrite_required"
        for item in report["issues"]["must_handle"]
    )
    assert not any(
        item["code"] == "human_review_decisions_not_replayed"
        for item in report["issues"]["must_handle"]
    )
    assert [item["command"] for item in report["next_actions"]] == [
        "/canon-ledger-write 3"
    ]


def test_confirm_report_requires_matching_decision_receipt(tmp_path: Path) -> None:
    from data_modules.human_review import HumanReviewService

    _make_project(tmp_path)
    item = {
        "decision_id": "ch0003-location",
        "decision_sha256": "current-decision-sha",
        "chapter": 3,
        "status": "resolved",
    }
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_003.commit.json"
    _write_json(
        commit_path,
        {
            "meta": {"chapter": 3, "status": "accepted"},
            "provenance": {
                "human_review": {
                    "resolved_decision_ids": ["ch0003-location"],
                    "decision_receipts": [
                        {
                            "decision_id": "ch0003-location",
                            "decision_sha256": "stale-decision-sha",
                        }
                    ],
                }
            },
        },
    )

    with patch.object(HumanReviewService, "list_items", return_value=[item]):
        stale = build_user_report(tmp_path, stage="confirm")

    assert any(
        issue["code"] == "human_review_decisions_not_replayed"
        for issue in stale["issues"]["must_handle"]
    )
    assert [action["command"] for action in stale["next_actions"]] == [
        "/canon-ledger-confirm 3"
    ]

    commit_payload = json.loads(commit_path.read_text(encoding="utf-8"))
    commit_payload["provenance"]["human_review"]["decision_receipts"][0][
        "decision_sha256"
    ] = "current-decision-sha"
    _write_json(commit_path, commit_payload)
    with patch.object(HumanReviewService, "list_items", return_value=[item]):
        applied = build_user_report(tmp_path, stage="confirm")

    assert not applied["issues"]["must_handle"]
    assert not any(
        str(action.get("command") or "").startswith("/canon-ledger-confirm")
        for action in applied["next_actions"]
    )


def test_confirm_report_uses_earliest_chapter_before_state_priority(
    tmp_path: Path,
) -> None:
    from data_modules.human_review import HumanReviewService

    _make_project(tmp_path)
    items = [
        {"decision_id": "ch0002-pending", "chapter": 2, "status": "pending"},
        {
            "decision_id": "ch0003-rewrite",
            "chapter": 3,
            "status": "rewrite_required",
        },
    ]

    with patch.object(HumanReviewService, "list_items", return_value=items):
        report = build_user_report(tmp_path, stage="confirm")

    assert [item["command"] for item in report["next_actions"]] == [
        "/canon-ledger-confirm 2"
    ]
    issue_commands = [
        str(item.get("command") or "")
        for bucket in ("needs_confirmation", "must_handle")
        for item in report["issues"][bucket]
        if str(item.get("command") or "")
    ]
    assert issue_commands == ["/canon-ledger-confirm 2"]
    assert not any(
        str(item.get("command") or "").startswith("/canon-ledger-write 4")
        for item in report["next_actions"]
    )


def test_review_report_rewrite_does_not_reopen_manual_confirmation(
    tmp_path: Path,
) -> None:
    from data_modules.human_review import HumanReviewService

    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0004章.md"
    chapter_file.write_text("第4章正文\n", encoding="utf-8")
    binding = build_chapter_binding(tmp_path, 4)
    _write_review(
        tmp_path,
        chapter=4,
        chapter_binding=binding,
        manual_checks=[
            {
                "category": "timeline",
                "location": "第3段",
                "description": "转场耗时是否成立",
                "evidence": "片刻后抵达南港",
                "reason": "作者已确认时间不足",
            }
        ],
    )
    items = [
        {
            "decision_id": "ch0004-timeline",
            "chapter": 4,
            "status": "rewrite_required",
            "source": "review_manual_check",
        }
    ]
    queue_path = HumanReviewService(tmp_path).queue_path(4)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text("{}", encoding="utf-8")

    with patch.object(HumanReviewService, "list_items", return_value=items):
        report = build_user_report(tmp_path, stage="review", chapter=4)

    assert any(
        item["code"] == "human_review_rewrite_required"
        for item in report["issues"]["must_handle"]
    )
    assert [item["command"] for item in report["next_actions"]] == [
        "/canon-ledger-write 4"
    ]


def test_write_report_resolved_without_provenance_never_offers_next_chapter(
    tmp_path: Path,
) -> None:
    from data_modules.human_review import HumanReviewService

    _write_success_case(tmp_path, chapter=1)
    items = [
        {
            "decision_id": "ch0001-resolved",
            "decision_sha256": "decision-sha",
            "chapter": 1,
            "status": "resolved",
        }
    ]

    with patch.object(HumanReviewService, "list_items", return_value=items):
        report = build_user_report(tmp_path, stage="write", chapter=1)

    assert any(
        item["code"] == "human_review_decisions_not_replayed"
        for item in report["issues"]["must_handle"]
    )
    assert [item["command"] for item in report["next_actions"]] == [
        "/canon-ledger-confirm 1"
    ]
    assert not any(
        str(item.get("command") or "") == "/canon-ledger-write 2"
        for item in report["next_actions"]
    )


def test_write_report_fails_closed_on_corrupt_prior_human_review_queue(
    tmp_path: Path,
) -> None:
    _write_success_case(tmp_path, chapter=4)
    queue_path = (
        tmp_path
        / ".canon-ledger"
        / "human-review"
        / "queue"
        / "chapter_0002.json"
    )
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text("{not-json", encoding="utf-8")

    report = build_user_report(tmp_path, stage="write", chapter=4)

    assert any(
        item["code"] == "human_review_state_invalid"
        for item in report["issues"]["must_handle"]
    )
    assert [item["command"] for item in report["next_actions"]] == [
        "/canon-ledger-doctor"
    ]

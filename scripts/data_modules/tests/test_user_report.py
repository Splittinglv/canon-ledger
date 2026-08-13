#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from data_modules.projection_log import append_projection_run  # noqa: E402
from data_modules.projection_log import commit_hash  # noqa: E402
from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.chapter_content_binding import build_chapter_binding  # noqa: E402
from data_modules.run_ledger import record_write_step  # noqa: E402
from data_modules.user_report import build_user_report, render_user_report_text  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_project(project_root: Path) -> None:
    for rel in (
        ".webnovel/backups",
        ".webnovel/archive",
        ".webnovel/summaries",
        "设定集",
        "大纲",
        "正文",
        "审查报告",
    ):
        (project_root / rel).mkdir(parents=True, exist_ok=True)
    _write_json(
        project_root / ".webnovel" / "state.json",
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
) -> None:
    review = {
        "chapter": chapter,
        "issues": [],
        "issues_count": 0,
        "blocking_count": blocking_count,
        "has_blocking": blocking_count > 0,
        "summary": "可继续" if blocking_count == 0 else "有阻断",
    }
    if blocking_count:
        review["issues"] = [
            {
                "severity": "critical",
                "category": "timeline",
                "location": "第2段",
                "description": "时间线断裂",
                "fix_hint": "补过渡",
                "blocking": True,
            }
        ]
        review["issues_count"] = 1
    if review_skipped:
        review["review_skipped"] = True
        review["review_mode"] = "minimal"
    if chapter_binding is not None:
        review["chapter_binding"] = dict(chapter_binding)
    _write_json(project_root / ".webnovel" / "tmp" / "review_results.json", review)
    _write_json(
        project_root / ".webnovel" / "tmp" / "review_metrics.json",
        {
            "start_chapter": chapter,
            "end_chapter": chapter,
            "issues_count": review["issues_count"],
            "blocking_count": blocking_count,
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
        project_root / ".webnovel" / "tmp" / "fulfillment_result.json",
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
        project_root / ".webnovel" / "tmp" / "disambiguation_result.json",
        _bound({"pending": []}),
    )
    _write_json(
        project_root / ".webnovel" / "tmp" / "extraction_result.json",
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
    projection_status: dict | None = None,
) -> dict:
    """Build a real, current-prose-bound commit rather than a legacy stub."""
    binding = build_chapter_binding(project_root, chapter)
    payload = ChapterCommitService(project_root).build_commit(
        chapter=chapter,
        review_result={
            "blocking_count": blocking_count,
            "chapter_binding": binding,
        },
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


def _write_success_case(project_root: Path, *, chapter: int = 1) -> dict:
    _make_project(project_root)
    chapter_file = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    _write_review(project_root, chapter=chapter, chapter_binding=binding)
    _write_data_artifacts(project_root, chapter_binding=binding)
    payload = _commit_payload(project_root, chapter=chapter)
    _write_commit(project_root, payload)

    # Mirror the evidence that the strict resume path accepts: the receipt
    # must bind this exact accepted commit and the ledger must record the same
    # current manuscript as its backup input.
    backup_dir = project_root / ".webnovel" / "backups"
    snapshot = backup_dir / f"snapshot_ch{chapter:04d}_current"
    snapshot.mkdir(parents=True, exist_ok=True)
    snapshot_chapter = snapshot / payload["chapter_binding"]["path"]
    snapshot_chapter.parent.mkdir(parents=True, exist_ok=True)
    snapshot_chapter.write_bytes(chapter_file.read_bytes())
    snapshot_commit = (
        snapshot
        / ".story-system"
        / "commits"
        / f"chapter_{chapter:03d}.commit.json"
    )
    snapshot_commit.parent.mkdir(parents=True, exist_ok=True)
    _write_json(snapshot_commit, payload)
    receipt_path = backup_dir / f"ch{chapter:04d}.receipt.json"
    _write_json(
        receipt_path,
        {
            "schema_version": "webnovel-backup-receipt/v1",
            "chapter": chapter,
            "chapter_binding": payload["chapter_binding"],
            "chapter_commit_path": f".story-system/commits/chapter_{chapter:03d}.commit.json",
            "chapter_commit_hash": commit_hash(payload),
            "mode": "local",
            "snapshot": snapshot.name,
        },
    )
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

    assert report["schema_version"] == "webnovel-user-report/v1"
    assert report["overall_status"] == "completed"
    assert report["stage"] == "write"
    assert any(item["label"] == "正文" and item["status"] == "completed" for item in report["files"])
    assert not report["issues"]["must_handle"]
    assert "/webnovel-write 2" in text
    assert "总状态：已完成。" in text
    assert "一、产生的文件与完成情况" in text
    assert "二、过程中遇到的问题与异常耗时" in text
    assert "三、下一步建议" in text


def test_render_write_report_uses_commit_snapshots_when_tmp_artifacts_are_cleaned(tmp_path: Path) -> None:
    _write_success_case(tmp_path, chapter=1)
    for path in (tmp_path / ".webnovel" / "tmp").glob("*_result.json"):
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
    payload = _commit_payload(tmp_path, chapter=1, blocking_count=1)
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
    # The commit snapshot changed, so refresh the backup receipt/snapshot to
    # model a completed retry followed by the normal backup step.
    binding = payload["chapter_binding"]
    backup_dir = tmp_path / ".webnovel" / "backups"
    snapshot = backup_dir / "snapshot_ch0001_retry"
    snapshot_chapter = snapshot / binding["path"]
    snapshot_chapter.parent.mkdir(parents=True, exist_ok=True)
    snapshot_chapter.write_bytes((tmp_path / binding["path"]).read_bytes())
    snapshot_commit = snapshot / ".story-system" / "commits" / commit_path.name
    snapshot_commit.parent.mkdir(parents=True, exist_ok=True)
    _write_json(snapshot_commit, payload)
    _write_json(
        backup_dir / "ch0001.receipt.json",
        {
            "schema_version": "webnovel-backup-receipt/v1",
            "chapter": 1,
            "chapter_binding": binding,
            "chapter_commit_path": ".story-system/commits/chapter_001.commit.json",
            "chapter_commit_hash": commit_hash(payload),
            "mode": "local",
            "snapshot": snapshot.name,
        },
    )
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
    assert ".webnovel/logs/run_last.log" in failed_text

    _write_success_case(tmp_path, chapter=1)
    completed = build_user_report(tmp_path, stage="write", chapter=1)
    completed_text = render_user_report_text(completed)
    assert completed["overall_status"] == "completed"
    assert ".webnovel/logs/run_last.log" not in completed_text


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
    backup_dir = tmp_path / ".webnovel" / "backups"
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

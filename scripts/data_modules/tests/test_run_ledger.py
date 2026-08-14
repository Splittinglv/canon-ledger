#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

import backup_manager  # noqa: E402
from data_modules.run_ledger import build_write_resume_plan, record_write_step  # noqa: E402
from data_modules.chapter_content_binding import build_chapter_binding  # noqa: E402
from data_modules.projection_log import commit_hash  # noqa: E402
from .review_test_helpers import standard_review  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_project(project_root: Path) -> None:
    (project_root / ".webnovel" / "tmp").mkdir(parents=True, exist_ok=True)
    (project_root / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (project_root / "正文").mkdir(parents=True, exist_ok=True)
    _write_json(project_root / ".webnovel" / "state.json", {"project_info": {"title": "测试书"}, "progress": {}})


def _commit_payload(project_root: Path, status: str = "accepted") -> dict:
    binding = build_chapter_binding(project_root, 1)
    return {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": 1,
            "status": status,
        },
        "chapter_binding": binding,
        "provenance": {"chapter_binding": binding},
        "review_result": standard_review(binding),
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        "disambiguation_result": {"pending": [], "chapter_binding": binding},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "chapter_binding": binding,
        },
        "projection_status": {
            "state": "done",
            "index": "skipped",
            "summary": "skipped",
            "memory": "skipped",
            "vector": "skipped",
        },
    }


def _write_bound_artifacts(project_root: Path) -> dict[str, Path]:
    binding = build_chapter_binding(project_root, 1)
    payloads = {
        "review_result": standard_review(binding),
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        "disambiguation_result": {
            "pending": [],
            "chapter_binding": binding,
        },
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "chapter_binding": binding,
        },
    }
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        filename = "review_results.json" if name == "review_result" else f"{name}.json"
        path = project_root / ".webnovel" / "tmp" / filename
        _write_json(path, payload)
        paths[name] = path
    return paths


def test_run_ledger_records_write_step_status(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")

    entry = record_write_step(
        tmp_path,
        chapter=1,
        step="draft",
        status="completed",
        outputs={"chapter_file": chapter_file},
    )

    assert entry["status"] == "completed"
    assert entry["outputs"]["chapter_file"]["exists"] is True
    assert (tmp_path / ".webnovel" / "run_ledger.json").is_file()


def test_write_resume_skips_completed_draft_and_review(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    review_path = tmp_path / ".webnovel" / "tmp" / "review_results.json"
    _write_json(
        review_path,
        standard_review(build_chapter_binding(tmp_path, 1)),
    )

    record_write_step(tmp_path, chapter=1, step="draft", status="completed", outputs={"chapter_file": chapter_file})
    record_write_step(
        tmp_path,
        chapter=1,
        step="review",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={"review_result": review_path},
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["draft"] == "skip"
    assert actions["review"] == "skip"
    assert actions["data"] == "run"


def test_write_resume_rechecks_review_when_chapter_file_changed(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文 v1\n", encoding="utf-8")
    record_write_step(tmp_path, chapter=1, step="draft", status="completed", outputs={"chapter_file": chapter_file})
    chapter_file.write_text("正文 v2\n", encoding="utf-8")

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["draft"] == "run"
    assert actions["review"] == "run"
    assert any(item["code"] == "chapter_file_changed" for item in plan["needs_user_confirmation"])


def test_write_resume_retries_backup_after_commit_done(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    record_write_step(tmp_path, chapter=1, step="draft", status="completed", outputs={"chapter_file": chapter_file})
    _write_bound_artifacts(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        _commit_payload(tmp_path, "accepted"),
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["draft"] == "skip"
    assert actions["review"] == "skip"
    assert actions["data"] == "skip"
    assert actions["commit"] == "skip"
    assert actions["projection"] == "skip"
    assert actions["backup"] == "retry"
    assert plan["resume_from"] == "backup"
    assert any(item["code"] == "chapter_already_accepted" for item in plan["needs_user_confirmation"])


def test_write_resume_reruns_commit_after_rejected_commit(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    review_path = tmp_path / ".webnovel" / "tmp" / "review_results.json"
    binding = build_chapter_binding(tmp_path, 1)
    _write_json(review_path, standard_review(binding, blocking_count=1))
    fulfillment_path = tmp_path / ".webnovel" / "tmp" / "fulfillment_result.json"
    disambiguation_path = tmp_path / ".webnovel" / "tmp" / "disambiguation_result.json"
    extraction_path = tmp_path / ".webnovel" / "tmp" / "extraction_result.json"
    _write_json(fulfillment_path, {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": [], "chapter_binding": binding})
    _write_json(disambiguation_path, {"pending": [], "chapter_binding": binding})
    _write_json(extraction_path, {"accepted_events": [], "state_deltas": [], "entity_deltas": [], "chapter_binding": binding})
    record_write_step(
        tmp_path,
        chapter=1,
        step="draft",
        status="completed",
        outputs={"chapter_file": chapter_file},
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="review",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={"review_result": review_path},
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="data",
        status="completed",
        inputs={"chapter_file": chapter_file},
        outputs={
            "fulfillment_result": fulfillment_path,
            "disambiguation_result": disambiguation_path,
            "extraction_result": extraction_path,
        },
    )
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        _commit_payload(tmp_path, "rejected"),
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["commit"] == "run"
    assert actions["projection"] == "run"
    assert plan["resume_from"] == "commit"
    assert any(item["code"] == "chapter_commit_rejected" for item in plan["needs_user_confirmation"])


def test_write_resume_does_not_skip_critical_steps_for_unbound_accepted_commit(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {
                "state": "done", "index": "skipped", "summary": "skipped",
                "memory": "skipped", "vector": "skipped",
            },
        },
    )
    (tmp_path / ".webnovel" / "backups" / "snapshot_ch0001_old").mkdir(
        parents=True,
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)

    actions = {item["step"]: item["action"] for item in plan["steps"]}
    assert actions["review"] == "run"
    assert actions["data"] == "run"
    assert actions["commit"] == "run"
    assert actions["projection"] == "run"
    assert actions["backup"] == "run"
    assert any(
        item["code"] == "chapter_commit_stale"
        for item in plan["needs_user_confirmation"]
    )


def test_write_resume_skips_backup_only_with_current_manuscript_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    _write_bound_artifacts(tmp_path)
    commit_payload = _commit_payload(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        commit_payload,
    )
    monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)
    assert backup_manager.GitBackupManager(str(tmp_path)).backup(
        1,
        require_accepted_binding=True,
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="backup",
        status="completed",
        inputs={"chapter_file": chapter_file},
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)
    actions = {item["step"]: item["action"] for item in plan["steps"]}

    assert actions["backup"] == "skip"


def test_write_resume_rejects_stale_backup_receipt(tmp_path: Path) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    _write_bound_artifacts(tmp_path)
    commit_payload = _commit_payload(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        commit_payload,
    )
    backup_dir = tmp_path / ".webnovel" / "backups"
    (backup_dir / "snapshot_ch0001_old").mkdir(parents=True)
    _write_json(
        backup_dir / "ch0001.receipt.json",
        {
            "schema_version": "webnovel-backup-receipt/v1",
            "chapter": 1,
            "chapter_binding": commit_payload["chapter_binding"],
            "chapter_commit_path": ".story-system/commits/chapter_001.commit.json",
            "chapter_commit_hash": "stale",
            "mode": "local",
            "snapshot": "snapshot_ch0001_old",
        },
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="backup",
        status="completed",
        inputs={"chapter_file": chapter_file},
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)
    actions = {item["step"]: item["action"] for item in plan["steps"]}

    assert actions["backup"] == "retry"


def test_write_resume_rejects_empty_local_snapshot_even_with_matching_receipt(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    _write_bound_artifacts(tmp_path)
    commit_payload = _commit_payload(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        commit_payload,
    )
    backup_dir = tmp_path / ".webnovel" / "backups"
    (backup_dir / "snapshot_ch0001_empty").mkdir(parents=True)
    _write_json(
        backup_dir / "ch0001.receipt.json",
        {
            "schema_version": "webnovel-backup-receipt/v2",
            "chapter": 1,
            "chapter_binding": commit_payload["chapter_binding"],
            "chapter_commit_path": ".story-system/commits/chapter_001.commit.json",
            "chapter_commit_hash": commit_hash(commit_payload),
            "mode": "local",
            "snapshot": "snapshot_ch0001_empty",
            "manifest_path": "snapshot.manifest.json",
            "manifest_sha256": "0" * 64,
            "signature_algorithm": "hmac-sha256",
            "signature": "0" * 64,
        },
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="backup",
        status="completed",
        inputs={"chapter_file": chapter_file},
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)
    actions = {item["step"]: item["action"] for item in plan["steps"]}

    assert actions["backup"] == "retry"


def test_write_resume_rejects_git_receipt_when_tag_does_not_contain_it(
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    chapter_file = tmp_path / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")
    _write_bound_artifacts(tmp_path)
    commit_payload = _commit_payload(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        commit_payload,
    )
    receipt = {
        "schema_version": "webnovel-backup-receipt/v1",
        "chapter": 1,
        "chapter_binding": commit_payload["chapter_binding"],
        "chapter_commit_path": ".story-system/commits/chapter_001.commit.json",
        "chapter_commit_hash": commit_hash(commit_payload),
        "mode": "git",
        "tag": "ch0001",
    }
    _write_json(
        tmp_path / ".webnovel" / "backups" / "ch0001.receipt.json",
        receipt,
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Author"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "author@example.com"], cwd=tmp_path, check=True)
    # 先给不含 receipt 的旧提交打标签；仅在工作区补一份 JSON 不能证明标签内有备份。
    (tmp_path / ".webnovel" / "backups" / "ch0001.receipt.json").unlink()
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "pre-receipt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "tag", "ch0001"], cwd=tmp_path, check=True)
    _write_json(
        tmp_path / ".webnovel" / "backups" / "ch0001.receipt.json",
        receipt,
    )
    record_write_step(
        tmp_path,
        chapter=1,
        step="backup",
        status="completed",
        inputs={"chapter_file": chapter_file},
    )

    plan = build_write_resume_plan(tmp_path, chapter=1)
    actions = {item["step"]: item["action"] for item in plan["steps"]}

    assert actions["backup"] == "retry"

from __future__ import annotations

import json
import subprocess

import backup_manager
from backup_manager import GitBackupManager
from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.tests.review_test_helpers import (
    standard_review,
    write_current_chapter_contract,
)


def test_backup_manager_gitignore_excludes_env(tmp_path, monkeypatch):
    def fake_run(args, cwd=None, check=False, capture_output=False, text=False, encoding=None, timeout=None):
        if args == ["git", "init"]:
            (tmp_path / ".git").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup_manager, "is_git_available", lambda: True)
    monkeypatch.setattr(backup_manager.subprocess, "run", fake_run)

    GitBackupManager(str(tmp_path))

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert ".env.*" in gitignore
    assert "!.env.example" in gitignore
    assert ".canon-ledger/backups/.integrity-key" in gitignore


def _run_git(project_root, *args):
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _configure_git_identity(project_root):
    assert _run_git(project_root, "config", "user.name", "Test Author").returncode == 0
    assert _run_git(project_root, "config", "user.email", "author@example.com").returncode == 0


def _persist_accepted_bound_commit(project_root, chapter=1):
    chapter_file = project_root / "正文" / f"第{chapter:04d}章.md"
    chapter_file.parent.mkdir(parents=True, exist_ok=True)
    chapter_file.write_text(f"第{chapter}章最终正文\n", encoding="utf-8")
    binding = build_chapter_binding(project_root, chapter)
    write_current_chapter_contract(project_root, chapter)
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=chapter,
        review_result=standard_review(binding),
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
            "chapter_binding": binding,
        },
    )
    service.persist_commit(payload)
    return chapter_file


def test_backup_aborts_when_git_commit_fails_without_identity(tmp_path, monkeypatch, capsys):
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()

    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("USERPROFILE", str(isolated_home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    assert _run_git(project_root, "config", "--local", "user.useConfigOnly", "true").returncode == 0
    _run_git(project_root, "config", "--local", "--unset", "user.name")
    _run_git(project_root, "config", "--local", "--unset", "user.email")

    manuscript_dir = project_root / "正文"
    manuscript_dir.mkdir()
    (manuscript_dir / "第0001章-test.md").write_text("正文", encoding="utf-8")

    manager = GitBackupManager(str(project_root))

    assert manager.backup(1, "身份缺失") is False

    output = capsys.readouterr().out
    assert "备份失败" in output
    assert _run_git(project_root, "rev-parse", "--verify", "ch0001").returncode != 0


def test_rollback_restores_files_on_current_branch_with_new_commit(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    _configure_git_identity(project_root)

    manuscript_dir = project_root / "正文"
    manuscript_dir.mkdir()
    chapter_file = manuscript_dir / "第0001章-test.md"

    chapter_file.write_text("第一版", encoding="utf-8")
    assert _run_git(project_root, "add", ".").returncode == 0
    assert _run_git(project_root, "commit", "-m", "Chapter 1").returncode == 0
    assert _run_git(project_root, "tag", "ch0001").returncode == 0

    chapter_file.write_text("第二版", encoding="utf-8")
    assert _run_git(project_root, "add", ".").returncode == 0
    assert _run_git(project_root, "commit", "-m", "Chapter 2").returncode == 0
    assert _run_git(project_root, "tag", "ch0002").returncode == 0
    before_count = int(_run_git(project_root, "rev-list", "--count", "HEAD").stdout.strip())

    manager = GitBackupManager(str(project_root))

    assert manager.rollback(1) is True

    assert _run_git(project_root, "symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert chapter_file.read_text(encoding="utf-8") == "第一版"
    after_count = int(_run_git(project_root, "rev-list", "--count", "HEAD").stdout.strip())
    assert after_count == before_count + 1
    assert "rollback: 恢复到 ch0001 备份点" in _run_git(project_root, "log", "-1", "--format=%s").stdout


def test_local_backup_copies_manuscript_when_git_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)

    canon_ledger_dir = tmp_path / ".canon-ledger"
    manuscript_dir = tmp_path / "正文"
    outline_dir = tmp_path / "大纲"
    settings_dir = tmp_path / "设定集"
    canon_ledger_dir.mkdir()
    manuscript_dir.mkdir()
    outline_dir.mkdir()
    settings_dir.mkdir()
    (canon_ledger_dir / "state.json").write_text('{"current_chapter": 1}', encoding="utf-8")
    (manuscript_dir / "第0001章-x.md").write_text("正文内容", encoding="utf-8")
    (outline_dir / "第0001章.md").write_text("大纲内容", encoding="utf-8")
    (settings_dir / "人物.md").write_text("设定内容", encoding="utf-8")

    manager = GitBackupManager(str(tmp_path))

    assert manager.backup(1) is True

    snapshots = sorted((canon_ledger_dir / "backups").glob("snapshot_ch0001_*"))
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert (snapshot / "正文" / "第0001章-x.md").read_text(encoding="utf-8") == "正文内容"
    assert (snapshot / "大纲" / "第0001章.md").read_text(encoding="utf-8") == "大纲内容"
    assert (snapshot / "设定集" / "人物.md").read_text(encoding="utf-8") == "设定内容"
    assert (snapshot / ".canon-ledger" / "state.json").read_text(encoding="utf-8") == '{"current_chapter": 1}'

    for chapter in range(2, 13):
        assert manager.backup(chapter) is True

    snapshots = sorted((canon_ledger_dir / "backups").glob("snapshot_ch*"))
    assert len(snapshots) == 10
    assert snapshot not in snapshots


def test_backup_with_required_accepted_binding_succeeds(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    _configure_git_identity(project_root)
    _persist_accepted_bound_commit(project_root, chapter=1)

    manager = GitBackupManager(str(project_root))

    assert manager.backup(1, require_accepted_binding=True) is True
    assert _run_git(project_root, "rev-parse", "--verify", "ch0001").returncode == 0
    receipt = json.loads(
        (project_root / ".canon-ledger" / "backups" / "ch0001.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["schema_version"] == "canon-ledger-backup-receipt/v1"
    assert receipt["chapter"] == 1
    assert receipt["mode"] == "git"


def test_backup_with_required_binding_rejects_changed_manuscript(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    _configure_git_identity(project_root)
    chapter_file = _persist_accepted_bound_commit(project_root, chapter=1)
    chapter_file.write_text("第1章正文已在 commit 后修改\n", encoding="utf-8")

    manager = GitBackupManager(str(project_root))

    assert manager.backup(1, require_accepted_binding=True) is False
    assert _run_git(project_root, "rev-parse", "--verify", "ch0001").returncode != 0


def test_backup_never_moves_existing_chapter_tag(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    _configure_git_identity(project_root)
    _persist_accepted_bound_commit(project_root, chapter=1)
    manager = GitBackupManager(str(project_root))

    assert manager.backup(1, require_accepted_binding=True) is True
    original_tag_target = _run_git(project_root, "rev-parse", "ch0001").stdout.strip()
    (project_root / "unrelated.md").write_text("后续提交\n", encoding="utf-8")
    assert _run_git(project_root, "add", "unrelated.md").returncode == 0
    assert _run_git(project_root, "commit", "-m", "unrelated follow-up").returncode == 0
    head_before_repeat = _run_git(project_root, "rev-parse", "HEAD").stdout.strip()

    assert manager.backup(1, require_accepted_binding=True) is True
    assert _run_git(project_root, "rev-parse", "ch0001").stdout.strip() == original_tag_target
    assert _run_git(project_root, "rev-parse", "HEAD").stdout.strip() == head_before_repeat


def test_strict_git_backup_forces_recovery_files_ignored_by_project(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    assert _run_git(project_root, "init", "-b", "main").returncode == 0
    _configure_git_identity(project_root)
    _persist_accepted_bound_commit(project_root, chapter=1)
    (project_root / ".gitignore").write_text(
        "正文/\n.story-system/commits/\n",
        encoding="utf-8",
    )
    manager = GitBackupManager(str(project_root))

    assert manager.backup(1, require_accepted_binding=True) is True
    assert (
        _run_git(project_root, "show", "ch0001:正文/第0001章.md").stdout
        == "第1章最终正文\n"
    )
    assert (
        _run_git(
            project_root,
            "show",
            "ch0001:.story-system/commits/chapter_001.commit.json",
        ).returncode
        == 0
    )


def _prepare_local_consistency_project(project_root, monkeypatch):
    monkeypatch.setattr(backup_manager, "is_git_available", lambda: False)
    (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
    (project_root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "project_info": {"title": "雾城旧约"},
                "progress": {"current_chapter": 1},
                "长期事实": {"守门人": "仍在城北"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_root / "大纲").mkdir()
    (project_root / "设定集").mkdir()
    (project_root / "大纲" / "总纲.md").write_text("守住旧约。\n", encoding="utf-8")
    (project_root / "设定集" / "人物.md").write_text("守门人不会离城。\n", encoding="utf-8")
    _persist_accepted_bound_commit(project_root, chapter=1)
    (project_root / ".story-system" / "timeline").mkdir(parents=True)
    (project_root / ".story-system" / "timeline" / "events.json").write_text(
        '{"第一章":"守门人仍在城北"}\n',
        encoding="utf-8",
    )
    (project_root / ".story-system" / "foreshadowing").mkdir(parents=True)
    (project_root / ".story-system" / "foreshadowing" / "open.json").write_text(
        '{"伏笔":"铜铃尚未揭晓"}\n',
        encoding="utf-8",
    )
    return GitBackupManager(str(project_root))


def test_strict_local_backup_contains_complete_consistency_state(tmp_path, monkeypatch):
    manager = _prepare_local_consistency_project(tmp_path, monkeypatch)
    (tmp_path / ".canon-ledger" / "run_ledger.json").write_text(
        '{"write":{"第一章":"完成"}}\n',
        encoding="utf-8",
    )

    assert manager.backup(1, require_accepted_binding=True) is True

    receipt = json.loads(manager._receipt_path(1).read_text(encoding="utf-8"))
    snapshot = tmp_path / ".canon-ledger" / "backups" / receipt["snapshot"]
    manifest = json.loads(
        (snapshot / "snapshot.manifest.json").read_text(encoding="utf-8")
    )
    assert receipt["schema_version"] == "canon-ledger-backup-receipt/v2"
    assert receipt["signature_algorithm"] == "hmac-sha256"
    assert manifest["snapshot_kind"] == "complete-project-consistency-state"
    assert (snapshot / ".story-system" / "timeline" / "events.json").is_file()
    assert (snapshot / ".story-system" / "foreshadowing" / "open.json").is_file()
    assert (snapshot / ".story-system" / "commits" / "chapter_001.commit.json").is_file()
    assert (snapshot / ".canon-ledger" / "state.json").is_file()
    assert (snapshot / ".canon-ledger" / "run_ledger.json").is_file()
    assert not (snapshot / ".canon-ledger" / "backups").exists()


def test_local_rollback_restores_old_chapter_and_removes_later_facts(tmp_path, monkeypatch):
    manager = _prepare_local_consistency_project(tmp_path, monkeypatch)
    assert manager.backup(1, require_accepted_binding=True) is True

    _persist_accepted_bound_commit(tmp_path, chapter=2)
    (tmp_path / ".story-system" / "timeline" / "events.json").write_text(
        '{"第二章":"守门人已经离城"}\n',
        encoding="utf-8",
    )
    (tmp_path / ".story-system" / "timeline" / "future-only.json").write_text(
        '{"后来":"城门失守"}\n',
        encoding="utf-8",
    )
    (tmp_path / ".canon-ledger" / "future-only.json").write_text(
        '{"错误事实":"铜铃已经揭晓"}\n',
        encoding="utf-8",
    )
    (tmp_path / ".canon-ledger" / "state.json").write_text(
        '{"progress":{"current_chapter":2},"错误事实":"守门人离城"}\n',
        encoding="utf-8",
    )

    assert manager.rollback(1) is True

    assert (tmp_path / "正文" / "第0001章.md").is_file()
    assert not (tmp_path / "正文" / "第0002章.md").exists()
    assert not (
        tmp_path / ".story-system" / "commits" / "chapter_002.commit.json"
    ).exists()
    assert json.loads(
        (tmp_path / ".story-system" / "timeline" / "events.json").read_text(
            encoding="utf-8"
        )
    ) == {"第一章": "守门人仍在城北"}
    assert not (
        tmp_path / ".story-system" / "timeline" / "future-only.json"
    ).exists()
    assert not (tmp_path / ".canon-ledger" / "future-only.json").exists()
    rebuilt = json.loads(
        (tmp_path / ".canon-ledger" / "projection_rebuild.json").read_text(
            encoding="utf-8"
        )
    )
    assert rebuilt["status"] == "complete"
    rescue = list(
        (tmp_path / ".canon-ledger" / "backups").glob(
            "rescue_before_restore_ch0001_*"
        )
    )
    assert len(rescue) == 1


def test_local_rollback_rejects_tampered_snapshot(tmp_path, monkeypatch):
    manager = _prepare_local_consistency_project(tmp_path, monkeypatch)
    assert manager.backup(1, require_accepted_binding=True) is True
    receipt = json.loads(manager._receipt_path(1).read_text(encoding="utf-8"))
    snapshot = tmp_path / ".canon-ledger" / "backups" / receipt["snapshot"]
    (snapshot / ".story-system" / "timeline" / "events.json").write_text(
        '{"第一章":"快照已被替换"}\n',
        encoding="utf-8",
    )
    current_marker = tmp_path / "正文" / "当前内容不应被改动.md"
    current_marker.write_text("保留当前内容\n", encoding="utf-8")

    assert manager.rollback(1) is False

    assert current_marker.read_text(encoding="utf-8") == "保留当前内容\n"
    assert not list(
        (tmp_path / ".canon-ledger" / "backups").glob(
            "rescue_before_restore_ch0001_*"
        )
    )


def test_local_rollback_rejects_external_receipt_and_empty_directory(tmp_path, monkeypatch):
    manager = _prepare_local_consistency_project(tmp_path, monkeypatch)
    backup_dir = tmp_path / ".canon-ledger" / "backups"
    empty_snapshot = backup_dir / "snapshot_ch0001_external"
    empty_snapshot.mkdir(parents=True)
    manager._receipt_path(1).write_text(
        json.dumps(
            {
                "schema_version": "canon-ledger-backup-receipt/v2",
                "chapter": 1,
                "mode": "local",
                "snapshot": empty_snapshot.name,
                "manifest_path": "snapshot.manifest.json",
                "manifest_sha256": "0" * 64,
                "signature_algorithm": "hmac-sha256",
                "signature": "0" * 64,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert manager.rollback(1) is False
    assert (tmp_path / "正文" / "第0001章.md").is_file()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

from .review_test_helpers import standard_review


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def _load_webnovel_module():
    _ensure_scripts_on_path()
    import data_modules.webnovel as webnovel_module

    return webnovel_module


def _make_cli_init_ready_project(project_root: Path) -> None:
    dirs = (
        ".webnovel/backups",
        ".webnovel/archive",
        ".webnovel/summaries",
        "设定集",
        "大纲",
        "正文",
        "审查报告",
    )
    for rel in dirs:
        (project_root / rel).mkdir(parents=True, exist_ok=True)

    (project_root / ".webnovel" / "state.json").write_text(
        json.dumps(
            {
                "project_info": {"title": "测试书", "genre": "玄幻"},
                "progress": {"current_chapter": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
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


def test_init_does_not_resolve_existing_project_root(monkeypatch):
    module = _load_webnovel_module()

    called = {}

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    def _fail_resolve(_explicit_project_root=None):
        raise AssertionError("init 子命令不应触发 project_root 解析")

    monkeypatch.setenv("WEBNOVEL_PROJECT_ROOT", r"D:\invalid\root")
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(module, "_resolve_root", _fail_resolve)
    monkeypatch.setattr(sys, "argv", ["webnovel", "init", "proj-dir", "测试书", "修仙"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "init_project.py"
    assert called["argv"] == ["proj-dir", "测试书", "修仙"]


def test_extract_context_forwards_with_resolved_project_root(monkeypatch, tmp_path):
    module = _load_webnovel_module()

    book_root = (tmp_path / "book").resolve()
    called = {}

    def _fake_resolve(explicit_project_root=None):
        return book_root

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_resolve_root", _fake_resolve)
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(tmp_path),
            "extract-context",
            "--chapter",
            "12",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "extract_chapter_context.py"
    assert called["argv"] == [
        "--project-root",
        str(book_root),
        "--chapter",
        "12",
        "--format",
        "json",
    ]


def test_memory_contract_forwards_context_budget(monkeypatch, tmp_path):
    module = _load_webnovel_module()
    book_root = (tmp_path / "book").resolve()
    called = {}

    monkeypatch.setattr(module, "_resolve_root", lambda _explicit=None: book_root)

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(book_root),
            "memory-contract",
            "load-context",
            "--chapter",
            "7",
            "--budget-tokens",
            "2048",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "memory_cli.py"
    assert called["argv"] == [
        "--project-root",
        str(book_root),
        "load-context",
        "--chapter",
        "7",
        "--budget-tokens",
        "2048",
    ]


def test_backup_forwards_resolved_book_root_from_parent_workspace(monkeypatch, tmp_path):
    module = _load_webnovel_module()

    workspace_root = (tmp_path / "workspace").resolve()
    book_root = (workspace_root / "book").resolve()
    (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
    (book_root / ".git").mkdir(parents=True, exist_ok=True)
    (book_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (book_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    called = {}

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.chdir(workspace_root)
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(workspace_root),
            "backup",
            "--chapter",
            "2",
            "--chapter-title",
            "第二章",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "backup_manager.py"
    assert called["argv"] == [
        "--project-root",
        str(book_root),
        "--chapter",
        "2",
        "--chapter-title",
        "第二章",
    ]


def test_webnovel_story_system_forwards_with_resolved_project_root(monkeypatch, tmp_path):
    module = _load_webnovel_module()

    book_root = (tmp_path / "book").resolve()
    called = {}

    def _fake_resolve(explicit_project_root=None):
        return book_root

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_resolve_root", _fake_resolve)
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(tmp_path),
            "story-system",
            "玄幻退婚流",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "story_system.py"
    assert called["argv"][:2] == ["--project-root", str(book_root)]


def test_webnovel_story_system_runtime_forwards(monkeypatch, tmp_path):
    module = _load_webnovel_module()

    project_root = (tmp_path / "book").resolve()
    called = {}

    def _fake_resolve(explicit_project_root=None):
        return project_root

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_resolve_root", _fake_resolve)
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "story-system",
            "玄幻退婚流",
            "--emit-runtime-contracts",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "story_system.py"
    assert "--emit-runtime-contracts" in called["argv"]


def test_webnovel_commit_forwards(monkeypatch, tmp_path):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    called = {}

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(sys, "argv", ["webnovel", "--project-root", str(project_root), "chapter-commit", "--chapter", "3"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "chapter_commit.py"


def test_webnovel_story_events_forwards(monkeypatch, tmp_path):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    called = {}

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        ["webnovel", "--project-root", str(project_root), "story-events", "--chapter", "3"],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "story_events.py"


def test_preflight_succeeds_for_valid_project_root(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()

    project_root = tmp_path / "book"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["webnovel", "--project-root", str(project_root), "preflight"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    assert int(exc.value.code or 0) == 0
    assert "OK project_root" in captured.out
    assert str(project_root.resolve()) in captured.out


def test_preflight_fails_when_required_scripts_are_missing(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()

    project_root = tmp_path / "book"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    fake_scripts_dir = tmp_path / "fake-scripts"
    fake_scripts_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(module, "_scripts_dir", lambda: fake_scripts_dir)
    monkeypatch.setattr(sys, "argv", ["webnovel", "--project-root", str(project_root), "preflight", "--format", "json"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    assert int(exc.value.code or 0) == 1
    assert '"ok": false' in captured.out
    assert '"name": "entry_script"' in captured.out


def test_preflight_includes_story_runtime_health(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()

    project_root = tmp_path / "book"
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["webnovel", "--project-root", str(project_root), "preflight", "--format", "json"],
    )

    with pytest.raises(SystemExit):
        module.main()

    captured = capsys.readouterr()
    assert '"story_runtime"' in captured.out
    assert '"mainline_ready"' in captured.out


def test_project_status_cli_outputs_json_without_reusing_status(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)

    monkeypatch.setattr(
        sys,
        "argv",
        ["webnovel", "--project-root", str(project_root), "project-status", "--format", "json"],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert int(exc.value.code or 0) == 0
    assert report["schema_version"] == "webnovel-project-status/v1"
    assert report["project"] == "测试书"
    assert report["phase"] == "init_ready"


def test_user_report_cli_outputs_json(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "user-report",
            "--stage",
            "init",
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert int(exc.value.code or 0) == 0
    assert report["schema_version"] == "webnovel-user-report/v1"
    assert report["stage"] == "init"
    assert report["overall_status"] == "completed"


def test_run_ledger_cli_records_and_reports_resume(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)
    chapter_file = project_root / "正文" / "第0001章.md"
    chapter_file.write_text("正文\n", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "run-ledger",
            "record-write-step",
            "--chapter",
            "1",
            "--step",
            "draft",
            "--status",
            "completed",
            "--outputs-json",
            json.dumps({"chapter_file": str(chapter_file)}, ensure_ascii=False),
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    entry = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert entry["step"] == "draft"
    assert entry["outputs"]["chapter_file"]["exists"] is True

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "run-ledger",
            "write-resume",
            "--chapter",
            "1",
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    resume = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert resume["schema_version"] == "webnovel-run-ledger/v1"
    assert resume["steps"][0]["step"] == "draft"
    assert resume["steps"][0]["action"] == "skip"


def test_run_log_cli_redacts_sensitive_payload(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "run-log",
            "--event",
            "failure",
            "--payload-json",
            json.dumps({"api_key": "secret-value", "message": "ok"}, ensure_ascii=False),
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    result = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    log_text = Path(result["path"]).read_text(encoding="utf-8")
    assert "secret-value" not in log_text
    assert "<redacted>" in log_text


def test_doctor_cli_reports_missing_init_file(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)
    (project_root / "大纲" / "总纲.md").unlink()

    monkeypatch.setattr(
        sys,
        "argv",
        ["webnovel", "--project-root", str(project_root), "doctor", "--format", "json"],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert int(exc.value.code or 0) == 1
    assert report["schema_version"] == "webnovel-doctor/v1"
    assert report["ok"] is False
    assert any(item["id"] == "file.required.大纲/总纲.md" for item in report["checks"])


def test_status_command_still_forwards_to_status_reporter(monkeypatch, tmp_path):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)
    called = {}

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(sys, "argv", ["webnovel", "--project-root", str(project_root), "status", "--focus", "all"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "status_reporter.py"


def test_write_gate_cli_runs_prewrite(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)
    for path, payload in (
        (project_root / ".story-system" / "MASTER_SETTING.json", {"meta": {"contract_type": "MASTER_SETTING"}}),
        (project_root / ".story-system" / "volumes" / "volume_001.json", {"meta": {"volume": 1}}),
        (
            project_root / ".story-system" / "chapters" / "chapter_001.json",
            {
                "chapter_directive": {
                    "goal": "确认第一章已经建立的事实",
                    "must_cover_nodes": [],
                }
            },
        ),
        (project_root / ".story-system" / "reviews" / "chapter_001.review.json", {"blocking_rules": []}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "write-gate",
            "--chapter",
            "1",
            "--stage",
            "prewrite",
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert int(exc.value.code or 0) == 0
    assert report["schema_version"] == "webnovel-write-gate/v1"
    assert report["stage"] == "prewrite"
    assert report["ok"] is True


def test_projections_retry_cli_runs(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    from data_modules.chapter_commit_service import ChapterCommitService
    from data_modules.chapter_content_binding import build_chapter_binding

    project_root = tmp_path / "book"
    _make_cli_init_ready_project(project_root)
    chapter_path = project_root / "正文" / "第0001章.md"
    chapter_path.write_text("第1章最终正文", encoding="utf-8")
    binding = build_chapter_binding(project_root, 1)
    service = ChapterCommitService(project_root)
    service.persist_commit(
        service.build_commit(
            chapter=1,
            review_result=standard_review(binding, blocking_count=1),
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
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(project_root),
            "projections",
            "retry",
            "--chapter",
            "1",
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert int(exc.value.code or 0) == 0
    assert report["schema_version"] == "webnovel-projections/v1"
    assert report["projection_status"]["state"] == "done"


def test_where_reports_empty_workspace_without_traceback(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(workspace)
    monkeypatch.delenv("WEBNOVEL_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    monkeypatch.setenv("WEBNOVEL_CLAUDE_HOME", str(tmp_path / "empty-claude-home"))
    monkeypatch.setattr(sys, "argv", ["webnovel", "where"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    assert int(exc.value.code or 0) == 1
    assert "还没有激活的书项目" in captured.err
    assert "Traceback" not in captured.err


def test_preflight_reports_empty_workspace_without_traceback(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(workspace)
    monkeypatch.delenv("WEBNOVEL_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    monkeypatch.setenv("WEBNOVEL_CLAUDE_HOME", str(tmp_path / "empty-claude-home"))
    monkeypatch.setattr(sys, "argv", ["webnovel", "preflight", "--format", "json"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert int(exc.value.code or 0) == 1
    assert report["ok"] is False
    assert "还没有激活的书项目" in report["project_root_error"]
    assert "Traceback" not in captured.err


def test_quality_trend_report_writes_to_book_root_when_input_is_workspace_root(tmp_path, monkeypatch):
    _ensure_scripts_on_path()
    import quality_trend_report as quality_trend_report_module

    workspace_root = (tmp_path / "workspace").resolve()
    book_root = (workspace_root / "凡人资本论").resolve()

    (workspace_root / ".claude").mkdir(parents=True, exist_ok=True)
    (workspace_root / ".claude" / ".webnovel-current-project").write_text(str(book_root), encoding="utf-8")

    (book_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (book_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")

    output_path = workspace_root / "report.md"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "quality_trend_report",
            "--project-root",
            str(workspace_root),
            "--limit",
            "1",
            "--output",
            str(output_path),
        ],
    )

    quality_trend_report_module.main()

    assert output_path.is_file()
    assert (book_root / ".webnovel" / "index.db").is_file()
    assert not (workspace_root / ".webnovel" / "index.db").exists()






def test_review_pipeline_builds_artifacts(tmp_path):
    _ensure_scripts_on_path()
    import review_pipeline as review_pipeline_module
    from data_modules.chapter_content_binding import build_chapter_binding

    project_root = (tmp_path / "book").resolve()
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    chapter_path = project_root / "正文" / "第0020章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("第20章正文", encoding="utf-8")
    chapter_binding = build_chapter_binding(project_root, 20)
    chapter_binding_path = project_root / ".webnovel" / "tmp" / "chapter_binding.json"
    chapter_binding_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_binding_path.write_text(
        json.dumps(chapter_binding, ensure_ascii=False),
        encoding="utf-8",
    )

    review_results_path = tmp_path / "review_results.json"
    raw_review = standard_review(chapter_binding, blocking_count=1)
    raw_review["issues"].append(
        {
            "severity": "medium",
            "category": "setting",
            "location": "第五段",
            "description": "本章使用了尚未获得的通行令牌。",
            "evidence": "既有记录中主角尚未获得该令牌。",
            "fix_hint": "补充令牌来源，或改用已经持有的凭证。",
            "blocking": False,
        }
    )
    raw_review["summary"] = "本章发现一个阻断问题和一个非阻断问题。"
    review_results_path.write_text(
        json.dumps(raw_review, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = review_pipeline_module.build_review_artifacts(
        project_root=project_root,
        chapter=20,
        review_results_path=review_results_path,
        report_file="审查报告/第20章.md",
        chapter_binding_path=chapter_binding_path,
    )

    assert payload["review_result"]["blocking_count"] == 1
    assert payload["review_result"]["has_blocking"] is True
    assert payload["review_result"]["issues_count"] == 2
    assert payload["review_audit"]["start_chapter"] == 20
    assert payload["review_audit"]["end_chapter"] == 20
    assert payload["review_audit"]["issues_count"] == 2
    assert payload["review_audit"]["blocking_count"] == 1
    assert payload["review_audit"]["severity_counts"]["critical"] == 1
    assert payload["review_audit"]["severity_counts"]["medium"] == 1
    assert payload["review_audit"]["critical_issues"] == ["第1个已确认的事实冲突。"]
    assert "overall_score" not in payload["review_audit"]
    assert payload["review_audit"]["report_file"] == "审查报告/第20章.md"

    persisted_review = json.loads(review_results_path.read_text(encoding="utf-8"))
    assert persisted_review["chapter"] == 20
    assert persisted_review["issues_count"] == 2
    assert persisted_review["blocking_count"] == 1
    assert persisted_review["has_blocking"] is True


def test_review_pipeline_forwards_with_resolved_project_root(monkeypatch, tmp_path):
    module = _load_webnovel_module()

    book_root = (tmp_path / "book").resolve()
    review_results = (tmp_path / "review_results.json").resolve()
    chapter_binding = (tmp_path / "chapter_binding.json").resolve()
    called = {}

    def _fake_resolve(explicit_project_root=None):
        return book_root

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_resolve_root", _fake_resolve)
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(tmp_path),
            "review-pipeline",
            "--chapter",
            "18",
            "--review-results",
            str(review_results),
            "--chapter-binding",
            str(chapter_binding),
            "--audit-out",
            str(tmp_path / "audit.json"),
            "--report-file",
            "审查报告/第18章.md",
            "--save-audit",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "review_pipeline.py"
    assert called["argv"] == [
        "--project-root",
        str(book_root),
        "--chapter",
        "18",
        "--review-results",
        str(review_results),
        "--chapter-binding",
        str(chapter_binding),
        "--audit-out",
        str(tmp_path / "audit.json"),
        "--report-file",
        "审查报告/第18章.md",
        "--save-audit",
    ]


def test_review_pipeline_minimal_cli_writes_explicit_skipped_artifact(
    monkeypatch,
    tmp_path,
):
    import review_pipeline as review_pipeline_module
    from data_modules.artifact_validator import validate_review_result
    from data_modules.chapter_content_binding import build_chapter_binding

    project_root = tmp_path / "测试作品"
    chapter_file = project_root / "正文" / "第0006章.md"
    chapter_file.parent.mkdir(parents=True, exist_ok=True)
    chapter_file.write_text("本章正文只用于验证最简审查凭据。\n", encoding="utf-8")
    chapter_binding = build_chapter_binding(project_root, 6)
    chapter_binding_path = project_root / ".webnovel" / "tmp" / "chapter_binding.json"
    chapter_binding_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_binding_path.write_text(
        json.dumps(chapter_binding, ensure_ascii=False),
        encoding="utf-8",
    )
    review_results_path = project_root / ".webnovel" / "tmp" / "review_results.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "review_pipeline.py",
            "--project-root",
            str(project_root),
            "--chapter",
            "6",
            "--review-results",
            str(review_results_path),
            "--chapter-binding",
            str(chapter_binding_path),
            "--minimal",
        ],
    )

    review_pipeline_module.main()

    persisted = json.loads(review_results_path.read_text(encoding="utf-8"))
    assert persisted["review_mode"] == "minimal"
    assert persisted["review_status"] == "skipped"
    assert persisted["review_skipped"] is True
    assert persisted["review_degraded"] is True
    assert persisted["reviewed_dimensions"] == []
    assert persisted["skipped_dimensions"] == [
        "setting",
        "timeline",
        "continuity",
        "character",
        "logic",
    ]
    assert validate_review_result(review_results_path)["ok"] is True


def test_review_pipeline_rejects_changed_manuscript_before_side_effects(tmp_path):
    import review_pipeline as review_pipeline_module
    from data_modules.chapter_content_binding import build_chapter_binding

    project_root = tmp_path / "book"
    chapter_file = project_root / "正文" / "第0020章.md"
    chapter_file.parent.mkdir(parents=True, exist_ok=True)
    chapter_file.write_text("待审正文 v1\n", encoding="utf-8")
    binding = build_chapter_binding(project_root, 20)
    binding_path = project_root / ".webnovel" / "tmp" / "chapter_binding.json"
    binding_path.parent.mkdir(parents=True, exist_ok=True)
    binding_path.write_text(json.dumps(binding, ensure_ascii=False), encoding="utf-8")
    review_path = project_root / ".webnovel" / "tmp" / "review_results.json"
    original_review = {
        "chapter": 20,
        "chapter_binding": binding,
        "issues": [
            {
                "severity": "high",
                "category": "setting",
                "evidence": "角色使用了尚未获得的通行令牌。",
            }
        ],
        "summary": "待审",
    }
    review_path.write_text(
        json.dumps(original_review, ensure_ascii=False),
        encoding="utf-8",
    )
    chapter_file.write_text("待审正文 v2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="chapter_content_hash_mismatch"):
        review_pipeline_module.build_review_artifacts(
            project_root=project_root,
            chapter=20,
            review_results_path=review_path,
            report_file="审查报告/第20章.md",
            chapter_binding_path=binding_path,
        )

    assert json.loads(review_path.read_text(encoding="utf-8")) == original_review
    assert not (project_root / ".story-system" / "anti_patterns.json").exists()
    assert not (project_root / "审查报告" / "第20章.md").exists()


def test_project_memory_forwards_with_resolved_project_root(monkeypatch, tmp_path):
    module = _load_webnovel_module()

    book_root = (tmp_path / "book").resolve()
    called = {}

    def _fake_resolve(explicit_project_root=None):
        return book_root

    def _fake_run_script(script_name, argv):
        called["script_name"] = script_name
        called["argv"] = list(argv)
        return 0

    monkeypatch.setattr(module, "_resolve_root", _fake_resolve)
    monkeypatch.setattr(module, "_run_script", _fake_run_script)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(tmp_path),
            "project-memory",
            "add-pattern",
            "--pattern-type",
            "timeline",
            "--description",
            "离开霜河城后，后续时间锚不得早于霜月初三。",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    assert called["script_name"] == "project_memory.py"
    assert called["argv"] == [
        "--project-root",
        str(book_root),
        "add-pattern",
        "--pattern-type",
        "timeline",
        "--description",
        "离开霜河城后，后续时间锚不得早于霜月初三。",
    ]


def test_project_memory_rejects_style_content(tmp_path):
    _ensure_scripts_on_path()
    import project_memory as project_memory_module

    project_root = (tmp_path / "book").resolve()
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 3}}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不能包含文风"):
        project_memory_module.add_pattern(
            project_root,
            pattern_type="timeline",
            description="正文使用短句，并让对白更口语化。",
            category="写作规范",
            importance="high",
        )

    assert not (project_root / ".webnovel" / "project_memory.json").exists()
    assert not (project_root / ".webnovel" / "memory_scratchpad.json").exists()


def test_project_memory_writes_a_consumable_consistency_rule(tmp_path):
    _ensure_scripts_on_path()
    import project_memory as project_memory_module

    project_root = (tmp_path / "book").resolve()
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 3}}, ensure_ascii=False),
        encoding="utf-8",
    )
    description = "角色离开霜河城后，后续时间锚必须晚于霜月初三。"

    result = project_memory_module.add_pattern(
        project_root,
        pattern_type="timeline",
        description=description,
        category="时间线",
        importance="high",
    )

    memory_path = project_root / ".webnovel" / "memory_scratchpad.json"
    payload = json.loads(memory_path.read_text(encoding="utf-8"))
    rules = payload["world_rules"]
    assert result["status"] == "success"
    assert result["path"] == str(memory_path)
    assert len(rules) == 1
    assert rules[0]["value"] == description
    assert rules[0]["source_chapter"] == 3
    assert rules[0]["payload"]["origin"] == "/webnovel-learn"

    from data_modules.config import DataModulesConfig
    from data_modules.memory.orchestrator import MemoryOrchestrator

    pack = MemoryOrchestrator(
        DataModulesConfig.from_project_root(project_root)
    ).build_memory_pack(4, include_soft=False)
    assert any(
        item.get("value") == description
        for item in pack.get("hard_constraints") or []
    )


def test_review_pipeline_main_creates_output_directories(tmp_path):
    _ensure_scripts_on_path()
    import review_pipeline as review_pipeline_module
    from data_modules.chapter_content_binding import build_chapter_binding

    project_root = (tmp_path / "book").resolve()
    (project_root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (project_root / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    chapter_path = project_root / "正文" / "第0009章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("第9章正文", encoding="utf-8")
    chapter_binding = build_chapter_binding(project_root, 9)
    chapter_binding_path = project_root / ".webnovel" / "tmp" / "chapter_binding.json"
    chapter_binding_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_binding_path.write_text(
        json.dumps(chapter_binding, ensure_ascii=False),
        encoding="utf-8",
    )

    review_results_path = tmp_path / "review_results.json"
    raw_review = standard_review(chapter_binding)
    raw_review["issues"] = [
        {
            "severity": "low",
            "category": "logic",
            "location": "第一段",
            "description": "行动结果缺少已建立的前置条件。",
            "evidence": "角色尚未获得开门所需的钥匙。",
            "fix_hint": "补充钥匙来源或调整进入方式。",
            "blocking": False,
        }
    ]
    raw_review["summary"] = "本章发现一处轻微事实问题。"
    review_results_path.write_text(
        json.dumps(raw_review, ensure_ascii=False),
        encoding="utf-8",
    )

    audit_out = project_root / ".webnovel" / "tmp" / "review" / "audit.json"
    report_file = project_root / "审查报告" / "第9章审查报告.md"

    old_argv = sys.argv
    sys.argv = [
        "review_pipeline",
        "--project-root",
        str(project_root),
        "--chapter",
        "9",
        "--review-results",
        str(review_results_path),
        "--chapter-binding",
        str(chapter_binding_path),
        "--audit-out",
        str(audit_out),
        "--report-file",
        "审查报告/第9章审查报告.md",
        "--save-audit",
    ]
    try:
        review_pipeline_module.main()
    finally:
        sys.argv = old_argv

    assert audit_out.is_file()
    assert report_file.is_file()
    report_text = report_file.read_text(encoding="utf-8")
    assert "# 第9章审查报告" in report_text
    assert "## 作者视图" in report_text
    assert "本章结论：⚠️建议改" in report_text
    assert "行动结果缺少已建立的前置条件" in report_text
    assert "## 其他问题" in report_text

    persisted_review = json.loads(review_results_path.read_text(encoding="utf-8"))
    assert persisted_review["chapter"] == 9
    assert persisted_review["issues_count"] == 1
    assert persisted_review["blocking_count"] == 0
    assert persisted_review["has_blocking"] is False

    import sqlite3

    with sqlite3.connect(project_root / ".webnovel" / "index.db") as conn:
        row = conn.execute(
            "SELECT chapter, review_mode, report_file FROM review_audits"
        ).fetchone()
    assert row == (9, "standard", "审查报告/第9章审查报告.md")


def test_webnovel_skill_flow_runs_story_contract_context_and_review_pipeline_with_stubbed_vector_model(
    monkeypatch, tmp_path, capsys
):
    _ensure_scripts_on_path()
    module = _load_webnovel_module()
    import data_modules.rag_adapter as rag_module
    from data_modules.config import DataModulesConfig

    project_root = (tmp_path / "book").resolve()
    cfg = DataModulesConfig.from_project_root(project_root)
    cfg.ensure_dirs()

    cfg.state_file.write_text(
        json.dumps(
            {
                "project": {"genre": "xuanhuan"},
                "progress": {
                    "current_chapter": 2,
                    "total_words": 9000,
                    "volumes_planned": [{"volume": 1, "chapters_range": "1-20"}],
                },
                "protagonist_state": {
                    "name": "萧炎",
                    "location": {"current": "天云宗外院"},
                    "power": {"realm": "斗者", "layer": 9},
                },
                "chapter_meta": {},
                "disambiguation_warnings": [],
                "disambiguation_pending": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    outline_dir = project_root / "大纲"
    outline_dir.mkdir(parents=True, exist_ok=True)
    (outline_dir / "第1卷-详细大纲.md").write_text(
        "\n".join(
            [
                "### 第3章：试炼冲突",
                "本章将聚焦萧炎与药老关系冲突，并回收旧线索真相。",
                "CBN：萧炎进入试炼场",
                "CPNs：",
                "- 药老提醒规则异常",
                "- 萧炎发现师徒分歧",
                "CEN：萧炎决定暂缓冲突",
                "必须覆盖节点：发现规则异常",
                "本章禁区：不可提前摊牌",
            ]
        ),
        encoding="utf-8",
    )

    refs_dir = project_root / ".claude" / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    (refs_dir / "genre-profiles.md").write_text("## xuanhuan\n- 升级线清晰", encoding="utf-8")
    (refs_dir / "reading-power-taxonomy.md").write_text("## xuanhuan\n- 冲突钩优先", encoding="utf-8")

    calls = {"embed": 0, "embed_batch": 0, "rerank": 0}

    class _StubVectorClient:
        async def embed(self, texts):
            calls["embed"] += 1
            return [[1.0, 0.0] for _ in texts]

        async def embed_batch(self, texts, skip_failures=True):
            calls["embed_batch"] += 1
            return [[1.0, 0.0] for _ in texts]

        async def rerank(self, query, documents, top_n=None):
            calls["rerank"] += 1
            limit = top_n or len(documents)
            return [
                {"index": i, "relevance_score": 1.0 / (i + 1)}
                for i in range(min(limit, len(documents)))
            ]

    monkeypatch.setenv("EMBED_API_KEY", "fake-embed-key")
    monkeypatch.setattr(rag_module, "get_client", lambda config: _StubVectorClient())

    from data_modules.chapter_commit_service import ChapterCommitService
    from data_modules.chapter_content_binding import build_chapter_binding
    from data_modules.vector_projection_writer import VectorProjectionWriter

    prior_chapter_path = project_root / "正文" / "第0002章.md"
    prior_chapter_path.parent.mkdir(parents=True, exist_ok=True)
    prior_chapter_path.write_text("萧炎与药老在试炼前关系紧张。", encoding="utf-8")
    prior_binding = build_chapter_binding(project_root, 2)
    prior_service = ChapterCommitService(project_root)
    prior_payload = prior_service.build_commit(
        chapter=2,
        review_result=standard_review(prior_binding),
        fulfillment_result={
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": prior_binding,
        },
        disambiguation_result={"pending": [], "chapter_binding": prior_binding},
        extraction_result={
            "accepted_events": [
                {
                    "event_id": "evt-ch2-relationship",
                    "chapter": 2,
                    "event_type": "relationship_changed",
                    "subject": "萧炎",
                    "payload": {"to_entity": "药老", "relationship_type": "紧张"},
                }
            ],
            "state_deltas": [],
            "entity_deltas": [],
            "chapter_binding": prior_binding,
        },
    )
    # 夹具会在下方写入这份精确投影；测试读取侧白名单前，先把提交标记为检索完成。
    prior_payload["projection_status"]["vector"] = "done"
    prior_chunks = VectorProjectionWriter(project_root)._collect_chunks(prior_payload)
    adapter = rag_module.RAGAdapter(cfg)
    asyncio.run(adapter.store_chunks(prior_chunks))
    prior_service.persist_commit(prior_payload)

    script_to_module = {
        "story_system.py": "story_system",
        "extract_chapter_context.py": "extract_chapter_context",
        "review_pipeline.py": "review_pipeline",
    }

    def _run_script_inproc(script_name, argv):
        module_name = script_to_module.get(script_name)
        if not module_name:
            raise AssertionError(f"unexpected script call: {script_name}")
        script_module = importlib.import_module(module_name)
        old_argv = sys.argv
        try:
            sys.argv = [module_name, *argv]
            script_module.main()
            return 0
        except SystemExit as exc:
            return int(exc.code or 0)
        finally:
            sys.argv = old_argv

    monkeypatch.setattr(module, "_run_script", _run_script_inproc)

    def _run_webnovel(argv):
        monkeypatch.setattr(sys, "argv", ["webnovel", *argv])
        with pytest.raises(SystemExit) as exc:
            module.main()
        return int(exc.value.code or 0)

    assert (
        _run_webnovel(
            [
                "--project-root",
                str(project_root),
                "story-system",
                "玄幻退婚流",
                "--chapter",
                "3",
                "--persist",
                "--emit-runtime-contracts",
                "--format",
                "json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    story_root = project_root / ".story-system"
    assert (story_root / "MASTER_SETTING.json").is_file()
    assert (story_root / "volumes" / "volume_001.json").is_file()
    assert (story_root / "reviews" / "chapter_003.review.json").is_file()

    assert (
        _run_webnovel(
            [
                "--project-root",
                str(project_root),
                "extract-context",
                "--chapter",
                "3",
                "--format",
                "json",
            ]
        )
        == 0
    )
    context_payload = json.loads(capsys.readouterr().out)
    assert (
        context_payload["story_contract"]["review_contract"]["meta"]["contract_type"]
        == "REVIEW_CONTRACT"
    )
    assert context_payload["prewrite_validation"]["blocking"] is False
    assert context_payload["rag_assist"]["invoked"] is True
    assert context_payload["rag_assist"]["hits"]
    assert calls["embed_batch"] >= 1
    assert calls["embed"] >= 1
    assert calls["rerank"] >= 1

    review_results_path = project_root / ".webnovel" / "tmp" / "review_results.json"
    review_results_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path = project_root / "正文" / "第0003章.md"
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("萧炎在试炼场发现规则异常。", encoding="utf-8")
    chapter_binding = build_chapter_binding(project_root, 3)
    chapter_binding_path = project_root / ".webnovel" / "tmp" / "chapter_binding.json"
    chapter_binding_path.write_text(
        json.dumps(chapter_binding, ensure_ascii=False),
        encoding="utf-8",
    )
    raw_review = standard_review(chapter_binding)
    raw_review["issues"] = [
        {
            "severity": "medium",
            "category": "continuity",
            "location": "第三段",
            "description": "上章留下的规则异常在本章没有得到事实回应。",
            "evidence": "上章确认规则异常，本章没有提及该事实。",
            "fix_hint": "补充角色对规则异常的回应。",
            "blocking": False,
        }
    ]
    raw_review["summary"] = "本章发现一个非阻断问题。"
    review_results_path.write_text(
        json.dumps(raw_review, ensure_ascii=False),
        encoding="utf-8",
    )
    audit_out = project_root / ".webnovel" / "tmp" / "review_audit.json"
    assert (
        _run_webnovel(
            [
                "--project-root",
                str(project_root),
                "review-pipeline",
                "--chapter",
                "3",
                "--review-results",
                str(review_results_path),
                "--chapter-binding",
                str(chapter_binding_path),
                "--audit-out",
                str(audit_out),
                "--report-file",
                "审查报告/第3章.md",
            ]
        )
        == 0
    )
    assert audit_out.is_file()
    audit_payload = json.loads(audit_out.read_text(encoding="utf-8"))
    assert audit_payload["issues_count"] == 1
    assert "overall_score" not in audit_payload


def test_subagent_models_cli_reads_project_file(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    book_root = tmp_path / "book"
    _make_cli_init_ready_project(book_root)
    (book_root / ".webnovel" / "subagent-models.json").write_text(
        json.dumps({"agents": {"data-agent": "kimi-k3-max"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path / "cursor-home"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "webnovel",
            "--project-root",
            str(book_root),
            "subagent-models",
            "--agent",
            "data-agent",
            "--format",
            "json",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agents"]["data-agent"]["model"] == "kimi-k3-max"
    assert payload["agents"]["data-agent"]["pass_to_task"] is True


def test_subagent_models_cli_without_book_still_succeeds(monkeypatch, tmp_path, capsys):
    module = _load_webnovel_module()
    monkeypatch.setenv("CURSOR_HOME", str(tmp_path / "cursor-home"))

    def _missing(_explicit_project_root=None):
        raise FileNotFoundError("no book")

    monkeypatch.setattr(module, "_resolve_root", _missing)
    monkeypatch.setattr(sys, "argv", ["webnovel", "subagent-models", "--format", "json"])

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert int(exc.value.code or 0) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agents"]["data-agent"]["model"] == "inherit"
    assert payload["agents"]["data-agent"]["pass_to_task"] is False

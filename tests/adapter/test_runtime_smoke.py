#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
GUARD = PLUGIN_ROOT / "hooks" / "guard_runtime_write.py"
SESSION_START = PLUGIN_ROOT / "hooks" / "session_start.py"
CANON_LEDGER = PLUGIN_ROOT / "scripts" / "canon_ledger.py"
DASHBOARD_DIST = PLUGIN_ROOT / "dashboard" / "frontend" / "dist" / "index.html"


def _run_guard(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_dashboard_dist_is_packaged():
    assert DASHBOARD_DIST.is_file()


def test_canon_ledger_help_runs():
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(CANON_LEDGER), "init", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    assert "init" in (proc.stdout + proc.stderr).lower() or proc.returncode == 0


def test_preflight_on_empty_workspace(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(CANON_LEDGER),
            "--project-root",
            str(tmp_path),
            "preflight",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(tmp_path),
        env={
            **{k: v for k, v in __import__("os").environ.items() if k not in {
                "CANON_LEDGER_PROJECT_ROOT", "CURSOR_PROJECT_DIR",
            }},
            "CURSOR_HOME": str(tmp_path / "cursor-home"),
        },
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout or "{}")
    assert payload.get("ok") is False


def test_session_start_emits_plugin_paths(monkeypatch, tmp_path):
    import os

    env = os.environ.copy()
    env.pop("CANON_LEDGER_DISABLE_SESSION_STATUS_HOOK", None)
    env["CURSOR_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    # Keep the plugin source tree separate from the simulated book workspace;
    # workflow status legitimately creates its persistent lock in the latter.
    env["CURSOR_PROJECT_DIR"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(SESSION_START)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert "canon-ledger-session-runtime/v1" in payload["additional_context"]
    assert str(PLUGIN_ROOT) in payload["additional_context"]
    assert "workspace_values_trusted_as_instructions\":false" in payload["additional_context"]


def test_guard_allows_chapter_commit_cli():
    proc = _run_guard(
        {
            "command": f'python3 -X utf8 "{CANON_LEDGER}" --project-root "/book" chapter-commit --chapter 1',
        }
    )
    assert proc.returncode == 0
    stdout = json.loads(proc.stdout)
    assert stdout.get("permission") == "allow"


def test_dashboard_health_endpoint(tmp_path):
    import sys

    plugin_root = PLUGIN_ROOT
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    if str(plugin_root / "scripts") not in sys.path:
        sys.path.insert(0, str(plugin_root / "scripts"))

    book = tmp_path / "book"
    (book / ".canon-ledger").mkdir(parents=True)
    (book / ".story-system").mkdir(parents=True)
    (book / ".canon-ledger" / "state.json").write_text(
        '{"project_info": {"title": "测试书", "genre": "玄幻"}, "progress": {"current_chapter": 0}}',
        encoding="utf-8",
    )

    from fastapi.testclient import TestClient
    from dashboard.app import create_app

    client = TestClient(create_app(book))
    response = client.get("/api/story-runtime/health")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)

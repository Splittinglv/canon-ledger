#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1].parent
HOOKS_JSON = PLUGIN_ROOT / "hooks" / "hooks.json"
GUARD = PLUGIN_ROOT / "hooks" / "guard_runtime_write.py"
SESSION_START = PLUGIN_ROOT / "hooks" / "session_start.py"


def _run_guard(payload: dict, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _run_guard_raw(raw: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=raw,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_hooks_json_uses_plugin_wrapper_and_plugin_root_paths():
    payload = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

    assert "description" in payload
    assert "hooks" in payload
    assert "sessionStart" in payload["hooks"]
    assert "preToolUse" in payload["hooks"]
    assert "beforeShellExecution" in payload["hooks"]
    pre_tool = payload["hooks"]["preToolUse"][0]
    before_shell = payload["hooks"]["beforeShellExecution"][0]
    assert "Delete" in pre_tool["matcher"]
    assert pre_tool["failClosed"] is True
    assert before_shell["failClosed"] is True
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "${CURSOR_PLUGIN_ROOT}" in serialized
    assert "C:\\Users" not in serialized


def test_guard_blocks_direct_commit_file_write():
    proc = _run_guard(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": r"D:\book\.story-system\commits\chapter_001.commit.json"},
        }
    )

    assert proc.returncode == 2
    stdout = json.loads(proc.stdout)
    assert stdout.get("permission") == "deny"
    assert "permissionDecision" in proc.stderr


def test_guard_allows_direct_state_write():
    # issue #113: audit fixes need direct state.json edits; the guard no
    # longer blocks them.
    proc = _run_guard(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": r"D:\book\.webnovel\state.json"},
        }
    )

    assert proc.returncode == 0


def test_guard_allows_bash_state_write():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {"command": 'python fix_state.py > "D:/book/.webnovel/state.json"'},
        }
    )

    assert proc.returncode == 0


def test_guard_still_blocks_index_db_write():
    proc = _run_guard(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": r"D:\book\.webnovel\index.db"},
        }
    )

    assert proc.returncode == 2


def test_guard_blocks_cursor_payload_protected_path():
    proc = _run_guard(
        {
            "toolName": "Write",
            "path": "/tmp/book/.webnovel/vectors.db",
        }
    )

    assert proc.returncode == 2
    stdout = json.loads(proc.stdout)
    assert stdout.get("permission") == "deny"


def test_guard_blocks_whole_story_system_tree():
    proc = _run_guard(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/book/notes/../.story-system/MASTER_SETTING.json"},
        }
    )

    assert proc.returncode == 2


def test_guard_blocks_delete_target_path():
    proc = _run_guard(
        {
            "tool_name": "Delete",
            "tool_input": {"target_path": "/tmp/book/.story-system/commits/chapter_001.commit.json"},
        }
    )

    assert proc.returncode == 2


def test_guard_blocks_cursor_shell_bypass_command():
    proc = _run_guard(
        {
            "command": "python3 scripts/chapter_commit.py --project-root book --chapter 3",
        }
    )

    assert proc.returncode == 2


def test_guard_allows_runtime_projection_command():
    env = {**os.environ, "SCRIPTS_DIR": str(PLUGIN_ROOT / "scripts")}
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" projections retry --chapter 3'
            },
        },
        env=env,
    )

    assert proc.returncode == 0


def test_guard_allows_single_runtime_commit_command():
    webnovel = PLUGIN_ROOT / "scripts" / "webnovel.py"
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": f'python3 -X utf8 "{webnovel}" --project-root book chapter-commit --chapter 3'
            },
        }
    )

    assert proc.returncode == 0


def test_guard_rejects_untrusted_script_named_webnovel():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python /tmp/scripts/webnovel.py chapter-commit --chapter 1"
            },
        }
    )

    assert proc.returncode == 2


def test_guard_rejects_trusted_token_with_untrusted_environment_value():
    env = {**os.environ, "SCRIPTS_DIR": "/tmp/scripts"}
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'python "${SCRIPTS_DIR}/webnovel.py" chapter-commit --chapter 1'
            },
        },
        env=env,
    )

    assert proc.returncode == 2


def test_guard_rejects_runtime_subcommand_used_only_as_option_value():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": 'python "${SCRIPTS_DIR}/webnovel.py" doctor --format chapter-commit'
            },
        }
    )

    assert proc.returncode == 2


def test_guard_blocks_chained_command_after_runtime_commit():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python scripts/webnovel.py --project-root book chapter-commit --chapter 1 && rm book/.webnovel/index.db"
            },
        }
    )

    assert proc.returncode == 2


def test_guard_blocks_background_command_after_runtime_commit():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python scripts/webnovel.py --project-root book chapter-commit --chapter 1 & rm book/.webnovel/index.db"
            },
        }
    )

    assert proc.returncode == 2


def test_guard_rejects_python_code_option_disguised_as_runtime_commit():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {
                "command": "python -c pass scripts/webnovel.py chapter-commit --chapter 1"
            },
        }
    )

    assert proc.returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "rm -f book/.webnovel/index.db",
        "cp replacement.db book/.webnovel/index.db",
        "tee book/.webnovel/vectors.db",
        "perl -pi -e s/a/b/ book/.story-system/MASTER_SETTING.json",
        "Remove-Item book/.story-system/commits/chapter_001.commit.json",
    ],
)
def test_guard_blocks_shell_access_to_protected_runtime(command):
    proc = _run_guard({"tool_name": "Bash", "tool_input": {"command": command}})

    assert proc.returncode == 2


@pytest.mark.parametrize(
    "command",
    [
        "rm -f book/.webnovel/ind?x.db",
        "rm -f book/.story?system/MASTER_SETTING.json",
        "rm -rf /book/.s????-system",
        "rm -f /book/.w???????/index.d?",
        "bash -c 'p=.st; p+=ory-system; rm -rf /book/$p'",
    ],
)
def test_guard_blocks_shell_wildcards_targeting_protected_runtime(command):
    proc = _run_guard({"tool_name": "Bash", "tool_input": {"command": command}})

    assert proc.returncode == 2


def test_guard_normalizes_dotdot_in_direct_paths():
    proc = _run_guard(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/book/.webnovel/x/../index.db"},
        }
    )

    assert proc.returncode == 2


def test_guard_blocks_direct_chapter_commit_script_bypass():
    proc = _run_guard(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "python scripts/chapter_commit.py --project-root book --chapter 3"},
        }
    )

    assert proc.returncode == 2


@pytest.mark.parametrize("raw", ["", "{", "[]", '"text"'])
def test_guard_rejects_invalid_hook_input(raw):
    proc = _run_guard_raw(raw)

    assert proc.returncode == 2
    assert json.loads(proc.stdout)["permission"] == "deny"


def test_guard_disable_environment_does_not_bypass_protection():
    env = {**os.environ, "WEBNOVEL_DISABLE_RUNTIME_GUARD_HOOK": "1"}
    proc = _run_guard(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/book/.story-system/MASTER_SETTING.json"},
        },
        env=env,
    )

    assert proc.returncode == 2


def test_session_start_can_be_disabled(monkeypatch):
    monkeypatch.setenv("WEBNOVEL_DISABLE_SESSION_STATUS_HOOK", "1")
    proc = subprocess.run(
        [sys.executable, str(SESSION_START)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert proc.returncode == 0
    assert proc.stdout == ""

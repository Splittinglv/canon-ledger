#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


def _load_cursor_paths():
    import sys

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import cursor_paths

    return cursor_paths


@pytest.fixture(autouse=True)
def isolate_plugin_env(monkeypatch, tmp_path):
    for key in (
        "CANON_LEDGER_PLUGIN_ROOT",
        "CURSOR_PLUGIN_ROOT",
        "CURSOR_PROJECT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)


def test_cursor_marketplace_manifest_lists_root_plugin():
    marketplace = json.loads((PLUGIN_ROOT / ".cursor-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = next(item for item in marketplace["plugins"] if item["name"] == "canon-ledger")
    assert plugin["source"] in {".", "./"}
    manifest = json.loads((PLUGIN_ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == manifest["version"]
    assert (PLUGIN_ROOT / ".cursor-plugin" / "plugin.json").is_file()


def test_cursor_plugin_identity_and_rules_are_scoped():
    manifest = json.loads(
        (PLUGIN_ROOT / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "canon-ledger"
    assert manifest["author"]["name"] == "Splittinglv"
    assert manifest["homepage"] == "https://github.com/Splittinglv/webnovel-writer-cursor"
    assert manifest["repository"] == "https://github.com/Splittinglv/webnovel-writer-cursor"

    rule = (PLUGIN_ROOT / "rules" / "canon-ledger-canon.mdc").read_text(encoding="utf-8")
    assert "alwaysApply: false" in rule
    assert "**/.canon-ledger/state.json" in rule
    assert "alwaysApply: true" not in rule


def test_style_prompt_template_exists_and_is_author_owned():
    path = PLUGIN_ROOT / "templates" / "output" / "设定集-文风提示词.md"
    text = path.read_text(encoding="utf-8")
    assert "## 作者提示词" in text
    assert "不会" in text or "不覆盖" in text
    write_skill = (PLUGIN_ROOT / "skills" / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "polish-guide.md" not in write_skill
    assert "style-adapter.md" not in write_skill
    assert "anti-ai-guide.md" not in write_skill
    assert "anti_ai_force_check=pass" not in write_skill


def test_optional_craft_pack_is_removed():
    assert not (PLUGIN_ROOT / "optional" / "canon-ledger-craft").exists()
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "optional/canon-ledger-craft" not in readme
    for skill_name in ("canon-ledger-write", "canon-ledger-plan", "canon-ledger-init", "canon-ledger-query"):
        text = (PLUGIN_ROOT / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert "optional/canon-ledger-craft" not in text, f"{skill_name} 仍引用已删除的技法目录"


def test_resolve_plugin_root_from_this_file():
    cursor_paths = _load_cursor_paths()
    root = cursor_paths.resolve_plugin_root()
    assert root == PLUGIN_ROOT
    assert (root / "scripts" / "canon_ledger.py").is_file()


def test_resolve_plugin_root_from_env(monkeypatch, tmp_path):
    cursor_paths = _load_cursor_paths()
    fake = tmp_path / "plugin"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "canon_ledger.py").write_text("# marker\n", encoding="utf-8")
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(fake))
    assert cursor_paths.resolve_plugin_root() == fake.resolve()


def test_export_cursor_env_prints_json_data(monkeypatch, tmp_path):
    import subprocess
    import sys

    workspace = tmp_path / 'book "quoted" $(not-executed)'
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "export_cursor_env.py"), "--format", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **{
                k: v
                for k, v in os.environ.items()
                if k
                not in {
                    "CANON_LEDGER_PLUGIN_ROOT",
                    "CURSOR_PLUGIN_ROOT",
                }
            },
            "CURSOR_PROJECT_DIR": str(workspace),
        },
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == "canon-ledger-cursor-env/v1"
    assert Path(payload["python_executable"]).is_file()
    assert payload["environment"] == {
        "CANON_LEDGER_PLUGIN_ROOT": str(PLUGIN_ROOT),
        "CURSOR_PLUGIN_ROOT": str(PLUGIN_ROOT),
        "SCRIPTS_DIR": str(SCRIPTS_DIR),
        "WORKSPACE_ROOT": str(workspace.resolve()),
        "CURSOR_PROJECT_DIR": str(workspace.resolve()),
    }
    assert "export " not in proc.stdout


def test_all_skills_parse_cursor_environment_as_data_without_cache_discovery():
    skill_files = sorted((PLUGIN_ROOT / "skills").glob("canon-ledger-*/SKILL.md"))

    assert len(skill_files) == 8
    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        assert "canon-ledger-cursor-env/v1" in text, skill_file
        assert 'payload["python_executable"]' in text, skill_file
        assert 'CANON_LEDGER_PYTHON' in text, skill_file
        assert '"${CANON_LEDGER_PYTHON}"' in text, skill_file
        assert "WEBNOVEL_" not in text, skill_file
        assert "CLAUDE" not in text.upper(), skill_file
        assert "python -X utf8" not in text, skill_file
        assert 'eval "$_EXPORT"' not in text, skill_file
        assert ".rglob(" not in text, skill_file
        assert "Invoke-Expression" not in text, skill_file

    cursor_paths_text = (SCRIPTS_DIR / "cursor_paths.py").read_text(encoding="utf-8")
    assert "emit_shell_exports" not in cursor_paths_text
    assert ".rglob(" not in cursor_paths_text


def test_skill_bootstrap_preserves_workspace_metacharacters_as_plain_data(tmp_path):
    import subprocess

    skill_text = (PLUGIN_ROOT / "skills" / "canon-ledger-doctor" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    bootstrap = skill_text.split("```bash", 1)[1].split("```", 1)[0]
    workspace = tmp_path / 'book "quoted" $(literal)'
    proc = subprocess.run(
        ["bash", "-c", bootstrap + '\nprintf "%s\\n" "$WORKSPACE_ROOT"'],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "CANON_LEDGER_PLUGIN_ROOT": str(PLUGIN_ROOT),
            "CURSOR_PROJECT_DIR": str(workspace),
        },
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines()[-1] == str(workspace.resolve())

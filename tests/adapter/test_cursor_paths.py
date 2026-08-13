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
        "WEBNOVEL_PLUGIN_ROOT",
        "CURSOR_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_ROOT",
        "CURSOR_PROJECT_DIR",
        "CLAUDE_PROJECT_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)


def test_cursor_marketplace_manifest_lists_root_plugin():
    marketplace = json.loads((PLUGIN_ROOT / ".cursor-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = next(item for item in marketplace["plugins"] if item["name"] == "webnovel-writer")
    assert plugin["source"] in {".", "./"}
    assert plugin["version"] == "6.2.1"
    assert (PLUGIN_ROOT / ".cursor-plugin" / "plugin.json").is_file()


def test_style_prompt_template_exists_and_is_author_owned():
    path = PLUGIN_ROOT / "templates" / "output" / "设定集-文风提示词.md"
    text = path.read_text(encoding="utf-8")
    assert "## 作者提示词" in text
    assert "不会" in text or "不覆盖" in text
    write_skill = (PLUGIN_ROOT / "skills" / "webnovel-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "polish-guide.md" not in write_skill
    assert "style-adapter.md" not in write_skill
    assert "anti-ai-guide.md" not in write_skill
    assert "anti_ai_force_check=pass" not in write_skill


def test_optional_craft_pack_is_removed():
    assert not (PLUGIN_ROOT / "optional" / "webnovel-craft").exists()
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "optional/webnovel-craft" not in readme
    for skill_name in ("webnovel-write", "webnovel-plan", "webnovel-init", "webnovel-query"):
        text = (PLUGIN_ROOT / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
        assert "optional/webnovel-craft" not in text, f"{skill_name} 仍引用已删除的技法目录"


def test_resolve_plugin_root_from_this_file():
    cursor_paths = _load_cursor_paths()
    root = cursor_paths.resolve_plugin_root()
    assert root == PLUGIN_ROOT
    assert (root / "scripts" / "webnovel.py").is_file()


def test_resolve_plugin_root_from_env(monkeypatch, tmp_path):
    cursor_paths = _load_cursor_paths()
    fake = tmp_path / "plugin"
    (fake / "scripts").mkdir(parents=True)
    (fake / "scripts" / "webnovel.py").write_text("# marker\n", encoding="utf-8")
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(fake))
    assert cursor_paths.resolve_plugin_root() == fake.resolve()


def test_export_cursor_env_prints_shell_exports(monkeypatch):
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "export_cursor_env.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={k: v for k, v in os.environ.items() if k not in {
            "WEBNOVEL_PLUGIN_ROOT", "CURSOR_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT",
            "CURSOR_PROJECT_DIR", "CLAUDE_PROJECT_DIR",
        }},
    )
    assert proc.returncode == 0
    assert f'WEBNOVEL_PLUGIN_ROOT="{PLUGIN_ROOT}"' in proc.stdout
    assert "SCRIPTS_DIR=" in proc.stdout
    assert "WORKSPACE_ROOT=" in proc.stdout

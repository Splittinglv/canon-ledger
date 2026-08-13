#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_plugin_version  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_readme(root: Path, version: str) -> None:
    (root / "README.md").write_text(
        "\n".join(
            [
                "# Test",
                "",
                f"[![Version](https://img.shields.io/badge/version-{version}-brightgreen.svg)](.cursor-plugin/plugin.json)",
                "",
                "| 版本 | 说明 |",
                "|------|------|",
                f"| **v{version} (当前)** | current |",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_cursor_layout(root: Path, version: str = "1.2.3") -> None:
    _write_json(
        root / ".cursor-plugin" / "plugin.json",
        {"name": "webnovel-writer", "version": version, "description": "desc"},
    )
    _write_json(
        root / ".cursor-plugin" / "marketplace.json",
        {
            "metadata": {"version": version},
            "plugins": [
                {
                    "name": "webnovel-writer",
                    "version": version,
                    "source": ".",
                }
            ],
        },
    )
    _write_readme(root, version)


def test_check_versions_supports_flat_cursor_repository(tmp_path):
    _write_cursor_layout(tmp_path)

    layout = sync_plugin_version.resolve_release_layout(tmp_path)

    assert layout.plugin_json == tmp_path / ".cursor-plugin" / "plugin.json"
    assert sync_plugin_version.check_versions(root=tmp_path) == 0


def test_sync_versions_updates_every_cursor_version_surface(tmp_path):
    _write_cursor_layout(tmp_path)

    previous, target, changed = sync_plugin_version.sync_versions(
        version="1.2.4",
        release_notes="safer release tooling",
        root=tmp_path,
    )

    plugin = sync_plugin_version.load_json(tmp_path / ".cursor-plugin" / "plugin.json")
    marketplace = sync_plugin_version.load_json(tmp_path / ".cursor-plugin" / "marketplace.json")
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert (previous, target, changed) == ("1.2.3", "1.2.4", True)
    assert plugin["version"] == "1.2.4"
    assert marketplace["metadata"]["version"] == "1.2.4"
    assert marketplace["plugins"][0]["version"] == "1.2.4"
    assert "version-1.2.4-brightgreen" in readme
    assert "| **v1.2.4 (当前)** | safer release tooling |" in readme
    assert sync_plugin_version.check_versions("1.2.4", root=tmp_path) == 0


def test_release_layout_keeps_legacy_marketplace_compatibility(tmp_path):
    _write_json(
        tmp_path / "webnovel-writer" / ".claude-plugin" / "plugin.json",
        {"name": "webnovel-writer", "version": "1.2.3", "description": "desc"},
    )
    _write_json(
        tmp_path / ".claude-plugin" / "marketplace.json",
        {
            "plugins": [
                {
                    "name": "webnovel-writer",
                    "version": "1.2.3",
                    "source": "./webnovel-writer",
                }
            ]
        },
    )
    _write_readme(tmp_path, "1.2.3")

    layout = sync_plugin_version.resolve_release_layout(tmp_path)

    assert layout.plugin_json == tmp_path / "webnovel-writer" / ".claude-plugin" / "plugin.json"
    assert sync_plugin_version.check_versions(root=tmp_path) == 0


def test_release_layout_missing_error_lists_checked_cursor_path(tmp_path):
    with pytest.raises(FileNotFoundError) as exc_info:
        sync_plugin_version.resolve_release_layout(tmp_path)

    assert str(tmp_path / ".cursor-plugin" / "plugin.json") in str(exc_info.value)


def test_windows_runner_uses_flat_cursor_repository_paths():
    runner = (SCRIPTS_DIR / "run_tests.ps1").read_text(encoding="utf-8")

    assert '$env:PYTHONPATH = "scripts"' in runner
    assert ".claude/scripts" not in runner
    assert 'Join-Path $PSScriptRoot "..\\.."' not in runner

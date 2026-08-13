#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

from validate_plugin_package import validate_package  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_minimal_package(root: Path, *, plugin_version: str = "1.2.3", marketplace_version: str = "1.2.3") -> None:
    _write_json(
        root / "webnovel-writer" / ".claude-plugin" / "plugin.json",
        {"name": "webnovel-writer", "version": plugin_version, "description": "desc"},
    )
    _write_json(
        root / ".claude-plugin" / "marketplace.json",
        {
            "plugins": [
                {
                    "name": "webnovel-writer",
                    "version": marketplace_version,
                    "source": "./webnovel-writer",
                }
            ]
        },
    )
    (root / "README.md").write_text(
        "\n".join(
            [
                "# Test",
                "",
                f"[![Version](https://img.shields.io/badge/version-{plugin_version}-brightgreen.svg)](.claude-plugin/marketplace.json)",
                "",
                "| 版本 | 说明 |",
                "|------|------|",
                f"| **v{plugin_version} (当前)** | test |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "webnovel-writer" / "LICENSE").parent.mkdir(parents=True, exist_ok=True)
    (root / "webnovel-writer" / "LICENSE").write_text("license\n", encoding="utf-8")
    skill = root / "webnovel-writer" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: demo\ndescription: demo\n---\n\n# Demo\n", encoding="utf-8")
    agent = root / "webnovel-writer" / "agents" / "demo.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text("---\nname: demo\ndescription: demo\ntools: Read\n---\n\n# Demo\n", encoding="utf-8")


def test_validate_plugin_package_passes_minimal_package(tmp_path):
    _write_minimal_package(tmp_path)

    report = validate_package(tmp_path)

    assert report["ok"] is True
    assert report["error_count"] == 0


def test_validate_plugin_package_accepts_plugin_root(tmp_path):
    _write_minimal_package(tmp_path)

    report = validate_package(tmp_path / "webnovel-writer")

    assert report["ok"] is True
    assert report["error_count"] == 0


def test_validate_plugin_package_accepts_cursor_root_marketplace(tmp_path):
    _write_json(
        tmp_path / ".cursor-plugin" / "plugin.json",
        {"name": "webnovel-writer", "version": "1.2.3", "description": "desc"},
    )
    _write_json(
        tmp_path / ".cursor-plugin" / "marketplace.json",
        {
            "name": "webnovel-writer-cursor",
            "owner": {"name": "test"},
            "plugins": [
                {
                    "name": "webnovel-writer",
                    "version": "1.2.3",
                    "source": ".",
                }
            ],
        },
    )
    (tmp_path / "README.md").write_text(
        "\n".join(
            [
                "# Test",
                "",
                "[![Version](https://img.shields.io/badge/version-1.2.3-brightgreen.svg)](.cursor-plugin/plugin.json)",
                "",
                "| 版本 | 说明 |",
                "|------|------|",
                "| **v1.2.3 (当前)** | test |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "LICENSE").write_text("license\n", encoding="utf-8")
    skill = tmp_path / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: demo\ndescription: demo\n---\n\n# Demo\n", encoding="utf-8")
    agent = tmp_path / "agents" / "demo.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text("---\nname: demo\ndescription: demo\ntools: Read\n---\n\n# Demo\n", encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is True
    assert report["error_count"] == 0
    assert not any(item["code"] == "marketplace.json" for item in report["issues"])


def test_validate_plugin_package_detects_version_mismatch(tmp_path):
    _write_minimal_package(tmp_path, plugin_version="1.2.3", marketplace_version="1.2.4")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "version.marketplace" for item in report["issues"])


def test_validate_plugin_package_detects_readme_badge_mismatch(tmp_path):
    _write_minimal_package(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("version-1.2.3", "version-1.2.2"), encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "version.readme_badge" for item in report["issues"])


def test_validate_plugin_package_detects_missing_skill_frontmatter(tmp_path):
    _write_minimal_package(tmp_path)
    skill = tmp_path / "webnovel-writer" / "skills" / "demo" / "SKILL.md"
    skill.write_text("---\nname: demo\n---\n\n# Demo\n", encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "skill.frontmatter" for item in report["issues"])


def test_validate_plugin_package_rejects_fail_open_runtime_hooks(tmp_path):
    _write_minimal_package(tmp_path)
    hooks = tmp_path / "webnovel-writer" / "hooks" / "hooks.json"
    _write_json(
        hooks,
        {
            "version": 1,
            "description": "guard",
            "hooks": {
                "preToolUse": [{"command": "guard", "matcher": "Write|Edit"}],
                "beforeShellExecution": [{"command": "guard"}],
            },
        },
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    codes = {item["code"] for item in report["issues"]}
    assert "hooks.fail_closed" in codes
    assert "hooks.delete_matcher" in codes


def test_validate_plugin_package_rejects_executable_skill_exports(tmp_path):
    _write_minimal_package(tmp_path)
    skill = tmp_path / "webnovel-writer" / "skills" / "demo" / "SKILL.md"
    skill.write_text(
        "---\nname: demo\ndescription: demo\n---\n\n```bash\neval \"$_EXPORT\"\n```\n",
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "security.skill_bootstrap_execution" for item in report["issues"])

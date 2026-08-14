#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def _ensure_scripts_on_path() -> None:
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))


_ensure_scripts_on_path()

from validate_plugin_package import validate_package  # noqa: E402


SOURCE_ROOT = SCRIPTS_DIR.parent
PRODUCT_REPOSITORY_URL = "https://github.com/Splittinglv/canon-ledger"
CORE_SURFACES = (
    "dashboard",
    "doctor",
    "init",
    "learn",
    "plan",
    "query",
    "review",
    "write",
)
RUNTIME_PACKAGES = {
    "cookie": ("1.1.1", "MIT", {}),
    "echarts": ("6.1.0", "Apache-2.0", {"tslib": "^2.3.0", "zrender": "6.1.0"}),
    "echarts-for-react": ("3.0.6", "MIT", {"fast-deep-equal": "^3.1.3", "size-sensor": "^1.0.3"}),
    "fast-deep-equal": ("3.1.3", "MIT", {}),
    "react": ("19.2.8", "MIT", {}),
    "react-dom": ("19.2.8", "MIT", {"scheduler": "^0.27.0"}),
    "react-router": ("7.18.2", "MIT", {"cookie": "^1.1.1", "set-cookie-parser": "^2.7.2"}),
    "react-router-dom": ("7.18.2", "MIT", {"react-router": "7.18.2"}),
    "scheduler": ("0.27.0", "MIT", {}),
    "set-cookie-parser": ("2.7.2", "MIT", {}),
    "size-sensor": ("1.0.3", "ISC", {}),
    "tslib": ("2.3.0", "0BSD", {}),
    "zrender": ("6.1.0", "BSD-3-Clause", {"tslib": "2.3.0"}),
}
BUILD_PACKAGES = {
    "@types/react": ("19.2.18", "MIT"),
    "@types/react-dom": ("19.2.4", "MIT"),
    "@vitejs/plugin-react": ("4.7.0", "MIT"),
    "vite": ("6.4.3", "MIT"),
}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_minimal_package(
    root: Path,
    *,
    plugin_version: str = "1.2.3",
    marketplace_version: str = "1.2.3",
    flat: bool = False,
) -> Path:
    plugin_root = root if flat else root / "canon-ledger"
    marketplace_source = "." if flat else "./canon-ledger"
    _write_json(
        plugin_root / ".cursor-plugin" / "plugin.json",
        {
            "name": "canon-ledger",
            "version": plugin_version,
            "description": "叙典 CanonLedger 长篇小说一致性引擎",
            "author": {"name": "Splittinglv"},
            "homepage": PRODUCT_REPOSITORY_URL,
            "repository": PRODUCT_REPOSITORY_URL,
            "license": "GPL-3.0",
        },
    )
    _write_json(
        root / ".cursor-plugin" / "marketplace.json",
        {
            "name": "canon-ledger-local",
            "owner": {"name": "Splittinglv"},
            "metadata": {"version": plugin_version},
            "plugins": [
                {
                    "name": "canon-ledger",
                    "version": marketplace_version,
                    "source": marketplace_source,
                    "description": "叙典 CanonLedger 长篇小说一致性引擎",
                    "author": {"name": "Splittinglv"},
                    "homepage": PRODUCT_REPOSITORY_URL,
                    "repository": PRODUCT_REPOSITORY_URL,
                    "license": "GPL-3.0",
                }
            ]
        },
    )
    (root / "README.md").write_text(
        "\n".join(
            [
                "# CanonLedger 测试包",
                "",
                f"[![Version](https://img.shields.io/badge/version-{plugin_version}-brightgreen.svg)](.cursor-plugin/marketplace.json)",
                "",
                "| 版本 | 说明 |",
                "|------|------|",
                f"| **v{plugin_version} (当前)** | 当前测试版本 |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plugin_root.mkdir(parents=True, exist_ok=True)
    (plugin_root / "LICENSE").write_text(
        (SOURCE_ROOT / "LICENSE").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (plugin_root / "NOTICE.md").write_text(
        f"# NOTICE\n\n叙典 CanonLedger 由 Splittinglv 发起并发布，使用生成式 AI 辅助开发；项目派生自 lingfengQAQ/webnovel-writer，按 GPL-3.0 发布。仓库：{PRODUCT_REPOSITORY_URL}。\n",
        encoding="utf-8",
    )
    (plugin_root / "ATTRIBUTION.md").write_text(
        f"# 来源\n\nCanonLedger 派生自 lingfengQAQ/webnovel-writer，使用生成式 AI 辅助开发，按 GNU General Public License v3 发布。仓库：{PRODUCT_REPOSITORY_URL}。\n",
        encoding="utf-8",
    )
    (plugin_root / "AUTHORS.md").write_text(
        "# 项目参与与来源\n\nCanonLedger 的发布账号为 Splittinglv，开发过程使用生成式 AI 工具辅助；历史上游作者为 lingfengQAQ。\n",
        encoding="utf-8",
    )

    notice_lines = [
        "# 第三方组件声明",
        "",
        "CanonLedger Dashboard 前端运行时依赖如下。",
        "",
        "| 包 | 锁定版本 | 许可证 | 关系 | 随包许可正文 |",
        "|----|----------|--------|------|----------------|",
    ]
    for name, (version, license_id, _dependencies) in sorted(RUNTIME_PACKAGES.items()):
        license_path = f"third_party_licenses/npm/{name}/LICENSE"
        extras = ""
        if name == "echarts":
            extras = "、[NOTICE](third_party_licenses/npm/echarts/NOTICE)、[D3](third_party_licenses/npm/echarts/LICENSE-d3)"
        notice_lines.append(
            f"| `{name}` | {version} | {license_id} | 测试依赖 | [LICENSE]({license_path}){extras} |"
        )
        source = SOURCE_ROOT / license_path
        target = plugin_root / license_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    notice_lines.extend(
        [
            "",
            "## Dashboard 前端构建工具",
            "",
            "| 包 | 锁定版本 | 许可证 |",
            "|----|----------|--------|",
        ]
    )
    for name, (version, license_id) in sorted(BUILD_PACKAGES.items()):
        notice_lines.append(f"| `{name}` | {version} | {license_id} |")
    for relative in (
        "third_party_licenses/npm/echarts/NOTICE",
        "third_party_licenses/npm/echarts/LICENSE-d3",
    ):
        source = SOURCE_ROOT / relative
        target = plugin_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (plugin_root / "THIRD_PARTY_NOTICES.md").write_text(
        "\n".join(notice_lines) + "\n",
        encoding="utf-8",
    )

    for surface in CORE_SURFACES:
        name = f"canon-ledger-{surface}"
        command = plugin_root / "commands" / f"{name}.md"
        command.parent.mkdir(parents=True, exist_ok=True)
        command.write_text(
            f"---\nname: {name}\ndescription: CanonLedger 核心命令\n---\n\n遵循 `{name}` 技能。\n",
            encoding="utf-8",
        )
        skill = plugin_root / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text(
            f"---\nname: {name}\ndescription: CanonLedger 核心技能\n---\n\n# CanonLedger\n\n响应 /{name}。\n",
            encoding="utf-8",
        )

    agent = plugin_root / "agents" / "demo.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text("---\nname: demo\ndescription: 测试代理\ntools: Read\n---\n\n# 测试代理\n", encoding="utf-8")
    rule = plugin_root / "rules" / "canon-ledger-canon.mdc"
    rule.parent.mkdir(parents=True, exist_ok=True)
    rule.write_text("# CanonLedger\n\n真源位于 .story-system，投影位于 .canon-ledger。\n", encoding="utf-8")

    entrypoint = plugin_root / "scripts" / "canon_ledger.py"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text("# CanonLedger\nfrom data_modules.canon_ledger import main\n", encoding="utf-8")
    data_entrypoint = plugin_root / "scripts" / "data_modules" / "canon_ledger.py"
    data_entrypoint.parent.mkdir(parents=True, exist_ok=True)
    data_entrypoint.write_text("# CanonLedger 使用 .canon-ledger 项目目录\n", encoding="utf-8")

    frontend = plugin_root / "dashboard" / "frontend"
    (frontend / "dist" / "assets").mkdir(parents=True, exist_ok=True)
    (frontend / "src").mkdir(parents=True, exist_ok=True)
    (frontend / "src" / "App.jsx").write_text(
        "export default function App() { return <h1>叙典 CANONLEDGER</h1>; }\n",
        encoding="utf-8",
    )
    (frontend / "index.html").write_text("<title>CanonLedger Dashboard</title>\n", encoding="utf-8")
    (frontend / "dist" / "index.html").write_text("<title>CanonLedger Dashboard</title>\n", encoding="utf-8")
    (frontend / "dist" / "assets" / "app.js").write_text("// 叙典 CANONLEDGER\n", encoding="utf-8")
    (frontend / "dist" / "assets" / "app.css").write_text("/* CanonLedger */\n", encoding="utf-8")
    _write_json(frontend / "package.json", {"name": "canon-ledger-dashboard", "version": "0.1.0"})
    direct_dependencies = {
        "echarts": "^6.1.0",
        "echarts-for-react": "^3.0.2",
        "react": "^19.0.0",
        "react-dom": "^19.0.0",
        "react-router-dom": "^7.18.2",
    }
    lock_packages: dict[str, dict] = {
        "": {
            "name": "canon-ledger-dashboard",
            "version": "0.1.0",
            "dependencies": direct_dependencies,
            "devDependencies": {
                "@types/react": "^19.0.0",
                "@types/react-dom": "^19.0.0",
                "@vitejs/plugin-react": "^4.4.0",
                "vite": "^6.4.3",
            },
        }
    }
    for name, (version, license_id, dependencies) in RUNTIME_PACKAGES.items():
        entry = {"version": version, "license": license_id}
        if dependencies:
            entry["dependencies"] = dependencies
        lock_packages[f"node_modules/{name}"] = entry
    for name, (version, license_id) in BUILD_PACKAGES.items():
        lock_packages[f"node_modules/{name}"] = {"version": version, "license": license_id}
    _write_json(
        frontend / "package-lock.json",
        {
            "name": "canon-ledger-dashboard",
            "version": "0.1.0",
            "lockfileVersion": 3,
            "requires": True,
            "packages": lock_packages,
        },
    )
    return plugin_root


def test_validate_plugin_package_passes_minimal_package(tmp_path):
    _write_minimal_package(tmp_path)

    report = validate_package(tmp_path)

    assert report["ok"] is True
    assert report["error_count"] == 0


def test_validate_plugin_package_accepts_plugin_root(tmp_path):
    _write_minimal_package(tmp_path)

    report = validate_package(tmp_path / "canon-ledger")

    assert report["ok"] is True
    assert report["error_count"] == 0


def test_validate_plugin_package_accepts_cursor_root_marketplace(tmp_path):
    _write_minimal_package(tmp_path, flat=True)

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
    skill = tmp_path / "canon-ledger" / "skills" / "canon-ledger-write" / "SKILL.md"
    skill.write_text(
        "---\nname: canon-ledger-write\n---\n\n# CanonLedger\n\n响应 /canon-ledger-write。\n",
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "skill.frontmatter" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_attribution_file(tmp_path):
    _write_minimal_package(tmp_path)
    (tmp_path / "canon-ledger" / "ATTRIBUTION.md").unlink()

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(
        item["code"] == "legal.required_file" and item["path"].endswith("ATTRIBUTION.md")
        for item in report["issues"]
    )


def test_validate_plugin_package_rejects_missing_ai_assistance_disclosure(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    authors = plugin_root / "AUTHORS.md"
    authors.write_text(
        authors.read_text(encoding="utf-8").replace("生成式 AI 工具辅助", "自动化工具辅助"),
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(
        item["code"] == "legal.document_content" and item["path"].endswith("AUTHORS.md")
        for item in report["issues"]
    )


def test_validate_plugin_package_rejects_old_repository_in_current_attribution(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    attribution = plugin_root / "ATTRIBUTION.md"
    attribution.write_text(
        attribution.read_text(encoding="utf-8").replace(
            PRODUCT_REPOSITORY_URL,
            "https://github.com/Splittinglv/webnovel-writer-cursor",
        ),
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(
        item["code"] == "legal.document_content" and item["path"].endswith("ATTRIBUTION.md")
        for item in report["issues"]
    )


def test_validate_plugin_package_rejects_incomplete_gpl_text(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "LICENSE").write_text("GNU GPL v3 摘要不是完整许可正文。\n", encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.gpl_text" for item in report["issues"])


def test_validate_plugin_package_rejects_wrong_manifest_license(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    manifest = plugin_root / ".cursor-plugin" / "plugin.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["license"] = "MIT"
    _write_json(manifest, payload)

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.manifest_license" for item in report["issues"])


def test_validate_plugin_package_rejects_wrong_manifest_repository(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    manifest = plugin_root / ".cursor-plugin" / "plugin.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["repository"] = "https://example.invalid/canon-ledger"
    _write_json(manifest, payload)

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "manifest.repository" for item in report["issues"])


def test_validate_plugin_package_rejects_wrong_marketplace_homepage(tmp_path):
    _write_minimal_package(tmp_path)
    marketplace = tmp_path / ".cursor-plugin" / "marketplace.json"
    payload = json.loads(marketplace.read_text(encoding="utf-8"))
    payload["plugins"][0]["homepage"] = "https://example.invalid/canon-ledger"
    _write_json(marketplace, payload)

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "marketplace.plugin_homepage" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_runtime_license_text(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "third_party_licenses" / "npm" / "react" / "LICENSE").unlink()

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.third_party_license_file" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_runtime_mapping(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    notices = plugin_root / "THIRD_PARTY_NOTICES.md"
    notices.write_text(
        notices.read_text(encoding="utf-8").replace("| `react` | 19.2.8 | MIT |", "| `react` | 未映射 | MIT |"),
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.third_party_mapping" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_echarts_notice(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "third_party_licenses" / "npm" / "echarts" / "NOTICE").unlink()

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.echarts_notice" for item in report["issues"])


def test_validate_plugin_package_rejects_changed_runtime_dependency_set(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    lock_path = plugin_root / "dashboard" / "frontend" / "package-lock.json"
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    payload["packages"][""]["dependencies"]["unexpected-runtime"] = "1.0.0"
    payload["packages"]["node_modules/unexpected-runtime"] = {"version": "1.0.0", "license": "MIT"}
    _write_json(lock_path, payload)

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.runtime_dependency_set" for item in report["issues"])


def test_validate_plugin_package_rejects_stale_build_tool_mapping(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    notices = plugin_root / "THIRD_PARTY_NOTICES.md"
    notices.write_text(
        notices.read_text(encoding="utf-8").replace("| `vite` | 6.4.3 | MIT |", "| `vite` | 6.4.1 | MIT |"),
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "legal.build_mapping" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_core_command(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "commands" / "canon-ledger-plan.md").unlink()

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "identity.command_set" for item in report["issues"])


def test_validate_plugin_package_rejects_incomplete_rule_sentinel(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "rules" / "canon-ledger-canon.mdc").write_text(
        "# CanonLedger\n\n只保留产品名不足以构成真源规则哨兵。\n",
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "identity.rule" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_dashboard_brand(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "dashboard" / "frontend" / "dist" / "index.html").write_text(
        "<title>小说一致性面板</title>\n",
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "identity.dashboard_brand" for item in report["issues"])


def test_validate_plugin_package_rejects_legacy_dashboard_source_brand(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    (plugin_root / "dashboard" / "frontend" / "src" / "App.jsx").write_text(
        "export default function App() { return <h1>PIXEL WRITER HUB</h1>; }\n",
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    codes = {item["code"] for item in report["issues"]}
    assert report["ok"] is False
    assert "identity.dashboard_visible_brand" in codes
    assert "identity.dashboard_legacy_brand" in codes


def test_validate_plugin_package_rejects_legacy_dashboard_dist_brand(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    assets = plugin_root / "dashboard" / "frontend" / "dist" / "assets"
    (assets / "legacy.js").write_text("const title = 'PIXEL WRITER HUB';\n", encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "identity.dashboard_dist_legacy_brand" for item in report["issues"])


def test_validate_plugin_package_rejects_missing_dashboard_dist_visible_brand(tmp_path):
    plugin_root = _write_minimal_package(tmp_path)
    assets = plugin_root / "dashboard" / "frontend" / "dist" / "assets"
    (assets / "app.js").write_text("// 构建产物缺少可见产品品牌。\n", encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "identity.dashboard_dist_visible_brand" for item in report["issues"])


def test_validate_plugin_package_rejects_legacy_entrypoint(tmp_path):
    _write_minimal_package(tmp_path)
    legacy = tmp_path / "canon-ledger" / "scripts" / "webnovel.py"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("print('legacy')\n", encoding="utf-8")

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "identity.legacy_entrypoint" for item in report["issues"])


def test_validate_plugin_package_rejects_fail_open_runtime_hooks(tmp_path):
    _write_minimal_package(tmp_path)
    hooks = tmp_path / "canon-ledger" / "hooks" / "hooks.json"
    _write_json(
        hooks,
        {
            "version": 1,
            "description": "运行时防护",
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


def test_validate_plugin_package_rejects_unbootstrapped_runtime_hooks(tmp_path):
    _write_minimal_package(tmp_path)
    hooks = tmp_path / "canon-ledger" / "hooks" / "hooks.json"
    _write_json(
        hooks,
        {
            "version": 1,
            "description": "运行时防护",
            "hooks": {
                "sessionStart": [{"command": "python3 hooks/session_start.py"}],
                "preToolUse": [
                    {
                        "command": "python3 hooks/guard_runtime_write.py",
                        "matcher": "Write|StrReplace|Edit|Delete",
                        "failClosed": True,
                    }
                ],
                "beforeShellExecution": [
                    {
                        "command": "python3 hooks/guard_runtime_write.py",
                        "failClosed": True,
                    }
                ],
            },
        },
    )

    report = validate_package(tmp_path)

    codes = {item["code"] for item in report["issues"]}
    assert "hooks.runtime_bootstrap" in codes
    assert "hooks.runtime_file" in codes


def test_validate_plugin_package_rejects_executable_skill_exports(tmp_path):
    _write_minimal_package(tmp_path)
    skill = tmp_path / "canon-ledger" / "skills" / "canon-ledger-write" / "SKILL.md"
    skill.write_text(
        "---\nname: canon-ledger-write\ndescription: CanonLedger 写作技能\n---\n\n"
        "# CanonLedger\n\n响应 /canon-ledger-write。\n\n```bash\neval \"$_EXPORT\"\n```\n",
        encoding="utf-8",
    )

    report = validate_package(tmp_path)

    assert report["ok"] is False
    assert any(item["code"] == "security.skill_bootstrap_execution" for item in report["issues"])

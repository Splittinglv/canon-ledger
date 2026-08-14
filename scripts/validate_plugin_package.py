#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import sync_plugin_version


SCHEMA_VERSION = "canon-ledger-plugin-package-validator/v1"
PLUGIN_NAME = "canon-ledger"
PLUGIN_AUTHOR = "Splittinglv"
MARKETPLACE_NAME = "canon-ledger-local"
PRODUCT_REPOSITORY_URL = "https://github.com/Splittinglv/webnovel-writer-cursor"
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = sync_plugin_version.VERSION_PATTERN
LOCAL_ABSOLUTE_RE = re.compile(r"(?i)(?:[a-z]:\\users\\|/users/[^/\s]+/|/home/[^/\s]+/)")
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
EXPECTED_RUNTIME_LICENSES = {
    "cookie": "MIT",
    "echarts": "Apache-2.0",
    "echarts-for-react": "MIT",
    "fast-deep-equal": "MIT",
    "react": "MIT",
    "react-dom": "MIT",
    "react-router": "MIT",
    "react-router-dom": "MIT",
    "scheduler": "MIT",
    "set-cookie-parser": "MIT",
    "size-sensor": "ISC",
    "tslib": "0BSD",
    "zrender": "BSD-3-Clause",
}
EXPECTED_BUILD_TOOL_LICENSES = {
    "@types/react": "MIT",
    "@types/react-dom": "MIT",
    "@vitejs/plugin-react": "MIT",
    "vite": "MIT",
}
LICENSE_TEXT_MARKERS = {
    "Apache-2.0": ("Apache License", "Version 2.0", "TERMS AND CONDITIONS"),
    "MIT": ("Permission is hereby granted", "THE SOFTWARE IS PROVIDED"),
    "0BSD": ("Permission to use, copy, modify, and/or distribute", "THE SOFTWARE IS PROVIDED"),
    "BSD-3-Clause": ("Redistribution and use in source and binary forms", "Neither the name"),
    "ISC": ("Permission to use, copy, modify, and/or distribute", "THE SOFTWARE IS PROVIDED"),
}
GPL_V3_LICENSE_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"


def _issue(
    code: str,
    *,
    message: str,
    severity: str = "error",
    path: str = "",
    repair: str = "",
) -> dict[str, str]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "path": path,
        "repair": repair,
    }


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"invalid_json:{exc}"
    except OSError as exc:
        return {}, f"read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "not_object"
    return payload, ""


def _load_text(path: Path) -> tuple[str, str]:
    try:
        return path.read_text(encoding="utf-8"), ""
    except FileNotFoundError:
        return "", "missing"
    except UnicodeDecodeError as exc:
        return "", f"invalid_utf8:{exc}"
    except OSError as exc:
        return "", f"read_error:{exc}"


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


def _marketplace_plugin(payload: dict[str, Any]) -> dict[str, Any] | None:
    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        return None
    for item in plugins:
        if isinstance(item, dict) and item.get("name") == PLUGIN_NAME:
            return item
    return None


def _plugin_manifest_path(root: Path) -> Path:
    return root / ".cursor-plugin" / "plugin.json"


def _is_plugin_root(root: Path) -> bool:
    return _plugin_manifest_path(root).is_file()


def _plugin_root(root: Path) -> Path:
    if _is_plugin_root(root):
        return root
    candidate = root / PLUGIN_NAME
    if _is_plugin_root(candidate):
        return candidate
    return root / PLUGIN_NAME


def _marketplace_candidates(repo_root: Path) -> list[Path]:
    return [repo_root / ".cursor-plugin" / "marketplace.json"]


def _existing_marketplace(repo_root: Path) -> Path | None:
    for candidate in _marketplace_candidates(repo_root):
        if candidate.is_file():
            return candidate
    return None


def _repo_root(root: Path) -> Path:
    if _is_plugin_root(root) and _existing_marketplace(root.parent) is not None:
        return root.parent
    return root


def _normalize_marketplace_source(value: Any) -> str:
    text = str(value or "").strip()
    if text in {".", "./"}:
        return "."
    return text.rstrip("/")


def _allowed_marketplace_sources(root: Path) -> set[str]:
    plugin_root = _plugin_root(root).resolve()
    repo_root = _repo_root(root).resolve()
    if plugin_root == repo_root:
        return {".", "./"}
    directory = plugin_root.name
    return {f"./{directory}", directory}


def _check_manifest(root: Path, issues: list[dict[str, str]]) -> tuple[str, str]:
    plugin_json = _plugin_manifest_path(_plugin_root(root))
    payload, error = _load_json(plugin_json)
    if error:
        issues.append(_issue("manifest.plugin_json", message=error, path=str(plugin_json), repair="恢复 .cursor-plugin/plugin.json。"))
        return "", ""
    name = str(payload.get("name") or "")
    version = str(payload.get("version") or "")
    if not KEBAB_RE.fullmatch(name) or name != PLUGIN_NAME:
        issues.append(_issue("manifest.name", message=f"插件名必须是 {PLUGIN_NAME}，当前为：{name}", path=str(plugin_json), repair=f"将插件 name 改为 {PLUGIN_NAME}。"))
    if not SEMVER_RE.fullmatch(version):
        issues.append(_issue("manifest.version", message=f"invalid semver: {version}", path=str(plugin_json), repair="使用 X.Y.Z 版本号。"))
    description = str(payload.get("description") or "").strip()
    if not description:
        issues.append(_issue("manifest.description", message="插件描述缺失", path=str(plugin_json), repair="补齐 description。"))
    elif "CanonLedger" not in description:
        issues.append(
            _issue(
                "identity.manifest_brand",
                message="插件描述未声明 CanonLedger 产品身份",
                path=str(plugin_json),
                repair="在 description 中明确使用叙典 CanonLedger 品牌名。",
            )
        )
    if str(payload.get("license") or "") != "GPL-3.0":
        issues.append(
            _issue(
                "legal.manifest_license",
                message="plugin.json 的 license 必须为 GPL-3.0",
                path=str(plugin_json),
                repair="将 license 恢复为 GPL-3.0，并保留完整 LICENSE 正文。",
            )
        )
    for field in ("homepage", "repository"):
        value = str(payload.get(field) or "")
        if value != PRODUCT_REPOSITORY_URL:
            issues.append(
                _issue(
                    f"manifest.{field}",
                    message=f"plugin.json 的 {field} 必须指向当前 CanonLedger 仓库，当前为：{value or '缺失'}",
                    path=str(plugin_json),
                    repair=f"将 {field} 改为 {PRODUCT_REPOSITORY_URL}。",
                )
            )
    author = payload.get("author")
    author_name = str(author.get("name") or "") if isinstance(author, dict) else ""
    if author_name != PLUGIN_AUTHOR:
        issues.append(
            _issue(
                "manifest.author",
                message=f"插件维护者必须是 {PLUGIN_AUTHOR}，当前为：{author_name or '缺失'}",
                path=str(plugin_json),
                repair=f"将 author.name 改为 {PLUGIN_AUTHOR}；历史来源请写入 NOTICE/ATTRIBUTION。",
            )
        )
    return name, version


def _check_marketplace(root: Path, plugin_version: str, issues: list[dict[str, str]]) -> None:
    repo_root = _repo_root(root)
    marketplace = _existing_marketplace(repo_root) or _marketplace_candidates(repo_root)[0]
    payload, error = _load_json(marketplace)
    if error:
        severity = "warning" if _is_plugin_root(root) else "error"
        issues.append(
            _issue(
                "marketplace.json",
                message=error,
                severity=severity,
                path=str(marketplace),
                repair="在仓库根补齐 .cursor-plugin/marketplace.json。",
            )
        )
        return
    if marketplace.suffix == ".json" and marketplace.parent.name == ".cursor-plugin":
        marketplace_name = str(payload.get("name") or "")
        if marketplace_name != MARKETPLACE_NAME:
            issues.append(
                _issue(
                    "marketplace.name",
                    message=f"本地市场名必须是 {MARKETPLACE_NAME}，当前为：{marketplace_name or '缺失'}",
                    path=str(marketplace),
                    repair=f"将 marketplace name 改为 {MARKETPLACE_NAME}。",
                )
            )
        owner = payload.get("owner")
        owner_name = str(owner.get("name") or "") if isinstance(owner, dict) else ""
        if owner_name != PLUGIN_AUTHOR:
            issues.append(
                _issue(
                    "marketplace.owner",
                    message=f"本地市场 owner 必须是 {PLUGIN_AUTHOR}，当前为：{owner_name or '缺失'}",
                    path=str(marketplace),
                    repair=f"将 owner.name 改为 {PLUGIN_AUTHOR}。",
                )
            )
    plugin = _marketplace_plugin(payload)
    if plugin is None:
        issues.append(_issue("marketplace.plugin", message=f"{PLUGIN_NAME} missing from marketplace", path=str(marketplace), repair=f"在 plugins[] 中加入 {PLUGIN_NAME}。"))
        return
    author = plugin.get("author")
    author_name = str(author.get("name") or "") if isinstance(author, dict) else ""
    if author_name != PLUGIN_AUTHOR:
        issues.append(
            _issue(
                "marketplace.plugin_author",
                message=f"市场插件维护者必须是 {PLUGIN_AUTHOR}，当前为：{author_name or '缺失'}",
                path=str(marketplace),
                repair=f"将 plugins[].author.name 改为 {PLUGIN_AUTHOR}。",
            )
        )
    description = str(plugin.get("description") or "").strip()
    if "CanonLedger" not in description:
        issues.append(
            _issue(
                "identity.marketplace_brand",
                message="市场插件描述未声明 CanonLedger 产品身份",
                path=str(marketplace),
                repair="在 plugins[].description 中使用叙典 CanonLedger 品牌名。",
            )
        )
    if str(plugin.get("license") or "") != "GPL-3.0":
        issues.append(
            _issue(
                "legal.marketplace_license",
                message="marketplace 插件条目的 license 必须为 GPL-3.0",
                path=str(marketplace),
                repair="将 plugins[].license 恢复为 GPL-3.0。",
            )
        )
    for field in ("homepage", "repository"):
        value = str(plugin.get(field) or "")
        if value != PRODUCT_REPOSITORY_URL:
            issues.append(
                _issue(
                    f"marketplace.plugin_{field}",
                    message=f"marketplace 插件条目的 {field} 必须指向当前 CanonLedger 仓库，当前为：{value or '缺失'}",
                    path=str(marketplace),
                    repair=f"将 plugins[].{field} 改为 {PRODUCT_REPOSITORY_URL}。",
                )
            )
    source = str(plugin.get("source") or "")
    allowed = _allowed_marketplace_sources(root)
    if _normalize_marketplace_source(source) not in {_normalize_marketplace_source(item) for item in allowed}:
        expected = " 或 ".join(sorted(allowed))
        issues.append(
            _issue(
                "marketplace.source",
                message=f"unexpected source: {plugin.get('source')}",
                path=str(marketplace),
                repair=f"source 应为 {expected}。",
            )
        )
    marketplace_version = str(plugin.get("version") or "")
    if plugin_version and marketplace_version != plugin_version:
        issues.append(
            _issue(
                "version.marketplace",
                message=f"plugin.json={plugin_version}, marketplace.json={marketplace_version}",
                path=str(marketplace),
                repair="运行 sync_plugin_version.py --version X.Y.Z --release-notes ...。",
            )
        )
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata_version = str(metadata.get("version") or "")
    if plugin_version and metadata_version != plugin_version:
        issues.append(
            _issue(
                "version.marketplace_metadata",
                message=f"plugin.json={plugin_version}, marketplace metadata={metadata_version or '缺失'}",
                path=str(marketplace),
                repair="同步 marketplace metadata.version。",
            )
        )


def _check_readme_version(root: Path, plugin_version: str, issues: list[dict[str, str]]) -> None:
    if _is_plugin_root(root):
        candidates = [_repo_root(root) / "README.md", root / "README.md"]
    else:
        candidates = [root / "README.md", _plugin_root(root) / "README.md"]
    readme = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    try:
        content = readme.read_text(encoding="utf-8")
        readme_version = sync_plugin_version.get_readme_current_version(content)
        readme_badge_version = sync_plugin_version.get_readme_badge_version(content)
    except Exception as exc:
        issues.append(_issue("version.readme.parse", message=str(exc), path=str(readme), repair="保持 README 版本表格式与 sync_plugin_version.py 一致。"))
        return
    if plugin_version and readme_version != plugin_version:
        issues.append(
            _issue(
                "version.readme",
                message=f"plugin.json={plugin_version}, README.md={readme_version}",
                path=str(readme),
                repair="运行 sync_plugin_version.py --version X.Y.Z --release-notes ...。",
            )
        )
    if plugin_version and readme_badge_version != plugin_version:
        issues.append(
            _issue(
                "version.readme_badge",
                message=f"plugin.json={plugin_version}, README badge={readme_badge_version}",
                path=str(readme),
                repair="运行 sync_plugin_version.py --version X.Y.Z --release-notes ...。",
            )
        )


def _check_frontmatter(root: Path, issues: list[dict[str, str]]) -> None:
    plugin_root = _plugin_root(root)
    for skill in sorted((plugin_root / "skills").glob("*/SKILL.md")):
        fm = _frontmatter(skill)
        for field in ("name", "description"):
            if not fm.get(field):
                issues.append(_issue("skill.frontmatter", message=f"skill missing {field}", path=str(skill), repair="按 plugin-dev skill-development 补齐 frontmatter。"))
    for agent in sorted((plugin_root / "agents").glob("*.md")):
        fm = _frontmatter(agent)
        for field in ("name", "description", "tools"):
            if not fm.get(field):
                issues.append(_issue("agent.frontmatter", message=f"agent missing {field}", path=str(agent), repair="按 plugin-dev agent-development 补齐 frontmatter。"))


def _runtime_dependency_rows(
    lock_payload: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    packages = lock_payload.get("packages")
    if not isinstance(packages, dict):
        return {}, ["package-lock.json 缺少 packages 对象"]
    root_package = packages.get("")
    if not isinstance(root_package, dict):
        return {}, ["package-lock.json 缺少根包记录"]
    direct = root_package.get("dependencies")
    if not isinstance(direct, dict):
        return {}, ["package-lock.json 根包缺少 dependencies 对象"]

    rows: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    pending = list(direct)
    while pending:
        name = pending.pop()
        if name in rows:
            continue
        entry = packages.get(f"node_modules/{name}")
        if not isinstance(entry, dict):
            missing.append(name)
            continue
        rows[name] = entry
        dependencies = entry.get("dependencies")
        if isinstance(dependencies, dict):
            pending.extend(str(item) for item in dependencies)
    return rows, missing


def _check_legal_compliance(root: Path, issues: list[dict[str, str]]) -> None:
    plugin_root = _plugin_root(root)
    required_documents = {
        "NOTICE.md": ("CanonLedger", "lingfengQAQ/webnovel-writer", "GPL-3.0"),
        "ATTRIBUTION.md": ("CanonLedger", "lingfengQAQ/webnovel-writer", "GNU General Public License v3"),
        "AUTHORS.md": ("CanonLedger", "Splittinglv", "lingfengQAQ"),
        "THIRD_PARTY_NOTICES.md": ("CanonLedger", "Dashboard 前端运行时", "third_party_licenses/npm/"),
    }

    license_path = plugin_root / "LICENSE"
    license_text, license_error = _load_text(license_path)
    if license_error:
        issues.append(
            _issue(
                "legal.required_file",
                message=f"缺少或无法读取 LICENSE：{license_error}",
                path=str(license_path),
                repair="恢复并随包分发 GNU GPL v3 完整正文。",
            )
        )
    else:
        gpl_markers = (
            "GNU GENERAL PUBLIC LICENSE",
            "Version 3, 29 June 2007",
            "END OF TERMS AND CONDITIONS",
        )
        license_hash = hashlib.sha256(license_text.encode("utf-8")).hexdigest()
        if (
            any(marker not in license_text for marker in gpl_markers)
            or license_hash != GPL_V3_LICENSE_SHA256
        ):
            issues.append(
                _issue(
                    "legal.gpl_text",
                    message="LICENSE 不是完整的 GNU GPL v3 正文",
                    path=str(license_path),
                    repair="恢复 GNU GPL v3 完整官方许可文本。",
                )
            )

    document_texts: dict[str, str] = {}
    for filename, markers in required_documents.items():
        path = plugin_root / filename
        content, error = _load_text(path)
        document_texts[filename] = content
        if error:
            issues.append(
                _issue(
                    "legal.required_file",
                    message=f"缺少或无法读取 {filename}：{error}",
                    path=str(path),
                    repair=f"恢复并随插件包分发 {filename}。",
                )
            )
            continue
        missing_markers = [marker for marker in markers if marker not in content]
        if missing_markers:
            issues.append(
                _issue(
                    "legal.document_content",
                    message=f"{filename} 缺少必要声明：{', '.join(missing_markers)}",
                    path=str(path),
                    repair="恢复产品身份、历史来源或第三方许可映射。",
                )
            )

    lock_path = plugin_root / "dashboard" / "frontend" / "package-lock.json"
    lock_payload, lock_error = _load_json(lock_path)
    if lock_error:
        issues.append(
            _issue(
                "legal.frontend_lock",
                message=f"无法读取前端锁文件：{lock_error}",
                path=str(lock_path),
                repair="恢复 dashboard/frontend/package-lock.json。",
            )
        )
        return

    rows, missing_entries = _runtime_dependency_rows(lock_payload)
    if missing_entries:
        issues.append(
            _issue(
                "legal.frontend_lock_closure",
                message=f"生产依赖闭包缺少锁定记录：{', '.join(sorted(missing_entries))}",
                path=str(lock_path),
                repair="重新生成完整 package-lock.json。",
            )
        )
    actual_names = set(rows)
    expected_names = set(EXPECTED_RUNTIME_LICENSES)
    if actual_names != expected_names:
        added = sorted(actual_names - expected_names)
        removed = sorted(expected_names - actual_names)
        issues.append(
            _issue(
                "legal.runtime_dependency_set",
                message=f"前端生产依赖与许可基线不一致；新增={added or '无'}，缺少={removed or '无'}",
                path=str(lock_path),
                repair="同步 EXPECTED_RUNTIME_LICENSES、THIRD_PARTY_NOTICES.md 和许可正文。",
            )
        )

    notices_text = document_texts.get("THIRD_PARTY_NOTICES.md", "")
    for name, expected_license in sorted(EXPECTED_RUNTIME_LICENSES.items()):
        entry = rows.get(name)
        if not isinstance(entry, dict):
            continue
        version = str(entry.get("version") or "")
        actual_license = str(entry.get("license") or "")
        if actual_license != expected_license:
            issues.append(
                _issue(
                    "legal.runtime_license_id",
                    message=f"{name}@{version or '未知版本'} 许可证应为 {expected_license}，锁文件为 {actual_license or '缺失'}",
                    path=str(lock_path),
                    repair="核对真实安装包并更新许可基线。",
                )
            )
        row_marker = f"| `{name}` | {version} | {actual_license} |"
        if not version or row_marker not in notices_text:
            issues.append(
                _issue(
                    "legal.third_party_mapping",
                    message=f"THIRD_PARTY_NOTICES.md 未精确映射 {name}@{version or '未知版本'}",
                    path=str(plugin_root / "THIRD_PARTY_NOTICES.md"),
                    repair="补齐包名、锁定版本、SPDX 许可标识和正文链接。",
                )
            )

        relative_license = Path("third_party_licenses") / "npm" / name / "LICENSE"
        license_copy = plugin_root / relative_license
        copied_text, copied_error = _load_text(license_copy)
        if copied_error:
            issues.append(
                _issue(
                    "legal.third_party_license_file",
                    message=f"{name} 许可正文缺失：{copied_error}",
                    path=str(license_copy),
                    repair=f"随包保留 {relative_license.as_posix()}。",
                )
            )
            continue
        required_markers = LICENSE_TEXT_MARKERS[expected_license]
        if len(copied_text) < 500 or any(marker not in copied_text for marker in required_markers):
            issues.append(
                _issue(
                    "legal.third_party_license_text",
                    message=f"{name} 许可文件不是完整的 {expected_license} 正文",
                    path=str(license_copy),
                    repair="从锁定安装包或其官方上游恢复许可正文。",
                )
            )
        if relative_license.as_posix() not in notices_text:
            issues.append(
                _issue(
                    "legal.third_party_mapping",
                    message=f"THIRD_PARTY_NOTICES.md 未链接 {name} 的许可正文",
                    path=str(plugin_root / "THIRD_PARTY_NOTICES.md"),
                    repair=f"链接 {relative_license.as_posix()}。",
                )
            )

    packages = lock_payload.get("packages")
    root_package = packages.get("") if isinstance(packages, dict) else {}
    dev_dependencies = (
        root_package.get("devDependencies")
        if isinstance(root_package, dict)
        else {}
    )
    actual_build_tools = set(dev_dependencies) if isinstance(dev_dependencies, dict) else set()
    expected_build_tools = set(EXPECTED_BUILD_TOOL_LICENSES)
    if actual_build_tools != expected_build_tools:
        added = sorted(actual_build_tools - expected_build_tools)
        removed = sorted(expected_build_tools - actual_build_tools)
        issues.append(
            _issue(
                "legal.build_dependency_set",
                message=f"前端直接构建依赖与许可基线不一致；新增={added or '无'}，缺少={removed or '无'}",
                path=str(lock_path),
                repair="同步 EXPECTED_BUILD_TOOL_LICENSES 与 THIRD_PARTY_NOTICES.md 构建工具表。",
            )
        )
    for name, expected_license in sorted(EXPECTED_BUILD_TOOL_LICENSES.items()):
        entry = packages.get(f"node_modules/{name}") if isinstance(packages, dict) else None
        if not isinstance(entry, dict):
            issues.append(
                _issue(
                    "legal.build_lock_entry",
                    message=f"前端直接构建依赖缺少锁定记录：{name}",
                    path=str(lock_path),
                    repair="重新生成完整 package-lock.json。",
                )
            )
            continue
        version = str(entry.get("version") or "")
        actual_license = str(entry.get("license") or "")
        if actual_license != expected_license:
            issues.append(
                _issue(
                    "legal.build_license_id",
                    message=f"{name}@{version or '未知版本'} 许可证应为 {expected_license}，锁文件为 {actual_license or '缺失'}",
                    path=str(lock_path),
                    repair="核对真实安装包并更新构建工具许可基线。",
                )
            )
        row_marker = f"| `{name}` | {version} | {actual_license} |"
        if not version or row_marker not in notices_text:
            issues.append(
                _issue(
                    "legal.build_mapping",
                    message=f"THIRD_PARTY_NOTICES.md 未精确映射构建工具 {name}@{version or '未知版本'}",
                    path=str(plugin_root / "THIRD_PARTY_NOTICES.md"),
                    repair="同步构建工具表中的包名、锁定版本和 SPDX 许可标识。",
                )
            )

    echarts_extras = {
        "third_party_licenses/npm/echarts/NOTICE": ("Apache ECharts", "Apache Software Foundation"),
        "third_party_licenses/npm/echarts/LICENSE-d3": ("Mike Bostock", "Redistribution and use"),
    }
    for relative, markers in echarts_extras.items():
        path = plugin_root / relative
        content, error = _load_text(path)
        if error or any(marker not in content for marker in markers):
            issues.append(
                _issue(
                    "legal.echarts_notice",
                    message=f"ECharts 附加声明缺失或不完整：{relative}",
                    path=str(path),
                    repair="从锁定的 echarts 安装包恢复 NOTICE 与 D3 许可文件。",
                )
            )
        if relative not in notices_text:
            issues.append(
                _issue(
                    "legal.third_party_mapping",
                    message=f"THIRD_PARTY_NOTICES.md 未链接 {relative}",
                    path=str(plugin_root / "THIRD_PARTY_NOTICES.md"),
                    repair="补齐 ECharts NOTICE 与内嵌 D3 许可映射。",
                )
            )


def _check_core_identity_surfaces(root: Path, issues: list[dict[str, str]]) -> None:
    plugin_root = _plugin_root(root)
    expected_names = {f"canon-ledger-{surface}" for surface in CORE_SURFACES}

    command_root = plugin_root / "commands"
    actual_commands = {path.stem for path in command_root.glob("*.md")}
    if actual_commands != expected_names:
        issues.append(
            _issue(
                "identity.command_set",
                message=f"命令集必须恰好包含 8 个 CanonLedger 核心命令，当前为：{sorted(actual_commands)}",
                path=str(command_root),
                repair="恢复 canon-ledger-dashboard/doctor/init/learn/plan/query/review/write。",
            )
        )
    for name in sorted(expected_names):
        path = command_root / f"{name}.md"
        content, error = _load_text(path)
        frontmatter = _frontmatter(path)
        if error or frontmatter.get("name") != name or name not in content:
            issues.append(
                _issue(
                    "identity.command_surface",
                    message=f"命令缺失或品牌声明不完整：{name}",
                    path=str(path),
                    repair="恢复同名 frontmatter 与 CanonLedger Skill 路由。",
                )
            )

    skill_root = plugin_root / "skills"
    actual_skills = {
        path.parent.name
        for path in skill_root.glob("*/SKILL.md")
        if path.parent.is_dir()
    }
    if actual_skills != expected_names:
        issues.append(
            _issue(
                "identity.skill_set",
                message=f"Skill 集必须恰好包含 8 个 CanonLedger 核心 Skill，当前为：{sorted(actual_skills)}",
                path=str(skill_root),
                repair="恢复 8 个 canon-ledger-* Skill。",
            )
        )
    for name in sorted(expected_names):
        path = skill_root / name / "SKILL.md"
        content, error = _load_text(path)
        frontmatter = _frontmatter(path)
        if error or frontmatter.get("name") != name or name not in content:
            issues.append(
                _issue(
                    "identity.skill_surface",
                    message=f"Skill 缺失或品牌声明不完整：{name}",
                    path=str(path),
                    repair="恢复同名 frontmatter 和 CanonLedger 命令声明。",
                )
            )

    entrypoints = {
        plugin_root / "scripts" / "canon_ledger.py": ("CanonLedger", "data_modules.canon_ledger"),
        plugin_root / "scripts" / "data_modules" / "canon_ledger.py": ("CanonLedger", ".canon-ledger"),
    }
    for path, markers in entrypoints.items():
        content, error = _load_text(path)
        if error or any(marker not in content for marker in markers):
            issues.append(
                _issue(
                    "identity.entrypoint",
                    message=f"CanonLedger 核心入口缺失或品牌哨兵不完整：{path.name}",
                    path=str(path),
                    repair="恢复 scripts/canon_ledger.py 与 data_modules/canon_ledger.py。",
                )
            )

    rule_path = plugin_root / "rules" / "canon-ledger-canon.mdc"
    rule_text, rule_error = _load_text(rule_path)
    if rule_error or any(marker not in rule_text for marker in ("CanonLedger", ".canon-ledger", ".story-system")):
        issues.append(
            _issue(
                "identity.rule",
                message="CanonLedger 真源规则缺失或品牌哨兵不完整",
                path=str(rule_path),
                repair="恢复 rules/canon-ledger-canon.mdc。",
            )
        )

    frontend_root = plugin_root / "dashboard" / "frontend"
    for relative in (Path("index.html"), Path("dist/index.html")):
        path = frontend_root / relative
        content, error = _load_text(path)
        if error or "CanonLedger Dashboard" not in content:
            issues.append(
                _issue(
                    "identity.dashboard_brand",
                    message=f"Dashboard 品牌哨兵缺失：{relative.as_posix()}",
                    path=str(path),
                    repair="在源 HTML 与构建产物中保留 CanonLedger Dashboard 标题。",
            )
        )
    app_path = frontend_root / "src" / "App.jsx"
    app_text, app_error = _load_text(app_path)
    if app_error or "叙典 CANONLEDGER" not in app_text:
        issues.append(
            _issue(
                "identity.dashboard_visible_brand",
                message="Dashboard 源码侧栏缺少可见品牌“叙典 CANONLEDGER”",
                path=str(app_path),
                repair="在 src/App.jsx 的可见侧栏标题中恢复“叙典 CANONLEDGER”。",
            )
        )
    if "PIXEL WRITER HUB" in app_text:
        issues.append(
            _issue(
                "identity.dashboard_legacy_brand",
                message="Dashboard 源码仍包含旧侧栏文案“PIXEL WRITER HUB”",
                path=str(app_path),
                repair="删除旧侧栏文案并重新构建 Dashboard。",
            )
        )

    dist_root = frontend_root / "dist"
    dist_javascript = sorted(dist_root.rglob("*.js"))
    readable_dist: dict[Path, str] = {}
    for path in dist_javascript:
        content, error = _load_text(path)
        if not error:
            readable_dist[path] = content
    if not any("叙典 CANONLEDGER" in content for content in readable_dist.values()):
        issues.append(
            _issue(
                "identity.dashboard_dist_visible_brand",
                message="Dashboard 构建产物未保留可见品牌“叙典 CANONLEDGER”",
                path=str(dist_root),
                repair="使用当前源码重新构建 Dashboard，并提交新的 dist 产物。",
            )
        )
    legacy_dist = [
        path
        for path, content in readable_dist.items()
        if "PIXEL WRITER HUB" in content
    ]
    if legacy_dist:
        issues.append(
            _issue(
                "identity.dashboard_dist_legacy_brand",
                message="Dashboard 构建产物仍包含旧侧栏文案“PIXEL WRITER HUB”",
                path=", ".join(str(path) for path in legacy_dist),
                repair="清理旧文案后重新构建 Dashboard，并删除过期构建文件。",
            )
        )
    dist_assets = frontend_root / "dist" / "assets"
    if not any(dist_assets.glob("*.js")) or not any(dist_assets.glob("*.css")):
        issues.append(
            _issue(
                "dashboard.dist_assets",
                message="Dashboard 构建产物缺少 JavaScript 或 CSS 资源",
                path=str(dist_assets),
                repair="重新构建 Dashboard 并随包保留 dist/assets。",
            )
        )
    package_json, package_error = _load_json(frontend_root / "package.json")
    lock_json, lock_error = _load_json(frontend_root / "package-lock.json")
    lock_root = lock_json.get("packages", {}).get("") if isinstance(lock_json.get("packages"), dict) else {}
    if (
        package_error
        or lock_error
        or str(package_json.get("name") or "") != "canon-ledger-dashboard"
        or str(lock_json.get("name") or "") != "canon-ledger-dashboard"
        or not isinstance(lock_root, dict)
        or str(lock_root.get("name") or "") != "canon-ledger-dashboard"
    ):
        issues.append(
            _issue(
                "identity.dashboard_package",
                message="Dashboard package.json 与 package-lock.json 未统一为 canon-ledger-dashboard",
                path=str(frontend_root),
                repair="同步前端包名和锁文件根包名。",
            )
        )


def _check_optional_assets(root: Path, issues: list[dict[str, str]]) -> None:
    plugin_root = _plugin_root(root)
    dashboard_dist = plugin_root / "dashboard" / "frontend" / "dist"
    if not dashboard_dist.is_dir():
        issues.append(_issue("dashboard.dist", message="dashboard frontend dist missing", severity="warning", path=str(dashboard_dist), repair="发布前运行 dashboard 前端 build 并包含 dist。"))
    hooks_json = plugin_root / "hooks" / "hooks.json"
    if hooks_json.exists():
        payload, error = _load_json(hooks_json)
        if error:
            issues.append(_issue("hooks.schema", message=error, path=str(hooks_json), repair="修复 hooks/hooks.json。"))
        elif "description" not in payload or "hooks" not in payload:
            issues.append(_issue("hooks.wrapper", message="hooks.json should use plugin-dev wrapper format", path=str(hooks_json), repair="外层包含 description 与 hooks。"))
        else:
            hooks = payload.get("hooks")
            if not isinstance(hooks, dict):
                issues.append(_issue("hooks.schema", message="hooks must be an object", path=str(hooks_json), repair="修复 hooks/hooks.json 的 hooks 对象。"))
            else:
                expected_bootstrap = {
                    "sessionStart": "session_start",
                    "preToolUse": "guard_runtime_write",
                    "beforeShellExecution": "guard_runtime_write",
                }
                for event, selector in expected_bootstrap.items():
                    entries = hooks.get(event)
                    if not isinstance(entries, list) or not entries:
                        continue
                    for entry in entries:
                        command = str(entry.get("command") or "") if isinstance(entry, dict) else ""
                        if (
                            '${CURSOR_PLUGIN_ROOT}/hooks/run_hook.py' not in command
                            or not command.rstrip().endswith(selector)
                        ):
                            issues.append(
                                _issue(
                                    "hooks.runtime_bootstrap",
                                    message=f"{event} 未通过依赖解释器启动器运行",
                                    path=str(hooks_json),
                                    repair="统一通过 hooks/run_hook.py 启动运行时 Hook。",
                                )
                            )
                for event in ("preToolUse", "beforeShellExecution"):
                    entries = hooks.get(event)
                    if not isinstance(entries, list) or not entries or any(
                        not isinstance(entry, dict) or entry.get("failClosed") is not True
                        for entry in entries
                    ):
                        issues.append(
                            _issue(
                                "hooks.fail_closed",
                                message=f"{event} must fail closed",
                                path=str(hooks_json),
                                repair=f"为 {event} 的每个执行型 hook 设置 failClosed: true。",
                            )
                        )
                pre_tool = hooks.get("preToolUse")
                if isinstance(pre_tool, list) and pre_tool:
                    matcher = "|".join(
                        str(entry.get("matcher") or "")
                        for entry in pre_tool
                        if isinstance(entry, dict)
                    )
                    if "Delete" not in matcher.split("|"):
                        issues.append(
                            _issue(
                                "hooks.delete_matcher",
                                message="preToolUse does not match Delete",
                                path=str(hooks_json),
                                repair="将 Delete 加入 runtime guard 的 matcher。",
                            )
                        )

            for relative in ("run_hook.py", "session_start.py", "guard_runtime_write.py"):
                target = hooks_json.parent / relative
                if not target.is_file():
                    issues.append(
                        _issue(
                            "hooks.runtime_file",
                            message=f"缺少 Hook 运行文件：{relative}",
                            path=str(target),
                            repair="恢复完整的 Hook 运行文件后重新打包。",
                        )
                    )


def _check_portability(root: Path, issues: list[dict[str, str]]) -> None:
    plugin_root = _plugin_root(root)
    targets = list((plugin_root / "skills").glob("*/SKILL.md"))
    targets.extend((plugin_root / "agents").glob("*.md"))
    targets.extend((plugin_root / ".cursor-plugin").glob("*.json"))
    hooks_root = plugin_root / "hooks"
    if hooks_root.is_dir():
        targets.extend(path for path in hooks_root.rglob("*") if path.suffix in {".json", ".py", ".sh", ".md"})
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if LOCAL_ABSOLUTE_RE.search(text):
            issues.append(
                _issue(
                    "portability.local_absolute_path",
                    message="local absolute path found in plugin component",
                    severity="warning",
                    path=str(path),
                    repair="插件组件内使用 ${CANON_LEDGER_PLUGIN_ROOT}、${CURSOR_PLUGIN_ROOT} 或相对路径。",
                )
            )
        if path.name == "SKILL.md" and (
            re.search(r"(?m)^\s*eval\s+", text)
            or "Invoke-Expression" in text
            or ("export_cursor_env.py" in text and ".rglob(" in text)
        ):
            issues.append(
                _issue(
                    "security.skill_bootstrap_execution",
                    message="skill bootstrap executes generated output or scans cache for an exporter",
                    path=str(path),
                    repair="使用受信插件根与固定 JSON 数据协议；禁止 eval/source/Invoke-Expression 和缓存扫描。",
                )
            )


def _check_deprecated_entrypoints(root: Path, issues: list[dict[str, str]]) -> None:
    """Reject legacy product entrypoints from a CanonLedger package."""
    plugin_root = _plugin_root(root)
    legacy_paths = [
        plugin_root / "scripts" / "webnovel.py",
        plugin_root / "scripts" / "data_modules" / "webnovel.py",
    ]
    legacy_paths.extend((plugin_root / "commands").glob("webnovel-*.md"))
    legacy_paths.extend((plugin_root / "skills").glob("webnovel-*"))
    for path in legacy_paths:
        if path.exists():
            issues.append(
                _issue(
                    "identity.legacy_entrypoint",
                    message=f"正式包不得包含旧产品入口：{path.relative_to(plugin_root)}",
                    path=str(path),
                    repair="删除旧入口，只保留 canon-ledger-* 与 canon_ledger.py。",
                )
            )

    for skill in (plugin_root / "skills").glob("canon-ledger-*/SKILL.md"):
        try:
            text = skill.read_text(encoding="utf-8")
        except OSError:
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in ("webnovel", ".webnovel", "claude")):
            issues.append(
                _issue(
                    "identity.legacy_environment",
                    message="正式 Skill 仍暴露旧产品或其他宿主命名",
                    path=str(skill),
                    repair="只使用 CanonLedger/Cursor 名称、CANON_LEDGER_* 与 .canon-ledger。",
                )
            )


def validate_package(root: str | Path | None = None, *, strict: bool = False) -> dict[str, Any]:
    if root is not None:
        repo_root = Path(root)
    else:
        here = Path(__file__).resolve()
        plugin_root = here.parent.parent
        repo_root = plugin_root if (plugin_root / "scripts" / "canon_ledger.py").is_file() else here.parent.parent.parent
    issues: list[dict[str, str]] = []
    _, plugin_version = _check_manifest(repo_root, issues)
    _check_marketplace(repo_root, plugin_version, issues)
    _check_readme_version(repo_root, plugin_version, issues)
    _check_frontmatter(repo_root, issues)
    _check_legal_compliance(repo_root, issues)
    _check_core_identity_surfaces(repo_root, issues)
    _check_optional_assets(repo_root, issues)
    _check_portability(repo_root, issues)
    _check_deprecated_entrypoints(repo_root, issues)
    blocking = [
        item for item in issues if item["severity"] == "error" or (strict and item["severity"] == "warning")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not blocking,
        "strict": strict,
        "root": str(repo_root),
        "error_count": sum(1 for item in issues if item["severity"] == "error"),
        "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
        "issues": issues,
    }


def format_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    lines = [
        f"{status} plugin package",
        f"errors: {report.get('error_count')} warnings: {report.get('warning_count')}",
    ]
    for item in report.get("issues") or []:
        lines.append(f"{item.get('severity', '').upper()} {item.get('code')}: {item.get('message')}")
        if item.get("path"):
            lines.append(f"  path: {item.get('path')}")
        if item.get("repair"):
            lines.append(f"  repair: {item.get('repair')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验叙典 CanonLedger 插件包元数据与组件")
    parser.add_argument("--root", default="", help="仓库根目录，默认自动推断")
    parser.add_argument("--strict", action="store_true", help="warning 也视为失败")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = validate_package(args.root or None, strict=args.strict)
    print(format_report(report, args.format))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

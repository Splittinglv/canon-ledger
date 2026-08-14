#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import sync_plugin_version


ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = sync_plugin_version.VERSION_PATTERN
REQUIRED_RELEASE_HEADINGS = (
    "## 发版范围",
    "## 给作者看的变化",
    "## 安装方式",
    "## 给维护者",
    "## 验证",
)
AUTHOR_WORDS = ("作者", "写章", "故事", "正文", "设定", "伏笔")


def _issue(code: str, *, message: str, path: str = "", repair: str = "") -> dict[str, str]:
    return {"code": code, "message": message, "path": path, "repair": repair}


def _load_text(path: Path) -> tuple[str, str]:
    try:
        return path.read_text(encoding="utf-8"), ""
    except FileNotFoundError:
        return "", "missing"
    except OSError as exc:
        return "", f"read_error:{exc}"


def _current_version(root: Path) -> str:
    payload = sync_plugin_version.load_json(sync_plugin_version.resolve_plugin_manifest(root))
    return str(payload.get("version") or "")


def _parse_version_tag(tag: str) -> tuple[int, int, int] | None:
    raw = tag[1:] if tag.startswith("v") else tag
    if not VERSION_RE.fullmatch(raw):
        return None
    major, minor, patch = raw.split(".")
    return int(major), int(minor), int(patch)


def _infer_previous_tag(root: Path, version: str) -> str:
    current = _parse_version_tag(version)
    if current is None:
        return ""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "tag", "--list", "v*"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for line in completed.stdout.splitlines():
        tag = line.strip()
        parsed = _parse_version_tag(tag)
        if parsed and parsed < current:
            candidates.append((parsed, tag))
    if not candidates:
        return ""
    return sorted(candidates)[-1][1]


def _git_release_issues(root: Path, version: str) -> list[dict[str, str]]:
    """在 Git 工作区执行发版校验时，同时验证版本标签历史。"""
    if not (root / ".git").exists():
        return []
    try:
        tags = subprocess.run(
            ["git", "-C", str(root), "tag", "--list", "v*"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        return [
            _issue(
                "git.unavailable",
                message=f"无法检查 Git 发版历史：{exc}",
                path=str(root),
                repair="在完整 Git checkout 中重新执行发版校验。",
            )
        ]
    if tags.returncode != 0 or head.returncode != 0 or dirty.returncode != 0:
        return [
            _issue(
                "git.unavailable",
                message="无法读取 Git tag、HEAD 或工作区状态",
                path=str(root),
                repair="修复 Git 仓库后重新执行发版校验。",
            )
        ]

    issues: list[dict[str, str]] = []
    semantic_tags = [
        tag.strip()
        for tag in tags.stdout.splitlines()
        if _parse_version_tag(tag.strip()) is not None
    ]
    if not semantic_tags:
        issues.append(
            _issue(
                "git.tag_history_missing",
                message="正式发版仓库没有任何可比较的 vX.Y.Z tag",
                path=str(root),
                repair="先为已有正式版本补建并推送 tag，再发布新版本。",
            )
        )
    current_version = _parse_version_tag(version)
    parsed_tags = [
        (parsed, tag)
        for tag in semantic_tags
        if (parsed := _parse_version_tag(tag)) is not None
    ]
    if current_version is not None and parsed_tags:
        highest_version, highest_tag = max(parsed_tags)
        if current_version < highest_version:
            issues.append(
                _issue(
                    "git.version_not_monotonic",
                    message=f"目标版本 v{version} 低于已有最高正式版本 {highest_tag}",
                    path=str(root),
                    repair="将目标版本递增到已有最高正式版本之上。",
                )
            )
    current_tag = f"v{version}"
    if current_tag in semantic_tags:
        target = subprocess.run(
            ["git", "-C", str(root), "rev-list", "-n", "1", current_tag],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if target.returncode != 0 or target.stdout.strip() != head.stdout.strip():
            issues.append(
                _issue(
                    "git.version_reused_after_tag",
                    message=f"版本 {version} 的 tag 不在当前 HEAD，不能继续复用同一版本号承载新提交",
                    path=str(root),
                    repair="递增版本号，并新增对应 release note 与 CHANGELOG 小节。",
                )
            )
    if dirty.stdout.strip():
        issues.append(
            _issue(
                "git.worktree_dirty",
                message="发版校验时工作区仍有未提交变更",
                path=str(root),
                repair="提交并复跑全部验证后再执行正式发版校验。",
            )
        )
    return issues


def _changelog_section(text: str, version: str) -> str:
    heading_re = re.compile(rf"^##\s+v{re.escape(version)}(?:\s|$)", re.MULTILINE)
    match = heading_re.search(text)
    if not match:
        return ""
    next_match = re.search(r"^##\s+", text[match.end():], re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(text)
    return text[match.start():end]


def validate_release_notes(
    root: str | Path | None = None,
    *,
    version: str | None = None,
    previous_tag: str | None = None,
) -> dict[str, Any]:
    repo_root = (Path(root) if root is not None else ROOT).resolve()
    issues: list[dict[str, str]] = []

    if version:
        target_version = version
    else:
        try:
            target_version = _current_version(repo_root)
        except (OSError, TypeError, ValueError) as exc:
            target_version = ""
            issues.append(
                _issue(
                    "layout.plugin_manifest",
                    message=str(exc),
                    path=str(repo_root),
                    repair="恢复根级 .cursor-plugin/plugin.json。",
                )
            )
    previous = previous_tag or _infer_previous_tag(repo_root, target_version)

    if not VERSION_RE.fullmatch(target_version):
        issues.append(_issue("version.invalid", message=f"invalid version: {target_version}", repair="使用 X.Y.Z 版本号。"))
    else:
        issues.extend(_git_release_issues(repo_root, target_version))

    release_path = repo_root / "releases" / f"v{target_version}.md"
    release_text, release_error = _load_text(release_path)
    if release_error:
        issues.append(
            _issue(
                "release_note.missing",
                message=f"release note {release_path.name} {release_error}",
                path=str(release_path),
                repair="新增 releases/vX.Y.Z.md，并覆盖上个 tag 到本次发布的全部变化。",
            )
        )
    else:
        expected_title = f"# v{target_version} - "
        if not release_text.startswith(expected_title):
            issues.append(
                _issue(
                    "release_note.title",
                    message=f"release note title must start with {expected_title!r}",
                    path=str(release_path),
                    repair="标题使用 '# vX.Y.Z - 一句中文用户收益'。",
                )
            )
        for heading in REQUIRED_RELEASE_HEADINGS:
            if heading not in release_text:
                issues.append(
                    _issue(
                        "release_note.heading",
                        message=f"missing heading: {heading}",
                        path=str(release_path),
                        repair="使用 releases/README.md 中的固定模板。",
                    )
                )
        expected_range = f"{previous}..v{target_version}" if previous else ""
        if expected_range and expected_range not in release_text:
            issues.append(
                _issue(
                    "release_note.range",
                    message=f"发行说明缺少精确发版范围 {expected_range}",
                    path=str(release_path),
                    repair=f"在“发版范围”中写明 `{expected_range}`。",
                )
            )
        if not any(word in release_text for word in AUTHOR_WORDS):
            issues.append(
                _issue(
                    "release_note.audience",
                    message="release note does not look author-facing",
                    path=str(release_path),
                    repair="发布说明顶部必须使用中文作者能理解的场景语言。",
                )
            )

    changelog_path = repo_root / "CHANGELOG.md"
    changelog_text, changelog_error = _load_text(changelog_path)
    if changelog_error:
        issues.append(
            _issue(
                "changelog.missing",
                message=f"CHANGELOG.md {changelog_error}",
                path=str(changelog_path),
                repair="新增 CHANGELOG.md，记录每个正式版本的用户可感知变化。",
            )
        )
    else:
        current_changelog_section = _changelog_section(changelog_text, target_version)
        if not current_changelog_section:
            issues.append(
                _issue(
                    "changelog.version",
                    message=f"CHANGELOG.md missing v{target_version}",
                    path=str(changelog_path),
                    repair="在 CHANGELOG.md 中新增当前版本小节。",
                )
            )
        expected_range = f"{previous}..v{target_version}" if previous else ""
        if expected_range and current_changelog_section and expected_range not in current_changelog_section:
            issues.append(
                _issue(
                    "changelog.range",
                    message=f"CHANGELOG.md 当前版本小节缺少精确发版范围 {expected_range}",
                    path=str(changelog_path),
                    repair=f"在当前版本小节写明 `{expected_range}`。",
                )
            )

    return {
        "schema_version": "canon-ledger-release-notes-validator/v1",
        "ok": not issues,
        "root": str(repo_root),
        "version": target_version,
        "previous_tag": previous,
        "release_note": str(release_path),
        "changelog": str(changelog_path),
        "issues": issues,
    }


def format_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    lines = [
        f"{status} release notes",
        f"version: {report.get('version')}",
        f"previous_tag: {report.get('previous_tag') or 'unknown'}",
    ]
    for item in report.get("issues") or []:
        lines.append(f"ERROR {item.get('code')}: {item.get('message')}")
        if item.get("path"):
            lines.append(f"  path: {item.get('path')}")
        if item.get("repair"):
            lines.append(f"  repair: {item.get('repair')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate author-facing release notes and changelog")
    parser.add_argument("--root", default="", help="仓库根目录，默认自动推断")
    parser.add_argument("--version", default="", help="目标版本；默认读取 plugin.json")
    parser.add_argument("--previous-tag", default="", help="上一个正式 tag；默认从 git tag 推断")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = validate_release_notes(
        args.root or None,
        version=args.version or None,
        previous_tag=args.previous_tag or None,
    )
    print(format_report(report, args.format))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

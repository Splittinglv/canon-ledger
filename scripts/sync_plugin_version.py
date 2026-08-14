from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "canon-ledger"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
README_ROW_PATTERN = re.compile(
    r"^\| \*\*v(?P<version>[^\s*]+)(?P<current> \(当前\))?\*\* \| (?P<notes>.*) \|$"
)
README_BADGE_PATTERN = re.compile(r"(badge/version-)(?P<version>\d+\.\d+\.\d+)(-brightgreen\.svg)")
README_HEADERS = {"| 版本 | 说明 |", "| 版本 | 主要变化 |"}
README_SEPARATORS = {"|------|------|", "|------|----------|"}


@dataclass(frozen=True)
class ReleaseLayout:
    root: Path
    plugin_json: Path
    marketplace_json: Path
    readme: Path


def _layout_candidates(root: Path) -> list[ReleaseLayout]:
    """Return supported release layouts in preferred order.

    Cursor's flat plugin layout is canonical. A nested Cursor package layout
    is also supported for repository packaging.
    """

    root = root.resolve()
    candidates = [
        ReleaseLayout(
            root=root,
            plugin_json=root / ".cursor-plugin" / "plugin.json",
            marketplace_json=root / ".cursor-plugin" / "marketplace.json",
            readme=root / "README.md",
        ),
    ]
    for directory in (PLUGIN_NAME,):
        candidates.extend(
            [
                ReleaseLayout(
                    root=root,
                    plugin_json=root / directory / ".cursor-plugin" / "plugin.json",
                    marketplace_json=root / ".cursor-plugin" / "marketplace.json",
                    readme=root / "README.md",
                ),
            ]
        )
    return candidates


def resolve_plugin_manifest(root: str | Path | None = None) -> Path:
    repo_root = Path(root) if root is not None else ROOT
    candidates = [layout.plugin_json for layout in _layout_candidates(repo_root)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    checked = "\n- ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"plugin.json not found; checked:\n- {checked}")


def resolve_release_layout(root: str | Path | None = None) -> ReleaseLayout:
    repo_root = Path(root) if root is not None else ROOT
    candidates = _layout_candidates(repo_root)
    for layout in candidates:
        if (
            layout.plugin_json.is_file()
            and layout.marketplace_json.is_file()
            and layout.readme.is_file()
        ):
            return layout
    checked = "\n- ".join(
        f"plugin={layout.plugin_json}, marketplace={layout.marketplace_json}, readme={layout.readme}"
        for layout in candidates
    )
    raise FileNotFoundError(f"complete release layout not found; checked:\n- {checked}")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def save_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    save_text(path, content)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        target_mode = 0o644
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp_name, target_mode)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def get_marketplace_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    plugins = payload.get("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError("marketplace.json plugins must be an array")
    for plugin in plugins:
        if isinstance(plugin, dict) and plugin.get("name") == PLUGIN_NAME:
            return plugin
    raise ValueError(f"marketplace.json 未找到插件条目：{PLUGIN_NAME}")


def parse_readme_rows(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = README_ROW_PATTERN.match(line.strip())
        if not match:
            continue
        rows.append(
            {
                "index": index,
                "version": match.group("version"),
                "notes": match.group("notes"),
                "is_current": bool(match.group("current")),
            }
        )
    return rows


def format_readme_row(version: str, notes: str, is_current: bool) -> str:
    marker = " (当前)" if is_current else ""
    return f"| **v{version}{marker}** | {notes.strip()} |"


def get_readme_current_version(content: str) -> str:
    rows = parse_readme_rows(content.splitlines())
    current_rows = [row for row in rows if row["is_current"]]
    if len(current_rows) != 1:
        raise ValueError("README.md must contain exactly one current release row")
    return str(current_rows[0]["version"])


def get_readme_badge_version(content: str) -> str:
    match = README_BADGE_PATTERN.search(content)
    if not match:
        raise ValueError("README.md version badge not found")
    return str(match.group("version"))


def update_readme_badge(content: str, version: str) -> str:
    if not README_BADGE_PATTERN.search(content):
        raise ValueError("README.md version badge not found")
    return README_BADGE_PATTERN.sub(rf"\g<1>{version}\g<3>", content, count=1)


def update_readme_release(content: str, version: str, release_notes: str | None) -> str:
    content = update_readme_badge(content, version)
    lines = content.splitlines()

    try:
        header_index = next(index for index, line in enumerate(lines) if line.strip() in README_HEADERS)
    except StopIteration as error:
        raise ValueError("README.md release table header not found") from error

    separator_index = header_index + 1
    if separator_index >= len(lines) or lines[separator_index].strip() not in README_SEPARATORS:
        raise ValueError("README.md release table separator not found")

    rows = parse_readme_rows(lines)
    target_row = next((row for row in rows if row["version"] == version), None)

    for row in rows:
        is_target = row["version"] == version
        notes = release_notes if is_target and release_notes is not None else row["notes"]
        lines[row["index"]] = format_readme_row(row["version"], notes, is_target)

    if target_row is None:
        if not release_notes:
            raise ValueError(
                "Release notes are required when the target version does not exist in README.md"
            )
        lines.insert(separator_index + 1, format_readme_row(version, release_notes, True))

    return "\n".join(lines) + "\n"


def sync_versions(
    version: str | None = None,
    release_notes: str | None = None,
    *,
    root: str | Path | None = None,
) -> tuple[str, str, bool]:
    layout = resolve_release_layout(root)
    plugin_payload = load_json(layout.plugin_json)
    marketplace_payload = load_json(layout.marketplace_json)
    readme_content = load_text(layout.readme)
    marketplace_plugin = get_marketplace_plugin(marketplace_payload)

    previous_version = str(plugin_payload.get("version", ""))
    target_version = version or previous_version
    changed = False

    if plugin_payload.get("version") != target_version:
        plugin_payload["version"] = target_version
        changed = True

    if marketplace_plugin.get("version") != target_version:
        marketplace_plugin["version"] = target_version
        changed = True

    marketplace_metadata = marketplace_payload.get("metadata")
    if isinstance(marketplace_metadata, dict) and marketplace_metadata.get("version") != target_version:
        marketplace_metadata["version"] = target_version
        changed = True

    updated_readme = update_readme_release(readme_content, target_version, release_notes)
    if updated_readme != readme_content:
        changed = True

    if changed:
        # All inputs and transformations are validated before the first write.
        save_json(layout.plugin_json, plugin_payload)
        save_json(layout.marketplace_json, marketplace_payload)
        save_text(layout.readme, updated_readme)

    return previous_version, target_version, changed


def check_versions(
    expected_version: str | None = None,
    *,
    root: str | Path | None = None,
) -> int:
    layout = resolve_release_layout(root)
    plugin_payload = load_json(layout.plugin_json)
    marketplace_payload = load_json(layout.marketplace_json)
    readme_content = load_text(layout.readme)
    marketplace_plugin = get_marketplace_plugin(marketplace_payload)

    plugin_version = str(plugin_payload.get("version", ""))
    marketplace_version = str(marketplace_plugin.get("version", ""))
    marketplace_metadata = marketplace_payload.get("metadata")
    marketplace_metadata_version = (
        str(marketplace_metadata.get("version", ""))
        if isinstance(marketplace_metadata, dict) and "version" in marketplace_metadata
        else ""
    )
    readme_version = get_readme_current_version(readme_content)
    readme_badge_version = get_readme_badge_version(readme_content)

    mismatches: list[str] = []
    if plugin_version != marketplace_version:
        mismatches.append(
            f"plugin.json={plugin_version}, marketplace.json={marketplace_version}"
        )
    if marketplace_metadata_version and plugin_version != marketplace_metadata_version:
        mismatches.append(
            f"plugin.json={plugin_version}, marketplace metadata={marketplace_metadata_version}"
        )
    if plugin_version != readme_version:
        mismatches.append(f"plugin.json={plugin_version}, README.md={readme_version}")
    if plugin_version != readme_badge_version:
        mismatches.append(f"plugin.json={plugin_version}, README badge={readme_badge_version}")
    if expected_version and plugin_version != expected_version:
        mismatches.append(
            f"expected={expected_version}, current release metadata={plugin_version}"
        )

    if mismatches:
        print("Version mismatch detected:")
        for mismatch in mismatches:
            print(f"- {mismatch}")
        return 1

    print(f"Versions are in sync: {plugin_version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 CanonLedger Cursor 插件发行元数据")
    parser.add_argument("--root", default="", help="Repository root; defaults to the script's repository")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether plugin metadata and README release info are in sync",
    )
    parser.add_argument(
        "--version",
        help="Update release metadata to the given semantic version",
    )
    parser.add_argument(
        "--expected-version",
        help="When used with --check, require the current release metadata to match this version",
    )
    parser.add_argument(
        "--release-notes",
        help="Release notes used for the README current release row",
    )
    args = parser.parse_args()

    if args.version and not VERSION_PATTERN.fullmatch(args.version):
        parser.error("--version must look like X.Y.Z")
    if args.expected_version and not VERSION_PATTERN.fullmatch(args.expected_version):
        parser.error("--expected-version must look like X.Y.Z")
    if args.expected_version and not args.check:
        parser.error("--expected-version can only be used together with --check")

    try:
        if args.check:
            return check_versions(
                expected_version=args.expected_version,
                root=args.root or None,
            )

        previous_version, target_version, changed = sync_versions(
            version=args.version,
            release_notes=args.release_notes,
            root=args.root or None,
        )
    except (OSError, ValueError) as error:
        print(f"Error: {error}")
        return 1

    if changed:
        print(f"Updated release metadata: {previous_version} -> {target_version}")
    else:
        print(f"No changes needed. Current version: {target_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

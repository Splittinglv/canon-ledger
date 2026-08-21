#!/usr/bin/env python3
"""Validate repository-local Markdown links and eval fixture paths."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
_SKIP_PARTS = frozenset(
    {
        ".git",
        ".venv",
        ".tmp",
        ".tox",
        ".nox",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".pytest_cache",
    }
)
_MARKDOWN_LINK = re.compile(
    r"!?\[[^\]]*\]\(\s*(?P<target><[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)"
)


def _repository_files(root: Path, suffix: str) -> Iterable[Path]:
    for path in sorted(root.rglob(f"*{suffix}")):
        if path.is_file() and not _SKIP_PARTS.intersection(path.relative_to(root).parts):
            yield path


def _resolve_local_target(root: Path, source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = unquote(target)
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith("#"):
        return None
    local_path = parsed.path
    if not local_path:
        return None
    if local_path.startswith("/"):
        resolved = (root / local_path.lstrip("/")).resolve()
    else:
        resolved = (source.parent / local_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return resolved
    return resolved


def _walk_eval_file_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "files" and isinstance(child, list):
                for item in child:
                    if isinstance(item, str) and item.strip():
                        yield item.strip()
            else:
                yield from _walk_eval_file_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_eval_file_values(child)


def validate_document_links(root: str | Path = ROOT) -> list[dict[str, str]]:
    repo_root = Path(root).expanduser().resolve()
    issues: list[dict[str, str]] = []

    for source in _repository_files(repo_root, ".md"):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(
                {
                    "code": "markdown.unreadable",
                    "source": source.relative_to(repo_root).as_posix(),
                    "target": "",
                    "detail": exc.__class__.__name__,
                }
            )
            continue
        for match in _MARKDOWN_LINK.finditer(text):
            raw_target = match.group("target")
            resolved = _resolve_local_target(repo_root, source, raw_target)
            if resolved is None:
                continue
            try:
                relative = resolved.relative_to(repo_root).as_posix()
            except ValueError:
                relative = str(resolved)
                issues.append(
                    {
                        "code": "markdown.target.outside_repository",
                        "source": source.relative_to(repo_root).as_posix(),
                        "target": raw_target,
                        "detail": relative,
                    }
                )
                continue
            if not resolved.exists():
                issues.append(
                    {
                        "code": "markdown.target.missing",
                        "source": source.relative_to(repo_root).as_posix(),
                        "target": raw_target,
                        "detail": relative,
                    }
                )

    for source in _repository_files(repo_root, ".json"):
        if "evals" not in source.parts:
            continue
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            issues.append(
                {
                    "code": "eval_json.unreadable",
                    "source": source.relative_to(repo_root).as_posix(),
                    "target": "",
                    "detail": exc.__class__.__name__,
                }
            )
            continue
        for raw_target in _walk_eval_file_values(payload):
            resolved = (repo_root / raw_target).resolve()
            try:
                relative = resolved.relative_to(repo_root).as_posix()
            except ValueError:
                issues.append(
                    {
                        "code": "eval_file.outside_repository",
                        "source": source.relative_to(repo_root).as_posix(),
                        "target": raw_target,
                        "detail": str(resolved),
                    }
                )
                continue
            if not resolved.is_file():
                issues.append(
                    {
                        "code": "eval_file.missing",
                        "source": source.relative_to(repo_root).as_posix(),
                        "target": raw_target,
                        "detail": relative,
                    }
                )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查仓库内 Markdown 链接与 eval files 路径"
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    issues = validate_document_links(args.root)
    if args.format == "json":
        print(
            json.dumps(
                {"ok": not issues, "issue_count": len(issues), "issues": issues},
                ensure_ascii=False,
                indent=2,
            )
        )
    elif issues:
        for issue in issues:
            print(
                f"{issue['code']}: {issue['source']} -> "
                f"{issue['target']} ({issue['detail']})"
            )
    else:
        print("Document links and eval fixture paths are valid.")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())

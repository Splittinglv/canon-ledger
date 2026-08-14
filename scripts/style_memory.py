#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /canon-ledger-learn 写入本书的长期文风提示词，不进入事实通道。"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from runtime_compat import enable_windows_utf8_stdio
from data_modules.fact_text import contains_jailbreak, normalize_author_text

try:
    from security_utils import _replace_with_retry, resolve_inside_project
except ImportError:  # pragma: no cover
    from scripts.security_utils import _replace_with_retry, resolve_inside_project


STYLE_PROMPT_RELATIVE = Path("设定集") / "文风提示词.md"
AUTHOR_HEADING_RE = re.compile(r"^(#{1,6})\s*作者提示词\s*$")
NEXT_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)(.+?)\s*$")
PLACEHOLDER_RE = re.compile(r"^（在此填写")
_ITEM_LIMIT = 200
_ITEM_MAX_CHARS = 500
_FILE_SIZE_LIMIT = 2 * 1024 * 1024
_INPUT_SIZE_LIMIT = 64 * 1024
_DEFAULT_TEMPLATE = """# 文风提示词

> 本文件由作者手改，插件**不会**用网文腔、Anti-AI 词库或风格适配覆盖它。
> `/canon-ledger-write` 起草时只把「作者提示词」标题下的正文交给模型。
> 上面的说明和 HTML 注释不会进写作任务书。

<!--
可以写：叙事视角、句长偏好、对话习惯、禁忌修辞、想贴近的作品、是否文言/白话。
多主角时也可写 POV 分配、轮换和防止抢戏。
可以留空：留空则按当前模型默认文风写。
不要在这里写剧情、设定、伏笔——那些走大纲和设定集。
-->

## 作者提示词

（在此填写。可整段删除这行占位，换成你的要求。）
"""


def _style_prompt_path(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    target = root / STYLE_PROMPT_RELATIVE
    resolve_inside_project(root, target, reject_leaf_symlink=True)
    return target


def _atomic_write_text(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > _FILE_SIZE_LIMIT:
        raise ValueError(f"文风提示词超过上限：最多 {_FILE_SIZE_LIMIT} 字节")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.stem + "_",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _default_template() -> str:
    template = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "output"
        / "设定集-文风提示词.md"
    )
    if template.is_file():
        try:
            text = template.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if text.strip():
            return text if text.endswith("\n") else text + "\n"
    return _DEFAULT_TEMPLATE if _DEFAULT_TEMPLATE.endswith("\n") else _DEFAULT_TEMPLATE + "\n"


class _SizeLimitExceeded(ValueError):
    pass


def _read_path_bytes_limited(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise _SizeLimitExceeded
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise _SizeLimitExceeded
    return data


def _read_stdin_bytes_limited(limit: int) -> bytes:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read(limit + 1)
    data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    if len(data) > limit:
        raise _SizeLimitExceeded
    return data


def _normalize_item(raw: Any) -> str:
    text = " ".join(str(raw or "").split())
    if not text:
        raise ValueError("文风条目不能为空")
    if len(text) > _ITEM_MAX_CHARS:
        raise ValueError(f"文风条目过长，最多 {_ITEM_MAX_CHARS} 字")
    normalized = normalize_author_text(text, max_chars=_ITEM_MAX_CHARS)
    if not normalized:
        raise ValueError("文风条目不能为空")
    if contains_jailbreak(normalized):
        raise ValueError("文风条目含有试图覆盖写作合同或系统提示的指令，已拒绝写入。")
    return normalized


def _author_span(lines: Sequence[str]) -> tuple[int, int, int] | None:
    for index, line in enumerate(lines):
        matched = AUTHOR_HEADING_RE.match(line.strip())
        if not matched:
            continue
        level = len(matched.group(1))
        end = len(lines)
        for cursor in range(index + 1, len(lines)):
            heading = NEXT_HEADING_RE.match(lines[cursor].strip())
            if heading and len(heading.group(1)) <= level:
                end = cursor
                break
        return index, end, level
    return None


def _existing_item_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ">", "<!--")):
        return None
    if PLACEHOLDER_RE.match(stripped):
        return None
    listed = LIST_ITEM_RE.match(stripped)
    text = listed.group(1) if listed else stripped
    key = " ".join(text.split())
    return key or None


def _read_style_prompt(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"拒绝写入符号链接：{STYLE_PROMPT_RELATIVE.as_posix()}")
    if not path.exists():
        return _default_template()
    if not path.is_file():
        raise ValueError(f"文风提示词不是普通文件：{STYLE_PROMPT_RELATIVE.as_posix()}")
    try:
        raw = _read_path_bytes_limited(path, _FILE_SIZE_LIMIT)
    except _SizeLimitExceeded as exc:
        raise ValueError(f"文风提示词超过上限：最多 {_FILE_SIZE_LIMIT} 字节") from exc
    except OSError as exc:
        raise ValueError("无法读取文风提示词") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文风提示词不是 UTF-8") from exc
    return text if text.endswith("\n") else text + "\n"


def _collect_existing_keys(lines: Sequence[str], start: int, end: int) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for line in lines[start + 1 : end]:
        key = _existing_item_key(line)
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _append_items(body_lines: List[str], items: Sequence[str]) -> List[str]:
    kept = [
        line
        for line in body_lines
        if not PLACEHOLDER_RE.match(line.strip())
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    if kept and kept[-1].strip():
        kept.append("")
    for item in items:
        kept.append(f"- {item}")
    kept.append("")
    return kept


def add_style_items(project_root: Path, items: Iterable[Any]) -> Dict[str, Any]:
    project_root = Path(project_root).expanduser().resolve()
    requested: list[str] = []
    seen_request: set[str] = set()
    for raw in items:
        item = _normalize_item(raw)
        if item in seen_request:
            continue
        seen_request.add(item)
        requested.append(item)
    if not requested:
        raise ValueError("文风条目不能为空")

    path = _style_prompt_path(project_root)
    original = _read_style_prompt(path)
    lines = original.splitlines()
    span = _author_span(lines)
    if span is None:
        lines = [*lines, "", "## 作者提示词", ""]
        span = _author_span(lines)
        if span is None:
            raise ValueError("无法定位文风提示词中的「作者提示词」标题")
    start, end, _level = span
    existing_keys = _collect_existing_keys(lines, start, end)
    existing_set = set(existing_keys)
    added = [item for item in requested if item not in existing_set]
    skipped = [item for item in requested if item in existing_set]
    if not added:
        return {
            "status": "skipped",
            "reason": "duplicate",
            "path": str(path),
            "added": [],
            "skipped_duplicates": skipped,
            "items": existing_keys,
        }

    if len(existing_keys) + len(added) > _ITEM_LIMIT:
        raise ValueError(f"文风条目超过上限：最多 {_ITEM_LIMIT} 条")

    body = _append_items(list(lines[start + 1 : end]), added)
    rewritten = [*lines[: start + 1], *body]
    if end < len(lines):
        remainder = list(lines[end:])
        if rewritten and rewritten[-1].strip() and remainder and remainder[0].strip():
            rewritten.append("")
        rewritten.extend(remainder)
    text = "\n".join(rewritten).rstrip() + "\n"
    resolve_inside_project(project_root, path, reject_leaf_symlink=True)
    _atomic_write_text(path, text)
    return {
        "status": "success",
        "path": str(path),
        "added": added,
        "skipped_duplicates": skipped,
        "items": existing_keys + added,
    }


def _extract_author_prompt(text: str) -> str:
    lines = str(text or "").splitlines()
    span = _author_span(lines)
    if span is None:
        return ""
    start, end, _level = span
    body = "\n".join(lines[start + 1 : end])
    body = re.sub(r"<!--.*?(?:-->|$)", "", body, flags=re.DOTALL)
    kept = [
        line
        for line in body.splitlines()
        if not PLACEHOLDER_RE.match(line.strip())
    ]
    return "\n".join(kept).strip()


def show_style_prompt(project_root: Path) -> Dict[str, Any]:
    """Return only the author prompt section when its path stays inside the project."""
    root = Path(project_root).expanduser().resolve()
    target = root / STYLE_PROMPT_RELATIVE
    try:
        resolve_inside_project(root, target, reject_leaf_symlink=True)
    except ValueError:
        return {"status": "missing", "reason": "unsafe_path", "text": ""}
    if target.is_symlink() or not target.is_file():
        return {"status": "missing", "reason": "not_found", "text": ""}
    try:
        raw = _read_path_bytes_limited(target, _FILE_SIZE_LIMIT)
        text = raw.decode("utf-8")
    except _SizeLimitExceeded:
        return {"status": "missing", "reason": "too_large", "text": ""}
    except (OSError, UnicodeDecodeError):
        return {"status": "missing", "reason": "unreadable", "text": ""}
    return {
        "status": "ok",
        "path": str(target),
        "text": _extract_author_prompt(text),
    }


def _parse_items_payload(raw: str) -> list[Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("文风输入必须是 JSON 对象 {\"items\": [...]} 或字符串数组") from exc
    if isinstance(payload, dict):
        items = payload.get("items")
        extra = set(payload) - {"items"}
        if extra:
            raise ValueError("文风输入 JSON 只能包含 items 字段")
        if not isinstance(items, list):
            raise ValueError("文风输入 items 必须是字符串数组")
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("文风输入必须是 JSON 对象 {\"items\": [...]} 或字符串数组")
    if not all(isinstance(item, str) for item in items):
        raise ValueError("文风条目必须是字符串")
    return items


def load_items_from_input_file(project_root: Path, input_file: str) -> list[Any]:
    if input_file == "-":
        try:
            data = _read_stdin_bytes_limited(_INPUT_SIZE_LIMIT)
        except _SizeLimitExceeded as exc:
            raise ValueError(f"文风输入超过上限：最多 {_INPUT_SIZE_LIMIT} 字节")
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("文风输入不是 UTF-8") from exc
        return _parse_items_payload(raw)

    root = Path(project_root).expanduser().resolve()
    source = Path(input_file)
    if not source.is_absolute():
        source = root / source
    resolve_inside_project(root, source, reject_leaf_symlink=True)
    if source.is_symlink() or not source.is_file():
        raise ValueError("文风输入文件不存在或不是普通文件")
    try:
        data = _read_path_bytes_limited(source, _INPUT_SIZE_LIMIT)
    except _SizeLimitExceeded as exc:
        raise ValueError(f"文风输入超过上限：最多 {_INPUT_SIZE_LIMIT} 字节")
    except OSError as exc:
        raise ValueError("无法读取文风输入文件") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文风输入不是 UTF-8") from exc
    return _parse_items_payload(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="写入本书长期文风提示词")
    parser.add_argument("--project-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-item", help="从 JSON 文件或标准输入追加文风偏好")
    add.add_argument(
        "--input-file",
        required=True,
        help="项目内 JSON 文件，或 - 表示 stdin；用户原文不得放进命令参数",
    )
    sub.add_parser("show", help="安全读取文风提示词；越出项目则视为缺失")

    args = parser.parse_args()
    try:
        if args.command == "add-item":
            items = load_items_from_input_file(Path(args.project_root), args.input_file)
            result = add_style_items(Path(args.project_root), items)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.command == "show":
            result = show_style_prompt(Path(args.project_root))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

    raise SystemExit(2)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()

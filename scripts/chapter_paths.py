#!/usr/bin/env python3
"""CanonLedger 当前正文文件路径助手。

正文统一位于 ``正文/第NNNN章.md`` 或 ``正文/第NNNN章-标题.md``。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_CHAPTER_NUM_RE = re.compile(r"第(?P<num>\d+)章")
_OUTLINE_HEADING_RE = re.compile(r"^#{1,6}\s*第\s*(?P<num>\d+)\s*章[：:]\s*(?P<title>.+?)\s*$", re.MULTILINE)
_SPLIT_OUTLINE_FILENAME_RE = re.compile(r"^第0*(?P<num>\d+)章[-—_ ]+(?P<title>.+?)\.md$")


def volume_num_for_chapter(chapter_num: int, *, chapters_per_volume: int = 50) -> int:
    if chapter_num <= 0:
        raise ValueError("chapter_num must be >= 1")
    return (chapter_num - 1) // chapters_per_volume + 1


def extract_chapter_num_from_filename(filename: str) -> Optional[int]:
    m = _CHAPTER_NUM_RE.search(filename)
    if not m:
        return None
    try:
        return int(m.group("num"))
    except ValueError:
        return None


def _safe_title_for_filename(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        return ""

    try:
        from security_utils import sanitize_filename
    except ImportError:  # pragma: no cover
        from scripts.security_utils import sanitize_filename

    safe_title = sanitize_filename(cleaned, max_length=60)
    return "" if safe_title == "unnamed_entity" else safe_title


def _extract_title_from_outline_text(outline_text: str, chapter_num: int) -> str:
    for match in _OUTLINE_HEADING_RE.finditer(outline_text):
        if int(match.group("num")) != chapter_num:
            continue
        return _safe_title_for_filename(match.group("title"))
    return ""


def _extract_title_from_split_outline_filename(outline_dir: Path, chapter_num: int) -> str:
    patterns = [
        f"第{chapter_num}章*.md",
        f"第{chapter_num:02d}章*.md",
        f"第{chapter_num:03d}章*.md",
        f"第{chapter_num:04d}章*.md",
    ]
    for pattern in patterns:
        for path in sorted(outline_dir.glob(pattern)):
            match = _SPLIT_OUTLINE_FILENAME_RE.match(path.name)
            if not match:
                continue
            if int(match.group("num")) != chapter_num:
                continue
            title = _safe_title_for_filename(match.group("title"))
            if title:
                return title
    return ""


def extract_chapter_title(project_root: Path, chapter_num: int) -> str:
    """从详细大纲提取章节标题，用于生成更直观的章节文件名。"""
    try:
        from chapter_outline_loader import load_chapter_outline
    except ImportError:  # pragma: no cover
        from scripts.chapter_outline_loader import load_chapter_outline

    outline_text = load_chapter_outline(project_root, chapter_num, max_chars=None)
    if not outline_text.startswith("⚠️"):
        title = _extract_title_from_outline_text(outline_text, chapter_num)
        if title:
            return title

    outline_dir = project_root / "大纲"
    if outline_dir.exists():
        return _extract_title_from_split_outline_filename(outline_dir, chapter_num)
    return ""


def _build_chapter_filename(project_root: Path, chapter_num: int) -> str:
    padded = f"{chapter_num:04d}"
    title = extract_chapter_title(project_root, chapter_num)
    if title:
        return f"第{padded}章-{title}.md"
    return f"第{padded}章.md"


def find_chapter_file(project_root: Path, chapter_num: int) -> Optional[Path]:
    """
    Find an existing chapter file for chapter_num under project_root/正文.
    Returns the first match (stable sorted order) or None if not found.
    """
    chapters_dir = project_root / "正文"
    if not chapters_dir.exists():
        return None

    for candidate in sorted(chapters_dir.glob(f"第{chapter_num:04d}章*.md")):
        if candidate.is_file() and extract_chapter_num_from_filename(candidate.name) == chapter_num:
            return candidate

    return None


def default_chapter_draft_path(project_root: Path, chapter_num: int) -> Path:
    """返回当前项目创建新章节时唯一支持的正文路径。"""
    return project_root / "正文" / _build_chapter_filename(project_root, chapter_num)

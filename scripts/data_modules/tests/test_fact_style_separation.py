#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_ROOTS = (
    PLUGIN_ROOT / "scripts",
    PLUGIN_ROOT / "agents",
    PLUGIN_ROOT / "skills",
    PLUGIN_ROOT / "commands",
    PLUGIN_ROOT / "references",
)
SKIP_DIR_NAMES = {
    "tests",
    "__pycache__",
    ".venv",
    "node_modules",
}
FORBIDDEN_MARKERS = (
    "project-memory",
    "project_memory",
    "author-consistency",
    "_SETTING_CRAFT_RE",
    "_SETTING_META_CONTROL_RE",
    "_setting_is_craft",
    "_STYLE_PRESCRIPTION_RE",
)


def _iter_production_files() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix.lower() not in {".py", ".md", ".json"}:
                continue
            files.append(path)
    return files


def test_no_legacy_fact_learning_or_craft_keyword_filter_paths():
    hits: list[str] = []
    for path in _iter_production_files():
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(PLUGIN_ROOT).as_posix()
        for marker in FORBIDDEN_MARKERS:
            if marker not in text:
                continue
            hits.append(f"{relative}: {marker}")
    assert hits == []


def test_python_ast_has_no_author_consistency_or_project_memory_identifiers():
    forbidden = {
        "project_memory",
        "add_pattern",
        "_is_author_consistency_rule",
        "_setting_is_craft",
        "_SETTING_CRAFT_RE",
        "_SETTING_META_CONTROL_RE",
        "_STYLE_PRESCRIPTION_RE",
        "_seed_non_projection_memory",
    }
    found: list[str] = []
    scripts_root = PLUGIN_ROOT / "scripts"
    for path in scripts_root.rglob("*.py"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {
            node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        names |= {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        names |= {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        overlap = sorted(names & forbidden)
        if overlap:
            relative = path.relative_to(PLUGIN_ROOT).as_posix()
            found.append(f"{relative}: {', '.join(overlap)}")
    assert found == []


SETTING_CRAFT_FIELDS = (
    "读者第一印象",
    "核心卖点",
    "战斗节奏特点",
    "读者可感知",
    "POV 分配",
    "防止抢戏",
)


def test_setting_templates_do_not_prompt_writing_craft_fields():
    root = PLUGIN_ROOT / "templates" / "output"
    hits: list[str] = []
    for path in sorted(root.glob("设定集-*.md")):
        if path.name == "设定集-文风提示词.md":
            continue
        text = path.read_text(encoding="utf-8")
        for marker in SETTING_CRAFT_FIELDS:
            if marker in text:
                hits.append(f"{path.name}: {marker}")
    assert hits == []

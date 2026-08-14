#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve CanonLedger plugin and workspace roots for Cursor."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

PLUGIN_MARKER = Path("scripts") / "canon_ledger.py"
PLUGIN_NAME = "canon-ledger"
LOCAL_INSTALL_REL = Path(".cursor") / "plugins" / "local" / PLUGIN_NAME

ENV_PLUGIN_ROOTS = (
    "CANON_LEDGER_PLUGIN_ROOT",
    "CURSOR_PLUGIN_ROOT",
)
ENV_WORKSPACE_ROOTS = (
    "CURSOR_PROJECT_DIR",
)


def _is_plugin_root(path: Path) -> bool:
    try:
        return (path.expanduser() / PLUGIN_MARKER).is_file()
    except OSError:
        return False


def _normalized(path: Path) -> Path:
    expanded = path.expanduser()
    try:
        return expanded.resolve()
    except OSError:
        return expanded


def _env_path(name: str) -> Optional[Path]:
    raw = os.environ.get(name)
    if not raw or not str(raw).strip():
        return None
    return Path(str(raw).strip())


def resolve_plugin_root(*, start: Optional[Path] = None) -> Path:
    """
    Locate the Cursor plugin directory that contains scripts/canon_ledger.py.

    Order:
    1) CANON_LEDGER_PLUGIN_ROOT / host plugin roots
    2) this file's plugin root (scripts/ parent)
    3) ~/.cursor/plugins/local/canon-ledger

    Cache directories are deliberately not searched. Cursor must inject the
    active plugin root; otherwise only this package or the fixed local install
    path is trusted.
    """
    for key in ENV_PLUGIN_ROOTS:
        candidate = _env_path(key)
        if candidate is not None and _is_plugin_root(candidate):
            return _normalized(candidate)

    here = Path(__file__).resolve().parent.parent
    if _is_plugin_root(here):
        return here

    if start is not None:
        start_path = _normalized(start)
        if _is_plugin_root(start_path):
            return start_path

    local = Path.home() / LOCAL_INSTALL_REL
    if _is_plugin_root(local):
        return _normalized(local)

    raise FileNotFoundError(
        "无法定位叙典 CanonLedger 插件根目录（缺少 scripts/canon_ledger.py）。"
        "请安装到 ~/.cursor/plugins/local/canon-ledger，或设置 "
        "CANON_LEDGER_PLUGIN_ROOT / CURSOR_PLUGIN_ROOT。"
    )


def resolve_workspace_root(*, cwd: Optional[Path] = None) -> Path:
    for key in ENV_WORKSPACE_ROOTS:
        candidate = _env_path(key)
        if candidate is not None:
            return _normalized(candidate)
    return _normalized(cwd or Path.cwd())


def emit_json_paths(*, plugin_root: Optional[Path] = None) -> int:
    try:
        root = plugin_root or resolve_plugin_root()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    workspace = resolve_workspace_root()
    print(
        json.dumps(
            {
                "schema_version": "canon-ledger-cursor-paths/v1",
                "plugin_root": str(root),
                "scripts_dir": str(root / "scripts"),
                "workspace_root": str(workspace),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(emit_json_paths())

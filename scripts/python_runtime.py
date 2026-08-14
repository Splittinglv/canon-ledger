#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve the Python interpreter that owns the plugin dependencies.

This module deliberately uses only the standard library so the system Python
can use it as a bootstrap even when Pydantic is installed only in a virtual
environment.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


REQUIRED_MODULES = ("pydantic", "filelock")
RUNTIME_ENV_KEYS = ("CANON_LEDGER_PYTHON",)
GLOBAL_RUNTIME_DIR_NAMES = ("canon-ledger",)


def _candidate_paths(plugin_root: Path) -> Iterable[Path]:
    for env_key in RUNTIME_ENV_KEYS:
        configured = str(os.environ.get(env_key) or "").strip()
        if configured:
            yield Path(configured).expanduser()

    if os.name == "nt":
        yield plugin_root / ".venv" / "Scripts" / "python.exe"
        for directory in GLOBAL_RUNTIME_DIR_NAMES:
            yield Path.home() / ".cursor" / directory / ".venv" / "Scripts" / "python.exe"
    else:
        yield plugin_root / ".venv" / "bin" / "python"
        for directory in GLOBAL_RUNTIME_DIR_NAMES:
            yield Path.home() / ".cursor" / directory / ".venv" / "bin" / "python"

    yield Path(sys.executable)
    for name in ("python3", "python"):
        resolved = shutil.which(name)
        if resolved:
            yield Path(resolved)


def _has_runtime_dependencies(candidate: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                str(candidate),
                "-X",
                "utf8",
                "-c",
                "import pydantic, filelock",
            ],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def resolve_python_executable(plugin_root: str | Path) -> Path:
    root = Path(plugin_root).expanduser().resolve()
    seen: set[str] = set()
    for raw_candidate in _candidate_paths(root):
        try:
            # Keep a virtual environment's launcher path intact. Resolving its
            # symlink to the base interpreter discards the venv site-packages.
            candidate = Path(os.path.abspath(raw_candidate.expanduser()))
        except OSError:
            continue
        marker = os.path.normcase(str(candidate))
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file() and os.access(candidate, os.X_OK) and _has_runtime_dependencies(candidate):
            return candidate
    modules = ", ".join(REQUIRED_MODULES)
    raise RuntimeError(
        "未找到包含插件依赖的 Python。请在插件目录或 "
        "~/.cursor/canon-ledger/.venv 创建虚拟环境并安装 requirements；"
        f"至少需要：{modules}。"
    )

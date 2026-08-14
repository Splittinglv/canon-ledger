#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve and validate the plugin environment as a fixed line protocol.

Skills invoke this trusted script once instead of copying the historical
multi-stage inline bootstrap. The output is data, never shell source: on
success exactly six lines are printed (the five environment values in
``ENVIRONMENT_KEYS`` order, then the dependency interpreter path) and the
caller assigns them individually with ``read``. Any validation failure
exits non-zero without printing a partial protocol.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from export_cursor_env import (  # noqa: E402
    ENVIRONMENT_KEYS,
    SCHEMA_VERSION,
    build_payload,
)


def _validated_lines() -> list[str]:
    payload = build_payload()
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("环境协议版本不受支持")
    environment = payload.get("environment")
    python_executable = payload.get("python_executable")
    if not isinstance(environment, dict) or set(environment) != set(ENVIRONMENT_KEYS):
        raise ValueError("环境协议字段不完整")
    values = [environment[key] for key in ENVIRONMENT_KEYS]
    if any(
        not isinstance(value, str)
        or not value
        or any(char in value for char in "\x00\r\n")
        for value in values
    ):
        raise ValueError("环境协议包含空值或控制字符")
    if (
        not isinstance(python_executable, str)
        or not python_executable
        or any(char in python_executable for char in "\x00\r\n")
        or not Path(python_executable).is_absolute()
        or not Path(python_executable).is_file()
    ):
        raise ValueError("依赖解释器路径无效")
    plugin_root, cursor_root, scripts_dir, workspace_root, project_dir = values
    if cursor_root != plugin_root:
        raise ValueError("插件根字段不一致")
    if scripts_dir != str(Path(plugin_root) / "scripts"):
        raise ValueError("脚本目录与插件根不一致")
    if project_dir != workspace_root:
        raise ValueError("工作区字段不一致")
    return [*values, python_executable]


def main() -> int:
    try:
        lines = _validated_lines()
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

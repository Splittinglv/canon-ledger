#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print shell exports for plugin/workspace paths. Safe to eval from skills."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cursor_paths import emit_shell_exports, resolve_workspace_root  # noqa: E402


def main() -> int:
    plugin_root = Path(__file__).resolve().parent.parent
    marker = plugin_root / "scripts" / "webnovel.py"
    if not marker.is_file():
        from cursor_paths import resolve_plugin_root

        return emit_shell_exports()
    workspace = resolve_workspace_root()
    print(f'export WEBNOVEL_PLUGIN_ROOT="{plugin_root}"')
    print(f'export CURSOR_PLUGIN_ROOT="{plugin_root}"')
    print(f'export CLAUDE_PLUGIN_ROOT="{plugin_root}"')
    print(f'export SCRIPTS_DIR="{plugin_root / "scripts"}"')
    print(f'export WORKSPACE_ROOT="{workspace}"')
    # Keep Claude-compatible workspace hint for project_locator.
    if not os.environ.get("CURSOR_PROJECT_DIR") and not os.environ.get("CLAUDE_PROJECT_DIR"):
        print(f'export CURSOR_PROJECT_DIR="{workspace}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

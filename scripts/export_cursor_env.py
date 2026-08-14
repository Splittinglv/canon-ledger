#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Emit plugin/workspace paths as a fixed JSON data contract.

The output is data, never shell source.  Consumers must parse the documented
fields and assign them individually instead of executing stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cursor_paths import resolve_workspace_root  # noqa: E402
from python_runtime import resolve_python_executable  # noqa: E402


SCHEMA_VERSION = "webnovel-cursor-env/v1"
ENVIRONMENT_KEYS = (
    "WEBNOVEL_PLUGIN_ROOT",
    "CURSOR_PLUGIN_ROOT",
    "CLAUDE_PLUGIN_ROOT",
    "SCRIPTS_DIR",
    "WORKSPACE_ROOT",
    "CURSOR_PROJECT_DIR",
)


def _trusted_plugin_root() -> Path:
    """Bind the response to the package containing this exporter.

    Environment variables and cache searches are deliberately not consulted:
    the caller is responsible for invoking the exporter from the plugin it
    already trusts.
    """
    root = Path(__file__).resolve().parent.parent
    marker = root / "scripts" / "webnovel.py"
    manifest = root / ".cursor-plugin" / "plugin.json"
    if not marker.is_file() or not manifest.is_file():
        raise FileNotFoundError(
            "export_cursor_env.py is not inside a complete webnovel-writer plugin"
        )
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid webnovel-writer plugin manifest") from exc
    if not isinstance(payload, dict) or payload.get("name") != "webnovel-writer":
        raise ValueError("unexpected plugin manifest identity")
    return root


def build_payload() -> dict[str, object]:
    plugin_root = _trusted_plugin_root()
    workspace = resolve_workspace_root()
    python_executable = resolve_python_executable(plugin_root)
    environment = {
        "WEBNOVEL_PLUGIN_ROOT": str(plugin_root),
        "CURSOR_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "SCRIPTS_DIR": str(plugin_root / "scripts"),
        "WORKSPACE_ROOT": str(workspace),
        "CURSOR_PROJECT_DIR": str(workspace),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": environment,
        "python_executable": str(python_executable),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Cursor plugin paths as JSON")
    parser.add_argument("--format", choices=("json",), default="json")
    parser.parse_args()
    try:
        payload = build_payload()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_LINES = 8
MAX_CHARS = 1000
DISABLE_ENV = "WEBNOVEL_DISABLE_SESSION_STATUS_HOOK"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clip(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()][:MAX_LINES]
    clipped = "\n".join(lines).strip()
    if len(clipped) > MAX_CHARS:
        clipped = clipped[: MAX_CHARS - 3].rstrip() + "..."
    return clipped


def _plugin_root() -> Path:
    for key in ("CURSOR_PLUGIN_ROOT", "WEBNOVEL_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        raw = os.environ.get(key)
        if raw:
            candidate = Path(raw).expanduser()
            if (candidate / "scripts" / "webnovel.py").is_file():
                return candidate.resolve()
    return Path(__file__).resolve().parents[1]


def _workspace_root() -> str:
    return os.environ.get("CURSOR_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def main() -> int:
    if _truthy(os.environ.get(DISABLE_ENV)):
        return 0

    plugin_root = _plugin_root()
    workspace_root = _workspace_root()
    scripts_dir = plugin_root / "scripts"
    webnovel = scripts_dir / "webnovel.py"

    extra_lines = [
        f"WEBNOVEL_PLUGIN_ROOT={plugin_root}",
        f"WEBNOVEL_SCRIPTS_DIR={scripts_dir}",
        f"WORKSPACE_ROOT={workspace_root}",
        "Run any /webnovel-* skill only after exporting these paths (or eval scripts/export_cursor_env.py).",
    ]

    status = ""
    if webnovel.is_file():
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(webnovel),
                    "--project-root",
                    str(workspace_root),
                    "project-status",
                    "--format",
                    "summary",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=4,
            )
            status = _clip(proc.stdout or proc.stderr or "")
        except Exception:
            status = ""

    additional = "\n".join(extra_lines)
    if status:
        additional = additional + "\n" + status

    print(json.dumps({"additional_context": additional}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap Cursor hooks with the dependency-owning Python interpreter."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _emit_bootstrap_failure(hook_name: str, message: str) -> int:
    if hook_name == "session_start":
        print(
            json.dumps(
                {
                    "additional_context": json.dumps(
                        {
                            "schema_version": "canon-ledger-session-runtime/v1",
                            "status": "unavailable",
                            "reason": "python_runtime_unavailable",
                        },
                        ensure_ascii=False,
                    )
                },
                ensure_ascii=False,
            )
        )
        print(message, file=sys.stderr)
        return 0

    deny = {
        "permission": "deny",
        "user_message": message,
        "agent_message": message,
        "hookSpecificOutput": {"permissionDecision": "deny"},
        "systemMessage": message,
    }
    print(json.dumps(deny, ensure_ascii=False))
    return 2


def main() -> int:
    hook_name = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    hook_files = {
        "session_start": "session_start.py",
        "guard_runtime_write": "guard_runtime_write.py",
    }
    script_name = hook_files.get(hook_name)
    if not script_name:
        return _emit_bootstrap_failure(hook_name, "未知的 CanonLedger hook。")

    plugin_root = Path(__file__).resolve().parents[1]
    scripts_dir = plugin_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from python_runtime import resolve_python_executable

        interpreter = resolve_python_executable(plugin_root)
    except Exception as exc:
        return _emit_bootstrap_failure(
            hook_name,
            f"CanonLedger 无法启动：{exc}",
        )

    target = Path(__file__).resolve().parent / script_name
    os.execv(
        str(interpreter),
        [str(interpreter), "-X", "utf8", str(target), *sys.argv[2:]],
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

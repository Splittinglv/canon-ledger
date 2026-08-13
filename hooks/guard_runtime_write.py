#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# state.json is intentionally NOT protected: audits routinely require bulk
# fixes that update_state.py flags cannot express, and state.json has its own
# backup + rebuild path (issue #113).
PROTECTED_SUFFIXES = (
    ".webnovel/index.db",
    ".webnovel/vectors.db",
    ".webnovel/memory_scratchpad.json",
    ".webnovel/projection_log.jsonl",
)
PROTECTED_BASENAMES = (
    "index.db",
    "vectors.db",
    "memory_scratchpad.json",
    "projection_log.jsonl",
)
SHELL_CONTROL_RE = re.compile(r"(?:&&|\|\||[;&|<>`\r\n]|\$\()")
COMMIT_BASENAME_RE = re.compile(r"(?:^|[/\\])chapter_?\d+\.commit\.json(?:$|[\s'\"])", re.I)
DANGEROUS_COMMAND_RE = re.compile(
    r"(?im)(?:^|[;&|]\s*)(?:rm|cp|mv|tee|perl|sed|bash|sh|zsh|fish|powershell|pwsh|cmd)\b"
)
TRUSTED_WEBNOVEL_ENV_TOKENS = {
    "${SCRIPTS_DIR}/webnovel.py": ("SCRIPTS_DIR", ""),
    "$SCRIPTS_DIR/webnovel.py": ("SCRIPTS_DIR", ""),
    "${WEBNOVEL_PLUGIN_ROOT}/scripts/webnovel.py": ("WEBNOVEL_PLUGIN_ROOT", "scripts"),
    "$WEBNOVEL_PLUGIN_ROOT/scripts/webnovel.py": ("WEBNOVEL_PLUGIN_ROOT", "scripts"),
    "${CURSOR_PLUGIN_ROOT}/scripts/webnovel.py": ("CURSOR_PLUGIN_ROOT", "scripts"),
    "$CURSOR_PLUGIN_ROOT/scripts/webnovel.py": ("CURSOR_PLUGIN_ROOT", "scripts"),
    "${CLAUDE_PLUGIN_ROOT}/scripts/webnovel.py": ("CLAUDE_PLUGIN_ROOT", "scripts"),
    "$CLAUDE_PLUGIN_ROOT/scripts/webnovel.py": ("CLAUDE_PLUGIN_ROOT", "scripts"),
}


def _load_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty hook input")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid hook JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("hook input must be an object")
    return payload


def _normalized_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("\\", "/")
    try:
        if ":" in raw[:3]:
            raw = PureWindowsPath(str(value)).as_posix()
        else:
            raw = PurePosixPath(raw).as_posix()
    except Exception:
        pass
    return posixpath.normpath(raw).lower()


def _deny(message: str) -> int:
    payload = {
        "permission": "deny",
        "user_message": message,
        "agent_message": message,
        "hookSpecificOutput": {"permissionDecision": "deny"},
        "systemMessage": message,
    }
    print(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({"hookSpecificOutput": {"permissionDecision": "deny"}, "systemMessage": message}, ensure_ascii=False), file=sys.stderr)
    return 2


def _allow() -> int:
    print(json.dumps({"permission": "allow"}))
    return 0


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    return value if isinstance(value, dict) else {}


def _tool_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("tool_name")
        or payload.get("toolName")
        or payload.get("tool")
        or payload.get("matcher")
        or ""
    ).strip()


def _file_paths_from_payload(payload: dict[str, Any], tool_input: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for source in (tool_input, payload):
        for key in (
            "file_path",
            "path",
            "filename",
            "filePath",
            "target_path",
            "targetPath",
            "file_paths",
            "paths",
        ):
            value = source.get(key)
            if isinstance(value, (list, tuple)):
                paths.extend(str(item) for item in value if item)
            elif value:
                paths.append(str(value))
    return paths


def _command_from_payload(payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    for source in (tool_input, payload):
        value = source.get("command")
        if value:
            return str(value)
    return ""


def _is_protected_path(path: str) -> bool:
    normalized = _normalized_path(path)
    if not normalized:
        return False
    components = {part for part in normalized.split("/") if part not in {"", ".", ".."}}
    if ".story-system" in components:
        return True
    return any(suffix in normalized for suffix in PROTECTED_SUFFIXES)


def _command_is_runtime_safe(command: str) -> bool:
    if not command.strip() or SHELL_CONTROL_RE.search(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    webnovel_indexes = [
        index
        for index, token in enumerate(tokens)
        if token.replace("\\", "/").rsplit("/", 1)[-1].lower() == "webnovel.py"
    ]
    if len(webnovel_indexes) != 1:
        return False
    webnovel_index = webnovel_indexes[0]
    webnovel_token = tokens[webnovel_index].replace("\\", "/")
    trusted_absolute = (Path(__file__).resolve().parents[1] / "scripts" / "webnovel.py").as_posix()
    if webnovel_token != trusted_absolute:
        env_spec = TRUSTED_WEBNOVEL_ENV_TOKENS.get(webnovel_token)
        if env_spec is None:
            return False
        env_name, suffix = env_spec
        raw_root = os.environ.get(env_name)
        if not raw_root:
            return False
        candidate = Path(raw_root).expanduser()
        if suffix:
            candidate /= suffix
        try:
            candidate_webnovel = (candidate / "webnovel.py").resolve(strict=False).as_posix()
        except OSError:
            return False
        if candidate_webnovel != trusted_absolute:
            return False
    prefix = tokens[:webnovel_index]
    if not prefix:
        return False
    interpreter = prefix[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if interpreter not in {"python", "python3", "python.exe", "python3.exe", "py", "py.exe"}:
        return False
    interpreter_args = prefix[1:]
    allowed_interpreter_args = (
        [],
        ["-X", "utf8"],
        ["-u"],
        ["-u", "-X", "utf8"],
        ["-X", "utf8", "-u"],
    )
    if interpreter in {"py", "py.exe"} and interpreter_args[:1] in (["-3"], ["-3.10"], ["-3.11"], ["-3.12"], ["-3.13"]):
        interpreter_args = interpreter_args[1:]
    if interpreter_args not in allowed_interpreter_args:
        return False
    arguments = tokens[webnovel_index + 1 :]
    command_arguments: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == "--project-root":
            if index + 1 >= len(arguments):
                return False
            index += 2
            continue
        if token.startswith("--project-root="):
            index += 1
            continue
        command_arguments.append(token.lower())
        index += 1
    if not command_arguments:
        return False
    if command_arguments[0] == "chapter-commit":
        return True
    return command_arguments[:2] in (["projections", "retry"], ["projections", "replay"])


def _command_mentions_protected_runtime(command: str) -> bool:
    lowered = command.lower().replace("\\", "/")
    non_comment_lines = "\n".join(
        line for line in lowered.splitlines() if not line.lstrip().startswith("#")
    )
    if (
        SHELL_CONTROL_RE.search(non_comment_lines)
        or any(character in non_comment_lines for character in "*?[")
    ) and DANGEROUS_COMMAND_RE.search(non_comment_lines):
        return True
    if ".story" in lowered:
        return True
    if ".webnovel" in lowered and any(character in lowered for character in "*?["):
        return True
    if any(suffix in lowered for suffix in PROTECTED_SUFFIXES) or any(
        basename in lowered for basename in PROTECTED_BASENAMES
    ):
        return True
    if COMMIT_BASENAME_RE.search(lowered):
        return True
    return bool(re.search(r"\bcommits(?:/|\\|\s|$)", lowered))


def _looks_like_runtime_bypass(command: str) -> bool:
    lowered = command.lower().replace("\\", "/")
    if _command_is_runtime_safe(command):
        return False
    if "chapter_commit.py" in lowered:
        return True
    if "webnovel.py" in lowered and any(
        marker in lowered
        for marker in ("chapter-commit", "projections retry", "projections replay")
    ):
        return True
    return _command_mentions_protected_runtime(command)


def main() -> int:
    try:
        payload = _load_input()
    except ValueError as exc:
        return _deny(f"webnovel-writer runtime guard rejected invalid hook input: {exc}.")
    tool_input = _tool_input(payload)
    tool = _tool_name(payload)
    command = _command_from_payload(payload, tool_input)

    if tool.lower() in {"bash", "shell"} or command:
        if not command:
            return _deny("webnovel-writer runtime guard received a shell request without a command.")
        if _looks_like_runtime_bypass(command):
            return _deny(
                "webnovel-writer blocked a direct write or bypass command for Story System/read-model files. Use webnovel.py write-gate, chapter-commit, or projections retry/replay instead."
            )
        return _allow()

    paths = _file_paths_from_payload(payload, tool_input)
    if any(_is_protected_path(path) for path in paths):
        return _deny(
            "webnovel-writer blocked a direct edit to Story System/read-model files. Use runtime commands so commit/projection invariants stay consistent."
        )
    if not tool or not paths:
        return _deny("webnovel-writer runtime guard rejected an incomplete tool request.")
    return _allow()


if __name__ == "__main__":
    raise SystemExit(main())

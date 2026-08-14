#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve optional per-subagent Cursor Task model slugs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

KNOWN_AGENTS = (
    "context-agent",
    "reviewer",
    "data-agent",
    "deconstruction-agent",
)

INHERIT_ALIASES = {"", "inherit", "default", "parent"}
CONFIG_FILENAME = "subagent-models.json"
GLOBAL_CONFIG_DIR_NAMES = ("canon-ledger",)
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+\[\]=,*-]*$")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def default_user_config_path() -> Path:
    cursor_home = Path(os.environ.get("CURSOR_HOME") or (Path.home() / ".cursor"))
    candidates = tuple(cursor_home / directory / CONFIG_FILENAME for directory in GLOBAL_CONFIG_DIR_NAMES)
    return next((path for path in candidates if path.is_file()), candidates[0])


def project_config_path(project_root: Path) -> Path:
    return Path(project_root) / ".canon-ledger" / CONFIG_FILENAME


def default_config_payload() -> dict[str, Any]:
    return {
        "_comment": (
            "可选。留 inherit 则子代理跟当前聊天用同一个模型。"
            "要单独指定时填 Cursor Task 允许的模型 id（不要用展示名或中文）。"
            "优先级：本轮对话点名 > 本书此文件 > ~/.cursor/canon-ledger/subagent-models.json > inherit。"
        ),
        "default": "inherit",
        "agents": {name: "inherit" for name in KNOWN_AGENTS},
    }


def normalize_model_value(raw: Any) -> tuple[str, Optional[str]]:
    """Return (model, warning). Invalid values fall back to inherit."""
    if raw is None:
        return "inherit", None
    text = str(raw).strip()
    if text.lower() in INHERIT_ALIASES:
        return "inherit", None
    if any(ch.isspace() for ch in text) or _CJK_RE.search(text):
        return "inherit", f"不是 Cursor Task 模型 id，已回退 inherit: {text}"
    if not _SLUG_RE.fullmatch(text):
        return "inherit", f"模型 id 含非法字符，已回退 inherit: {text}"
    return text, None


def _load_config_file(path: Path) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    if not path.is_file():
        return {}, warnings
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"无法读取 {path}: {exc}")
        return {}, warnings
    if not isinstance(payload, dict):
        warnings.append(f"{path} 必须是 JSON 对象")
        return {}, warnings
    return payload, warnings


def _extract_layer(payload: Mapping[str, Any]) -> tuple[str, dict[str, str], list[str]]:
    warnings: list[str] = []
    default_raw = payload.get("default", "inherit")
    default, default_warning = normalize_model_value(default_raw)
    if default_warning:
        warnings.append(f"default: {default_warning}")

    agents_raw = payload.get("agents") or {}
    agents: dict[str, str] = {}
    if agents_raw and not isinstance(agents_raw, Mapping):
        warnings.append("agents 必须是对象")
        return default, agents, warnings

    for name, value in dict(agents_raw).items():
        key = str(name).strip()
        if key.startswith("_"):
            continue
        model, warning = normalize_model_value(value)
        if warning:
            warnings.append(f"{key}: {warning}")
        if key:
            agents[key] = model
    return default, agents, warnings


def resolve_subagent_models(
    project_root: Optional[Path] = None,
    *,
    user_config_path: Optional[Path] = None,
    agent: Optional[str] = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    user_path = user_config_path if user_config_path is not None else default_user_config_path()
    user_payload, user_warnings = _load_config_file(user_path)
    warnings.extend(user_warnings)
    user_default, user_agents, user_layer_warnings = _extract_layer(user_payload)
    warnings.extend(user_layer_warnings)

    project_path: Optional[Path] = None
    project_payload: dict[str, Any] = {}
    project_default = "inherit"
    project_agents: dict[str, str] = {}
    if project_root is not None:
        project_path = project_config_path(project_root)
        project_payload, project_warnings = _load_config_file(project_path)
        warnings.extend(project_warnings)
        project_default, project_agents, project_layer_warnings = _extract_layer(project_payload)
        warnings.extend(project_layer_warnings)

    names: Iterable[str]
    if agent:
        names = (agent,)
    else:
        extra = [name for name in sorted(set(user_agents) | set(project_agents)) if name not in KNOWN_AGENTS]
        names = (*KNOWN_AGENTS, *extra)

    resolved: dict[str, dict[str, Any]] = {}
    for name in names:
        if name in project_agents:
            model = project_agents[name]
            source = "project"
        elif project_payload and project_default != "inherit":
            model = project_default
            source = "project-default"
        elif name in user_agents:
            model = user_agents[name]
            source = "user"
        elif user_payload and user_default != "inherit":
            model = user_default
            source = "user-default"
        else:
            model = "inherit"
            source = "inherit"
        resolved[name] = {
            "model": model,
            "pass_to_task": model != "inherit",
            "source": source,
        }

    return {
        "default": project_default if project_payload else user_default,
        "agents": resolved,
        "project_config": str(project_path) if project_path else "",
        "user_config": str(user_path),
        "warnings": warnings,
    }


def format_subagent_models(report: Mapping[str, Any], fmt: str = "json") -> str:
    if fmt == "text":
        lines = []
        for name, item in (report.get("agents") or {}).items():
            model = item.get("model", "inherit")
            source = item.get("source", "inherit")
            flag = "pass" if item.get("pass_to_task") else "inherit"
            lines.append(f"{name}: {model} ({flag}, {source})")
        for warning in report.get("warnings") or []:
            lines.append(f"warning: {warning}")
        return "\n".join(lines) + ("\n" if lines else "")
    return json.dumps(report, ensure_ascii=False, indent=2)

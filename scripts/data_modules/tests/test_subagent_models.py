#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from pathlib import Path

from data_modules.subagent_models import (
    format_subagent_models,
    normalize_model_value,
    resolve_subagent_models,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_normalize_inherit_and_invalid_values():
    assert normalize_model_value("") == ("inherit", None)
    assert normalize_model_value("inherit") == ("inherit", None)
    assert normalize_model_value("kimi-k3-max") == ("kimi-k3-max", None)
    model, warning = normalize_model_value("Grok 4.6")
    assert model == "inherit"
    assert warning
    model, warning = normalize_model_value("快速模型")
    assert model == "inherit"
    assert warning


def test_missing_configs_inherit(tmp_path):
    report = resolve_subagent_models(
        tmp_path / "no-book",
        user_config_path=tmp_path / "missing-user.json",
    )
    assert report["agents"]["data-agent"] == {
        "model": "inherit",
        "pass_to_task": False,
        "source": "inherit",
    }
    assert report["agents"]["reviewer"]["pass_to_task"] is False
    assert report["warnings"] == []


def test_project_overrides_user_and_default(tmp_path):
    user = tmp_path / "user.json"
    _write_json(
        user,
        {
            "default": "composer-2.5-fast",
            "agents": {"reviewer": "cursor-grok-4.6-xhigh-fast"},
        },
    )
    project_root = tmp_path / "book"
    _write_json(
        project_root / ".webnovel" / "subagent-models.json",
        {
            "default": "inherit",
            "agents": {"data-agent": "kimi-k3-max"},
        },
    )

    report = resolve_subagent_models(project_root, user_config_path=user)
    assert report["agents"]["data-agent"]["model"] == "kimi-k3-max"
    assert report["agents"]["data-agent"]["pass_to_task"] is True
    assert report["agents"]["data-agent"]["source"] == "project"
    assert report["agents"]["reviewer"]["model"] == "cursor-grok-4.6-xhigh-fast"
    assert report["agents"]["reviewer"]["source"] == "user"
    assert report["agents"]["context-agent"]["model"] == "composer-2.5-fast"
    assert report["agents"]["context-agent"]["source"] == "user-default"


def test_project_default_applies_when_agent_omitted(tmp_path):
    project_root = tmp_path / "book"
    _write_json(
        project_root / ".webnovel" / "subagent-models.json",
        {"default": "kimi-k3-max", "agents": {}},
    )
    report = resolve_subagent_models(
        project_root,
        user_config_path=tmp_path / "missing.json",
        agent="data-agent",
    )
    assert list(report["agents"]) == ["data-agent"]
    assert report["agents"]["data-agent"]["source"] == "project-default"
    assert report["agents"]["data-agent"]["pass_to_task"] is True


def test_corrupt_project_file_warns_and_inherits(tmp_path):
    project_root = tmp_path / "book"
    config = project_root / ".webnovel" / "subagent-models.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not json", encoding="utf-8")
    report = resolve_subagent_models(
        project_root,
        user_config_path=tmp_path / "missing.json",
    )
    assert report["agents"]["data-agent"]["model"] == "inherit"
    assert report["warnings"]


def test_text_format_includes_pass_flag(tmp_path):
    project_root = tmp_path / "book"
    _write_json(
        project_root / ".webnovel" / "subagent-models.json",
        {"agents": {"data-agent": "kimi-k3-max"}},
    )
    report = resolve_subagent_models(
        project_root,
        user_config_path=tmp_path / "missing.json",
        agent="data-agent",
    )
    text = format_subagent_models(report, "text")
    assert "data-agent: kimi-k3-max (pass, project)" in text

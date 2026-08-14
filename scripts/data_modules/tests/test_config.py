#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Config tests
"""

import os

from data_modules import config as config_module
from data_modules.config import DataModulesConfig, get_config, set_project_root


def test_config_paths_and_defaults(tmp_path):
    cfg = DataModulesConfig.from_project_root(tmp_path)
    assert cfg.project_root == tmp_path
    assert cfg.canon_ledger_dir.name == ".canon-ledger"
    assert cfg.state_file.name == "state.json"
    assert cfg.scratchpad_file.name == "memory_scratchpad.json"
    assert cfg.index_db.name == "index.db"
    assert cfg.rag_db.name == "rag.db"
    assert cfg.vector_db.name == "vectors.db"

    cfg.ensure_dirs()
    assert cfg.canon_ledger_dir.exists()


def test_get_config_and_set_project_root(tmp_path):
    set_project_root(tmp_path)
    cfg = get_config()
    assert cfg.project_root == tmp_path


def test_load_dotenv(monkeypatch, tmp_path):
    # prepare .env
    env_path = tmp_path / ".env"
    env_path.write_text("EMBED_BASE_URL=https://example.com\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)

    # call loader explicitly
    config_module._load_dotenv()
    assert os.environ.get("EMBED_BASE_URL") == "https://example.com"


def test_project_dotenv_is_scoped_to_its_config(monkeypatch, tmp_path):
    """项目甲的接口配置不应泄漏到随后打开的项目乙。"""
    for name in (
        "EMBED_BASE_URL",
        "EMBED_MODEL",
        "EMBED_API_KEY",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "RERANK_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    project_a = tmp_path / "项目甲"
    project_b = tmp_path / "项目乙"
    project_a.mkdir()
    project_b.mkdir()
    (project_a / ".env").write_text(
        "\n".join(
            [
                "EMBED_BASE_URL=https://embed.invalid/v1",
                "EMBED_API_KEY=项目甲嵌入密钥",
                "RERANK_BASE_URL=https://rerank.invalid/v1",
                "RERANK_API_KEY=项目甲重排密钥",
            ]
        ),
        encoding="utf-8",
    )

    config_a = DataModulesConfig.from_project_root(project_a)
    config_b = DataModulesConfig.from_project_root(project_b)

    assert config_a.embed_base_url == "https://embed.invalid/v1"
    assert config_a.embed_api_key == "项目甲嵌入密钥"
    assert config_a.rerank_base_url == "https://rerank.invalid/v1"
    assert config_a.rerank_api_key == "项目甲重排密钥"
    assert config_b.embed_api_key == ""
    assert config_b.rerank_api_key == ""
    assert os.environ.get("EMBED_API_KEY") is None
    assert os.environ.get("RERANK_API_KEY") is None

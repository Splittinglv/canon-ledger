#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import sqlite3
import json
from pathlib import Path

from .test_project_phase import _make_contracts, _make_init_ready
from .test_project_phase import _write_json


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_on_path()

import data_modules.doctor as doctor_module  # noqa: E402
from data_modules.projection_log import append_projection_run  # noqa: E402


def test_doctor_init_ready_does_not_require_story_contracts(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is True
    assert report["phase"] == "init_ready"
    assert not [item for item in report["checks"] if str(item["id"]).startswith("file.contract.")]


def test_doctor_missing_init_file_blocks_with_repair(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    (tmp_path / "大纲" / "总纲.md").unlink()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is False
    matches = [item for item in report["checks"] if item["id"] == "file.required.大纲/总纲.md"]
    assert matches
    assert matches[0]["status"] == "error"
    assert matches[0]["repair"]


def test_doctor_checks_contracts_after_story_system_starts(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    _make_contracts(tmp_path, chapter=1)
    (tmp_path / ".story-system" / "reviews" / "chapter_001.review.json").unlink()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is False
    contract_checks = [item for item in report["checks"] if item["id"] == "file.contract.review"]
    assert contract_checks
    assert contract_checks[0]["status"] == "error"


def test_doctor_no_project_reports_repair(monkeypatch):
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(None)

    assert report["ok"] is False
    assert report["phase"] == "no_project"
    assert report["recommended_actions"]


def test_doctor_treats_missing_rag_keys_as_optional_bm25_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    monkeypatch.delenv("RERANK_API_KEY", raising=False)

    checks = doctor_module._rag_checks(tmp_path)

    by_id = {item["id"]: item for item in checks}
    assert by_id["rag.embed.api_key"]["status"] == "ok"
    assert by_id["rag.embed.api_key"]["severity"] == "info"
    assert "BM25" in by_id["rag.embed.api_key"]["impact"]
    assert by_id["rag.rerank.api_key"]["status"] == "ok"
    assert by_id["rag.rerank.api_key"]["severity"] == "info"


def test_doctor_blocks_unbound_retrieval_rows(tmp_path):
    vector_db = tmp_path / ".canon-ledger" / "vectors.db"
    vector_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(vector_db) as conn:
        conn.execute(
            """
            CREATE TABLE vectors (
                chunk_id TEXT PRIMARY KEY,
                source_file TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO vectors(chunk_id, source_file) VALUES (?, ?)",
            ("legacy", "commit:chapter_001"),
        )

    checks = doctor_module._rag_checks(tmp_path)

    provenance = next(item for item in checks if item["id"] == "rag.retrieval_provenance")
    assert provenance["status"] == "error"
    assert "projections replay" in provenance["repair"]


def test_doctor_blocks_retrieval_schema_without_provenance_column(tmp_path):
    vector_db = tmp_path / ".canon-ledger" / "vectors.db"
    vector_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(vector_db) as conn:
        conn.execute(
            "CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, chapter INTEGER, content TEXT)"
        )

    checks = doctor_module._rag_checks(tmp_path)

    provenance = next(item for item in checks if item["id"] == "rag.retrieval_provenance")
    assert provenance["status"] == "error"
    assert "unsupported_schema_missing_source_file" in provenance["actual"]
    assert "projections replay" in provenance["repair"]


def test_doctor_blocks_commit_without_projection_log(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    _write_json(
        tmp_path / ".story-system" / "commits" / "chapter_001.commit.json",
        {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {"state": "done"},
        },
    )
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is False
    matches = [item for item in report["checks"] if item["id"] == "projection_log.present"]
    assert matches
    assert matches[0]["status"] == "error"


def test_doctor_blocks_pending_projection_log_run(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    commit_payload = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {"state": "pending"},
    }
    commit_path = tmp_path / ".story-system" / "commits" / "chapter_001.commit.json"
    _write_json(commit_path, commit_payload)
    append_projection_run(
        tmp_path,
        commit_payload,
        {"state": {"status": "pending"}},
        commit_path=commit_path,
    )
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = [item for item in report["checks"] if item["id"] == "projection_log.latest_run"]
    assert matches
    assert matches[0]["status"] == "error"
    assert report["ok"] is False


def test_doctor_warns_when_later_chapters_need_revalidation(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    (commits / "chapter_002.commit.json").write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "chapter": 2,
                    "status": "accepted",
                    "validation_status": "needs_revalidation",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    matches = [item for item in report["checks"] if item["id"] == "commits.revalidation"]
    assert matches
    assert matches[0]["status"] == "warning"
    assert "2" in matches[0]["actual"]

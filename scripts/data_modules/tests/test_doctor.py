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
from data_modules.canon_v3.migration import migrate_legacy  # noqa: E402
from data_modules.canon_v3.projection import projection_path  # noqa: E402
from data_modules.canon_v3.service import CanonV3Service  # noqa: E402
from data_modules.projection_log import append_projection_run  # noqa: E402
from data_modules.workflow_authority import WorkflowAuthority  # noqa: E402
from .test_canon_v3_author_axiom import _draft_record, _proposal  # noqa: E402
from .test_canon_v3_migration import _persist_accepted_commit  # noqa: E402
from .test_canon_v3_service import _batch, _power, _project  # noqa: E402


def test_doctor_init_ready_does_not_require_story_contracts(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    CanonV3Service(tmp_path).initialize_new_project()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is True
    assert report["phase"] == "init_ready"
    assert not [item for item in report["checks"] if str(item["id"]).startswith("file.contract.")]


def test_doctor_reports_exact_ready_workflow_and_head_bound_projection(
    tmp_path, monkeypatch
):
    _make_init_ready(tmp_path)
    CanonV3Service(tmp_path).initialize_new_project()
    authority = WorkflowAuthority(tmp_path).snapshot()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    checks = {item["id"]: item for item in report["checks"]}
    assert report["ok"] is True
    assert report["workflow_snapshot"] == authority
    assert report["primary_action"] == authority["primary_action"]
    assert report["recommended_actions"] == [authority["primary_action"]["command"]]
    assert checks["canon_v3.workflow_snapshot"]["status"] == "ok"
    assert checks["canon_v3.current_manifest"]["status"] == "ok"
    assert checks["canon_v3.staging"]["status"] == "ok"
    assert checks["canon_v3.author_axioms"]["status"] == "ok"
    assert checks["canon_v3.projection"]["status"] == "ok"
    assert report["canon_v3"]["current"]["reachable_objects_validated"] is True
    assert report["canon_v3"]["projection"]["head_hash"] == authority["head_hash"]


def test_doctor_validates_exact_chapter_staging(tmp_path, monkeypatch):
    root, manuscript, binding = _project(
        tmp_path, "林舟的境界从炼气突破到了筑基。\n"
    )
    _make_init_ready(root)
    service = CanonV3Service(root)
    service.prepare(_batch(service, binding, [_power(manuscript, binding)]))
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(root)

    check = next(item for item in report["checks"] if item["id"] == "canon_v3.staging")
    assert report["workflow_snapshot"]["state"] == "awaiting_human"
    assert report["primary_action"]["code"] == "review_staging"
    assert check["status"] == "ok"
    assert report["canon_v3"]["staging"]["kinds"] == ["chapter"]
    assert report["canon_v3"]["staging"]["transaction_hash"] == report[
        "workflow_snapshot"
    ]["transaction_hash"]
    assert report["canon_v3"]["staging"]["stage_digest"] == report[
        "workflow_snapshot"
    ]["stage_digest"]


def test_doctor_validates_author_axiom_staging_against_same_head(
    tmp_path, monkeypatch
):
    root = tmp_path / "book"
    _make_init_ready(root)
    service = CanonV3Service(root)
    service.initialize_new_project()
    record = _draft_record(
        root,
        name="doctor",
        key="death_is_irreversible",
        value="死亡不可逆",
    )
    service.prepare_author_axioms(_proposal(service, [record]))
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(root)

    checks = {item["id"]: item for item in report["checks"]}
    assert report["workflow_snapshot"]["transaction_kind"] == "author_axiom"
    assert checks["canon_v3.staging"]["status"] == "ok"
    assert checks["canon_v3.author_axioms"]["status"] == "ok"
    assert report["canon_v3"]["staging"]["kinds"] == ["author_axiom"]
    assert report["canon_v3"]["author_axioms"]["transaction_hash"] == report[
        "workflow_snapshot"
    ]["transaction_hash"]


def test_doctor_reports_projection_rebuild_as_primary_action(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    CanonV3Service(tmp_path).initialize_new_project()
    projection_path(tmp_path).unlink()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    projection = next(
        item for item in report["checks"] if item["id"] == "canon_v3.projection"
    )
    assert report["ok"] is False
    assert report["workflow_snapshot"]["state"] == "projection_rebuild_required"
    assert report["primary_action"]["code"] == "rebuild_projection"
    assert report["recommended_actions"] == [
        "canon_ledger.py canon-v3 rebuild-projection"
    ]
    assert projection["status"] == "error"
    assert projection["repair"] == "canon_ledger.py canon-v3 rebuild-projection"


def test_doctor_reports_invalid_current_and_reachable_object_failure(
    tmp_path, monkeypatch
):
    _make_init_ready(tmp_path)
    service = CanonV3Service(tmp_path)
    service.initialize_new_project()
    service.repository.current_path.write_text("0" * 64 + "\n", encoding="ascii")
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    checks = {item["id"]: item for item in report["checks"]}
    assert report["ok"] is False
    assert report["workflow_snapshot"]["state"] == "invalid"
    assert report["primary_action"]["code"] == "run_canon_v3_doctor"
    assert checks["canon_v3.workflow_snapshot"]["status"] == "error"
    assert checks["canon_v3.current_manifest"]["status"] == "error"
    assert "missing_object" in checks["canon_v3.current_manifest"]["actual"]
    assert checks["canon_v3.author_axioms"]["status"] == "error"


def test_doctor_audits_legacy_repair_without_mutating_head(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    manuscript, _commit, _payload = _persist_accepted_commit(tmp_path, 1)
    migrated = migrate_legacy(tmp_path)
    old_head = migrated["head_hash"]
    manuscript.write_text("第1章正文已经被改写。", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    audit = next(
        item for item in report["checks"] if item["id"] == "canon_v3.cutover_audit"
    )
    assert report["workflow_snapshot"]["bootstrap_mode"] == "legacy_repair"
    assert report["primary_action"]["code"] in {
        "remigrate_legacy_suffix",
        "repair_legacy_prefix",
    }
    assert report["primary_action"]["command"] == (
        "canon_ledger.py canon-v3 audit-cutover"
    )
    assert report["recommended_actions"] == [
        "canon_ledger.py canon-v3 audit-cutover"
    ]
    assert report["canon_v3"]["cutover_audit"]["state"] == "blocked"
    assert audit["status"] == "error"
    assert CanonV3Service(tmp_path).repository.current_head() == old_head


def test_doctor_missing_init_file_blocks_with_repair(tmp_path, monkeypatch):
    _make_init_ready(tmp_path)
    (tmp_path / "大纲" / "总纲.md").unlink()
    monkeypatch.setattr(doctor_module, "_python_checks", lambda: [])

    report = doctor_module.build_doctor_report(tmp_path)

    assert report["ok"] is False
    assert report["recommended_actions"] == [
        report["primary_action"]["command"]
    ]
    matches = [item for item in report["checks"] if item["id"] == "file.required.大纲/总纲.md"]
    assert matches
    assert matches[0]["status"] == "error"
    assert matches[0]["repair"]
    runtime = next(
        item for item in report["checks"] if item["id"] == "story_runtime.health"
    )
    assert runtime["message"] == "legacy compatibility runtime health"
    assert "accepted commit" not in runtime["repair"]
    assert "fallback source" not in runtime["repair"]


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


def test_doctor_warns_on_unbound_compatibility_retrieval_rows(tmp_path):
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
    assert provenance["status"] == "warning"
    assert "canon-v3 status" in provenance["repair"]
    assert "projections replay" not in provenance["repair"]


def test_doctor_warns_on_compatibility_retrieval_schema_without_provenance_column(
    tmp_path,
):
    vector_db = tmp_path / ".canon-ledger" / "vectors.db"
    vector_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(vector_db) as conn:
        conn.execute(
            "CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, chapter INTEGER, content TEXT)"
        )

    checks = doctor_module._rag_checks(tmp_path)

    provenance = next(item for item in checks if item["id"] == "rag.retrieval_provenance")
    assert provenance["status"] == "warning"
    assert "unsupported_schema_missing_source_file" in provenance["actual"]
    assert "canon-v3 status" in provenance["repair"]
    assert "projections replay" not in provenance["repair"]


def test_doctor_warns_on_commit_without_compatibility_projection_log(
    tmp_path, monkeypatch
):
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
    assert matches[0]["status"] == "warning"
    assert "projection replay" not in matches[0]["repair"]


def test_doctor_warns_on_pending_compatibility_projection_log_run(
    tmp_path, monkeypatch
):
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
    assert matches[0]["status"] == "warning"
    assert "projection retry" not in matches[0]["repair"]
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

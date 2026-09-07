#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

from data_modules.canon_v3.query import CanonQueryError, CanonQueryFacade
from data_modules.canon_v3.public_read import active_fact_rows
from data_modules.canon_v3.schema import canonical_digest
from data_modules.canon_v3.service import CanonV3Service
from data_modules.config import DataModulesConfig
from data_modules.memory_contract_adapter import MemoryContractAdapter


def _project(root: Path) -> Path:
    for rel in (
        ".canon-ledger",
        "正文",
        "设定集",
        "大纲",
    ):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "project_info": {"title": "查询权威测试", "genre": "玄幻"},
                "progress": {"current_chapter": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    CanonV3Service(root).initialize_new_project()
    return root


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            snapshot[relative] = ("directory", "")
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def _project_with_genesis(root: Path) -> Path:
    for rel in (".canon-ledger", ".story-system", "正文", "设定集", "大纲"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / ".canon-ledger" / "state.json").write_text(
        json.dumps(
            {
                "project_info": {"title": "Genesis 查询测试", "genre": "玄幻"},
                "progress": {"current_chapter": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / ".story-system" / "MASTER_SETTING.json").write_text(
        json.dumps(
            {
                "initial_canon": {
                    "protagonist": {"name": "林舟"},
                    "world": {"scale": "九州大陆"},
                },
                "setting_canon": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    CanonV3Service(root).initialize_new_project()
    return root


def _author_axiom_record(root: Path) -> dict:
    relative = Path(".canon-ledger/tmp/author_axioms/query-world.json")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    value = "死者不能复生"
    payload = {
        "schema_version": "canon-v3/author-axiom-draft/v1",
        "author_axioms": {"death_is_irreversible": value},
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    path.write_bytes(raw)
    quote = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    return {
        "axiom_key": "death_is_irreversible",
        "category": "world_rule",
        "source": {
            "source_type": "author_axiom_draft_span",
            "source_id": "query-world-rule",
            "document_path": relative.as_posix(),
            "document_sha256": hashlib.sha256(raw).hexdigest(),
            "start": start,
            "end": start + len(quote_raw),
            "quote": quote,
            "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
            "json_pointer": "/author_axioms/death_is_irreversible",
            "value": value,
            "value_sha256": canonical_digest(value),
        },
    }


def _publish_author_axiom(service: CanonV3Service, record: dict) -> None:
    workflow = service.workflow_snapshot()
    service.prepare_author_axioms(
        {
            "schema_version": "canon-v3/author-axiom-proposal/v2",
            "parent_head": workflow["head_hash"],
            "workflow_digest": workflow["workflow_digest"],
            "active_author_axiom_digest": workflow["author_axiom_digest"],
            "expected_stage_digest": workflow.get("stage_digest"),
            "records": [record],
            "genesis_overrides": [],
        }
    )
    status = service.author_axiom_status()
    service.record_author_axiom_decisions(
        {
            "schema_version": "canon-v3/author-axiom-decision-request/v2",
            "expected_stage_digest": status["stage_digest"],
            "transaction_hash": status["transaction_hash"],
            "decisions": [
                {
                    "case_key": case["case_key"],
                    **case["decision_binding"],
                    "action": "approve",
                }
                for case in status["cases"]
            ],
        }
    )
    status = service.author_axiom_status()
    service.finalize_author_axioms(
        {
            "schema_version": "canon-v3/author-axiom-finalize-request/v2",
            "expected_stage_digest": status["stage_digest"],
            "transaction_hash": status["transaction_hash"],
            "finalize_token": status["finalize_token"],
        }
    )


def test_query_snapshot_is_head_bound_and_ignores_poisoned_legacy_index(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "book")
    db_path = project / ".canon-ledger" / "index.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE state_changes "
            "(id INTEGER, entity_id TEXT, field TEXT, new_value TEXT, chapter INTEGER)"
        )
        conn.execute(
            "INSERT INTO state_changes VALUES (1, '林舟', '生死', '已死亡（伪造旧投影）', 0)"
        )

    result = CanonQueryFacade(project).entity_state("林舟", as_of_chapter=0)

    assert result["authority"] == "canon_v3"
    assert result["head_hash"]
    assert result["generation"] == 0
    assert result["projection_digest"]
    assert "伪造旧投影" not in json.dumps(result, ensure_ascii=False)


def test_public_active_facts_include_genesis_and_published_author_axioms(
    tmp_path: Path,
) -> None:
    project = _project_with_genesis(tmp_path / "book")
    service = CanonV3Service(project)
    _publish_author_axiom(service, _author_axiom_record(project))

    result = CanonQueryFacade(project).snapshot(as_of_chapter=0)
    facts = result["data"]["active_facts"]

    assert {
        (row["origin"], row["category"], row.get("field"), row.get("value"))
        for row in facts
    } >= {
        ("genesis", "character_state", "name", "林舟"),
        ("genesis", "world_rule", "scale", "九州大陆"),
        (
            "author_axiom",
            "world_rule",
            "death_is_irreversible",
            "死者不能复生",
        ),
    }
    assert len({row["fact_digest"] for row in facts}) == len(facts)
    serialized = json.dumps(facts, ensure_ascii=False)
    assert "legacy_base" not in serialized
    assert '"source_type": "author_axiom"' in serialized
    assert "author_axiom_draft_span" not in serialized
    assert '"quote"' not in serialized

    adapter = MemoryContractAdapter(
        DataModulesConfig(project_root=project)
    )
    exported = adapter.export_asof_snapshot(as_of_chapter=0)
    assert exported["active_facts"] == facts

    context = adapter.load_context(1)
    context_rows = {
        str(row.get("fact_digest") or row.get("record_digest") or "")
        for section in (
            context.sections["canonical_facts"],
            context.sections["hard_constraints"],
            context.sections["author_axioms"],
        )
        for row in section
    }
    assert {row["fact_digest"] for row in facts} == context_rows




def test_cli_knowledge_uses_canon_query_and_legacy_index_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import data_modules.canon_ledger as cli

    project = _project(tmp_path / "book")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "knowledge",
            "query-entity-state",
            "--entity",
            "林舟",
            "--at-chapter",
            "0",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert payload["authority"] == "canon_v3"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "index",
            "stats",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    error = json.loads(capsys.readouterr().err)
    assert int(exc.value.code or 0) == 2
    assert error["error"] == "canon_v3_public_command_disabled"
    assert error["tool"] == "index"


@pytest.mark.parametrize(
    ("tool", "operation"),
    [
        ("index", "stats"),
        ("state", "get-progress"),
        ("rag", "stats"),
        ("entity", "list-aliases"),
        ("memory", "stats"),
    ],
)
def test_explicit_legacy_read_flag_cannot_create_or_touch_adapter_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    operation: str,
) -> None:
    import data_modules.canon_ledger as cli

    project = _project(tmp_path / "book")
    before = _tree_snapshot(project)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "--legacy-read-only",
            tool,
            operation,
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()

    error = json.loads(capsys.readouterr().err)
    assert int(exc.value.code or 0) == 2
    assert error["error"] == "canon_v3_public_command_disabled"
    assert error["tool"] == tool
    assert _tree_snapshot(project) == before


def test_canon_v3_query_cli_returns_bound_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import data_modules.canon_ledger as cli

    project = _project(tmp_path / "book")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "canon-v3",
            "query",
            "snapshot",
            "--as-of-chapter",
            "0",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert payload["schema_version"] == "canon-v3/query-result/v1"
    assert payload["authority"] == "canon_v3"
    assert payload["head_hash"]


def test_canon_v3_history_is_the_sanitized_public_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import data_modules.canon_ledger as cli

    project = _project(tmp_path / "book")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canon-ledger",
            "--project-root",
            str(project),
            "canon-v3",
            "history",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert int(exc.value.code or 0) == 0
    assert payload["schema_version"] == "canon-v3/query-result/v1"
    assert payload["query"] == "snapshot"
    assert payload["authority"] == "canon_v3"
    assert "legacy_base" not in payload


def test_query_fails_closed_when_asof_history_reports_invalid_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path / "book")
    monkeypatch.setattr(
        "data_modules.canon_v3.query.export_asof_snapshot",
        lambda *_args, **_kwargs: {
            "as_of_chapter": 0,
            "invalid_sources": ["canon_v3_projection_binding_invalid"],
        },
    )

    with pytest.raises(CanonQueryError, match="query_invalid_sources"):
        CanonQueryFacade(project).snapshot(as_of_chapter=0)


def test_ambiguous_entity_query_requests_human_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data_modules.canon_v3.query import _BoundSnapshot

    entities = {
        "actor:a": {"id": "actor:a", "name": "小白"},
        "actor:b": {"id": "actor:b", "name": "小白"},
    }
    facade = CanonQueryFacade(tmp_path / "book")
    bound = _BoundSnapshot(
        workflow={
            "head_hash": "a" * 64,
            "generation": 0,
            "workflow_digest": "b" * 64,
        },
        projection={},
        as_of={
            "as_of_chapter": 0,
            "invalid_sources": [],
            "entities": entities,
            "state_changes": [],
        },
    )
    monkeypatch.setattr(facade, "_bound_snapshot", lambda _chapter: bound)

    data = facade.entity_state("小白", as_of_chapter=0)["data"]
    assert data["resolution"] == "ambiguous"
    assert data["requires_human_resolution"] is True
    assert data["matching_entity_ids"] == ["actor:a", "actor:b"]
    assert data["state"] == {}


def test_relationship_query_preserves_opposite_directions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data_modules.canon_v3.query import _BoundSnapshot

    facade = CanonQueryFacade(tmp_path / "book")
    bound = _BoundSnapshot(
        workflow={
            "head_hash": "a" * 64,
            "generation": 2,
            "workflow_digest": "b" * 64,
        },
        projection={},
        as_of={
            "as_of_chapter": 2,
            "invalid_sources": [],
            "entities": {
                "actor:a": {"id": "actor:a", "name": "甲"},
                "actor:b": {"id": "actor:b", "name": "乙"},
            },
            "hard_constraints": [
                {
                    "category": "relationship_changed",
                    "subject": "actor:a",
                    "field": "actor:b",
                    "value": "戒备",
                    "source_chapter": 1,
                    "fact_digest": "1" * 64,
                    "payload": {
                        "kind": "relationship_changed",
                        "subject": "actor:a",
                        "object": "actor:b",
                        "after": "戒备",
                    },
                },
                {
                    "category": "relationship_changed",
                    "subject": "actor:b",
                    "field": "actor:a",
                    "value": "信任",
                    "source_chapter": 2,
                    "fact_digest": "2" * 64,
                    "payload": {
                        "kind": "relationship_changed",
                        "subject": "actor:b",
                        "object": "actor:a",
                        "after": "信任",
                    },
                },
            ],
        },
    )
    monkeypatch.setattr(facade, "_bound_snapshot", lambda _chapter: bound)

    rows = facade.relationships("甲", as_of_chapter=2)["data"]["relationships"]
    assert [(row["subject"], row["object"], row["relationship"]) for row in rows] == [
        ("actor:a", "actor:b", "戒备"),
        ("actor:b", "actor:a", "信任"),
    ]

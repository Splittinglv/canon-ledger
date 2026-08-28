#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from data_modules.chapter_content_binding import build_chapter_binding
from data_modules.canon_v3 import retrieval as retrieval_module
from data_modules.canon_v3.evidence import candidate_digest
from data_modules.canon_v3.query import CanonQueryFacade
from data_modules.canon_v3.retrieval import (
    RETRIEVAL_SEARCH_REQUEST_SCHEMA,
    _fact_search_text,
    rebuild_retrieval_projection,
    retrieval_path,
    retrieval_status,
    search_retrieval,
)
from data_modules.canon_v3.schema import (
    FactCandidate,
    OpenLoopCreatedClaim,
    canonical_digest,
)
from data_modules.canon_v3.service import CanonV3Service
from data_modules.config import DataModulesConfig
from data_modules.memory_contract_adapter import MemoryContractAdapter
from data_modules.tests.canon_v3_protocol_helpers import (
    finalize,
    proposal_authority,
)


def _project(root: Path) -> Path:
    state = root / ".canon-ledger" / "state.json"
    master = root / ".story-system" / "MASTER_SETTING.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    master.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {
                "project_info": {"title": "Retrieval 测试", "genre": "玄幻"},
                "progress": {"current_chapter": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    master.write_text(
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


def _request(
    query: str,
    *,
    as_of_chapter: int | None = None,
    mode: str = "auto",
    top_k: int = 8,
) -> dict:
    return {
        "schema_version": RETRIEVAL_SEARCH_REQUEST_SCHEMA,
        "query": query,
        "as_of_chapter": as_of_chapter,
        "top_k": top_k,
        "mode": mode,
        "categories": [],
    }


def _tree_bytes(root: Path) -> dict[str, bytes | str]:
    rows: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[relative] = f"symlink:{path.readlink()}"
        elif path.is_file():
            rows[relative] = path.read_bytes()
    return rows


def _author_record(root: Path, value: str, *, suffix: str) -> dict:
    relative = Path(f".canon-ledger/tmp/author_axioms/retrieval-{suffix}.json")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "canon-v3/author-axiom-draft/v1",
        "author_axioms": {"moon_gate_rule": value},
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path.write_bytes(raw)
    quote = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    return {
        "axiom_key": "moon_gate_rule",
        "category": "world_rule",
        "source": {
            "source_type": "author_axiom_draft_span",
            "source_id": f"retrieval-{suffix}",
            "document_path": relative.as_posix(),
            "document_sha256": hashlib.sha256(raw).hexdigest(),
            "start": start,
            "end": start + len(quote_raw),
            "quote": quote,
            "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
            "json_pointer": "/author_axioms/moon_gate_rule",
            "value": value,
            "value_sha256": canonical_digest(value),
        },
    }


def _publish_author_records(root: Path, records: list[dict]) -> None:
    service = CanonV3Service(root)
    workflow = service.workflow_snapshot()
    service.prepare_author_axioms(
        {
            "schema_version": "canon-v3/author-axiom-proposal/v2",
            "parent_head": workflow["head_hash"],
            "workflow_digest": workflow["workflow_digest"],
            "active_author_axiom_digest": workflow["author_axiom_digest"],
            "expected_stage_digest": workflow.get("stage_digest"),
            "records": records,
            "genesis_overrides": [],
        }
    )
    staged = service.author_axiom_status()
    service.record_author_axiom_decisions(
        {
            "schema_version": "canon-v3/author-axiom-decision-request/v2",
            "expected_stage_digest": staged["stage_digest"],
            "transaction_hash": staged["transaction_hash"],
            "decisions": [
                {
                    "case_key": case["case_key"],
                    **case["decision_binding"],
                    "action": "approve",
                }
                for case in staged["cases"]
            ],
        }
    )
    staged = service.author_axiom_status()
    service.finalize_author_axioms(
        {
            "schema_version": "canon-v3/author-axiom-finalize-request/v2",
            "expected_stage_digest": staged["stage_digest"],
            "transaction_hash": staged["transaction_hash"],
            "finalize_token": staged["finalize_token"],
        }
    )


def _publish_loop(root: Path, chapter: int, loop: str) -> None:
    manuscript = root / "正文" / f"第{chapter:04d}章.md"
    manuscript.parent.mkdir(parents=True, exist_ok=True)
    quote = f"{loop}，仍未揭晓。"
    manuscript.write_text(quote, encoding="utf-8")
    binding = build_chapter_binding(root, chapter)
    raw = manuscript.read_bytes()
    quote_raw = quote.encode("utf-8")
    start = raw.index(quote_raw)
    candidate = FactCandidate(
        candidate_id=f"loop-{chapter}",
        claim=OpenLoopCreatedClaim(loop=loop),
        sources=(
            {
                "source_type": "manuscript_span",
                "source_id": f"loop-span-{chapter}",
                "document_sha256": binding["sha256"],
                "chapter": chapter,
                "start": start,
                "end": start + len(quote_raw),
                "quote": quote,
                "quote_sha256": hashlib.sha256(quote_raw).hexdigest(),
            },
        ),
        support_map={"loop": (f"loop-span-{chapter}",)},
    )
    service = CanonV3Service(root)
    authority = proposal_authority(service, chapter)
    digest = candidate_digest(candidate)
    service.prepare(
        {
            **authority,
            "chapter": chapter,
            "chapter_binding": binding,
            "candidates": [candidate.model_dump(mode="json")],
            "observations": [],
            "scan_attestations": [
                {
                    "attestation_id": f"retrieval-scan-{chapter}",
                    "scanner": "reviewer",
                    "scanner_version": "retrieval-test",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": authority["parent_head"],
                    "author_axiom_digest": authority["author_axiom_digest"],
                    "entity_registry_digest": authority[
                        "entity_registry_digest"
                    ],
                    "dimensions": [
                        "setting",
                        "timeline",
                        "continuity",
                        "character",
                        "logic",
                    ],
                    "status": "complete",
                    "checked_candidate_digests": [digest],
                }
            ],
        }
    )
    finalize(service)


def test_missing_index_search_is_read_only_head_bound_bm25(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    root = _project(tmp_path / "book")
    before = _tree_bytes(root)

    status = retrieval_status(root)
    result = search_retrieval(root, _request("九州大陆"))

    assert status["state"] == "missing"
    assert status["writing_blocked"] is False
    assert result["mode"] == "bm25"
    assert result["index_state"] == "in_memory"
    assert result["degraded"] is True
    assert result["hits"][0]["active_fact"]["value"] == "九州大陆"
    assert result["hits"][0]["usable_as_canon"] is False
    assert result["hits"][0]["resolved_against"] == "active_canon"
    assert not retrieval_path(root).exists()
    assert _tree_bytes(root) == before


def test_bm25_rebuild_is_exact_and_does_not_create_legacy_vector_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    root = _project(tmp_path / "book")

    rebuilt = rebuild_retrieval_projection(root, bm25_only=True)
    status = retrieval_status(root)
    result = search_retrieval(root, _request("林舟", mode="bm25"))

    assert rebuilt["mode"] == "bm25"
    assert rebuilt["usable_as_canon"] is False
    assert status["state"] == "ready"
    assert status["head_hash"] == CanonQueryFacade(root).snapshot()["head_hash"]
    assert result["index_state"] == "ready"
    assert result["hits"][0]["active_fact"]["value"] == "林舟"
    assert retrieval_path(root).is_file()
    assert not (root / ".canon-ledger" / "vectors.db").exists()


def test_missing_embedding_key_never_constructs_remote_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CANON_LEDGER_RETRIEVAL_REMOTE", "1")
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    root = _project(tmp_path / "book")

    class ForbiddenClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("missing key must not construct an HTTP client")

    monkeypatch.setattr(retrieval_module, "EmbeddingAPIClient", ForbiddenClient)
    rebuilt = rebuild_retrieval_projection(root)

    assert rebuilt["mode"] == "bm25"
    assert rebuilt["degraded_reason"] == "embedding_not_configured"
    assert rebuilt["writing_blocked"] is False


def test_global_embedding_key_without_opt_in_never_constructs_remote_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_API_KEY", "global-key")
    monkeypatch.delenv("CANON_LEDGER_RETRIEVAL_REMOTE", raising=False)
    root = _project(tmp_path / "book")

    class ForbiddenClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("a key alone must not enable remote embedding")

    monkeypatch.setattr(retrieval_module, "EmbeddingAPIClient", ForbiddenClient)
    rebuilt = rebuild_retrieval_projection(root)

    assert rebuilt["mode"] == "bm25"
    assert rebuilt["degraded_reason"] == "remote_embedding_disabled"
    assert rebuilt["writing_blocked"] is False


def test_auto_search_with_stored_vectors_respects_remote_opt_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_API_KEY", "global-key")
    monkeypatch.delenv("CANON_LEDGER_RETRIEVAL_REMOTE", raising=False)
    root = _project(tmp_path / "book")
    rebuilt = rebuild_retrieval_projection(
        root,
        embedder=lambda _config, texts: ([[1.0, 0.0]] * len(texts), ""),
    )

    class ForbiddenClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("local-only search must not embed its query remotely")

    monkeypatch.setattr(retrieval_module, "EmbeddingAPIClient", ForbiddenClient)
    result = search_retrieval(root, _request("林舟", mode="auto"))

    assert rebuilt["mode"] == "vector"
    assert result["mode"] == "bm25"
    assert result["reason"] == "remote_embedding_disabled"
    assert result["hits"]


def test_embedding_failure_still_installs_a_usable_bm25_projection(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path / "book")

    def failed_embedder(_config: DataModulesConfig, texts):
        return [None] * len(texts), "embedding_request_failed"

    rebuilt = rebuild_retrieval_projection(root, embedder=failed_embedder)
    result = search_retrieval(root, _request("林舟", mode="vector"))

    assert rebuilt["state"] == "ready"
    assert rebuilt["mode"] == "bm25"
    assert rebuilt["degraded_reason"] == "embedding_request_failed"
    assert result["mode"] == "bm25"
    assert result["writing_blocked"] is False
    assert result["hits"][0]["active_fact"]["value"] == "林舟"


def test_vector_rebuild_reuses_exact_unchanged_embeddings(tmp_path: Path) -> None:
    root = _project(tmp_path / "book")
    calls: list[list[str]] = []

    def embedder(_config: DataModulesConfig, texts):
        calls.append(list(texts))
        return [
            [1.0, 0.0] if "九州" in text else [0.0, 1.0]
            for text in texts
        ], ""

    first = rebuild_retrieval_projection(root, embedder=embedder)

    def must_not_embed(_config: DataModulesConfig, _texts):
        raise AssertionError("unchanged facts must reuse exact embeddings")

    second = rebuild_retrieval_projection(root, embedder=must_not_embed)
    result = search_retrieval(
        root,
        _request("大陆边界", mode="auto"),
        query_embedder=lambda _config, _query: ([1.0, 0.0], ""),
    )

    assert first["mode"] == "vector"
    assert len(calls) == 1
    assert second["reused_embedding_count"] == second["fact_count"]
    assert result["mode"] == "hybrid"
    assert result["hits"][0]["active_fact"]["value"] == "九州大陆"


def test_stale_index_never_surfaces_superseded_author_axiom(tmp_path: Path) -> None:
    root = _project(tmp_path / "book")
    old = _author_record(root, "月门只在夜间开启", suffix="old")
    _publish_author_records(root, [old])
    rebuild_retrieval_projection(root, bm25_only=True)

    new = _author_record(root, "月门只在黎明开启", suffix="new")
    _publish_author_records(root, [new])

    assert retrieval_status(root)["state"] == "stale"
    stale_fallback = search_retrieval(root, _request("月门黎明"))
    serialized = json.dumps(stale_fallback, ensure_ascii=False)
    assert "月门只在黎明开启" in serialized
    assert "月门只在夜间开启" not in serialized

    rebuild_retrieval_projection(root, bm25_only=True)
    exact = search_retrieval(root, _request("月门"))
    serialized = json.dumps(exact, ensure_ascii=False)
    assert retrieval_status(root)["state"] == "ready"
    assert "月门只在黎明开启" in serialized
    assert "月门只在夜间开启" not in serialized


def test_tampered_index_is_ignored_and_search_remains_read_only(tmp_path: Path) -> None:
    root = _project(tmp_path / "book")
    rebuild_retrieval_projection(root, bm25_only=True)
    path = retrieval_path(root)
    with sqlite3.connect(path) as conn:
        raw = conn.execute(
            "SELECT retrieval_id, fact_json FROM facts ORDER BY retrieval_id LIMIT 1"
        ).fetchone()
        assert raw is not None
        fact = json.loads(raw[1])
        fact["tampered_note"] = "投毒内容：主角已经死亡"
        conn.execute(
            "UPDATE facts SET fact_json = ?, fact_content_digest = ? "
            "WHERE retrieval_id = ?",
            (
                json.dumps(fact, ensure_ascii=False, sort_keys=True),
                canonical_digest(fact),
                raw[0],
            ),
        )
        conn.commit()
    before = _tree_bytes(root)

    status = retrieval_status(root)
    result = search_retrieval(root, _request("九州大陆"))

    assert status["state"] == "invalid"
    assert status["writing_blocked"] is False
    assert result["index_state"] == "invalid"
    assert result["mode"] == "bm25"
    serialized = json.dumps(result, ensure_ascii=False)
    assert "九州大陆" in serialized
    assert "投毒内容" not in serialized
    assert _tree_bytes(root) == before


def test_historical_search_never_leaks_future_facts(tmp_path: Path) -> None:
    root = _project(tmp_path / "book")
    _publish_loop(root, 1, "第一谜题")
    _publish_loop(root, 2, "第二谜题")
    rebuild_retrieval_projection(root, bm25_only=True)

    chapter_one = search_retrieval(
        root,
        _request("第二谜题", as_of_chapter=1),
    )
    chapter_two = search_retrieval(
        root,
        _request("第二谜题", as_of_chapter=2),
    )

    assert chapter_one["as_of_chapter"] == 1
    assert chapter_one["index_state"] == "in_memory"
    assert all(hit["source_chapter"] <= 1 for hit in chapter_one["hits"])
    assert "第二谜题" not in json.dumps(chapter_one, ensure_ascii=False)
    assert any(
        hit["active_fact"]["payload"]["loop"] == "第二谜题"
        for hit in chapter_two["hits"]
    )


def test_knowledge_rendering_keeps_the_subject_boundary_explicit() -> None:
    row = {
        "schema_version": "canon-v3/public-active-fact/v1",
        "authority_layer": "active_canon",
        "authority_state": "active",
        "origin": "chapter_commit",
        "fact_digest": "a" * 64,
        "category": "knowledge_state_changed",
        "subject": "林舟",
        "field": "密门位置",
        "value": "known",
        "payload": {
            "kind": "knowledge_state_changed",
            "proposition": "密门在钟楼下",
            "after": "known",
        },
        "source_chapter": 3,
    }

    text = _fact_search_text(row)

    assert text == "知识边界事实：林舟；命题：密门在钟楼下；状态：known"
    assert "所有人" not in text


def test_context_uses_only_resolved_v3_retrieval_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    root = _project(tmp_path / "book")
    outline = root / "大纲" / "第1章-章纲.md"
    outline.parent.mkdir(parents=True, exist_ok=True)
    outline.write_text("林舟回顾九州大陆的边界。", encoding="utf-8")
    rebuild_retrieval_projection(root, bm25_only=True)

    pack = MemoryContractAdapter(
        DataModulesConfig.from_project_root(root)
    ).load_context(1)
    assist = pack.sections["rag_assist"]

    assert assist["invoked"] is True
    assert assist["mode"] == "bm25"
    assert assist["head_hash"] == CanonQueryFacade(root).snapshot()["head_hash"]
    assert assist["hits"]
    assert all(
        hit["authority_layer"] == "retrieval_assist"
        and hit["resolved_against"] == "active_canon"
        and hit["usable_as_canon"] is False
        and hit["active_fact"]["authority_state"] == "active"
        for hit in assist["hits"]
    )

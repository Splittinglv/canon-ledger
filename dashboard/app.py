"""
CanonLedger Dashboard - FastAPI 主应用

仅提供 GET 接口（严格只读）；所有文件读取经过 path_guard 防穿越校验。
"""

import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .path_guard import safe_resolve
from .watcher import FileWatcher

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------
_project_root: Path | None = None
_watcher = FileWatcher()

STATIC_DIR = Path(__file__).parent / "frontend" / "dist"
LOCAL_CORS_ORIGINS = [
    "http://localhost",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
]


def _get_project_root() -> Path:
    if _project_root is None:
        raise HTTPException(status_code=500, detail="项目根目录未配置")
    return _project_root


def _canon_ledger_dir() -> Path:
    return _get_project_root() / ".canon-ledger"


def _story_system_dir() -> Path:
    return _get_project_root() / ".story-system"


def _ensure_scripts_dir_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    scripts_entry = str(scripts_dir)
    if scripts_entry not in sys.path:
        sys.path.insert(0, scripts_entry)


def _load_state_payload(*, required: bool = False) -> dict:
    state_path = _canon_ledger_dir() / "state.json"
    if not state_path.is_file():
        if required:
            raise HTTPException(404, "state.json 不存在")
        return {}

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"state.json 读取失败: {exc}") from exc

    return payload if isinstance(payload, dict) else {}


def _inspect_vector_db(project_root: Path) -> dict:
    """Inspect the retired v2 store for disclosure only, never for readiness."""

    from data_modules.config import DataModulesConfig

    cfg = DataModulesConfig.from_project_root(project_root)
    vector_db = cfg.vector_db
    exists = vector_db.is_file()
    size_bytes = vector_db.stat().st_size if exists else 0
    record_count = 0
    error = ""

    if vector_db.is_symlink():
        error = "legacy_vector_db_symlink_ignored"
    elif exists and size_bytes > 0:
        try:
            uri = f"{vector_db.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                cursor = conn.cursor()
                table_exists = cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vectors'"
                ).fetchone()
                if table_exists:
                    row = cursor.execute("SELECT COUNT(*) FROM vectors").fetchone()
                    record_count = int(row[0] or 0) if row else 0
        except sqlite3.Error as exc:
            error = str(exc)

    return {
        "path": str(vector_db),
        "exists": exists,
        "size_bytes": size_bytes,
        "record_count": record_count,
        "error": error,
        "authority_layer": "legacy_ignored",
        "usable_for_retrieval": False,
        "usable_as_canon": False,
    }


def _build_env_status(project_root: Path) -> dict:
    from data_modules.config import DataModulesConfig
    from data_modules.canon_v3.retrieval import retrieval_status

    cfg = DataModulesConfig.from_project_root(project_root)
    legacy_vector_info = _inspect_vector_db(project_root)
    retrieval = retrieval_status(project_root)

    embed_key_present = cfg.embedding_enabled
    remote_opt_in = cfg.retrieval_remote_enabled
    remote_enabled = cfg.retrieval_embedding_enabled
    rerank_ready = bool(str(cfg.rerank_api_key or "").strip())
    retrieval_mode = str(retrieval.get("mode") or "bm25")
    if retrieval.get("state") != "ready":
        retrieval_mode = "bm25_fallback"
    elif not remote_enabled:
        # The projection may still contain reusable vectors, but without the
        # project privacy opt-in no remote query embedding is allowed.
        retrieval_mode = "bm25"

    return {
        "embed": {
            "base_url": cfg.embed_base_url,
            "model": cfg.embed_model,
            "api_key_present": embed_key_present,
            "remote_opt_in": remote_opt_in,
            "remote_enabled": remote_enabled,
        },
        "rerank": {
            "base_url": cfg.rerank_base_url,
            "model": cfg.rerank_model,
            "api_key_present": rerank_ready,
            "used_by_canon_v3_retrieval": False,
        },
        "retrieval": retrieval,
        "legacy_vector_db": legacy_vector_info,
        # Compatibility alias for older Dashboard clients. The authority labels
        # make it explicit that existence is not retrieval readiness.
        "vector_db": legacy_vector_info,
        "rag_mode": retrieval_mode,
    }


def _canon_dashboard_view() -> dict[str, Any]:
    """Build one disposable dashboard view from an exact fresh Canon HEAD."""

    from data_modules.canon_v3.public_read import PublicReadError, active_fact_rows
    from data_modules.canon_v3.query import CanonQueryError, CanonQueryFacade
    from data_modules.workflow_authority import WorkflowAuthority

    authority = WorkflowAuthority(_get_project_root())
    try:
        bound = CanonQueryFacade(_get_project_root()).bound_snapshot()
        workflow = bound.workflow
        projection = bound.projection
        snapshot = bound.as_of
        active_facts = active_fact_rows(snapshot)
    except (CanonQueryError, PublicReadError) as exc:
        workflow = authority.snapshot()
        state = str(workflow.get("state") or "invalid")
        raise HTTPException(
            status_code=409,
            detail={
                "schema_version": "canon-v3/dashboard-error/v1",
                "code": (
                    state
                    if state
                    in {
                        "projection_rebuild_required",
                        "migration_required",
                        "recompile_required",
                        "rewrite_required",
                        "invalid",
                    }
                    else "canon_v3_read_model_unavailable"
                ),
                "message": str(exc),
                "authority": "canon_v3",
                "usable_for_writing": False,
                "workflow": workflow,
                "primary_action": workflow.get("primary_action"),
            },
        ) from exc
    from data_modules.canon_v3.schema import canonical_digest

    binding = {
        "schema_version": "canon-v3/dashboard-binding/v1",
        "authority": "canon_v3",
        "head_hash": workflow.get("head_hash"),
        "generation": int(workflow.get("generation") or 0),
        "workflow_digest": workflow.get("workflow_digest"),
        "author_axiom_digest": workflow.get("author_axiom_digest"),
        "projection_digest": canonical_digest(projection),
        "as_of_chapter": int(snapshot.get("as_of_chapter") or 0),
    }
    return {
        "workflow": workflow,
        "projection": projection,
        "snapshot": snapshot,
        "active_facts": active_facts,
        "binding": binding,
    }


def _bound_items(view: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "binding": dict(view["binding"]),
        "source": "canon_v3_head",
        "items": items,
    }


def _historical_fact_rows(items: Any) -> list[dict[str, Any]]:
    """Label HEAD-bound history without presenting it as current active Canon."""

    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["authority_layer"] = "canon_history"
        row["authority_state"] = "historical"
        row["usable_as_active_fact"] = False
        rows.append(row)
    return rows


def _legacy_dashboard_endpoint_retired(
    endpoint: str,
    *,
    replacement: str | None = None,
) -> None:
    """Fail closed instead of presenting legacy index analytics as Canon facts."""

    raise HTTPException(
        status_code=410,
        detail={
            "schema_version": "canon-v3/dashboard-error/v1",
            "code": "legacy_dashboard_endpoint_retired",
            "endpoint": endpoint,
            "authority": "legacy_read_only",
            "usable_for_writing": False,
            "replacement": replacement,
        },
    )


def _dashboard_entities(view: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = view["snapshot"]
    protagonist = str(
        ((snapshot.get("initial_canon") or {}).get("protagonist") or {}).get(
            "name"
        )
        or ""
    )
    rows: list[dict[str, Any]] = []
    for stable_id, raw in sorted((snapshot.get("entities") or {}).items()):
        if not isinstance(raw, dict):
            continue
        entity_id = str(raw.get("id") or stable_id)
        name = str(raw.get("name") or entity_id)
        attributes = raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {}
        rows.append(
            {
                "id": entity_id,
                "canonical_name": name,
                "type": str(raw.get("type") or "实体"),
                "namespace": str(raw.get("namespace") or "actor"),
                "tier": str(raw.get("tier") or ""),
                "aliases": [str(item) for item in raw.get("aliases") or []],
                "first_appearance": int(raw.get("first_appearance") or 0),
                "last_appearance": int(raw.get("last_appearance") or 0),
                "desc": str(raw.get("desc") or raw.get("description") or ""),
                "current_json": json.dumps(attributes, ensure_ascii=False, sort_keys=True),
                "is_archived": 0,
                "is_protagonist": bool(protagonist and name == protagonist),
            }
        )
    rows.sort(key=lambda row: (-int(row["last_appearance"]), row["id"]))
    return rows


def _entity_id_lookup(view: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in _dashboard_entities(view):
        for value in (row["id"], row["canonical_name"], *(row.get("aliases") or [])):
            token = str(value or "").strip()
            if token and token not in lookup:
                lookup[token] = row["id"]
    return lookup


def _relationship_row(
    raw: dict[str, Any],
    lookup: dict[str, str],
) -> dict[str, Any] | None:
    claim = raw.get("claim") if isinstance(raw.get("claim"), dict) else raw.get("payload")
    claim = claim if isinstance(claim, dict) else {}
    kind = str(claim.get("kind") or raw.get("category") or "")
    if kind not in {"relationship", "relationship_changed"}:
        return None
    subject = str(
        claim.get("subject")
        or raw.get("subject")
        or raw.get("from_entity")
        or ""
    )
    object_name = str(
        claim.get("object")
        or raw.get("object")
        or raw.get("to_entity")
        or raw.get("field")
        or ""
    )
    if not subject or not object_name:
        return None
    relation = str(
        claim.get("after")
        or claim.get("relationship")
        or claim.get("state")
        or raw.get("value")
        or "关联"
    )
    return {
        "id": str(raw.get("fact_digest") or raw.get("id") or raw.get("effect_id") or ""),
        "from_entity": lookup.get(subject, subject),
        "to_entity": lookup.get(object_name, object_name),
        "type": relation,
        "event_type": "relationship_changed",
        "description": relation,
        "chapter": int(raw.get("chapter") or raw.get("source_chapter") or 0),
        "revision": int(raw.get("revision") or 0),
    }


def _dashboard_state_changes(
    view: dict[str, Any],
    *,
    entity: str | None = None,
) -> list[dict[str, Any]]:
    lookup = _entity_id_lookup(view)
    rows: list[dict[str, Any]] = []
    for raw in view["snapshot"].get("state_changes") or []:
        if not isinstance(raw, dict):
            continue
        claim = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        kind = str(claim.get("kind") or raw.get("category") or "")
        if kind not in {
            "character_state",
            "character_state_changed",
            "power_breakthrough",
        }:
            continue
        subject = str(
            claim.get("subject")
            or raw.get("subject")
            or raw.get("entity_id")
            or ""
        )
        entity_id = lookup.get(subject, subject)
        if entity and entity_id != entity:
            continue
        rows.append(
            {
                "id": str(raw.get("fact_digest") or raw.get("id") or ""),
                "entity_id": entity_id,
                "field": str(
                    claim.get("canonical_field")
                    or claim.get("attribute")
                    or claim.get("system")
                    or raw.get("field")
                    or "state"
                ),
                "old_value": claim.get("before", raw.get("old_value")),
                "new_value": claim.get("after", raw.get("value")),
                "chapter": int(raw.get("source_chapter") or raw.get("chapter") or 0),
                "revision": int(raw.get("revision") or 0),
            }
        )
    rows.sort(key=lambda row: (-row["chapter"], row["id"]))
    return rows


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------

def create_app(project_root: str | Path | None = None) -> FastAPI:
    global _project_root

    if project_root:
        _project_root = Path(project_root).resolve()

    _ensure_scripts_dir_on_path()

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        canon_ledger = _canon_ledger_dir()
        story_system = _story_system_dir()
        if canon_ledger.is_dir() or story_system.is_dir():
            _watcher.start(
                watch_canon_ledger_dir=canon_ledger if canon_ledger.is_dir() else None,
                watch_story_system_dir=story_system if story_system.is_dir() else None,
                loop=asyncio.get_running_loop(),
            )
        try:
            yield
        finally:
            _watcher.stop()

    app = FastAPI(title="CanonLedger Dashboard", version="0.2.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=LOCAL_CORS_ORIGINS,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # ===========================================================
    # API：项目元信息
    # ===========================================================

    @app.get("/api/project/info")
    def project_info():
        """Return planning metadata plus the exact public workflow snapshot."""

        from data_modules.workflow_authority import WorkflowAuthority

        raw = _load_state_payload(required=True)
        workflow = WorkflowAuthority(_get_project_root()).snapshot()
        factual_keys = {
            "protagonist_state",
            "plot_threads",
            "chapter_meta",
            "relationships",
            "entities",
        }
        payload = {key: value for key, value in raw.items() if key not in factual_keys}
        payload["legacy_advisory"] = {
            key: raw[key] for key in factual_keys if key in raw
        }
        progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
        progress = dict(progress)
        if workflow.get("latest_chapter") is not None:
            progress["current_chapter"] = int(workflow.get("latest_chapter") or 0)
        payload["progress"] = progress
        payload["canon_binding"] = {
            "authority": "canon_v3",
            "head_hash": workflow.get("head_hash"),
            "generation": int(workflow.get("generation") or 0),
            "workflow_digest": workflow.get("workflow_digest"),
            "projection_fresh": bool(workflow.get("projection_fresh")),
        }
        payload["workflow"] = workflow
        payload["source"] = "planning_metadata_with_canon_v3_workflow"
        return payload

    @app.get("/api/story-runtime/health")
    def story_runtime_health():
        """Compatibility health alias backed only by Canon v3 authority."""
        from data_modules.workflow_authority import WorkflowAuthority

        return WorkflowAuthority(_get_project_root()).snapshot()

    @app.get("/api/canon-v3/workflow")
    def canon_v3_workflow():
        """Return the same v3 snapshot used by CLI, reports, and write gates."""
        from data_modules.workflow_authority import WorkflowAuthority

        return WorkflowAuthority(_get_project_root()).snapshot()

    @app.get("/api/canon-v3/retrieval")
    def canon_v3_retrieval():
        """Return read-only status for the optional HEAD-bound retrieval plane."""
        from data_modules.canon_v3.retrieval import retrieval_status

        return retrieval_status(_get_project_root())

    @app.get("/api/canon-v3/history")
    def canon_v3_history():
        view = _canon_dashboard_view()
        snapshot = view["snapshot"]
        projection = view["projection"]
        return {
            "schema_version": "canon-v3/dashboard-history/v2",
            "binding": dict(view["binding"]),
            "source": "canon_v3_head",
            "authority_layer": "head_bound_canon_bundle",
            "facts_authority_layer": "active_canon",
            "history_authority_layer": "canon_history",
            "chapters": list(projection.get("chapters") or []),
            "initial_canon": dict(snapshot.get("initial_canon") or {}),
            "setting_canon": dict(snapshot.get("setting_canon") or {}),
            "author_axioms": dict(snapshot.get("author_axioms") or {}),
            "facts": list(view["active_facts"]),
            "history": _historical_fact_rows(projection.get("history")),
        }

    @app.get("/api/canon-v3/facts")
    def canon_v3_facts(
        family: Optional[str] = None,
        include_history: bool = False,
    ):
        """Return active or historical facts from one exact HEAD binding."""

        view = _canon_dashboard_view()
        if include_history:
            rows = _historical_fact_rows(view["projection"].get("history"))
        else:
            rows = [
                dict(item)
                for item in view["active_facts"]
                if isinstance(item, dict)
            ]
        if family:
            rows = [
                row
                for row in rows
                if str(
                    row.get("category")
                    or (row.get("claim") or {}).get("kind")
                    or (row.get("payload") or {}).get("kind")
                    or ""
                )
                == family
            ]
        payload = _bound_items(view, rows)
        payload["schema_version"] = "canon-v3/dashboard-facts/v2"
        payload["authority_layer"] = (
            "canon_history" if include_history else "active_canon"
        )
        payload["authority_state"] = (
            "historical" if include_history else "active"
        )
        payload["view"] = "history" if include_history else "active"
        return payload

    @app.get("/api/canon-v3/characters")
    def canon_v3_characters():
        """Return the entire character fact page from one atomic view."""

        view = _canon_dashboard_view()
        lookup = _entity_id_lookup(view)
        relationships = [
            normalized
            for raw in view["snapshot"].get("canonical_facts") or []
            if isinstance(raw, dict)
            and (normalized := _relationship_row(raw, lookup)) is not None
        ]
        relationship_events = [
            normalized
            for raw in view["projection"].get("history") or []
            if isinstance(raw, dict)
            and (normalized := _relationship_row(raw, lookup)) is not None
        ]
        relationships.sort(key=lambda row: (-row["chapter"], row["id"]))
        relationship_events.sort(key=lambda row: (-row["chapter"], row["id"]))
        return {
            "binding": dict(view["binding"]),
            "source": "canon_v3_head",
            "latest_chapter": int(view["workflow"].get("latest_chapter") or 0),
            "entities": _dashboard_entities(view),
            "relationships": relationships,
            "relationship_events": relationship_events,
            "state_changes": _dashboard_state_changes(view),
        }

    @app.get("/api/canon-v3/obligations")
    def canon_v3_obligations():
        """Return active/open and historical lifecycle facts from one HEAD."""

        view = _canon_dashboard_view()
        return {
            "binding": dict(view["binding"]),
            "source": "canon_v3_head",
            "latest_chapter": int(view["workflow"].get("latest_chapter") or 0),
            "items": [
                dict(item)
                for item in view["snapshot"].get("obligations") or []
                if isinstance(item, dict)
            ],
            "lifecycle_history": [
                dict(item)
                for item in view["snapshot"].get("lifecycle_history") or []
                if isinstance(item, dict)
            ],
        }

    # ===========================================================
    # API：HEAD-bound Canon 事实视图。Dashboard 不读取 legacy index.db。
    # ===========================================================

    @app.get("/api/canon-v3/entities")
    @app.get("/api/entities", include_in_schema=False)
    def list_entities(
        entity_type: Optional[str] = Query(None, alias="type"),
        include_archived: bool = False,
    ):
        """列出 exact HEAD 中的实体（可按类型过滤）。"""
        view = _canon_dashboard_view()
        rows = _dashboard_entities(view)
        if entity_type:
            rows = [row for row in rows if row.get("type") == entity_type]
        if not include_archived:
            rows = [row for row in rows if not row.get("is_archived")]
        return _bound_items(view, rows)

    @app.get("/api/entities/{entity_id}")
    def get_entity(entity_id: str):
        view = _canon_dashboard_view()
        row = next(
            (item for item in _dashboard_entities(view) if item["id"] == entity_id),
            None,
        )
        if row is None:
            raise HTTPException(404, "实体不存在")
        return {
            "binding": dict(view["binding"]),
            "source": "canon_v3_head",
            "item": row,
        }

    @app.get("/api/canon-v3/relationships")
    @app.get("/api/relationships", include_in_schema=False)
    def list_relationships(entity: Optional[str] = None, limit: int = 200):
        view = _canon_dashboard_view()
        lookup = _entity_id_lookup(view)
        rows = [
            normalized
            for raw in view["snapshot"].get("canonical_facts") or []
            if isinstance(raw, dict)
            and (normalized := _relationship_row(raw, lookup)) is not None
        ]
        if entity:
            rows = [
                row
                for row in rows
                if entity in {row["from_entity"], row["to_entity"]}
            ]
        rows.sort(key=lambda row: (-row["chapter"], row["id"]))
        return _bound_items(view, rows[: max(0, int(limit))])

    @app.get("/api/canon-v3/relationship-events")
    @app.get("/api/relationship-events", include_in_schema=False)
    def list_relationship_events(
        entity: Optional[str] = None,
        from_chapter: Optional[int] = None,
        to_chapter: Optional[int] = None,
        limit: int = 200,
    ):
        view = _canon_dashboard_view()
        lookup = _entity_id_lookup(view)
        rows = [
            normalized
            for raw in view["projection"].get("history") or []
            if isinstance(raw, dict)
            and (normalized := _relationship_row(raw, lookup)) is not None
        ]
        if entity:
            rows = [
                row
                for row in rows
                if entity in {row["from_entity"], row["to_entity"]}
            ]
        if from_chapter is not None:
            rows = [row for row in rows if row["chapter"] >= from_chapter]
        if to_chapter is not None:
            rows = [row for row in rows if row["chapter"] <= to_chapter]
        rows.sort(key=lambda row: (-row["chapter"], row["id"]))
        return _bound_items(view, rows[: max(0, int(limit))])

    @app.get("/api/chapters")
    def list_chapters():
        view = _canon_dashboard_view()
        rows = [
            {
                "chapter": int(item.get("chapter") or 0),
                "revision": int(item.get("revision") or 0),
                "commit_hash": str(item.get("commit_hash") or ""),
                "transaction_hash": str(item.get("transaction_hash") or ""),
                "characters": [],
                "title": "",
                "summary": "",
            }
            for item in view["projection"].get("chapters") or []
            if isinstance(item, dict)
        ]
        rows.sort(key=lambda row: row["chapter"])
        return _bound_items(view, rows)

    @app.get("/api/scenes")
    def list_scenes(chapter: Optional[int] = None, limit: int = 500):
        view = _canon_dashboard_view()
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(view["projection"].get("history") or [], start=1):
            if not isinstance(raw, dict):
                continue
            claim = raw.get("claim") if isinstance(raw.get("claim"), dict) else {}
            if claim.get("kind") != "presence_observed":
                continue
            source_chapter = int(raw.get("chapter") or 0)
            if chapter is not None and source_chapter != int(chapter):
                continue
            rows.append(
                {
                    "id": str(raw.get("fact_digest") or raw.get("effect_id") or ""),
                    "chapter": source_chapter,
                    "scene_index": int(claim.get("scene_index") or index),
                    "entity_id": str(claim.get("subject") or ""),
                    "location": str(claim.get("location") or ""),
                    "presence": str(claim.get("presence") or ""),
                }
            )
        rows.sort(key=lambda row: (row["chapter"], row["scene_index"], row["id"]))
        return _bound_items(view, rows[: max(0, int(limit))])

    @app.get("/api/reading-power")
    def list_reading_power(limit: int = 50):
        _legacy_dashboard_endpoint_retired("/api/reading-power")

    @app.get("/api/review-metrics")
    def list_review_metrics(limit: int = 20):
        _legacy_dashboard_endpoint_retired("/api/review-metrics")

    @app.get("/api/stats/chapter-trend")
    def chapter_trend(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
        _legacy_dashboard_endpoint_retired(
            "/api/stats/chapter-trend",
            replacement="/api/canon-v3/history",
        )

    @app.get("/api/commits")
    def list_commits(limit: int = Query(20, ge=1, le=200)):
        view = _canon_dashboard_view()
        items = [
            {
                "chapter": int(item.get("chapter") or 0),
                "revision": int(item.get("revision") or 0),
                "commit_hash": str(item.get("commit_hash") or ""),
                "transaction_hash": str(item.get("transaction_hash") or ""),
                "status": "accepted",
                "projection_status": "fresh",
                "source": "canon_v3_head",
            }
            for item in view["projection"].get("chapters") or []
            if isinstance(item, dict)
        ]
        items.sort(key=lambda item: item["chapter"], reverse=True)
        return {
            "binding": dict(view["binding"]),
            "source": "canon_v3_head",
            "items": items[:limit],
            "total": len(items),
            "limit": limit,
        }

    @app.get("/api/contracts/summary")
    def contracts_summary():
        _legacy_dashboard_endpoint_retired(
            "/api/contracts/summary",
            replacement="/api/canon-v3/workflow",
        )

    @app.get("/api/env-status")
    def env_status():
        return _build_env_status(_get_project_root())

    @app.get("/api/env-status/probe")
    def env_status_probe():
        from data_modules.workflow_authority import WorkflowAuthority

        status = _build_env_status(_get_project_root())
        workflow = WorkflowAuthority(_get_project_root()).snapshot()
        retrieval = status["retrieval"]
        checks = [
            {
                "name": "remote_embedding",
                "ok": True,
                "detail": (
                    "已显式开启；active Canon 检索文本可能发送到远程服务"
                    if status["embed"]["remote_enabled"]
                    else (
                        "已选择远程语义召回但未配置 key，使用本地 BM25"
                        if status["embed"]["remote_opt_in"]
                        else "未显式开启远程发送，使用本地 BM25"
                    )
                ),
            },
            {
                "name": "canon_v3_retrieval",
                "ok": not bool(retrieval.get("writing_blocked")),
                "detail": (
                    f"state={retrieval.get('state')} mode={retrieval.get('mode')} "
                    f"facts={retrieval.get('fact_count', 0)} "
                    f"embedded={retrieval.get('embedded_count', 0)}; optional"
                ),
            },
            {
                "name": "canon_v3_workflow",
                "ok": str(workflow.get("state") or "") == "ready",
                "detail": (
                    f"state={workflow.get('state')} "
                    f"action={(workflow.get('primary_action') or {}).get('code')}"
                ),
            },
        ]
        return {
            "ok": all(bool(item["ok"]) for item in checks),
            "rag_mode": status["rag_mode"],
            "checks": checks,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/canon-v3/state-changes")
    @app.get("/api/state-changes", include_in_schema=False)
    def list_state_changes(entity: Optional[str] = None, limit: int = 100):
        view = _canon_dashboard_view()
        rows = _dashboard_state_changes(view, entity=entity)
        return _bound_items(view, rows[: max(0, int(limit))])

    @app.get("/api/aliases")
    def list_aliases(entity: Optional[str] = None):
        view = _canon_dashboard_view()
        rows = [
            {"entity_id": row["id"], "alias": alias}
            for row in _dashboard_entities(view)
            for alias in row.get("aliases") or []
            if not entity or row["id"] == entity
        ]
        rows.sort(key=lambda row: (row["entity_id"], row["alias"]))
        return _bound_items(view, rows)

    # ===========================================================
    # API：退役的 legacy index 分析端点。保留 410 仅用于兼容发现。
    # ===========================================================

    @app.get("/api/overrides")
    def list_overrides(status: Optional[str] = None, limit: int = 100):
        _legacy_dashboard_endpoint_retired("/api/overrides")

    @app.get("/api/debts")
    def list_debts(status: Optional[str] = None, limit: int = 100):
        _legacy_dashboard_endpoint_retired("/api/debts")

    @app.get("/api/debt-events")
    def list_debt_events(debt_id: Optional[int] = None, limit: int = 200):
        _legacy_dashboard_endpoint_retired("/api/debt-events")

    @app.get("/api/invalid-facts")
    def list_invalid_facts(status: Optional[str] = None, limit: int = 100):
        _legacy_dashboard_endpoint_retired("/api/invalid-facts")

    @app.get("/api/rag-queries")
    def list_rag_queries(query_type: Optional[str] = None, limit: int = 100):
        _legacy_dashboard_endpoint_retired("/api/rag-queries")

    @app.get("/api/tool-stats")
    def list_tool_stats(tool_name: Optional[str] = None, limit: int = 200):
        _legacy_dashboard_endpoint_retired("/api/tool-stats")

    @app.get("/api/checklist-scores")
    def list_checklist_scores(limit: int = 100):
        _legacy_dashboard_endpoint_retired("/api/checklist-scores")

    @app.get("/api/story-events")
    def list_story_events(chapter: Optional[int] = None, limit: int = 200):
        _legacy_dashboard_endpoint_retired(
            "/api/story-events",
            replacement="/api/canon-v3/history",
        )

    @app.get("/api/story-events/health")
    def story_event_health():
        _legacy_dashboard_endpoint_retired(
            "/api/story-events/health",
            replacement="/api/canon-v3/workflow",
        )

    # ===========================================================
    # API：文档浏览（正文/大纲/设定集 —— 只读）
    # ===========================================================

    @app.get("/api/files/tree")
    def file_tree():
        """列出 正文/、大纲/、设定集/ 三个目录的树结构。"""
        root = _get_project_root()
        result = {}
        for folder_name in ("正文", "大纲", "设定集"):
            folder = root / folder_name
            if not folder.is_dir():
                result[folder_name] = []
                continue
            result[folder_name] = _walk_tree(folder, root)
        return result

    @app.get("/api/files/read")
    def file_read(path: str):
        """只读读取一个文件内容（限 正文/大纲/设定集 目录）。"""
        root = _get_project_root()
        resolved = safe_resolve(root, path)

        # 二次限制：只允许三大目录
        allowed_parents = [root / n for n in ("正文", "大纲", "设定集")]
        if not any(_is_child(resolved, p) for p in allowed_parents):
            raise HTTPException(403, "仅允许读取 正文/大纲/设定集 目录下的文件")

        if not resolved.is_file():
            raise HTTPException(404, "文件不存在")

        max_bytes = 2 * 1024 * 1024
        if resolved.stat().st_size > max_bytes:
            raise HTTPException(413, "文件过大，无法预览")

        # 文本文件直接读；其他情况返回占位信息
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = "[二进制文件，无法预览]"

        return {"path": path, "content": content}

    # ===========================================================
    # SSE：实时变更推送
    # ===========================================================

    @app.get("/api/events")
    async def sse():
        """Server-Sent Events 端点，推送 .canon-ledger/.story-system 的文件变更。"""
        q = _watcher.subscribe()

        async def _gen():
            try:
                while True:
                    msg = await q.get()
                    yield f"data: {msg}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                _watcher.unsubscribe(q)

        return StreamingResponse(_gen(), media_type="text/event-stream")

    # ===========================================================
    # 前端静态文件托管
    # ===========================================================

    if STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

        @app.get("/{full_path:path}")
        def serve_spa(full_path: str):
            """SPA fallback：任何非 /api 路径都返回 index.html。"""
            if full_path.startswith("api/"):
                raise HTTPException(404, "API 路径不存在")
            index = STATIC_DIR / "index.html"
            if index.is_file():
                return FileResponse(str(index))
            raise HTTPException(404, "前端尚未构建")
    else:
        @app.get("/")
        def no_frontend():
            return HTMLResponse(
                "<h2>CanonLedger Dashboard API is running</h2>"
                "<p>前端尚未构建。请先在 <code>dashboard/frontend</code> 目录执行 <code>npm run build</code>。</p>"
                '<p>API 文档：<a href="/docs">/docs</a></p>'
            )

    return app


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _walk_tree(folder: Path, root: Path) -> list[dict]:
    items = []
    for child in sorted(folder.iterdir()):
        rel = str(child.relative_to(root)).replace("\\", "/")
        if child.is_dir():
            items.append({"name": child.name, "type": "dir", "path": rel, "children": _walk_tree(child, root)})
        else:
            items.append({"name": child.name, "type": "file", "path": rel, "size": child.stat().st_size})
    return items


def _is_child(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False

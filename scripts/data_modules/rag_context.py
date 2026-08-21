#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared, fact-only RAG context for the writing entry points.

The helper deliberately owns the query shape and result envelope used by both
``memory-contract load-context`` and ``extract-context``.  Retrieval is an
optional evidence source: it may surface already-written story facts, but it
does not produce genre, prose-style, pacing, or plot prescriptions.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import sqlite3
import struct
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .commit_artifacts import retrieval_source_marker
from .chapter_content_binding import verify_commit_content_binding
from .consistency_context import sanitize_chapter_directive_text
from .config import DataModulesConfig
from .fact_text import sanitize_fact_text


def _normalize_outline_text(outline: str) -> str:
    text = str(outline or "")
    if not text or text.startswith("⚠️"):
        return ""
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def chapter_goal_from_contract(chapter_contract: Mapping[str, Any] | None) -> str:
    """Read the authoritative chapter goal from either runtime contract shape.

    ``load-context`` receives the raw ``CHAPTER_BRIEF`` in
    ``runtime_sources.contracts['chapter']``.  ``extract-context`` receives a
    wrapper whose brief lives below ``story_contract.chapter_brief``.  Keeping
    that compatibility here prevents the two writing entry points from
    drifting to different goal keys.
    """
    if not isinstance(chapter_contract, Mapping):
        return ""

    contract: Mapping[str, Any] = chapter_contract
    story_contract = contract.get("story_contract")
    if isinstance(story_contract, Mapping):
        contract = story_contract
    chapter_brief = contract.get("chapter_brief")
    if isinstance(chapter_brief, Mapping):
        contract = chapter_brief

    directive = contract.get("chapter_directive")
    override_allowed = contract.get("override_allowed")
    directive = directive if isinstance(directive, Mapping) else {}
    override_allowed = override_allowed if isinstance(override_allowed, Mapping) else {}
    raw_goal = _normalize_outline_text(
        str(directive.get("goal") or override_allowed.get("chapter_focus") or "")
    )
    return sanitize_chapter_directive_text(raw_goal)


def build_rag_query(
    outline: str,
    *,
    chapter: int,
    max_chars: int,
    chapter_goal: str = "",
) -> str:
    """Build a neutral fact-retrieval query for a target chapter.

    Every usable outline is queryable.  We intentionally do not route through
    a genre-specific keyword list: a quiet scene can still depend on a prior
    location, relationship, promise, or timeline fact.  The authoritative
    chapter-directive goal leads the query; the outline supplements it.
    """
    target_chapter = max(1, int(chapter or 0))
    limit = max(40, int(max_chars or 0))
    goal = _normalize_outline_text(chapter_goal)
    plain = _normalize_outline_text(outline)
    if not goal and not plain:
        return ""
    evidence_text = goal
    if plain and plain != goal:
        evidence_text = f"{evidence_text} {plain}".strip()
    # ``context_rag_assist_max_query_chars`` bounds the actionable text, not
    # just the outline suffix; a user-authored directive can be arbitrarily
    # long and must not turn one context load into an unbounded API request.
    # Chapter is already enforced as a database upper bound.  Keeping generic
    # tokens such as ``第``/``章`` in a BM25 query would match almost every
    # event chunk and could surface unrelated facts when the real terms miss.
    return evidence_text[:limit].strip()


def empty_rag_assist(*, enabled: bool, reason: str = "") -> dict[str, Any]:
    """Return the stable RAG section shape, including graceful degradation."""
    return {
        "enabled": bool(enabled),
        "invoked": False,
        "reason": reason,
        "query": "",
        "mode": "",
        "degraded": False,
        "chapter_limit": 0,
        "intent": "",
        "needs_graph": False,
        "center_entities": [],
        "hits": [],
    }


def _retrieval_index_state(config: DataModulesConfig) -> str:
    """Inspect the retrieval store without creating or migrating it."""
    db_path = Path(config.vector_db)
    if not db_path.is_file():
        return "index_empty"
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {"vectors", "bm25_index", "doc_stats"}.issubset(tables):
                return "index_unavailable"
            required_vector_columns = {
                "chunk_id",
                "chapter",
                "scene_index",
                "content",
                "embedding",
                "parent_chunk_id",
                "chunk_type",
                "source_file",
                "created_at",
            }
            vector_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(vectors)").fetchall()
            }
            if not required_vector_columns.issubset(vector_columns):
                return "index_unavailable"
            row = conn.execute("SELECT COUNT(*) FROM doc_stats").fetchone()
            return "ready" if row and int(row[0] or 0) > 0 else "index_empty"
    except (OSError, sqlite3.Error):
        return "index_unavailable"


def _tokenize_bm25(text: str) -> list[str]:
    chinese = re.findall(r"[\u4e00-\u9fff]", str(text or ""))
    english = re.findall(r"[a-zA-Z0-9]+", str(text or "").lower())
    return chinese + english


def _bm25_search_read_only(
    config: DataModulesConfig,
    *,
    query: str,
    top_k: int | None,
    chapter: int,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[dict[str, Any]]:
    """Keyword retrieval without constructing writers or telemetry stores."""
    query_terms = set(_tokenize_bm25(query))
    if not query_terms:
        return []
    uri = f"{Path(config.vector_db).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        total_row = conn.execute(
            "SELECT COUNT(*), AVG(doc_length) FROM doc_stats"
        ).fetchone()
        total_docs = int((total_row or (0, 0))[0] or 1)
        avg_length = float((total_row or (0, 0))[1] or 1.0)
        scores: dict[str, float] = {}
        for term in query_terms:
            matches = conn.execute(
                """
                SELECT b.chunk_id, b.tf, d.doc_length
                FROM bm25_index b
                JOIN doc_stats d ON b.chunk_id = d.chunk_id
                WHERE b.term = ?
                """,
                (term,),
            ).fetchall()
            if not matches:
                continue
            idf = math.log((total_docs - len(matches) + 0.5) / (len(matches) + 0.5) + 1)
            for chunk_id, tf, doc_length in matches:
                score = idf * (tf * (k1 + 1)) / (
                    tf + k1 * (1 - b + b * doc_length / avg_length)
                )
                scores[str(chunk_id)] = scores.get(str(chunk_id), 0.0) + score
        rows: list[dict[str, Any]] = []
        for chunk_id, score in scores.items():
            row = conn.execute(
                """
                SELECT chunk_id, chapter, scene_index, content, source_file
                FROM vectors
                WHERE chunk_id = ? AND chapter <= ?
                """,
                (chunk_id, int(chapter)),
            ).fetchone()
            if row:
                rows.append(
                    {
                        "chunk_id": row[0],
                        "chapter": row[1],
                        "scene_index": row[2],
                        "content": row[3],
                        "source_file": row[4],
                        "score": score,
                        "source": "bm25",
                    }
                )
    rows.sort(key=lambda item: float(item["score"]), reverse=True)
    return rows if top_k is None else rows[: max(1, int(top_k))]


def _vector_search_read_only(
    config: DataModulesConfig,
    *,
    query_embedding: list[float],
    top_k: int | None,
    chapter: int,
) -> list[dict[str, Any]]:
    uri = f"{Path(config.vector_db).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        source_rows = conn.execute(
            """
            SELECT chunk_id, chapter, scene_index, content, embedding, source_file
            FROM vectors
            WHERE chapter <= ? AND embedding IS NOT NULL AND length(embedding) > 0
            """,
            (int(chapter),),
        ).fetchall()
    rows: list[dict[str, Any]] = []
    for chunk_id, source_chapter, scene_index, content, raw_embedding, source_file in source_rows:
        try:
            packed = bytes(raw_embedding)
            if not packed or len(packed) % 4:
                continue
            embedding = list(struct.unpack(f"{len(packed) // 4}f", packed))
            if len(embedding) != len(query_embedding):
                continue
            dot = sum(left * right for left, right in zip(query_embedding, embedding))
            norm_query = math.sqrt(sum(value * value for value in query_embedding))
            norm_row = math.sqrt(sum(value * value for value in embedding))
            score = dot / (norm_query * norm_row) if norm_query and norm_row else 0.0
        except (TypeError, ValueError, OverflowError):
            continue
        rows.append(
            {
                "chunk_id": chunk_id,
                "chapter": source_chapter,
                "scene_index": scene_index,
                "content": content,
                "source_file": source_file,
                "score": score,
                "source": "vector",
            }
        )
    rows.sort(key=lambda item: float(item["score"]), reverse=True)
    return rows if top_k is None else rows[: max(1, int(top_k))]


def _rrf_merge(
    vector_rows: list[dict[str, Any]],
    bm25_rows: list[dict[str, Any]],
    *,
    top_k: int | None,
    rrf_k: int,
) -> list[dict[str, Any]]:
    ranked: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    for rows in (vector_rows, bm25_rows):
        for rank, row in enumerate(rows, start=1):
            chunk_id = str(row.get("chunk_id") or "")
            if not chunk_id:
                continue
            ranked.setdefault(chunk_id, dict(row))
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    merged: list[dict[str, Any]] = []
    for chunk_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        row = dict(ranked[chunk_id])
        row["score"] = score
        row["source"] = "hybrid"
        merged.append(row)
    return merged if top_k is None else merged[: max(1, int(top_k))]


def _semantic_search_read_only(
    config: DataModulesConfig,
    *,
    query: str,
    top_k: int,
    chapter: int,
) -> tuple[list[dict[str, Any]], str]:
    """Remote query embedding + local read-only vector/BM25 retrieval."""
    # Reuse the same injectable API-client boundary as projection storage,
    # without constructing ``RAGAdapter`` (which owns writable databases).
    from .rag_adapter import get_client

    client = get_client(config)
    try:
        embeddings = _await_if_needed(client.embed([query]))
        if not embeddings or not embeddings[0]:
            embed_client = getattr(client, "_embed_client", client)
            reason = str(
                getattr(embed_client, "last_error_message", "")
                or "embedding_unavailable"
            )
            return (
                _bm25_search_read_only(
                    config,
                    query=query,
                    top_k=None,
                    chapter=chapter,
                ),
                reason,
            )
        vector_rows = _vector_search_read_only(
            config,
            query_embedding=list(embeddings[0]),
            top_k=None,
            chapter=chapter,
        )
        bm25_rows = _bm25_search_read_only(
            config,
            query=query,
            top_k=None,
            chapter=chapter,
        )
        if not vector_rows:
            return bm25_rows, "no_embedded_vectors"
        merged = _rrf_merge(
            vector_rows,
            bm25_rows,
            top_k=None,
            rrf_k=max(1, int(config.rrf_k)),
        )
        return merged, ""
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            _await_if_needed(closer())


def _rerank_read_only(
    config: DataModulesConfig,
    *,
    query: str,
    rows: list[Any],
    top_k: int,
) -> list[Any]:
    """Optionally rerank only candidates already proven trustworthy."""
    candidates = list(rows)[: max(top_k * 4, int(config.rerank_top_n) * 2)]
    if not candidates:
        return []
    from .rag_adapter import get_client

    client = get_client(config)
    try:
        reranker = getattr(client, "rerank", None)
        if not callable(reranker):
            return candidates[:top_k]
        reranked = _await_if_needed(
            reranker(
                query,
                [str(_value(row, "content", "") or "") for row in candidates],
                top_n=top_k,
            )
        )
        if not reranked:
            return candidates[:top_k]
        selected: list[Any] = []
        for item in reranked:
            if not isinstance(item, Mapping):
                continue
            index = _safe_int(item.get("index"))
            if index < 0 or index >= len(candidates):
                continue
            row = candidates[index]
            if isinstance(row, Mapping):
                row = dict(row)
                row["score"] = _safe_float(
                    item.get("relevance_score", row.get("score", 0.0))
                )
            selected.append(row)
        return (selected or candidates)[:top_k]
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            _await_if_needed(closer())


def _trusted_commit_chapters(
    project_root: Path,
    rows: Any,
    *,
    chapter_limit: int,
) -> tuple[list[Any], int]:
    """Keep only rows backed by the current accepted, projected commit."""
    trusted: list[Any] = []
    filtered = 0
    # chapter -> (current marker, exact structured fact chunks).  Matching the
    # marker alone is insufficient: a manually inserted summary/scene row can
    # copy that marker.  Re-deriving the expected chunks from the accepted
    # commit makes the structured projection the allowlist.
    cache: dict[int, tuple[str, dict[str, str]]] = {}
    v3_cutover: int | None = None
    if (project_root / ".story-system" / "v3" / "CURRENT").is_file():
        try:
            from .canon_v3.service import CanonV3Service

            workflow = CanonV3Service(project_root).workflow_snapshot()
            raw_cutover = workflow.get("cutover_chapter")
            v3_cutover = int(raw_cutover) if raw_cutover is not None else -1
        except Exception:
            # When v3 authority cannot be read, no legacy retrieval row is
            # safe enough to inject into a writing context.
            v3_cutover = -1
    for row in rows or []:
        source_chapter = _safe_int(_value(row, "chapter", 0))
        if source_chapter not in cache:
            allowed = 0 < source_chapter <= int(chapter_limit)
            if v3_cutover is not None:
                # K and earlier belong to the immutable imported prefix. Rows
                # after K come from a superseded v2 suffix; v3 RAG projection
                # is not implemented yet, so fail closed for those chapters.
                allowed = allowed and source_chapter <= v3_cutover
            expected_chunks: dict[str, str] = {}
            commit_path = (
                project_root
                / ".story-system"
                / "commits"
                / f"chapter_{source_chapter:03d}.commit.json"
            )
            if allowed:
                try:
                    commit = json.loads(commit_path.read_text(encoding="utf-8"))
                    meta = commit.get("meta") if isinstance(commit, dict) else {}
                    statuses = commit.get("projection_status") if isinstance(commit, dict) else {}
                    meta = meta if isinstance(meta, Mapping) else {}
                    statuses = statuses if isinstance(statuses, Mapping) else {}
                    vector_status = str(statuses.get("vector") or "")
                    allowed = (
                        _safe_int(meta.get("chapter")) == source_chapter
                        and str(meta.get("status") or "") == "accepted"
                        and vector_status in {"done", "skipped"}
                    )
                    if allowed:
                        allowed, _binding_code = verify_commit_content_binding(
                            project_root,
                            source_chapter,
                            commit,
                        )
                    if allowed:
                        from .vector_projection_writer import VectorProjectionWriter

                        expected_chunks = {
                            str(chunk.get("chunk_id") or ""): str(
                                chunk.get("content") or ""
                            )
                            for chunk in VectorProjectionWriter(
                                project_root
                            )._collect_chunks(commit)
                        }
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    allowed = False
            cache[source_chapter] = (
                retrieval_source_marker(commit) if allowed else "",
                expected_chunks if allowed else {},
            )
        expected_marker, expected_chunks = cache[source_chapter]
        source_file = str(_value(row, "source_file", "") or "").strip()
        chunk_id = str(_value(row, "chunk_id", "") or "").strip()
        content = str(_value(row, "content", "") or "")
        if (
            expected_marker
            and source_file == expected_marker
            and chunk_id in expected_chunks
            and content == expected_chunks[chunk_id]
        ):
            trusted.append(row)
        else:
            filtered += 1
    return trusted, filtered


def _value(row: Any, name: str, default: Any = "") -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _serialize_hits(rows: Any, *, mode: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for row in rows or []:
        content = sanitize_fact_text(_value(row, "content", ""), max_chars=180)
        if not content:
            continue
        hits.append(
            {
                "chunk_id": str(_value(row, "chunk_id", "") or ""),
                "chapter": _safe_int(_value(row, "chapter", 0)),
                "scene_index": _safe_int(_value(row, "scene_index", 0)),
                "score": round(_safe_float(_value(row, "score", 0.0)), 6),
                "source": str(_value(row, "source", "") or mode),
                "source_file": str(_value(row, "source_file", "") or ""),
                "content": content[:180],
            }
        )
    return hits


_BM25_ONLY_SOURCES = {
    "bm25",
    "bm25_only",
    "bm25_fallback",
    "hybrid_bm25_fallback",
}


def _is_bm25_only(rows: Any) -> bool:
    """Whether the adapter's returned rows prove a local-only fallback.

    The adapter can fall back internally when an embedding call returns no
    vector, so no exception reaches this helper.  Its degradation property is
    the primary signal; all-BM25 result sources are the backward-compatible
    fallback for adapters that do not expose that property.
    """
    sources = [str(_value(row, "source", "") or "").strip().lower() for row in rows or []]
    return bool(sources) and all(source in _BM25_ONLY_SOURCES for source in sources)


def _await_if_needed(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)

    # The writing-context API is synchronous but can be called by an async
    # host.  ``asyncio.run`` is illegal on that host loop, so run this one
    # coroutine in a short-lived worker thread (the same bridge used by the
    # vector projection writer) and preserve its exception semantics.
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(value)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def load_rag_assist(
    project_root: str | Path,
    *,
    chapter: int,
    outline: str = "",
    chapter_goal: str = "",
    config: DataModulesConfig | None = None,
) -> dict[str, Any]:
    """Retrieve prior-story evidence for a writing context.

    No embedding key is treated as a supported BM25 mode, not as a reason to
    skip retrieval. SQLite reads stay read-only; only the optional remote
    embedding/rerank clients are invoked here.
    """
    root = Path(project_root)
    cfg = config or DataModulesConfig.from_project_root(root)
    enabled = bool(getattr(cfg, "context_rag_assist_enabled", True))
    payload = empty_rag_assist(enabled=enabled)
    if not enabled:
        payload["reason"] = "disabled_by_config"
        return payload

    target_chapter = max(1, _safe_int(chapter))
    chapter_limit = max(0, target_chapter - 1)
    top_k = max(1, _safe_int(getattr(cfg, "context_rag_assist_top_k", 4)) or 4)
    max_chars = max(
        40,
        _safe_int(getattr(cfg, "context_rag_assist_max_query_chars", 120)) or 120,
    )
    query = build_rag_query(
        outline,
        chapter=target_chapter,
        max_chars=max_chars,
        chapter_goal=chapter_goal,
    )
    payload["query"] = query
    payload["chapter_limit"] = chapter_limit

    # Avoid retrieving arbitrary facts from the generic chapter-number
    # tokens when neither the chapter directive nor the outline supplies a
    # factual anchor.  This is a benign lack of context, not a write blocker.
    if not query:
        payload["reason"] = "no_query_text"
        return payload

    # Chapter 1 has no accepted prior chapter.  Avoid opening/creating a
    # vector store and, more importantly, avoid accidentally retrieving a
    # stale same-chapter projection.
    has_embed_key = bool(str(getattr(cfg, "embed_api_key", "") or "").strip())
    if chapter_limit == 0:
        payload.update(
            {
                "mode": "semantic" if has_embed_key else "bm25",
                "reason": "index_empty",
            }
        )
        return payload

    index_state = _retrieval_index_state(cfg)
    if index_state != "ready":
        payload.update(
            {
                "mode": "semantic" if has_embed_key else "bm25",
                "reason": index_state,
            }
        )
        return payload

    try:
        intent_payload: Mapping[str, Any] = {}
        from .query_router import QueryRouter

        router = QueryRouter()
        if router is not None and hasattr(router, "route_intent"):
            routed = router.route_intent(query)
            if isinstance(routed, Mapping):
                intent_payload = routed
        center_entities = [
            str(entity)
            for entity in (intent_payload.get("entities") or [])
            if str(entity).strip()
        ]

        mode = "semantic"
        fallback_reason = ""
        if has_embed_key:
            try:
                payload["invoked"] = True
                rows, semantic_fallback = _semantic_search_read_only(
                    cfg,
                    query=query,
                    top_k=top_k,
                    chapter=chapter_limit,
                )
                if semantic_fallback:
                    mode = "bm25"
                    fallback_reason = semantic_fallback
            except Exception as exc:
                mode = "bm25"
                fallback_reason = f"auto_failed:{exc.__class__.__name__}"
                rows = _bm25_search_read_only(
                    cfg,
                    query=query,
                    top_k=None,
                    chapter=chapter_limit,
                )
        else:
            mode = "bm25"
            fallback_reason = "missing_embed_api_key"
            payload["invoked"] = True
            rows = _bm25_search_read_only(
                cfg,
                query=query,
                top_k=None,
                chapter=chapter_limit,
            )

        rows, filtered_count = _trusted_commit_chapters(
            root,
            rows,
            chapter_limit=chapter_limit,
        )
        if mode == "semantic" and _is_bm25_only(rows):
            mode = "bm25"
            fallback_reason = "embedding_unavailable_bm25"

        if mode == "semantic":
            try:
                rows = _rerank_read_only(
                    cfg,
                    query=query,
                    rows=list(rows),
                    top_k=top_k,
                )
            except Exception:
                rows = list(rows)[:top_k]
        else:
            rows = list(rows)[:top_k]

        hits = _serialize_hits(rows, mode=mode)
        payload.update(
            {
                "invoked": True,
                "mode": mode,
                "degraded": mode == "bm25",
                "reason": fallback_reason
                or ("ok" if hits else ("untrusted_hits_filtered" if filtered_count else "no_hit")),
                "intent": str(intent_payload.get("intent") or ""),
                "needs_graph": bool(intent_payload.get("needs_graph")),
                "center_entities": center_entities,
                "hits": hits,
            }
        )
    except Exception as exc:
        payload["reason"] = f"rag_error:{exc.__class__.__name__}"
        payload["degraded"] = True
    return payload

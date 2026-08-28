#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional HEAD-bound retrieval projection for Canon v3 active facts.

The retrieval database is disposable acceleration, never authority.  Every
stored row is derived from the sanitized public active-fact view and bound to
one exact Canon HEAD/generation/workflow/projection/fact set.  Search results
are resolved back against the same active snapshot before they are returned.

Missing embeddings, a missing/stale index, and remote API failures degrade to
read-only in-memory BM25.  They never make writing unavailable and never
authorize a fact that is absent from active Canon.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import sqlite3
import struct
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from security_utils import resolve_exact_project_role_path
except ImportError:  # pragma: no cover
    from scripts.security_utils import resolve_exact_project_role_path

from ..api_client import EmbeddingAPIClient
from ..config import DataModulesConfig
from ..workflow_authority import WorkflowAuthority
from .query import CanonQueryFacade, bound_workflow_unchanged
from .schema import canonical_digest


RETRIEVAL_PROJECTION_SCHEMA = "canon-v3/retrieval-projection/v1"
RETRIEVAL_SEARCH_REQUEST_SCHEMA = "canon-v3/retrieval-search-request/v1"
RETRIEVAL_SEARCH_RESULT_SCHEMA = "canon-v3/retrieval-search-result/v1"
RETRIEVAL_STATUS_SCHEMA = "canon-v3/retrieval-status/v1"
RETRIEVAL_RELATIVE_PATH = Path(
    ".story-system/v3/projections/retrieval.sqlite3"
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_METADATA_KEYS = frozenset(
    {
        "schema_version",
        "head_hash",
        "generation",
        "workflow_digest",
        "author_axiom_digest",
        "projection_digest",
        "as_of_chapter",
        "active_fact_set_digest",
        "fact_count",
        "embedded_count",
        "embedding_model",
        "mode",
        "degraded_reason",
    }
)


class CanonRetrievalError(RuntimeError):
    """Base error for the disposable Canon v3 retrieval projection."""


class CanonRetrievalInvalid(CanonRetrievalError):
    """The on-disk retrieval projection is malformed or internally corrupt."""


class CanonRetrievalChanged(CanonRetrievalError):
    """Canon authority changed while a build or search was running."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RetrievalSearchRequest(_StrictModel):
    schema_version: Literal[RETRIEVAL_SEARCH_REQUEST_SCHEMA] = (
        RETRIEVAL_SEARCH_REQUEST_SCHEMA
    )
    query: str = Field(min_length=1, max_length=2000)
    as_of_chapter: int | None = Field(default=None, ge=0)
    top_k: int = Field(default=8, ge=1, le=50)
    mode: Literal["auto", "vector", "bm25"] = "auto"
    categories: list[str] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("retrieval query cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_categories(self) -> "RetrievalSearchRequest":
        normalized = sorted(
            {
                str(item).strip()
                for item in self.categories
                if str(item).strip()
            }
        )
        if normalized != self.categories:
            raise ValueError(
                "retrieval categories must be sorted, unique, and non-empty"
            )
        return self


def retrieval_path(project_root: str | Path) -> Path:
    return (
        Path(project_root).expanduser().resolve()
        / RETRIEVAL_RELATIVE_PATH
    )


def _fact_key(row: Mapping[str, Any]) -> str:
    origin = str(row.get("origin") or "canon").strip()
    fact_digest = str(row.get("fact_digest") or "").strip().lower()
    if not _HASH_RE.fullmatch(fact_digest):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_fact_digest_invalid"
        )
    return f"{origin}:{fact_digest}"


def _canonical_fact(row: Mapping[str, Any]) -> dict[str, Any]:
    if (
        row.get("authority_layer") != "active_canon"
        or row.get("authority_state") != "active"
    ):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_non_active_fact_forbidden"
        )
    copied = json.loads(
        json.dumps(
            dict(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    _fact_key(copied)
    return copied


def _active_facts(query_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = query_result.get("data")
    if not isinstance(data, Mapping):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_query_snapshot_invalid"
        )
    raw_rows = data.get("active_facts")
    if not isinstance(raw_rows, list):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_active_facts_missing"
        )
    facts = [_canonical_fact(row) for row in raw_rows if isinstance(row, Mapping)]
    if len(facts) != len(raw_rows):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_active_fact_shape_invalid"
        )
    facts.sort(key=_fact_key)
    keys = [_fact_key(row) for row in facts]
    if len(keys) != len(set(keys)):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_active_fact_key_duplicate"
        )
    return facts


def _fact_content_digest(row: Mapping[str, Any]) -> str:
    return canonical_digest(dict(row))


def _fact_set_digest(facts: Sequence[Mapping[str, Any]]) -> str:
    return canonical_digest(
        [
            {
                "retrieval_id": _fact_key(row),
                "fact_content_digest": _fact_content_digest(row),
            }
            for row in facts
        ]
    )


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return _compact_json(value)


def _fact_search_text(row: Mapping[str, Any]) -> str:
    """Render explicit factual semantics without evidence/style metadata."""

    category = str(row.get("category") or "story_fact").strip()
    subject = str(row.get("subject") or "").strip()
    field = str(row.get("field") or "").strip()
    value = _display_value(row.get("value"))
    payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}

    if category in {"knowledge", "knowledge_state_changed", "knowledge_information"}:
        proposition = _display_value(
            payload.get("proposition")
            or payload.get("information")
            or field
            or value
        )
        state = _display_value(
            payload.get("after")
            or payload.get("knowledge_state")
            or value
        )
        text = f"知识边界事实：{subject}；命题：{proposition}；状态：{state}"
    else:
        payload_text = _display_value(payload)
        text = (
            f"事实类型：{category}；主体：{subject}；字段：{field}；"
            f"值：{value}；结构：{payload_text}"
        )
    return " ".join(text.split())[:4000]


def _tokenize(text: str) -> list[str]:
    chinese = re.findall(r"[\u4e00-\u9fff]", str(text or ""))
    english = re.findall(r"[a-zA-Z0-9_]+", str(text or "").lower())
    return chinese + english


def _bm25_rank(
    rows: Sequence[Mapping[str, Any]],
    query: str,
) -> list[tuple[str, float]]:
    query_terms = set(_tokenize(query))
    if not query_terms or not rows:
        return []
    documents: dict[str, list[str]] = {
        str(row["retrieval_id"]): _tokenize(str(row["search_text"]))
        for row in rows
    }
    lengths = [len(tokens) for tokens in documents.values()]
    average = sum(lengths) / len(lengths) if lengths else 1.0
    total = max(1, len(documents))
    counters = {
        key: Counter(tokens) for key, tokens in documents.items()
    }
    scores: dict[str, float] = {}
    k1 = 1.5
    b = 0.75
    for term in query_terms:
        matches = [key for key, counts in counters.items() if counts.get(term)]
        if not matches:
            continue
        idf = math.log((total - len(matches) + 0.5) / (len(matches) + 0.5) + 1)
        for key in matches:
            count = float(counters[key][term])
            length = float(len(documents[key]) or 1)
            score = idf * (count * (k1 + 1.0)) / (
                count + k1 * (1.0 - b + b * length / (average or 1.0))
            )
            scores[key] = scores.get(key, 0.0) + score
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _serialize_embedding(value: Sequence[Any]) -> bytes:
    if not value:
        raise ValueError("embedding cannot be empty")
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool):
            raise ValueError("embedding cannot contain booleans")
        number = float(item)
        if not math.isfinite(number):
            raise ValueError("embedding must contain finite numbers")
        numbers.append(number)
    return struct.pack(f"{len(numbers)}f", *numbers)


def _deserialize_embedding(value: bytes | bytearray | memoryview) -> list[float]:
    raw = bytes(value)
    if not raw or len(raw) % 4:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_embedding_blob_invalid"
        )
    return list(struct.unpack(f"{len(raw) // 4}f", raw))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding dimension mismatch")
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    if not norm_left or not norm_right:
        return 0.0
    return dot / (norm_left * norm_right)


def _vector_rank(
    rows: Sequence[Mapping[str, Any]],
    query_embedding: Sequence[float],
) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    for row in rows:
        raw = row.get("embedding")
        if raw is None:
            continue
        try:
            score = _cosine(query_embedding, _deserialize_embedding(raw))
        except (CanonRetrievalInvalid, TypeError, ValueError):
            continue
        ranked.append((str(row["retrieval_id"]), score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def _rrf_rank(
    vector_rows: Sequence[tuple[str, float]],
    bm25_rows: Sequence[tuple[str, float]],
    *,
    rrf_k: int,
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranked in (vector_rows, bm25_rows):
        for index, (key, _score) in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (
                max(1, int(rrf_k)) + index
            )
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _await(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(value)
        except Exception as exc:  # pragma: no cover - propagated below.
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _embed_documents(
    config: DataModulesConfig,
    texts: Sequence[str],
) -> tuple[list[list[float] | None], str]:
    if not texts:
        return [], ""
    if not config.retrieval_remote_enabled:
        return [None] * len(texts), "remote_embedding_disabled"
    if not config.embedding_enabled:
        return [None] * len(texts), "embedding_not_configured"

    async def run() -> tuple[list[list[float] | None], str]:
        async with EmbeddingAPIClient(config) as client:
            values = await client.embed_batch(list(texts))
            reason = str(client.last_error_message or "").strip()
            return list(values), reason

    try:
        values, reason = _await(run())
    except Exception as exc:
        return [None] * len(texts), f"embedding_error:{exc.__class__.__name__}"
    if len(values) < len(texts):
        values.extend([None] * (len(texts) - len(values)))
    return values[: len(texts)], reason


def _embed_query(
    config: DataModulesConfig,
    query: str,
) -> tuple[list[float] | None, str]:
    if not config.retrieval_remote_enabled:
        return None, "remote_embedding_disabled"
    if not config.embedding_enabled:
        return None, "embedding_not_configured"

    async def run() -> tuple[list[float] | None, str]:
        async with EmbeddingAPIClient(config) as client:
            values = await client.embed([query])
            value = list(values[0]) if values and values[0] else None
            return value, str(client.last_error_message or "").strip()

    try:
        return _await(run())
    except Exception as exc:
        return None, f"embedding_error:{exc.__class__.__name__}"


def _metadata_from_query(
    query_result: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    *,
    embedding_model: str,
    embedded_count: int,
    degraded_reason: str,
) -> dict[str, Any]:
    fact_count = len(facts)
    if embedded_count <= 0:
        mode = "bm25"
    elif embedded_count < fact_count:
        mode = "hybrid"
    else:
        mode = "vector"
    return {
        "schema_version": RETRIEVAL_PROJECTION_SCHEMA,
        "head_hash": str(query_result.get("head_hash") or ""),
        "generation": int(query_result.get("generation") or 0),
        "workflow_digest": str(query_result.get("workflow_digest") or ""),
        "author_axiom_digest": str(
            query_result.get("author_axiom_digest") or ""
        ),
        "projection_digest": str(query_result.get("projection_digest") or ""),
        "as_of_chapter": int(query_result.get("as_of_chapter") or 0),
        "active_fact_set_digest": _fact_set_digest(facts),
        "fact_count": fact_count,
        "embedded_count": max(0, int(embedded_count)),
        "embedding_model": embedding_model if embedded_count else "",
        "mode": mode,
        "degraded_reason": str(degraded_reason or ""),
    }


def _validate_metadata(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_metadata_not_mapping"
        )
    payload = dict(raw)
    if set(payload) != _REQUIRED_METADATA_KEYS:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_metadata_keys_invalid"
        )
    if payload.get("schema_version") != RETRIEVAL_PROJECTION_SCHEMA:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_schema_invalid"
        )
    for key in (
        "head_hash",
        "workflow_digest",
        "author_axiom_digest",
        "projection_digest",
        "active_fact_set_digest",
    ):
        if not _HASH_RE.fullmatch(str(payload.get(key) or "")):
            raise CanonRetrievalInvalid(
                f"canon_v3_retrieval_{key}_invalid"
            )
    for key in ("generation", "as_of_chapter", "fact_count", "embedded_count"):
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CanonRetrievalInvalid(
                f"canon_v3_retrieval_{key}_invalid"
            )
    if payload["embedded_count"] > payload["fact_count"]:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_embedded_count_invalid"
        )
    if payload.get("mode") not in {"bm25", "hybrid", "vector"}:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_mode_invalid"
        )
    return payload


def _database_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {"metadata", "facts"}.issubset(tables):
                raise CanonRetrievalInvalid(
                    "canon_v3_retrieval_tables_missing"
                )
            meta_row = conn.execute(
                "SELECT payload_json FROM metadata WHERE singleton = 1"
            ).fetchone()
            if not meta_row:
                raise CanonRetrievalInvalid(
                    "canon_v3_retrieval_metadata_missing"
                )
            metadata = _validate_metadata(json.loads(str(meta_row[0])))
            raw_rows = conn.execute(
                """
                SELECT retrieval_id, fact_digest, fact_content_digest,
                       origin, category, source_chapter, search_text,
                       fact_json, embedding
                FROM facts
                ORDER BY retrieval_id
                """
            ).fetchall()
    except json.JSONDecodeError as exc:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_json_invalid"
        ) from exc
    except sqlite3.Error as exc:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_sqlite_invalid"
        ) from exc

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        try:
            fact = _canonical_fact(json.loads(str(raw[7])))
        except json.JSONDecodeError as exc:
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_fact_json_invalid"
            ) from exc
        row = {
            "retrieval_id": str(raw[0]),
            "fact_digest": str(raw[1]),
            "fact_content_digest": str(raw[2]),
            "origin": str(raw[3]),
            "category": str(raw[4]),
            "source_chapter": int(raw[5] or 0),
            "search_text": str(raw[6]),
            "fact": fact,
            "embedding": raw[8],
        }
        if row["retrieval_id"] != _fact_key(fact):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_fact_key_mismatch"
            )
        if row["fact_digest"] != str(fact.get("fact_digest") or ""):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_fact_digest_mismatch"
            )
        if row["fact_content_digest"] != _fact_content_digest(fact):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_fact_content_mismatch"
            )
        if row["origin"] != str(fact.get("origin") or ""):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_origin_mismatch"
            )
        if row["category"] != str(fact.get("category") or ""):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_category_mismatch"
            )
        if row["source_chapter"] != int(fact.get("source_chapter") or 0):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_chapter_mismatch"
            )
        if row["search_text"] != _fact_search_text(fact):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_text_mismatch"
            )
        rows.append(row)
    if len(rows) != int(metadata["fact_count"]):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_fact_count_mismatch"
        )
    embedded_count = sum(1 for row in rows if row["embedding"] is not None)
    if embedded_count != int(metadata["embedded_count"]):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_embedding_count_mismatch"
        )
    if _fact_set_digest([row["fact"] for row in rows]) != metadata[
        "active_fact_set_digest"
    ]:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_fact_set_digest_mismatch"
        )
    dimensions: set[int] = set()
    for row in rows:
        if row["embedding"] is not None:
            dimensions.add(len(_deserialize_embedding(row["embedding"])))
    if len(dimensions) > 1:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_embedding_dimensions_mismatch"
        )
    expected_mode = (
        "bm25"
        if embedded_count == 0
        else "vector"
        if embedded_count == len(rows)
        else "hybrid"
    )
    if metadata["mode"] != expected_mode:
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_mode_count_mismatch"
        )
    if bool(str(metadata["embedding_model"] or "")) != bool(embedded_count):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_embedding_model_mismatch"
        )
    return metadata, rows


def _binding_matches(
    metadata: Mapping[str, Any],
    query_result: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
) -> bool:
    return bool(
        metadata.get("head_hash") == query_result.get("head_hash")
        and int(metadata.get("generation") or 0)
        == int(query_result.get("generation") or 0)
        and metadata.get("workflow_digest")
        == query_result.get("workflow_digest")
        and metadata.get("author_axiom_digest")
        == query_result.get("author_axiom_digest")
        and metadata.get("projection_digest")
        == query_result.get("projection_digest")
        and int(metadata.get("as_of_chapter") or 0)
        == int(query_result.get("as_of_chapter") or 0)
        and metadata.get("active_fact_set_digest")
        == _fact_set_digest(facts)
    )


def _rows_from_facts(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "retrieval_id": _fact_key(fact),
            "fact_digest": str(fact.get("fact_digest") or ""),
            "fact_content_digest": _fact_content_digest(fact),
            "origin": str(fact.get("origin") or ""),
            "category": str(fact.get("category") or ""),
            "source_chapter": int(fact.get("source_chapter") or 0),
            "search_text": _fact_search_text(fact),
            "fact": dict(fact),
            "embedding": None,
        }
        for fact in facts
    ]


def _current_query_snapshot(project_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = CanonQueryFacade(project_root).snapshot()
    return result, _active_facts(result)


def retrieval_status(project_root: str | Path) -> dict[str, Any]:
    """Inspect the v3 retrieval projection without creating any path."""

    root = Path(project_root).expanduser().resolve()
    authority = WorkflowAuthority(root)
    workflow = authority.snapshot()
    base = {
        "schema_version": RETRIEVAL_STATUS_SCHEMA,
        "authority": "canon_v3",
        "authority_layer": "retrieval_assist",
        "usable_as_canon": False,
        "writing_blocked": False,
        "head_hash": workflow.get("head_hash"),
        "generation": int(workflow.get("generation") or 0),
        "workflow_digest": workflow.get("workflow_digest"),
        "projection_fresh": bool(workflow.get("projection_fresh")),
        "path": RETRIEVAL_RELATIVE_PATH.as_posix(),
        "fallback_mode": "in_memory_bm25",
    }
    try:
        query_result, facts = _current_query_snapshot(root)
    except Exception as exc:
        return {
            **base,
            "state": "authority_unavailable",
            "mode": "bm25",
            "fact_count": 0,
            "embedded_count": 0,
            "degraded_reason": exc.__class__.__name__,
            "rebuild_command": "canon_ledger.py canon-v3 retrieval rebuild",
        }
    after_query = authority.snapshot()
    if (
        not bound_workflow_unchanged(workflow, after_query)
        or workflow.get("head_hash") != query_result.get("head_hash")
        or workflow.get("workflow_digest")
        != query_result.get("workflow_digest")
    ):
        return {
            **base,
            "state": "authority_changed",
            "mode": "bm25",
            "fact_count": len(facts),
            "embedded_count": 0,
            "degraded_reason": "authority_changed_during_status",
            "rebuild_command": "canon_ledger.py canon-v3 retrieval rebuild",
        }
    try:
        path = resolve_exact_project_role_path(
            root,
            retrieval_path(root),
            expected_relative=RETRIEVAL_RELATIVE_PATH,
        )
    except (OSError, ValueError) as exc:
        return {
            **base,
            "state": "invalid",
            "mode": "bm25",
            "fact_count": len(facts),
            "embedded_count": 0,
            "degraded_reason": f"unsafe_index_path:{exc.__class__.__name__}",
            "rebuild_command": "canon_ledger.py canon-v3 retrieval rebuild",
        }
    if not path.is_file() or path.is_symlink():
        return {
            **base,
            "state": "missing",
            "mode": "bm25",
            "fact_count": len(facts),
            "embedded_count": 0,
            "degraded_reason": "index_missing",
            "rebuild_command": "canon_ledger.py canon-v3 retrieval rebuild",
        }
    try:
        metadata, _rows = _database_rows(path)
    except Exception as exc:
        return {
            **base,
            "state": "invalid",
            "mode": "bm25",
            "fact_count": len(facts),
            "embedded_count": 0,
            "degraded_reason": str(exc),
            "rebuild_command": "canon_ledger.py canon-v3 retrieval rebuild",
        }
    state = "ready" if _binding_matches(metadata, query_result, facts) else "stale"
    return {
        **base,
        "state": state,
        "mode": metadata["mode"],
        "fact_count": int(metadata["fact_count"]),
        "embedded_count": int(metadata["embedded_count"]),
        "embedding_model": metadata["embedding_model"],
        "degraded_reason": (
            metadata["degraded_reason"]
            if state == "ready"
            else "head_binding_mismatch"
        ),
        "index_binding": {
            key: metadata[key]
            for key in (
                "head_hash",
                "generation",
                "workflow_digest",
                "author_axiom_digest",
                "projection_digest",
                "as_of_chapter",
                "active_fact_set_digest",
            )
        },
        "rebuild_command": "canon_ledger.py canon-v3 retrieval rebuild",
    }


def _old_embedding_map(
    path: Path,
    *,
    embedding_model: str,
) -> dict[tuple[str, str], bytes]:
    try:
        metadata, rows = _database_rows(path)
    except Exception:
        return {}
    if metadata.get("embedding_model") != embedding_model:
        return {}
    return {
        (str(row["retrieval_id"]), str(row["fact_content_digest"])): bytes(
            row["embedding"]
        )
        for row in rows
        if row.get("embedding") is not None
    }


def _write_database(
    temporary: Path,
    metadata: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    with sqlite3.connect(temporary) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            """
            CREATE TABLE metadata (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE facts (
                retrieval_id TEXT PRIMARY KEY,
                fact_digest TEXT NOT NULL,
                fact_content_digest TEXT NOT NULL,
                origin TEXT NOT NULL,
                category TEXT NOT NULL,
                source_chapter INTEGER NOT NULL,
                search_text TEXT NOT NULL,
                fact_json TEXT NOT NULL,
                embedding BLOB
            )
            """
        )
        conn.execute(
            "CREATE INDEX facts_category ON facts(category)"
        )
        conn.execute(
            "CREATE INDEX facts_source_chapter ON facts(source_chapter)"
        )
        conn.execute(
            "INSERT INTO metadata(singleton, payload_json) VALUES (1, ?)",
            (_compact_json(dict(metadata)),),
        )
        conn.executemany(
            """
            INSERT INTO facts(
                retrieval_id, fact_digest, fact_content_digest, origin,
                category, source_chapter, search_text, fact_json, embedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["retrieval_id"],
                    row["fact_digest"],
                    row["fact_content_digest"],
                    row["origin"],
                    row["category"],
                    row["source_chapter"],
                    row["search_text"],
                    _compact_json(row["fact"]),
                    row.get("embedding"),
                )
                for row in rows
            ],
        )
        conn.commit()
    descriptor = os.open(temporary, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover
        return
    try:
        os.fsync(descriptor)
    except OSError:  # pragma: no cover
        pass
    finally:
        os.close(descriptor)


def rebuild_retrieval_projection(
    project_root: str | Path,
    *,
    bm25_only: bool = False,
    embedder: Callable[
        [DataModulesConfig, Sequence[str]],
        tuple[list[list[float] | None], str],
    ]
    | None = None,
) -> dict[str, Any]:
    """Atomically rebuild retrieval from one exact latest active-fact set."""

    root = Path(project_root).expanduser().resolve()
    authority = WorkflowAuthority(root)
    before = authority.snapshot()
    query_result, facts = _current_query_snapshot(root)
    if not (
        before.get("head_hash") == query_result.get("head_hash")
        and before.get("workflow_digest") == query_result.get("workflow_digest")
    ):
        raise CanonRetrievalChanged(
            "canon_v3_retrieval_authority_changed_before_build"
        )
    config = DataModulesConfig.from_project_root(root)
    rows = _rows_from_facts(facts)
    path = retrieval_path(root)
    resolve_exact_project_role_path(
        root,
        path,
        expected_relative=RETRIEVAL_RELATIVE_PATH,
    )
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise CanonRetrievalInvalid(
            "canon_v3_retrieval_target_not_regular_file"
        )

    model = str(config.embed_model or "").strip()
    old_embeddings = _old_embedding_map(path, embedding_model=model)
    for row in rows:
        row["embedding"] = old_embeddings.get(
            (str(row["retrieval_id"]), str(row["fact_content_digest"]))
        )

    degraded_reason = ""
    missing = [row for row in rows if row.get("embedding") is None]
    if bm25_only:
        degraded_reason = "bm25_only_requested"
    elif missing:
        provider = embedder or _embed_documents
        embeddings, provider_reason = provider(
            config,
            [str(row["search_text"]) for row in missing],
        )
        expected_dimension: int | None = None
        existing_dimensions = {
            len(_deserialize_embedding(row["embedding"]))
            for row in rows
            if row.get("embedding") is not None
        }
        if len(existing_dimensions) == 1:
            expected_dimension = next(iter(existing_dimensions))
        elif len(existing_dimensions) > 1:
            for row in rows:
                row["embedding"] = None
            missing = list(rows)
            embeddings, provider_reason = provider(
                config,
                [str(row["search_text"]) for row in missing],
            )
        if len(embeddings) < len(missing):
            embeddings.extend([None] * (len(missing) - len(embeddings)))
        invalid_seen = False
        for row, embedding in zip(missing, embeddings):
            if embedding is None:
                continue
            try:
                raw = _serialize_embedding(embedding)
                dimension = len(raw) // 4
                if expected_dimension is None:
                    expected_dimension = dimension
                if dimension != expected_dimension:
                    raise ValueError("embedding dimension mismatch")
            except (TypeError, ValueError, OverflowError):
                invalid_seen = True
                continue
            row["embedding"] = raw
        if provider_reason:
            degraded_reason = provider_reason
        elif invalid_seen:
            degraded_reason = "invalid_embedding"

    embedded_count = sum(1 for row in rows if row.get("embedding") is not None)
    if embedded_count < len(rows) and not degraded_reason:
        if not config.retrieval_remote_enabled:
            degraded_reason = "remote_embedding_disabled"
        elif not config.embedding_enabled:
            degraded_reason = "embedding_not_configured"
        else:
            degraded_reason = "embedding_incomplete"
    metadata = _metadata_from_query(
        query_result,
        facts,
        embedding_model=model,
        embedded_count=embedded_count,
        degraded_reason=degraded_reason,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        temporary.unlink()
        _write_database(temporary, metadata, rows)
        after = authority.snapshot()
        if not bound_workflow_unchanged(before, after):
            raise CanonRetrievalChanged(
                "canon_v3_retrieval_authority_changed_during_build"
            )
        resolve_exact_project_role_path(
            root,
            path,
            expected_relative=RETRIEVAL_RELATIVE_PATH,
        )
        if path.is_symlink():
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_target_symlink_forbidden"
            )
        os.replace(temporary, path)
        _fsync_directory(path.parent)
        installed = authority.snapshot()
        if not bound_workflow_unchanged(before, installed):
            raise CanonRetrievalChanged(
                "canon_v3_retrieval_authority_changed_after_install"
            )
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    return {
        "schema_version": RETRIEVAL_STATUS_SCHEMA,
        "state": "ready",
        "authority": "canon_v3",
        "authority_layer": "retrieval_assist",
        "usable_as_canon": False,
        "writing_blocked": False,
        "path": RETRIEVAL_RELATIVE_PATH.as_posix(),
        **metadata,
        "reused_embedding_count": sum(
            1
            for row in rows
            if (
                row["retrieval_id"],
                row["fact_content_digest"],
            )
            in old_embeddings
        ),
    }


def _filter_rows(
    rows: Sequence[Mapping[str, Any]],
    categories: Sequence[str],
) -> list[dict[str, Any]]:
    selected = set(categories)
    return [
        dict(row)
        for row in rows
        if not selected or str(row.get("category") or "") in selected
    ]


def search_retrieval(
    project_root: str | Path,
    request: Mapping[str, Any] | RetrievalSearchRequest,
    *,
    query_embedder: Callable[
        [DataModulesConfig, str], tuple[list[float] | None, str]
    ]
    | None = None,
) -> dict[str, Any]:
    """Search optional retrieval and resolve every hit against active Canon."""

    parsed = (
        request
        if isinstance(request, RetrievalSearchRequest)
        else RetrievalSearchRequest.model_validate(request)
    )
    root = Path(project_root).expanduser().resolve()
    authority = WorkflowAuthority(root)
    before = authority.snapshot()
    query_result = CanonQueryFacade(root).snapshot(
        as_of_chapter=parsed.as_of_chapter
    )
    facts = _active_facts(query_result)
    active_by_key = {_fact_key(fact): fact for fact in facts}
    rows = _rows_from_facts(facts)
    index_state = "in_memory"
    index_reason = "historical_as_of_bm25"
    metadata: dict[str, Any] | None = None
    path = retrieval_path(root)
    path_safe = True
    try:
        path = resolve_exact_project_role_path(
            root,
            path,
            expected_relative=RETRIEVAL_RELATIVE_PATH,
        )
    except (OSError, ValueError):
        path_safe = False
        index_state = "invalid"
        index_reason = "unsafe_index_path_bm25_fallback"
    historical_as_of = int(query_result.get("as_of_chapter") or 0) != int(
        before.get("latest_chapter") or 0
    )
    if (
        not historical_as_of
        and path_safe
        and path.is_file()
        and not path.is_symlink()
    ):
        try:
            candidate_metadata, candidate_rows = _database_rows(path)
            if _binding_matches(candidate_metadata, query_result, facts):
                metadata = candidate_metadata
                rows = candidate_rows
                index_state = "ready"
                index_reason = ""
            else:
                index_state = "stale"
                index_reason = "head_binding_mismatch_bm25_fallback"
        except Exception:
            index_state = "invalid"
            index_reason = "invalid_index_bm25_fallback"
    elif not historical_as_of and path_safe:
        index_reason = "index_missing_bm25_fallback"

    rows = _filter_rows(rows, parsed.categories)
    bm25 = _bm25_rank(rows, parsed.query)
    ranked = bm25
    mode = "bm25"
    degraded = parsed.mode != "bm25" or index_state != "ready"
    reason = index_reason
    config = DataModulesConfig.from_project_root(root)

    embedded_rows = [row for row in rows if row.get("embedding") is not None]
    vector_requested = parsed.mode in {"auto", "vector"}
    if vector_requested and index_state == "ready" and embedded_rows:
        provider = query_embedder or _embed_query
        query_embedding, embed_reason = provider(config, parsed.query)
        if query_embedding:
            vector = _vector_rank(rows, query_embedding)
            if vector:
                if parsed.mode == "vector":
                    ranked = vector
                    mode = "vector"
                else:
                    ranked = _rrf_rank(
                        vector,
                        bm25,
                        rrf_k=max(1, int(config.rrf_k)),
                    )
                    mode = "hybrid"
                degraded = bool(
                    metadata
                    and int(metadata.get("embedded_count") or 0)
                    < int(metadata.get("fact_count") or 0)
                )
                reason = (
                    str(metadata.get("degraded_reason") or "")
                    if degraded and metadata
                    else ""
                )
            else:
                reason = "no_compatible_vectors_bm25_fallback"
        else:
            reason = embed_reason or "query_embedding_unavailable"
    elif vector_requested and not reason:
        if not config.retrieval_remote_enabled:
            reason = "remote_embedding_disabled"
        elif not config.embedding_enabled:
            reason = "embedding_not_configured"
        elif not embedded_rows:
            reason = "embedded_rows_missing"

    selected = ranked[: parsed.top_k]
    row_by_key = {str(row["retrieval_id"]): row for row in rows}
    hits: list[dict[str, Any]] = []
    for rank, (key, score) in enumerate(selected, start=1):
        row = row_by_key.get(key)
        active = active_by_key.get(key)
        if row is None or active is None:
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_hit_not_active"
            )
        if row["fact_content_digest"] != _fact_content_digest(active):
            raise CanonRetrievalInvalid(
                "canon_v3_retrieval_hit_content_mismatch"
            )
        hits.append(
            {
                "rank": rank,
                "retrieval_id": key,
                "fact_digest": row["fact_digest"],
                "fact_content_digest": row["fact_content_digest"],
                "origin": row["origin"],
                "category": row["category"],
                "source_chapter": row["source_chapter"],
                "score": round(float(score), 8),
                "source": mode,
                "matched_text": row["search_text"][:500],
                "authority_layer": "retrieval_assist",
                "resolved_against": "active_canon",
                "usable_as_canon": False,
                "active_fact": active,
            }
        )

    after = authority.snapshot()
    if not bound_workflow_unchanged(before, after):
        raise CanonRetrievalChanged(
            "canon_v3_authority_changed_during_retrieval_search"
        )
    return {
        "schema_version": RETRIEVAL_SEARCH_RESULT_SCHEMA,
        "authority": "canon_v3",
        "authority_layer": "retrieval_assist",
        "usable_as_canon": False,
        "writing_blocked": False,
        "head_hash": query_result.get("head_hash"),
        "generation": int(query_result.get("generation") or 0),
        "workflow_digest": query_result.get("workflow_digest"),
        "author_axiom_digest": query_result.get("author_axiom_digest"),
        "projection_digest": query_result.get("projection_digest"),
        "as_of_chapter": int(query_result.get("as_of_chapter") or 0),
        "active_fact_set_digest": _fact_set_digest(facts),
        "query_digest": canonical_digest(parsed.query),
        "mode": mode,
        "degraded": bool(degraded),
        "reason": reason or ("ok" if hits else "no_hit"),
        "index_state": index_state,
        "hits": hits,
    }


__all__ = [
    "CanonRetrievalChanged",
    "CanonRetrievalError",
    "CanonRetrievalInvalid",
    "RETRIEVAL_PROJECTION_SCHEMA",
    "RETRIEVAL_RELATIVE_PATH",
    "RETRIEVAL_SEARCH_REQUEST_SCHEMA",
    "RETRIEVAL_SEARCH_RESULT_SCHEMA",
    "RETRIEVAL_STATUS_SCHEMA",
    "RetrievalSearchRequest",
    "rebuild_retrieval_projection",
    "retrieval_path",
    "retrieval_status",
    "search_retrieval",
]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Dict, List

from .commit_artifacts import extraction_list, retrieval_source_marker
from .fact_text import sanitize_fact_atom, sanitize_fact_text

logger = logging.getLogger(__name__)


class VectorProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        status = str((commit_payload.get("meta") or {}).get("status") or "")
        chapter = int((commit_payload.get("meta") or {}).get("chapter") or 0)
        if status != "accepted":
            try:
                self._replace_chapter_chunks([], chapter=chapter)
            except Exception as exc:
                return {
                    "applied": False,
                    "writer": "vector",
                    "reason": f"error:{exc}",
                }
            return {"applied": False, "writer": "vector", "reason": "commit_rejected"}

        chunks = self._collect_chunks(commit_payload)
        if not chunks:
            try:
                self._replace_chapter_chunks([], chapter=chapter)
            except Exception as exc:
                return {
                    "applied": False,
                    "writer": "vector",
                    "reason": f"error:{exc}",
                }
            return {"applied": False, "writer": "vector", "reason": "no_chunks"}

        try:
            outcome = self._normalize_store_outcome(self._store_chunks(chunks))
            if outcome["storage_error"]:
                return {
                    "applied": False,
                    "writer": "vector",
                    "stored": outcome["stored"],
                    "reason": "error:storage_error",
                    "error": outcome["storage_error"],
                    "mode": outcome["mode"],
                }
            if outcome["bm25_only"] and outcome["bm25_indexed"] > 0:
                # BM25 remains a usable retrieval projection.  It is not a
                # semantic-vector success, so the service deliberately maps
                # this result to `skipped` rather than `done` or `failed`.
                return {
                    "applied": False,
                    "writer": "vector",
                    "stored": outcome["stored"],
                    "bm25_indexed": outcome["bm25_indexed"],
                    "embedded": outcome["embedded"],
                    "reason": "bm25_only",
                    "mode": outcome["mode"],
                    "degraded_reason": outcome["degraded_reason"],
                }
            if outcome["mode"] == "hybrid" and outcome["embedded"] < outcome["stored"]:
                return {
                    "applied": False,
                    "writer": "vector",
                    "stored": outcome["stored"],
                    "bm25_indexed": outcome["bm25_indexed"],
                    "embedded": outcome["embedded"],
                    "reason": "embedding_partial",
                    "mode": outcome["mode"],
                    "degraded_reason": outcome["degraded_reason"],
                }
            if outcome["stored"] <= 0:
                return {
                    "applied": False,
                    "writer": "vector",
                    "stored": outcome["stored"],
                    "reason": "error:store_failed",
                    "mode": outcome["mode"],
                }
            return {
                "applied": True,
                "writer": "vector",
                "stored": outcome["stored"],
                "embedded": outcome["embedded"],
                "bm25_indexed": outcome["bm25_indexed"],
                "mode": outcome["mode"],
                "degraded_reason": outcome["degraded_reason"],
            }
        except Exception as exc:
            logger.warning("vector_projection_failed: %s", exc)
            return {"applied": False, "writer": "vector", "reason": f"error:{exc}"}

    @staticmethod
    def _outcome_field(outcome: Any, name: str, default: Any = None) -> Any:
        if isinstance(outcome, dict):
            return outcome.get(name, default)
        return getattr(outcome, name, default)

    @staticmethod
    def _count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _normalize_store_outcome(self, outcome: Any) -> Dict[str, Any]:
        """Normalize legacy ``int`` and richer RAG store results.

        ``RAGAdapter.store_chunks`` historically returned only the number of
        embedded chunks.  Newer adapters can expose a structured outcome so a
        successful BM25-only fallback is distinguishable from a failed SQLite
        write.  Keeping the legacy branch fail-closed means an old ``0`` still
        surfaces as ``store_failed`` rather than being mistaken for success.
        """
        stored_value = self._outcome_field(outcome, "stored", None)
        if stored_value is None:
            stored_value = self._outcome_field(outcome, "vector_stored", None)
        if stored_value is None:
            stored_value = outcome if isinstance(outcome, (int, float)) else 0
        stored = self._count(stored_value)

        mode = str(self._outcome_field(outcome, "mode", "") or "").strip().lower()
        bm25_only_value = self._outcome_field(outcome, "bm25_only", False)
        bm25_only = mode == "bm25_only" or bool(bm25_only_value)
        bm25_indexed_value = self._outcome_field(outcome, "bm25_indexed", None)
        if bm25_indexed_value is None:
            bm25_indexed_value = self._outcome_field(outcome, "bm25_only_count", None)
        bm25_indexed = self._count(bm25_indexed_value)
        if bm25_only and bm25_indexed <= 0:
            # StoreOutcome's legacy-compatible `stored` value is the count of
            # successfully indexed retrieval chunks in BM25-only mode.
            bm25_indexed = stored
        embedded = self._count(
            self._outcome_field(
                outcome,
                "embedded",
                stored if not bm25_only else 0,
            )
        )
        degraded_reason = str(
            self._outcome_field(outcome, "degraded_reason", "") or ""
        ).strip()

        raw_storage_error = self._outcome_field(outcome, "storage_error", "")
        storage_error = ""
        if isinstance(raw_storage_error, str):
            storage_error = raw_storage_error.strip()
        if mode == "storage_error" or bool(raw_storage_error):
            error_detail = str(
                self._outcome_field(outcome, "error_message", "") or ""
            ).strip()
            if not error_detail:
                errors = self._outcome_field(outcome, "errors", ())
                if isinstance(errors, (list, tuple)):
                    error_detail = "; ".join(str(item) for item in errors if str(item).strip())
                elif errors:
                    error_detail = str(errors).strip()
            if not storage_error:
                storage_error = error_detail
            if not storage_error:
                storage_error = "storage_error"

        return {
            "stored": stored,
            "embedded": embedded,
            "bm25_indexed": bm25_indexed,
            "bm25_only": bm25_only,
            "storage_error": storage_error,
            "mode": mode or ("bm25_only" if bm25_only else "vector"),
            "degraded_reason": degraded_reason,
        }

    def _collect_chunks(self, commit_payload: dict) -> List[Dict[str, Any]]:
        """Render only structured consistency facts into retrieval chunks.

        Summary/scene/timeline prose and free-form event descriptions are
        deliberately excluded. They are useful writing artifacts, but they are
        also capable of carrying style or plot instructions. Cross-chapter
        facts must be represented by accepted events or deltas before RAG can
        surface them.
        """
        chunks: List[Dict[str, Any]] = []
        chapter = int(commit_payload.get("meta", {}).get("chapter") or 0)
        source_marker = retrieval_source_marker(commit_payload)

        chunk_counts: Dict[str, int] = {}

        for event in extraction_list(commit_payload, "accepted_events"):
            if not isinstance(event, dict):
                continue
            normalized_event = dict(event)
            # The accepted commit is the authoritative temporal source.  Do
            # not let a legacy or hand-edited nested event backdate future
            # facts into an earlier chapter's retrieval window.
            normalized_event["chapter"] = chapter
            text = self._event_to_text(normalized_event)
            if text:
                event_key = event.get("event_id") or f"{event.get('event_type')}:{event.get('subject')}:{text}"
                chunk_id = self._unique_chunk_id(chunk_counts, "event", chapter, event_key)
                chunks.append({
                    "chunk_id": chunk_id,
                    "chapter": chapter,
                    "scene_index": 0,
                    "content": text,
                    "chunk_type": "event",
                    "parent_chunk_id": None,
                    "source_file": source_marker,
                })

        for index, delta in enumerate(
            extraction_list(commit_payload, "state_deltas"),
            start=1,
        ):
            if not isinstance(delta, dict):
                continue
            normalized_delta = dict(delta)
            normalized_delta["chapter"] = chapter
            text = self._state_delta_to_text(normalized_delta)
            if text:
                delta_key = (
                    delta.get("delta_id")
                    or f"{delta.get('entity_id') or delta.get('entity')}:{delta.get('field') or delta.get('field_path')}:{index}"
                )
                chunks.append(
                    {
                        "chunk_id": self._unique_chunk_id(
                            chunk_counts,
                            "state_delta",
                            chapter,
                            delta_key,
                        ),
                        "chapter": chapter,
                        "scene_index": 0,
                        "content": text,
                        "chunk_type": "state_delta",
                        "parent_chunk_id": None,
                        "source_file": source_marker,
                    }
                )

        for delta in extraction_list(commit_payload, "entity_deltas"):
            if not isinstance(delta, dict):
                continue
            normalized_delta = dict(delta)
            normalized_delta["chapter"] = chapter
            text = self._delta_to_text(normalized_delta)
            if text:
                delta_key = delta.get("delta_id") or delta.get("entity_id") or text
                chunk_id = self._unique_chunk_id(chunk_counts, "entity_delta", chapter, delta_key)
                chunks.append({
                    "chunk_id": chunk_id,
                    "chapter": chapter,
                    "scene_index": 0,
                    "content": text,
                    "chunk_type": "entity_delta",
                    "parent_chunk_id": None,
                    "source_file": source_marker,
                })

        fact_chunks: List[Dict[str, Any]] = []
        for chunk in chunks:
            content = sanitize_fact_text(chunk.get("content"))
            if not content:
                continue
            normalized_chunk = dict(chunk)
            normalized_chunk["content"] = content
            fact_chunks.append(normalized_chunk)
        return fact_chunks

    def _unique_chunk_id(
        self,
        counts: Dict[str, int],
        kind: str,
        chapter: int,
        key: Any,
    ) -> str:
        base_id = self._chunk_id(kind, chapter, key)
        occurrence = counts.get(base_id, 0) + 1
        counts[base_id] = occurrence
        return base_id if occurrence == 1 else f"{base_id}_{occurrence}"

    def _chunk_id(self, kind: str, chapter: int, key: Any) -> str:
        raw = f"{kind}:{chapter}:{key}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"ch{chapter:04d}_{kind}_{digest}"

    def _event_to_text(self, event: dict) -> str:
        chapter = int(event.get("chapter") or 0)
        subject = sanitize_fact_atom(event.get("subject"))
        event_type = str(event.get("event_type") or "").strip()
        payload_value = event.get("payload")
        payload = payload_value if isinstance(payload_value, dict) else {}

        if event_type == "power_breakthrough":
            new_val = sanitize_fact_atom(
                payload.get("new")
                or payload.get("to")
                or payload.get("new_value")
                or payload.get("new_state")
            )
            return f"第{chapter}章：{subject}突破至{new_val}" if subject and new_val else ""
        elif event_type == "character_state_changed":
            field = sanitize_fact_atom(
                payload.get("field") or payload.get("field_path") or ""
            )
            new_val = sanitize_fact_atom(
                payload.get("new")
                or payload.get("to")
                or payload.get("new_value")
                or payload.get("new_state")
            )
            if subject and field and new_val:
                return f"第{chapter}章：{subject}的{field}变为{new_val}"
            if subject and new_val:
                return f"第{chapter}章：{subject}的状态变为{new_val}"
            return ""
        elif event_type == "relationship_changed":
            to_entity = sanitize_fact_atom(payload.get("to_entity") or payload.get("to"))
            rel_type = sanitize_fact_atom(
                payload.get("relationship_type") or payload.get("type")
            )
            return (
                f"第{chapter}章：{subject}与{to_entity}关系变为{rel_type}"
                if subject and to_entity and rel_type
                else ""
            )
        elif event_type in ("world_rule_revealed", "world_rule_broken"):
            rule_id = sanitize_fact_atom(
                payload.get("rule_id")
                or payload.get("rule_key")
                or subject
            )
            category = sanitize_fact_atom(payload.get("rule_category") or payload.get("category"))
            scope = sanitize_fact_atom(payload.get("scope"))
            if not rule_id:
                return ""
            action = "揭示" if "revealed" in event_type else "打破"
            attributes = "、".join(value for value in (category, scope) if value)
            suffix = f"（{attributes}）" if attributes else ""
            return f"第{chapter}章：{action}世界规则「{rule_id}」{suffix}"
        elif event_type == "open_loop_created":
            loop_id = sanitize_fact_atom(
                payload.get("loop_id") or payload.get("open_loop_id") or event.get("event_id")
            )
            loop_type = sanitize_fact_atom(payload.get("loop_type"))
            if not loop_id:
                return ""
            suffix = f"（{loop_type}）" if loop_type else ""
            return f"第{chapter}章：悬念「{loop_id}」已建立{suffix}"
        elif event_type == "open_loop_closed":
            loop_id = sanitize_fact_atom(
                payload.get("loop_id")
                or payload.get("target_loop_id")
                or payload.get("open_loop_id")
                or payload.get("target_id")
                or payload.get("resolves_event_id")
                or subject
            )
            if not loop_id:
                return ""
            return f"第{chapter}章：悬念「{loop_id}」已收束"
        elif event_type == "promise_created":
            promise_id = sanitize_fact_atom(
                payload.get("promise_id") or event.get("event_id") or subject
            )
            return f"第{chapter}章：立下读者承诺「{promise_id}」" if promise_id else ""
        elif event_type == "promise_paid_off":
            promise_id = sanitize_fact_atom(
                payload.get("promise_id")
                or payload.get("target_promise_id")
                or payload.get("target_id")
                or payload.get("resolves_event_id")
                or subject
            )
            if not promise_id:
                return ""
            return f"第{chapter}章：读者承诺「{promise_id}」已兑现"
        elif event_type == "artifact_obtained":
            name = sanitize_fact_atom(payload.get("name") or payload.get("artifact_id") or subject)
            owner = sanitize_fact_atom(payload.get("owner") or payload.get("holder"))
            if name and owner:
                return f"第{chapter}章：{owner}获得{name}" if owner else f"第{chapter}章：获得{name}"
        return ""

    def _delta_to_text(self, delta: dict) -> str:
        chapter = int(delta.get("chapter") or 0)
        from_e = sanitize_fact_atom(delta.get("from_entity"))
        to_e = sanitize_fact_atom(delta.get("to_entity"))
        rel = sanitize_fact_atom(delta.get("relationship_type"))

        if from_e and to_e and rel:
            return f"第{chapter}章：{from_e}与{to_e}关系变为{rel}"

        entity_id = sanitize_fact_atom(delta.get("entity_id"))
        canonical = sanitize_fact_atom(delta.get("canonical_name") or entity_id)
        if entity_id and canonical:
            return f"第{chapter}章：实体变更——{canonical}"
        return ""

    def _state_delta_to_text(self, delta: dict) -> str:
        chapter = int(delta.get("chapter") or 0)
        entity = sanitize_fact_atom(
            delta.get("entity_id") or delta.get("entity") or delta.get("subject") or ""
        )
        field = sanitize_fact_atom(delta.get("field") or delta.get("field_path"))
        sentinel = object()
        value: Any = delta.get("new", sentinel)
        if value is sentinel:
            value = delta.get("to", sentinel)
        if value is sentinel:
            value = delta.get("value", sentinel)
        if not entity or not field or value is sentinel:
            return ""
        rendered = sanitize_fact_atom(value)
        if not rendered:
            return ""
        return f"第{chapter}章：{entity}的{field}变为{rendered}"

    def _run_store_coro(self, coro: Coroutine[Any, Any, Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result: Dict[str, Any] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result["value"] if "value" in result else 0

    def _store_chunks(self, chunks: List[Dict[str, Any]]) -> Any:
        from .config import DataModulesConfig
        from .rag_adapter import RAGAdapter

        config = DataModulesConfig.from_project_root(self.project_root)
        adapter = RAGAdapter(config)
        chapter = int(chunks[0].get("chapter") or 0) if chunks else 0
        return self._run_store_coro(
            adapter.store_chunks(chunks, replace_chapter=chapter)
        )

    def _replace_chapter_chunks(
        self,
        chunks: List[Dict[str, Any]],
        *,
        chapter: int,
    ) -> None:
        """Make the current commit's retrieval rows authoritative for its chapter."""
        if chapter <= 0:
            raise ValueError("commit chapter must be positive")
        from .config import DataModulesConfig

        config = DataModulesConfig.from_project_root(self.project_root)
        if not chunks and not config.vector_db.is_file():
            return
        from .rag_adapter import RAGAdapter

        adapter = RAGAdapter(config)
        adapter.replace_chapter_chunks(chapter, chunks)

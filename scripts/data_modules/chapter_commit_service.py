#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from chapter_outline_loader import volume_num_for_chapter_from_state

from .chapter_commit_schema import (
    ChapterCommitSchema,
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
    normalize_timeline_events,
)
from .chapter_content_binding import (
    ChapterBindingError,
    build_chapter_binding,
    chapter_bindings_equal,
    verify_chapter_binding,
    verify_commit_content_binding,
)
from .commit_artifacts import extraction_list
from .config import DataModulesConfig
from .event_log_store import EventLogStore
from .event_projection_router import EventProjectionRouter
from .story_contracts import write_json
from .index_manager import IndexManager
from .override_ledger_service import (
    AmendProposalTrigger,
    ensure_override_ledger_columns,
    persist_amend_proposals,
)


class ChapterCommitService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build_commit(
        self,
        chapter: int,
        review_result: Dict[str, Any],
        fulfillment_result: Dict[str, Any],
        disambiguation_result: Dict[str, Any],
        extraction_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        review = ReviewResult.model_validate(review_result)
        fulfillment = FulfillmentResult.model_validate(fulfillment_result)
        disambiguation = DisambiguationResult.model_validate(disambiguation_result)
        extraction = ExtractionResult.model_validate(extraction_result)

        artifact_models = {
            "review_result": review,
            "fulfillment_result": fulfillment,
            "disambiguation_result": disambiguation,
            "extraction_result": extraction,
        }
        for artifact_name, artifact in artifact_models.items():
            binding_payload = artifact.chapter_binding.model_dump()
            ok, code = verify_chapter_binding(
                self.project_root,
                chapter,
                binding_payload,
            )
            if not ok:
                raise ChapterBindingError(
                    code,
                    f"{artifact_name}.chapter_binding verification failed: {code}",
                )

        # Re-read once after all artifact checks.  This final fingerprint is
        # the commit's source of truth and closes the normal review→commit
        # mutation window.
        chapter_binding = build_chapter_binding(self.project_root, chapter)
        for artifact_name, artifact in artifact_models.items():
            if not chapter_bindings_equal(
                chapter_binding,
                artifact.chapter_binding,
            ):
                raise ChapterBindingError(
                    "chapter_content_hash_mismatch",
                    f"{artifact_name}.chapter_binding is stale",
                )
        rejected = bool(review.blocking_count) or bool(
            fulfillment.missed_nodes
        ) or bool(disambiguation.pending)
        status = "rejected" if rejected else "accepted"
        volume = volume_num_for_chapter_from_state(self.project_root, chapter) or 1
        accepted_events = EventLogStore(self.project_root).normalize_events(
            chapter, extraction.accepted_events
        )
        extraction_payload = extraction.model_dump()
        extraction_payload["accepted_events"] = accepted_events
        # Pre-v1 data-agent artifacts nested timeline rows under memory_facts.
        # Lift them into the canonical commit field so old projects remain
        # replayable through the same router/writer path.
        legacy_memory_facts = extraction_payload.get("memory_facts")
        timeline_rows = extraction.timeline_events
        if (
            not timeline_rows
            and isinstance(legacy_memory_facts, dict)
            and isinstance(legacy_memory_facts.get("timeline_events"), list)
        ):
            timeline_rows = legacy_memory_facts["timeline_events"]
        extraction_payload["timeline_events"] = normalize_timeline_events(
            chapter, timeline_rows
        )
        commit_payload = {
            "meta": {
                "schema_version": "story-system/v1",
                "chapter": chapter,
                "status": status,
            },
            "chapter_binding": chapter_binding,
            "contract_refs": {
                "master": "MASTER_SETTING.json",
                "volume": f"volume_{volume:03d}.json",
                "chapter": f"chapter_{chapter:03d}.json",
                "review": f"chapter_{chapter:03d}.review.json",
            },
            "provenance": {
                "write_fact_role": "chapter_commit",
                "projection_role": "derived_read_models",
                "legacy_state_role": "projection_only",
                "chapter_binding": chapter_binding,
            },
            "outline_snapshot": {
                "planned_nodes": fulfillment.planned_nodes,
                "covered_nodes": fulfillment.covered_nodes,
                "missed_nodes": fulfillment.missed_nodes,
                "extra_nodes": fulfillment.extra_nodes,
            },
            "review_result": review.model_dump(),
            "fulfillment_result": fulfillment.model_dump(),
            "disambiguation_result": disambiguation.model_dump(),
            "extraction_result": extraction_payload,
            "projection_status": {
                "state": "pending",
                "index": "pending",
                "summary": "pending",
                "memory": "pending",
                "vector": "pending",
            },
        }
        if status == "accepted":
            from .memory.writer import MemoryWriter

            lifecycle_errors = MemoryWriter(
                DataModulesConfig.from_project_root(self.project_root)
            ).validate_commit_projection(commit_payload)
            if lifecycle_errors:
                raise ValueError(f"invalid_consistency_fact:{lifecycle_errors[0]}")
        return ChapterCommitSchema.model_validate(commit_payload).model_dump()

    def persist_commit(self, payload: Dict[str, Any]) -> Path:
        target = self.project_root / ".story-system" / "commits"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"chapter_{int(payload['meta']['chapter']):03d}.commit.json"
        write_json(path, payload)
        return path

    def _projection_writers(self) -> dict[str, Any]:
        from .index_projection_writer import IndexProjectionWriter
        from .memory_projection_writer import MemoryProjectionWriter
        from .state_projection_writer import StateProjectionWriter
        from .summary_projection_writer import SummaryProjectionWriter
        from .vector_projection_writer import VectorProjectionWriter

        return {
            "state": StateProjectionWriter(self.project_root),
            "index": IndexProjectionWriter(self.project_root),
            "summary": SummaryProjectionWriter(self.project_root),
            "memory": MemoryProjectionWriter(self.project_root),
            "vector": VectorProjectionWriter(self.project_root),
        }

    def _writer_status(self, result: dict[str, Any]) -> str:
        if result.get("applied"):
            return "done"
        reason = str(result.get("reason") or "").strip()
        if reason in {
            "not_required",
            "commit_rejected",
            "no_chunks",
            "bm25_only",
            "embedding_partial",
        }:
            # BM25-only is a successful lexical-retrieval fallback, not a
            # semantic-vector write.  Preserve the result in the projection
            # log while keeping the vector writer visibly non-done.
            return "skipped"
        if reason.startswith("error:"):
            return f"failed:{reason[6:] or 'writer_error'}"
        return "skipped"

    def _persist_projection_run(
        self,
        payload: Dict[str, Any],
        writer_results: dict[str, dict[str, Any]],
    ) -> None:
        commit_path = self.persist_commit(payload)
        try:
            from .projection_log import append_projection_run

            append_projection_run(
                self.project_root,
                payload,
                writer_results,
                commit_path=commit_path,
            )
        except Exception:
            pass

    def _block_invalid_lifecycle(self, payload: Dict[str, Any]) -> bool:
        """Fail before event-log/derived writes when a closure has no target."""
        if str((payload.get("meta") or {}).get("status") or "") != "accepted":
            return False

        from .memory.writer import MemoryWriter

        errors = MemoryWriter(
            DataModulesConfig.from_project_root(self.project_root)
        ).validate_commit_projection(payload)
        if not errors:
            return False

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}
        error = errors[0]
        payload["projection_status"]["memory"] = f"failed:{error}"
        required = set(EventProjectionRouter().required_writers(payload))
        writer_results: dict[str, dict[str, Any]] = {}
        for name in required:
            if name == "memory":
                writer_results[name] = {
                    "status": f"failed:{error}",
                    "error": error,
                    "reason": "lifecycle_validation_failed",
                }
            else:
                payload["projection_status"].setdefault(name, "pending")
                writer_results[name] = {
                    "status": str(payload["projection_status"].get(name) or "pending"),
                    "reason": "blocked_by_lifecycle_validation",
                }
        self._persist_projection_run(payload, writer_results)
        return True

    def _verify_commit_content_binding(self, payload: Dict[str, Any]) -> str:
        """Return a stable failure code when a commit no longer binds current prose."""
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        try:
            chapter = int((meta or {}).get("chapter") or 0)
        except (TypeError, ValueError):
            return "artifact_chapter_mismatch"
        if chapter <= 0:
            return "artifact_chapter_mismatch"
        ok, code = verify_commit_content_binding(
            self.project_root,
            chapter,
            payload,
        )
        return "" if ok else code

    def _block_changed_chapter_content(self, payload: Dict[str, Any]) -> bool:
        """Fail closed before any event or derived read-model write."""
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return False

        error_code = self._verify_commit_content_binding(payload)
        if not error_code:
            return False

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}
        required = set(EventProjectionRouter().required_writers(payload)) or {"state"}
        writer_results: dict[str, dict[str, Any]] = {}
        for name in required:
            payload["projection_status"][name] = "failed:chapter_content_changed"
            writer_results[name] = {
                "status": "failed:chapter_content_changed",
                "error": error_code,
                "reason": "chapter_content_changed",
            }
        self._persist_projection_run(payload, writer_results)
        return True

    def apply_projection_writers(
        self,
        payload: Dict[str, Any],
        *,
        only_writers: set[str] | None = None,
    ) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        payload.setdefault("projection_status", {})
        if not isinstance(payload["projection_status"], dict):
            payload["projection_status"] = {}

        if self._block_changed_chapter_content(payload):
            return payload
        if self._block_invalid_lifecycle(payload):
            return payload

        writers = self._projection_writers()
        required_writers = set(EventProjectionRouter().required_writers(payload))
        writer_results: dict[str, dict[str, Any]] = {}
        for name, writer in writers.items():
            if only_writers is not None and name not in only_writers:
                writer_results[name] = {
                    "status": str(payload["projection_status"].get(name) or "pending"),
                    "reason": "not_selected",
                }
                continue
            if name not in required_writers:
                payload["projection_status"][name] = "skipped"
                writer_results[name] = {"status": "skipped", "reason": "not_required"}
                continue
            # A writer can execute arbitrary storage code.  Re-hash before
            # every subsequent writer so a concurrent/manual prose edit
            # cannot let the rest of the projection chain stamp stale facts
            # as done.
            binding_error = self._verify_commit_content_binding(payload)
            if binding_error:
                for pending_name in required_writers:
                    current = str(payload["projection_status"].get(pending_name) or "")
                    if current in {"", "pending"}:
                        payload["projection_status"][pending_name] = (
                            "failed:chapter_content_changed"
                        )
                        writer_results[pending_name] = {
                            "status": "failed:chapter_content_changed",
                            "error": binding_error,
                            "reason": "chapter_content_changed",
                        }
                self._persist_projection_run(payload, writer_results)
                return payload
            try:
                result = writer.apply(payload)
                payload["projection_status"][name] = self._writer_status(result)
                writer_results[name] = {
                    "status": payload["projection_status"][name],
                    "result": result,
                }
            except Exception as exc:
                payload["projection_status"][name] = f"failed:{exc}"
                writer_results[name] = {"status": "failed", "error": str(exc)}
        self._persist_projection_run(payload, writer_results)
        return payload

    def apply_projections(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        if self._block_changed_chapter_content(payload):
            return payload

        if status == "accepted":
            chapter = int((payload.get("meta") or {}).get("chapter") or 0)
            event_store = EventLogStore(self.project_root)
            accepted_events = extraction_list(payload, "accepted_events")
            extraction = payload.setdefault("extraction_result", {})
            if not isinstance(extraction, dict):
                extraction = {}
                payload["extraction_result"] = extraction
            extraction["accepted_events"] = event_store.normalize_events(
                chapter, accepted_events
            )
            # Normalization is a user-code boundary.  Re-read the manuscript
            # immediately before lifecycle handling and the first event write.
            if self._block_changed_chapter_content(payload):
                return payload
            if self._block_invalid_lifecycle(payload):
                return payload
            event_store.write_events(chapter, extraction["accepted_events"])

            proposals = AmendProposalTrigger().check(chapter, extraction["accepted_events"])
            if proposals:
                manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
                with manager._get_conn() as conn:
                    ensure_override_ledger_columns(conn)
                    persist_amend_proposals(conn, chapter, proposals)
                    conn.commit()

        return self.apply_projection_writers(payload)

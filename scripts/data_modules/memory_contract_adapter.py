#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MemoryContractAdapter——薄适配器，包装现有模块满足 MemoryContract Protocol。

不做存储重构，仅委托给 StateManager / IndexManager / ScratchpadManager 等。
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from .chapter_commit_service import ChapterCommitService
from .commit_artifacts import extraction_list
from .config import DataModulesConfig, get_config
from .memory_contract import (
    CommitResult,
    ContextPack,
    EntitySnapshot,
    LifecycleObligation,
    OpenLoop,
    Rule,
    TimelineEvent,
)
from .memory.hard_constraints import normalize_hard_constraints
from .state_snapshot import validate_state_snapshot
from .consistency_context import sanitize_story_contracts
from .rag_context import chapter_goal_from_contract, empty_rag_assist, load_rag_assist
from .story_runtime_sources import commit_status_view, load_runtime_sources
from .urgency_utils import coerce_urgency

logger = logging.getLogger(__name__)


def _estimate_tokens(value: Any) -> int:
    """Deterministic tokenizer-free estimate used for context budgeting.

    UTF-8 bytes / 4 is deliberately conservative for mixed Chinese/ASCII and
    stable across supported Python platforms.  This is a budget signal, not a
    model-vendor billing tokenizer.
    """
    try:
        blob = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = str(value or "")
    if not blob:
        return 0
    return max(1, (len(blob.encode("utf-8")) + 3) // 4)


def _estimate_context_pack_tokens(
    *,
    chapter: int,
    sections: Dict[str, Any],
    budget: Dict[str, Any],
    completeness: Dict[str, Any],
) -> int:
    """Estimate the complete public JSON payload, not only ``sections``."""
    used = 0
    for _ in range(4):
        payload = {
            "chapter": int(chapter),
            "sections": sections,
            "budget_used_tokens": used,
            "schema_version": "webnovel-context-pack/v2",
            "budget": {**budget, "used_tokens": used},
            "completeness": completeness,
        }
        measured = _estimate_tokens(payload)
        if measured == used:
            break
        used = measured
    return used


def _sanitize_state_value(value: Any) -> tuple[Any, bool]:
    """Return a compact structured state value and whether data was rejected."""
    from .fact_text import sanitize_fact_atom

    if value is None or isinstance(value, (bool, int)):
        return value, False
    if isinstance(value, float):
        return (value, False) if math.isfinite(value) else (None, True)
    if isinstance(value, str):
        cleaned = sanitize_fact_atom(value, max_chars=240)
        return cleaned, bool(value.strip() and not cleaned)
    if isinstance(value, list):
        result = []
        rejected = False
        for item in value:
            cleaned, item_rejected = _sanitize_state_value(item)
            rejected = rejected or item_rejected
            if not item_rejected:
                result.append(cleaned)
        return result, rejected
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        rejected = False
        for raw_key, raw_value in value.items():
            key = sanitize_fact_atom(raw_key, max_chars=120)
            if not key:
                rejected = True
                continue
            cleaned, item_rejected = _sanitize_state_value(raw_value)
            rejected = rejected or item_rejected
            if not item_rejected:
                result[key] = cleaned
        return result, rejected
    return None, True


class MemoryContractAdapter:
    """满足 MemoryContract Protocol 的具体实现。"""

    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()

    # ------------------------------------------------------------------
    # 内部懒加载（避免在构造时就初始化所有重量级模块）
    # ------------------------------------------------------------------

    def _state_manager(self):
        from .state_manager import StateManager
        return StateManager(self.config)

    def _index_manager(self):
        from .index_manager import IndexManager
        return IndexManager(self.config)

    def _memory_writer(self):
        from .memory.writer import MemoryWriter
        return MemoryWriter(self.config)

    def _memory_store(self):
        from .memory.store import ScratchpadManager
        return ScratchpadManager(self.config)

    def _memory_orchestrator(self):
        from .memory.orchestrator import MemoryOrchestrator
        return MemoryOrchestrator(self.config)

    # ------------------------------------------------------------------
    # 契约方法
    # ------------------------------------------------------------------

    def commit_chapter(self, chapter: int, result: dict) -> CommitResult:
        if self._should_use_commit_mainline(result):
            return self._commit_chapter_mainline(chapter, result)

        return self._commit_chapter_legacy(chapter, result)

    def _commit_chapter_legacy(self, chapter: int, result: dict) -> CommitResult:
        warnings: List[str] = []
        entities_added = 0
        entities_updated = 0
        state_changes_recorded = 0
        relationships_added = 0
        memory_items_added = 0
        summary_path = ""

        # 1. StateManager: process_chapter_result
        try:
            sm = self._state_manager()
            sm._load_state()
            sm_warnings = sm.process_chapter_result(chapter, result)
            warnings.extend(sm_warnings or [])
            entities_added = len(result.get("entities_new", []) or [])
            entities_updated = len(result.get("entities_appeared", []) or [])
            state_changes_recorded = len(result.get("state_changes", []) or [])
            relationships_added = len(result.get("relationships_new", []) or [])
        except Exception as e:
            logger.warning("commit_chapter: StateManager failed: %s", e)
            warnings.append(f"StateManager error: {e}")

        # 2. MemoryWriter: update_from_chapter_result
        try:
            mw = self._memory_writer()
            mem_stats = mw.update_from_chapter_result(chapter, result)
            memory_items_added = mem_stats.get("items_added", 0)
            if mem_stats.get("warnings"):
                warnings.extend(mem_stats["warnings"])
        except Exception as e:
            logger.warning("commit_chapter: MemoryWriter failed: %s", e)
            warnings.append(f"MemoryWriter error: {e}")

        # 3. 摘要路径
        padded = f"{chapter:04d}"
        summary_file = self.config.webnovel_dir / "summaries" / f"ch{padded}.md"
        if summary_file.exists():
            summary_path = str(summary_file)

        return CommitResult(
            chapter=chapter,
            entities_added=entities_added,
            entities_updated=entities_updated,
            state_changes_recorded=state_changes_recorded,
            relationships_added=relationships_added,
            memory_items_added=memory_items_added,
            summary_path=summary_path,
            warnings=warnings,
        )

    def _commit_chapter_mainline(self, chapter: int, result: dict) -> CommitResult:
        service = ChapterCommitService(self.config.project_root)
        payload = service.build_commit(
            chapter=chapter,
            review_result=result.get("review_result", {}) or {},
            fulfillment_result=result.get("fulfillment_result", {}) or {},
            disambiguation_result=result.get("disambiguation_result", {}) or {},
            extraction_result=result.get("extraction_result", {}) or {},
        )
        service.persist_commit(payload)
        if payload["meta"]["status"] == "accepted":
            payload = service.apply_projections(payload)

        summary_file = self.config.webnovel_dir / "summaries" / f"ch{chapter:04d}.md"
        return CommitResult(
            chapter=chapter,
            entities_added=len(extraction_list(payload, "entity_deltas")),
            entities_updated=0,
            state_changes_recorded=len(extraction_list(payload, "state_deltas")),
            relationships_added=0,
            memory_items_added=0,
            summary_path=str(summary_file) if summary_file.exists() else "",
            warnings=[f"commit_status={payload['meta']['status']}"],
        )

    def _should_use_commit_mainline(self, result: dict) -> bool:
        if not isinstance(result, dict):
            return False
        mainline_keys = {
            "review_result",
            "fulfillment_result",
            "disambiguation_result",
            "extraction_result",
        }
        return any(key in result for key in mainline_keys)

    def load_context(self, chapter: int, budget_tokens: int = 4000) -> ContextPack:
        requested_tokens = max(1, int(budget_tokens or 1))
        mandatory: Dict[str, Any] = {}
        optional: Dict[str, Any] = {}
        source_status: Dict[str, Dict[str, str]] = {}
        missing_sources: List[str] = []
        omitted_hard_ids: List[str] = []
        runtime_sources = load_runtime_sources(self.config.project_root, chapter)

        mandatory["story_contracts"] = sanitize_story_contracts(
            dict(runtime_sources.contracts)
        )
        mandatory["runtime_status"] = {
            "chapter": int(getattr(runtime_sources, "chapter", chapter) or chapter),
            "fallback_sources": list(
                getattr(runtime_sources, "fallback_sources", []) or []
            ),
            "primary_write_source": str(
                getattr(runtime_sources, "primary_write_source", "chapter_commit")
            ),
            "latest_commit": commit_status_view(
                getattr(runtime_sources, "latest_commit", None)
            ),
            "latest_accepted_commit": commit_status_view(
                getattr(runtime_sources, "latest_accepted_commit", None)
            ),
        }
        mandatory["latest_commit"] = (
            commit_status_view(getattr(runtime_sources, "latest_commit", None)) or {}
        )
        source_status["story_contracts"] = {"status": "ok"}

        # 1. MemoryOrchestrator 基础包
        memory_pack: Dict[str, Any] = {}
        try:
            orch = self._memory_orchestrator()
            try:
                memory_pack = orch.build_memory_pack(chapter, include_soft=False)
            except TypeError as exc:
                if "include_soft" not in str(exc):
                    raise
                # Compatibility with third-party/test orchestrators that
                # implement the pre-v1 signature.
                memory_pack = orch.build_memory_pack(chapter)
            if not isinstance(memory_pack, dict):
                raise TypeError("memory_pack_must_be_object")
            source_status["scratchpad"] = {"status": "ok"}
        except Exception as e:
            logger.warning("load_context: orchestrator failed: %s", e)
            memory_pack = {}
            source_status["scratchpad"] = {
                "status": "error",
                "reason": e.__class__.__name__,
            }
            missing_sources.append("scratchpad")

        hard_constraints, hard_error = normalize_hard_constraints(memory_pack)
        if hard_error:
            source_status["scratchpad"] = {
                "status": "error",
                "reason": hard_error,
            }
            missing_sources.append("scratchpad")
            hard_constraints = []
        for warning in memory_pack.get("warnings") or []:
            if not isinstance(warning, dict):
                continue
            if warning.get("type") == "unsafe_hard_constraint":
                ids = [
                    str(item)
                    for item in (warning.get("ids") or [])
                    if str(item)
                ]
                omitted_hard_ids.extend(ids)
                if int(warning.get("count") or len(ids) or 0) > 0 and not ids:
                    missing_sources.append("scratchpad")
                    source_status["scratchpad"] = {
                        "status": "error",
                        "reason": "unsafe_hard_constraint_without_ids",
                    }

        mandatory["hard_constraints"] = hard_constraints

        # Context Pack v2 exposes one canonical hard set.  Optional evidence
        # never repeats it, and aliases that duplicated the same prose were
        # intentionally retired with the schema-version bump.
        if memory_pack:
            optional["memory_pack"] = {
                key: value
                for key, value in memory_pack.items()
                if key
                not in {
                    "hard_constraints",
                    "active_constraints",
                    "working_memory",
                    "semantic_memory",
                    "long_term_facts",
                    "episodic_memory",
                    "recent_changes",
                }
            }

        # 2. 章纲摘要
        try:
            from chapter_outline_loader import load_chapter_outline
            outline = load_chapter_outline(self.config.project_root, chapter, max_chars=1500)
            if outline and not outline.startswith("⚠️"):
                optional["outline"] = outline
            source_status["outline"] = {"status": "ok"}
        except Exception as e:
            logger.warning("load_context: outline failed: %s", e)
            source_status["outline"] = {
                "status": "error",
                "reason": e.__class__.__name__,
            }

        # 2.5. RAG is a default, best-effort fact lookup.  It never becomes a
        # creative instruction: callers receive only prior-story evidence and
        # can continue safely when the index is empty or unavailable.
        try:
            # Build retrieval input from the already-sanitized contract view;
            # raw chapter contracts may contain prose/style instructions.
            chapter_goal = chapter_goal_from_contract(
                mandatory["story_contracts"].get("chapter")
                or mandatory["story_contracts"].get("chapter_brief")
            )
            optional["rag_assist"] = load_rag_assist(
                self.config.project_root,
                chapter=chapter,
                outline=str(optional.get("outline") or ""),
                chapter_goal=chapter_goal,
                config=self.config,
            )
            source_status["rag"] = {"status": "ok"}
        except Exception as e:
            logger.warning("load_context: rag assist failed: %s", e)
            optional["rag_assist"] = empty_rag_assist(
                enabled=bool(getattr(self.config, "context_rag_assist_enabled", True)),
                reason=f"rag_error:{e.__class__.__name__}",
            )
            optional["rag_assist"]["degraded"] = True
            source_status["rag"] = {
                "status": "degraded",
                "reason": e.__class__.__name__,
            }

        # Free-form summaries are intentionally not injected. Accepted events,
        # state deltas, hard constraints and fact-only RAG are the trusted
        # continuity sources.
        source_status["summaries"] = {"status": "excluded_untyped"}

        # 4. 主角状态 + 进度。state.json 是“当前”投影，不是历史快照；
        # 只有在其 current_chapter 严格早于目标章时才可用于写前上下文。
        try:
            if self.config.state_file.exists():
                state_payload = json.loads(
                    self.config.state_file.read_text(encoding="utf-8")
                )
                if not isinstance(state_payload, dict):
                    raise ValueError("state_root_must_be_object")
            else:
                state_payload = {}
            progress = state_payload.get("progress") or {}
            if not isinstance(progress, dict):
                raise ValueError("state_progress_must_be_object")
            state_chapter, state_safe, state_reason = validate_state_snapshot(
                state_payload,
                chapter,
            )
            if not state_safe:
                source_status["state"] = {
                    "status": "as_of_unavailable",
                    "reason": state_reason,
                    "current_chapter": (
                        str(state_chapter) if state_chapter is not None else ""
                    ),
                }
                missing_sources.append("state_as_of_chapter")
            else:
                protagonist = state_payload.get("protagonist_state") or {}
                safe_protagonist, protagonist_rejected = _sanitize_state_value(
                    protagonist
                )
                safe_progress, progress_rejected = _sanitize_state_value(progress)
                if protagonist_rejected or progress_rejected:
                    source_status["state"] = {
                        "status": "error",
                        "reason": "unsafe_state_value",
                    }
                    missing_sources.append("state")
                else:
                    if safe_protagonist:
                        mandatory["protagonist"] = safe_protagonist
                    if safe_progress:
                        mandatory["progress"] = safe_progress
                    source_status["state"] = {"status": "ok"}
        except Exception as e:
            logger.warning("load_context: state failed: %s", e)
            source_status["state"] = {
                "status": "error",
                "reason": e.__class__.__name__,
            }
            missing_sources.append("state")

        missing_sources = list(dict.fromkeys(missing_sources))
        completeness = {
            "status": "blocked" if missing_sources or omitted_hard_ids else "complete",
            "missing_sources": missing_sources,
            "omitted_hard_ids": sorted(set(omitted_hard_ids)),
            "source_status": source_status,
        }

        omitted_soft_sections: List[str] = []
        sections = dict(mandatory)

        def _budget_envelope(*, hard_over_budget: bool) -> Dict[str, Any]:
            return {
                "requested_tokens": requested_tokens,
                "used_tokens": 0,
                "mandatory_tokens": 0,
                "hard_constraint_tokens": _estimate_tokens(hard_constraints),
                "hard_over_budget": hard_over_budget,
                "overflow_tokens": 0,
                "truncated": bool(omitted_soft_sections),
                "omitted_soft_sections": list(omitted_soft_sections),
            }

        mandatory_budget = _budget_envelope(hard_over_budget=False)
        mandatory_tokens = _estimate_context_pack_tokens(
            chapter=chapter,
            sections=mandatory,
            budget=mandatory_budget,
            completeness=completeness,
        )
        hard_over_budget = mandatory_tokens > requested_tokens
        if hard_over_budget:
            completeness["status"] = "blocked"

        # Add soft sections by importance.  The complete public envelope is
        # measured for every decision so metadata itself cannot push a
        # supposedly in-budget pack over the target.
        for key in ("outline", "memory_pack", "rag_assist"):
            if key not in optional:
                continue
            candidate = {**sections, key: optional[key]}
            candidate_budget = _budget_envelope(
                hard_over_budget=hard_over_budget
            )
            candidate_budget["mandatory_tokens"] = mandatory_tokens
            candidate_used = _estimate_context_pack_tokens(
                chapter=chapter,
                sections=candidate,
                budget=candidate_budget,
                completeness=completeness,
            )
            if not hard_over_budget and candidate_used <= requested_tokens:
                sections[key] = optional[key]
            else:
                omitted_soft_sections.append(key)

        budget = _budget_envelope(hard_over_budget=hard_over_budget)
        budget["mandatory_tokens"] = mandatory_tokens
        used_tokens = _estimate_context_pack_tokens(
            chapter=chapter,
            sections=sections,
            budget=budget,
            completeness=completeness,
        )
        if not hard_over_budget and used_tokens > requested_tokens:
            for key in ("rag_assist", "memory_pack", "outline"):
                if key not in sections:
                    continue
                sections.pop(key, None)
                if key not in omitted_soft_sections:
                    omitted_soft_sections.append(key)
                budget["truncated"] = True
                budget["omitted_soft_sections"] = list(omitted_soft_sections)
                used_tokens = _estimate_context_pack_tokens(
                    chapter=chapter,
                    sections=sections,
                    budget=budget,
                    completeness=completeness,
                )
                if used_tokens <= requested_tokens:
                    break
        if used_tokens > requested_tokens and not any(
            key in sections
            for key in ("outline", "memory_pack", "rag_assist")
        ):
            hard_over_budget = True
            completeness["status"] = "blocked"
            budget["hard_over_budget"] = True
        budget["used_tokens"] = used_tokens
        budget["overflow_tokens"] = max(0, used_tokens - requested_tokens)
        budget["truncated"] = bool(omitted_soft_sections)
        budget["omitted_soft_sections"] = list(omitted_soft_sections)
        # Digit-width changes in used/overflow are included in the final pass.
        used_tokens = _estimate_context_pack_tokens(
            chapter=chapter,
            sections=sections,
            budget=budget,
            completeness=completeness,
        )
        budget["used_tokens"] = used_tokens
        budget["overflow_tokens"] = max(0, used_tokens - requested_tokens)

        return ContextPack(
            chapter=chapter,
            sections=sections,
            budget_used_tokens=used_tokens,
            budget=budget,
            completeness=completeness,
        )

    def query_entity(self, entity_id: str) -> Optional[EntitySnapshot]:
        try:
            sm = self._state_manager()
            sm._load_state()
            entity = sm.get_entity(entity_id)
            if not entity:
                return None

            entity_type = sm.get_entity_type(entity_id) or "角色"
            state_changes = sm.get_state_changes(entity_id)
            recent_changes = state_changes[-5:] if state_changes else []

            return EntitySnapshot(
                id=entity_id,
                name=entity.get("name", entity_id),
                type=entity_type,
                tier=entity.get("tier", "核心"),
                aliases=entity.get("aliases", []),
                attributes={k: v for k, v in entity.items()
                            if k not in ("name", "tier", "aliases", "first_appearance", "last_appearance")},
                first_appearance=entity.get("first_appearance", 0),
                last_appearance=entity.get("last_appearance", 0),
                recent_state_changes=recent_changes,
            )
        except Exception as e:
            logger.warning("query_entity(%s) failed: %s", entity_id, e)
            return None

    def query_rules(self, domain: str = "") -> List[Rule]:
        try:
            store = self._memory_store()
            items = store.query(category="world_rule", status="active")
            rules = []
            for item in items:
                if domain and item.subject != domain and domain not in item.value:
                    continue
                rules.append(Rule(
                    id=item.id,
                    subject=item.subject,
                    field=item.field,
                    value=item.value,
                    domain=item.subject,
                    source_chapter=item.source_chapter,
                ))
            return rules
        except Exception as e:
            logger.warning("query_rules failed: %s", e)
            return []

    def read_summary(self, chapter: int) -> str:
        padded = f"{chapter:04d}"
        summary_file = self.config.webnovel_dir / "summaries" / f"ch{padded}.md"
        try:
            if summary_file.exists():
                return summary_file.read_text(encoding="utf-8")
            return ""
        except Exception as e:
            logger.warning("read_summary(%d) failed: %s", chapter, e)
            return ""

    def get_open_loops(self, status: str = "active") -> List[OpenLoop]:
        try:
            store = self._memory_store()
            items = store.query(category="open_loop", status=status)
            return [
                OpenLoop(
                    id=str((item.payload or {}).get("lifecycle_id") or item.id),
                    content=item.value,
                    status=item.status,
                    planted_chapter=item.source_chapter,
                    expected_payoff=item.payload.get("expected_payoff", ""),
                    urgency=coerce_urgency(item.payload.get("urgency")),
                )
                for item in items
            ]
        except Exception as e:
            logger.warning("get_open_loops failed: %s", e)
            return []

    def get_lifecycle_obligations(
        self, status: str = "active"
    ) -> List[LifecycleObligation]:
        try:
            store = self._memory_store()
            result: List[LifecycleObligation] = []
            for category in ("open_loop", "reader_promise"):
                for item in store.query(category=category, status=status):
                    payload = item.payload or {}
                    result.append(
                        LifecycleObligation(
                            id=str(payload.get("lifecycle_id") or item.id),
                            category=category,
                            content=item.value,
                            status=item.status,
                            source_chapter=item.source_chapter,
                            expected_payoff=str(payload.get("expected_payoff") or ""),
                            urgency=coerce_urgency(payload.get("urgency")),
                        )
                    )
            result.sort(key=lambda item: (item.category, item.source_chapter, item.id))
            return result
        except Exception as e:
            logger.warning("get_lifecycle_obligations failed: %s", e)
            return []

    def get_timeline(self, from_ch: int, to_ch: int) -> List[TimelineEvent]:
        try:
            store = self._memory_store()
            items = store.query(category="timeline", status="active")
            ordered_events = []
            for item in items:
                ch = item.source_chapter
                if from_ch <= ch <= to_ch:
                    payload = item.payload or {}
                    try:
                        sequence = int(payload.get("sequence") or 0)
                    except (TypeError, ValueError):
                        sequence = 0
                    timeline_id = str(payload.get("timeline_id") or item.id)
                    ordered_events.append(
                        (
                            ch,
                            sequence,
                            timeline_id,
                            TimelineEvent(
                                event=item.value,
                                chapter=ch,
                                time_hint=str(payload.get("time_hint") or "").strip(),
                                event_type=str(payload.get("event_type") or item.subject).strip(),
                            ),
                        )
                    )
            ordered_events.sort(key=lambda row: (row[0], row[1], row[2]))
            return [row[3] for row in ordered_events]
        except Exception as e:
            logger.warning("get_timeline failed: %s", e)
            return []

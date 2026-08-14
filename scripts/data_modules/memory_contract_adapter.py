#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MemoryContractAdapter——薄适配器，包装现有模块满足 MemoryContract Protocol。

不做存储重构，仅委托给 StateManager / IndexManager / ScratchpadManager 等。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .chapter_commit_service import ChapterCommitService
from .canonical_history import (
    export_asof_snapshot,
    latest_canonical_chapter,
    load_canonical_history,
)
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
            "schema_version": "canon-ledger-context-pack/v2",
            "budget": {**budget, "used_tokens": used},
            "completeness": completeness,
        }
        measured = _estimate_tokens(payload)
        if measured == used:
            break
        used = measured
    return used


class MemoryContractAdapter:
    """满足 MemoryContract Protocol 的具体实现。"""

    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()

    # ------------------------------------------------------------------
    # 内部懒加载（避免在构造时就初始化所有重量级模块）
    # ------------------------------------------------------------------

    def _memory_orchestrator(self):
        from .memory.orchestrator import MemoryOrchestrator
        return MemoryOrchestrator(self.config)

    # ------------------------------------------------------------------
    # 契约方法
    # ------------------------------------------------------------------

    def commit_chapter(self, chapter: int, result: dict) -> CommitResult:
        required = {
            "review_result",
            "fulfillment_result",
            "disambiguation_result",
            "extraction_result",
        }
        if not isinstance(result, dict) or not required.issubset(result):
            raise ValueError("章节提交必须提供当前 CanonLedger 的四个绑定工件")
        return self._commit_chapter_mainline(chapter, result)

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

        summary_file = self.config.canon_ledger_dir / "summaries" / f"ch{chapter:04d}.md"
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

    def load_context(self, chapter: int, budget_tokens: int = 4000) -> ContextPack:
        requested_tokens = max(1, int(budget_tokens or 1))
        mandatory: Dict[str, Any] = {}
        optional: Dict[str, Any] = {}
        source_status: Dict[str, Dict[str, str]] = {}
        missing_sources: List[str] = []
        omitted_hard_ids: List[str] = []
        history_as_of = max(0, int(chapter) - 1)
        runtime_sources = load_runtime_sources(
            self.config.project_root,
            chapter,
            history_as_of,
        )
        canonical_history = load_canonical_history(
            self.config.project_root,
            history_as_of,
        )

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
            "history_as_of_chapter": history_as_of,
            "canonical_chapters": list(canonical_history.valid_chapters),
        }
        mandatory["latest_commit"] = (
            commit_status_view(getattr(runtime_sources, "latest_commit", None)) or {}
        )
        contract_failures = [
            str(item)
            for item in (getattr(runtime_sources, "fallback_sources", []) or [])
            if str(item).startswith(("missing_", "invalid_", "stale_"))
        ]
        if contract_failures:
            source_status["story_contracts"] = {
                "status": "error",
                "reason": ",".join(contract_failures),
            }
            missing_sources.extend(contract_failures)
        else:
            source_status["story_contracts"] = {"status": "ok"}

        if canonical_history.invalid_sources:
            source_status["canonical_history"] = {
                "status": "error",
                "reason": ",".join(canonical_history.invalid_sources),
            }
            missing_sources.extend(canonical_history.invalid_sources)
        else:
            source_status["canonical_history"] = {
                "status": "ok",
                "as_of_chapter": str(history_as_of),
            }
        omitted_hard_ids.extend(canonical_history.omitted_fact_ids)

        # 1. MemoryOrchestrator 基础包
        memory_pack: Dict[str, Any] = {}
        try:
            orch = self._memory_orchestrator()
            memory_pack = orch.build_memory_pack(chapter, include_soft=False)
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

        scratchpad_hard, hard_error = normalize_hard_constraints(memory_pack)
        if hard_error:
            source_status["scratchpad"] = {
                "status": "error",
                "reason": hard_error,
            }
            missing_sources.append("scratchpad")
            scratchpad_hard = []
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

        # 章节提交是章节事实源；暂存区只补充初始化事实。
        setup_hard = [
            row
            for row in scratchpad_hard
            if int((row or {}).get("source_chapter") or 0) == 0
        ]
        if scratchpad_hard:
            trusted_chapters = set(canonical_history.valid_chapters)
            for row in scratchpad_hard:
                source_chapter = int((row or {}).get("source_chapter") or 0)
                if source_chapter > 0 and source_chapter not in trusted_chapters:
                    omitted_hard_ids.append(str((row or {}).get("id") or "unbound_fact"))

        hard_constraints: List[Dict[str, Any]] = []
        seen_hard_ids: set[str] = set()
        for row in [*canonical_history.hard_constraints, *setup_hard]:
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id") or "")
            if not item_id or item_id in seen_hard_ids:
                continue
            seen_hard_ids.add(item_id)
            hard_constraints.append(row)

        mandatory["hard_constraints"] = hard_constraints
        mandatory["canonical_facts"] = list(canonical_history.canonical_facts)
        mandatory["knowledge"] = {
            "information": dict(canonical_history.information),
            "by_entity": dict(canonical_history.knowledge_by_entity),
        }
        mandatory["presence"] = {
            "current": dict(canonical_history.presence),
        }
        mandatory["custody"] = {
            "current": dict(canonical_history.custody),
        }
        mandatory["fact_coverage"] = dict(canonical_history.coverage)

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

        # state.json 是可丢弃的当前投影，写作上下文只读初始化合同和绑定提交。
        protagonist_setup = dict(
            canonical_history.initial_canon.get("protagonist") or {}
        )
        protagonist_name = str(protagonist_setup.get("name") or "")
        matched_entity = next(
            (
                entity
                for entity in canonical_history.entities.values()
                if protagonist_name
                and str(entity.get("name") or "") == protagonist_name
            ),
            None,
        )
        if matched_entity:
            protagonist_setup["entity"] = matched_entity
        if protagonist_setup:
            mandatory["protagonist"] = protagonist_setup
        mandatory["progress"] = {
            "as_of_chapter": history_as_of,
            "canonical_chapters": list(canonical_history.valid_chapters),
        }
        source_status["state"] = {
            "status": "excluded_projection",
            "reason": "canonical_history_used",
        }

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

    def _query_as_of(self, as_of_chapter: int | None) -> int:
        if as_of_chapter is not None:
            return max(0, int(as_of_chapter))
        return latest_canonical_chapter(self.config.project_root)

    def query_entity(
        self,
        entity_id: str,
        as_of_chapter: int | None = None,
    ) -> Optional[EntitySnapshot]:
        as_of = self._query_as_of(as_of_chapter)
        history = load_canonical_history(self.config.project_root, as_of)
        entity = history.entities.get(str(entity_id or "").strip())
        if entity:
            changes = [
                row
                for row in history.state_changes
                if row.get("entity_id") == entity.get("id")
            ]
            return EntitySnapshot(
                id=str(entity.get("id") or entity_id),
                name=str(entity.get("name") or entity_id),
                type=str(entity.get("type") or "角色"),
                tier=str(entity.get("tier") or "核心"),
                aliases=list(entity.get("aliases") or []),
                attributes=dict(entity.get("attributes") or {}),
                first_appearance=int(entity.get("first_appearance") or 0),
                last_appearance=int(entity.get("last_appearance") or 0),
                recent_state_changes=changes[-5:],
            )
        return None

    def query_rules(
        self,
        domain: str = "",
        as_of_chapter: int | None = None,
    ) -> List[Rule]:
        as_of = self._query_as_of(as_of_chapter)
        history = load_canonical_history(self.config.project_root, as_of)
        canonical_rules: List[Rule] = []
        for item in history.rules:
            if domain and item.get("subject") != domain and domain not in str(item.get("value") or ""):
                continue
            canonical_rules.append(
                Rule(
                    id=str(item.get("id") or ""),
                    subject=str(item.get("subject") or ""),
                    field=str(item.get("field") or ""),
                    value=str(item.get("value") or ""),
                    domain=str(item.get("subject") or ""),
                    source_chapter=int(item.get("source_chapter") or 0),
                )
            )
        return canonical_rules

    def read_summary(self, chapter: int) -> str:
        padded = f"{chapter:04d}"
        summary_file = self.config.canon_ledger_dir / "summaries" / f"ch{padded}.md"
        try:
            if summary_file.exists():
                return summary_file.read_text(encoding="utf-8")
            return ""
        except Exception as e:
            logger.warning("read_summary(%d) failed: %s", chapter, e)
            return ""

    def get_open_loops(
        self,
        status: str = "active",
        as_of_chapter: int | None = None,
    ) -> List[OpenLoop]:
        as_of = self._query_as_of(as_of_chapter)
        history = load_canonical_history(self.config.project_root, as_of)
        canonical = [
            OpenLoop(
                id=str(item.get("id") or ""),
                content=str(item.get("value") or ""),
                status="active",
                planted_chapter=int(item.get("source_chapter") or 0),
                expected_payoff=str((item.get("payload") or {}).get("expected_payoff") or ""),
                urgency=coerce_urgency((item.get("payload") or {}).get("urgency")),
            )
            for item in history.obligations
            if item.get("category") == "open_loop" and status == "active"
        ]
        return canonical

    def get_lifecycle_obligations(
        self,
        status: str = "active",
        as_of_chapter: int | None = None,
    ) -> List[LifecycleObligation]:
        as_of = self._query_as_of(as_of_chapter)
        history = load_canonical_history(self.config.project_root, as_of)
        canonical = [
            LifecycleObligation(
                id=str(item.get("id") or ""),
                category=str(item.get("category") or ""),
                content=str(item.get("value") or ""),
                status="active",
                source_chapter=int(item.get("source_chapter") or 0),
                expected_payoff=str((item.get("payload") or {}).get("expected_payoff") or ""),
                urgency=coerce_urgency((item.get("payload") or {}).get("urgency")),
            )
            for item in history.obligations
            if status == "active"
        ]
        canonical.sort(key=lambda item: (item.category, item.source_chapter, item.id))
        return canonical

    def get_timeline(
        self,
        from_ch: int,
        to_ch: int,
        as_of_chapter: int | None = None,
    ) -> List[TimelineEvent]:
        as_of = self._query_as_of(as_of_chapter)
        history = load_canonical_history(self.config.project_root, as_of)
        canonical = [
            TimelineEvent(
                event=str(item.get("value") or ""),
                chapter=int(item.get("source_chapter") or 0),
                time_hint=str((item.get("payload") or {}).get("time_hint") or ""),
                event_type=str((item.get("payload") or {}).get("event_type") or ""),
            )
            for item in history.timeline
            if int(from_ch) <= int(item.get("source_chapter") or 0) <= int(to_ch)
        ]
        return canonical

    def export_asof_snapshot(
        self,
        chapter: int | None = None,
        as_of_chapter: int | None = None,
    ) -> Dict[str, Any]:
        """Export an immutable as-of snapshot for reviewer / data-agent."""
        return export_asof_snapshot(
            self.config.project_root,
            chapter=chapter,
            as_of_chapter=as_of_chapter,
        )

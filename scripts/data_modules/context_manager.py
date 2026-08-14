#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ContextManager - assemble context packs with weighted priorities.
"""
from __future__ import annotations

import json
import re
import sys
import logging
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio
from typing import Any, Dict, List, Optional

try:
    from chapter_outline_loader import load_chapter_outline
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import load_chapter_outline

from .config import get_config
from .index_manager import IndexManager, WritingChecklistScoreMeta
from .context_ranker import ContextRanker
from .prewrite_validator import PrewriteValidator
from .story_contracts import read_json_if_exists
from .story_runtime_sources import (
    RuntimeSourceSnapshot,
    commit_status_view,
    load_runtime_sources,
)
from .context_weights import (
    DEFAULT_TEMPLATE as CONTEXT_DEFAULT_TEMPLATE,
    TEMPLATE_WEIGHTS as CONTEXT_TEMPLATE_WEIGHTS,
    TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT as CONTEXT_TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT,
)
from .genre_aliases import normalize_genre_token, to_profile_key
from .genre_profile_builder import (
    build_composite_genre_hints,
    extract_genre_section,
    extract_markdown_refs,
    parse_genre_tokens,
)
from .consistency_context import sanitize_story_contracts
from .memory.hard_constraints import normalize_hard_constraints
from .fact_text import sanitize_fact_text
from .state_snapshot import has_projected_state_content, state_as_of_chapter
from .writing_guidance_builder import (
    build_methodology_guidance_items,
    build_methodology_strategy_card,
    build_guidance_items,
    build_writing_checklist,
    is_checklist_item_completed,
)


logger = logging.getLogger(__name__)


class ContextManager:
    DEFAULT_TEMPLATE = CONTEXT_DEFAULT_TEMPLATE
    TEMPLATE_WEIGHTS = CONTEXT_TEMPLATE_WEIGHTS
    TEMPLATE_WEIGHTS_DYNAMIC = CONTEXT_TEMPLATE_WEIGHTS_DYNAMIC_DEFAULT
    EXTRA_SECTIONS = {
        "story_skeleton",
        "memory",
        "long_term_memory",
        "preferences",
        "alerts",
        "reader_signal",
        "genre_profile",
        "writing_guidance",
        "plot_structure",
        "story_contract",
        "runtime_status",
        "latest_commit",
        "prewrite_validation",
        "context_completeness",
        "hard_constraints",
    }
    SECTION_ORDER = [
        "core",
        "story_contract",
        "runtime_status",
        "latest_commit",
        "prewrite_validation",
        "context_completeness",
        "hard_constraints",
        "scene",
        "global",
        "reader_signal",
        "genre_profile",
        "writing_guidance",
        "plot_structure",
        "story_skeleton",
        "memory",
        "long_term_memory",
        "preferences",
        "alerts",
    ]
    SUMMARY_SECTION_RE = re.compile(r"##\s*剧情摘要\s*\r?\n(.*?)(?=\r?\n##|\Z)", re.DOTALL)

    def __init__(self, config=None):
        self.config = config or get_config()
        self._index_manager: IndexManager | None = None
        self.context_ranker = ContextRanker(self.config)
        self._state_load_error = ""

    @property
    def index_manager(self) -> IndexManager:
        """Initialize SQLite only when a caller actually needs indexed data."""
        if self._index_manager is None:
            self._index_manager = IndexManager(self.config)
        return self._index_manager

    def _index_exists(self) -> bool:
        return Path(self.config.index_db).is_file()

    def build_context(
        self,
        chapter: int,
        template: str | None = None,
        max_chars: Optional[int] = None,
    ) -> Dict[str, Any]:
        template = template or self.DEFAULT_TEMPLATE
        self._active_template = template
        if template not in self.TEMPLATE_WEIGHTS:
            template = self.DEFAULT_TEMPLATE
            self._active_template = template

        pack = self._build_pack(chapter)
        if getattr(self.config, "context_ranker_enabled", True):
            pack = self.context_ranker.rank_pack(pack, chapter)

        return self._assemble_json_payload(pack, template=template)

    def _assemble_json_payload(self, pack: Dict[str, Any], template: str = DEFAULT_TEMPLATE) -> Dict[str, Any]:
        chapter = int((pack.get("meta") or {}).get("chapter") or 0)
        weights = self._resolve_template_weights(template=template, chapter=chapter)

        payload: Dict[str, Any] = {
            "meta": {
                **(pack.get("meta") or {}),
                "context_contract_version": "v4",
            },
        }
        for section_name in self.SECTION_ORDER:
            if section_name in pack and section_name != "global":
                content = pack[section_name]
                weight = weights.get(section_name, 0.0)
                if weight > 0 or section_name in self.EXTRA_SECTIONS:
                    payload[section_name] = content

        if chapter > 0:
            payload["meta"]["context_weight_stage"] = self._resolve_context_stage(chapter)

        return payload

    def filter_invalid_items(self, items: List[Dict[str, Any]], source_type: str, id_key: str) -> List[Dict[str, Any]]:
        if not items or not self._index_exists():
            return list(items)
        confirmed = self.index_manager.get_invalid_ids(source_type, status="confirmed")
        pending = self.index_manager.get_invalid_ids(source_type, status="pending")
        result = []
        for item in items:
            item_id = str(item.get(id_key, ""))
            if item_id in confirmed:
                continue
            if item_id in pending:
                item = dict(item)
                item["warning"] = "pending_invalid"
            result.append(item)
        return result

    def apply_confidence_filter(self, items: List[Dict[str, Any]], min_confidence: float) -> List[Dict[str, Any]]:
        filtered: List[Dict[str, Any]] = []
        for item in items:
            conf = item.get("confidence")
            if conf is None or conf >= min_confidence:
                filtered.append(item)
        return filtered

    def _build_pack(self, chapter: int) -> Dict[str, Any]:
        state = self._load_state()
        runtime_sources = load_runtime_sources(self.config.project_root, chapter)
        use_orchestrator = bool(getattr(self.config, "context_use_memory_orchestrator", False))
        state_as_of_chapter, state_as_of_valid = self._state_as_of_chapter(state)
        if self._state_load_error:
            state_as_of_chapter, state_as_of_valid = None, False
        state_snapshot_safe = (
            state_as_of_valid
            and (
                state_as_of_chapter == 0
                or state_as_of_chapter < int(chapter)
            )
        )

        orchestrator_pack: Dict[str, Any] = {}
        orchestrator_error: Exception | None = None
        try:
            from .memory.orchestrator import MemoryOrchestrator

            # Hard constraints are a read contract, not an optional context
            # enhancement.  Always perform this single read; the feature flag
            # below controls only whether budgeted soft memory is exposed or
            # allowed to expose budgeted soft-memory sections.
            orchestrator = MemoryOrchestrator(self.config)
            orchestrator_pack = orchestrator.build_memory_pack(
                chapter,
                include_soft=use_orchestrator,
            )
            if not isinstance(orchestrator_pack, dict):
                raise TypeError("memory_pack_must_be_object")
        except Exception as exc:
            logger.warning("memory_orchestrator_failed: %s", exc)
            orchestrator_error = exc

        hard_constraints, hard_constraints_error = normalize_hard_constraints(
            orchestrator_pack
        )
        omitted_hard_ids, unsafe_hard_warning = self._unsafe_hard_constraint_ids(
            orchestrator_pack
        )
        missing_sources: List[str] = []
        blocked_reasons: List[str] = []
        source_status: Dict[str, Dict[str, Any]] = {}

        if orchestrator_error is not None:
            missing_sources.append("scratchpad")
            blocked_reasons.append("memory_orchestrator_unavailable")
            source_status["scratchpad"] = {
                "status": "error",
                "reason": orchestrator_error.__class__.__name__,
            }
        elif hard_constraints_error:
            missing_sources.append("scratchpad")
            blocked_reasons.append(hard_constraints_error)
            source_status["scratchpad"] = {
                "status": "error",
                "reason": hard_constraints_error,
            }
        elif unsafe_hard_warning:
            blocked_reasons.append("unsafe_hard_constraint")
            source_status["scratchpad"] = {
                "status": "blocked",
                "reason": "unsafe_hard_constraint",
            }
        else:
            source_status["scratchpad"] = {"status": "ok"}

        source_status["state"] = {
            "status": "ok" if state_snapshot_safe else "blocked",
            "state_as_of_chapter": state_as_of_chapter,
        }
        if not state_snapshot_safe:
            state_reason = (
                "invalid_state_as_of_chapter"
                if not state_as_of_valid
                else "state_snapshot_not_before_target"
            )
            source_status["state"]["reason"] = state_reason
            blocked_reasons.append(state_reason)

        context_completeness = {
            "status": "blocked" if blocked_reasons else "complete",
            "state_as_of_chapter": state_as_of_chapter,
            "missing_sources": missing_sources,
            "omitted_hard_ids": omitted_hard_ids,
            "blocked_reasons": blocked_reasons,
            "source_status": source_status,
        }

        core = {
            "chapter_outline": self._load_outline(chapter),
            "protagonist_snapshot": (
                state.get("protagonist_state", {}) if state_snapshot_safe else {}
            ),
            # Free-form model summaries are not a trusted control boundary.
            # Structured events/state and fact-only RAG carry continuity.
            "recent_summaries": [],
            "recent_meta": self._load_recent_meta(
                state,
                chapter,
                window=self.config.context_recent_meta_window,
            ) if state_snapshot_safe else [],
        }
        if use_orchestrator and orchestrator_pack:
            working_items = [
                item
                for item in (orchestrator_pack.get("working_memory") or [])
                if isinstance(item, dict)
                and (
                    state_snapshot_safe
                    or str(item.get("source") or "") != "state_export"
                )
            ]
            outline_item = next((x for x in working_items if x.get("source") == "outline"), None)
            state_item = next((x for x in working_items if x.get("source") == "state_export"), None)
            core["chapter_outline"] = str(outline_item.get("content", "")) if outline_item else core["chapter_outline"]
            if (
                state_snapshot_safe
                and isinstance(state_item, dict)
                and isinstance(state_item.get("content"), dict)
            ):
                state_export = dict(state_item.get("content") or {})
                core["protagonist_snapshot"] = state_export.get("protagonist_state", core["protagonist_snapshot"])

        scene = {
            "location_context": (
                state.get("protagonist_state", {}).get("location", {})
                if state_snapshot_safe
                else {}
            ),
            "appearing_characters": self._load_recent_appearances(
                chapter=chapter,
                limit=self.config.context_max_appearing_characters,
            ),
        }
        scene["appearing_characters"] = self.filter_invalid_items(
            scene["appearing_characters"], source_type="entity", id_key="entity_id"
        )
        story_contract = self._build_story_contract_from_runtime(runtime_sources)
        runtime_status = {
            "chapter": int(runtime_sources.chapter),
            "fallback_sources": list(runtime_sources.fallback_sources),
            "primary_write_source": str(runtime_sources.primary_write_source),
            "latest_commit": commit_status_view(runtime_sources.latest_commit),
            "latest_accepted_commit": commit_status_view(
                runtime_sources.latest_accepted_commit
            ),
        }
        latest_commit = commit_status_view(runtime_sources.latest_commit) or {}

        global_ctx = {
            "worldview_skeleton": self._load_setting("世界观"),
            "power_system_skeleton": self._load_setting("力量体系"),
        }

        preferences = self._load_json_optional(self.config.canon_ledger_dir / "preferences.json")
        # 默认写作上下文不注入无类型的自由文本项目记忆。
        memory: Dict[str, Any] = {}
        long_term_memory = self._long_term_memory_from_orchestrator_pack(
            orchestrator_pack,
            include_soft=use_orchestrator,
            include_state_snapshot=state_snapshot_safe,
        )
        story_skeleton: List[Dict[str, Any]] = []
        alert_slice = max(0, int(self.config.context_alerts_slice))
        reader_signal = self._load_reader_signal(chapter)
        genre_profile = self._build_runtime_genre_profile(state, story_contract)
        writing_guidance = self._build_writing_guidance(chapter, reader_signal, genre_profile)
        plot_structure = self._plot_structure_from_story_contract(story_contract)
        prewrite_validation = PrewriteValidator(self.config.project_root).build(
            chapter=chapter,
            review_contract=story_contract.get("review_contract") or {},
            plot_structure=plot_structure,
            story_contract=story_contract,
            state_snapshot=state if state_snapshot_safe else {},
        )
        if not state_snapshot_safe:
            reason = "state snapshot is not trusted for the requested chapter"
            reasons = list(prewrite_validation.get("blocking_reasons") or [])
            if reason not in reasons:
                reasons.append(reason)
            prewrite_validation["blocking"] = True
            prewrite_validation["blocking_reasons"] = reasons

        return {
            "meta": {"chapter": chapter},
            "core": core,
            "story_contract": story_contract,
            "runtime_status": runtime_status,
            "latest_commit": latest_commit,
            "prewrite_validation": prewrite_validation,
            "context_completeness": context_completeness,
            "hard_constraints": hard_constraints,
            "scene": scene,
            "global": global_ctx,
            "reader_signal": reader_signal,
            "genre_profile": genre_profile,
            "writing_guidance": writing_guidance,
            "plot_structure": plot_structure,
            "story_skeleton": story_skeleton,
            "preferences": preferences,
            "memory": memory,
            "long_term_memory": long_term_memory,
            "alerts": {
                "disambiguation_warnings": (
                    state.get("disambiguation_warnings", [])[-alert_slice:]
                    if state_snapshot_safe and alert_slice
                    else []
                ),
                "disambiguation_pending": (
                    state.get("disambiguation_pending", [])[-alert_slice:]
                    if state_snapshot_safe and alert_slice
                    else []
                ),
            },
        }

    @staticmethod
    def _state_as_of_chapter(state: Dict[str, Any]) -> tuple[Any, bool]:
        return state_as_of_chapter(state)

    @staticmethod
    def _has_projected_state_content(state: Dict[str, Any]) -> bool:
        return has_projected_state_content(state)

    @staticmethod
    def _unsafe_hard_constraint_ids(
        memory_pack: Dict[str, Any],
    ) -> tuple[List[str], bool]:
        if not isinstance(memory_pack, dict):
            return [], False
        omitted: List[str] = []
        unsafe_warning = False
        for warning in memory_pack.get("warnings") or []:
            if not isinstance(warning, dict):
                continue
            if str(warning.get("type") or "") != "unsafe_hard_constraint":
                continue
            unsafe_warning = True
            for item_id in warning.get("ids") or []:
                normalized = str(item_id or "").strip()
                if normalized and normalized not in omitted:
                    omitted.append(normalized)
        return omitted, unsafe_warning

    @staticmethod
    def _long_term_memory_from_orchestrator_pack(
        memory_pack: Dict[str, Any],
        *,
        include_soft: bool,
        include_state_snapshot: bool,
    ) -> Dict[str, Any]:
        """Expose only budgeted soft memory; hard facts have one canonical key."""
        if not include_soft or not isinstance(memory_pack, dict):
            return {}
        payload: Dict[str, Any] = {
            key: value
            for key, value in memory_pack.items()
            if key != "hard_constraints"
        }
        payload["working_memory"] = [
            item
            for item in (payload.get("working_memory") or [])
            if isinstance(item, dict)
            and str(item.get("source") or "") != "summary"
        ]
        if not include_state_snapshot:
            payload["working_memory"] = [
                item
                for item in (payload.get("working_memory") or [])
                if isinstance(item, dict)
                and str(item.get("source") or "") != "state_export"
            ]
        return payload

    def _load_reader_signal(self, chapter: int) -> Dict[str, Any]:
        if not getattr(self.config, "context_reader_signal_enabled", False):
            return {}

        recent_limit = max(1, int(getattr(self.config, "context_reader_signal_recent_limit", 5)))
        pattern_window = max(1, int(getattr(self.config, "context_reader_signal_window_chapters", 20)))
        review_window = max(1, int(getattr(self.config, "context_reader_signal_review_window", 5)))
        include_debt = bool(getattr(self.config, "context_reader_signal_include_debt", False))

        recent_power = self.index_manager.get_recent_reading_power(limit=recent_limit)
        pattern_stats = self.index_manager.get_pattern_usage_stats(last_n_chapters=pattern_window)
        hook_stats = self.index_manager.get_hook_type_stats(last_n_chapters=pattern_window)
        review_trend = self.index_manager.get_review_trend_stats(last_n=review_window)

        low_score_ranges: List[Dict[str, Any]] = []
        for row in review_trend.get("recent_ranges", []):
            score = row.get("overall_score")
            notes = row.get("notes", "")
            has_blocking = "blocking=" in notes and "blocking=0" not in notes
            is_low_score = isinstance(score, (int, float)) and float(score) < 75
            if is_low_score or has_blocking:
                low_score_ranges.append(
                    {
                        "start_chapter": row.get("start_chapter"),
                        "end_chapter": row.get("end_chapter"),
                        "overall_score": score if isinstance(score, (int, float)) else 0.0,
                        "notes": notes,
                    }
                )

        signal: Dict[str, Any] = {
            "recent_reading_power": recent_power,
            "pattern_usage": pattern_stats,
            "hook_type_usage": hook_stats,
            "review_trend": review_trend,
            "low_score_ranges": low_score_ranges,
            "next_chapter": chapter,
        }

        if include_debt:
            signal["debt_summary"] = self.index_manager.get_debt_summary()

        return signal

    def _load_genre_profile(self, genre_raw: str) -> Dict[str, Any]:
        if not getattr(self.config, "context_genre_profile_enabled", False):
            return {}

        genre_raw = str(genre_raw or "").strip()
        genres = self._parse_genre_tokens(genre_raw)
        if not genres:
            return {}
        max_genres = max(1, int(getattr(self.config, "context_genre_profile_max_genres", 2)))
        genres = genres[:max_genres]

        primary_genre = genres[0]
        secondary_genres = genres[1:]
        composite = len(genres) > 1
        profile_path = self.config.project_root / ".cursor" / "references" / "genre-profiles.md"
        taxonomy_path = self.config.project_root / ".cursor" / "references" / "reading-power-taxonomy.md"

        profile_text = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
        taxonomy_text = taxonomy_path.read_text(encoding="utf-8") if taxonomy_path.exists() else ""

        profile_excerpt = self._extract_genre_section(profile_text, primary_genre)
        taxonomy_excerpt = self._extract_genre_section(taxonomy_text, primary_genre)

        secondary_profiles: List[str] = []
        secondary_taxonomies: List[str] = []
        for extra in secondary_genres:
            secondary_profiles.append(self._extract_genre_section(profile_text, extra))
            secondary_taxonomies.append(self._extract_genre_section(taxonomy_text, extra))

        refs = self._extract_markdown_refs(
            "\n".join([profile_excerpt] + secondary_profiles),
            max_items=int(getattr(self.config, "context_genre_profile_max_refs", 8)),
        )

        composite_hints = self._build_composite_genre_hints(genres, refs)

        return {
            "genre": primary_genre,
            "genre_raw": genre_raw,
            "genres": genres,
            "composite": composite,
            "secondary_genres": secondary_genres,
            "profile_excerpt": profile_excerpt,
            "taxonomy_excerpt": taxonomy_excerpt,
            "secondary_profile_excerpts": secondary_profiles,
            "secondary_taxonomy_excerpts": secondary_taxonomies,
            "reference_hints": refs,
            "composite_hints": composite_hints,
        }

    def _build_runtime_genre_profile(
        self,
        state: Dict[str, Any],
        story_contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        primary_genre = str(
            (
                ((story_contract.get("master_setting") or {}).get("route") or {}).get("primary_genre")
                or ""
            )
        ).strip()
        if not getattr(self.config, "context_genre_profile_enabled", False):
            if primary_genre:
                return {"genre": primary_genre, "mode": "label_only"}
            return {}

        if not primary_genre:
            return {}

        runtime_profile = self._load_genre_profile(primary_genre)
        runtime_profile = dict(runtime_profile or {})
        runtime_profile.setdefault("genre", primary_genre)
        runtime_profile.setdefault("genre_raw", primary_genre)
        runtime_profile.setdefault("genres", [primary_genre])
        runtime_profile.setdefault("secondary_genres", [])
        runtime_profile.setdefault("composite", len(runtime_profile.get("genres") or []) > 1)
        runtime_profile.setdefault("reference_hints", [])
        runtime_profile.setdefault("composite_hints", [])
        runtime_profile["mode"] = "contract_first"

        return runtime_profile

    def _build_writing_guidance(
        self,
        chapter: int,
        reader_signal: Dict[str, Any],
        genre_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not getattr(self.config, "context_writing_guidance_enabled", False):
            return {
                "enabled": False,
                "guidance_items": [],
                "checklist": [],
                "checklist_score": {},
            }

        limit = max(1, int(getattr(self.config, "context_writing_guidance_max_items", 6)))
        low_score_threshold = float(
            getattr(self.config, "context_writing_guidance_low_score_threshold", 75.0)
        )

        guidance_bundle = build_guidance_items(
            chapter=chapter,
            reader_signal=reader_signal,
            genre_profile=genre_profile,
            low_score_threshold=low_score_threshold,
            hook_diversify_enabled=bool(
                getattr(self.config, "context_writing_guidance_hook_diversify", True)
            ),
        )

        guidance = list(guidance_bundle.get("guidance") or [])
        methodology_strategy: Dict[str, Any] = {}

        if self._is_methodology_enabled_for_genre(genre_profile):
            methodology_strategy = build_methodology_strategy_card(
                chapter=chapter,
                reader_signal=reader_signal,
                genre_profile=genre_profile,
                label=str(getattr(self.config, "context_methodology_label", "digital-serial-v1")),
            )
            guidance.extend(build_methodology_guidance_items(methodology_strategy))

        checklist = self._build_writing_checklist(
            chapter=chapter,
            guidance_items=guidance,
            reader_signal=reader_signal,
            genre_profile=genre_profile,
            strategy_card=methodology_strategy,
        )

        checklist_score = self._compute_writing_checklist_score(
            chapter=chapter,
            checklist=checklist,
            reader_signal=reader_signal,
        )

        if getattr(self.config, "context_writing_score_persist_enabled", True):
            self._persist_writing_checklist_score(checklist_score)

        low_ranges = guidance_bundle.get("low_ranges") or []
        hook_usage = guidance_bundle.get("hook_usage") or {}
        pattern_usage = guidance_bundle.get("pattern_usage") or {}
        genre = str(guidance_bundle.get("genre") or genre_profile.get("genre") or "").strip()

        hook_types = list(hook_usage.keys())[:3] if isinstance(hook_usage, dict) else []
        top_patterns = (
            sorted(pattern_usage, key=pattern_usage.get, reverse=True)[:3]
            if isinstance(pattern_usage, dict)
            else []
        )

        return {
            "chapter": chapter,
            "guidance_items": guidance[:limit],
            "checklist": checklist,
            "checklist_score": checklist_score,
            "methodology": methodology_strategy,
            "signals_used": {
                "has_low_score_ranges": bool(low_ranges),
                "hook_types": hook_types,
                "top_patterns": top_patterns,
                "genre": genre,
                "methodology_enabled": bool(methodology_strategy.get("enabled")),
            },
        }

    def _compute_writing_checklist_score(
        self,
        chapter: int,
        checklist: List[Dict[str, Any]],
        reader_signal: Dict[str, Any],
    ) -> Dict[str, Any]:
        total_items = len(checklist)
        required_items = 0
        completed_items = 0
        completed_required = 0
        total_weight = 0.0
        completed_weight = 0.0
        pending_labels: List[str] = []

        for item in checklist:
            if not isinstance(item, dict):
                continue
            required = bool(item.get("required"))
            weight = float(item.get("weight") or 1.0)
            total_weight += weight
            if required:
                required_items += 1

            completed = self._is_checklist_item_completed(item, reader_signal)
            if completed:
                completed_items += 1
                completed_weight += weight
                if required:
                    completed_required += 1
            else:
                pending_labels.append(str(item.get("label") or item.get("id") or "未命名项"))

        completion_rate = (completed_items / total_items) if total_items > 0 else 1.0
        weighted_rate = (completed_weight / total_weight) if total_weight > 0 else completion_rate
        required_rate = (completed_required / required_items) if required_items > 0 else 1.0

        score = 100.0 * (0.5 * weighted_rate + 0.3 * required_rate + 0.2 * completion_rate)

        if getattr(self.config, "context_writing_score_include_reader_trend", True):
            trend_window = max(1, int(getattr(self.config, "context_writing_score_trend_window", 10)))
            trend = self.index_manager.get_writing_checklist_score_trend(last_n=trend_window)
            baseline = float(trend.get("score_avg") or 0.0)
            if baseline > 0:
                score += max(-10.0, min(10.0, (score - baseline) * 0.1))

        score = round(max(0.0, min(100.0, score)), 2)

        return {
            "chapter": chapter,
            "score": score,
            "completion_rate": round(completion_rate, 4),
            "weighted_completion_rate": round(weighted_rate, 4),
            "required_completion_rate": round(required_rate, 4),
            "total_items": total_items,
            "required_items": required_items,
            "completed_items": completed_items,
            "completed_required": completed_required,
            "total_weight": round(total_weight, 2),
            "completed_weight": round(completed_weight, 2),
            "pending_items": pending_labels,
            "trend_window": int(getattr(self.config, "context_writing_score_trend_window", 10)),
        }

    def _is_checklist_item_completed(self, item: Dict[str, Any], reader_signal: Dict[str, Any]) -> bool:
        return is_checklist_item_completed(item, reader_signal)

    def _persist_writing_checklist_score(self, checklist_score: Dict[str, Any]) -> None:
        if not checklist_score:
            return
        try:
            self.index_manager.save_writing_checklist_score(
                WritingChecklistScoreMeta(
                    chapter=int(checklist_score.get("chapter") or 0),
                    template=str(getattr(self, "_active_template", self.DEFAULT_TEMPLATE) or self.DEFAULT_TEMPLATE),
                    total_items=int(checklist_score.get("total_items") or 0),
                    required_items=int(checklist_score.get("required_items") or 0),
                    completed_items=int(checklist_score.get("completed_items") or 0),
                    completed_required=int(checklist_score.get("completed_required") or 0),
                    total_weight=float(checklist_score.get("total_weight") or 0.0),
                    completed_weight=float(checklist_score.get("completed_weight") or 0.0),
                    completion_rate=float(checklist_score.get("completion_rate") or 0.0),
                    score=float(checklist_score.get("score") or 0.0),
                    score_breakdown={
                        "weighted_completion_rate": checklist_score.get("weighted_completion_rate"),
                        "required_completion_rate": checklist_score.get("required_completion_rate"),
                        "trend_window": checklist_score.get("trend_window"),
                    },
                    pending_items=list(checklist_score.get("pending_items") or []),
                    source="context_manager",
                )
            )
        except Exception as exc:
            logger.warning("failed to persist writing checklist score: %s", exc)

    def _resolve_context_stage(self, chapter: int) -> str:
        early = max(1, int(getattr(self.config, "context_dynamic_budget_early_chapter", 30)))
        late = max(early + 1, int(getattr(self.config, "context_dynamic_budget_late_chapter", 120)))
        if chapter <= early:
            return "early"
        if chapter >= late:
            return "late"
        return "mid"

    def _resolve_template_weights(self, template: str, chapter: int) -> Dict[str, float]:
        template_key = template if template in self.TEMPLATE_WEIGHTS else self.DEFAULT_TEMPLATE
        base = dict(self.TEMPLATE_WEIGHTS.get(template_key, self.TEMPLATE_WEIGHTS[self.DEFAULT_TEMPLATE]))
        if not getattr(self.config, "context_dynamic_budget_enabled", True):
            return base

        stage = self._resolve_context_stage(chapter)
        dynamic_weights = getattr(self.config, "context_template_weights_dynamic", None)
        if not isinstance(dynamic_weights, dict):
            dynamic_weights = self.TEMPLATE_WEIGHTS_DYNAMIC

        stage_weights = dynamic_weights.get(stage, {}) if isinstance(dynamic_weights.get(stage, {}), dict) else {}
        staged = stage_weights.get(template_key)
        if isinstance(staged, dict):
            return dict(staged)

        return base

    def _parse_genre_tokens(self, genre_raw: str) -> List[str]:
        support_composite = bool(getattr(self.config, "context_genre_profile_support_composite", True))
        separators_raw = getattr(self.config, "context_genre_profile_separators", ("+", "/", "|", ","))
        separators = tuple(str(token) for token in separators_raw if str(token))
        return parse_genre_tokens(
            genre_raw,
            support_composite=support_composite,
            separators=separators,
        )

    def _normalize_genre_token(self, token: str) -> str:
        return normalize_genre_token(token)

    def _build_composite_genre_hints(self, genres: List[str], refs: List[str]) -> List[str]:
        return build_composite_genre_hints(genres, refs)

    def _build_writing_checklist(
        self,
        chapter: int,
        guidance_items: List[str],
        reader_signal: Dict[str, Any],
        genre_profile: Dict[str, Any],
        strategy_card: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        _ = chapter
        if not getattr(self.config, "context_writing_checklist_enabled", True):
            return []

        min_items = max(1, int(getattr(self.config, "context_writing_checklist_min_items", 3)))
        max_items = max(min_items, int(getattr(self.config, "context_writing_checklist_max_items", 6)))
        default_weight = float(getattr(self.config, "context_writing_checklist_default_weight", 1.0))
        if default_weight <= 0:
            default_weight = 1.0

        return build_writing_checklist(
            guidance_items=guidance_items,
            reader_signal=reader_signal,
            genre_profile=genre_profile,
            strategy_card=strategy_card,
            min_items=min_items,
            max_items=max_items,
            default_weight=default_weight,
        )

    def _is_methodology_enabled_for_genre(self, genre_profile: Dict[str, Any]) -> bool:
        if not bool(getattr(self.config, "context_methodology_enabled", False)):
            return False

        whitelist_raw = getattr(self.config, "context_methodology_genre_whitelist", ("*",))
        if isinstance(whitelist_raw, str):
            whitelist_iter = [whitelist_raw]
        else:
            whitelist_iter = list(whitelist_raw or [])

        whitelist = {str(token).strip().lower() for token in whitelist_iter if str(token).strip()}
        if not whitelist:
            return True
        if "*" in whitelist or "all" in whitelist:
            return True

        genre = str((genre_profile or {}).get("genre") or "").strip()
        if not genre:
            return False

        profile_key = to_profile_key(genre)
        return profile_key in whitelist

    def _extract_genre_section(self, text: str, genre: str) -> str:
        return extract_genre_section(text, genre)

    def _extract_markdown_refs(self, text: str, max_items: int = 8) -> List[str]:
        return extract_markdown_refs(text, max_items=max_items)

    def _load_state(self) -> Dict[str, Any]:
        path = self.config.state_file
        if not path.exists():
            self._state_load_error = ""
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("state_root_must_be_object")
            self._state_load_error = ""
            return payload
        except Exception as exc:
            self._state_load_error = exc.__class__.__name__
            logger.warning("state_load_failed: %s", exc)
            return {}

    def _load_outline(self, chapter: int) -> str:
        return load_chapter_outline(self.config.project_root, chapter, max_chars=1500)

    @staticmethod
    def _plot_structure_from_story_contract(
        story_contract: Dict[str, Any],
    ) -> Dict[str, Any]:
        chapter_brief = story_contract.get("chapter_brief") or {}
        directive = chapter_brief.get("chapter_directive") or {}
        if not isinstance(directive, dict) or not directive:
            return {}
        return {
            "cbn": str(directive.get("cbn") or ""),
            "cpns": list(directive.get("cpns") or []),
            "cen": str(directive.get("cen") or ""),
            "must_cover_nodes": list(directive.get("must_cover_nodes") or []),
            "forbidden_zones": list(directive.get("forbidden_zones") or []),
            "source": str(directive.get("source") or ""),
        }

    def _build_story_contract_from_runtime(self, runtime_sources: RuntimeSourceSnapshot) -> Dict[str, Any]:
        story_root = self.config.story_system_dir
        return sanitize_story_contracts(
            {
                "master_setting": runtime_sources.contracts.get("master") or {},
                "chapter_brief": runtime_sources.contracts.get("chapter") or {},
                "volume_brief": runtime_sources.contracts.get("volume") or {},
                "review_contract": runtime_sources.contracts.get("review") or {},
                "anti_patterns": read_json_if_exists(story_root / "anti_patterns.json") or [],
            }
        )

    def _load_recent_summaries(self, chapter: int, window: int = 3) -> List[Dict[str, Any]]:
        summaries = []
        for ch in range(max(1, chapter - window), chapter):
            summary = self._load_summary_text(ch)
            if summary:
                summaries.append(summary)
        return summaries

    def _load_recent_meta(self, state: Dict[str, Any], chapter: int, window: int = 3) -> List[Dict[str, Any]]:
        meta = state.get("chapter_meta", {}) or {}
        results = []
        for ch in range(max(1, chapter - window), chapter):
            for key in (f"{ch:04d}", str(ch)):
                if key in meta:
                    results.append({"chapter": ch, **meta.get(key, {})})
                    break
        return results

    def _load_recent_appearances(
        self,
        *,
        chapter: int,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not self._index_exists():
            return []
        appearances = self.index_manager.get_recent_appearances(
            limit=limit,
            before_chapter=chapter,
        )
        return appearances or []

    def _load_setting(self, keyword: str) -> str:
        settings_dir = self.config.settings_dir
        candidates = [
            settings_dir / f"{keyword}.md",
        ]
        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8")
        # fallback: any file containing keyword
        matches = list(settings_dir.glob(f"*{keyword}*.md"))
        if matches:
            return matches[0].read_text(encoding="utf-8")
        return f"[{keyword}设定未找到]"

    def _extract_summary_excerpt(self, text: str, max_chars: int) -> str:
        if not text:
            return ""
        match = self.SUMMARY_SECTION_RE.search(text)
        excerpt = match.group(1).strip() if match else text.strip()
        if max_chars > 0 and len(excerpt) > max_chars:
            return excerpt[:max_chars].rstrip()
        return excerpt

    def _load_summary_text(self, chapter: int, snippet_chars: Optional[int] = None) -> Optional[Dict[str, Any]]:
        summary_path = self.config.canon_ledger_dir / "summaries" / f"ch{chapter:04d}.md"
        if not summary_path.exists():
            return None
        text = summary_path.read_text(encoding="utf-8")
        if snippet_chars:
            summary_text = self._extract_summary_excerpt(text, snippet_chars)
        else:
            summary_text = text
        summary_text = sanitize_fact_text(summary_text, max_chars=max(1, len(summary_text)))
        return {"chapter": chapter, "summary": summary_text} if summary_text else None

    def _load_story_skeleton(self, chapter: int) -> List[Dict[str, Any]]:
        interval = max(1, int(self.config.context_story_skeleton_interval))
        max_samples = max(0, int(self.config.context_story_skeleton_max_samples))
        snippet_chars = int(self.config.context_story_skeleton_snippet_chars)

        if max_samples <= 0 or chapter <= interval:
            return []

        samples: List[Dict[str, Any]] = []
        cursor = chapter - interval
        while cursor >= 1 and len(samples) < max_samples:
            summary = self._load_summary_text(cursor, snippet_chars=snippet_chars)
            if summary and summary.get("summary"):
                samples.append(summary)
            cursor -= interval

        samples.reverse()
        return samples

    def _load_json_optional(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}


def main():
    import argparse
    from .cli_output import print_success, print_error

    parser = argparse.ArgumentParser(description="Context Manager CLI")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--template", type=str, default=ContextManager.DEFAULT_TEMPLATE)

    args = parser.parse_args()

    config = None
    if args.project_root:
        # 允许传入"工作区根目录"，统一解析到真正的 book project_root（必须包含 .canon-ledger/state.json）
        from project_locator import resolve_project_root
        from .config import DataModulesConfig

        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    manager = ContextManager(config)
    try:
        payload = manager.build_context(
            chapter=args.chapter,
            template=args.template,
        )
        print_success(payload, message="context_built")
        try:
            manager.index_manager.log_tool_call("context_manager:build", True, chapter=args.chapter)
        except Exception as exc:
            logger.warning("failed to log successful tool call: %s", exc)
    except Exception as exc:
        print_error("CONTEXT_BUILD_FAILED", str(exc), suggestion="请检查项目结构与依赖文件")
        try:
            manager.index_manager.log_tool_call(
                "context_manager:build", False, error_code="CONTEXT_BUILD_FAILED", error_message=str(exc), chapter=args.chapter
            )
        except Exception as log_exc:
            logger.warning("failed to log failed tool call: %s", log_exc)


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()

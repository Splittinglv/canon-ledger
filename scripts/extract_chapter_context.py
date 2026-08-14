#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_chapter_context.py - extract chapter writing context

Features:
- chapter outline snippet
- previous chapter summaries (prefers .canon-ledger/summaries)
- compact state summary
- ContextManager contract sections (reader_signal / genre_profile / writing_guidance)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from chapter_outline_loader import load_chapter_outline, load_chapter_plot_structure

from runtime_compat import enable_windows_utf8_stdio

try:
    from chapter_paths import find_chapter_file
except ImportError:  # pragma: no cover
    from scripts.chapter_paths import find_chapter_file


def _ensure_scripts_path():
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def find_project_root(start_path: Path | None = None) -> Path:
    """解析真实书项目根（包含 `.canon-ledger/state.json` 的目录）。"""
    from project_locator import resolve_project_root

    if start_path is None:
        return resolve_project_root()
    return resolve_project_root(str(start_path))


def extract_chapter_outline(project_root: Path, chapter_num: int) -> str:
    """Extract chapter outline segment from volume outline file."""
    return load_chapter_outline(project_root, chapter_num, max_chars=1500)


def _load_summary_file(project_root: Path, chapter_num: int) -> str:
    """Load summary section from `.canon-ledger/summaries/chNNNN.md`."""
    summary_path = project_root / ".canon-ledger" / "summaries" / f"ch{chapter_num:04d}.md"
    if not summary_path.exists():
        return ""

    text = summary_path.read_text(encoding="utf-8")
    summary_match = re.search(r"##\s*剧情摘要\s*\r?\n(.+?)(?=\r?\n##|$)", text, re.DOTALL)
    if summary_match:
        return summary_match.group(1).strip()
    return ""


def extract_chapter_summary(project_root: Path, chapter_num: int) -> str:
    """Extract chapter summary, fallback to chapter body head."""
    summary = _load_summary_file(project_root, chapter_num)
    if summary:
        return summary

    chapter_file = find_chapter_file(project_root, chapter_num)
    if not chapter_file or not chapter_file.exists():
        return f"⚠️ 第{chapter_num}章文件不存在"

    content = chapter_file.read_text(encoding="utf-8")

    summary_match = re.search(r"##\s*本章摘要\s*\r?\n(.+?)(?=\r?\n##|$)", content, re.DOTALL)
    if summary_match:
        return summary_match.group(1).strip()

    stats_match = re.search(r"##\s*本章统计\s*\r?\n(.+?)(?=\r?\n##|$)", content, re.DOTALL)
    if stats_match:
        return f"[无摘要，仅统计]\n{stats_match.group(1).strip()}"

    lines = content.split("\n")
    text_lines = [line for line in lines if not line.startswith("#") and line.strip()]
    text = "\n".join(text_lines)[:500]
    return f"[自动截取前500字]\n{text}..."


def extract_state_summary(project_root: Path, chapter_num: int | None = None) -> str:
    """Extract key fields from `.canon-ledger/state.json`."""
    state_file = project_root / ".canon-ledger" / "state.json"
    if not state_file.exists():
        return "⚠️ state.json 不存在"

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return "⚠️ state.json 读取失败，已阻止状态快照注入"
    if not isinstance(state, dict):
        return "⚠️ state.json 根结构无效，已阻止状态快照注入"
    if chapter_num is not None:
        _ensure_scripts_path()
        from data_modules.state_snapshot import validate_state_snapshot

        _as_of, safe, _reason = validate_state_snapshot(state, chapter_num)
        if not safe:
            return "⚠️ state.json 不是目标章之前的可信快照，已阻止状态快照注入"
    summary_parts: List[str] = []

    if "progress" in state:
        progress = state["progress"]
        summary_parts.append(
            f"**进度**: 第{progress.get('current_chapter', '?')}章 / {progress.get('total_words', '?')}字"
        )

    if "protagonist_state" in state:
        ps = state["protagonist_state"]
        power = ps.get("power", {})
        summary_parts.append(f"**主角实力**: {power.get('realm', '?')} {power.get('layer', '?')}层")
        summary_parts.append(f"**当前位置**: {ps.get('location', '?')}")
        golden_finger = ps.get("golden_finger")
        if isinstance(golden_finger, dict) and str(golden_finger.get("name") or "").strip():
            summary_parts.append(
                f"**金手指**: {golden_finger.get('name')} Lv.{golden_finger.get('level', 0)}"
            )

    if "strand_tracker" in state:
        tracker = state["strand_tracker"]
        history = tracker.get("history", [])[-5:]
        if history:
            items: List[str] = []
            for row in history:
                if not isinstance(row, dict):
                    continue
                chapter = row.get("chapter", "?")
                strand = row.get("strand") or row.get("dominant") or "unknown"
                items.append(f"Ch{chapter}:{strand}")
            if items:
                summary_parts.append(f"**近5章Strand**: {', '.join(items)}")

    plot_threads = state.get("plot_threads", {}) if isinstance(state.get("plot_threads"), dict) else {}
    foreshadowing = plot_threads.get("foreshadowing", [])
    if isinstance(foreshadowing, list) and foreshadowing:
        active = [row for row in foreshadowing if row.get("status") in {"active", "未回收"}]
        urgent = [row for row in active if row.get("urgency", 0) > 50]
        if urgent:
            urgent_list = [
                f"{row.get('content', '?')[:30]}... (紧急度:{row.get('urgency')})"
                for row in urgent[:3]
            ]
            summary_parts.append(f"**紧急伏笔**: {'; '.join(urgent_list)}")

    return "\n".join(summary_parts)


def _chapter_goal(contract_context: Dict[str, Any]) -> str:
    _ensure_scripts_path()
    from data_modules.rag_context import chapter_goal_from_contract

    return chapter_goal_from_contract(contract_context)


def _load_rag_assist(
    project_root: Path,
    chapter_num: int,
    outline: str,
    *,
    chapter_goal: str = "",
) -> Dict[str, Any]:
    """Compatibility wrapper over the shared fact-only RAG context helper."""
    _ensure_scripts_path()
    from data_modules.config import DataModulesConfig
    from data_modules.rag_context import empty_rag_assist, load_rag_assist

    config = DataModulesConfig.from_project_root(project_root)
    try:
        return load_rag_assist(
            project_root,
            chapter=chapter_num,
            outline=outline,
            chapter_goal=chapter_goal,
            config=config,
        )
    except Exception as exc:
        payload = empty_rag_assist(
            enabled=bool(getattr(config, "context_rag_assist_enabled", True)),
            reason=f"rag_error:{exc.__class__.__name__}",
        )
        payload["degraded"] = True
        return payload


def _load_contract_context(project_root: Path, chapter_num: int) -> Dict[str, Any]:
    """Build context via ContextManager and return selected sections."""
    _ensure_scripts_path()
    from data_modules.config import DataModulesConfig
    from data_modules.context_manager import ContextManager

    config = DataModulesConfig.from_project_root(project_root)
    manager = ContextManager(config)
    payload = manager.build_context(chapter=chapter_num, template="plot")

    return {
        "context_contract_version": (payload.get("meta") or {}).get("context_contract_version"),
        "context_weight_stage": (payload.get("meta") or {}).get("context_weight_stage"),
        "story_contract": payload.get("story_contract", {}),
        "runtime_status": payload.get("runtime_status", {}),
        "latest_commit": payload.get("latest_commit", {}),
        "prewrite_validation": payload.get("prewrite_validation", {}),
        "context_completeness": payload.get("context_completeness", {}),
        "hard_constraints": payload.get("hard_constraints", []),
        "reader_signal": payload.get("reader_signal", {}),
        "genre_profile": payload.get("genre_profile", {}),
        "writing_guidance": payload.get("writing_guidance", {}),
        "plot_structure": payload.get("plot_structure", {}),
        "long_term_memory": payload.get("long_term_memory", {}),
        "scene": payload.get("scene", {}),
        "core": payload.get("core", {}),
    }


def build_chapter_context_payload(project_root: Path, chapter_num: int) -> Dict[str, Any]:
    """Assemble full chapter context payload for text/json output."""
    outline = extract_chapter_outline(project_root, chapter_num)

    # Untyped summary prose is excluded; structured facts/RAG provide history.
    prev_summaries: List[str] = []

    state_summary = extract_state_summary(project_root, chapter_num=chapter_num)
    contract_context = _load_contract_context(project_root, chapter_num)
    plot_structure = contract_context.get("plot_structure") or load_chapter_plot_structure(project_root, chapter_num)
    rag_assist = _load_rag_assist(
        project_root,
        chapter_num,
        outline,
        chapter_goal=_chapter_goal(contract_context),
    )

    return {
        "chapter": chapter_num,
        "outline": outline,
        "previous_summaries": prev_summaries,
        "state_summary": state_summary,
        "context_contract_version": contract_context.get("context_contract_version"),
        "context_weight_stage": contract_context.get("context_weight_stage"),
        "story_contract": contract_context.get("story_contract", {}),
        "runtime_status": contract_context.get("runtime_status", {}),
        "latest_commit": contract_context.get("latest_commit", {}),
        "prewrite_validation": contract_context.get("prewrite_validation", {}),
        "context_completeness": contract_context.get("context_completeness", {}),
        "hard_constraints": contract_context.get("hard_constraints", []),
        "reader_signal": contract_context.get("reader_signal", {}),
        "genre_profile": contract_context.get("genre_profile", {}),
        "writing_guidance": contract_context.get("writing_guidance", {}),
        "plot_structure": plot_structure,
        "long_term_memory": contract_context.get("long_term_memory", {}),
        "scene": contract_context.get("scene", {}),
        "core": contract_context.get("core", {}),
        "rag_assist": rag_assist,
    }


def _render_text(payload: Dict[str, Any]) -> str:
    """JSON 序列化输出，text 渲染由 context-agent 负责。"""
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="提取章节创作所需的精简上下文")
    parser.add_argument("--chapter", type=int, required=True, help="目标章节号")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    parser.add_argument("--format", choices=["json"], default="json",
                        help="输出格式（始终 JSON，text 渲染由 context-agent 负责）")

    args = parser.parse_args()

    try:
        project_root = (
            find_project_root(Path(args.project_root))
            if args.project_root
            else find_project_root()
        )
        payload = build_chapter_context_payload(project_root, args.chapter)
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    except Exception as exc:
        print(f"❌ 错误: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()

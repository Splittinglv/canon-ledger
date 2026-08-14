#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /canon-ledger-learn 写成可被默认上下文消费的结构化一致性规则。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from runtime_compat import enable_windows_utf8_stdio
from data_modules.config import DataModulesConfig
from data_modules.fact_text import sanitize_fact_text
from data_modules.memory.schema import MemoryItem, now_iso
from data_modules.memory.store import ScratchpadManager

VALID_PATTERN_TYPES = frozenset({"foreshadow", "timeline", "setting", "character"})
PATTERN_LABELS = {
    "foreshadow": "伏笔处理",
    "timeline": "时间线衔接",
    "setting": "设定执行",
    "character": "人物一致性",
}


def _current_chapter(project_root: Path) -> Optional[int]:
    state_path = project_root / ".canon-ledger" / "state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    progress = state.get("progress") if isinstance(state, dict) else {}
    chapter = progress.get("current_chapter") if isinstance(progress, dict) else None
    try:
        return int(chapter) if chapter is not None else None
    except (TypeError, ValueError):
        return None


def add_pattern(
    project_root: Path,
    *,
    pattern_type: str,
    description: str,
    category: str = "",
    importance: str = "medium",
    source_chapter: Optional[int] = None,
) -> Dict[str, Any]:
    project_root = project_root.expanduser().resolve()
    pattern_type = str(pattern_type or "").strip().lower()
    if pattern_type not in VALID_PATTERN_TYPES:
        raise ValueError(
            "pattern_type 只允许 foreshadow、timeline、setting、character；"
            "文风、句式和其它自由经验请写入设定集/文风提示词.md 或明确的设定/章纲。"
        )
    description = " ".join(str(description or "").split())
    if not description:
        raise ValueError("description 不能为空")
    safe_description = sanitize_fact_text(description, max_chars=500)
    if safe_description != description:
        raise ValueError(
            "description 只能记录跨章事实与一致性约束，不能包含文风、文笔、句式、桥段或模型控制指令"
        )

    chapter = source_chapter if source_chapter is not None else _current_chapter(project_root)
    if chapter is not None and (isinstance(chapter, bool) or int(chapter) < 0):
        raise ValueError("source_chapter 必须是非负整数")
    source = int(chapter or 0)
    digest = hashlib.sha256(
        f"{pattern_type}\0{description}".encode("utf-8")
    ).hexdigest()[:20]
    item_id = f"author-consistency-{digest}"
    config = DataModulesConfig.from_project_root(project_root)
    manager = ScratchpadManager(config)
    if any(row.id == item_id for row in manager.query(category="world_rule", status=None)):
        learned = {
            "id": item_id,
            "pattern_type": pattern_type,
            "description": description,
            "source_chapter": source,
        }
        return {"status": "skipped", "reason": "duplicate", "learned": learned}

    learned: Dict[str, Any] = {
        "id": item_id,
        "pattern_type": pattern_type,
        "description": description,
        "source_chapter": source,
        "learned_at": now_iso(),
    }
    if category:
        learned["category"] = category
    if importance:
        learned["importance"] = importance
    manager.upsert_item(
        MemoryItem(
            id=item_id,
            layer="semantic",
            category="world_rule",
            subject=f"作者确认的{PATTERN_LABELS[pattern_type]}",
            field=pattern_type,
            value=description,
            payload={
                "origin": "/canon-ledger-learn",
                "pattern_type": pattern_type,
                "category": str(category or ""),
                "importance": str(importance or "medium"),
            },
            status="active",
            source_chapter=source,
            evidence=["author:/canon-ledger-learn"],
            updated_at=now_iso(),
        )
    )
    return {
        "status": "success",
        "learned": learned,
        "path": str(config.scratchpad_file),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="写入结构化项目一致性规则")
    parser.add_argument("--project-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-pattern", help="追加一条项目经验记忆")
    add.add_argument("--pattern-type", choices=sorted(VALID_PATTERN_TYPES), required=True)
    add.add_argument("--description", required=True)
    add.add_argument("--category", default="")
    add.add_argument("--importance", default="medium")
    add.add_argument("--source-chapter", type=int)

    args = parser.parse_args()
    try:
        if args.command == "add-pattern":
            result = add_pattern(
                Path(args.project_root),
                pattern_type=args.pattern_type,
                description=args.description,
                category=args.category,
                importance=args.importance,
                source_chapter=args.source_chapter,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

    raise SystemExit(2)


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()

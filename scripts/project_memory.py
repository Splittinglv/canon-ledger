#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 /canon-ledger-learn 写成可被默认上下文消费的结构化一致性规则。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from runtime_compat import enable_windows_utf8_stdio
from data_modules.config import DataModulesConfig
from data_modules.fact_text import normalize_author_text, sanitize_fact_text
from data_modules.memory.schema import MemoryItem, now_iso
from data_modules.memory.store import ScratchpadManager

VALID_PATTERN_TYPES = frozenset({"foreshadow", "timeline", "setting", "character"})
PATTERN_LABELS = {
    "foreshadow": "伏笔处理",
    "timeline": "时间线衔接",
    "setting": "设定执行",
    "character": "人物一致性",
}

# `/canon-ledger-learn` 是一条很窄的入口：写进来的条目会以 author-consistency-*
# 前缀进入默认写作上下文的 hard_constraints，所以这里可以用一份专门的写法词表
# 显式拒绝创作处方。
#
# 注意：data_modules/fact_text.py 有意不做这件事——它要保住「限知视角」
# 「用三年时间炼成金丹」这类正常事实，因此不能靠 sanitize_fact_text 反推文风。
_STYLE_PRESCRIPTION_RE = re.compile(
    r"(?:文风|文笔|风格|笔调|行文|文体|腔调|口吻|语气|口语化|书面语|"
    r"句式|短句|长句|断句|句子|段落|分段|篇幅|字数|章长|"
    r"修辞|比喻|排比|白描|描写手法|润色|去水|水词|"
    r"节奏|爽点|打脸|钩子|悬念|反转|套路|桥段|"
    r"黄金三章|开篇|章首|章末|结尾|收尾|"
    r"prose|writing\s+style|tone|voice|pacing|cadence|rhetoric)",
    re.IGNORECASE,
)


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
    style_hit = _STYLE_PRESCRIPTION_RE.search(description)
    if style_hit:
        raise ValueError(
            "description 只能记录跨章事实与一致性约束，不能包含文风、文笔、句式、"
            f"节奏、桥段或模型控制指令（命中「{style_hit.group(0)}」）。"
            "口吻与文笔偏好请写入 设定集/文风提示词.md。"
        )
    # 这条记忆会进 hard_constraints，属于控制面：越狱句必须显式拒绝，不能静默剥离。
    if sanitize_fact_text(description, max_chars=500) != description:
        raise ValueError(
            "description 含有试图覆盖写作合同或系统提示的指令，已拒绝写入长期记忆。"
        )
    safe_description = normalize_author_text(description, max_chars=500)
    if not safe_description:
        raise ValueError("description 不能为空")
    description = safe_description

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

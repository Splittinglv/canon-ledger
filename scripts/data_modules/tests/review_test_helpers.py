#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试专用的完整事实审查产物工厂。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REVIEW_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]


def write_current_chapter_contract(
    project_root: str | Path,
    chapter: int,
    *,
    planned_nodes: list[str] | None = None,
    goal: str | None = None,
) -> Path:
    """为测试写入当前 CanonLedger 章合同，不创建任何旧字段。"""
    root = Path(project_root)
    path = root / ".story-system" / "chapters" / f"chapter_{int(chapter):03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "CHAPTER_BRIEF",
                    "chapter": int(chapter),
                },
                "chapter_directive": {
                    "goal": goal or f"验证第{int(chapter)}章当前提交主链",
                    "must_cover_nodes": list(planned_nodes or []),
                    "forbidden_zones": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def standard_review(
    chapter_binding: dict[str, Any] | None = None,
    *,
    blocking_count: int = 0,
) -> dict[str, Any]:
    issues = [
        {
            "severity": "critical",
            "category": "logic",
            "location": "事实核对位置",
            "description": f"第{index + 1}个已确认的事实冲突。",
            "evidence": "正文事实与既有记录互相矛盾。",
            "fix_hint": "统一正文与已确认事实。",
            "blocking": True,
        }
        for index in range(blocking_count)
    ]
    payload: dict[str, Any] = {
        "review_mode": "standard",
        "review_status": "completed",
        "review_skipped": False,
        "review_degraded": False,
        "reviewed_dimensions": list(REVIEW_DIMENSIONS),
        "skipped_dimensions": [],
        "dimension_results": [
            {"dimension": dimension, "conclusion": "已完成事实核对。"}
            for dimension in REVIEW_DIMENSIONS
        ],
        "issues": issues,
        "issues_count": len(issues),
        "blocking_count": len(issues),
        "has_blocking": bool(issues),
        "summary": "事实审查已完成。",
    }
    if chapter_binding is not None:
        payload["chapter_binding"] = dict(chapter_binding)
        payload["chapter"] = int(chapter_binding.get("chapter") or 0)
    return payload


def minimal_review(chapter_binding: dict[str, Any]) -> dict[str, Any]:
    dimensions = list(REVIEW_DIMENSIONS)
    return {
        "chapter": int(chapter_binding.get("chapter") or 0),
        "chapter_binding": dict(chapter_binding),
        "review_mode": "minimal",
        "review_status": "skipped",
        "review_skipped": True,
        "review_degraded": True,
        "reviewed_dimensions": [],
        "skipped_dimensions": dimensions,
        "dimension_results": [],
        "issues": [],
        "issues_count": 0,
        "blocking_count": 0,
        "has_blocking": False,
        "summary": "用户明确选择跳过事实审查。",
    }

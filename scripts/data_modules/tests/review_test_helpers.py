#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试专用的完整事实审查产物工厂。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REVIEW_DIMENSIONS = ["setting", "timeline", "continuity", "character", "logic"]

HARD_EVIDENCE_EVENT_TYPES = frozenset(
    {
        "knowledge_state_changed",
        "presence_observed",
        "custody_changed",
        "open_loop_created",
        "promise_created",
        "relationship_changed",
        "world_rule_broken",
        "world_rule_revealed",
    }
)


def inject_hard_evidence_quotes(
    extraction: dict[str, Any],
    *,
    chapter: int,
    chapter_text: str,
) -> tuple[dict[str, Any], str]:
    """Fill missing hard-constraint quotes and append them to chapter text.

    Tests that assert missing-quote failures must not call this helper.
    """
    payload = dict(extraction)
    events = list(payload.get("accepted_events") or [])
    text = chapter_text if chapter_text else f"第{chapter}章最终正文\n"
    extra_lines: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_payload = (
            dict(event.get("payload"))
            if isinstance(event.get("payload"), dict)
            else {}
        )
        quote = str(event_payload.get("evidence_quote") or "").strip()
        event_type = str(event.get("event_type") or "")
        if event_type in HARD_EVIDENCE_EVENT_TYPES and not quote:
            if event_type == "world_rule_revealed":
                domain = str(
                    event_payload.get("domain") or event.get("subject") or ""
                ).strip()
                content = str(event_payload.get("rule_content") or "").strip()
                quote = (
                    f"{domain}：{content}" if domain and content else content or domain
                )
            else:
                quote = str(
                    event_payload.get("unanswered_question")
                    or event_payload.get("content")
                    or event_payload.get("description")
                    or event_payload.get("rule_content")
                    or event_payload.get("proposed_value")
                    or event_payload.get("canonical_claim")
                    or f"第{chapter}章最终正文"
                ).strip()
            if quote:
                event_payload["evidence_quote"] = quote
                event["payload"] = event_payload
        if quote and quote not in text and quote not in extra_lines:
            extra_lines.append(quote)
    if extra_lines:
        text = text.rstrip("\n") + "\n" + "\n".join(extra_lines) + "\n"
    payload["accepted_events"] = events
    return payload, text


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

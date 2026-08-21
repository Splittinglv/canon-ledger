#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .story_runtime_sources import load_runtime_sources


_CHAPTER_FILE_RE = re.compile(r"chapter_(\d{3,4})")


def _extract_chapter_from_name(path: Path) -> int:
    match = _CHAPTER_FILE_RE.search(path.name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _latest_story_system_chapter(project_root: Path) -> int:
    story_root = project_root / ".story-system"
    if not story_root.is_dir():
        return 0

    candidates = []
    for pattern in (
        "chapters/chapter_*.json",
        "reviews/chapter_*.review.json",
        "commits/chapter_*.commit.json",
    ):
        for path in story_root.glob(pattern):
            candidates.append(_extract_chapter_from_name(path))
    return max(candidates or [0])


def _resolve_chapter(project_root: Path, chapter: int | None) -> int:
    if chapter is not None:
        try:
            return max(0, int(chapter))
        except (TypeError, ValueError):
            return 0

    latest_story_system_chapter = _latest_story_system_chapter(project_root)
    state_path = project_root / ".canon-ledger" / "state.json"
    if not state_path.is_file():
        return latest_story_system_chapter

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return latest_story_system_chapter

    try:
        state_chapter = max(0, int(((state.get("progress") or {}).get("current_chapter") or 0)))
    except (TypeError, ValueError):
        state_chapter = 0
    return max(state_chapter, latest_story_system_chapter)


def build_story_runtime_health(project_root: Path, chapter: int | None = None) -> dict[str, Any]:
    project_root = Path(project_root)
    from .workflow_authority import WorkflowAuthority

    workflow = WorkflowAuthority(project_root).snapshot()
    if chapter is not None:
        try:
            current_chapter = max(1, int(chapter))
        except (TypeError, ValueError):
            current_chapter = 1
    else:
        current_chapter = int(
            workflow.get("chapter")
            or workflow.get("expected_next_chapter")
            or 1
        )

    snapshot = load_runtime_sources(project_root, current_chapter)
    runtime_workflow = dict(snapshot.workflow_snapshot or {})
    workflow_changed = bool(
        runtime_workflow
        and runtime_workflow.get("workflow_digest") != workflow.get("workflow_digest")
    )
    if runtime_workflow:
        workflow = runtime_workflow
    latest_commit = snapshot.latest_commit or {}
    fallback_sources = list(snapshot.fallback_sources)
    if workflow_changed:
        fallback_sources.append("canon_v3_workflow_changed_during_health_read")
    return {
        "chapter": current_chapter,
        "mainline_ready": bool(
            workflow.get("state") == "ready"
            and workflow.get("can_write_next")
            and current_chapter in {
                int(value)
                for value in workflow.get("allowed_write_chapters") or []
            }
            and not fallback_sources
        ),
        "fallback_sources": fallback_sources,
        "advisory_sources": list(snapshot.advisory_sources),
        "latest_commit_status": (latest_commit.get("meta") or {}).get("status", "missing"),
        "primary_write_source": snapshot.primary_write_source,
        "head_hash": workflow.get("head_hash"),
        "generation": int(workflow.get("generation") or 0),
        "workflow_digest": workflow.get("workflow_digest"),
        "workflow_state": workflow.get("state"),
        "primary_action": workflow.get("primary_action"),
    }

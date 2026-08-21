#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chapter_outline_loader import volume_num_for_chapter_from_state
from chapter_paths import volume_num_for_chapter

from .story_contracts import (
    StoryContractPaths,
    read_json_if_exists,
    verify_setting_canon,
)
from .chapter_content_binding import verify_commit_content_binding


@dataclass
class RuntimeSourceSnapshot:
    chapter: int
    contracts: dict[str, dict[str, Any]]
    latest_commit: dict[str, Any] | None
    latest_accepted_commit: dict[str, Any] | None
    fallback_sources: list[str] = field(default_factory=list)
    advisory_sources: list[str] = field(default_factory=list)
    source_errors: dict[str, str] = field(default_factory=dict)
    primary_write_source: str = "chapter_commit"
    workflow_snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter,
            "contracts": self.contracts,
            "latest_commit": self.latest_commit,
            "latest_accepted_commit": self.latest_accepted_commit,
            "fallback_sources": list(self.fallback_sources),
            "advisory_sources": list(self.advisory_sources),
            "source_errors": dict(self.source_errors),
            "primary_write_source": self.primary_write_source,
            "workflow_snapshot": dict(self.workflow_snapshot or {}),
        }


def commit_status_view(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return workflow metadata without exposing review/extraction prose.

    Commits contain model-authored artifacts.  Context consumers only need
    the commit's trust and projection state; copying the full envelope would
    create a second path around the consistency-context sanitizer.
    """
    if not isinstance(payload, dict) or not payload:
        return None

    raw_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    meta: dict[str, Any] = {}
    chapter = raw_meta.get("chapter")
    if type(chapter) is int and chapter > 0:
        meta["chapter"] = chapter
    schema_version = str(raw_meta.get("schema_version") or "")
    if schema_version in {"story-system/v1", "story-system/v2", "story-system/v3"}:
        meta["schema_version"] = schema_version
    status = str(raw_meta.get("status") or "").strip().lower()
    meta["status"] = status if status in {"accepted", "rejected"} else "unknown"
    head_hash = str(raw_meta.get("head_hash") or "")
    if len(head_hash) == 64 and all(char in "0123456789abcdef" for char in head_hash):
        meta["head_hash"] = head_hash
    generation = raw_meta.get("generation")
    if type(generation) is int and generation >= 0:
        meta["generation"] = generation

    raw_projection = (
        payload.get("projection_status")
        if isinstance(payload.get("projection_status"), dict)
        else {}
    )
    projection_status: dict[str, str] = {}
    for key in ("summary", "index", "state", "memory", "vector"):
        value = str(raw_projection.get(key) or "").strip().lower()
        if value.startswith("failed"):
            projection_status[key] = "failed"
        elif value in {"pending", "done", "skipped"}:
            projection_status[key] = value
    canon_projection = str(raw_projection.get("canon") or "").strip().lower()
    if canon_projection in {"done", "pending", "failed"}:
        projection_status["canon"] = canon_projection

    result: dict[str, Any] = {
        "meta": meta,
        "projection_status": projection_status,
    }
    if payload.get("source") == "canon_v3_head":
        result["source"] = "canon_v3_head"
    raw_trust = payload.get("trust") if isinstance(payload.get("trust"), dict) else {}
    if raw_trust:
        trust: dict[str, Any] = {}
        if type(raw_trust.get("content_binding")) is bool:
            trust["content_binding"] = raw_trust["content_binding"]
        if trust:
            result["trust"] = trust
    return result


def _volume_for_chapter(project_root: Path, chapter: int) -> int:
    return (
        volume_num_for_chapter_from_state(project_root, chapter)
        or volume_num_for_chapter(chapter)
    )


def _status_only_commit(payload: dict[str, Any], *, binding_error: str) -> dict[str, Any]:
    """Expose workflow status without leaking untrusted artifact facts."""
    result = commit_status_view(payload) or {"meta": {}, "projection_status": {}}
    result["trust"] = {
        "content_binding": False,
        "reason": str(binding_error or "commit_untrusted")[:160],
    }
    return result


def _load_latest_commit(
    project_root: Path,
    paths: StoryContractPaths,
    chapter: int,
) -> dict[str, Any] | None:
    for current in range(chapter, 0, -1):
        try:
            payload = read_json_if_exists(paths.commit_json(current))
        except (OSError, ValueError):
            return {
                "meta": {"chapter": current},
                "projection_status": {},
                "trust": {"content_binding": False, "reason": "invalid_commit_json"},
            }
        if payload:
            status = str((payload.get("meta") or {}).get("status") or "")
            trusted, code = verify_commit_content_binding(
                project_root,
                current,
                payload,
            )
            if status == "accepted" and trusted:
                return payload
            reason = code if not trusted else f"commit_status_{status or 'missing'}"
            return _status_only_commit(payload, binding_error=reason)
    return None


def _load_latest_accepted_commit(
    project_root: Path,
    paths: StoryContractPaths,
    chapter: int,
) -> dict[str, Any] | None:
    for current in range(chapter, 0, -1):
        try:
            payload = read_json_if_exists(paths.commit_json(current))
        except (OSError, ValueError):
            continue
        trusted, _code = verify_commit_content_binding(
            project_root,
            current,
            payload,
        )
        if (
            trusted
            and payload
            and (payload.get("meta") or {}).get("status") == "accepted"
        ):
            return payload
    return None


def load_runtime_sources(
    project_root: Path,
    chapter: int,
    history_as_of_chapter: int | None = None,
) -> RuntimeSourceSnapshot:
    project_root = Path(project_root)
    paths = StoryContractPaths.from_project_root(project_root)
    volume = _volume_for_chapter(project_root, chapter)

    contract_paths = {
        "master": paths.master_json,
        "volume": paths.volume_json(volume),
        "chapter": paths.chapter_json(chapter),
        "review": paths.review_json(chapter),
    }
    contracts: dict[str, dict[str, Any]] = {}
    source_errors: dict[str, str] = {}
    for key, path in contract_paths.items():
        try:
            payload = read_json_if_exists(path) or {}
            if payload and not isinstance(payload, dict):
                raise ValueError("contract_root_must_be_object")
            contracts[key] = payload
        except (OSError, ValueError) as exc:
            contracts[key] = {}
            source_errors[key] = exc.__class__.__name__
    from .workflow_authority import WorkflowAuthority

    authority = WorkflowAuthority(project_root)
    workflow = authority.snapshot()
    # MASTER_SETTING remains useful for routing/author preferences, but its
    # factual snapshots are not HEAD-bound and therefore never enter runtime
    # context through this compatibility contract.
    master_contract = contracts.get("master")
    if isinstance(master_contract, dict):
        contracts["master"] = {
            key: value
            for key, value in master_contract.items()
            if key not in {"initial_canon", "setting_canon"}
        }

    if not workflow.get("head_hash"):
        advisory_sources = []
        for key, payload in contracts.items():
            if not payload:
                prefix = "invalid" if key in source_errors else "missing"
                advisory_sources.append(f"{prefix}_{key}_contract")
        fallback_sources = [
            f"canon_v3_workflow_{workflow.get('state') or 'invalid'}"
        ]
        return RuntimeSourceSnapshot(
            chapter=chapter,
            contracts=contracts,
            latest_commit=None,
            latest_accepted_commit=None,
            fallback_sources=fallback_sources,
            advisory_sources=advisory_sources,
            source_errors=source_errors,
            primary_write_source="canon_v3_head",
            workflow_snapshot=workflow,
        )

    if workflow.get("head_hash"):
        from .canon_v3.projection import projection_is_fresh
        from .canon_v3.repository import CanonV3Repository

        repository = CanonV3Repository(project_root)
        fresh = projection_is_fresh(project_root)
        all_v3_commits = [
            commit
            for _commit_hash, commit in repository.current_commits()
        ]
        current_commits = [
            commit
            for commit in all_v3_commits
            if int(commit.get("chapter") or 0) <= int(chapter)
        ]
        accepted_as_of = (
            int(chapter)
            if history_as_of_chapter is None
            else max(0, int(history_as_of_chapter))
        )
        accepted_commits = [
            commit
            for commit in all_v3_commits
            if int(commit.get("chapter") or 0) <= accepted_as_of
        ]

        def _status(commit: dict[str, Any] | None) -> dict[str, Any] | None:
            if commit is None:
                return None
            return {
                "meta": {
                    "schema_version": "story-system/v3",
                    "chapter": int(commit.get("chapter") or 0),
                    "revision": int(commit.get("revision") or 0),
                    "status": "accepted",
                    "head_hash": repository.current_head(validate=False),
                    "generation": int(workflow.get("generation") or 0),
                },
                "projection_status": {"canon": "done" if fresh else "pending"},
                "source": "canon_v3_head",
            }
        status_commit = _status(current_commits[-1] if current_commits else None)
        status_accepted = _status(
            accepted_commits[-1] if accepted_commits else None
        )
        advisory_sources = []
        for key, payload in contracts.items():
            if not payload:
                prefix = "invalid" if key in source_errors else "missing"
                advisory_sources.append(f"{prefix}_{key}_contract")
        fallback_sources = []
        if not workflow.get("can_write_next"):
            fallback_sources.append(
                f"canon_v3_workflow_{workflow.get('state') or 'invalid'}"
            )
        post_read_workflow = authority.snapshot()
        if post_read_workflow.get("workflow_digest") != workflow.get(
            "workflow_digest"
        ):
            workflow = post_read_workflow
            status_commit = None
            status_accepted = None
            fallback_sources.append("canon_v3_workflow_changed_during_runtime_read")
        return RuntimeSourceSnapshot(
            chapter=chapter,
            contracts=contracts,
            latest_commit=status_commit,
            latest_accepted_commit=status_accepted,
            fallback_sources=fallback_sources,
            advisory_sources=advisory_sources,
            source_errors=source_errors,
            primary_write_source="canon_v3_head",
            workflow_snapshot=workflow,
        )
    latest_commit = _load_latest_commit(project_root, paths, chapter)
    accepted_as_of = (
        int(chapter)
        if history_as_of_chapter is None
        else max(0, int(history_as_of_chapter))
    )
    latest_accepted_commit = (
        _load_latest_accepted_commit(project_root, paths, accepted_as_of)
        if accepted_as_of > 0
        else None
    )

    fallback_sources: list[str] = []
    for key, payload in contracts.items():
        if not payload:
            prefix = "invalid" if key in source_errors else "missing"
            fallback_sources.append(f"{prefix}_{key}_contract")
    master_payload = contracts.get("master") or {}
    if master_payload:
        setting_ok, setting_reason = verify_setting_canon(
            project_root,
            master_payload.get("setting_canon"),
        )
        if not setting_ok:
            fallback_sources.append(setting_reason)
    if accepted_as_of > 0 and latest_accepted_commit is None:
        fallback_sources.append("missing_accepted_commit")

    return RuntimeSourceSnapshot(
        chapter=chapter,
        contracts=contracts,
        latest_commit=latest_commit,
        latest_accepted_commit=latest_accepted_commit,
        fallback_sources=fallback_sources,
        source_errors=source_errors,
    )

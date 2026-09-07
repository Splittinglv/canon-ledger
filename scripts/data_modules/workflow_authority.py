#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single public workflow authority for every book-project surface.

``CURRENT`` is an implementation detail of the Canon v3 repository, not a
feature flag.  Public callers must consult this module even before a v3 HEAD
exists so an uninitialised project fails closed instead of silently falling
back to retired fact writers or read models.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


WORKFLOW_AUTHORITY_SCHEMA = "canon-v3/workflow-snapshot/v2"


class WorkflowAuthorityError(ValueError):
    """Base error raised by the public workflow authority."""


class LegacyFactMutationDisabled(WorkflowAuthorityError):
    """Raised whenever a retired v1/v2 fact writer is invoked."""


class CanonReadModelUnavailable(WorkflowAuthorityError):
    """Raised when no exact, fresh HEAD-bound read model is available."""


def _digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bootstrap_mode(snapshot: Mapping[str, Any]) -> str:
    action = str(snapshot.get("recovery_action") or "")
    head = snapshot.get("head_hash")
    if not head and action == "initialize_v3":
        return "new_project"
    return "canon_v3"


def _structured_action(
    *,
    code: str,
    label: str,
    command: str,
    transaction_kind: str,
    parameters: Mapping[str, Any] | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    """Build one typed action while retaining the historic ``code`` field."""

    is_skill = command.startswith("/")
    payload: dict[str, Any] = {
        "id": action_id or code,
        "code": code,
        "label": label,
        "interface": "skill" if is_skill else "cli",
        "transaction_kind": transaction_kind,
        "command": command,
    }
    if is_skill:
        payload["skill"] = command.split(maxsplit=1)[0].lstrip("/")
    if parameters:
        payload["parameters"] = dict(parameters)
    return payload


def _primary_action(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    state = str(snapshot.get("state") or "invalid")
    transaction_kind = str(snapshot.get("transaction_kind") or "chapter")
    recovery = str(snapshot.get("recovery_action") or "run_canon_v3_doctor")
    chapter = int(snapshot.get("chapter") or 0)
    expected = int(snapshot.get("expected_next_chapter") or 0)
    actions: dict[str, tuple[str, str, str]] = {
        "ready": (
            "write_next_chapter",
            f"继续写第 {expected} 章" if expected else "继续写下一章",
            f"/canon-ledger-write {expected}" if expected else "/canon-ledger-write",
        ),
        "ready_to_finalize": (
            "finalize_transaction",
            "发布当前事实事务",
            "/canon-ledger-confirm",
        ),
        "awaiting_human": (
            "review_staging",
            "完成当前关键事实的人工确认",
            f"/canon-ledger-confirm {chapter}" if chapter else "/canon-ledger-confirm",
        ),
        "rewrite_required": (
            "rewrite_chapter",
            "按已确认的穿帮项修改正文并重新审查",
            f"/canon-ledger-write {chapter}" if chapter else "/canon-ledger-write",
        ),
        "recompile_required": (
            "reprepare_chapter",
            "按当前正文与 Canon HEAD 重新生成事实提议",
            f"/canon-ledger-write {chapter}" if chapter else "/canon-ledger-write",
        ),
        "projection_rebuild_required": (
            "rebuild_projection",
            "按当前 HEAD 重建事实投影",
            "canon_ledger.py canon-v3 rebuild-projection",
        ),
        "initialization_required": (
            "initialize_v3",
            "初始化 Canon v3",
            "canon_ledger.py canon-v3 initialize",
        ),
        "invalid": (
            "run_canon_v3_doctor",
            "运行深度检查并修复 Canon 完整性",
            "canon_ledger.py doctor --deep",
        ),
    }
    if transaction_kind == "author_axiom":
        actions.update(
            {
                "rewrite_required": (
                    "rewrite_author_axiom_draft",
                    "修改受管硬设定草案并重新创建 axiom proposal",
                    "/canon-ledger-plan",
                ),
                "recompile_required": (
                    "reprepare_author_axioms",
                    "按当前 HEAD 重新绑定并准备硬设定事务",
                    "/canon-ledger-plan",
                ),
            }
        )
    code, label, command = actions.get(
        state,
        (recovery, recovery or "修复 Canon 工作流", "canon_ledger.py doctor --deep"),
    )
    return _structured_action(
        code=code,
        label=label,
        command=command,
        transaction_kind=transaction_kind,
    )


def normalize_workflow_snapshot(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Purely normalize an engine snapshot into the public authority view."""

    if (
        raw.get("schema_version") == WORKFLOW_AUTHORITY_SCHEMA
        and isinstance(raw.get("primary_action"), Mapping)
        and raw.get("bootstrap_mode")
        and raw.get("authority_view_digest")
    ):
        return dict(raw)
    normalized = {
        **raw,
        "schema_version": WORKFLOW_AUTHORITY_SCHEMA,
        "engine_schema_version": str(raw.get("schema_version") or ""),
        "bootstrap_mode": _bootstrap_mode(raw),
        "primary_action": _primary_action(raw),
        "stage_digest": raw.get("stage_digest"),
        "finalize_token": raw.get("finalize_token"),
    }
    # The engine digest covers every mutable authority input and is the token
    # echoed by proposal/decision clients.  Presentation-only normalization has
    # a separate view digest so it cannot fork transaction identity.
    normalized["workflow_digest"] = str(raw.get("workflow_digest") or "")
    normalized["authority_view_digest"] = _digest(
        {
            key: value
            for key, value in normalized.items()
            if key != "authority_view_digest"
        }
    )
    return normalized


@dataclass(frozen=True, slots=True)
class WorkflowAuthority:
    project_root: Path

    def __init__(self, project_root: str | Path):
        object.__setattr__(
            self,
            "project_root",
            Path(project_root).expanduser().resolve(),
        )

    def snapshot(self) -> dict[str, Any]:
        from .canon_v3.service import CanonV3Service

        raw = CanonV3Service(self.project_root).workflow_snapshot()
        return normalize_workflow_snapshot(raw)

    def require_fresh_projection(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return one projection proven to match the exact authoritative HEAD."""

        from .canon_v3.projection import read_projection

        workflow = self.snapshot()
        head = workflow.get("head_hash")
        generation = int(workflow.get("generation") or 0)
        readable_states = {
            "ready",
            "ready_to_finalize",
            "awaiting_human",
            "rewrite_required",
            "recompile_required",
        }
        if (
            not head
            or not workflow.get("projection_fresh")
            or workflow.get("state") not in readable_states
        ):
            raise CanonReadModelUnavailable(
                "canon_v3_head_projection_unavailable:"
                f"state={workflow.get('state')};"
                f"action={(workflow.get('primary_action') or {}).get('code')}"
            )
        projection = read_projection(self.project_root, require_fresh=True)
        binding = projection.get("binding") if isinstance(projection, dict) else {}
        if (
            not isinstance(binding, dict)
            or binding.get("head_hash") != head
            or int(
                binding.get("generation")
                if binding.get("generation") is not None
                else -1
            )
            != generation
        ):
            raise CanonReadModelUnavailable(
                "canon_v3_projection_workflow_binding_mismatch"
            )
        return workflow, projection

    def assert_legacy_fact_mutation_disabled(self, operation: str) -> None:
        """Reject v1/v2 mutations regardless of whether ``CURRENT`` exists."""

        workflow = self.snapshot()
        compatibility_code = {
            "chapter_commit": "canon_v3_active_v2_write_disabled",
            "human_review": "canon_v3_active_v2_human_review_disabled",
        }.get(str(operation or ""), "canon_v3_legacy_mutation_disabled")
        raise LegacyFactMutationDisabled(
            f"{compatibility_code}:legacy_fact_mutation_disabled:"
            f"operation={str(operation or 'unknown')};"
            f"state={workflow.get('state')};"
            f"action={(workflow.get('primary_action') or {}).get('code')}"
        )


__all__ = [
    "CanonReadModelUnavailable",
    "LegacyFactMutationDisabled",
    "WORKFLOW_AUTHORITY_SCHEMA",
    "WorkflowAuthority",
    "WorkflowAuthorityError",
    "normalize_workflow_snapshot",
]

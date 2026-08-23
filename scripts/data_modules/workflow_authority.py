#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single public workflow authority for every book-project surface.

``CURRENT`` is an implementation detail of the Canon v3 repository, not a
feature flag.  Public callers must consult this module even before a v3 HEAD
exists so an uninitialised or legacy project fails closed instead of silently
falling back to v2 fact writers/read models.
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
    state = str(snapshot.get("state") or "invalid")
    head = snapshot.get("head_hash")
    if action in {
        "recertify_legacy_v1",
        "review_and_publish_legacy_recertification",
        "resolve_recertification_staging_conflict",
        "audit_blocked_legacy_recertification",
    } or snapshot.get("authoritative_transaction") == "legacy_recertification":
        return "recertification"
    if not head and action == "initialize_v3":
        return "new_project"
    if not head and action == "migrate_legacy":
        return "legacy_cutover"
    if action in {"remigrate_legacy_suffix", "repair_legacy_prefix"}:
        return "legacy_repair"
    if action in {
        "supersede_legacy_soft_facts",
        "classify_legacy_fact_boundary",
        "fork_legacy_fact_boundary",
        "audit_legacy_fact_boundary",
    }:
        return "legacy_fact_boundary"
    if action == "supersede_active_soft_author_axioms":
        return "author_axiom_fact_boundary"
    if state == "migration_required" and head:
        return "recertification"
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
        "migration_required": (
            recovery,
            (
                "初始化 Canon v3"
                if recovery == "initialize_v3"
                else "迁移并重新认证旧正史"
            ),
            (
                "canon_ledger.py canon-v3 initialize"
                if recovery == "initialize_v3"
                else "canon_ledger.py canon-v3 migrate"
            ),
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
    if state == "migration_required" and recovery in {
        "recertify_legacy_v1",
        "review_and_publish_legacy_recertification",
    }:
        return _structured_action(
            code="review_and_publish_legacy_recertification",
            label="逐项确认旧正史并原子发布重新认证链",
            command="canon_ledger.py canon-v3 repair-cutover --apply --input-file <request.json>",
            transaction_kind=transaction_kind,
        )
    if state == "migration_required" and recovery in {
        "remigrate_legacy_suffix",
        "repair_legacy_prefix",
    }:
        # A frozen v2 prefix that changed after cutover cannot be made safe by
        # calling ``migrate`` again: migrate_legacy deliberately rejects an
        # existing stale CURRENT.  The only universally executable next step
        # is the read-only audit.  It identifies the exact invalid source so a
        # human can restore the frozen bytes or explicitly rebuild the suffix;
        # status must then be re-read before any write is attempted.
        return _structured_action(
            code=recovery,
            label="审计失效的旧前缀并由作者修复精确来源",
            command="canon_ledger.py canon-v3 audit-cutover",
            transaction_kind=transaction_kind,
        )
    if state == "migration_required" and recovery in {
        "supersede_legacy_soft_facts",
        "classify_legacy_fact_boundary",
        "audit_legacy_fact_boundary",
    }:
        labels = {
            "supersede_legacy_soft_facts": (
                "审阅 v2 软事实清理计划，再生成完整 author-axiom supersession"
            ),
            "classify_legacy_fact_boundary": (
                "逐项人工分类 v2 自定义设定叶子，保持项目只读"
            ),
            "audit_legacy_fact_boundary": "审计 v2 事实边界，保持项目只读",
        }
        return _structured_action(
            code=recovery,
            label=labels[recovery],
            command="canon_ledger.py canon-v3 repair-cutover --dry-run",
            transaction_kind=transaction_kind,
        )
    if state == "migration_required" and recovery == (
        "supersede_active_soft_author_axioms"
    ):
        return _structured_action(
            code=recovery,
            label="旧 active author axioms 含软偏好；逐项人工移除后再继续写作",
            command="/canon-ledger-plan",
            transaction_kind="author_axiom",
        )
    if state == "migration_required" and recovery == (
        "fork_legacy_fact_boundary"
    ):
        return _structured_action(
            code=recovery,
            label="活动后缀依赖旧软 admission；保留原项目只读并在 clean target 建立分支",
            command="/canon-ledger-init",
            transaction_kind=transaction_kind,
        )
    if state == "migration_required" and recovery == (
        "resolve_recertification_staging_conflict"
    ):
        stage_kind = str(
            snapshot.get("conflicting_transaction_kind") or ""
        )
        stage_digest = str(snapshot.get("conflicting_stage_digest") or "")
        if stage_kind in {"chapter", "author_axiom"} and len(stage_digest) == 64:
            return _structured_action(
                code=recovery,
                label="精确归档冲突的未发布事实事务，再继续旧正史认证",
                command=(
                    "canon_ledger.py canon-v3 archive-staging "
                    f"--transaction-kind {stage_kind} "
                    f"--expected-stage-digest {stage_digest}"
                ),
                transaction_kind=stage_kind,
                parameters={
                    "transaction_kind": stage_kind,
                    "expected_stage_digest": stage_digest,
                },
                action_id="archive_conflicting_staging",
            )
        return _structured_action(
            code="run_canon_v3_doctor",
            label="冲突的 STAGING 无法建立精确摘要；先运行完整性检查",
            command="canon_ledger.py doctor --deep",
            transaction_kind=transaction_kind,
        )
    if state == "migration_required" and recovery == (
        "audit_blocked_legacy_recertification"
    ):
        return _structured_action(
            code=recovery,
            label="旧正史重新认证审计失败，保持当前 HEAD 并先修复来源",
            command="canon_ledger.py canon-v3 audit-cutover",
            transaction_kind=transaction_kind,
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

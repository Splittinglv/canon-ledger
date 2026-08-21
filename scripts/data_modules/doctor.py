#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .config import DataModulesConfig
from .project_phase import (
    INIT_REQUIRED_DIRS,
    INIT_REQUIRED_FILES,
    PHASE_INIT_READY,
    PHASE_INIT_SCAFFOLDED,
    PHASE_NO_PROJECT,
    ProjectPhaseSnapshot,
    contract_files_for_chapter,
    resolve_project_phase,
)
from .projection_log import (
    latest_projection_run,
    projection_log_path,
    projection_run_failed,
    projection_run_pending,
)
from .story_runtime_health import build_story_runtime_health
from .commit_lineage import list_needs_revalidation
from .workflow_authority import WORKFLOW_AUTHORITY_SCHEMA, WorkflowAuthority


SCHEMA_VERSION = "canon-ledger-doctor/v1"
CHECK_OK = "ok"
CHECK_WARNING = "warning"
CHECK_ERROR = "error"
CHECK_SKIPPED = "skipped"
CANON_V3_STATES = {
    "ready",
    "ready_to_finalize",
    "awaiting_human",
    "rewrite_required",
    "recompile_required",
    "projection_rebuild_required",
    "migration_required",
    "invalid",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _check(
    check_id: str,
    *,
    status: str,
    severity: str,
    message: str,
    path: str = "",
    expected: str = "",
    actual: str = "",
    impact: str = "",
    repair: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "severity": severity,
        "message": message,
        "path": path,
        "expected": expected,
        "actual": actual,
        "impact": impact,
        "repair": repair,
    }


def _rel(project_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"invalid json: {exc}"
    except OSError as exc:
        return {}, f"read error: {exc}"
    if not isinstance(payload, dict):
        return {}, "json root is not object"
    return payload, ""


def _workflow_primary_command(workflow: dict[str, Any]) -> str:
    primary = workflow.get("primary_action")
    if not isinstance(primary, dict):
        return "canon_ledger.py canon-v3 status"
    command = str(primary.get("command") or "").strip()
    return command or "canon_ledger.py canon-v3 status"


def _workflow_summary(workflow: dict[str, Any]) -> str:
    primary = workflow.get("primary_action")
    primary = primary if isinstance(primary, dict) else {}
    return json.dumps(
        {
            "schema_version": workflow.get("schema_version"),
            "state": workflow.get("state"),
            "bootstrap_mode": workflow.get("bootstrap_mode"),
            "transaction_kind": workflow.get("transaction_kind"),
            "chapter": workflow.get("chapter"),
            "head_hash": workflow.get("head_hash"),
            "generation": workflow.get("generation"),
            "stage_digest": workflow.get("stage_digest"),
            "projection_fresh": workflow.get("projection_fresh"),
            "can_write_next": workflow.get("can_write_next"),
            "primary_action": {
                "code": primary.get("code"),
                "command": primary.get("command"),
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _canon_v3_checks(
    project_root: Path,
    *,
    deep: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Validate the exact public v3 authority before compatibility surfaces."""

    checks: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "current": {},
        "staging": {},
        "author_axioms": {},
        "projection": {},
        "cutover_audit": {},
    }
    workflow_available = True
    try:
        workflow = WorkflowAuthority(project_root).snapshot()
    except Exception as exc:
        workflow_available = False
        workflow = {}
        checks.append(
            _check(
                "canon_v3.workflow_snapshot",
                status=CHECK_ERROR,
                severity="blocker",
                message="Canon v3 workflow snapshot unavailable",
                path=str(project_root / ".story-system" / "v3"),
                expected=WORKFLOW_AUTHORITY_SCHEMA,
                actual=str(exc),
                impact="无法确定 HEAD、STAGING 或唯一恢复动作，不能继续写作。",
                repair="运行 canon_ledger.py canon-v3 status；若仍失败，再运行 /canon-ledger-doctor --deep。",
            )
        )

    state = str(workflow.get("state") or "invalid")
    digest = str(workflow.get("workflow_digest") or "")
    view_digest = str(workflow.get("authority_view_digest") or "")
    schema_ok = workflow.get("schema_version") == WORKFLOW_AUTHORITY_SCHEMA
    shape_ok = (
        state in CANON_V3_STATES
        and bool(_SHA256_RE.fullmatch(digest))
        and bool(_SHA256_RE.fullmatch(view_digest))
    )
    ready = bool(
        schema_ok
        and shape_ok
        and state == "ready"
        and workflow.get("head_hash")
        and workflow.get("can_write_next")
        and workflow.get("projection_fresh")
    )
    if workflow_available:
        checks.append(
            _check(
                "canon_v3.workflow_snapshot",
                status=CHECK_OK if ready else CHECK_ERROR,
                severity="info" if ready else "blocker",
                message=f"Canon v3 workflow state: {state}",
                path=str(project_root / ".story-system" / "v3"),
                expected=(
                    f"{WORKFLOW_AUTHORITY_SCHEMA}; ready + can_write_next + fresh projection"
                ),
                actual=_workflow_summary(workflow),
                impact=(
                    ""
                    if ready
                    else "当前权威状态不允许开始下一章；兼容索引或旧报告不能覆盖它。"
                ),
                repair="" if ready else _workflow_primary_command(workflow),
            )
        )

    primary = workflow.get("primary_action")
    primary_ok = bool(
        isinstance(primary, dict)
        and str(primary.get("code") or "").strip()
        and str(primary.get("command") or "").strip()
    )
    if workflow_available:
        checks.append(
            _check(
                "canon_v3.primary_action",
                status=CHECK_OK if primary_ok else CHECK_ERROR,
                severity="info" if primary_ok else "blocker",
                message="workflow exposes one author-facing recovery action",
                expected="primary_action.code + primary_action.command",
                actual=json.dumps(primary or {}, ensure_ascii=False, sort_keys=True),
                impact="" if primary_ok else "无法给作者提供确定、可重放的恢复动作。",
                repair=(
                    ""
                    if primary_ok
                    else "运行 canon_ledger.py canon-v3 status 获取新的 primary_action。"
                ),
            )
        )

    from .canon_v3.repository import CanonV3Repository

    repository = CanonV3Repository(project_root)
    workflow_head = workflow.get("head_hash")
    current_head: str | None = None
    current_generation: int | None = None
    try:
        current_head = repository.current_head(validate=True)
        diagnostics["current"] = {
            "head_hash": current_head,
            "workflow_head_hash": workflow_head,
        }
        if workflow_available and current_head != workflow_head:
            raise ValueError(
                f"workflow/CURRENT mismatch: workflow={workflow_head}, current={current_head}"
            )
        if current_head is None:
            if not workflow_available or state != "migration_required":
                raise ValueError("CURRENT missing outside migration_required")
            checks.append(
                _check(
                    "canon_v3.current_manifest",
                    status=CHECK_SKIPPED,
                    severity="info",
                    message="CURRENT not published yet",
                    path=str(repository.current_path),
                    expected="absent only before initialize/migrate",
                    actual=f"bootstrap_mode={workflow.get('bootstrap_mode')}",
                    impact="由 workflow blocker 和 primary_action 决定下一步。",
                    repair=_workflow_primary_command(workflow),
                )
            )
        else:
            manifest = repository.read_manifest(
                current_head,
                validate_references=True,
            )
            current_generation = int(manifest.get("generation") or 0)
            chapter_commits = repository.current_commits()
            axiom_commits = repository.current_author_axiom_commits()
            diagnostics["current"].update(
                {
                    "generation": int(manifest.get("generation") or 0),
                    "chapter_commit_count": len(chapter_commits),
                    "author_axiom_commit_count": len(axiom_commits),
                    "reachable_objects_validated": True,
                    "deep": bool(deep),
                }
            )
            if workflow_available and current_generation != int(
                workflow.get("generation") or 0
            ):
                raise ValueError("workflow/CURRENT generation mismatch")
            checks.append(
                _check(
                    "canon_v3.current_manifest",
                    status=CHECK_OK,
                    severity="info",
                    message="CURRENT manifest and reachable immutable objects valid",
                    path=str(repository.current_path),
                    expected="content-addressed manifest/commit/transaction/decision references",
                    actual=json.dumps(
                        diagnostics["current"], ensure_ascii=False, sort_keys=True
                    ),
                )
            )
    except Exception as exc:
        diagnostics["current"]["error"] = str(exc)
        checks.append(
            _check(
                "canon_v3.current_manifest",
                status=CHECK_ERROR,
                severity="blocker",
                message="CURRENT manifest or reachable immutable object invalid",
                path=str(repository.current_path),
                expected="workflow HEAD equals a fully validated CURRENT manifest",
                actual=str(exc),
                impact="活动正史的内容寻址或引用链不可信，必须保持只读。",
                repair=_workflow_primary_command(workflow),
            )
        )

    authority_head = workflow_head if workflow_available else current_head
    authority_generation = (
        int(workflow.get("generation") or 0)
        if workflow_available
        else current_generation
    )

    from .canon_v3.author_axiom import AuthorAxiomStagingPointer
    from .canon_v3.service import CanonV3Service, StagingPointer
    from .canon_v3.staging_authority import authoritative_staging_kinds

    try:
        staging_kinds = authoritative_staging_kinds(project_root)
        diagnostics["staging"] = {"kinds": list(staging_kinds)}
        if len(staging_kinds) > 1:
            raise ValueError(
                "multiple authoritative staging files: " + ",".join(staging_kinds)
            )
        pointer: Any = None
        if staging_kinds:
            kind = staging_kinds[0]
            relative = (
                Path(".story-system/v3/STAGING.json")
                if kind == "chapter"
                else Path(".story-system/v3/AUTHOR_AXIOM_STAGING.json")
            )
            raw, error = _read_json(project_root / relative)
            if error:
                raise ValueError(f"{kind} STAGING unreadable: {error}")
            pointer = (
                StagingPointer.model_validate(raw)
                if kind == "chapter"
                else AuthorAxiomStagingPointer.model_validate(raw)
            )
            if kind == "chapter" and not pointer.is_v2:
                raise ValueError("unpublished v1 STAGING is not authoritative")
            diagnostics["staging"].update(
                {
                    "schema_version": pointer.schema_version,
                    "transaction_hash": pointer.transaction_hash,
                    "stage_digest": pointer.stage_digest,
                }
            )
            if not workflow_available:
                raise ValueError("workflow unavailable; cannot verify STAGING binding")
            expected_kind = str(workflow.get("transaction_kind") or "chapter")
            if expected_kind != kind:
                raise ValueError(
                    f"workflow/STAGING kind mismatch: workflow={expected_kind}, file={kind}"
                )
            if pointer.transaction_hash != workflow.get("transaction_hash"):
                raise ValueError("workflow/STAGING transaction mismatch")
            if pointer.stage_digest != workflow.get("stage_digest"):
                raise ValueError("workflow/STAGING stage digest mismatch")
        elif workflow_available and workflow.get("transaction_hash") and str(
            workflow.get("transaction_kind") or ""
        ) in {"chapter", "author_axiom"}:
            raise ValueError("workflow transaction exists without its STAGING file")
        checks.append(
            _check(
                "canon_v3.staging",
                status=CHECK_OK,
                severity="info",
                message="authoritative STAGING is singular and exact-version bound",
                path=str(project_root / ".story-system" / "v3"),
                expected="zero or one chapter/author-axiom STAGING",
                actual=json.dumps(
                    diagnostics["staging"], ensure_ascii=False, sort_keys=True
                ),
            )
        )
    except Exception as exc:
        diagnostics["staging"]["error"] = str(exc)
        checks.append(
            _check(
                "canon_v3.staging",
                status=CHECK_ERROR,
                severity="blocker",
                message="STAGING authority or exact version binding invalid",
                path=str(project_root / ".story-system" / "v3"),
                expected="one staging kind bound to workflow transaction/stage digest",
                actual=str(exc),
                impact="旧选择可能被应用到错误事务，禁止决定或发布。",
                repair=_workflow_primary_command(workflow),
            )
        )

    service = CanonV3Service(project_root)
    try:
        active_axioms = service.active_author_axioms()
        axiom_status = service.author_axiom_status()
        diagnostics["author_axioms"] = {
            "schema_version": active_axioms.get("schema_version"),
            "head_hash": active_axioms.get("head_hash"),
            "author_axiom_digest": active_axioms.get("author_axiom_digest"),
            "record_count": len(active_axioms.get("records") or []),
            "genesis_admission_count": len(
                active_axioms.get("genesis_admissions") or []
            ),
            "workflow_state": axiom_status.get("state"),
            "transaction_hash": axiom_status.get("transaction_hash"),
        }
        if active_axioms.get("head_hash") != authority_head:
            raise ValueError("active author-axiom snapshot HEAD mismatch")
        if workflow_available and active_axioms.get(
            "author_axiom_digest"
        ) != workflow.get("author_axiom_digest"):
            raise ValueError("active author-axiom digest mismatch")
        if workflow_available and str(
            workflow.get("transaction_kind") or ""
        ) == "author_axiom":
            if axiom_status.get("transaction_hash") != workflow.get(
                "transaction_hash"
            ):
                raise ValueError("author-axiom workflow transaction mismatch")
        checks.append(
            _check(
                "canon_v3.author_axioms",
                status=CHECK_OK,
                severity="info",
                message="active author axioms and axiom workflow bind the same HEAD",
                expected="HEAD-bound immutable axiom snapshot",
                actual=json.dumps(
                    diagnostics["author_axioms"], ensure_ascii=False, sort_keys=True
                ),
            )
        )
    except Exception as exc:
        diagnostics["author_axioms"]["error"] = str(exc)
        checks.append(
            _check(
                "canon_v3.author_axioms",
                status=CHECK_ERROR,
                severity="blocker",
                message="author-axiom authority invalid",
                expected="active digest and transaction match workflow HEAD/STAGING",
                actual=str(exc),
                impact="硬设定版本无法可靠用于查询或写作。",
                repair=_workflow_primary_command(workflow),
            )
        )

    if authority_head:
        from .canon_v3.projection import projection_path, read_projection

        canon_projection_path = projection_path(project_root)
        try:
            projection = read_projection(project_root, require_fresh=True)
            binding = projection.get("binding") or {}
            diagnostics["projection"] = dict(binding)
            if (
                binding.get("head_hash") != authority_head
                or int(binding.get("generation") or 0)
                != int(authority_generation or 0)
                or (workflow_available and not workflow.get("projection_fresh"))
            ):
                raise ValueError("projection/workflow binding mismatch")
            checks.append(
                _check(
                    "canon_v3.projection",
                    status=CHECK_OK,
                    severity="info",
                    message="Canon projection is fresh for the exact HEAD",
                    path=str(canon_projection_path),
                    expected="projection binding equals workflow HEAD/generation",
                    actual=json.dumps(binding, ensure_ascii=False, sort_keys=True),
                )
            )
        except Exception as exc:
            diagnostics["projection"]["error"] = str(exc)
            repair = _workflow_primary_command(workflow)
            if (workflow.get("primary_action") or {}).get("code") == (
                "rebuild_projection"
            ):
                repair = "canon_ledger.py canon-v3 rebuild-projection"
            checks.append(
                _check(
                    "canon_v3.projection",
                    status=CHECK_ERROR,
                    severity="blocker",
                    message="Canon projection is missing, stale, or bound to another HEAD",
                    path=str(canon_projection_path),
                    expected="fresh HEAD/generation-bound projection",
                    actual=str(exc),
                    impact="事实查询和下一章上下文不得回退 legacy index。",
                    repair=repair,
                )
            )
    else:
        checks.append(
            _check(
                "canon_v3.projection",
                status=CHECK_SKIPPED,
                severity="info",
                message="projection not expected before CURRENT publication",
                expected="initialize/migrate publishes CURRENT, then builds projection",
                actual=f"bootstrap_mode={workflow.get('bootstrap_mode')}",
                repair=_workflow_primary_command(workflow),
            )
        )

    bootstrap_mode = str(workflow.get("bootstrap_mode") or "")
    cutover = workflow.get("cutover_chapter")
    should_audit_cutover = bool(
        bootstrap_mode in {"legacy_cutover", "legacy_repair", "recertification"}
        or (isinstance(cutover, int) and cutover > 0)
    )
    if should_audit_cutover:
        from .canon_v3.migration import audit_cutover

        try:
            audit = audit_cutover(
                project_root,
                cutover_chapter=(
                    int(cutover) if isinstance(cutover, int) and cutover >= 0 else None
                ),
            )
            diagnostics["cutover_audit"] = {
                "schema_version": audit.get("schema_version"),
                "state": audit.get("state"),
                "requires_recertification": audit.get("requires_recertification"),
                "required_case_count": audit.get("required_case_count"),
                "reason_codes": list(audit.get("reason_codes") or []),
                "detached_plan_digest": audit.get("detached_plan_digest"),
            }
            audit_state = str(audit.get("state") or "blocked")
            blocked = audit_state == "blocked"
            checks.append(
                _check(
                    "canon_v3.cutover_audit",
                    status=CHECK_ERROR if blocked else CHECK_OK,
                    severity="blocker" if blocked else "info",
                    message=f"legacy cutover audit: {audit_state}",
                    expected="ready or exact needs_recertification plan",
                    actual=json.dumps(
                        diagnostics["cutover_audit"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    impact=(
                        "旧前缀、证据、目标或身份准入未通过，只能保持只读。"
                        if blocked
                        else ""
                    ),
                    repair="" if not blocked else _workflow_primary_command(workflow),
                )
            )
        except Exception as exc:
            diagnostics["cutover_audit"]["error"] = str(exc)
            checks.append(
                _check(
                    "canon_v3.cutover_audit",
                    status=CHECK_ERROR,
                    severity="blocker",
                    message="legacy cutover audit failed",
                    expected="deterministic read-only cutover audit",
                    actual=str(exc),
                    impact="不能证明旧前缀可迁移或重新认证。",
                    repair=_workflow_primary_command(workflow),
                )
            )
    else:
        diagnostics["cutover_audit"] = {"state": "not_applicable"}
        checks.append(
            _check(
                "canon_v3.cutover_audit",
                status=CHECK_SKIPPED,
                severity="info",
                message="legacy cutover audit not applicable",
                actual=f"bootstrap_mode={bootstrap_mode};cutover={cutover}",
            )
        )

    return checks, workflow, diagnostics


def _expected_profile(snapshot: ProjectPhaseSnapshot) -> dict[str, Any]:
    expected_files = list(INIT_REQUIRED_FILES)
    expected_dirs = list(INIT_REQUIRED_DIRS)
    if snapshot.phase not in {PHASE_NO_PROJECT, PHASE_INIT_SCAFFOLDED, PHASE_INIT_READY}:
        expected_files.extend(snapshot.missing_contract_files)
    if snapshot.target_chapter > 0 and snapshot.phase not in {PHASE_NO_PROJECT, PHASE_INIT_SCAFFOLDED, PHASE_INIT_READY}:
        expected_files.extend(
            str(path.relative_to(Path(snapshot.project_root)))
            for path in contract_files_for_chapter(Path(snapshot.project_root), snapshot.target_chapter).values()
        )
    return {
        "phase": snapshot.phase,
        "target_chapter": snapshot.target_chapter,
        "files": sorted(set(expected_files)),
        "dirs": sorted(set(expected_dirs)),
    }


def _preflight_checks(preflight_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not preflight_report:
        return []
    checks: list[dict[str, Any]] = []
    for item in preflight_report.get("checks") or []:
        if not isinstance(item, dict):
            continue
        ok = bool(item.get("ok"))
        name = str(item.get("name") or "unknown")
        checks.append(
            _check(
                f"preflight.{name}",
                status=CHECK_OK if ok else CHECK_ERROR,
                severity="info" if ok else "blocker",
                message=f"preflight {name} {'ok' if ok else 'failed'}",
                path=str(item.get("path") or ""),
                actual=str(item.get("error") or ""),
                impact="" if ok else "统一 CLI 或项目解析可能不可用。",
                repair="" if ok else "先修复 preflight 输出的路径或 project_root 问题。",
            )
        )
    return checks


def _file_checks(project_root: Path, snapshot: ProjectPhaseSnapshot) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel in INIT_REQUIRED_DIRS:
        path = project_root / rel
        exists = path.is_dir()
        checks.append(
            _check(
                f"file.dir.{rel}",
                status=CHECK_OK if exists else CHECK_ERROR,
                severity="info" if exists else "blocker",
                message=f"required directory {rel}",
                path=str(path),
                expected="directory exists",
                actual="exists" if exists else "missing",
                impact="" if exists else "项目骨架不完整，后续写作/备份/报告可能写入失败。",
                repair="" if exists else "重新运行 /canon-ledger-init，或手动创建该目录后再运行 doctor。",
            )
        )
    for rel in INIT_REQUIRED_FILES:
        path = project_root / rel
        exists = path.is_file()
        checks.append(
            _check(
                f"file.required.{rel}",
                status=CHECK_OK if exists else CHECK_ERROR,
                severity="info" if exists else "blocker",
                message=f"required file {rel}",
                path=str(path),
                expected="file exists",
                actual="exists" if exists else "missing",
                impact="" if exists else "项目初始化产物缺失，当前阶段判断和后续流程会不可靠。",
                repair="" if exists else "使用 /canon-ledger-init 补齐项目骨架，或按 init_project.py 模板补齐文件。",
            )
        )

    if snapshot.phase not in {PHASE_NO_PROJECT, PHASE_INIT_SCAFFOLDED, PHASE_INIT_READY} and snapshot.target_chapter > 0:
        for name, path in contract_files_for_chapter(project_root, snapshot.target_chapter).items():
            exists = path.is_file()
            checks.append(
                _check(
                    f"file.contract.{name}",
                    status=CHECK_OK if exists else CHECK_ERROR,
                    severity="info" if exists else "blocker",
                    message=f"story contract {name}",
                    path=str(path),
                    expected="file exists",
                    actual="exists" if exists else "missing",
                    impact="" if exists else "写章上下文缺少当前主链合同，无法可靠绑定设定、时间线和章纲目标。",
                    repair="" if exists else "运行 canon_ledger.py story-system ... --persist --emit-runtime-contracts --chapter N。",
                )
            )
    return checks


def _json_checks(project_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    json_files = [
        project_root / ".canon-ledger" / "state.json",
        project_root / ".story-system" / "MASTER_SETTING.json",
    ]
    for path in json_files:
        if not path.exists():
            checks.append(
                _check(
                    f"json.{_rel(project_root, path)}",
                    status=CHECK_SKIPPED,
                    severity="info",
                    message=f"{_rel(project_root, path)} not present",
                    path=str(path),
                    expected="valid JSON object when present",
                    actual="missing",
                )
            )
            continue
        payload, error = _read_json(path)
        checks.append(
            _check(
                f"json.{_rel(project_root, path)}",
                status=CHECK_OK if not error else CHECK_ERROR,
                severity="info" if not error else "blocker",
                message=f"{_rel(project_root, path)} json parse",
                path=str(path),
                expected="valid JSON object",
                actual="ok" if not error else error,
                impact="" if not error else "JSON 无法读取会导致 CLI、dashboard 或状态推导失败。",
                repair="" if not error else "用 UTF-8 修复 JSON 格式；必要时从 git/backup 恢复。",
            )
        )
        if path.name == "state.json" and not error:
            for key in ("project_info", "progress"):
                checks.append(
                    _check(
                        f"json.state.{key}",
                        status=CHECK_OK if isinstance(payload.get(key), dict) else CHECK_ERROR,
                        severity="info" if isinstance(payload.get(key), dict) else "blocker",
                        message=f"state.json contains {key}",
                        path=str(path),
                        expected="object field",
                        actual=type(payload.get(key)).__name__,
                        impact="" if isinstance(payload.get(key), dict) else "当前项目状态投影不符合 CanonLedger 7 schema。",
                        repair="" if isinstance(payload.get(key), dict) else "从当前项目备份恢复 state.json，或新建 CanonLedger 项目。",
                    )
                )
    return checks


def _sqlite_table_count(path: Path, table: str) -> tuple[bool, int, str]:
    if not path.is_file():
        return False, 0, "missing"
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not row:
                return False, 0, "table_missing"
            count_row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return True, int(count_row[0] or 0) if count_row else 0, ""
    except sqlite3.Error as exc:
        return False, 0, str(exc)


def _sqlite_checks(project_root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    cfg = DataModulesConfig.from_project_root(project_root)
    for db_path, table, check_id, impact in (
        (cfg.index_db, "chapters", "sqlite.index_db.chapters", "查询、关系图谱和 dashboard 章节统计会降级。"),
        (cfg.vector_db, "vectors", "sqlite.vector_db.vectors", "RAG 事实索引不可用，写作仍可继续，但无法补查已提交章节。"),
    ):
        exists = db_path.is_file()
        table_ok, count, error = _sqlite_table_count(db_path, table)
        if not exists:
            checks.append(
                _check(
                    check_id,
                    status=CHECK_WARNING,
                    severity="warning",
                    message=f"{db_path.name} missing",
                    path=str(db_path),
                    expected=f"sqlite db with {table} table",
                    actual="missing",
                    impact=impact,
                    repair=(
                        "该数据库只作兼容增强；先运行 canon_ledger.py canon-v3 status，"
                        "并只执行返回的 primary_action。"
                    ),
                )
            )
            continue
        checks.append(
            _check(
                check_id,
                status=CHECK_OK if table_ok else CHECK_WARNING,
                severity="info" if table_ok else "warning",
                message=f"{db_path.name}.{table}",
                path=str(db_path),
                expected=f"{table} table readable",
                actual=f"rows={count}" if table_ok else error,
                impact="" if table_ok else impact,
                repair=(
                    ""
                    if table_ok
                    else "该数据库不具备 Canon 权威；先按 canon-v3 status 的 primary_action 恢复。"
                ),
            )
        )
    return checks


def _rag_checks(project_root: Path) -> list[dict[str, Any]]:
    cfg = DataModulesConfig.from_project_root(project_root)
    checks: list[dict[str, Any]] = []
    for key, present, base_url, model, fallback in (
        (
            "embed",
            bool(str(cfg.embed_api_key or "").strip()),
            cfg.embed_base_url,
            cfg.embed_model,
            "BM25 关键词召回仍可用；仅语义向量召回未启用。",
        ),
        (
            "rerank",
            bool(str(cfg.rerank_api_key or "").strip()),
            cfg.rerank_base_url,
            cfg.rerank_model,
            "召回结果仍可用；仅远程精排未启用。",
        ),
    ):
        checks.append(
            _check(
                f"rag.{key}.api_key",
                status=CHECK_OK,
                severity="info",
                message=(
                    f"{key} api key configured"
                    if present
                    else f"{key} api key not configured (optional)"
                ),
                expected="api key present in env or .env",
                actual=f"present; model={model}; base_url={base_url}" if present else f"missing; model={model}; base_url={base_url}",
                impact="" if present else fallback,
                repair="" if present else "如需可选增强，复制 .env.example 为 .env 并填写对应 API key；不要提交真实 key。",
            )
        )
    if cfg.vector_db.is_file():
        unsupported_rows = 0
        provenance_error = ""
        try:
            uri = f"{cfg.vector_db.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as conn:
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "vectors" in tables:
                    columns = {
                        str(row[1])
                        for row in conn.execute("PRAGMA table_info(vectors)").fetchall()
                    }
                    if "source_file" not in columns:
                        provenance_error = "unsupported_schema_missing_source_file"
                    else:
                        row = conn.execute(
                            """
                            SELECT COUNT(*) FROM vectors
                            WHERE source_file IS NULL
                               OR source_file = ''
                               OR source_file NOT LIKE 'commit:chapter_%:%'
                            """
                        ).fetchone()
                        unsupported_rows = int(row[0] or 0) if row else 0
                else:
                    provenance_error = "vectors_table_missing"
        except sqlite3.Error as exc:
            provenance_error = f"sqlite_error:{exc.__class__.__name__}"
        needs_rebuild = bool(unsupported_rows or provenance_error)
        checks.append(
            _check(
                "rag.retrieval_provenance",
                status=CHECK_WARNING if needs_rebuild else CHECK_OK,
                severity="warning" if needs_rebuild else "info",
                message="retrieval rows bound to accepted commit snapshots",
                expected="all default-context rows carry a commit snapshot marker",
                actual=(
                    f"unsupported_or_unbound_rows={unsupported_rows}; schema={provenance_error}"
                    if provenance_error
                    else f"unsupported_or_unbound_rows={unsupported_rows}"
                ),
                impact=(
                    "兼容向量库含未绑定行或不受支持的结构，不能把它当作 Canon 事实源。"
                    if needs_rebuild
                    else ""
                ),
                repair=(
                    "运行 canon_ledger.py canon-v3 status，并只执行返回的 primary_action；"
                    "不要运行任何 v2 projection 补跑命令恢复 Canon。"
                    if needs_rebuild
                    else ""
                ),
            )
        )
    return checks


def _projection_log_checks(project_root: Path, snapshot: ProjectPhaseSnapshot) -> list[dict[str, Any]]:
    log_path = projection_log_path(project_root)
    latest_commit = snapshot.latest_commit
    if latest_commit is None:
        return [
            _check(
                "projection_log.present",
                status=CHECK_SKIPPED,
                severity="info",
                message="no commit yet; projection log not required",
                path=str(log_path),
                expected="projection log after first commit",
                actual="no commit",
            )
        ]
    if not log_path.is_file():
        return [
            _check(
                "projection_log.present",
                status=CHECK_WARNING,
                severity="warning",
                message="projection log missing for project with commits",
                path=str(log_path),
                expected="projection_log.jsonl exists after projection run",
                actual="missing",
                impact="旧 projection 日志缺失；Canon v3 freshness 由 HEAD-bound projection 单独校验。",
                repair=(
                    "运行 canon_ledger.py canon-v3 status，并只执行返回的 primary_action；"
                    "不要补跑任何 v2 projection 队列。"
                ),
            )
        ]
    latest = latest_projection_run(project_root, chapter=latest_commit.chapter)
    if not latest:
        return [
            _check(
                "projection_log.latest_run",
                status=CHECK_WARNING,
                severity="warning",
                message="projection log has no run for latest commit",
                path=str(log_path),
                expected=f"run for chapter {latest_commit.chapter}",
                actual="missing",
                impact="最新旧式 projection 执行历史不可见，但它不能覆盖 Canon v3 workflow。",
                repair=(
                    "运行 canon_ledger.py canon-v3 status，并只执行返回的 primary_action；"
                    "不要补跑任何 v2 projection 队列。"
                ),
            )
        ]
    failed = projection_run_failed(latest)
    pending = projection_run_pending(latest)
    status_ok = not failed and not pending
    return [
        _check(
            "projection_log.latest_run",
            status=CHECK_OK if status_ok else CHECK_WARNING,
            severity="info" if status_ok else "warning",
            message="latest projection log run",
            path=str(log_path),
            expected="latest run status done/skipped",
            actual=f"chapter={latest.get('chapter')} status={latest.get('status')}",
            impact=(
                "旧 read-model projection 未完成；当前事实可用性只看 Canon v3 projection。"
                if not status_ok
                else ""
            ),
            repair=(
                "运行 canon_ledger.py canon-v3 status，并只执行返回的 primary_action；"
                "不要运行任何 v2 projection 补跑命令恢复 Canon。"
                if not status_ok
                else ""
            ),
        )
    ]


def _python_checks() -> list[dict[str, Any]]:
    checks = [
        _check(
            "python.version",
            status=CHECK_OK if sys.version_info >= (3, 10) else CHECK_ERROR,
            severity="info" if sys.version_info >= (3, 10) else "blocker",
            message="python version",
            expected=">= 3.10",
            actual=platform.python_version(),
            impact="" if sys.version_info >= (3, 10) else "运行时依赖 Python 3.10+ 语法和库行为。",
            repair="" if sys.version_info >= (3, 10) else "切换到 Python 3.10 或更高版本。",
        )
    ]
    for module_name in ("aiohttp", "filelock", "pydantic"):
        found = importlib.util.find_spec(module_name) is not None
        checks.append(
            _check(
                f"python.import.{module_name}",
                status=CHECK_OK if found else CHECK_ERROR,
                severity="info" if found else "blocker",
                message=f"import {module_name}",
                expected="module importable",
                actual="present" if found else "missing",
                impact="" if found else "核心数据模块可能无法运行。",
                repair="" if found else "运行 python -m pip install -r scripts/requirements.txt。",
            )
        )
    for module_name in ("fastapi", "uvicorn", "watchdog"):
        found = importlib.util.find_spec(module_name) is not None
        checks.append(
            _check(
                f"python.import.dashboard.{module_name}",
                status=CHECK_OK if found else CHECK_WARNING,
                severity="info" if found else "warning",
                message=f"import {module_name}",
                expected="module importable for dashboard",
                actual="present" if found else "missing",
                impact="" if found else "Dashboard 服务端可能无法启动。",
                repair="" if found else "运行 python -m pip install -r dashboard/requirements.txt。",
            )
        )
    return checks


def _dashboard_checks(plugin_root: Path | None = None) -> list[dict[str, Any]]:
    if plugin_root is None:
        plugin_root = Path(__file__).resolve().parents[2]
    dashboard_root = plugin_root / "dashboard"
    dist = dashboard_root / "frontend" / "dist"
    package_json = dashboard_root / "frontend" / "package.json"
    requirements = dashboard_root / "requirements.txt"
    checks: list[dict[str, Any]] = []
    for check_id, path, expected in (
        ("dashboard.root", dashboard_root, "directory exists"),
        ("dashboard.frontend.dist", dist, "built frontend dist exists"),
        ("dashboard.frontend.package_json", package_json, "package.json exists"),
        ("dashboard.requirements", requirements, "requirements.txt exists"),
    ):
        exists = path.is_dir() if expected.startswith("directory") or path == dist else path.is_file()
        checks.append(
            _check(
                check_id,
                status=CHECK_OK if exists else CHECK_WARNING,
                severity="info" if exists else "warning",
                message=check_id,
                path=str(path),
                expected=expected,
                actual="exists" if exists else "missing",
                impact="" if exists else "Dashboard 可能无法打开或发布包缺少前端产物。",
                repair="" if exists else "按 dashboard 文档安装/构建前端，或检查发布包是否遗漏 dist。",
            )
        )
    return checks


def _revalidation_checks(project_root: Path) -> list[dict[str, Any]]:
    chapters = list_needs_revalidation(project_root)
    if not chapters:
        return [
            _check(
                "commits.revalidation",
                status=CHECK_OK,
                severity="info",
                message="no chapters waiting for revalidation",
                expected="later chapters remain valid after earlier rewrites",
                actual="none",
            )
        ]
    earliest = chapters[0]
    listed = "、".join(str(item) for item in chapters)
    return [
        _check(
            "commits.revalidation",
            status=CHECK_WARNING,
            severity="warning",
            message="later chapters need revalidation after an earlier rewrite",
            path=str(project_root / ".story-system" / "commits"),
            expected="accepted commits match current predecessor context",
            actual=f"chapters={listed}",
            impact="这些章节仍按旧前文抽取，长期记忆不能当作当前真源。",
            repair=f"从最早失效章开始重新审查并提交：/canon-ledger-write {earliest}",
        )
    ]


def build_doctor_report(
    project_root: str | Path | None,
    *,
    chapter: int | None = None,
    deep: bool = False,
    preflight_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = resolve_project_phase(project_root, chapter=chapter)
    checks: list[dict[str, Any]] = []
    workflow: dict[str, Any] = {}
    canon_v3: dict[str, Any] = {}
    checks.extend(_preflight_checks(preflight_report))

    if snapshot.phase == PHASE_NO_PROJECT or not snapshot.project_root:
        checks.append(
            _check(
                "project.root",
                status=CHECK_ERROR,
                severity="blocker",
                message="project root not resolved",
                path=str(project_root or ""),
                expected=".canon-ledger/state.json",
                actual="missing",
                impact="无法判断项目状态，也不能安全运行写作链路。",
                repair="先运行 /canon-ledger-init，或运行 canon_ledger.py use <project_root> 绑定已有项目。",
            )
        )
    else:
        root = Path(snapshot.project_root)
        v3_checks, workflow, canon_v3 = _canon_v3_checks(root, deep=deep)
        checks.extend(v3_checks)
        checks.extend(_file_checks(root, snapshot))
        checks.extend(_json_checks(root))
        try:
            runtime_health = build_story_runtime_health(root, chapter=chapter)
        except Exception as exc:
            runtime_health = {"error": str(exc)}
            checks.append(
                _check(
                    "story_runtime.health",
                    status=CHECK_WARNING,
                    severity="warning",
                    message="story runtime health failed",
                    actual=str(exc),
                    impact="Story System 主链健康摘要不可用。",
                    repair="检查 .story-system 合同与 commit JSON 是否可读。",
                )
            )
        else:
            checks.append(
                _check(
                    "story_runtime.health",
                    status=CHECK_OK if runtime_health.get("mainline_ready") else CHECK_WARNING,
                    severity="info" if runtime_health.get("mainline_ready") else "warning",
                    message="legacy compatibility runtime health",
                    expected="advisory compatibility summary; Canon authority comes from workflow",
                    actual=json.dumps(runtime_health, ensure_ascii=False),
                    impact=(
                        ""
                        if runtime_health.get("mainline_ready")
                        else "兼容 runtime 摘要未就绪；它不能触发事实 fallback，也不能覆盖 v3 blocker。"
                    ),
                    repair=(
                        ""
                        if runtime_health.get("mainline_ready")
                        else "事实恢复只执行 canon-v3 status 返回的 primary_action；合同缺项仅作写作素材告警。"
                    ),
                )
            )
        checks.extend(_sqlite_checks(root))
        checks.extend(_revalidation_checks(root))
        checks.extend(_projection_log_checks(root, snapshot))
        checks.extend(_rag_checks(root))

    checks.extend(_python_checks())
    if deep:
        checks.extend(_dashboard_checks())

    blocking = [item for item in checks if item["severity"] == "blocker" and item["status"] == CHECK_ERROR]
    warnings = [item for item in checks if item["status"] == CHECK_WARNING]
    primary_action = workflow.get("primary_action") if workflow else {}
    primary_command = (
        str(primary_action.get("command") or "").strip()
        if isinstance(primary_action, dict)
        else ""
    )
    # One exact workflow produces one author-facing action. Compatibility and
    # environment repairs remain attached to their warning rows for diagnosis,
    # but never compete with or override the workflow authority.
    recommended_actions = (
        [primary_command]
        if primary_command
        else [str(item["repair"]) for item in checks if item.get("repair")]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not blocking,
        "project_root": snapshot.project_root,
        "mode": "deep" if deep else "standard",
        "phase": snapshot.phase,
        "workflow_snapshot": workflow,
        "primary_action": primary_action,
        "canon_v3": canon_v3,
        "expected_profile": _expected_profile(snapshot),
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
        "checks": checks,
        "recommended_actions": list(dict.fromkeys(recommended_actions)),
    }


def format_doctor_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "OK" if report.get("ok") else "ERROR"
    lines = [
        f"{status} canon-ledger-doctor",
        f"project_root: {report.get('project_root') or '(未解析)'}",
        f"phase: {report.get('phase')}",
        f"blocking: {report.get('blocking_count')} warnings: {report.get('warning_count')}",
    ]
    workflow = report.get("workflow_snapshot") or {}
    if workflow:
        lines.append(
            "canon_v3: "
            f"state={workflow.get('state')} "
            f"head={workflow.get('head_hash') or 'none'} "
            f"generation={workflow.get('generation')} "
            f"stage={workflow.get('stage_digest') or 'none'} "
            f"projection_fresh={workflow.get('projection_fresh')}"
        )
        primary = report.get("primary_action") or {}
        if primary:
            lines.append(
                "primary_action: "
                f"{primary.get('code')} -> {primary.get('command')}"
            )
    for item in report.get("checks") or []:
        if item.get("status") == CHECK_OK:
            continue
        lines.append(f"{str(item.get('status')).upper()} {item.get('id')}: {item.get('message')}")
        if item.get("path"):
            lines.append(f"  path: {item.get('path')}")
        if item.get("actual"):
            lines.append(f"  actual: {item.get('actual')}")
        if item.get("impact"):
            lines.append(f"  impact: {item.get('impact')}")
        if item.get("repair"):
            label = "advisory" if item.get("status") == CHECK_WARNING else "repair"
            lines.append(f"  {label}: {item.get('repair')}")
    actions = report.get("recommended_actions") or []
    if actions:
        lines.append("recommended_actions:")
        lines.extend(f"- {action}" for action in actions[:8])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 CanonLedger 项目只读诊断")
    parser.add_argument("--project-root", default="", help="书项目根目录")
    parser.add_argument("--chapter", type=int, default=None, help="目标章节号")
    parser.add_argument("--deep", action="store_true", help="包含 dashboard 等较深检查")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = build_doctor_report(args.project_root or None, chapter=args.chapter, deep=args.deep)
    print(format_doctor_report(report, args.format))
    raise SystemExit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    main()

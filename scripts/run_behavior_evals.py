#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from runtime_compat import enable_windows_utf8_stdio


SCHEMA_VERSION = "canon-ledger-behavior-eval-report/v1"


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    plugin_root = here.parent.parent
    if (plugin_root / "scripts" / "canon_ledger.py").is_file():
        return plugin_root
    return here.parents[2]


def _plugin_root(root: Path) -> Path:
    if (root / ".cursor-plugin" / "plugin.json").is_file():
        return root
    nested = root / "canon-ledger"
    if (nested / "scripts" / "canon_ledger.py").is_file():
        return nested
    return root


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    result: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


def _result(case: dict[str, Any], *, passed: bool, reason: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": case.get("id"),
        "type": case.get("type"),
        "passed": passed,
        "reason": reason,
        "evidence": evidence or [],
    }


def _eval_skill_frontmatter(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    for skill in sorted((_plugin_root(root) / "skills").glob("*/SKILL.md")):
        fm = _frontmatter(_read(skill))
        if not fm.get("name") or not fm.get("description"):
            missing.append(str(skill.relative_to(root)))
    return _result(
        case,
        passed=not missing,
        reason="所有技能都包含 name 和 description。" if not missing else "技能前置元数据不完整。",
        evidence=missing,
    )


def _eval_skill_contract(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    skill_name = str(case.get("skill") or "").strip()
    path = _plugin_root(root) / "skills" / skill_name / "SKILL.md"
    if not path.is_file():
        return _result(case, passed=False, reason="技能文件不存在。", evidence=[str(path)])
    text = _read(path)
    missing = [str(item) for item in case.get("required") or [] if str(item) not in text]
    for group in case.get("required_any") or []:
        options = [str(item) for item in group]
        if options and not any(option in text for option in options):
            missing.append("至少需要其中一项：" + " | ".join(options))

    ordering_errors: list[str] = []
    for pair in case.get("ordered") or []:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        left, right = str(pair[0]), str(pair[1])
        left_pos = text.find(left)
        right_pos = text.find(right)
        if left_pos < 0 or right_pos < 0 or left_pos >= right_pos:
            ordering_errors.append(f"{left} 应位于 {right} 之前")

    forbidden = [
        str(pattern)
        for pattern in case.get("forbidden_patterns") or []
        if re.search(str(pattern), text)
    ]
    passed = not missing and not ordering_errors and not forbidden
    return _result(
        case,
        passed=passed,
        reason=f"{skill_name} 契约符合预期。" if passed else f"{skill_name} 契约发生偏移。",
        evidence=missing + ordering_errors + forbidden or [str(path.relative_to(root))],
    )


def _eval_write_blocking_gate(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    path = _plugin_root(root) / "skills" / "canon-ledger-write" / "SKILL.md"
    text = _read(path)
    required = [
        "state == ready",
        "can_write_next == true",
        "--stage prewrite",
        "--stage precommit",
        "postcommit gate",
        "expected_stage_digest",
        "finalize_token",
    ]
    missing = [item for item in required if item not in text]
    precommit_pos = text.find("--stage precommit")
    commit_pos = text.rfind("执行 exact finalize")
    ordering_ok = precommit_pos >= 0 and commit_pos >= 0 and precommit_pos < commit_pos
    if not ordering_ok:
        missing.append("提交前闸门必须位于 canon-v3 finalize 之前")
    return _result(
        case,
        passed=not missing,
        reason="写作流程保留了阻断规则和运行时闸门。" if not missing else "写作流程契约不完整。",
        evidence=missing or [str(path.relative_to(root))],
    )


def _eval_data_agent_boundary(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    path = _plugin_root(root) / "agents" / "data-agent.md"
    text = _read(path)
    required = [
        "`.canon-ledger/tmp/canon_v3_proposal.json`",
        "`canon-v3/proposal-batch/v2`",
        "不是正史写入者",
        "每个 source 都必须被 support map 至少一个字段使用",
        "不得写 delta、人工队列或正史",
    ]
    missing = [item for item in required if item not in text]
    forbidden_patterns = [
        r"canon_ledger\.py[^\n]+state\s+process",
        r"canon_ledger\.py[^\n]+memory\s+update",
        r"canon_ledger\.py[^\n]+rag\s+index-chapter",
    ]
    forbidden = [pattern for pattern in forbidden_patterns if re.search(pattern, text)]
    return _result(
        case,
        passed=not missing and not forbidden,
        reason="data-agent 只负责生成 v3 事实提议，职责边界符合预期。" if not missing and not forbidden else "data-agent 职责边界发生偏移。",
        evidence=missing + forbidden or [str(path.relative_to(root))],
    )


def _eval_artifact_ownership(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    plugin_root = _plugin_root(root)
    write_text = _read(plugin_root / "skills" / "canon-ledger-write" / "SKILL.md")
    reviewer_tools = _frontmatter(_read(plugin_root / "agents" / "reviewer.md")).get("tools", "")
    data_tools = _frontmatter(_read(plugin_root / "agents" / "data-agent.md")).get("tools", "")
    missing: list[str] = []
    if "Write" in reviewer_tools:
        missing.append("reviewer 不应持 Write（canon_v3_review.json 由主流程落盘）")
    if "Write" not in data_tools:
        missing.append("data-agent 应持 Write（它是 canon_v3_proposal.json 的写入者）")
    data_text = _read(plugin_root / "agents" / "data-agent.md")
    reviewer_text = _read(plugin_root / "agents" / "reviewer.md")
    for item, haystack in (
        (".canon-ledger/tmp/canon_v3_proposal.json", data_text),
        ("不得修改候选、写人工队列", reviewer_text),
        ("reviewer", write_text),
        ("data-agent phase=assemble", write_text),
    ):
        if item not in haystack:
            missing.append(f"缺写入所有权红线：{item}")
    return _result(
        case,
        passed=not missing,
        reason="产物写入权与工具及提示词一致。" if not missing else "产物写入权发生偏移。",
        evidence=missing or ["reviewer→主流程 canon_v3_review.json；data-agent→canon_v3_proposal.json"],
    )


def _eval_commit_projection_runtime(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    scripts_dir = _plugin_root(root) / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from data_modules.chapter_content_binding import build_chapter_binding
    from data_modules.chapter_commit_service import ChapterCommitService

    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        (project_root / ".canon-ledger").mkdir(parents=True, exist_ok=True)
        (project_root / ".canon-ledger" / "state.json").write_text("{}", encoding="utf-8")
        chapter_file = project_root / "正文" / "第0001章.md"
        chapter_file.parent.mkdir(parents=True, exist_ok=True)
        chapter_file.write_text("第1章行为评估正文\n", encoding="utf-8")
        _write_json(
            project_root / ".story-system" / "chapters" / "chapter_001.json",
            {
                "meta": {
                    "schema_version": "story-system/v1",
                    "contract_type": "CHAPTER_BRIEF",
                    "chapter": 1,
                },
                "chapter_directive": {
                    "goal": "验证阻断提交仍能驱动状态投影",
                    "must_cover_nodes": ["完成关键事实节点"],
                    "forbidden_zones": [],
                },
            },
        )
        binding = build_chapter_binding(project_root, 1)
        service = ChapterCommitService(project_root)
        payload = service.build_commit(
            chapter=1,
            review_result=_review_artifact(binding, blocking=False),
            fulfillment_result={
                "planned_nodes": ["完成关键事实节点"],
                "covered_nodes": [],
                "missed_nodes": ["完成关键事实节点"],
                "extra_nodes": [],
                "enforcement": "strict",
                "chapter_binding": binding,
            },
            disambiguation_result={"pending": [], "chapter_binding": binding},
            extraction_result={
                "accepted_events": [],
                "state_deltas": [],
                "entity_deltas": [],
                "chapter_binding": binding,
            },
        )
        projected = service.apply_projections(payload)
        state_path = project_root / ".canon-ledger" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
    ok = (
        projected.get("projection_status", {}).get("state") == "done"
        and state.get("progress", {}).get("chapter_status", {}).get("1") == "chapter_rejected"
    )
    return _result(
        case,
        passed=ok,
        reason="章节提交能够驱动状态投影。" if ok else "章节提交未能正确驱动状态投影。",
        evidence=[str(projected.get("projection_status"))],
    )


def _eval_dashboard_read_only(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    path = _plugin_root(root) / "dashboard" / "app.py"
    text = _read(path)
    forbidden = re.findall(r"@app\.(post|put|delete|patch)\b", text)
    get_only = 'allow_methods=["GET"]' in text or 'allow_methods=[\n        "GET"' in text
    ok = not forbidden and get_only and "strictly read" not in text.lower()
    # 模块中的中文说明是权威的本地判据。
    ok = ok or (not forbidden and "仅提供 GET 接口" in text)
    return _result(
        case,
        passed=ok,
        reason="仪表盘只提供 GET 接口。" if ok else "仪表盘中发现了写入接口。",
        evidence=forbidden or [str(path.relative_to(root))],
    )


def _public_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(_plugin_root(root) / "scripts" / "canon_ledger.py"),
        *[str(item) for item in args],
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )


def _public_cli_json(root: Path, *args: str) -> dict[str, Any]:
    result = _public_cli(root, *args)
    if result.returncode != 0:
        raise RuntimeError(
            f"public CLI failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    payload = json.loads(result.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("public CLI did not return a JSON object")
    return payload


def _eval_canon_v3_init_runtime(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "book"
        first = _public_cli(
            root,
            "init",
            str(project),
            "行为评估长篇",
            "玄幻",
            "--protagonist-name",
            "林舟",
            "--protagonist-desire",
            "向所有人证明自己",
            "--protagonist-flaw",
            "冲动自负",
            "--protagonist-archetype",
            "孤狼",
        )
        if first.returncode != 0:
            return _result(
                case,
                passed=False,
                reason="clean init 公共 CLI 失败。",
                evidence=[first.stderr.strip(), first.stdout.strip()],
            )
        status = _public_cli_json(
            root, "--project-root", str(project), "canon-v3", "status"
        )
        query = _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "query",
            "snapshot",
            "--as-of-chapter",
            "0",
        )
        state_path = project / ".canon-ledger" / "state.json"
        current_path = project / ".story-system" / "v3" / "CURRENT"
        before_state = state_path.read_bytes()
        before_current = current_path.read_bytes()
        second = _public_cli(
            root,
            "init",
            str(project),
            "不应覆盖",
            "玄幻",
            "--protagonist-name",
            "另一个人",
        )
        query_text = json.dumps(query, ensure_ascii=False)
        soft_absent = all(
            value not in query_text
            for value in ("向所有人证明自己", "冲动自负", "孤狼")
        )
        ok = (
            status.get("state") == "ready"
            and status.get("projection_fresh") is True
            and query.get("authority") == "canon_v3"
            and query.get("head_hash") == status.get("head_hash")
            and second.returncode != 0
            and state_path.read_bytes() == before_state
            and current_path.read_bytes() == before_current
            and soft_absent
        )
        return _result(
            case,
            passed=ok,
            reason=(
                "clean init、权威查询与 dirty re-init 零写入通过。"
                if ok
                else "初始化/查询/重入行为不符合 Canon v3 契约。"
            ),
            evidence=[
                f"state={status.get('state')}",
                f"query_authority={query.get('authority')}",
                f"reinit_rc={second.returncode}",
                f"soft_absent={soft_absent}",
            ],
        )


def _eval_canon_v3_nonempty_transaction(
    root: Path, case: dict[str, Any]
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "book"
        created = _public_cli(
            root,
            "init",
            str(project),
            "非空事务评估",
            "玄幻",
            "--protagonist-name",
            "林舟",
        )
        if created.returncode != 0:
            return _result(
                case,
                passed=False,
                reason="非空事务项目初始化失败。",
                evidence=[created.stderr.strip(), created.stdout.strip()],
            )

        quote = "月门只在夜间开启"
        chapter_file = project / "正文" / "第0001章.md"
        chapter_file.write_text(quote, encoding="utf-8")
        binding_response = _public_cli_json(
            root,
            "--project-root",
            str(project),
            "chapter-binding",
            "--chapter",
            "1",
            "--out",
            str(project / ".canon-ledger" / "tmp" / "chapter_binding.json"),
            "--format",
            "json",
        )
        binding = binding_response["chapter_binding"]
        status = _public_cli_json(
            root, "--project-root", str(project), "canon-v3", "status"
        )
        candidate_path = project / ".canon-ledger" / "tmp" / "candidate.json"
        reviewer_path = project / ".canon-ledger" / "tmp" / "reviewer.json"
        proposal_path = project / ".canon-ledger" / "tmp" / "proposal.json"
        candidate = {
            "schema_version": "canon-v3/candidate-draft/v1",
            "chapter": 1,
            "chapter_binding": binding,
            "parent_head": status["head_hash"],
            "workflow_digest": status["workflow_digest"],
            "author_axiom_digest": status["author_axiom_digest"],
            "entity_registry_digest": status["entity_registry_digest"],
            "expected_stage_digest": None,
            "candidates": [
                {
                    "candidate_id": "rule-moon-gate",
                    "claim": {
                        "kind": "world_rule_revealed",
                        "rule": quote,
                    },
                    "sources": [
                        {
                            "source_type": "manuscript_span",
                            "source_id": "source-moon-gate",
                            "document_sha256": binding["sha256"],
                            "chapter": 1,
                            "start": 0,
                            "end": len(quote.encode("utf-8")),
                            "quote": quote,
                            "quote_sha256": hashlib.sha256(
                                quote.encode("utf-8")
                            ).hexdigest(),
                        }
                    ],
                    "support_map": {"rule": ["source-moon-gate"]},
                    "identity_links": {},
                }
            ],
            "extraction_blockers": [],
        }
        _write_json(candidate_path, candidate)
        validated = _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "validate-agent-output",
            "candidate-draft",
            "--input-file",
            ".canon-ledger/tmp/candidate.json",
        )
        digest = validated["candidate_digest_map"]["rule-moon-gate"]
        reviewer = {
            "schema_version": "canon-v3/reviewer-output/v3",
            "chapter": 1,
            "chapter_sha256": binding["sha256"],
            "parent_head": status["head_hash"],
            "author_axiom_digest": status["author_axiom_digest"],
            "entity_registry_digest": status["entity_registry_digest"],
            "candidate_digest_map": {"rule-moon-gate": digest},
            "candidate_digests": [digest],
            "observations": [
                {
                    "observation_id": "checkpoint-moon-gate",
                    "candidate_id": "rule-moon-gate",
                    "kind": "checkpoint",
                    "level": "human_required",
                    "reason": "世界硬规则需要作者确认",
                    "prior_fact_digests": [],
                }
            ],
            "scan_attestations": [
                {
                    "attestation_id": "scan-moon-gate",
                    "scanner": "reviewer",
                    "scanner_version": "canon-v3-reviewer-v3",
                    "chapter_sha256": binding["sha256"],
                    "parent_head": status["head_hash"],
                    "author_axiom_digest": status["author_axiom_digest"],
                    "entity_registry_digest": status["entity_registry_digest"],
                    "dimensions": [
                        "setting",
                        "timeline",
                        "continuity",
                        "character",
                        "logic",
                    ],
                    "status": "complete",
                    "checked_candidate_digests": [digest],
                }
            ],
            "extraction_incomplete": [],
        }
        _write_json(reviewer_path, reviewer)
        _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "validate-agent-output",
            "reviewer-output",
            "--input-file",
            ".canon-ledger/tmp/reviewer.json",
        )
        proposal = _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "assemble-proposal",
            "--candidate-file",
            ".canon-ledger/tmp/candidate.json",
            "--reviewer-file",
            ".canon-ledger/tmp/reviewer.json",
        )
        _write_json(proposal_path, proposal)
        _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "prepare",
            "--input-file",
            ".canon-ledger/tmp/proposal.json",
        )
        awaiting = _public_cli_json(
            root, "--project-root", str(project), "canon-v3", "status"
        )
        human_case = (awaiting.get("cases") or [])[0]
        binding_fields = dict(human_case["decision_binding"])
        decision_path = project / ".canon-ledger" / "tmp" / "decision.json"
        decision = {
            "schema_version": "canon-v3/decision-request/v2",
            "expected_stage_digest": awaiting["stage_digest"],
            "transaction_hash": awaiting["transaction_hash"],
            "decisions": [
                {
                    "case_key": human_case["case_key"],
                    **binding_fields,
                    "action": "approve",
                }
            ],
        }
        _write_json(decision_path, decision)
        _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "decide",
            "--input-file",
            ".canon-ledger/tmp/decision.json",
        )
        finalizable = _public_cli_json(
            root, "--project-root", str(project), "canon-v3", "status"
        )
        finalize_path = project / ".canon-ledger" / "tmp" / "finalize.json"
        _write_json(
            finalize_path,
            {
                "schema_version": "canon-v3/finalize-request/v2",
                "expected_stage_digest": finalizable["stage_digest"],
                "transaction_hash": finalizable["transaction_hash"],
                "finalize_token": finalizable["finalize_token"],
            },
        )
        _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "finalize",
            "--input-file",
            ".canon-ledger/tmp/finalize.json",
        )
        ready = _public_cli_json(
            root, "--project-root", str(project), "canon-v3", "status"
        )
        query = _public_cli_json(
            root,
            "--project-root",
            str(project),
            "canon-v3",
            "query",
            "snapshot",
            "--as-of-chapter",
            "1",
        )
        encoded = json.dumps(query, ensure_ascii=False)
        ok = (
            awaiting.get("state") == "awaiting_human"
            and "approve" in human_case.get("allowed_actions", [])
            and finalizable.get("state") == "ready_to_finalize"
            and ready.get("state") == "ready"
            and ready.get("latest_chapter") == 1
            and quote in encoded
        )
        return _result(
            case,
            passed=ok,
            reason=(
                "非空候选、人工确认、发布与 HEAD-bound 查询全链通过。"
                if ok
                else "真实 Canon v3 非空事务未完成。"
            ),
            evidence=[
                f"awaiting={awaiting.get('state')}",
                f"allowed={human_case.get('allowed_actions')}",
                f"finalizable={finalizable.get('state')}",
                f"ready={ready.get('state')}",
            ],
        )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_report_project(project_root: Path) -> None:
    for rel in (
        ".canon-ledger/tmp",
        ".canon-ledger/backups",
        ".story-system/commits",
        "正文",
        "审查报告",
    ):
        (project_root / rel).mkdir(parents=True, exist_ok=True)
    _write_json(
        project_root / ".canon-ledger" / "state.json",
        {"project_info": {"title": "测试书", "genre": "玄幻"}, "progress": {"current_chapter": 0}},
    )


def _report_binding(project_root: Path, chapter: int) -> dict[str, Any]:
    from data_modules.chapter_content_binding import build_chapter_binding

    return build_chapter_binding(project_root, chapter)


def _review_artifact(binding: dict[str, Any], *, blocking: bool = False, minimal: bool = False) -> dict[str, Any]:
    dimensions = ["setting", "timeline", "continuity", "character", "logic"]
    if minimal:
        return {
            "chapter": int(binding["chapter"]),
            "chapter_binding": binding,
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
            "summary": "用户选择最简模式，本章未执行事实审查。",
        }
    issues = []
    if blocking:
        issues.append(
            {
                "severity": "critical",
                "category": "timeline",
                "location": "第二段",
                "description": "时间线与上一章冲突。",
                "evidence": "上一章与本章使用了互相矛盾的时间锚点。",
                "fix_hint": "统一两章的时间锚点。",
                "blocking": True,
            }
        )
    return {
        "chapter": int(binding["chapter"]),
        "chapter_binding": binding,
        "review_mode": "standard",
        "review_status": "completed",
        "review_skipped": False,
        "review_degraded": False,
        "reviewed_dimensions": dimensions,
        "skipped_dimensions": [],
        "dimension_results": [
            {"dimension": dimension, "conclusion": "已完成事实核对。"}
            for dimension in dimensions
        ],
        "issues": issues,
        "issues_count": len(issues),
        "blocking_count": len(issues),
        "has_blocking": bool(issues),
        "summary": "本章事实审查已完成。",
    }


def _write_report_artifacts(project_root: Path, *, chapter: int = 1, review_skipped: bool = False, blocking: bool = False) -> None:
    binding = _report_binding(project_root, chapter)
    review = _review_artifact(binding, blocking=blocking, minimal=review_skipped)
    _write_json(project_root / ".canon-ledger" / "tmp" / "review_results.json", review)
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "review_audit.json",
        {
            "chapter": chapter,
            "start_chapter": chapter,
            "end_chapter": chapter,
            "review_mode": review["review_mode"],
            "review_status": review["review_status"],
            "review_degraded": review["review_degraded"],
            "reviewed_dimensions": review["reviewed_dimensions"],
            "skipped_dimensions": review["skipped_dimensions"],
            "issues_count": review["issues_count"],
            "blocking_count": review["blocking_count"],
            "report_file": f"审查报告/第{chapter}章审查报告.md",
        },
    )
    (project_root / "审查报告" / f"第{chapter}章审查报告.md").write_text("# 审查报告\n", encoding="utf-8")
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "fulfillment_result.json",
        {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "disambiguation_result.json",
        {"pending": [], "chapter_binding": binding},
    )
    _write_json(
        project_root / ".canon-ledger" / "tmp" / "extraction_result.json",
        {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "摘要",
            "chapter_binding": binding,
        },
    )


def _commit_payload(
    *,
    project_root: Path,
    chapter: int = 1,
    status: str = "accepted",
    projection_status: dict[str, str] | None = None,
    review_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    binding = _report_binding(project_root, chapter)
    if review_result and review_result.get("review_mode") == "minimal":
        review = _review_artifact(binding, minimal=True)
    else:
        review = _review_artifact(
            binding,
            blocking=bool((review_result or {}).get("blocking_count")),
        )
    return {
        "meta": {
            "schema_version": "story-system/v1",
            "chapter": chapter,
            "status": status,
        },
        "chapter_binding": binding,
        "provenance": {"chapter_binding": binding},
        "review_result": review,
        "fulfillment_result": {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
            "chapter_binding": binding,
        },
        "disambiguation_result": {"pending": [], "chapter_binding": binding},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "摘要",
            "chapter_binding": binding,
        },
        "projection_status": projection_status
        or {"state": "done", "index": "skipped", "summary": "skipped", "memory": "skipped", "vector": "skipped"},
    }


def _eval_user_report_probe(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    scripts_dir = _plugin_root(root) / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from data_modules.projection_log import append_projection_run
    from data_modules.user_report import build_user_report, render_user_report_text

    scenario = str(case.get("scenario") or "")
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        _make_report_project(project_root)
        chapter_file = project_root / "正文" / "第0001章.md"
        chapter_file.write_text("正文\n", encoding="utf-8")
        _write_report_artifacts(project_root, chapter=1)
        commit_path = project_root / ".story-system" / "commits" / "chapter_001.commit.json"

        if scenario == "minimal_review_skipped":
            _write_report_artifacts(project_root, chapter=1, review_skipped=True)
            _write_json(
                commit_path,
                _commit_payload(
                    project_root=project_root,
                    review_result={
                        "blocking_count": 0,
                        "review_skipped": True,
                        "review_mode": "minimal",
                        "summary": "用户选择最简模式，本章未执行完整审查。",
                    }
                ),
            )
            (project_root / ".canon-ledger" / "backups" / "ch0001_ok").mkdir(parents=True, exist_ok=True)
            report = build_user_report(project_root, stage="write", chapter=1)
            text = render_user_report_text(report)
            ok = report["overall_status"] == "partial" and "review_skipped" in json.dumps(report, ensure_ascii=False) and "minimal" in text
            evidence = [report["overall_status"], text]
        elif scenario == "missing_data_artifacts":
            for rel in ("fulfillment_result.json", "disambiguation_result.json", "extraction_result.json"):
                path = project_root / ".canon-ledger" / "tmp" / rel
                if path.exists():
                    path.unlink()
            report = build_user_report(project_root, stage="write", chapter=1)
            ok = report["overall_status"] != "completed" and bool(report["issues"]["must_handle"])
            evidence = [report["overall_status"], json.dumps(report["issues"], ensure_ascii=False)]
        elif scenario == "projection_retry_auto_handled":
            failed_payload = _commit_payload(
                project_root=project_root,
                projection_status={"state": "done", "index": "failed:locked", "summary": "skipped", "memory": "skipped", "vector": "skipped"},
            )
            _write_json(commit_path, failed_payload)
            append_projection_run(project_root, failed_payload, {"index": {"status": "failed:locked"}}, commit_path=commit_path)
            append_projection_run(
                project_root,
                failed_payload,
                {
                    "state": {"status": "done"},
                    "index": {"status": "skipped"},
                    "summary": {"status": "skipped"},
                    "memory": {"status": "skipped"},
                    "vector": {"status": "skipped"},
                },
                commit_path=commit_path,
            )
            (project_root / ".canon-ledger" / "backups" / "ch0001_ok").mkdir(parents=True, exist_ok=True)
            report = build_user_report(project_root, stage="write", chapter=1)
            ok = any(item.get("code") == "projection retry" for item in report["issues"]["auto_handled"]) and not report["issues"]["must_handle"]
            evidence = [json.dumps(report["issues"], ensure_ascii=False)]
        elif scenario == "review_blocking_must_handle":
            _write_report_artifacts(project_root, chapter=1, blocking=True)
            report = build_user_report(project_root, stage="review", chapter=1)
            ok = report["overall_status"] == "needs_user" and any(item.get("code") == "blocking_review" for item in report["issues"]["must_handle"])
            evidence = [json.dumps(report["issues"], ensure_ascii=False)]
        else:
            return _result(case, passed=False, reason=f"未知的用户报告场景：{scenario}")
    return _result(
        case,
        passed=ok,
        reason=f"用户报告场景 {scenario} 检查通过。" if ok else f"用户报告场景 {scenario} 检查失败。",
        evidence=evidence,
    )


EVALUATORS = {
    "skill_frontmatter": _eval_skill_frontmatter,
    "skill_contract": _eval_skill_contract,
    "write_blocking_gate": _eval_write_blocking_gate,
    "data_agent_boundary": _eval_data_agent_boundary,
    "artifact_ownership": _eval_artifact_ownership,
    "commit_projection_runtime": _eval_commit_projection_runtime,
    "dashboard_read_only": _eval_dashboard_read_only,
    "user_report_probe": _eval_user_report_probe,
    "canon_v3_init_runtime": _eval_canon_v3_init_runtime,
    "canon_v3_nonempty_transaction": _eval_canon_v3_nonempty_transaction,
}


def load_suite(root: Path, suite: str) -> dict[str, Any]:
    path = _plugin_root(root) / "evals" / "fixtures" / "behavior" / f"{suite}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_behavior_evals(root: str | Path | None = None, *, suite: str = "fast") -> dict[str, Any]:
    repo_root = Path(root) if root is not None else _repo_root()
    payload = load_suite(repo_root, suite)
    results: list[dict[str, Any]] = []
    for case in payload.get("cases") or []:
        evaluator = EVALUATORS.get(str(case.get("type") or ""))
        if evaluator is None:
            results.append(_result(case, passed=False, reason="未知的评估类型。"))
            continue
        try:
            results.append(evaluator(repo_root, case))
        except Exception as exc:
            results.append(_result(case, passed=False, reason=f"评估执行异常：{exc}"))
    failed = [item for item in results if not item.get("passed")]
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": suite,
        "ok": not failed,
        "root": str(repo_root),
        "total": len(results),
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }


def format_report(report: dict[str, Any], output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    status = "通过" if report.get("ok") else "失败"
    lines = [f"{status} 行为评估 {report.get('suite')}：通过 {report.get('passed')}/{report.get('total')}"]
    for item in report.get("results") or []:
        marker = "通过" if item.get("passed") else "失败"
        lines.append(f"{marker} {item.get('id')}: {item.get('reason')}")
    return "\n".join(lines)


def main() -> int:
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    parser = argparse.ArgumentParser(description="运行可重复的一致性引擎行为评估")
    parser.add_argument("--root", default="", help="仓库根目录，默认自动推断")
    parser.add_argument("--suite", default="fast", choices=["fast"])
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()
    report = run_behavior_evals(args.root or None, suite=args.suite)
    print(format_report(report, args.format))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

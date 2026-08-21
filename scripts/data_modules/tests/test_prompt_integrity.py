#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "skills"
COMMANDS = ROOT / "commands"
AGENTS = ROOT / "agents"
REFERENCES = ROOT / "references"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _frontmatter(path: Path) -> dict[str, str]:
    text = _read(path)
    assert text.startswith("---\n"), path
    end = text.find("\n---", 4)
    assert end > 0, path
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            values[key.strip()] = value.strip()
    return values


SKILL_FILES = sorted(SKILLS.glob("*/SKILL.md"))
COMMAND_FILES = sorted(COMMANDS.glob("canon-ledger-*.md"))
AGENT_FILES = sorted(AGENTS.glob("*.md"))


@pytest.mark.parametrize("path", SKILL_FILES + COMMAND_FILES + AGENT_FILES, ids=lambda p: p.name)
def test_prompt_frontmatter_is_complete(path: Path) -> None:
    values = _frontmatter(path)
    assert values.get("name")
    assert values.get("description")
    if path.parent == AGENTS:
        assert values.get("tools") is not None
        assert values.get("model")


def test_every_canon_skill_uses_one_shared_protocol() -> None:
    assert len(SKILL_FILES) == 9
    for path in SKILL_FILES:
        text = _read(path)
        assert "canon-v3-skill-protocol.md" in text, path
    protocol = _read(REFERENCES / "canon-v3-skill-protocol.md")
    for marker in (
        "唯一 Workflow Authority",
        "expected_stage_digest",
        "target_digest",
        "material_digest",
        "finalize_token",
        "文风",
        "Legacy 边界",
    ):
        assert marker in protocol


def test_all_commands_are_thin_skill_routes() -> None:
    assert len(COMMAND_FILES) == 9
    command_names = {path.stem for path in COMMAND_FILES}
    skill_names = {path.parent.name for path in SKILL_FILES}
    assert command_names == skill_names
    for path in COMMAND_FILES:
        text = _read(path)
        assert "Skill" in text or "技能" in text
        assert "chapter-commit --from-last-commit" not in text


def test_write_confirm_and_review_share_v2_transaction_authority() -> None:
    write = _read(SKILLS / "canon-ledger-write" / "SKILL.md")
    confirm = _read(SKILLS / "canon-ledger-confirm" / "SKILL.md")
    review = _read(SKILLS / "canon-ledger-review" / "SKILL.md")
    for marker in ("expected_stage_digest", "finalize_token"):
        assert marker in write
        assert marker in confirm
    for marker in (
        "canon-v3/decision-request/v2",
        "transaction_hash",
        "target_digest",
        "material_digest",
        "expected_decision_head_hash",
        "canon-v3/finalize-request/v2",
    ):
        assert marker in confirm
    assert "exact candidate draft" in review
    assert "Historical audit" in review
    assert "不创建或替换 STAGING" in review


def test_confirm_maps_status_decision_head_to_request_precondition() -> None:
    protocol = _read(REFERENCES / "canon-v3-skill-protocol.md")
    confirm = _read(SKILLS / "canon-ledger-confirm" / "SKILL.md")
    for text in (protocol, confirm):
        assert "decision_head_hash" in text
        assert "expected_decision_head_hash" in text
    assert "status.cases[i].decision_head_hash" in confirm
    assert "decisions[i].expected_decision_head_hash" in confirm
    assert "不能使用活动 Canon `head_hash` 或 `parent_head`" in confirm


def test_legacy_recertification_has_one_exact_human_publish_route() -> None:
    protocol = _read(REFERENCES / "canon-v3-skill-protocol.md")
    confirm = _read(SKILLS / "canon-ledger-confirm" / "SKILL.md")
    doctor = _read(SKILLS / "canon-ledger-doctor" / "SKILL.md")
    for marker in (
        "transaction_kind=legacy_recertification",
        "canon-v3/legacy-recertification-publish-request/v1",
        "canon-v3/legacy-recertification-decision/v1",
        "expected_current_head",
        "detached_plan_digest",
        "publish_token",
        "repair-cutover --apply",
    ):
        assert marker in protocol
        assert marker in confirm
    assert "逐项确认" in confirm
    assert "收齐全部明确选择前不得调用" in confirm
    assert "--dry-run" in doctor and "--apply --input-file" in doctor


def test_production_skills_do_not_restore_legacy_fact_writers() -> None:
    write = _read(SKILLS / "canon-ledger-write" / "SKILL.md")
    review = _read(SKILLS / "canon-ledger-review" / "SKILL.md")
    plan = _read(SKILLS / "canon-ledger-plan" / "SKILL.md")
    query = _read(SKILLS / "canon-ledger-query" / "SKILL.md")
    assert "canon_ledger.py review-pipeline" not in write
    assert "canon_ledger.py review-pipeline" not in review
    assert "update-state --" not in plan
    assert "knowledge query-" not in plan
    assert "canon_ledger.py knowledge query-" not in query
    assert "替代 v3 entity registry" in query
    assert "删除旧事实型 `update-state`" in plan


def test_plan_and_init_cannot_create_unreviewed_hard_facts() -> None:
    init = _read(SKILLS / "canon-ledger-init" / "SKILL.md")
    plan = _read(SKILLS / "canon-ledger-plan" / "SKILL.md")
    assert "canon-v3 initialize" in init
    assert "author-axiom digest" in init
    assert "author_axiom_proposals" in plan
    assert "不得直接覆盖活动设定" in plan
    assert "软计划" in plan
    assert "style" in plan


def test_query_context_and_dashboard_are_head_bound() -> None:
    query = _read(SKILLS / "canon-ledger-query" / "SKILL.md")
    context = _read(AGENTS / "context-agent.md")
    dashboard = _read(SKILLS / "canon-ledger-dashboard" / "SKILL.md")
    for marker in ("active_canon", "staged_proposal", "legacy_read_only", "draft_setting"):
        assert marker in query
    for marker in ("workflow_digest", "head_hash", "author_axiom_digest"):
        assert marker in context
    assert "禁止降级读取 `.canon-ledger/state.json`、`index.db`" in context
    assert "/api/canon-v3/workflow" in dashboard
    assert "head_hash, generation" in dashboard
    assert "不能读取 legacy `index.db`" in dashboard


def test_query_uses_real_public_facades_and_fails_closed() -> None:
    query = _read(SKILLS / "canon-ledger-query" / "SKILL.md")
    for marker in (
        "canon-v3 history",
        "canon-v3 author-axioms",
        "canon-v3 status",
        "canon-v3 audit-cutover",
        "style-memory show",
        "/api/canon-v3/entities",
        "/api/canon-v3/relationships",
        "/api/canon-v3/state-changes",
    ):
        assert marker in query
    assert "当前公开查询面不会绕过投影" in query
    assert "停止事实回答" in query
    assert "不扫描 object store" in query
    assert "当前没有可证明已认证状态的公共查询 facade" in query


def test_historical_audit_has_fixed_derived_schema_and_writer() -> None:
    schema = _read(REFERENCES / "review-schema.md")
    review = _read(SKILLS / "canon-ledger-review" / "SKILL.md")
    data_agent = _read(AGENTS / "data-agent.md")
    for marker in (
        "canon-v3/historical-audit/v1",
        ".canon-ledger/tmp/canon_v3_historical_audit.json",
        ".canon-ledger/tmp/canon_v3_historical_audit.md",
    ):
        assert marker in schema
        assert marker in review
    assert '"disposition": "read_only"' in schema
    assert "本 Skill 是持久化责任方" in review
    assert "也不写文件" in data_agent
    assert "唯一文件写入仍是 `phase=assemble` 的 v2 proposal" in data_agent


def test_style_skill_is_explicitly_non_canon() -> None:
    text = _read(SKILLS / "canon-ledger-learn" / "SKILL.md")
    assert "style-memory add-item" in text
    assert "不写 hard constraints、author axioms、事实 memory、STAGING 或 HEAD" in text
    for marker in (
        "head_hash",
        "workflow_digest",
        "stage_digest",
        "projection binding",
        "migration digest",
        "cases",
    ):
        assert marker in text
    assert "必须完全不变" in text


def test_style_values_cannot_be_routed_to_author_axiom_approval() -> None:
    confirm = _read(SKILLS / "canon-ledger-confirm" / "SKILL.md")
    data_agent = _read(AGENTS / "data-agent.md")
    for marker in (
        "proposed_category/proposed_value",
        "不得建议 `approve` 为硬设定",
        "/canon-ledger-learn",
    ):
        assert marker in confirm
    for marker in (
        "实际 value",
        "style_only",
        "/canon-ledger-learn",
        "不得生成 author-axiom record",
    ):
        assert marker in data_agent


def test_agent_write_ownership_is_closed() -> None:
    context_tools = _frontmatter(AGENTS / "context-agent.md")["tools"]
    reviewer_tools = _frontmatter(AGENTS / "reviewer.md")["tools"]
    data_tools = _frontmatter(AGENTS / "data-agent.md")["tools"]
    deconstruction_tools = _frontmatter(AGENTS / "deconstruction-agent.md")["tools"]
    assert "Write" not in context_tools
    assert "Write" not in reviewer_tools
    assert "Write" in data_tools
    assert "Write" not in deconstruction_tools
    assert "唯一允许写 `.canon-ledger/tmp/canon_v3_proposal.json`" in _read(
        AGENTS / "data-agent.md"
    )
    assert "不得修改候选、写人工队列" in _read(AGENTS / "reviewer.md")
    assert "不是 Canon" in _read(AGENTS / "deconstruction-agent.md")


def test_reviewer_contract_is_v2_and_fact_only() -> None:
    text = _read(AGENTS / "reviewer.md")
    schema = _read(REFERENCES / "review-schema.md")
    for marker in (
        "canon-v3/reviewer-output/v2",
        "parent_head",
        "author_axiom_digest",
        "entity_registry_digest",
        "observations",
        "scan_attestations",
        "extraction_incomplete",
        "setting",
        "timeline",
        "continuity",
        "character",
        "logic",
    ):
        assert marker in text
        assert marker in schema
    for stale in ("manual_checks", "blocking_count", "overall_score"):
        assert stale not in text
    assert "无证据锚点的低概率猜测忽略" in text
    assert "文风、文笔" in text


def test_data_agent_proposal_is_v2_and_has_no_unused_source_escape() -> None:
    text = _read(AGENTS / "data-agent.md")
    for marker in (
        "canon-v3/proposal-batch/v2",
        "expected_stage_digest",
        "parent_head",
        "workflow_digest",
        "author_axiom_digest",
        "entity_registry_digest",
        "每个 source 都必须被 support map 至少一个字段使用",
        "不允许重复内容 source、未使用 source 或伪 quote",
    ):
        assert marker in text
    for forbidden in ("accepted_events", "state_deltas", "entity_deltas", "timeline_events"):
        # The agent may name retired fields only in an explicit prohibition.
        if forbidden in text:
            assert "禁止输出或沿用" in text


def test_markdown_reference_links_resolve() -> None:
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]+)?)\)")
    for prompt in [*SKILL_FILES, *AGENT_FILES, *REFERENCES.rglob("*.md")]:
        for raw in link_pattern.findall(_read(prompt)):
            target = raw.split("#", 1)[0]
            if target.startswith(("http://", "https://", "/")):
                continue
            assert (prompt.parent / target).resolve().is_file(), f"{prompt}: {raw}"


def test_skill_and_agent_evals_use_v2_language() -> None:
    eval_files = [
        SKILLS / "canon-ledger-write" / "evals" / "evals.json",
        SKILLS / "canon-ledger-review" / "evals" / "evals.json",
        AGENTS / "evals" / "evals.json",
    ]
    for path in eval_files:
        payload = json.loads(_read(path))
        assert payload.get("evals")
        text = json.dumps(payload, ensure_ascii=False)
        for stale in ("--fast", "--minimal"):
            assert stale not in text, path
        assert "调用 index.db.review_audits" not in text, path
        assert "输出 `manual_checks`" not in text, path
    agent_eval = _read(AGENTS / "evals" / "evals.json")
    assert "reviewer-output/v2" in agent_eval
    assert "extraction_incomplete" in agent_eval


def test_plugin_description_matches_product_boundary() -> None:
    plugin = json.loads(_read(ROOT / ".cursor-plugin" / "plugin.json"))
    marketplace = json.loads(_read(ROOT / ".cursor-plugin" / "marketplace.json"))
    assert plugin["version"] == "8.0.0"
    assert marketplace["plugins"][0]["version"] == plugin["version"]
    for description in (plugin["description"], marketplace["plugins"][0]["description"]):
        assert "长期事实" in description
        assert "章纲履约" in description
        assert "不" in description or "自由" in description


def test_shared_protocol_and_architecture_agree_on_cutover_and_lineage() -> None:
    protocol = _read(REFERENCES / "canon-v3-skill-protocol.md")
    architecture = _read(REFERENCES / "canon-v3-architecture.md")
    for marker in (
        "semantic_claim_digest",
        "stage_digest",
        "finalize_token",
        "recertification",
        "author-axiom",
        "legacy",
    ):
        assert marker in protocol or marker.replace("-", "_") in protocol
        assert marker in architecture or marker.replace("-", "_") in architecture

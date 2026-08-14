#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt 完整性静态校验。

验证 agents/*.md 和 skills/*/SKILL.md 的结构、引用、CLI 命令等，
不需要 LLM 调用，可加入 CI。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 基础路径
# ---------------------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent
AGENTS_DIR = PLUGIN_ROOT / "agents"
SKILLS_DIR = PLUGIN_ROOT / "skills"
REFERENCES_DIR = PLUGIN_ROOT / "references"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

AGENT_FILES = sorted(AGENTS_DIR.glob("*.md"))
SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))
ALL_PROMPT_FILES = AGENT_FILES + SKILL_FILES
AUTHOR_REPORT_SKILLS = (
    "canon-ledger-init",
    "canon-ledger-plan",
    "canon-ledger-write",
    "canon-ledger-review",
)
SUBAGENT_RUN_FIELDS = (
    '"status": "completed | partial | failed | skipped"',
    '"problems": []',
    '"auto_handled": []',
    '"needs_user_action": false',
    '"duration_ms": 0',
    '"outputs": []',
)
SUBAGENT_PROMPT_FILES = (
    "context-agent.md",
    "reviewer.md",
    "data-agent.md",
    "deconstruction-agent.md",
)

# canon_ledger.py 注册的子命令（从 add_parser 提取）
REGISTERED_CLI_SUBCOMMANDS = {
    "where", "preflight", "project-status", "doctor", "write-gate", "chapter-binding", "projections", "user-report",
    "run-ledger", "run-log", "use",
    "index", "state", "rag", "style", "entity", "context", "memory",
    "status", "update-state", "backup", "archive",
    "init", "extract-context", "memory-contract", "project-memory", "review-pipeline",
    "placeholder-scan", "master-outline-sync",
    "story-system", "chapter-commit", "story-events", "knowledge",
    "subagent-models",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_frontmatter(text: str) -> dict:
    """提取 YAML frontmatter 为 dict。"""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _extract_referenced_paths(text: str, base_dir: Path) -> list[tuple[str, Path]]:
    """从 markdown 中提取被引用的文件路径（references/, skills/, agents/ 等）。

    返回 (raw_ref, resolved_path) 列表。
    """
    refs = []
    # 匹配 `references/xxx.md`、`../../references/xxx.md`、`skills/xxx` 等相对路径
    for m in re.finditer(r'[`"]((?:\.\./)*(?:references|skills|agents)/[^\s`"]+\.md)[`"]', text):
        raw = m.group(1)
        resolved = (base_dir / raw).resolve()
        refs.append((raw, resolved))
    # 匹配 references 段落中列出的路径（不带引号）
    for m in re.finditer(r'^- `((?:\.\./)*(?:references|skills|agents)/[^\s`]+\.md)`', text, re.MULTILINE):
        raw = m.group(1)
        resolved = (base_dir / raw).resolve()
        refs.append((raw, resolved))
    return refs


def _extract_cli_subcommands(text: str) -> list[str]:
    """从 prompt 中提取 canon_ledger.py 调用的子命令。"""
    cmds = set()
    for m in re.finditer(r'canon_ledger\.py["\s]+--project-root\s+[^\s]+\s+([a-z][\w-]*)', text):
        cmd = m.group(1)
        cmds.add(cmd)
    return sorted(cmds)


# ---------------------------------------------------------------------------
# 1. Frontmatter 完整性
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("agent_file", AGENT_FILES, ids=lambda f: f.name)
def test_agent_frontmatter_complete(agent_file: Path):
    """每个 agent 必须有 name, description, tools。"""
    fm = _extract_frontmatter(_read_text(agent_file))
    assert "name" in fm, f"{agent_file.name}: 缺少 name"
    assert "description" in fm, f"{agent_file.name}: 缺少 description"
    assert "tools" in fm, f"{agent_file.name}: 缺少 tools"


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda f: f.parent.name)
def test_skill_frontmatter_complete(skill_file: Path):
    """每个 skill 必须有 name, description。"""
    fm = _extract_frontmatter(_read_text(skill_file))
    assert "name" in fm, f"{skill_file.parent.name}: 缺少 name"
    assert "description" in fm, f"{skill_file.parent.name}: 缺少 description"


def test_user_facing_skills_do_not_self_identify_as_webnovel_plugin():
    """Skill / command 描述不得再自称网文插件。"""
    forbidden = ("网文插件", "初始化网文", "网文项目", "网文创作")
    files = list(SKILL_FILES) + sorted((PLUGIN_ROOT / "commands").glob("*.md"))
    for path in files:
        fm = _extract_frontmatter(_read_text(path))
        description = str(fm.get("description") or "")
        hits = [token for token in forbidden if token in description]
        assert not hits, f"{path.relative_to(PLUGIN_ROOT)}: 描述仍自称 {hits}"


# ---------------------------------------------------------------------------
# 2. Agent 模板结构（≥4 段）
# ---------------------------------------------------------------------------

EXPECTED_AGENT_SECTIONS = [
    "1.",
    "2.",
    "3.",
    "4.",
]


@pytest.mark.parametrize("agent_file", AGENT_FILES, ids=lambda f: f.name)
def test_agent_template_structure(agent_file: Path):
    """每个 agent 至少包含 4 个编号段（§12.2 松绑：不强制 8 段，避免为过测试留空段）。"""
    text = _read_text(agent_file)
    missing = []
    for section in EXPECTED_AGENT_SECTIONS:
        # 匹配 "## 1. 身份与目标" 或 "## 2. 可用工具与脚本"（允许后缀）
        pattern = rf"^## {re.escape(section)}"
        if not re.search(pattern, text, re.MULTILINE):
            missing.append(section)
    assert not missing, f"{agent_file.name}: 缺少段落 {missing}"


# ---------------------------------------------------------------------------
# 3. 引用完整性
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt_file", ALL_PROMPT_FILES, ids=lambda f: f.name)
def test_all_references_exist(prompt_file: Path):
    """prompt 中引用的所有文件路径都必须真实存在。"""
    text = _read_text(prompt_file)
    base_dir = prompt_file.parent
    refs = _extract_referenced_paths(text, base_dir)
    missing = []
    for raw, resolved in refs:
        if not resolved.exists():
            missing.append(raw)
    assert not missing, f"{prompt_file.name}: 引用了不存在的文件 {missing}"


# ---------------------------------------------------------------------------
# 4. CLI 命令有效性
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt_file", ALL_PROMPT_FILES, ids=lambda f: f.name)
def test_cli_commands_valid(prompt_file: Path):
    """prompt 中的 canon_ledger.py 子命令都必须在 CLI 注册表中。"""
    text = _read_text(prompt_file)
    cmds = _extract_cli_subcommands(text)
    # 排除已知例外（如 canon-ledger-review 的 workflow 命令待重构）
    skill_name = prompt_file.parent.name
    exceptions = _KNOWN_CLI_EXCEPTIONS.get(skill_name, set())
    invalid = [c for c in cmds if c not in REGISTERED_CLI_SUBCOMMANDS and c not in exceptions]
    assert not invalid, f"{prompt_file.name}: 使用了未注册的 CLI 子命令 {invalid}"


# ---------------------------------------------------------------------------
# 5. Review Schema 一致性
# ---------------------------------------------------------------------------

def test_review_schema_consistency():
    """reviewer.md 输出格式中的字段必须与 review_schema.py 定义匹配。"""
    reviewer_text = _read_text(AGENTS_DIR / "reviewer.md")

    # 从 reviewer.md 的 JSON 示例中提取 issue 字段
    issue_fields_in_prompt = set()
    json_block = re.search(r'"issues":\s*\[\s*\{([^}]+)\}', reviewer_text, re.DOTALL)
    if json_block:
        for m in re.finditer(r'"(\w+)":', json_block.group(1)):
            issue_fields_in_prompt.add(m.group(1))

    # 从 review_schema.py 提取 ReviewIssue 字段
    schema_path = SCRIPTS_DIR / "data_modules" / "review_schema.py"
    schema_text = _read_text(schema_path)
    schema_fields = set()
    in_review_issue = False
    for line in schema_text.splitlines():
        if "class ReviewIssue" in line:
            in_review_issue = True
            continue
        if in_review_issue:
            if line.strip().startswith("class ") or line.strip().startswith("def "):
                break
            m = re.match(r"\s+(\w+):\s+", line)
            if m:
                schema_fields.add(m.group(1))

    # reviewer prompt 中的字段应该是 schema 字段的子集
    assert issue_fields_in_prompt, "无法从 reviewer.md 提取 issue 字段"
    assert schema_fields, "无法从 review_schema.py 提取字段"
    extra = issue_fields_in_prompt - schema_fields
    assert not extra, f"reviewer.md 中有字段不在 review_schema.py 中: {extra}"
    assert "blocking_count" in reviewer_text
    assert "issues_count" in reviewer_text


def test_review_schema_md_matches_code_categories():
    """审查 skill 每轮必读的 schema 必须与代码五维分类一致。"""
    from data_modules.review_schema import REVIEW_DIMENSIONS, VALID_CATEGORIES

    schema_md = _read_text(REFERENCES_DIR / "review-schema.md")

    assert VALID_CATEGORIES == set(REVIEW_DIMENSIONS)
    for name in REVIEW_DIMENSIONS:
        assert name in schema_md
    for stale in ("ai_flavor", "pacing", "other"):
        assert stale not in schema_md
    assert "overall_score" not in schema_md
    assert "dimension_scores" not in schema_md
    assert "review_audit" in schema_md
    assert "review_audits" in schema_md


def test_query_skill_describes_current_contracts():
    """查询 skill 对合同的描述必须对准当前写前真源，而不是已掏空的写法字段。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-query" / "SKILL.md")
    for stale in ("节奏策略", "动态上下文", "本章焦点", "核心禁忌"):
        assert stale not in text, f"查询 skill 仍在用旧合同口径：{stale}"
    assert "volume_goal" in text
    assert "chapter_directive" in text
    assert "setting_canon" in text
    assert "must_cover_nodes" in text
    assert "forbidden_zones" in text


def test_reference_loading_map_matches_current_skills():
    """加载表不得把 plan 写成会读冲突设计教程，也不得指向已删除的 architecture 文档。"""
    text = _read_text(REFERENCES_DIR / "index" / "reference-loading-map.md")
    plan_text = _read_text(SKILLS_DIR / "canon-ledger-plan" / "SKILL.md")
    assert "不加载冲突设计" in plan_text
    assert "需要冲突设计" not in text
    assert "docs/architecture" not in text
    assert "phase0-slimming" not in text


def test_reviewer_consumes_chapter_contract_obligations():
    reviewer_text = _read_text(AGENTS_DIR / "reviewer.md")
    write_text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    review_text = _read_text(SKILLS_DIR / "canon-ledger-review" / "SKILL.md")

    for marker in (
        "chapter_contract_file",
        "review_contract_file",
        "must_cover_nodes",
        "forbidden_zones",
    ):
        assert marker in reviewer_text
    for text in (write_text, review_text):
        assert "chapter_contract_file" in text
        assert "review_contract_file" in text


# ---------------------------------------------------------------------------
# 6. 无残留引用（已删文件）
# ---------------------------------------------------------------------------

KNOWN_DELETED_FILES = [
    "step-1.5-contract.md",
    "step-3-review-gate.md",
    "step-5-debt-switch.md",
    "workflow-details.md",
    "checker-output-schema.md",
    "workflow_manager.py",
    "canon-ledger-resume",
    "golden_three_checker.py",
    "snapshot_manager.py",
]

_KNOWN_CLI_EXCEPTIONS = {}


@pytest.mark.parametrize("prompt_file", ALL_PROMPT_FILES, ids=lambda f: f.name)
def test_no_stale_references(prompt_file: Path):
    """不得引用已知已删除的文件。"""
    text = _read_text(prompt_file)
    found = [name for name in KNOWN_DELETED_FILES if name in text]
    assert not found, f"{prompt_file.name}: 残留引用已删除文件 {found}"


def test_canon_ledger_review_skill_uses_unified_reviewer_pipeline():
    """canon-ledger-review 必须与 canon-ledger-write 使用同一套 reviewer + review-pipeline 链路。"""
    skill_text = _read_text(SKILLS_DIR / "canon-ledger-review" / "SKILL.md")

    assert "`reviewer`" in skill_text
    assert "使用 `Task` 工具调用插件 agent `reviewer`" in skill_text
    assert "subagent_type:" not in skill_text
    assert "review-pipeline" in skill_text
    assert ".canon-ledger/tmp/review_results.json" in skill_text
    assert ".canon-ledger/tmp/review_audit.json" in skill_text
    assert "--save-audit" in skill_text
    assert "overall_score" not in skill_text

    for legacy_agent in (
        "consistency-checker",
        "continuity-checker",
        "ooc-checker",
        "reader-pull-checker",
        "high-point-checker",
        "pacing-checker",
    ):
        assert legacy_agent not in skill_text

    assert " workflow " not in skill_text


def test_reviewer_chain_requires_chinese_natural_language():
    """审查代理与两个调用入口都必须要求自然语言审查内容使用中文。"""
    reviewer_text = _read_text(AGENTS_DIR / "reviewer.md")
    review_skill = _read_text(SKILLS_DIR / "canon-ledger-review" / "SKILL.md")
    write_skill = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")

    assert "正文证据引用保持原文" in reviewer_text
    assert "除 JSON 字段、固定枚举、路径和正文原样引用外" in review_skill
    assert "除 JSON 字段、固定枚举、路径和正文原样引用外" in write_skill
    assert "所有自然语言审查内容使用中文" in review_skill
    assert "所有自然语言审查内容使用中文" in write_skill
    assert "跳过审查凭据" in write_skill
    assert "--minimal" in write_skill


def test_active_skills_use_cursor_task_tool():
    """关键 skill 用 Task 直接调用插件 agent。"""
    for skill_file in SKILL_FILES:
        text = _read_text(skill_file)
        fm = _extract_frontmatter(text)
        assert "allowed-tools" not in fm, f"{skill_file.parent.name}: skill 不应保留 allowed-tools"


def test_canon_ledger_write_skill_uses_explicit_agent_invocation_templates():
    """关键 subagent 必须经 Task 工具按插件 agent 名显式调用。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")

    for subagent in ("context-agent", "reviewer", "data-agent"):
        assert f"插件 agent `{subagent}`" in text, f"缺少 {subagent} 的插件 agent 显式调用"
        assert f"agents/{subagent}.md" in text or (
            subagent == "reviewer" and "agents/reviewer.md" in text
        )
    assert "subagent_type:" not in text, "不应再使用伪函数 subagent_type 调用块"
    assert "不得用主流程口头代替 subagent 输出" in text


@pytest.mark.parametrize("skill_name", AUTHOR_REPORT_SKILLS)
def test_main_skills_define_author_friendly_final_report_contract(skill_name: str):
    """四个主 Skill 必须提供作者友好的总状态 + 三段式最终报告契约。"""
    text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")

    assert "作者友好最终报告契约" in text
    assert "总状态：已完成 / 部分完成 / 需要你处理 / 未完成" in text
    for section in (
        "一、产生的文件与完成情况",
        "二、过程中遇到的问题与异常耗时",
        "三、下一步建议",
    ):
        assert section in text, f"{skill_name}: 缺少最终报告段落 {section}"
    for issue_type in ("已自动处理", "建议确认", "必须处理"):
        assert issue_type in text, f"{skill_name}: 缺少异常分类 {issue_type}"
    assert "任务化语言" in text
    assert "可复制命令" in text
    assert "/canon-ledger-doctor" in text
    assert "不写 token 统计" in text


def test_write_skill_final_report_covers_commit_projection_and_backup():
    """写章最终报告必须覆盖正文、审查、data artifacts、commit、projection、backup。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    for required in (
        "正文文件路径",
        "审查报告路径",
        ".canon-ledger/tmp/review_results.json",
        ".canon-ledger/tmp/fulfillment_result.json",
        ".canon-ledger/tmp/disambiguation_result.json",
        ".canon-ledger/tmp/extraction_result.json",
        ".story-system/commits/chapter_{NNN}.commit.json",
        "state / index / summary / memory / vector 更新状态",
        "备份状态",
        "是否可以继续写下一章",
    ):
        assert required in text
    assert "chapter-commit rejected" in text
    assert "最终状态不得写“已完成”" in text
    assert "--fast" in text and "--minimal" in text
    assert "projection retry" in text


def test_review_skill_final_report_covers_audit_and_blocking_decision():
    """审查最终报告必须覆盖报告、审计记录、阻断数与用户裁决状态。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-review" / "SKILL.md")
    for required in (
        "审查报告文件",
        ".canon-ledger/tmp/review_results.json",
        ".canon-ledger/tmp/review_audit.json",
        "review_audits",
        "阻断问题数量",
        "用户裁决状态",
        "如果无阻断，明确可以继续写作",
    ):
        assert required in text
    assert "有 blocking 问题且用户未选择处理策略" in text
    assert "最终状态为“需要你处理”" in text


def test_main_skills_record_subagent_run_summaries_for_agent_calls():
    """主 Skill 调用 Agent 后必须记录 SubagentRun 汇总，供最终报告使用。"""
    expected = {
        "canon-ledger-init": ("deconstruction-agent",),
        "canon-ledger-write": ("context-agent", "reviewer", "data-agent"),
        "canon-ledger-review": ("reviewer",),
    }

    for skill_name, agents in expected.items():
        text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")
        assert "SubagentRun" in text, f"{skill_name}: 缺少 SubagentRun 汇总契约"
        for field in SUBAGENT_RUN_FIELDS:
            assert field in text, f"{skill_name}: 缺少 SubagentRun 字段 {field}"
        for agent_name in agents:
            assert f'"name": "{agent_name}"' in text, (
                f"{skill_name}: 缺少 {agent_name} 的 SubagentRun name"
            )
    plan_text = _read_text(SKILLS_DIR / "canon-ledger-plan" / "SKILL.md")
    assert "SubagentRun" not in plan_text, "canon-ledger-plan 当前不调用 Agent，不应虚构 SubagentRun"


@pytest.mark.parametrize("agent_file_name", SUBAGENT_PROMPT_FILES)
def test_agents_expose_subagent_run_summary_signals_without_changing_outputs(agent_file_name: str):
    """Agent prompt 必须暴露可汇总信号，但不得把 SubagentRun 写入原始产物。"""
    text = _read_text(AGENTS_DIR / agent_file_name)

    assert "SubagentRun 可汇总信号" in text
    for field in ("`status`", "`problems`", "`auto_handled`", "`needs_user_action`", "`duration_ms`", "`outputs`"):
        assert field in text, f"{agent_file_name}: 缺少可汇总字段 {field}"
    assert "主流程" in text and "记录" in text

    if agent_file_name == "reviewer.md":
        assert "不要把 `SubagentRun` 写进 reviewer JSON" in text
    elif agent_file_name == "data-agent.md":
        assert "不要把 `SubagentRun` 写进三份 artifact" in text
    elif agent_file_name == "deconstruction-agent.md":
        assert "不要把 `SubagentRun` 写进 `init_reference_research` 顶层" in text
    elif agent_file_name == "context-agent.md":
        assert "不要把 `SubagentRun` JSON 写入任务书" in text


def test_agent_prompts_use_canon_ledger_runtime_identity():
    """Agent 只引用 CanonLedger 的运行目录、命令与环境变量。"""
    legacy_dir = "." + "web" + "novel"
    legacy_cli = "web" + "novel.py"

    for filename in SUBAGENT_PROMPT_FILES:
        text = _read_text(AGENTS_DIR / filename)
        assert ".canon-ledger" in text, f"{filename}: 缺少 CanonLedger 项目目录"
        assert legacy_dir not in text, f"{filename}: 仍引用旧项目目录"
        assert legacy_cli not in text, f"{filename}: 仍引用旧 CLI"

    for filename in ("context-agent.md", "data-agent.md", "reviewer.md"):
        text = _read_text(AGENTS_DIR / filename)
        assert "${CANON_LEDGER_PYTHON}" in text, f"{filename}: 缺少 CanonLedger Python 环境变量"
        assert "canon_ledger.py" in text, f"{filename}: 缺少 CanonLedger CLI"


def test_agent_eval_fixture_uses_canon_ledger_project_data_dir():
    """Agent 评测样例只使用 CanonLedger 项目数据目录。"""
    fixture_root = AGENTS_DIR / "evals" / "files" / "test-project"
    data_root = fixture_root / ".canon-ledger"

    for relative_path in ("state.json", "memory_scratchpad.json", "summaries/ch0003.md"):
        assert (data_root / relative_path).is_file(), f"评测样例缺少 {relative_path}"
    assert not (fixture_root / ("." + "web" + "novel")).exists(), "评测样例仍保留旧项目目录"


@pytest.mark.parametrize("skill_name", AUTHOR_REPORT_SKILLS)
def test_main_skills_define_author_friendly_progress_and_recovery_contract(skill_name: str):
    """四个主 Skill 必须有过程提示、少打扰确认、卡住恢复和日志边界。"""
    text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")

    for required in (
        "作者友好过程提示与恢复契约",
        "过程提示",
        "少打扰确认策略",
        "有限选项",
        "卡住时必须说明",
        "卡点",
        "已完成内容",
        "恢复建议",
        ".canon-ledger/logs/run_last.log",
        "run-log",
        "user-report",
    ):
        assert required in text, f"{skill_name}: 缺少过程/恢复契约 {required}"
    assert "不直接输出原始 JSON" in text or "不输出原始 JSON" in text


def test_write_skill_progress_nodes_are_author_friendly_and_limited():
    """写章过程节点必须压缩到不超过 6 个作者可理解阶段。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    marker = "写章过程节点（最多 6 个）"
    assert marker in text
    section = text[text.find(marker): text.find("## 充分性闸门")]
    nodes = re.findall(r"^\d+\.\s+(.+)$", section, flags=re.MULTILINE)
    assert 1 <= len(nodes) <= 6
    for forbidden in ("write-gate", "chapter-commit", "projection_status", "artifact", "schema"):
        assert forbidden not in "\n".join(nodes)
    for friendly in ("检查项目环境", "整理写作依据", "起草正文", "写作检查", "保存本章故事事实", "提交备份"):
        assert any(friendly in node for node in nodes), f"缺少作者友好节点 {friendly}"


def test_write_skill_resume_contract_uses_runtime_ledger_and_confirmation_boundaries():
    """写章重复执行必须先查可信断点，且在覆盖风险处停下确认。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    for required in (
        "run-ledger write-resume",
        "可信断点",
        "正文被手动改过",
        "章纲更新晚于正文",
        "本章已 accepted",
        "沿用当前正文 / 重新起草 / 只查看状态",
        "不得覆盖作者手改",
    ):
        assert required in text


def test_story_system_runtime_contract_commands_exist():
    text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "story-system" in text
    assert "--emit-runtime-contracts" in text


def test_canon_ledger_write_skill_uses_chapter_commit_as_step5_mainline():
    text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "chapter-commit" in text
    assert "CHAPTER_COMMIT" in text
    assert "state process-chapter" not in text


def test_canon_ledger_write_skill_uses_project_root_backup_not_bare_git_add():
    text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "canon_ledger.py" in text
    assert "--project-root \"${PROJECT_ROOT}\" backup" in text
    assert "git add ." not in text


def test_canon_ledger_query_skill_prefers_story_system_and_memory_contract():
    text = (SKILLS_DIR / "canon-ledger-query" / "SKILL.md").read_text(encoding="utf-8")
    assert "memory-contract load-context" in text
    assert ".story-system/" in text
    assert 'cat "$PROJECT_ROOT/.canon-ledger/state.json"' not in text


def test_context_agent_prefers_contract_and_latest_commit_mainline():
    text = (AGENTS_DIR / "context-agent.md").read_text(encoding="utf-8")
    assert "story_contracts" in text or ".story-system/" in text
    assert "CHAPTER_COMMIT" in text or "chapter-commit" in text
    assert "load-context" in text


def test_context_agent_consumes_all_hard_constraints_before_budgeted_evidence():
    text = (AGENTS_DIR / "context-agent.md").read_text(encoding="utf-8")

    assert "hard_constraints" in text
    assert "active_constraints" not in text
    for category in (
        "world_rule",
        "open_loop",
        "reader_promise",
        "relationship",
    ):
        assert category in text
    assert "不得按“前 N 条”" in text
    assert "数量限制只适用于这些软证据" in text
    assert "文风唯一来源" in text
    assert "设定集/文风提示词.md" in text
    assert "不要降级到会混入" in text


def test_context_agent_loads_fixed_guides_and_outputs_writer_brief():
    text = (AGENTS_DIR / "context-agent.md").read_text(encoding="utf-8")
    assert "写作铁律" in text
    assert "文风提示词" in text
    assert "写作任务书" in text
    assert "Step 2 直写提示词" not in text
    assert "Context Contract" not in text
    assert "怎么写更顺" not in text


def test_canon_ledger_plan_does_not_require_per_chapter_cool_point():
    text = (SKILLS_DIR / "canon-ledger-plan" / "SKILL.md").read_text(encoding="utf-8")
    assert "爽点不是必填" in text
    assert "禁止把「本章无爽点」当成规划失败" in text
    required_line = next(
        line for line in text.splitlines() if line.startswith("每章必须包含：")
    )
    assert "爽点" not in required_line


def test_write_review_init_skills_honor_subagent_model_config():
    for skill_name in ("canon-ledger-write", "canon-ledger-review", "canon-ledger-init"):
        text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")
        assert "subagent-models" in text, f"{skill_name} 未读取子代理模型配置"
        assert "pass_to_task" in text, f"{skill_name} 未说明何时把 model 传给 Task"


def test_canon_ledger_write_skill_skips_style_pipeline():
    text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "设定集/文风提示词.md" in text
    assert "anti_ai_force_check=pass" not in text
    assert "事实修补" in text
    assert "将正文改写为网文风格" not in text
    for forbidden in ("polish-guide.md", "style-adapter.md", "anti-ai-guide.md", "网文腔"):
        assert forbidden not in text, f"write skill 不应再点名 {forbidden}"


def test_agents_do_not_name_nonexistent_writing_dna_files():
    for filename in ("context-agent.md", "reviewer.md"):
        text = (AGENTS_DIR / filename).read_text(encoding="utf-8")
        assert "P20_WRITING_DNA" not in text
        assert "WRITING_DNA.md" not in text


def test_data_agent_is_described_as_extraction_only_not_direct_write_mainline():
    text = (AGENTS_DIR / "data-agent.md").read_text(encoding="utf-8")
    assert "chapter-commit" in text
    assert "extraction_result.json" in text
    assert "planned_nodes" in text
    assert "missed_nodes" in text
    assert "pending" in text
    assert "event_id" in text
    assert "event_type" in text
    assert "subject" in text
    assert "hook_type" not in text
    assert "hook_strength" not in text
    assert "直接写入 index.db 和 state.json" not in text
    for forbidden in (
        "RAG 向量索引",
        "observability",
        "场景索引已写入",
        "索引失败",
    ):
        assert forbidden not in text, f"data-agent.md 不应保留 projection 写入语义: {forbidden}"
    # data-agent 不得携带可运行的 chapter-commit 命令（commit 是主流程的事实提交入口，data-agent 只产 artifact）
    assert not re.search(r"canon_ledger\.py[^\n]+chapter-commit", text), (
        "data-agent.md 不应出现可运行的主 CLI chapter-commit 命令"
    )


# (已按 plan §12.2 退役) test_canon_ledger_write_data_agent_prompt_requires_extraction_schema：
# 该测试逐字要求主 Skill 写出 data artifact 的 schema 字段名，与判据一冲突。schema 字段保障已迁到
# data-agent.md 生产方（test_data_agent_is_described_as_extraction_only_not_direct_write_mainline）
# + precommit 负向用例（Task 7）。主 Skill 不再内联长 schema。


def test_dashboard_and_plan_skills_surface_story_runtime_mainline():
    dashboard_text = (SKILLS_DIR / "canon-ledger-dashboard" / "SKILL.md").read_text(encoding="utf-8")
    plan_text = (SKILLS_DIR / "canon-ledger-plan" / "SKILL.md").read_text(encoding="utf-8")
    assert "story-runtime/health" in dashboard_text
    assert ".story-system/" in plan_text


def test_canon_ledger_write_skill_routes_step2_through_writing_brief():
    text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "写作任务书" in text
    assert "context-agent" in text
    assert "Step 0.5" not in text
    assert 'cat "${SKILL_ROOT}/../../references/shared/core-constraints.md"' not in text
    assert 'cat "${SKILL_ROOT}/references/anti-ai-guide.md"' not in text


def test_context_agent_and_write_skill_form_isolated_write_chain():
    context_text = (AGENTS_DIR / "context-agent.md").read_text(encoding="utf-8")
    skill_text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")

    assert "写作任务书" in context_text
    assert "写作任务书" in skill_text
    assert "context-agent" in skill_text
    assert "Context Contract" not in context_text
    assert "Step 2 直写提示词" not in context_text


def test_no_direct_state_writes_in_write_skill():
    """canon-ledger-write SKILL.md 中不应有 set-chapter-status 调用。"""
    text = (SKILLS_DIR / "canon-ledger-write" / "SKILL.md").read_text(encoding="utf-8")
    assert "state set-chapter-status" not in text, (
        "canon-ledger-write 中不应直接调用 state set-chapter-status，"
        "chapter_status 由 state_projection_writer 在 commit 时自动推进"
    )


def test_no_direct_state_writes_in_agents():
    """agents 目录中不应有直接写 state/index 的指令。"""
    for agent_file in AGENT_FILES:
        text = _read_text(agent_file)
        assert "state set-chapter-status" not in text, (
            f"{agent_file.name}: 不应直接调用 state set-chapter-status"
        )


def test_deconstruction_agent_preserves_init_handoff_and_boundaries():
    """参考作品拆解必须只做提取，并限定在初始化阶段。"""
    text = _read_text(AGENTS_DIR / "deconstruction-agent.md")

    assert "init_reference_research" in text
    assert ".canon-ledger/tmp/reference_analyses/<safe-title>/" not in text
    assert "不写任何文件" in text
    assert "不得写 `_progress.md`" in text
    assert "resume_state" in text
    assert "tools: Read, Grep, Bash" in text
    assert "快速模式" in text
    assert "深度模式" in text
    assert "黄金三章" in text
    assert "情节点" in text
    assert "质量门控" in text
    assert "不得凭记忆" in text
    assert "条件框架" in text
    assert "情绪链条" in text
    assert "核心梗边界" in text

    for field in (
        "reader_promise",
        "opening_hook_patterns",
        "cool_point_loops",
        "protagonist_patterns",
        "antagonist_pressure_patterns",
        "pacing_notes",
        "borrowable_structures",
        "do_not_copy",
        "differentiation_requirements",
        "init_candidates",
        "quality",
        "resume_state",
        "orphan_plot_fallback",
        "canon_contamination_warnings",
    ):
        assert f'"{field}"' in text

    for forbidden_path in (
        ".story-system/",
        "设定集/",
        "大纲/",
        "正文/",
        ".canon-ledger/",
    ):
        assert forbidden_path in text

    assert "不写 `idea_bank.json`" in text
    assert "用户确认后" in text
    forbidden_marker = " ".join(("MIT", "License", "attribution"))
    assert forbidden_marker not in text


def test_canon_ledger_init_deconstruction_wiring_keeps_confirmation_gate():
    """初始化流程只能使用已经确认并完成变形的参考模式。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-init" / "SKILL.md")

    assert "使用 `Task` 工具调用插件 agent `deconstruction-agent`" in text
    assert "subagent_type:" not in text
    assert "Step 1.5：灵感来源询问" in text
    assert "进入故事核采集前" in text
    assert "不要默认拆书" in text
    assert "你这本书的灵感来源想从哪里开始" in text
    assert "init_reference_research" in text
    assert "init_reference_research JSON 对象" in text
    assert ".canon-ledger/tmp/reference_analyses/<safe-title>/" not in text
    assert "project_root=${PROJECT_ROOT" not in text
    assert "不写任何文件" in text
    assert "不得由 init 主流程口头替代拆解结果" in text
    assert "`quality`" in text
    assert "`quality.passed=false`" in text
    assert "`confidence < 0.85`" in text

    for handoff_field in (
        "reader_promise",
        "opening_hook_patterns",
        "cool_point_loops",
        "protagonist_patterns",
        "antagonist_pressure_patterns",
        "pacing_notes",
        "borrowable_structures",
        "differentiation_requirements",
        "init_candidates",
    ):
        assert handoff_field in text

    for forbidden_path in (
        "idea_bank.json",
        ".story-system",
        "设定集",
        "大纲",
        "正文",
        ".canon-ledger/state.json",
    ):
        assert forbidden_path in text

    assert "用户确认前" in text
    assert "Step 2-6 只能使用用户确认过、并已变形为本书差异化表达的模式" in text
    assert "汇总 Step 1.5 已确认的灵感来源" in text


# ---------------------------------------------------------------------------
# 7. A 类跨层红线：行为/契约级断言（Phase 0 守护）
#    这些断言守护「已实现」的业务红线，全部应为绿。优先断言结构不变量
#    （命令存在/顺序、节点 schema、变量化的真实参数），不做脆弱的文案匹配。
# ---------------------------------------------------------------------------

# A 类红线 2：placeholder-scan 必须出现在 plan 与 write 两层的关键节点。
def test_placeholder_scan_runs_in_both_plan_and_write_skills():
    """红线 2：plan 与 write 都必须显式调用 placeholder-scan CLI。"""
    plan_text = _read_text(SKILLS_DIR / "canon-ledger-plan" / "SKILL.md")
    write_text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    for name, text in (("canon-ledger-plan", plan_text), ("canon-ledger-write", write_text)):
        cmds = _extract_cli_subcommands(text)
        assert "placeholder-scan" in cmds, (
            f"{name}: 关键节点缺少 placeholder-scan CLI 调用"
        )


# A 类红线 3：story-system 章级刷新必须传入真实 CHAPTER_GOAL 变量，
# 不得把 {章纲目标} / 第N章章纲目标 这类占位文本当作 positional query。
@pytest.mark.parametrize("skill_name", ["canon-ledger-plan", "canon-ledger-write", "canon-ledger-review"])
def test_story_system_chapter_refresh_uses_real_goal_not_placeholder_query(skill_name: str):
    """红线 3：story-system 的 query 实参是 ${CHAPTER_GOAL} 变量，且禁占位文本写在命令里。"""
    text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")
    # 命令必须用变量化的真实目标作为 query 实参
    assert 'story-system "${CHAPTER_GOAL}"' in text, (
        f"{skill_name}: story-system 未使用真实 ${{CHAPTER_GOAL}} 作为 query 实参"
    )
    assert 'CHAPTER_GOAL="$(' in text, f"{skill_name}: 未从详细大纲实际赋值 CHAPTER_GOAL"
    assert "goal else sys.exit(2)" in text, f"{skill_name}: 本章目标为空时没有关闭流程"
    # 占位 query 绝不能作为 story-system 的 positional 实参出现
    for placeholder in ("{章纲目标}", "第N章章纲目标"):
        assert f'story-system "{placeholder}"' not in text, (
            f"{skill_name}: story-system 不得把占位文本 {placeholder} 当作 query"
        )
    # 必须显式声明「禁止占位 query」这一约束（断言事实存在，不锁具体措辞）
    assert "{章纲目标}" in text and "第N章章纲目标" in text, (
        f"{skill_name}: 缺少对占位 query 的明确禁止说明"
    )


# A 类红线 4：story-system 章级刷新必须 --persist 且 --emit-runtime-contracts。
@pytest.mark.parametrize("skill_name", ["canon-ledger-plan", "canon-ledger-write", "canon-ledger-review"])
def test_story_system_chapter_refresh_persists_runtime_contracts(skill_name: str):
    """红线 4：章级 story-system 刷新必须同时 --persist 与 --emit-runtime-contracts。"""
    text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")
    cmd_start = text.find('story-system "${CHAPTER_GOAL}"')
    assert cmd_start >= 0, f"{skill_name}: 缺少章级 story-system 调用"
    # 取该调用所在的命令行（到下一空行/段落结束），断言两个关键开关都在
    cmd_tail = text[cmd_start:cmd_start + 400]
    assert "--persist" in cmd_tail, f"{skill_name}: 章级 story-system 缺少 --persist"
    assert "--emit-runtime-contracts" in cmd_tail, (
        f"{skill_name}: 章级 story-system 缺少 --emit-runtime-contracts"
    )
    assert "--chapter" in cmd_tail, f"{skill_name}: 章级 story-system 缺少 --chapter"


def test_default_volume_template_only_records_consistency_facts():
    """默认卷规划模板不应规定固定情节节拍。"""
    text = _read_text(PLUGIN_ROOT / "templates" / "output" / "大纲-卷节拍表.md")
    for prescription in ("Fichtean", "All Is Lost", "中段反转（必填）", "爽点密度"):
        assert prescription not in text
    for required in ("开卷状态", "角色与关系状态迁移", "伏笔与开放问题", "卷末事实快照"):
        assert required in text


# A 类红线 5：write-gate 三道闸门必须齐全且顺序为 prewrite→precommit→postcommit。
def test_write_skill_gate_stages_ordered_prewrite_precommit_postcommit():
    """红线 5：write-gate 三道 gate 顺序不可乱。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    prewrite = text.find("write-gate --chapter {chapter_num} --stage prewrite")
    precommit = text.find("write-gate --chapter {chapter_num} --stage precommit")
    postcommit = text.find("write-gate --chapter {chapter_num} --stage postcommit")
    assert prewrite >= 0, "缺少 prewrite gate"
    assert precommit >= 0, "缺少 precommit gate"
    assert postcommit >= 0, "缺少 postcommit gate"
    assert prewrite < precommit < postcommit, (
        "write-gate 三道 gate 顺序必须为 prewrite→precommit→postcommit"
    )


# A 类红线 7：reviewer 原始 JSON 必须经 review-pipeline --save-audit 落库（write 与 review 两层）。
@pytest.mark.parametrize("skill_name", ["canon-ledger-write", "canon-ledger-review"])
def test_review_pipeline_persists_audit_in_review_chain(skill_name: str):
    """红线 7：reviewer JSON 经 review-pipeline --save-audit 落库。"""
    text = _read_text(SKILLS_DIR / skill_name / "SKILL.md")
    cmds = _extract_cli_subcommands(text)
    assert "review-pipeline" in cmds, f"{skill_name}: 缺少 review-pipeline CLI 调用"
    assert "--save-audit" in text, f"{skill_name}: review-pipeline 未带 --save-audit 落库"
    assert "--save-metrics" not in text, f"{skill_name}: 默认链不应继续写入评分指标"


def test_write_skill_resolves_review_mode_before_subagent_tasks():
    """写章流程必须给子代理传入已经确定的审查模式。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    assert "${REVIEW_MODE}" not in text, "不得把未赋值的 shell 变量传给子代理"
    assert text.count("- review_mode={review_mode}") == 2
    assert "默认命令取 `standard`" in text
    assert "`--fast` 取 `fast`" in text
    assert "`--minimal` 取 `minimal`" in text
    assert "`minimal` 不调用 reviewer" in text


# A 类红线 10：postcommit 必须验证 projection 五项；失败只 projections retry。
def test_write_skill_postcommit_verifies_five_projections_and_retry_only():
    """红线 10：projection 五项（state/index/summary/memory/vector）验证，失败只 retry。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    assert "state/index/summary/memory/vector" in text, (
        "缺少 projection 五项（state/index/summary/memory/vector）验证说明"
    )
    # 失败兜底唯一手段是 projections retry（命令以续行书写，直接断言字面调用）
    assert "projections retry --chapter {chapter_num}" in text, (
        "projection 失败兜底必须是 projections retry --chapter {chapter_num}"
    )


# A 类红线 12：plan 必须覆盖节拍表/时间线/结构化章纲节点/结构化总纲写回/状态更新。
def test_plan_skill_covers_outline_writeback_and_state_sync_contract():
    """红线 12：plan 的节拍表/时间线/章纲节点/总纲写回 JSON/master-outline-sync/update-state。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-plan" / "SKILL.md")
    assert "大纲/第{volume_id}卷-节拍表.md" in text
    assert "大纲/第{volume_id}卷-时间线.md" in text
    for node in ("CBN", "CPNs", "CEN", "必须覆盖节点", "本章禁区"):
        assert node in text, f"plan 缺少结构化章纲节点标记 {node}"
    required_line = next(
        line for line in text.splitlines() if line.startswith("每章必须包含：")
    )
    assert "时间锚点" in required_line
    assert "钩子" not in required_line
    assert "大纲/第{volume_id}卷-总纲写回.json" in text
    cmds = _extract_cli_subcommands(text)
    assert "master-outline-sync" in cmds, "plan 缺少 master-outline-sync 写回命令"
    assert "update-state" in cmds, "plan 缺少 update-state 状态更新命令"


# ---------------------------------------------------------------------------
# 8. B 类跨层新契约（plan §5.2-B / §4.5 写入所有权矩阵）
#    tools↔落盘一致性现状已满足 → 作通过型守护；
#    提交前只读 git diff 变更面校验现状缺失 → xfail，Task 5（Phase 1）落地后移除标记转正。
# ---------------------------------------------------------------------------

def _agent_tools(agent_name: str) -> list[str]:
    """解析某 agent frontmatter 的 tools 列表。"""
    fm = _extract_frontmatter(_read_text(AGENTS_DIR / f"{agent_name}.md"))
    return [t.strip() for t in fm.get("tools", "").split(",") if t.strip()]


# B 类红线（写入所有权 ↔ tools 一致，单一写入者）：
# data-agent 是三份 tmp artifact 的唯一写入者 → 必须持 Write；
# reviewer/context-agent/deconstruction-agent 只返回结果、由主流程落盘 → 不得持 Write。
def test_agent_write_ownership_matches_tools_frontmatter():
    """红线（写入所有权）：仅 data-agent 持 Write，其余三个 agent 不持 Write。"""
    assert "Write" in _agent_tools("data-agent"), (
        "data-agent 必须持有 Write（它是三份 tmp artifact 的唯一写入者）"
    )
    for agent_name in ("reviewer", "context-agent", "deconstruction-agent"):
        assert "Write" not in _agent_tools(agent_name), (
            f"{agent_name} 不得持有 Write（它只返回结果，由主流程落盘）"
        )


# B 类红线（提交前变更面校验）：write SKILL 在 chapter-commit 前必须执行只读 git diff 变更面校验。
# 现状 write SKILL 尚无此步 → 标 xfail；Task 5（Phase 1）实现后移除本标记，转为硬守护。
# B 类红线（提交前变更面校验）：write SKILL 在 chapter-commit 前必须执行只读 git diff 变更面校验。
# Phase 1 (Task 5) 已落地 → 转为硬守护（移除 xfail 标记）。
def test_write_skill_has_readonly_git_diff_change_surface_check():
    """红线（提交前变更面校验）：write SKILL 在 chapter-commit 前执行只读 git diff 校验。"""
    text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    assert "diff --name-status" in text, (
        "write SKILL 缺少提交前只读 git diff --name-status 变更面校验"
    )
    assert "diff --check" in text, (
        "write SKILL 缺少 git diff --check 空白/冲突标记校验"
    )


# B 类红线（写入所有权·prompt 层）：write/review 必须在文本层声明所有权，
# 与 frontmatter（test_agent_write_ownership_matches_tools_frontmatter）+ behavior eval（artifact_ownership）三处互守。
def test_write_review_skills_state_artifact_ownership():
    """reviewer 返回 JSON、主流程落盘 review_results.json、data-agent 唯一写入者。"""
    write_text = _read_text(SKILLS_DIR / "canon-ledger-write" / "SKILL.md")
    review_text = _read_text(SKILLS_DIR / "canon-ledger-review" / "SKILL.md")
    for name, text in (("canon-ledger-write", write_text), ("canon-ledger-review", review_text)):
        assert "主流程" in text and ".canon-ledger/tmp/review_results.json" in text, (
            f"{name}: 缺 reviewer→主流程落盘 review_results.json 的所有权说明"
        )
    assert "唯一写入者" in write_text, "canon-ledger-write 缺 data-agent 唯一写入者说明"
    assert "主流程只检查文件存在与 schema" in write_text
    assert "不直接写 state/index/summaries/memory/vectors/projection" in write_text


# §9.3/§12.3：reviewer 删除 ReAct/思维链 元叙述后的正向守护（审查只给输出合同，不教它怎么想）。
def test_reviewer_has_no_react_meta_narrative():
    """reviewer.md 不得保留 ReAct/思维链 元叙述。"""
    text = _read_text(AGENTS_DIR / "reviewer.md")
    assert "ReAct" not in text, "reviewer 不应出现 ReAct 字样"
    assert "思维链" not in text, "reviewer 不应保留思维链元叙述"


def test_reviewer_does_not_block_on_missing_previous_chapter_summary():
    """第一章或读不到摘要时，不得把连贯维写成 blocking。"""
    text = _read_text(AGENTS_DIR / "reviewer.md")
    assert "读不到上章摘要 → 不是错误" in text
    assert "禁止因此输出 blocking" in text
    assert "不要查章末钩子、场景过渡写法、情绪弧" in text
    assert "自由文本摘要不是真源" in text
    assert "不得因为「没有上章」输出 blocking" in text

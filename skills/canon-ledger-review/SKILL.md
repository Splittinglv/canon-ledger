---
name: canon-ledger-review
description: 审查章节长期事实：设定、时间线、连续性、角色知识边界与明确规则冲突。不确定项转人工，不评价文风、剧情选择或人物动机。
---

# Quality Review Skill

## 目标

- 解析真实书项目根，调度统一 `reviewer` 完成结构化审查并落库。
- 主链事实以 `.story-system/reviews/chapter_{NNN}.review.json` 与 latest accepted `CHAPTER_COMMIT` 为准；`.canon-ledger/state.json` 仅为只读投影。
- 有 `blocking=true` 问题时交用户裁决。

## 红线

- 必须通过 `Task` 工具调用 `reviewer`，禁止主流程伪造结论或口头总结代替 subagent 输出。
- reviewer 只返回严格 JSON；主流程负责把返回值写入 `${PROJECT_ROOT}/.canon-ledger/tmp/review_results.json`，随后由 `review-pipeline` 覆盖为标准 review_result artifact。
- 报告与无评分审查审计只由 `review-pipeline --save-audit` 产出；主流程不生成文笔、节奏或总体评分。
- 只有有直接证据、两条事实不能同时成立的矛盾才能进入 issues；容易误判的判断进入 `manual_checks`，不自动阻断。
- `must_cover_nodes` / `forbidden_zones` 属于写作计划履约，不属于默认事实穿帮检查。
- 项目根不合法 / 缺 `.canon-ledger/state.json` / 缺待审正文 → 阻断。

## 执行流程

### Step 1：解析项目根

```bash
# 这段引导仅适用于 POSIX shell（sh/bash/zsh）；Windows 请使用 Git Bash 或 WSL。
# 缓存安装必须使用 Cursor 注入的插件根；不扫描缓存目录寻找可执行脚本。
# bootstrap_env.py 输出固定六行数据协议：逐行 read 赋值，禁止 eval/source 执行输出。
_PLUGIN_ROOT_HINT="${CANON_LEDGER_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
if [ ! -f "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/canon-ledger"
fi
if [ ! -f "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py" ]; then
  _PLUGIN_ROOT_HINT="$(pwd)"
fi
if [ ! -f "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py" ]; then
  _PLUGIN_ROOT_HINT="$(dirname "$(pwd)")"
fi
if [ ! -f "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py" ]; then
  _PLUGIN_ROOT_HINT="$(dirname "$(dirname "$(pwd)")")"
fi
if [ ! -f "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py" ]; then
  _PLUGIN_ROOT_HINT="$(dirname "$(dirname "$(dirname "$(pwd)")")")"
fi
_ENV_LINES="$(python3 -X utf8 "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py")" || {
  echo "ERROR: 插件根不可信或安装不完整。请使用 Cursor 注入的插件根，或安装到 ~/.cursor/plugins/local/canon-ledger" >&2
  exit 1
}
_ENV_PARSE_OK=1
{
  IFS= read -r CANON_LEDGER_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r SCRIPTS_DIR || _ENV_PARSE_OK=0
  IFS= read -r WORKSPACE_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PROJECT_DIR || _ENV_PARSE_OK=0
  IFS= read -r CANON_LEDGER_PYTHON || _ENV_PARSE_OK=0
} <<EOF
$_ENV_LINES
EOF
if [ "$_ENV_PARSE_OK" -ne 1 ]; then
  echo "ERROR: 无法解析插件环境协议" >&2
  exit 1
fi
export CANON_LEDGER_PLUGIN_ROOT CURSOR_PLUGIN_ROOT SCRIPTS_DIR WORKSPACE_ROOT CURSOR_PROJECT_DIR CANON_LEDGER_PYTHON
unset _PLUGIN_ROOT_HINT _ENV_LINES _ENV_PARSE_OK
```

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-review"
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" subagent-models --format json
```
调用 `reviewer` 前必须读取上面的 JSON。`agents["reviewer"].pass_to_task=true` 时，Task 必须传入该 `model` slug；否则不要传 `model`（跟当前聊天同一个模型）。本轮用户点名优先于书项目 `.canon-ledger/subagent-models.json` 和用户级 `~/.cursor/canon-ledger/subagent-models.json`。

`PROJECT_ROOT` 必须包含 `.canon-ledger/state.json`，否则阻断。

### Step 2：目标章缺合同时刷新 runtime 合同

目标章缺 runtime 合同时，先用详细大纲的真实本章目标刷新（`CHAPTER_GOAL` 禁止 `{章纲目标}` / `第N章章纲目标` 占位文本）：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
GENRE="$("${CANON_LEDGER_PYTHON}" -X utf8 -c "import json; s=json.load(open('${PROJECT_ROOT}/.canon-ledger/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"
CHAPTER_GOAL="$("${CANON_LEDGER_PYTHON}" -X utf8 -c "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from chapter_outline_loader import load_chapter_execution_directive; directive=load_chapter_execution_directive(Path(sys.argv[2]),int(sys.argv[3])); goal=str(directive.get('goal') or '').strip(); print(goal) if goal else sys.exit(2)" "${SCRIPTS_DIR}" "${PROJECT_ROOT}" "{chapter_num}")" || {
  echo "错误：详细大纲缺少本章真实目标，停止刷新合同。" >&2
  exit 1
}
[ -n "${CHAPTER_GOAL}" ] || { echo "错误：本章目标为空，停止审查。" >&2; exit 1; }

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  story-system "${CHAPTER_GOAL}" --genre "${GENRE}" --chapter {chapter_num} --persist --emit-runtime-contracts --format both
```

### Step 3：按需加载参考

| Trigger | Reference |
|---------|-----------|
| always | `../../references/review-schema.md` |
| blocking issue 需用户裁决 (Step 8) | `../../references/review/blocking-override-guidelines.md` |

审查只对长期事实、知识边界和明确机械规则。不要检查人物动机、一般因果、章纲完成度，也不要加载写法教程或爽点库。

### Step 4：导出 as-of 快照并确认待审正文

不要读取 `.canon-ledger/state.json` 或 `index.db` 来核对人物/伏笔——那是当前投影，重审旧章会混入未来事实。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" chapter-binding \
  --chapter {chapter_num} \
  --out "${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json" \
  --format json

mkdir -p "${PROJECT_ROOT}/.canon-ledger/tmp"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  memory-contract export-asof --chapter {chapter_num} \
  --out "${PROJECT_ROOT}/.canon-ledger/tmp/asof_snapshot.json"
```

确认对应正文文件存在且非空；缺正文或缺 as-of 快照立即阻断。

### Step 5：调用统一审查 Agent

必须通过 `Task` 工具调用 `reviewer`。审查方法与维度细则由 reviewer 自带，本 Skill 不展开。

```text
使用 `Task` 工具调用插件 agent `reviewer`。如果 `Task` 不能按名称调用插件 agent，则启动 `generalPurpose` 子代理：先 `Read` `${CANON_LEDGER_PLUGIN_ROOT}/agents/reviewer.md`，再严格执行该规范。仅当 `subagent-models` 中 `agents["reviewer"].pass_to_task=true` 时才给 `Task` 传 `model`。

任务参数：chapter={chapter_num}; review_mode=standard; chapter_file={chapter_file}; asof_snapshot_file=${PROJECT_ROOT}/.canon-ledger/tmp/asof_snapshot.json; chapter_contract_file=${PROJECT_ROOT}/.story-system/chapters/chapter_{NNN}.json; review_contract_file=${PROJECT_ROOT}/.story-system/reviews/chapter_{NNN}.review.json; chapter_binding_file=${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json; project_root=${PROJECT_ROOT}; scripts_dir=${SCRIPTS_DIR}。先读取 as-of v3 快照，禁止查询当前 state/index；同时检查 coverage 与 verification；章合同只作背景，不把节点履约当事实问题；读取 binding 后将完整对象原样放入输出 JSON 顶层 `chapter_binding`；逐项覆盖 setting/timeline/continuity/character/logic 五个维度；确定矛盾写 issues，证据不足写 manual_checks；除 JSON 字段、固定枚举、路径和正文原样引用外，所有自然语言审查内容使用中文，严格输出 reviewer schema JSON，不评分，不口头总结。
```

reviewer 返回后，主流程把严格 JSON 写入 `${PROJECT_ROOT}/.canon-ledger/tmp/review_results.json`（reviewer 不持 Write，是这份 artifact 的非写入方）。`review-pipeline` 必须把同一路径覆盖为标准 review_result artifact（含 `blocking_count`）。

调用后主流程必须记录 `SubagentRun` 汇总（仅供最终报告使用）：

```json
{
  "name": "reviewer",
  "user_label": "写作检查",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

reviewer 跳过、失败、输出不完整、正文为空、维度跳过、blocking issue、manual_checks 或耗时异常，必须写入 `problems` / `auto_handled`，不得在最终报告中静默。

### Step 6：生成报告并保存审计记录

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" review-pipeline \
  --chapter {chapter_num} \
  --review-results "${PROJECT_ROOT}/.canon-ledger/tmp/review_results.json" \
  --chapter-binding "${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json" \
  --audit-out "${PROJECT_ROOT}/.canon-ledger/tmp/review_audit.json" \
  --report-file "审查报告/第{chapter_num}章审查报告.md" \
  --save-audit
```

`review-pipeline --save-audit` 同时完成报告生成、`review_audit.json` 输出、`review_audits` 表写入。审计记录只保存检查范围、问题计数和阻断数，不保存质量分数。阻断判断以 review_results 中的 `blocking=true` 为准。

### Step 7：刷新只读审查投影

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" update-state -- --add-review "{chapter_num}-{chapter_num}" "审查报告/第{chapter_num}章审查报告.md"
```

该记录是只读投影，不是写后事实真源。

### Step 8：处理阻断

存在任意 `blocking=true` 问题时，用 `AskQuestion` 让用户裁决（若不可用，在聊天里给出同样选项）：

- 立即修复：输出返工清单，仅在用户明确授权下做最小修改。
- 仅保存报告，稍后处理：保留报告与指标记录，结束流程。

## 成功标准

1. 已解析真实书项目根。
2. 已通过 `reviewer` 输出结构化问题 JSON，落盘到 `.canon-ledger/tmp/review_results.json`。
3. 审查报告已生成，`review_audits` 已写入 `index.db`，`review_audit.json` 已输出。
4. 审查记录已写入 `.canon-ledger/state.json` 只读投影。
5. 存在阻断问题时，用户已明确选择处理策略。

## 作者友好过程提示与恢复契约

审查开始前先说明本次会经历：定位待审正文 -> 刷新缺失合同 -> 写作检查 -> 生成报告和指标 -> 处理阻断裁决。过程提示用作者语言，不直接输出原始 JSON、traceback 或长命令日志；技术详情写入 `.canon-ledger/logs/run_last.log`：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" run-log \
  --event review-progress \
  --payload-json "{\"stage\": \"review\", \"chapter\": {chapter_num}}" \
  --format text
```

过程提示每次不超过两行，只说当前动作和影响，例如“正在生成审查报告：确定穿帮会单列，拿不准的地方交给你确认”。少打扰确认策略：无阻断时不强制询问；manual_checks 可留在报告稍后处理，只有 blocking issue、缺待审正文或用户要求立即修改时才停下询问。

需要用户裁决时使用有限选项，并说明影响；例如立即修复 / 仅保存报告稍后处理 / 放弃本次审查。卡住时必须说明卡点、已完成内容和恢复建议，例如“reviewer 结果已保存，审计记录落库失败；重新运行 `/canon-ledger-review {chapter_num}` 会从报告落库继续”。

不可恢复故障才在最终报告提示 `.canon-ledger/logs/run_last.log`；平时只保留日志，不打扰作者。收尾必须调用作者报告 helper：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage review \
  --chapter {chapter_num} \
  --format text
```

## 作者友好最终报告契约

最终回复必须面向作者，不输出原始 JSON、traceback 或长命令日志。使用固定三段式，并以一句总状态开头：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、产生的文件与完成情况
- ...

二、过程中遇到的问题与异常耗时
- 已自动处理：...
- 建议确认：...
- 必须处理：...

三、下一步建议
- ...
```

必须汇报：
- 审查报告文件。
- `.canon-ledger/tmp/review_results.json`。
- `.canon-ledger/tmp/review_audit.json`。
- `review_audits` 是否落库。
- 阻断问题数量。
- 用户裁决状态。
- 如果无阻断，明确可以继续写作。

状态规则：
- 有 blocking 问题且用户未选择处理策略时，最终状态为“需要你处理”。
- 只保存报告、稍后处理时，最终状态为“需要你处理”或“部分完成”。
- reviewer 跳过、失败或输出不完整时，最终状态不得写“已完成”。

异常分类：
- 已自动处理：重复生成报告、覆盖本次旧审查中间文件、成功补写审计记录。
- 建议确认：`manual_checks`、命名归属或语义不明确的事实看一眼。
- 必须处理：阻断问题、缺待审正文、reviewer 输出不完整、审计记录落库失败。

下一步建议必须使用任务化语言 + 可复制命令。该章人工事实队列仍有待确认项时（`human-review list --chapter {chapter_num}` 的 `pending` 非空），必须一并给出确认命令，例如：

```text
- 审查无阻断，可以继续写下一章：
  /canon-ledger-write {next_chapter}
- 本章还有候选事实等你确认，逐条裁决后系统会自动重新提交本章：
  /canon-ledger-confirm {chapter_num}
```

不写 token 统计；如需排查故障，只给日志路径或建议运行 `/canon-ledger-doctor`。

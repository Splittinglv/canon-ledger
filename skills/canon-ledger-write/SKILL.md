---
name: canon-ledger-write
description: 按上下文→起草→事实审查→提交→备份产出章节。默认只守长期事实一致性；文风和写作偏好由用户、全书提示词与模型决定，不做强制检查。
---

# 写章流程

## 目标

产出章节到 `正文/第{NNNN}章-{title}.md`。默认只守长期一致性：设定、时间线、伏笔、人物状态、角色知识边界、真实在场和物品持有。章纲完成度单独报告、默认不阻断；文风优先级为本轮用户要求 > 书项目 `设定集/文风提示词.md`（可空）> 当前模型默认，且不进入事实审查。字数跟用户或大纲走，插件不规定章长。

## 模式

| 模式 | 流程 |
|------|------|
| 默认 | Step 1→2→3→4(仅事实修补)→5→6 |
| `--fast` | Step 1→2→3(轻量事实维)→4(仅事实修补)→5→6 |
| `--minimal` | Step 1→2→3(写 no-review artifact)→5→6 |

主流程必须先把命令模式解析为文字参数 `{review_mode}`：默认命令取 `standard`，`--fast` 取 `fast`，`--minimal` 取 `minimal`。Task 中应直接传入解析后的枚举值，不得传未赋值的 shell 变量或字面占位符。

## 硬规则

- 禁止并步、跳步、伪造审查
- 必须使用 `Task` 工具调用指定 subagent；不得用主流程口头代替 subagent 输出
- 每个正文字节版本只审查一轮；blocking 事实问题定点修复后，因正文哈希已变更，必须对最终版本重新调用 reviewer 并覆盖旧审查 artifact；经用户裁决的未修改版本可沿用当前 binding
- 失败只补跑失败步骤，不回退
- 默认不加载写法教程、爽点库、Anti-AI / 润色材料；不把书面语/口语、句式、修辞当问题来改正文
- 参考资料按步骤按需加载，只取一致性事实

## 分轨优先级

- 事实：作者初始化设定 / 人工确认 / verified 事实 > 已接受章节事实；普通剧情或文风要求不能覆盖。
- 剧情：本轮剧情要求 > 章纲 > 模型自由发挥。
- 风格：本轮文风要求 > 全书文风提示词 > 模型默认。
- 只有用户明确提出修改设定、追认或重置事实时才进入 retcon 流程；普通写章要求不得隐式改写 canon。

## CSV 检索（Step 2 按需，仅命名区分）

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill write --table {表名} --query "{关键词}" --genre {题材}
```

触发条件：新角色→命名规则（区分人物，不规定文笔）。插件只随包发布命名规则、人设与关系、金手指与设定三张一致性表，不提供写法、桥段或爽点表。

## 执行流程

### 准备：预检

```bash
# 这段引导仅适用于 POSIX shell（sh/bash/zsh）；Windows 请使用 Git Bash 或 WSL。
# 缓存安装必须使用 Cursor 注入的插件根；不扫描缓存目录寻找可执行脚本。
# bootstrap_env.py 输出固定六行数据协议：逐行 read 赋值，禁止 eval/source 执行输出。
_PLUGIN_ROOT_HINT="${CANON_LEDGER_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
if [ -z "$_PLUGIN_ROOT_HINT" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/canon-ledger"
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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-write"

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" preflight
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" subagent-models --format json
```
子代理模型是可选配置。读取 JSON 里每个 `agents.<name>`：`pass_to_task=true` 时，调用对应 Task **必须**传入该 `model` slug；`inherit` / `pass_to_task=false` 则**不要**传 Task 的 `model`（跟当前聊天同一个模型）。本轮用户点名的模型优先于配置文件。slug 必须是 Cursor Task 当前允许的模型 id，不要用展示名或中文。配置文件：书项目 `.canon-ledger/subagent-models.json`，其次 `~/.cursor/canon-ledger/subagent-models.json`。没有配置文件就全部 inherit。

### 准备：刷新合同树

genre 从 `.canon-ledger/state.json` 的初始化配置快照读取，用于刷新合同树；写前主链真源仍是 `.story-system/` 合同。调用 story-system 前必须先从详细大纲解析真实本章目标，禁止传 `{章纲目标}`、`第N章章纲目标` 等占位 query。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
GENRE="$("${CANON_LEDGER_PYTHON}" -X utf8 -c "import json,sys; s=json.load(open('${PROJECT_ROOT}/.canon-ledger/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"
CHAPTER_GOAL="$("${CANON_LEDGER_PYTHON}" -X utf8 -c "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from chapter_outline_loader import load_chapter_execution_directive; directive=load_chapter_execution_directive(Path(sys.argv[2]),int(sys.argv[3])); goal=str(directive.get('goal') or '').strip(); print(goal) if goal else sys.exit(2)" "${SCRIPTS_DIR}" "${PROJECT_ROOT}" "{chapter_num}")" || {
  echo "错误：详细大纲缺少本章真实目标，停止刷新合同。" >&2
  exit 1
}
[ -n "${CHAPTER_GOAL}" ] || { echo "错误：本章目标为空，停止写作。" >&2; exit 1; }

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  story-system "${CHAPTER_GOAL}" --genre "${GENRE}" --chapter {chapter_num} --persist --emit-runtime-contracts --format both

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage prewrite --format json
```

必备文件：`MASTER_SETTING.json`（题材/设定合同）、`volume_{NNN}.json`（卷级约束）、`chapter_{NNN}.review.json`（必须节点/禁区）。缺失则阻断。

`chapter_{NNN}.json` 必须优先检查顶层 `chapter_directive`。`chapter_focus` 只能来自 `chapter_directive.goal` 或真实 query，不得从 `dynamic_context` 的参考摘要继承。

主流程必须把本轮用户消息里除写章命令/章号/模式开关外的要求写入 `${PROJECT_ROOT}/.canon-ledger/tmp/turn_requirements.md`，格式固定为：

```markdown
## 本轮写作要求
{用户原文中的情节、视角、人物、禁区等要求；没有则写「无」}

## 本轮文风覆盖
{用户明确的人称/口吻/文风，例如「这一章用冷峻第一人称」；没有则写「无」}

## 本轮事实变更（retcon）
{只有用户明确要求修改既有设定/事实时原样记录；普通剧情要求写「无」}
```

没有本轮额外要求时仍要写该文件，三段都写「无」。禁止把本轮要求只留在聊天记录里。

写作任务书排序必须固定为：
1. 本章硬性约束：完整消费事实型 `chapter_directive` 字段 `goal/obstacles/cost/time_anchor/chapter_span/previous_chapter_gap/countdown/key_entities/chapter_change/core_conflict/viewpoint/chapter_end_open_question`。`hook/hook_type/hook_strength/strand/antagonist_tier` 以及 CBN/CPNs/CEN 不是硬约束；仅当本轮用户要求或 `设定集/文风提示词.md` 点名时才写入任务书
2. 章纲节点（若有 `must_cover_nodes`），作为剧情方向与履约报告依据，默认不升格为事实检查
3. 本章禁区：`forbidden_zones`，作为剧情计划提醒；只有作者显式选择 strict 才作为提交阻断
4. 剧情/人物事实：上章未闭合问题、伏笔、能力边界、在场与持有、知识边界。不要写入 anti_patterns、钩子类型或写法教程
5. 文风：先粘贴本轮用户明确的人称/口吻/文风覆盖；再粘贴 `设定集/文风提示词.md` 里作者手写的正文（去掉 HTML 注释）。两处都没有 → 写「无」

### Step 1：context-agent 生成写作任务书

必须使用 `Task` 工具调用 `context-agent`，不得由主流程自行整理任务书。

使用 `Task` 工具调用插件 agent `context-agent`。如果 `Task` 不能按名称调用插件 agent，则启动 `generalPurpose` 子代理：先 `Read` `${CANON_LEDGER_PLUGIN_ROOT}/agents/context-agent.md`，再严格执行该规范。仅当 `subagent-models` 中 `agents["context-agent"].pass_to_task=true` 时才给 `Task` 传 `model`。

Task:
- chapter={chapter_num}
- review_mode={review_mode}（传入上方已经解析好的 `standard`、`fast` 或 `minimal`）
- project_root=${PROJECT_ROOT}
- scripts_dir=${SCRIPTS_DIR}
- storage_path=${PROJECT_ROOT}/.canon-ledger
- 不要把 `state.json` 整文件当事实源 Read；卷号由现有只返回整数的 loader 提供，写作任务书的事实只来自 as-of 快照与 load-context
- turn_requirements_file=${PROJECT_ROOT}/.canon-ledger/tmp/turn_requirements.md
- style_override=本轮文风覆盖原文；没有则传空字符串
- 先 research，把本轮要求拆成剧情、风格、显式 retcon；普通剧情和风格不得覆盖事实。再按长期事实边界 → 本章剧情目标/节点 → 计划禁区 → 人物事实 → 文风的顺序输出五段写作任务书。
- 文风优先级：本轮用户要求 > 全书文风提示词 > 模型默认。不要把写法教程、句式/口吻建议、题材节奏配方写进任务书。
- 不要把 hook/CBN/anti_patterns 当硬约束。钩子仅当本轮用户要求或全书文风提示词点名时进入任务书。
- 上下文不足时返回 blocker。

产物：一份写作任务书，能独立支撑 Step 2 起草。

调用后主流程必须记录 `SubagentRun` 汇总（仅供最终报告使用）：

```json
{
  "name": "context-agent",
  "user_label": "整理写作依据",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

上下文不足、事实源降级、伏笔数据缺失、任务书不完整或耗时异常，必须写入 `problems` / `auto_handled`，不得在最终报告中静默。

### Step 2：起草正文

只根据任务书起草。不要加载插件里的写法教程。

只输出纯正文，无占位正文（如 `[待补充]`、`[TODO]`、`...（省略）...`）。有结构化节点时以章纲为剧情方向，长期事实不能被普通剧情要求覆盖。起草时同守三条防幻觉约束：时间不得无故回跳，倒计时必须推进；上章明确未闭合的问题本章应有承接（允许部分兑现）；能力、道具、情报不得超出任务书与已记录事实。文风服从任务书第 5 段：本轮用户要求优先于全书文风提示词；两处都没有就按当前模型默认写。

### Step 3：审查

必须使用 `Task` 工具调用 `reviewer`，不得由主流程伪造审查 JSON。

调用 reviewer 前先固化待审正文的内容绑定，并导出截至 N-1 的不可变事实快照：

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

使用 `Task` 工具调用插件 agent `reviewer`。如果 `Task` 不能按名称调用插件 agent，则启动 `generalPurpose` 子代理：先 `Read` `${CANON_LEDGER_PLUGIN_ROOT}/agents/reviewer.md`，再严格执行该规范。仅当 `subagent-models` 中 `agents["reviewer"].pass_to_task=true` 时才给 `Task` 传 `model`。

Task:
- chapter={chapter_num}
- review_mode={review_mode}（本步骤只会传 `standard` 或 `fast`；`minimal` 不调用 reviewer）
- chapter_file=${CHAPTER_FILE}
- asof_snapshot_file=${PROJECT_ROOT}/.canon-ledger/tmp/asof_snapshot.json（截至 N-1 的不可变快照；禁止改读当前 state/index）
- chapter_contract_file=${PROJECT_ROOT}/.story-system/chapters/chapter_{NNN}.json
- review_contract_file=${PROJECT_ROOT}/.story-system/reviews/chapter_{NNN}.review.json
- chapter_binding_file=${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json（读取后将完整对象原样写入输出 JSON 顶层 `chapter_binding`）
- project_root=${PROJECT_ROOT}
- scripts_dir=${SCRIPTS_DIR}
- 只返回严格的 reviewer schema JSON，不写任何文件。
- 除 JSON 字段、固定枚举、路径和正文原样引用外，所有自然语言审查内容使用中文。
- standard 必须逐项覆盖 setting/timeline/continuity/character/logic；fast 必须覆盖 setting/timeline/continuity/character（知识边界不可跳过），只跳过 logic，并由 artifact 明确报告降级。
- 只有有直接证据、两条事实不能同时成立的矛盾进入 issues；容易误判的内容进入 manual_checks，不检查文风、章纲完成度、一般因果或人物动机。
- 不评分、不口头总结。

reviewer 只返回 JSON；主流程负责用 `Write` 把返回的 JSON 写入 `${PROJECT_ROOT}/.canon-ledger/tmp/review_results.json`（reviewer 不持 Write，是这份 artifact 的非写入方）。随后必须运行 review-pipeline；review-pipeline 会把同一路径覆盖为标准 review_result artifact（含 `blocking_count`），供 precommit gate 与后续提交命令使用。

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

reviewer 跳过、失败、输出不完整、`--minimal` 写 no-review artifact、blocking issue、manual_checks、维度跳过或耗时异常，必须写入 `problems` / `auto_handled`，不得在最终报告中静默。

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

每个正文字节版本只跑一轮审查。只处理已证实的事实问题。`blocking=true` 的事实问题在不改剧情、不破设定、**不改文风**的前提下定点修复；任何字节变更都会使旧 `chapter_binding`、review artifact 和审计记录失效，因此必须先重新生成 binding，再对最终正文重新调用 reviewer + review-pipeline，然后才能进 Step 5。确实无法修复的阻断问题用 `AskQuestion` 让用户裁决。`manual_checks` 进入确认队列，提交备份后当场确认；不得擅自改正文或阻断提交。口吻、句式、修辞类意见一律忽略。`--fast` 检查 setting/timeline/continuity/character（含知识边界），并在作者报告中明确标记未检查 logic。

`--minimal` 不调用 reviewer，也不生成审查报告或审计记录；必须通过统一 CLI **覆盖写入**本章新的跳过审查凭据（禁止复用旧 artifact），使 Step 5 提交链有有效 `--review-result`（成功标准“审查已落库”对 `--minimal` 的豁免仍成立）：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" review-pipeline \
  --chapter {chapter_num} \
  --review-results "${PROJECT_ROOT}/.canon-ledger/tmp/review_results.json" \
  --chapter-binding "${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json" \
  --minimal
```

### Step 4：事实修补

只修补 Step 3 留下的已证实事实问题（设定 / 时间线 / 连贯 / 知识边界 / 明确机械规则）。不修人物动机、一般因果、文风或 `manual_checks`；没有确定 issue 则立即进入 Step 5。

修复时只改正错的事实，不顺带改口吻、修辞或句式。`--minimal` 跳过本步。

### Step 5：提交

#### 5.1 Data Agent 提取事实

必须使用 `Task` 工具调用 `data-agent`，产出 fulfillment_result / disambiguation_result / extraction_result 三份 JSON，并复用 Step 3 的 review_results。若 Step 3 未导出 as-of 快照，此处补跑 `memory-contract export-asof --chapter {chapter_num}`。

使用 `Task` 工具调用插件 agent `data-agent`。如果 `Task` 不能按名称调用插件 agent，则启动 `generalPurpose` 子代理：先 `Read` `${CANON_LEDGER_PLUGIN_ROOT}/agents/data-agent.md`，再严格执行该规范。仅当 `subagent-models` 中 `agents["data-agent"].pass_to_task=true` 时才给 `Task` 传 `model`。

Task:
- chapter={chapter_num}
- chapter_file=${CHAPTER_FILE}
- asof_snapshot_file=${PROJECT_ROOT}/.canon-ledger/tmp/asof_snapshot.json（实体、别名、伏笔状态只来自这份 N-1 快照）
- chapter_contract_file=${PROJECT_ROOT}/.story-system/chapters/chapter_{NNN}.json（权威 `must_cover_nodes` 来源）
- chapter_binding_file=${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json（必须原样复制到三份 artifact 顶层）
- project_root=${PROJECT_ROOT}
- scripts_dir=${SCRIPTS_DIR}
- output_dir=${PROJECT_ROOT}/.canon-ledger/tmp
- 按你自己的 schema（见 data-agent 输出格式段）生成 fulfillment_result.json、disambiguation_result.json、extraction_result.json 三份 artifact。
- 你是这三份 artifact 的唯一写入者；不直接写 state/index/summaries/memory/vectors/projection。

artifact 字段 schema 由 data-agent 自身定义、runtime validator 校验；主流程只检查文件存在与 schema，不重写、不补写、不口头替代。

调用后主流程必须记录 `SubagentRun` 汇总（仅供最终报告使用）：

```json
{
  "name": "data-agent",
  "user_label": "保存本章故事事实",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

三份 artifact 写入状态、schema 不合格、pending 人工项、长时间无进展或输出不完整，必须写入 `problems`；普通 pending 会排除候选事实后继续提交，不得把它误报成 runtime 失败。

#### 5.2 提交前校验与 CHAPTER_COMMIT

先跑 precommit gate：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage precommit --format json
```

precommit 通过后，运行提交前只读 `git diff` 变更面校验（写入所有权 sanity check，只读、不 stage、不提交）：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
if git -C "${PROJECT_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "${PROJECT_ROOT}" diff --name-status -- .
  git -C "${PROJECT_ROOT}" diff --check -- .
fi
```

变更面不得出现插件目录、其他书项目、其他章节正文或不属于本章流程的手写状态文件；`git diff` 只覆盖 git 可见文件，SQLite / `.canon-ledger/` 内部语义由 5.3 postcommit 与 runtime 只读查询验证。若项目根不是 git worktree，记录“跳过 git diff 校验”，不得因此跳过 precommit gate。本步只读，禁止在此执行 `git add`/`git commit`。

校验通过后运行 chapter-commit：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" chapter-commit \
  --chapter {chapter_num} \
  --review-result "${PROJECT_ROOT}/.canon-ledger/tmp/review_results.json" \
  --fulfillment-result "${PROJECT_ROOT}/.canon-ledger/tmp/fulfillment_result.json" \
  --disambiguation-result "${PROJECT_ROOT}/.canon-ledger/tmp/disambiguation_result.json" \
  --extraction-result "${PROJECT_ROOT}/.canon-ledger/tmp/extraction_result.json"
```

自动判定：`blocking_count>0`、存在 `pending[].blocking=true`，或 `fulfillment.enforcement=strict` 且 `missed_nodes` 非空 → rejected。普通 pending 和 advisory missed_nodes 不阻断；pending 的候选事件在人工确认前不会进入 canon。

chapter-commit 后如有普通 pending，列出人工队列：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  human-review list --chapter {chapter_num}
```

裁决交给交互式确认完成：备份后若队列仍有 pending，本轮必须立刻按 `/canon-ledger-confirm {chapter_num}` 逐条 AskQuestion，不得把确认留到以后，也不得把「写下一章」列为下一步。提交与备份可以先做完（歧义未入正史）。裁决绑定当前正文哈希，正文变化后旧裁决不会被复用。

#### 5.3 验证投影

projection_status 五项（state/index/summary/memory/vector）全部 done 或 skipped。

chapter_status 由 projection writer 自动推进：accepted→committed，rejected→rejected。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage postcommit --format json
```

#### 5.4 失败隔离

commit 未生成→重跑 5.2。projection 失败→只补跑 projection，不回退 Step 1-4。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  projections retry --chapter {chapter_num} --format json
```

### Step 6：Git 备份

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" backup \
  --chapter {chapter_num} \
  --chapter-title "{title}" \
  --require-accepted-binding
```

备份必须以解析后的 `PROJECT_ROOT` 为准，禁止从工作区父目录执行裸全量 Git add，避免把书项目仓库作为父仓库的嵌入仓库/submodule 加入。

备份后再列一次人工队列。若 `pending` 非空：立刻 `Read` `${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-confirm/SKILL.md`，从该 skill 的 Step 2 执行（环境与项目根已就绪，不必重复引导），当场逐条 `AskQuestion`。队列为空才进入最终报告的「写下一章」。

## 作者友好过程提示与恢复契约

开始写章前先用作者语言说明本次目标、主要阶段和是否需要守在旁边，不承诺固定耗时。过程提示只说当前在做什么和会产生什么，不直接输出原始 JSON、traceback 或长命令日志；技术详情写入 `.canon-ledger/logs/run_last.log`：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" run-log \
  --event write-start \
  --payload-json "{\"chapter\": {chapter_num}, \"mode\": \"{mode}\"}" \
  --format text
```

写章过程节点（最多 6 个）：

1. 检查项目环境：确认项目、占位符和本章要求可用。
2. 整理写作依据：读取章纲、最近剧情和未回收伏笔。
3. 起草正文：根据写作任务书生成本章正文。
4. 写作检查：确定穿帮才定点修补，拿不准的判断转人工，不改文风或剧情选择。
5. 保存本章故事事实：提取本章目标完成情况、歧义和新事实。
6. 提交备份：把本章事实入账、更新故事资料并备份；有待确认项则当场确认。

重复执行同一章时，先读取可信断点：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" run-ledger write-resume \
  --chapter {chapter_num} \
  --mode "{mode}" \
  --format json
```

`run-ledger write-resume` 只给续跑建议，不自动覆盖文件。它会根据正文、审查结果、data artifacts、commit、projection 和备份状态判断从哪里继续。正文被手动改过、章纲更新晚于正文、本章已 accepted 又重跑时，必须停下用有限选项询问：沿用当前正文 / 重新起草 / 只查看状态；不得覆盖作者手改。

每个关键步骤完成后记录 `run-ledger record-write-step`，至少记录 step、status、输入/输出文件路径、problems、auto_handled 和 duration_ms，供下一次续跑和最终报告使用。

少打扰确认策略：确定无误的事实不反复询问；有 pending 或审查疑点时必须当场确认，不得把「继续写下一章」当作并列选项。无待确认项才进入下一章。只有创作方向、显式 retcon、文件覆盖风险或 blocking issue 无法定点处理时才另开询问。

卡住时必须说明卡点、已完成内容和恢复建议：例如“正文和审查报告已保留，保存本章故事事实失败；重新运行 `/canon-ledger-write {chapter_num}` 会从 data-agent 继续”。不可恢复故障才在最终报告提示 `.canon-ledger/logs/run_last.log`；平时只保留日志，不打扰作者。

收尾必须调用作者报告 helper，优先以 helper 输出组织最终回复：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage write \
  --chapter {chapter_num} \
  --format text
```

## 充分性闸门

1. 正文文件存在且非空
2. 审查已落库（`--minimal` 除外）
3. blocking=true 必须在 Step 3 定点修复或经用户裁决
4. 普通 manual_checks / pending 已显式报告，候选事实未混入 canon
5. 未把插件写法教程写进正文要求
6. accepted CHAPTER_COMMIT，projection 五项 done/skipped
7. chapter_status=committed（projection 自动推进）
8. `write-gate` 的 prewrite / precommit / postcommit 均通过

## 失败恢复

审查缺失→重跑 Step 3。摘要/状态/记忆缺失→重跑 Step 5。事实修补后设定仍冲突→回 Step 4 只改事实，再重跑 Step 5。不要用润色重写来「修复」文风。

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
- 正文文件路径。
- 审查报告路径。
- `.canon-ledger/tmp/review_results.json`。
- `.canon-ledger/tmp/fulfillment_result.json`。
- `.canon-ledger/tmp/disambiguation_result.json`。
- `.canon-ledger/tmp/extraction_result.json`。
- `.canon-ledger/human-review/queue/chapter_{NNNN}.json`（有无待确认项）。
- `.story-system/commits/chapter_{NNN}.commit.json`。
- state / index / summary / memory / vector 更新状态。
- 备份状态。
- 是否还有待确认项；仅当确认队列为空时才提示可写下一章。

状态规则：
- `chapter-commit rejected`、任一 `write-gate` failed、projection failed 时，最终状态不得写“已完成”。
- `--fast` 和 `--minimal` 的跳过项必须说明；`--minimal` 跳过审查时归入“已自动处理”或“建议确认”，不得假装已完成完整审查。
- projection retry 发生时必须说明已自动处理和最终结果。

异常分类：
- 已自动处理：projection retry 成功、RAG 临时降级但不影响结果、旧 no-review artifact 被本章新 artifact 覆盖。
- 建议确认：reviewer `manual_checks`、人工事实队列须当场确认。
- 必须处理：blocking issue 未裁决、data artifacts 缺失或 schema 不完整、commit rejected、projection failed。

下一步建议必须使用任务化语言 + 可复制命令。本章存在人工事实队列或审查疑点时，确认是唯一下一步，不得并列「写下一章」：

```text
- 有 {pending_count} 条候选事实等你确认，逐条裁决后系统会自动重新提交本章：
  /canon-ledger-confirm {chapter_num}
```

队列为空才建议写下一章：

```text
- 接下来可以写下一章：
  /canon-ledger-write {next_chapter}
```

不写 token 统计；如需排查故障，只给日志路径或建议运行 `/canon-ledger-doctor`。

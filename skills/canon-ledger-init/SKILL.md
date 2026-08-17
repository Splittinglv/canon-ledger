---
name: canon-ledger-init
description: 初始化长篇小说项目：分阶段收集书名、题材、主角与世界观，生成设定集、总纲和一致性合同。用户说开新书、初始化项目、/canon-ledger-init 时使用。
---

# Project Initialization (Deep Mode)

## 目标

- 结构化交互收集足够信息，避免"先生成再返工"。
- 产出可落地骨架：`.canon-ledger/state.json`、`设定集/*`、`大纲/总纲.md`、`.canon-ledger/idea_bank.json`、`.story-system/MASTER_SETTING.json`。
- 保证后续 `/canon-ledger-plan` 与 `/canon-ledger-write` 可直接运行。

## 执行原则

1. 先收集，再生成；未过充分性闸门，不执行 `canon_ledger.py init`。
2. 分波次提问，每轮只问"当前缺失且会阻塞下一步"的信息；用户已明确的不重复问，冲突让用户裁决。
3. 参考书拆解只返回结构化结果；用户确认前不得写入 `idea_bank.json`、`.story-system`、`设定集`、`大纲`、`正文`、`.canon-ledger/state.json` 或任何 canon/read model 文件。

## 引用加载策略

路径说明：`references/` 指 `skills/canon-ledger-init/references/`；`../../references/` 指共享 references。详细采集字段见 `references/init-collection-schema.md`（按需区段读，逐项收集，必填项以「充分性闸门」为准）。

| Step | Trigger | Reference |
|------|---------|-----------|
| Step 1 | always | `references/system-data-flow.md` |
| 角色卡顿 | 人物扁平 | `references/worldbuilding/character-design.md` |
| 世界观/力量 | 按需 | `references/worldbuilding/faction-systems.md`、`references/worldbuilding/power-systems.md`、`references/worldbuilding/world-rules.md`、`references/worldbuilding/setting-consistency.md` |
| 命名 | 开始命名 | `"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill init --table 命名规则 --query "{命名对象} {题材}" --genre {题材}` |

按需读取世界观设计指南。不加载卖点公式、反套路库或追读力配置。

## 工具策略

- `Read/Grep`：读项目上下文与参考文件。
- `Bash`：执行 `canon_ledger.py init`、文件存在性检查、最小验证。
- `Task`：拆分并行子任务；Step 1.5 用户选择参考书拆解作灵感来源时调用 `deconstruction-agent`。
- `AskQuestion`：关键分歧裁决、候选选择、最终确认。若 AskQuestion 不可用，在聊天里给出 2–3 个有限选项。
- `WebSearch`/`WebFetch`：仅在用户要求市场趋势/平台风向、创意约束需时间敏感依据、或题材信息明显不确定时使用，先 search 后 fetch 核验。

## 交互流程（Deep）

### Step 1：预检与上下文加载

环境设置（bash 命令执行前）：
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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-init"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" subagent-models --format json
```
拆书子代理模型可选。读取 JSON 里 `agents["deconstruction-agent"]`：`pass_to_task=true` 时，调用 Task 必须传入该 `model` slug；否则不要传 `model`。本轮用户点名优先于配置文件。新书尚未创建时，这条命令仍可用（回退到 `~/.cursor/canon-ledger/subagent-models.json`，最后回退 inherit）。

必须做：
- 确认当前目录可写；确认入口脚本 `${SCRIPTS_DIR}/canon_ledger.py` 存在（仅支持插件目录）。
- 初始化前不要用 `where` 把 `WORKSPACE_ROOT` 解析成书项目根；新项目尚不存在时没有可供定位的状态文件。
- 只打印工作区与脚本目录，确认生成目标将在工作区下的书名安全化子目录中。
- 加载最小参考：`references/system-data-flow.md`。插件不提供题材套路库或爽点模板；题材只作为分类标签，不带写法处方。

输出：进入 Deep 采集前的"已知信息清单"和"待收集清单"。

### Step 1.5：灵感来源询问（可选）

进入故事核采集前，必须先用 `AskQuestion` 或直接提问确认用户是否提供灵感来源。不要默认拆书，也不要把参考作品当作必填项。

建议询问：

```text
你这本书的灵感来源想从哪里开始？可以直接说原创想法，也可以提供参考作品做拆书提炼。若要拆书，请给参考书名+平台，并尽量提供章节摘录或文本路径；没有参考也可以直接跳过。
```

可接受来源：原创想法、参考作品拆书（书名/平台/章节摘录/文本路径）、市场趋势、已有脑洞片段。

当用户选择参考作品拆书且提供文本路径或章节摘录时，必须使用 `Task` 工具调用 `deconstruction-agent`，不得由 init 主流程口头替代拆解结果。

```text
使用 `Task` 工具调用插件 agent `deconstruction-agent`。如果 `Task` 无法指定具名插件 agent，则启动 `generalPurpose` 子代理：先读取 `${CANON_LEDGER_PLUGIN_ROOT}/agents/deconstruction-agent.md`，再执行其中规范。仅当 `subagent-models` 表明 `agents["deconstruction-agent"].pass_to_task` 为 `true` 时，才向 `Task` 传入 `model`。

Prompt: reference_title={reference_title}; reference_source={reference_source}; reference_text_path={reference_text_path}; reference_text_excerpt={reference_text_excerpt}; analysis_mode={quick|deep|auto}; init_goal={当前初始化故事方向或空}; target_genre={题材或空}。只返回 init_reference_research JSON 对象，不写任何文件，不创建目录，不写 .story-system、.canon-ledger、设定集、大纲、正文、idea_bank.json、state.json 或任何 canon/read model 文件。
```

调用后主流程必须记录一份 `SubagentRun` 汇总（仅供最终报告使用，不写入 canon）：

```json
{
  "name": "deconstruction-agent",
  "user_label": "参考作品拆解",
  "status": "completed | partial | failed | skipped",
  "problems": [],
  "auto_handled": [],
  "needs_user_action": false,
  "duration_ms": 0,
  "outputs": []
}
```

`quality.passed=false`、`confidence < 0.85`、输入不足、文本不可读、降级 quick mode 或输出不完整时，必须写入 `problems`，并让最终报告进入“建议确认 / 必须处理”。

处理规则：
- 只有书名/平台、无文本或摘录时，先问能否提供摘录/路径；不能提供则把参考书仅作"方向线索"，不得编造其黄金三章、角色、设定或剧情事实。
- 接收返回的 `init_reference_research` JSON 后，只使用 `reader_promise`、`opening_hook_patterns`、`cool_point_loops`、`protagonist_patterns`、`antagonist_pressure_patterns`、`pacing_notes`、`borrowable_structures`、`differentiation_requirements`、`init_candidates`、`quality`。
- 先检查 `quality`：`quality.passed=false`、`confidence < 0.85` 或 `warnings` 非空时，不得把候选折叠进创意约束包，只能把风险和需补充材料展示给用户确认。
- `do_not_copy` 和 `canon_contamination_warnings` 必须进入已知信息清单，作为后续创意生成红线。
- Step 2-6 只能使用用户确认过、并已变形为本书差异化表达的模式；禁止把参考书角色、设定、组织、地点、金手指、剧情事实原样写入生成项目文件。

### Step 2：故事核与商业定位

必收：书名、题材（支持 A+B 复合）、目标规模（总字数或总章数）、一句话故事、核心冲突、目标读者/平台。

canonical 题材集合（写入 `project_info.genre`）：都市、玄幻、仙侠、奇幻、科幻、历史、悬疑、游戏、古言、现言、幻言、年代、种田、快穿、衍生。

可自由输入细分题材、套路或形式；初始化脚本只把它们映射到中性的 canonical 标签，不会加载题材模板或把套路写入设定真源。插件不随包提供题材模板、套路库或爽点公式——题材只是分类标签。优先让用户自由描述再二次结构化确认；卡住时给 2-4 个候选方向。

### Step 3：角色骨架与关系事实

必收：主角姓名、主角欲望、主角缺陷（会害他付代价）、主角结构（单/多主角）、感情线配置（无/单女主/多女主）。对立角色、组织、环境或规则压力均为可选；只收用户已经确定的名称、目标和与主角的事实关系，不默认补小/中/大层级。主角原型标签、多主角分工同样可选。

### Step 4：金手指与兑现机制

必收：金手指类型仅当用户要金手指时收集（可为"无金手指"）。未提金手指则跳过，不阻断。若收集：名称/系统名可空、可见度、不可逆代价（必须有代价或明确"无+理由"）。
条件必收：系统流给系统性格+升级节奏；重生给重生时间点+记忆完整度；传承/器灵给辅助边界+出手限制。

### Step 5：世界观与力量规则

必收：世界规模（单城/多域/大陆/多界）、力量体系类型、势力格局、社会阶层与资源分配。
题材相关：货币体系与兑换规则、宗门/组织层级、境界链与小境界。

### Step 6：创意约束包（可选）

仅当用户明确要求差异化卖点、反套路或市场定位时，用对话整理，不加载插件内套路库。未要求则跳过，不阻断初始化。

流程：
1. 汇总 Step 1.5 已确认的灵感来源：原创想法、参考拆书结果、市场趋势或用户自己的约束。
2. 用对话生成 2-3 套差异化表达，每套含：一句话卖点、硬约束 2-3 条、主角缺陷驱动一句话、反派镜像一句话。
3. 用户选择最终方案，或拒绝并给出原因。

备注：
- 若用户要求"贴近当下市场"，可触发外部检索并标注时间戳。
- 若使用了参考拆解，展示候选时必须标明参考来源、转换方式、不可复制项和差异化要求；用户未明确确认前，不写入 `idea_bank.json` 或任何生成项目文件。

### Step 7：一致性复述与最终确认

必须输出"初始化摘要草案"并让用户确认：故事核（题材/一句话故事/核心冲突）、主角核（欲望/缺陷）、世界核（规模/力量/势力）。金手指核、创意约束核有则写，没有标「无」。

确认规则：用户未明确确认，不执行生成；用户仅改局部，回到对应 Step 最小重采集。

## 充分性闸门（必须通过）

未满足以下条件前，禁止执行 `canon_ledger.py init`：

1. 书名、题材（可复合）已确定。
2. 目标规模可计算（字数或章数至少一个）。
3. 主角姓名 + 欲望 + 缺陷完整。
4. 世界规模 + 力量体系类型完整。
5. 金手指、反套路、卖点公式均非必填。

## 项目目录安全规则（必须）

- `project_root` 必须由书名安全化生成：`PROJECT_ROOT="${WORKSPACE_ROOT}/${PROJECT_SLUG}"`；安全化结果为空或以 `.` 开头时自动前缀 `proj-`。
- 禁止在插件目录（`${CANON_LEDGER_PLUGIN_ROOT}`）下生成项目文件；禁止直接把 `WORKSPACE_ROOT` 当作 `PROJECT_ROOT`，除非用户明确指定当前目录就是书项目根。
- 初始化前必须展示并确认 `WORKSPACE_ROOT`、`PROJECT_SLUG`、`PROJECT_ROOT`。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
PROJECT_SLUG="$("${CANON_LEDGER_PYTHON}" -X utf8 -c "import re,sys; title=sys.argv[1].strip(); slug=re.sub(r'[\\\\/:*?\"<>|]+','',title); slug=re.sub(r'\\s+','-',slug).strip('-'); print(('proj-' + slug) if (not slug or slug.startswith('.')) else slug)" "{title}")"
PROJECT_ROOT="${WORKSPACE_ROOT}/${PROJECT_SLUG}"
echo "WORKSPACE_ROOT=${WORKSPACE_ROOT}"
echo "PROJECT_SLUG=${PROJECT_SLUG}"
echo "PROJECT_ROOT=${PROJECT_ROOT}"
```

## 执行生成

### 1) 运行初始化脚本

参数全部来自上面的采集对象（书名/题材/主角/金手指/世界观/反派/创意约束等），逐字段映射为 `canon_ledger.py init` 的 `--*` 选项；完整字段清单见 `references/init-collection-schema.md`，可用 `"${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" init --help` 核对选项名。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" init \
  "${PROJECT_ROOT}" "{title}" "{genre}" \
  --protagonist-name "{protagonist_name}" \
  --target-words {target_words} --target-chapters {target_chapters} \
  --protagonist-desire "{protagonist_desire}" --protagonist-flaw "{protagonist_flaw}" \
  --golden-finger-type "{gf_type}" --gf-irreversible-cost "{gf_irreversible_cost}" \
  --world-scale "{world_scale}" --power-system-type "{power_system_type}" \
  --core-selling-points "{core_points}"
  # 其余字段（结构/感情线/反派/势力/货币/境界/原型/读者/平台等）按采集对象继续追加对应 --* 选项
```

### 2) 写入 `idea_bank.json`

写入 `.canon-ledger/idea_bank.json`，内容必须与最终选定方案一致：

```json
{
  "selected_idea": {"title": "", "one_liner": "", "anti_trope": "", "hard_constraints": []},
  "constraints_inherited": {"anti_trope": "", "hard_constraints": [], "protagonist_flaw": "", "antagonist_mirror": "", "opening_hook": ""}
}
```

### 3) Patch 总纲

`大纲/总纲.md` 必须补齐：故事一句话、主线目标、已确定的故事边界。暗线、对立来源和其他创作结构只在用户明确提供时记录，不写不算失败。

### 4) 生成写前合同树（Story System 初始化）

init 完成后立即生成 MASTER_SETTING，让后续 plan 有调性/禁忌参照。此处不传 `--chapter`（只生成 `MASTER_SETTING.json` 和 `anti_patterns.json`），也不传 `--emit-runtime-contracts`（还没有卷/章级数据）；plan 拆到具体章节时再生成 volume/chapter/review 合同。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
GENRE="$("${CANON_LEDGER_PYTHON}" -X utf8 -c "import json,os; root=os.environ['PROJECT_ROOT']; s=json.load(open(root + '/.canon-ledger/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  story-system "${GENRE}" --genre "${GENRE}" --persist --format json
```

## 验证与交付

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
test -f "${PROJECT_ROOT}/.canon-ledger/state.json"
find "${PROJECT_ROOT}/设定集" -maxdepth 1 -type f -name "*.md"
test -f "${PROJECT_ROOT}/大纲/总纲.md"
test -f "${PROJECT_ROOT}/.canon-ledger/idea_bank.json"
test -f "${PROJECT_ROOT}/.story-system/MASTER_SETTING.json"
test "$(basename "${PROJECT_ROOT}")" = "${PROJECT_SLUG}"
```

成功标准：
- `state.json` 存在且 title/genre/target_words/target_chapters 不为空。
- 设定集核心文件存在：`世界观.md`、`力量体系.md`、`主角卡.md`；单主角不生成 `主角组.md`，`heroine_config=无女主` 不生成 `女主卡.md`。
- 默认不生成 `金手指设计.md`、`复合题材-融合逻辑.md`、`爽点规划.md` 或空目录；这些以主角卡、世界观、卷纲为事实源。
- `总纲.md` 已填核心主线与约束字段；`idea_bank.json` 已写入且与最终选定方案一致。
- `.story-system/MASTER_SETTING.json` 存在且 `route.primary_genre` 非空。

## 失败处理（最小回滚）

触发：关键文件缺失；总纲关键字段缺失；约束启用但 `idea_bank.json` 缺失或不一致。

恢复：只补缺失字段，不全量重问；只重跑最小步骤（文件缺失→重跑 `canon_ledger.py init`；总纲缺字段→只 patch 总纲；idea_bank 不一致→只重写该文件）；重新验证，全部通过后结束。

## 作者友好过程提示与恢复契约

初始化开始前先说明本次会经历：收集故事核心 -> 确认创意约束 -> 生成项目骨架 -> 写入初始故事档案 -> 验证能否进入规划。过程提示用作者语言，不直接输出原始 JSON、traceback 或长命令日志；技术详情写入 `.canon-ledger/logs/run_last.log`：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" run-log \
  --event init-progress \
  --payload-json "{\"stage\": \"init\"}" \
  --format text
```

过程提示每次不超过两行，只说当前动作和影响，例如“正在生成项目骨架：会创建设定集、总纲和初始故事档案”。少打扰确认策略：默认继续收集和生成；只有核心设定、参考拆解采用、项目目录安全、写入 canon 前的最终方案需要用户拍板。

需要用户裁决时使用有限选项，并说明每个选项影响；例如保留当前设定 / 修改局部 / 暂停初始化。卡住时必须说明卡点、已完成内容和恢复建议，例如“设定集已生成，Story System 初始档案缺失；重新运行 `/canon-ledger-init` 会只补缺失文件”。

不可恢复故障才在最终报告提示 `.canon-ledger/logs/run_last.log`；平时只保留日志，不打扰作者。收尾必须调用作者报告 helper：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage init \
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
- 项目目录、`.canon-ledger/state.json`、`.canon-ledger/idea_bank.json`。
- `设定集/世界观.md`、`设定集/力量体系.md`、`设定集/主角卡.md`、`设定集/反派设计.md`。
- `大纲/总纲.md`、`.story-system/MASTER_SETTING.json`。
- 是否使用参考作品拆解；用户确认前未写入 canon 的情况。
- 缺失信息是否影响后续 `/canon-ledger-plan`。

异常分类：
- 已自动处理：脚本补齐目录、重跑最小初始化步骤、重新生成缺失的非内容文件等。
- 建议确认：参考拆解质量略低、候选创意需用户再看一眼。
- 必须处理：核心设定未确认、项目目录不安全、关键文件仍缺失。

下一步建议必须使用任务化语言 + 可复制命令，例如：

```text
- 接下来可以规划第一卷：
  /canon-ledger-plan 1
```

不写 token 统计；如需排查故障，只给日志路径或建议运行 `/canon-ledger-doctor`。

---
name: canon-ledger-query
description: 查询书项目中的角色、伏笔、力量体系、势力与运行时状态。用户询问设定、角色名、伏笔或使用 /canon-ledger-query 时使用。
---

# Information Query Skill

## Use when

用户询问关于故事设定、角色、力量体系、势力、伏笔、金手指、时间线等项目内信息时触发。

## 项目根保护

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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-query"
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
```
- `PROJECT_ROOT` 必须包含 `.canon-ledger/state.json`
- **禁止**在 `${CANON_LEDGER_PLUGIN_ROOT}/` 下读取或写入项目文件

## 查询分类 → 最窄工具

先识别查询类型，再用下表最窄工具。不默认全量加载，只在综合 / 跨多类型查询时用 `memory-contract load-context`。

| 查询类型 | 关键词 | 最窄工具 |
|---------|--------|---------|
| 角色历史状态 | 某角色在第N章时 / 时间点状态 / 境界变化 | `knowledge query-entity-state` |
| 实体关系 | 关系 / 敌友 / 师徒 / 阵营归属 | `knowledge query-relationships` |
| 世界规则 | 力量规则 / 设定铁律 / 境界体系约束 | `memory-contract query-rules` |
| 伏笔 / open loop | 伏笔 / 紧急伏笔 / 未闭合悬念 | `memory-contract get-open-loops` |
| 综合 / 复杂 | 跨多类型、需要时间线 + 长期记忆联合 | `memory-contract load-context` |
| 静态设定 | 角色卡 / 力量体系 / 世界观 / 势力 / 标签格式 | `Grep` + `Read` 设定集 |

## 引用加载策略

按查询类型按需加载，先识别再加载。路径说明：`references/` 指 skill 私有 `skills/canon-ledger-query/references/`；`../../references/` 指共享 references。

| 查询类型 | Reference | 实际路径 |
|---------|-----------|---------|
| 数据流 / 优先级 | 数据流规范 | `${SKILL_ROOT}/references/system-data-flow.md` |
| 伏笔分析 | 伏笔分析 | `${SKILL_ROOT}/references/advanced/foreshadowing.md` |
| 格式查询 | 标签规范 | `${SKILL_ROOT}/references/tag-specification.md` |

不得同时加载两个以上 reference，除非用户请求明确跨多类型。

## 查询流程

1. **识别查询类型**：按「查询分类 → 最窄工具」表匹配关键词。
2. **按优先级定位写前真源**（写前真源 → 写后真源 → 投影层）：
   1. `.story-system/MASTER_SETTING.json` - 全书合同：题材路由、`setting_canon` 设定快照、`initial_canon` 初始化角色事实。写前清洗后 `master_constraints` 为空，文风不在合同里
   2. `.story-system/volumes/*.json` - 卷级合同：有效事实在 `volume_goal`（卷名、摘要、本卷目标、预期结束状态、核心冲突、章节范围）。`selected_pacing`、`selected_tropes`、`selected_scenes` 写前清洗后为空，不是合同事实
   3. `.story-system/chapters/*.json` - 章级合同：权威在 `chapter_directive`（`goal`、`must_cover_nodes`、`forbidden_zones`、时间锚点等章纲事实）。`override_allowed.chapter_focus` 只是 `goal` 的别名；`dynamic_context` 固定为空，不是检索到的写法材料
   4. latest accepted `.story-system/commits/chapter_XXX.commit.json` - 写后事实（已发布章节的定稿状态）
   5. `memory-contract` 系列查询 - 记忆编排结果（长期记忆、伏笔、时间线）
   6. `.canon-ledger/state.json` / `index.db` - 只读投影层（角色卡、章节列表）

   **优先级说明**：
   - 写前真源（1-3）：作者开写前必须遵守的"大纲、设定、禁区"
   - 写后真源（4）：已发布章节的"定稿状态"，不可篡改
   - 投影层（5-6）：从写后真源自动生成的"查询视图"，方便快速检索

3. **调用最窄工具检索**：按类型只调用所需命令，不默认全量 `load-context`。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
# 角色历史状态：某实体在指定章节时的状态
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" knowledge query-entity-state --entity "{entity_id}" --at-chapter {N}

# 实体关系：某实体在指定章节时的所有关系
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" knowledge query-relationships --entity "{entity_id}" --at-chapter {N}

# 世界规则
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" memory-contract query-rules --chapter {chapter_num}

# 伏笔 / open loop
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" memory-contract get-open-loops

# 仅综合 / 复杂查询：需要时间线 + 长期记忆联合时才用
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" memory-contract load-context --chapter {chapter_num}
```

   静态设定（角色卡 / 力量体系 / 世界观 / 标签格式）直接用 `Grep` 定位行号再 `Read` 取片段，不经 memory-contract。

4. **格式化输出**：按下方模板输出。

## 输出格式

```markdown
# 查询结果：{关键词}

## 概要
- **匹配类型**: {type}
- **数据源**: {实际命中的真源 / 投影层}
- **匹配数量**: X 条

## 详细信息
{结构化数据，含文件路径和行号}

## 数据一致性检查
{state.json 与静态文件的差异，若无差异则省略}
```

## 边界与失败恢复

- 只读操作，不修改任何项目文件
- 若数据源缺失，明确告知用户缺少什么文件
- 若查询无匹配，返回空结果并建议检查范围
- 若 `.story-system/` 合同缺失或损坏，必须阻断并提示先修复合同；不得降级为不完整事实查询

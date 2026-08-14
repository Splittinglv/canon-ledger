---
name: canon-ledger-query
description: 查询书项目中的角色、伏笔、力量体系、势力与运行时状态。用户询问设定、角色名、伏笔或使用 /canon-ledger-query 时使用。
---

# Information Query Skill

## Use when

用户询问关于故事设定、角色、力量体系、势力、伏笔、金手指、节奏等项目内信息时触发。

## 项目根保护

```bash
# 这段引导仅适用于 POSIX shell（sh/bash/zsh）；Windows 请使用 Git Bash 或 WSL。
# 缓存安装必须使用 Cursor 注入的插件根；不扫描缓存目录寻找可执行脚本。
_PLUGIN_ROOT_HINT="${CANON_LEDGER_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
if [ -z "$_PLUGIN_ROOT_HINT" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/canon-ledger"
fi
_EXPORTER="$(python3 -X utf8 -c '
import json, sys
from pathlib import Path
try:
    root = Path(sys.argv[1]).expanduser().resolve()
    manifest = json.loads((root / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    exporter = (root / "scripts" / "export_cursor_env.py").resolve()
except (OSError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if manifest.get("name") != "canon-ledger":
    raise SystemExit(1)
if exporter.parent.parent != root or not exporter.is_file() or not (root / "scripts" / "canon_ledger.py").is_file():
    raise SystemExit(1)
print(exporter)
' "$_PLUGIN_ROOT_HINT")" || {
  echo "ERROR: 插件根不可信或安装不完整。请使用 Cursor 注入的插件根，或安装到 ~/.cursor/plugins/local/canon-ledger" >&2
  exit 1
}
_ENV_JSON="$(python3 -X utf8 "$_EXPORTER" --format json)" || exit 1
_ENV_LINES="$(printf '%s' "$_ENV_JSON" | python3 -X utf8 -c '
import json, sys
from pathlib import Path
keys = (
    "CANON_LEDGER_PLUGIN_ROOT", "CURSOR_PLUGIN_ROOT",
    "SCRIPTS_DIR", "WORKSPACE_ROOT", "CURSOR_PROJECT_DIR",
)
try:
    payload = json.load(sys.stdin)
    environment = payload["environment"]
    python_executable = payload["python_executable"]
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if payload.get("schema_version") != "canon-ledger-cursor-env/v1" or not isinstance(environment, dict):
    raise SystemExit(1)
if set(environment) != set(keys):
    raise SystemExit(1)
values = [environment[key] for key in keys]
if any(not isinstance(value, str) or not value or any(char in value for char in "\x00\r\n") for value in values):
    raise SystemExit(1)
if (
    not isinstance(python_executable, str)
    or not python_executable
    or any(char in python_executable for char in "\x00\r\n")
    or not Path(python_executable).is_absolute()
    or not Path(python_executable).is_file()
):
    raise SystemExit(1)
if (
    values[1] != values[0]
    or values[2] != str(Path(values[0]) / "scripts")
    or values[4] != values[3]
):
    raise SystemExit(1)
sys.stdout.write("\n".join([*values, python_executable]) + "\n")
')" || {
  echo "ERROR: export_cursor_env.py 返回了无效环境协议" >&2
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
unset _PLUGIN_ROOT_HINT _EXPORTER _ENV_JSON _ENV_LINES _ENV_PARSE_OK
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
   1. `.story-system/MASTER_SETTING.json` - 全书主设定（题材、调性、核心禁忌）
   2. `.story-system/volumes/*.json` - 卷级合同（本卷目标、节奏策略）
   3. `.story-system/chapters/*.json` - 章级合同（本章焦点、动态上下文）
   4. latest accepted `.story-system/commits/chapter_XXX.commit.json` - 写后事实（已发布章节的定稿状态）
   5. `memory-contract` 系列查询 - 记忆编排结果（长期记忆、伏笔、时间线）
   6. `.canon-ledger/state.json` / `index.db` - 只读投影层（角色卡、章节列表）

   **优先级说明**：
   - 写前真源（1-3）：作者开写前必须遵守的"大纲、设定、禁区"
   - 写后真源（4）：已发布章节的"定稿状态"，不可篡改
   - 投影层（5-6）：从写后真源自动生成的"查询视图"，方便快速检索

3. **调用最窄工具检索**：按类型只调用所需命令，不默认全量 `load-context`。

```bash
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

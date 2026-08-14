---
name: canon-ledger-learn
description: 把当前书里好用的写法沉淀进项目长期记忆。用户说记住这个写法、沉淀经验或 /canon-ledger-learn 时使用。
---

# /canon-ledger-learn

## Project Root Guard（必须先确认）

- 必须在项目根目录执行（需存在 `.canon-ledger/state.json`）
- 用统一入口解析项目根，避免写错目录：

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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-learn"
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
```

## 目标

提取可复用的跨章事实处理方式（伏笔回收、时间线衔接、设定执行、人物一致性），通过统一命令写成 `.canon-ledger/memory_scratchpad.json` 中可被默认上下文实际读取的结构化规则。口吻、句式、文笔偏好请写进 `设定集/文风提示词.md`，不要当作项目记忆。

## 执行流程

1. 读取 `"$PROJECT_ROOT/.canon-ledger/state.json"` 的 `progress.current_chapter` 作为当前章节号；缺失时由统一命令按全局规则处理。
2. 解析用户输入（`/canon-ledger-learn` 后的经验文本；为空则取本次对话中用户明确认可的事实处理方式），归类 `pattern_type`（仅允许 `foreshadow` / `timeline` / `setting` / `character`）。无法归类就停止并请用户明确，不得塞进 `other`。对话、口吻、节奏、桥段和文笔内容改写入文风提示词，不进长期一致性记忆。
3. 调用 `project-memory add-pattern` 写入，不得手写或拼接 JSON：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" project-memory add-pattern \
  --pattern-type "{pattern_type}" \
  --description "{用户输入或提炼后的完整描述}" \
  --category "{分类，可空}" \
  --importance "{high|medium|low}"
```

## 约束

- 不删除旧规则，仅追加；同一 `pattern_type` + `description` 会稳定去重。
- 命令会拒绝文风、文笔、句式、桥段和模型控制文本；不得为了通过校验改写用户原意。
- 禁止使用 `Write` 或手工编辑 `.canon-ledger/memory_scratchpad.json`。

## 成功标准

- `memory_scratchpad.json` 中存在对应的 active `world_rule`，下一章 `memory-contract load-context` 能读到它。
- 输出包含 `status: success` 和完整 `learned` 对象；重复规则返回 `status: skipped`。

## 失败恢复

| 故障 | 恢复方式 |
|------|---------|
| 旧 `project_memory.json` 存在 | 不再把其中自由文本注入写作上下文；需要的事实由用户重新确认后写入结构化规则 |
| 长期记忆 JSON 损坏 | fail closed，不写入并建议运行 `/canon-ledger-doctor` |
| 输入属于文风或无法归类 | 停止；文风写入 `设定集/文风提示词.md`，事实请用户明确类型 |

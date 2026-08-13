---
name: webnovel-learn
description: 把当前书里好用的写法沉淀进项目长期记忆。用户说记住这个写法、沉淀经验或 /webnovel-learn 时使用。
---

# /webnovel-learn

## Project Root Guard（必须先确认）

- 必须在项目根目录执行（需存在 `.webnovel/state.json`）
- 用统一入口解析项目根，避免写错目录：

```bash
# 这段引导仅适用于 POSIX shell（sh/bash/zsh）；Windows 请使用 Git Bash 或 WSL。
# 缓存安装必须使用 Cursor 注入的插件根；不扫描缓存目录寻找可执行脚本。
_PLUGIN_ROOT_HINT="${WEBNOVEL_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}}"
if [ -z "$_PLUGIN_ROOT_HINT" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/webnovel-writer"
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
if manifest.get("name") != "webnovel-writer":
    raise SystemExit(1)
if exporter.parent.parent != root or not exporter.is_file() or not (root / "scripts" / "webnovel.py").is_file():
    raise SystemExit(1)
print(exporter)
' "$_PLUGIN_ROOT_HINT")" || {
  echo "ERROR: 插件根不可信或安装不完整。请使用 Cursor 注入的插件根，或安装到 ~/.cursor/plugins/local/webnovel-writer" >&2
  exit 1
}
_ENV_JSON="$(python3 -X utf8 "$_EXPORTER" --format json)" || exit 1
_ENV_LINES="$(printf '%s' "$_ENV_JSON" | python3 -X utf8 -c '
import json, sys
from pathlib import Path
keys = (
    "WEBNOVEL_PLUGIN_ROOT", "CURSOR_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT",
    "SCRIPTS_DIR", "WORKSPACE_ROOT", "CURSOR_PROJECT_DIR",
)
try:
    payload = json.load(sys.stdin)
    environment = payload["environment"]
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if payload.get("schema_version") != "webnovel-cursor-env/v1" or not isinstance(environment, dict):
    raise SystemExit(1)
if set(environment) != set(keys):
    raise SystemExit(1)
values = [environment[key] for key in keys]
if any(not isinstance(value, str) or not value or any(char in value for char in "\x00\r\n") for value in values):
    raise SystemExit(1)
if values[1] != values[0] or values[2] != values[0] or values[3] != str(Path(values[0]) / "scripts") or values[5] != values[4]:
    raise SystemExit(1)
sys.stdout.write("\n".join(values) + "\n")
')" || {
  echo "ERROR: export_cursor_env.py 返回了无效环境协议" >&2
  exit 1
}
_ENV_PARSE_OK=1
{
  IFS= read -r WEBNOVEL_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CLAUDE_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r SCRIPTS_DIR || _ENV_PARSE_OK=0
  IFS= read -r WORKSPACE_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PROJECT_DIR || _ENV_PARSE_OK=0
} <<EOF
$_ENV_LINES
EOF
if [ "$_ENV_PARSE_OK" -ne 1 ]; then
  echo "ERROR: 无法解析插件环境协议" >&2
  exit 1
fi
export WEBNOVEL_PLUGIN_ROOT CURSOR_PLUGIN_ROOT CLAUDE_PLUGIN_ROOT SCRIPTS_DIR WORKSPACE_ROOT CURSOR_PROJECT_DIR
unset _PLUGIN_ROOT_HINT _EXPORTER _ENV_JSON _ENV_LINES _ENV_PARSE_OK
export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/webnovel-learn"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
```

## 目标

提取可复用的跨章事实处理方式（伏笔回收、时间线衔接、设定执行），追加到 `.webnovel/project_memory.json`。口吻、句式、文笔偏好请写进 `设定集/文风提示词.md`，不要当作项目记忆。

## 执行流程

1. 读取 `"$PROJECT_ROOT/.webnovel/state.json"` 的 `progress.current_chapter` 作为当前章节号；缺失则用 `source_chapter: null`，不阻断。
2. 解析用户输入（`/webnovel-learn` 后的经验文本；为空则取本次对话中用户认可的写法），归类 `pattern_type`（foreshadow/timeline/setting/character/other，无法归类用 `other`）。对话/口吻/节奏类内容改写入文风提示词，不进 project_memory。
3. 调用 `project-memory add-pattern` 写入，不得手写或拼接 JSON：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" project-memory add-pattern \
  --pattern-type "{pattern_type}" \
  --description "{用户输入或提炼后的完整描述}" \
  --category "{分类，可空}" \
  --importance "{high|medium|low}"
```

## 约束

- 不删除旧记录，仅追加。
- 追加前扫描已有 `patterns`；`pattern_type` + `description` 完全相同则跳过并告知用户，部分相似不去重。
- 禁止使用 `Write` 或手工编辑 `.webnovel/project_memory.json`。

## 成功标准

- `project_memory.json` 存在且格式合法，新 pattern 已追加到 `patterns` 数组。
- 输出包含 `status: success` 和完整 `learned` 对象。

## 失败恢复

| 故障 | 恢复方式 |
|------|---------|
| `project_memory.json` 不存在 | 脚本自动初始化 `{"patterns": []}` 后继续 |
| JSON 解析失败 | 不写入脏数据，告知用户文件损坏并建议手动修复 |
| `state.json` 缺失无法取章节号 | 用 `source_chapter: null`，不阻断 |

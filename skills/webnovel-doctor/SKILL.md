---
name: webnovel-doctor
description: 只读体检网文项目的目录、数据库、RAG、依赖和 Dashboard 产物。用户说项目体检、诊断或 /webnovel-doctor 时使用。
---

# Webnovel Doctor

## 目标

只读诊断当前书项目：确认所处阶段应有的目录、文件、JSON、SQLite、RAG 配置、Python 依赖与 Dashboard 构建产物是否完整。

## 原则

1. 只读诊断：不写项目文件、不自动修复、不安装依赖、不启动 Dashboard。
2. 先 `project-status` 取短状态，再 `doctor` 做阶段感知检查。
3. 统一用 `"${WEBNOVEL_PYTHON}" -X utf8`，避免中文路径编码问题。
4. 缺失项按 runtime 推导的阶段解释影响与修复建议，不把 init 刚结束的项目按已写多章项目检查。

## 执行

准备路径：

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
    python_executable = payload["python_executable"]
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if payload.get("schema_version") != "webnovel-cursor-env/v1" or not isinstance(environment, dict):
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
if values[1] != values[0] or values[2] != values[0] or values[3] != str(Path(values[0]) / "scripts") or values[5] != values[4]:
    raise SystemExit(1)
sys.stdout.write("\n".join([*values, python_executable]) + "\n")
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
  IFS= read -r WEBNOVEL_PYTHON || _ENV_PARSE_OK=0
} <<EOF
$_ENV_LINES
EOF
if [ "$_ENV_PARSE_OK" -ne 1 ]; then
  echo "ERROR: 无法解析插件环境协议" >&2
  exit 1
fi
export WEBNOVEL_PLUGIN_ROOT CURSOR_PLUGIN_ROOT CLAUDE_PLUGIN_ROOT SCRIPTS_DIR WORKSPACE_ROOT CURSOR_PROJECT_DIR WEBNOVEL_PYTHON
unset _PLUGIN_ROOT_HINT _EXPORTER _ENV_JSON _ENV_LINES _ENV_PARSE_OK
export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/webnovel-doctor"
```

短状态：

```bash
"${WEBNOVEL_PYTHON}" -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" project-status --format summary
```

标准体检：

```bash
"${WEBNOVEL_PYTHON}" -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" doctor --format text
```

指定章节加 `--chapter {chapter_num}`，深度体检加 `--deep`。

## 输出方式

汇报包含：当前 `phase` 与 `target_chapter`、是否有 blocker、缺失或异常文件路径、RAG / Python / Dashboard 配置是否缺失、每个问题的影响和建议修复动作。

不执行真实修复，不展示或要求粘贴 API key。

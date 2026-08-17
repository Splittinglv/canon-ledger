---
name: canon-ledger-doctor
description: 只读体检当前书项目的目录、数据库、RAG、依赖和 Dashboard 产物。用户说项目体检、诊断或 /canon-ledger-doctor 时使用。
---

# CanonLedger Doctor

## 目标

只读诊断当前书项目：确认所处阶段应有的目录、文件、JSON、SQLite、RAG 配置、Python 依赖与 Dashboard 构建产物是否完整。

## 原则

1. 只读诊断：不写项目文件、不自动修复、不安装依赖、不启动 Dashboard。
2. 先 `project-status` 取短状态，再 `doctor` 做阶段感知检查。
3. 统一用 `"${CANON_LEDGER_PYTHON}" -X utf8`，避免中文路径编码问题。
4. 缺失项按 runtime 推导的阶段解释影响与修复建议，不把 init 刚结束的项目按已写多章项目检查。

## 执行

准备路径：

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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-doctor"
```
短状态：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" project-status --format summary
```

标准体检：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" doctor --format text
```

指定章节加 `--chapter {chapter_num}`，深度体检加 `--deep`。

## 输出方式

汇报包含：当前 `phase` 与 `target_chapter`、是否有 blocker、缺失或异常文件路径、RAG / Python / Dashboard 配置是否缺失、每个问题的影响和建议修复动作。

不执行真实修复，不展示或要求粘贴 API key。

---
name: canon-ledger-dashboard
description: 启动只读可视化面板，浏览项目状态、实体图谱、章节与伏笔。用户说打开仪表盘、dashboard 或 /canon-ledger-dashboard 时使用。
---

# CanonLedger Dashboard

## 目标

- 在本地启动只读 Web 面板，查看创作进度、设定词典、关系图谱、章节内容与伏笔。
- 暴露 Story Runtime 主链状态：`/api/story-runtime/health`、latest commit、fallback 情况。
- 可监听 `.canon-ledger/` 变化，但不修改任何项目文件。

## 执行流程

### Step 1：确认环境与模块目录

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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-dashboard"
export DASHBOARD_DIR="${CANON_LEDGER_PLUGIN_ROOT}/dashboard"
if [ ! -d "${DASHBOARD_DIR}" ]; then
  echo "ERROR: 未找到 dashboard 模块: ${DASHBOARD_DIR}" >&2
  exit 1
fi
```
### Step 2：解析项目根目录

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
echo "项目路径: ${PROJECT_ROOT}"
```

`PROJECT_ROOT` 必须解析成功。

### Step 3：校验前端产物与依赖

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="${CANON_LEDGER_PLUGIN_ROOT}:${PYTHONPATH}"
else
  export PYTHONPATH="${CANON_LEDGER_PLUGIN_ROOT}"
fi

if [ ! -f "${DASHBOARD_DIR}/frontend/dist/index.html" ]; then
  echo "ERROR: 缺少前端构建产物 ${DASHBOARD_DIR}/frontend/dist/index.html（dist 应随插件打包，确认插件完整安装）" >&2
  exit 1
fi
```

不默认安装依赖。仅当 Step 4 因缺依赖启动失败时，提示用户手动执行：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -m pip install -r "${DASHBOARD_DIR}/requirements.txt"
```

### Step 4：启动 Dashboard

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -m dashboard.server --project-root "${PROJECT_ROOT}"
```

不自动打开浏览器时加 `--no-browser`；自定义端口加 `--port 9000`。

启动后优先确认接口可用：`/api/story-runtime/health`、`/api/preflight`。

## 成功标准

- Dashboard 进程已启动并输出可访问 URL；页面显示项目数据（章节列表、实体图谱等）。

## 失败恢复

| 故障 | 恢复方式 |
|------|---------|
| 启动报缺依赖 | 手动 `pip install -r "${DASHBOARD_DIR}/requirements.txt"`，检查 Python 版本与网络 |
| 前端 `dist/` 缺失 | 确认插件完整安装，dist 应随插件打包 |
| 项目根解析失败 | 检查 `.canon-ledger/state.json` 是否存在，确认 `WORKSPACE_ROOT` 正确 |
| 端口占用 | 用 `--port <其他端口>` 或关闭占用进程 |
| 页面空白/数据缺失 | 确认 `.canon-ledger/` 下有 state.json、index.db 等数据文件 |

## 安全边界

- 纯只读面板，不提供修改接口，不修改任何项目文件。
- 文件访问限制在 `PROJECT_ROOT` 范围内，默认仅监听 localhost。

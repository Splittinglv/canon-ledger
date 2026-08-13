---
name: webnovel-dashboard
description: 启动只读可视化面板，浏览项目状态、实体图谱、章节与追读力。用户说打开仪表盘、dashboard 或 /webnovel-dashboard 时使用。
---

# Webnovel Dashboard

## 目标

- 在本地启动只读 Web 面板，查看创作进度、设定词典、关系图谱、章节内容与追读力数据。
- 暴露 Story Runtime 主链状态：`/api/story-runtime/health`、latest commit、fallback 情况。
- 可监听 `.webnovel/` 变化，但不修改任何项目文件。

## 执行流程

### Step 1：确认环境与模块目录

```bash
# 解析 Cursor 插件根与书项目工作区（sessionStart 可能已注入 WEBNOVEL_PLUGIN_ROOT）
_EXPORT=""
for _cand in \
  "${WEBNOVEL_PLUGIN_ROOT:-}/scripts/export_cursor_env.py" \
  "${CURSOR_PLUGIN_ROOT:-}/scripts/export_cursor_env.py" \
  "${CLAUDE_PLUGIN_ROOT:-}/scripts/export_cursor_env.py" \
  "${HOME}/.cursor/plugins/local/webnovel-writer/scripts/export_cursor_env.py"
do
  if [ -f "$_cand" ]; then
    _EXPORT="$(python3 -X utf8 "$_cand")" && break
  fi
done
if [ -z "$_EXPORT" ] && [ -d "${HOME}/.cursor/plugins/cache" ]; then
  _hit="$(python3 -X utf8 -c "from pathlib import Path
hits=[p for p in (Path.home()/'.cursor/plugins/cache').rglob('scripts/export_cursor_env.py') if 'webnovel' in p.as_posix()]
print(hits[0] if hits else '')")"
  if [ -n "$_hit" ] && [ -f "$_hit" ]; then
    _EXPORT="$(python3 -X utf8 "$_hit")"
  fi
fi
if [ -z "$_EXPORT" ]; then
  echo "ERROR: 未找到 webnovel-writer 插件根。请按 README 安装到 ~/.cursor/plugins/local/webnovel-writer" >&2
  exit 1
fi
eval "$_EXPORT"
export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/webnovel-dashboard"
export DASHBOARD_DIR="${WEBNOVEL_PLUGIN_ROOT}/dashboard"
if [ ! -d "${DASHBOARD_DIR}" ]; then
  echo "ERROR: 未找到 dashboard 模块: ${DASHBOARD_DIR}" >&2
  exit 1
fi
```

### Step 2：解析项目根目录

```bash
export PROJECT_ROOT="$(python "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
echo "项目路径: ${PROJECT_ROOT}"
```

`PROJECT_ROOT` 必须解析成功。

### Step 3：校验前端产物与依赖

```bash
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="${WEBNOVEL_PLUGIN_ROOT}:${PYTHONPATH}"
else
  export PYTHONPATH="${WEBNOVEL_PLUGIN_ROOT}"
fi

if [ ! -f "${DASHBOARD_DIR}/frontend/dist/index.html" ]; then
  echo "ERROR: 缺少前端构建产物 ${DASHBOARD_DIR}/frontend/dist/index.html（dist 应随插件打包，确认插件完整安装）" >&2
  exit 1
fi
```

不默认安装依赖。仅当 Step 4 因缺依赖启动失败时，提示用户手动执行：

```bash
python -m pip install -r "${DASHBOARD_DIR}/requirements.txt"
```

### Step 4：启动 Dashboard

```bash
python -m dashboard.server --project-root "${PROJECT_ROOT}"
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
| 项目根解析失败 | 检查 `.webnovel/state.json` 是否存在，确认 `WORKSPACE_ROOT` 正确 |
| 端口占用 | 用 `--port <其他端口>` 或关闭占用进程 |
| 页面空白/数据缺失 | 确认 `.webnovel/` 下有 state.json、index.db 等数据文件 |

## 安全边界

- 纯只读面板，不提供修改接口，不修改任何项目文件。
- 文件访问限制在 `PROJECT_ROOT` 范围内，默认仅监听 localhost。

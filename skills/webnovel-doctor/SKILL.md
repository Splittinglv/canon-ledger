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
3. 统一用 `python -X utf8`，避免中文路径编码问题。
4. 缺失项按 runtime 推导的阶段解释影响与修复建议，不把 init 刚结束的项目按已写多章项目检查。

## 执行

准备路径：

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
export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/webnovel-doctor"
```

短状态：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" project-status --format summary
```

标准体检：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" doctor --format text
```

指定章节加 `--chapter {chapter_num}`，深度体检加 `--deep`。

## 输出方式

汇报包含：当前 `phase` 与 `target_chapter`、是否有 blocker、缺失或异常文件路径、RAG / Python / Dashboard 配置是否缺失、每个问题的影响和建议修复动作。

不执行真实修复，不展示或要求粘贴 API key。

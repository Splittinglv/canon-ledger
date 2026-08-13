---
name: webnovel-learn
description: 把当前书里好用的写法沉淀进项目长期记忆。用户说记住这个写法、沉淀经验或 /webnovel-learn 时使用。
---

# /webnovel-learn

## Project Root Guard（必须先确认）

- 必须在项目根目录执行（需存在 `.webnovel/state.json`）
- 用统一入口解析项目根，避免写错目录：

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

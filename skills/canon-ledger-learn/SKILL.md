---
name: canon-ledger-learn
description: 把用户明确要求记住的文风、口吻、句式或写作偏好写入本书 设定集/文风提示词.md。用户说记住这个文风、沉淀写法或 /canon-ledger-learn 时使用。
---

# /canon-ledger-learn

## Project Root Guard（必须先确认）

- 必须在项目根目录执行（需存在 `.canon-ledger/state.json`）
- 用统一入口解析项目根，避免写错目录：

```bash
# 这段引导仅适用于 POSIX shell（sh/bash/zsh）；Windows 请使用 Git Bash 或 WSL。
# 缓存安装必须使用 Cursor 注入的插件根；不扫描缓存目录寻找可执行脚本。
# bootstrap_env.py 输出固定六行数据协议：逐行 read 赋值，禁止 eval/source 执行输出。
_PLUGIN_ROOT_HINT="${CANON_LEDGER_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
if [ -z "$_PLUGIN_ROOT_HINT" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/canon-ledger"
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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-learn"
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
```
## 目标

只学习本书的长期文风和写作偏好。用户明确要求记住的口吻、句式、叙事方式或文笔习惯，整理成简洁条目后写入 `设定集/文风提示词.md`。

优先级：本轮用户要求 > `设定集/文风提示词.md` > 模型默认写法。

## 执行流程

1. 读取用户在 `/canon-ledger-learn` 后给出的文风要求；若命令参数为空，则取本次对话中用户明确要求记住的文风、口吻、句式、叙事方式或写作偏好。没有明确文风要求就停止并询问，不要把剧情、设定、伏笔或时间线写成文风。
2. 保留用户原意，整理成简洁条目。不要用关键词表过滤或改写用户原话。
3. 用 `Write` 把 JSON 写到 `${PROJECT_ROOT}/.canon-ledger/tmp/style-learn.json`。用户原文只出现在该文件里，用户原文不得放进命令。JSON 形如：

```json
{"items": ["第一条文风偏好", "第二条文风偏好"]}
```

4. 调用 `style-memory add-item` 写入，不得手写或拼接 `设定集/文风提示词.md`：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" style-memory add-item \
  --input-file "${PROJECT_ROOT}/.canon-ledger/tmp/style-learn.json"
```

## 约束

- 只写入 `设定集/文风提示词.md` 的「作者提示词」标题下；命令会保留文件中其他人工内容，并对完全相同的条目去重。
- 不写入 `hard_constraints`，不写入 `.canon-ledger/memory_scratchpad.json`。
- `Write` 只允许用于 `.canon-ledger/tmp/style-learn.json`。禁止用 `Write` 或手工编辑 `设定集/文风提示词.md` 与记忆 JSON。
- 设定、伏笔、时间线和人物事实请改设定集或等章末提交提炼，不要走本命令。

## 成功标准

- `设定集/文风提示词.md` 的「作者提示词」下出现对应条目；重复条目返回 `status: skipped`。
- 输出包含 `status: success` 或 `status: skipped`，以及 `added` / `skipped_duplicates`。
- 本轮写作仍以当前对话里的用户要求为先，再读全书文风提示词。

## 失败恢复

| 故障 | 恢复方式 |
|------|---------|
| 文风提示词无法写入 | fail closed，不改文件并建议检查 `设定集/` 权限或运行 `/canon-ledger-doctor` |
| 输入是设定/剧情而不是文风 | 停止；事实写入设定集，文风才使用本命令 |

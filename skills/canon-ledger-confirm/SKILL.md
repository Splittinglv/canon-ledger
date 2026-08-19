---
name: canon-ledger-confirm
description: 在对话里逐条裁决人工确认队列：审查疑点用 confirm/rewrite/可选 replace，抽取候选事实保留 confirm/ignore/replace。无需返工时重放本章提交；rewrite 则要求修改正文并重走写章链。不评价文风与剧情。
---

# Human Confirm Skill

## 目标

- 让作者在对话里逐条裁决人工确认队列，不需要手写裁决 JSON、不需要记 CLI 命令。
- 把「不是穿帮」与「确认穿帮」分成可验证的后续路径：前者可重放提交，后者必须修改正文并重新审查。
- 只有本章没有 `rewrite`、所有裁决都允许重放时，才用 `chapter-commit --from-last-commit` 重放本章提交：已确认事实升级为 `verified` 进入正史，投影自动重建。

## 红线

- 审查疑点（`source=review_manual_check`，含知识边界、时间线、在场/持有、设定、机械规则）与抽取歧义走同一条确认，但选项语义必须分开。本 skill 只裁决事实，不评价文风、剧情选择或人物动机；选 `rewrite` 时不在确认流里直接改正文，而是交回 `/canon-ledger-write {chapter_num}` 完成返工。
- 每一条裁决必须经 `AskQuestion` 由作者亲自选择（不可用时在聊天里给出同样选项）；禁止主流程替作者猜测、默认确认或批量跳过。
- 每批 `AskQuestion` 不超过 5 条。
- 抽取候选事实（`source` 不是 `review_manual_check`）保留原语义：`confirm`=按候选事实入正史，`ignore`=只丢弃这条候选事实，`replace`=按作者的替换表述入正史。`ignore` 不得在这类条目上解释为「正文穿帮」。
- 审查疑点只呈现：`confirm`=作者确认不是穿帮并关闭疑点；`rewrite`=作者确认是穿帮，必须改正文后重新审查；`replace`=作者明确追认/改写一条可入正史的事实（仅当队列提供此选项）。审查疑点不得呈现 `ignore`；旧队列中的审查 `ignore` 必须按 `rewrite` 语义展示和处理。
- 知识边界审查项（`category=knowledge_boundary`）选 `confirm` 时只表示「不是穿帮」，不强制补事件；只有作者明确要追认角色已知时才选 `replace`，并组装 `knowledge_state_changed`（`state=known`）的 `replacement_event`。
- 选 `replace` 时必须向作者收集替换后的表述，以该条的候选事实或审查上下文组装 `replacement_event`：保持运行时要求的 `event_id`/`chapter`/`sequence`/`event_type`/`subject` 结构；knowledge 事件还须锁定 `information_id`（可改 `canonical_claim`，不可换成另一条秘密的编号），`payload` 按作者表述修改，`evidence_quote` 必须是本章正文中的原样引用。禁止模型自造作者没有说过的事实。
- 任一审查疑点选了 `rewrite`，必须停止本章确认流程，禁止运行 `chapter-commit --from-last-commit`；最终状态必须是「需要你处理」，唯一下一步是 `/canon-ledger-write {chapter_num}`。
- 写第 N 章前，任一更早章存在未裁决 `pending`、`rewrite_required`，或裁决已保存但未重放生效，都必须被 prewrite 硬门禁阻断。只允许重写问题章本身，不得绕过去写后续章。
- 重放提交报“正文已改动”类错误（如 `chapter_content_hash_mismatch`）时不得强行提交：裁决绑定正文哈希，正文变化后必须重跑 `/canon-ledger-write` 走完整写作链。
- 项目根不合法 / 缺 `.canon-ledger/state.json` → 阻断。

## 执行流程

### Step 1：解析项目根

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
export SKILL_ROOT="${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-confirm"
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
```

`PROJECT_ROOT` 必须包含 `.canon-ledger/state.json`，否则阻断。

### Step 2：读取人工确认队列

用户给了章节号就只处理该章；省略章节号则列出全部章节的待确认项。

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  human-review list --chapter {chapter_num}
```

输出 JSON 的 `pending` 为待裁决项，`resolved` 为已裁决且允许重放的项，`rewrite_required` 为已确认穿帮、必须改正文的项。`pending` 为空时先运行 Step 6 的 user-report：

- 报告出现 `rewrite_required` 或「正文需要修改」时，不得重放；报告「需要你处理」并把 `/canon-ledger-write {chapter_num}` 列为唯一下一步。
- 报告仅出现「裁决已保存但尚未生效」，且所有已保存裁决都允许重放时，才跳到 Step 5 对该章重放提交。
- 报告没有上述未闭环状态时，才产出最终报告（总状态：已完成，说明没有待确认项）并结束。

裁决落库与重放生效是两步，只完成前一步不得报告完成；`rewrite` 是明确不允许重放的终止分支，也不得报告完成。

涉及多个章节时按章节号从小到大逐章处理：每章完整走完 Step 3 → Step 5 再进入下一章，禁止把多章裁决混在一次提交里。

### Step 3：逐条向作者裁决

用 `AskQuestion` 分批提问，每批不超过 5 条；`AskQuestion` 不可用时在聊天里给出同样的选项。每条问题必须展示：

- 待确认原因（`reason`）
- 正文证据原样引用（`evidence_quote`）
- 已有记录（`existing_fact`，为空则说明是新事实）
- 候选事实摘要（`candidate_event` 的 `event_type`、`subject`；knowledge 事件还必须原样展示 `information_id`、`canonical_claim`、`state`；没有则写「无」）

选项必须按条目来源呈现，不得把两组语义混在同一个提问里：

- 审查疑点（`source=review_manual_check`）：
  - `confirm`：这不是穿帮，关闭疑点。
  - `rewrite`：这是穿帮，本章必须修改正文并重新审查。
  - `replace`：按作者给出的明确事实追认/替换后写入正史，仅当该条提供此选项时展示。
- 抽取候选事实（其它 `source`）：
  - `confirm`：按候选事实原样写入正史。
  - `ignore`：本章不记录这条候选事实，不表示正文有问题。
  - `replace`：作者给出替换表述后写入正史，仅当该条提供此选项时展示。

作者选 `replace` 的条目，继续向作者收集替换后的表述，再按红线组装 `replacement_event`。
作者选 `rewrite` 后，不再询问如何为旧正文补事实；记录裁决后转到 Step 5 的返工分支。

### Step 4：写入裁决

把全部裁决组装为项目内文件 `${PROJECT_ROOT}/.canon-ledger/tmp/human_review_decisions.json`：

```json
{
  "decisions": [
    {
      "decision_id": "来自队列的 decision_id",
      "action": "confirm | ignore | rewrite | replace",
      "replacement_event": null,
      "note": "作者备注（可选）"
    }
  ]
}
```

`ignore` 仅适用于抽取候选事实，`rewrite` 仅适用于审查疑点；`replacement_event` 仅在 `action=replace` 时提供。然后落库：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  human-review resolve --input-file ".canon-ledger/tmp/human_review_decisions.json"
```

### Step 5：按裁决分支收口

落库后先检查本章的动作分布。

#### 分支 A：存在 `rewrite`

任一审查疑点选了 `rewrite` 就立即停止本章：

- 禁止运行 `chapter-commit --from-last-commit`，不得让旧正文进入 accepted/committed。
- 不处理更晚章节，因为该章的 `rewrite_required` 会硬阻断后续写作。
- 直接进入 Step 6，总状态写「需要你处理」，唯一下一步为 `/canon-ledger-write {chapter_num}`。
- 作者修改正文后，旧裁决会因正文哈希变化而失效；必须重新生成 binding、重新调用 reviewer，再提交最终正文。

#### 分支 B：无 `rewrite`，重放本章提交

从该章上次的提交文件重放，不需要重跑提取：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  chapter-commit --chapter {chapter_num} --from-last-commit
```

- 报“正文已改动”类错误 → 按红线停止本章，最终报告的“必须处理”段提示重跑 `/canon-ledger-write {chapter_num}`。
- 成功后验证投影：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  write-gate --chapter {chapter_num} --stage postcommit --format json
```

- projection 失败时只补跑投影，不回退裁决：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" \
  projections retry --chapter {chapter_num} --format json
```

### Step 6：收尾

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage confirm \
  --chapter {chapter_num} \
  --format text
```

## 成功标准

1. 已解析真实书项目根。
2. 队列里每条 pending 项都得到作者亲自裁决，或明确留待下次处理。
3. 裁决已通过 `human-review resolve` 落库。
4. 无 `rewrite` 时，已裁决章节的 `chapter-commit --from-last-commit` 重放成功，postcommit gate 通过。
5. 有 `rewrite` 时，未运行重放，未把旧正文报告为已完成，作者已收到 `/canon-ledger-write {chapter_num}` 的唯一恢复指引。
6. 重放被正文改动阻止时，作者已收到重跑 `/canon-ledger-write` 的明确指引。

## 作者友好过程提示与恢复契约

开始前先说明本次会经历：读取待确认清单 → 逐条向你确认 → 保存裁决 → 无返工项时重新提交本章，有返工项时交回写章流程。过程提示每次不超过两行，用作者语言，不直接输出原始 JSON、traceback 或长命令日志；技术详情写入 `.canon-ledger/logs/run_last.log`：

```bash
: "${CANON_LEDGER_PYTHON:?环境未就绪：请先在同一个 shell 会话中执行 SKILL.md 开头的环境引导代码块，再重试本块}" "${PROJECT_ROOT:?PROJECT_ROOT 未设置：请先在同一个 shell 会话中执行本 skill 解析项目根的代码块，再重试本块}"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${PROJECT_ROOT}" run-log \
  --event confirm-progress \
  --payload-json "{\"stage\": \"confirm\", \"chapter\": {chapter_num}}" \
  --format text
```

少打扰确认策略：本命令本身就是作者主动发起的确认流程，除逐条裁决外不再追加额外确认；`pending`、`rewrite_required` 和未重放裁决都为空时才直接报告结束，不问任何问题。需要作者裁决时使用与来源匹配的有限选项（审查疑点：confirm / rewrite / 可选 replace；抽取候选：confirm / ignore / replace），并说明每个选项的影响。

作者可以随时中途停下：已落库的裁决不会丢失，重新运行 `/canon-ledger-confirm {chapter_num}` 会跳过已裁决项，只问剩下的。卡住时必须说明卡点、已完成内容和恢复建议，例如“裁决已保存，但重放提交失败；先按提示处理后重新运行 `/canon-ledger-confirm {chapter_num}` 即可从重放继续”。

## 作者友好最终报告契约

最终回复必须面向作者，不输出原始 JSON、traceback 或长命令日志。使用固定三段式，并以一句总状态开头：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、产生的文件与完成情况
- ...

二、过程中遇到的问题与异常耗时
- 已自动处理：...
- 建议确认：...
- 必须处理：...

三、下一步建议
- ...
```

必须汇报：

- 本次裁决条数与动作分布（confirm / ignore / rewrite / replace），并按审查疑点与抽取候选区分语义。
- 剩余未裁决条数（按章节）。
- 每个已裁决章节的分支结果：已重放及投影状态，或因 `rewrite` 明确未重放。
- 正文改动导致重放失败的章节及其恢复命令。

状态规则：

- 全部裁决完成、没有 `rewrite` 且重放成功 → “已完成”。
- 作者留了部分条目未裁决 → “部分完成”。
- 任一审查疑点选了 `rewrite` → “需要你处理”；不执行重放，不得报“已完成”。
- 裁决已落库但本章重放未成功（含未执行重放）→ “需要你处理”，不得报“已完成”。
- 重放被正文改动阻止、resolve 失败或投影失败未恢复 → “需要你处理”。

异常分类：

- 已自动处理：投影失败后 `projections retry` 成功、重复运行时跳过已裁决项。
- 建议确认：抽取候选事实中被 `ignore` 的项（提醒后续章节不要依赖它）。
- 必须处理：`rewrite` 要求修改本章正文、正文改动导致重放失败、裁决文件校验失败、投影重试仍失败。

下一步建议必须使用任务化语言 + 可复制命令，例如：

```text
- 裁决已生效，可以继续写下一章：
  /canon-ledger-write {next_chapter}
- 第 {chapter_num} 章已确认存在穿帮，旧正文不会重放；请修改正文并重跑完整审查：
  /canon-ledger-write {chapter_num}
- 第 {chapter_num} 章正文改过，旧裁决不能复用，请重新走写作链：
  /canon-ledger-write {chapter_num}
```

上述建议按状态互斥：存在 `pending`、`rewrite_required` 或裁决尚未重放生效时，不得同时列出「继续写下一章」。

不写 token 统计；如需排查故障，只给日志路径或建议运行 `/canon-ledger-doctor`。

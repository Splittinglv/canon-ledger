---
name: canon-ledger-plan
description: 基于活动 Canon HEAD 规划卷纲、时间线和章纲；规划默认是软方向，新增长期硬设定必须走独立事务认证。
---

# 规划卷纲与章纲

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)
和 [`../../references/index/reference-loading-map.md`](../../references/index/reference-loading-map.md)。

## 1. Workflow Gate

运行 `canon-v3 status`。已有章节的项目只有 `ready + projection_fresh=true` 才能开始新规划；initialization、pending、rewrite、recompile、projection stale 或 invalid 时停止并执行唯一恢复动作。

规划不能用合同就绪或旧 state/index 绕过 Canon 状态。

## 2. 读取规划依据

只使用：

- 活动 HEAD 的 as-of facts、obligations、timeline 和 entity registry；
- 已生效 author axioms；
- 总纲、已有卷纲和作者本轮要求；
- style-only 文风偏好仅用于表达规划，不转成事实。

禁止把 `.canon-ledger/state.json`、`index.db`、旧 commit 摘要或 STAGING 提议当作当前事实。跨卷人物状态、关系和伏笔统一通过 v3 as-of query facade 获取。

## 3. 规划内容

先确认本卷目标、章节范围、时间跨度、预期结束状态和仍需保留的开放问题，再生成：

- 卷纲和分章目标；
- 明确时间锚点与章节范围；
- 必须延续的活动事实；
- 剧情信号、开放问题和预计回收位置；
- 每章的写作方向与可选禁区。

章纲是作者的执行方向，不是已经发生的 Canon。章纲完成度默认 advisory；不要把爽点、节奏、模板或风格写成硬门禁。

## 4. 区分软计划与硬设定

规划产物分为：

```text
outline_updates              可直接写入大纲/合同的软计划
author_axiom_proposals       新增或修改的长期硬设定草案
style_notes                  可选文风说明
```

- `outline_updates` 可以写入 `大纲/` 和 `.story-system` 规划合同，但不能修改 HEAD。
- `author_axiom_proposals` 先写入受管 draft
  `.canon-ledger/tmp/author_axioms/<name>.json`；顶层只能是
  `schema_version=canon-v3/author-axiom-draft/v1` 与 `author_axioms`，不得直接覆盖活动设定。
  每个硬设定是 `/author_axioms/<axiom_key>` 的 JSON leaf；
  runtime 会同时检查 key、category 与实际 value；不得用 `world_rule` 或无害 key
  包装文风、动机、人格、人设、成长弧等软设计。
  style、软大纲、剧情目标、节奏和写作偏好不得写入此文件。
- `style_notes` 只有作者明确要求记住时才交给 `/canon-ledger-learn`。

硬设定草案由 data-agent 生成每个 leaf 的 exact UTF-8 byte span、文件 SHA、
quote SHA、JSON pointer 与 value SHA，再组装
`canon-v3/author-axiom-proposal/v2`（绑定当前 HEAD、workflow digest、active
axiom digest 与 expected stage）。依次调用 `author-axiom-prepare`、由作者
逐条 `author-axiom-decide`、最后 `author-axiom-finalize`。删除旧 axiom 也
必须形成带 exact prior record 的 remove case 并人工批准；未提及不能静默删除。
完成前 query/context 继续使用上一个 active axiom digest。
该路径必须使用 data-agent `mode=author_axiom_proposal`，先调用
`canon-v3 agent-schema author-axiom-proposal`，再对唯一输出
`.canon-ledger/tmp/canon_v3_author_axiom_proposal.json` 执行
`validate-agent-output author-axiom-proposal`；校验通过后才可以把同一文件交给
`author-axiom-prepare`。不得从测试或内部实现猜 schema。

## 5. 写回与合同刷新

只增量更新目标卷和目标章节，不重写整份总纲或设定集。大纲成功写回后，先执行精确 dry-run：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 planning refresh-contracts \
  --chapter "${CHAPTER}" --dry-run
```

确认返回的 `head_before=head_after`、`authority=planning_only`、
`source_outline.digest` 与刚写回的大纲一致，且三份 preview 共享同一
`planning_batch_digest`，再去掉 `--dry-run` 应用。
应用后三份合同的 `meta.planning_batch_digest` 仍必须与结果同值；不一致时不使用
混合合同，重新执行刷新。
刷新只能在 `ready + projection_fresh=true` 下进行，并且唯一允许写入：

```text
.story-system/chapters/chapter_NNN.json
.story-system/volumes/volume_NNN.json
.story-system/reviews/chapter_NNN.review.json
```

刷新不得同步 MASTER/设定集、修改或创建 STAGING，也不得产生
lock/backup/Git 文件。章纲节点和禁区保留在 chapter planning directive 中；
review contract 的事实 `must_check/blocking_rules` 必须为空，不能把大纲履约
变成事实门禁。若大纲、辅助输入或 Canon 在构建期间变化，丢弃派生结果并重新
读取 status，不发布混合版本。

卷号和章 directive 只从已写回总纲、分章大纲或卷详细大纲派生；旧
`.canon-ledger/state.json` 不参与路由。重复分章文件、重叠卷范围或总纲与卷文件
冲突时停止让作者修正规划文件，不按文件名字典序猜测。

删除旧事实型 `update-state`。卷号、章节范围等非事实进度使用明确的 planning metadata 命令或从规划文件派生，不能复用通用事实写入口。

## 6. 验收

- 规划依据绑定当前 `head_hash/workflow_digest`。
- 输出明确区分软计划、硬设定草案和 style。
- 未认证硬设定没有进入 HEAD、projection、query 或 context；draft 可修改或
  删除，但发布前任一字节变化都会使事务失效。
- Canon workflow 仍为 ready；若产生 author-axiom 事务，则唯一下一步是完成该事务，而不是开始写章。
- 合同刷新返回 source outline/input digest，且应用前后 HEAD/generation/workflow digest 不变。
- 最终报告列出更新文件、待认证硬设定和下一步章节，不输出文风评分。

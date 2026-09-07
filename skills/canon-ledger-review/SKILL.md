---
name: canon-ledger-review
description: 审查章节的长期事实连续性；下一章草稿复用 Canon v3 提议事务，历史章节或范围默认只读审计，不评价文风和剧情选择。
---

# Canon v3 事实审查

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)
和 [`../../references/index/reference-loading-map.md`](../../references/index/reference-loading-map.md)，执行共享环境、状态、事实与 reference 边界。

## 两种模式

### Staged draft review

仅用于 workflow 允许的下一章草稿，或恢复当前唯一 staged 章节。它是 `/canon-ledger-write` 的“正文完成后”半段，允许生成 proposal/STAGING，并继续人工确认与发布。

### Historical audit

用于已经在活动 HEAD 中的旧章或章节范围。默认只读：不创建或替换 STAGING、不截断后缀、不修改 HEAD、不声称可以继续写作。作者明确选择 revise 后，才转入当前章完整重编译流程。

章节范围始终使用 historical audit。

## 红线

- 删除旧 `issues/manual_checks/blocking`、`review-pipeline`、`update-state`、`index.db.review_audits` 链。
- reviewer 必须收到 data-agent 返回的 exact `candidate_draft` 及其 validator 返回的
  `candidate_id -> candidate_digest` map；必须逐项回显，不能自行提取、改写或重新配对候选。
- 只检查长期事实、知识、在场、持有、时间线和明确规则冲突。
- 文风、节奏、人物动机、一般因果、章纲履约和无锚点低概率猜测不进入 observation。

## 1. 选择模式并检查 workflow

运行 `canon-v3 status`：

- 目标章等于 `expected_next_chapter`，且 workflow 为 ready，使用 staged draft review。
- 目标章等于当前 staged chapter，使用 staged draft review 恢复同一事务。
- 目标章已经在活动 manifest 或输入为范围，使用 historical audit。
- initialization/projection/invalid 状态不创建 staged review；按 snapshot 的恢复动作停止。

## 2. 固化输入

对 staged 单章读取当前正文并生成 exact chapter binding，导出目标章 N-1 的
HEAD-bound as-of snapshot。历史审计不得用当前 query 重新拼装旧状态，必须先
调用只读 revision export：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 historical-export \
  --chapter "${CHAPTER}" --revision "${REVISION}"
```

export 必须来自当前 HEAD 可达的 manifest/commit/transaction 对象，并固定
`audited_head/generation/parent_head/commit_hash/transaction_hash`、章节 binding、
exact commit、decision/lineage decision payloads、candidates/effects、当时的
author axioms 与 entity registry。若截断后重新发布
导致同一 chapter/revision 对应多个可达 commit，停止并让作者从返回列表选择
精确 `--commit-hash`，不得自行猜测。

只有当前正文 SHA 与 revision binding 完全一致，或固定的内容寻址 revision
archive 中存在同 SHA 字节时，export 才能返回 `source_available`。返回
`source_unavailable` 时只保存不完整诊断，不能从 candidate quote、当前正文、
Git 或模型记忆重建旧正文。

范围审计逐章导出时，所有 export 的 `audited_head/generation` 必须完全相同；
任一次不一致就丢弃本轮派生缓存并从新的 CURRENT 重新开始，不能混合版本。

## 3. 统一 Agent 链

1. 调用 `data-agent phase=extract`，传 chapter、binding、chapter file、N-1 snapshot、active author-axiom digest。
2. 把 exact candidate draft 和 validator 返回的 exact candidate digest map 原样交给
   `reviewer`；要求逐项回显 map，并完整覆盖 setting/timeline/continuity/character/logic。
3. 保存 reviewer 原始 JSON 到 `.canon-ledger/tmp/canon_v3_review.json`。
4. staged 模式调用 `data-agent phase=assemble` 生成严格 v2 proposal；historical 模式把 exact revision export 原样交给 `data-agent mode=historical_audit`，只接收返回的 `canon-v3/historical-audit/v1` bundle。Agent 不得替换 export 内的 authority、binding、candidate、effect、axiom、registry 或 source。

Reviewer output 必须以 v3 `candidate_digest_map` 精确绑定每个 candidate ID；assemble
逐项比较 draft map，审核后互换 ID 必须失败。ScanAttestation 必须绑定 chapter SHA、
parent HEAD、candidate digests、entity registry 和 active author axioms。正文明示的长期事实漏提时，停止并重跑 extract，不能写 complete。

长篇项目允许在 reviewer 前用候选中的实体、物品、地点、规则和信息命题组合一个严格
`canon-v3/retrieval-search-request/v1`，写入 `.canon-ledger/tmp/retrieval_query.json`，并调用：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 retrieval search \
  --input-file ".canon-ledger/tmp/retrieval_query.json"
```

staged 模式固定 `as_of_chapter=N-1`；historical 模式固定 export 对应的 as-of 边界。
命中只能作为定位 prior fact 的候选，必须保持其 exact `active_fact/fact_digest`，不能引用
`matched_text` 作为证据。检索缺失、降级或无命中时照常完成五维扫描；它既不减少扫描范围，
也不能产生 conflict/checkpoint 或人工 case。
默认 `mode=auto` 必须服从项目远程开关；仅本次审核查询要求本地时将请求设为
`mode=bm25`。只有 `CANON_LEDGER_RETRIEVAL_REMOTE=1` 与非空 key 同时满足时，查询文本才
允许发送到远程 Embedding 服务。

## 4A. Staged draft review

proposal 写入 `.canon-ledger/tmp/canon_v3_proposal.json`，然后执行带版本的 prepare：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 prepare \
  --input-file ".canon-ledger/tmp/canon_v3_proposal.json"
```

已有 STAGING 时，proposal/request 必须包含刚从 status 读取的 `expected_stage_digest`；不同版本拒绝覆盖。

- `awaiting_human`：立即执行 `canon-ledger-confirm`，不把“继续下一章”作为选项。
- `ready_to_finalize`：由 `canon-ledger-confirm` 生成 exact finalize request 并发布。
- `rewrite_required|recompile_required`：返回当前章唯一恢复动作。

## 4B. Historical audit

先完整读取 [`../../references/review-schema.md`](../../references/review-schema.md) 的 `HistoricalAuditBundle v1`。data-agent 在此模式不写文件；本 Skill 是持久化责任方，校验 bundle 后只写：

```text
.canon-ledger/tmp/canon_v3_historical_audit.json
.canon-ledger/tmp/canon_v3_historical_audit.md
```

reviewer 原始输出仍固定写入 `.canon-ledger/tmp/canon_v3_review.json`；处理范围时必须在下一章覆盖它前先把本章绑定收进 bundle。JSON 和作者可读报告必须绑定：

```text
audited_head / generation / export_digest
chapter revision / binding / parent_head / commit_hash
candidate digests
scan attestation digest
observations
source_available | source_unavailable
```

这些 tmp 文件是可覆盖的派生缓存，不是 data-agent 的 proposal 输出，也不是对象库或 Canon source。historical export 和报告都不读取或创建 STAGING，不是 Gate，也不能生成人工决定。若发现历史穿帮，向作者提供：保持只读记录 / 显式 revise 该章。只有 revise 才进入新的 v3 prepare，旧后缀按正常重写规则处理。

## 成功标准

- staged 模式：使用共享 proposal/prepare/confirm/finalize 链，最终状态由 workflow 决定。
- audit 模式：HEAD、STAGING 和 projection 均未改变，报告清楚标记审计版本。
- 两种模式均没有文风评分、旧 queue、legacy pipeline 或 index 写入。

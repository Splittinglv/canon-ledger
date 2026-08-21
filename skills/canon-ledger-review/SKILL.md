---
name: canon-ledger-review
description: 审查章节的长期事实连续性；下一章草稿复用 Canon v3 提议事务，历史章节或范围默认只读审计，不评价文风和剧情选择。
---

# Canon v3 事实审查

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)，执行共享环境、状态和事实边界。

## 两种模式

### Staged draft review

仅用于 workflow 允许的下一章草稿，或恢复当前唯一 staged 章节。它是 `/canon-ledger-write` 的“正文完成后”半段，允许生成 proposal/STAGING，并继续人工确认与发布。

### Historical audit

用于已经在活动 HEAD 中的旧章或章节范围。默认只读：不创建或替换 STAGING、不截断后缀、不修改 HEAD、不声称可以继续写作。作者明确选择 revise 后，才转入当前章完整重编译流程。

章节范围始终使用 historical audit。

## 红线

- 删除旧 `issues/manual_checks/blocking`、`review-pipeline`、`update-state`、`index.db.review_audits` 链。
- reviewer 必须收到 data-agent 返回的 exact `candidate_draft`；不得让 reviewer 自行提取或改写候选。
- 只检查长期事实、知识、在场、持有、时间线和明确规则冲突。
- 文风、节奏、人物动机、一般因果、章纲履约和无锚点低概率猜测不进入 observation。

## 1. 选择模式并检查 workflow

运行 `canon-v3 status`：

- 目标章等于 `expected_next_chapter`，且 workflow 为 ready，使用 staged draft review。
- 目标章等于当前 staged chapter，使用 staged draft review 恢复同一事务。
- 目标章已经在活动 manifest 或输入为范围，使用 historical audit。
- migration/projection/invalid 状态不创建 staged review；按 snapshot 的恢复动作停止。

## 2. 固化输入

对单章读取当前正文并生成 exact chapter binding。导出目标章 N-1 的 HEAD-bound as-of snapshot。历史审计必须固定所审 revision 的正文 binding 和当时 parent HEAD，不能读取未来事实或 legacy index。

## 3. 统一 Agent 链

1. 调用 `data-agent phase=extract`，传 chapter、binding、chapter file、N-1 snapshot、active author-axiom digest。
2. 把 exact candidate draft 原样交给 `reviewer`；要求完整覆盖 setting/timeline/continuity/character/logic。
3. 保存 reviewer 原始 JSON 到 `.canon-ledger/tmp/canon_v3_review.json`。
4. staged 模式调用 `data-agent phase=assemble` 生成严格 v2 proposal；historical 模式调用 `data-agent mode=historical_audit`，只接收返回的 `canon-v3/historical-audit/v1` bundle。

ScanAttestation 必须绑定 chapter SHA、parent HEAD、candidate digests、entity registry 和 active author axioms。正文明示的长期事实漏提时，停止并重跑 extract，不能写 complete。

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
audited_head
chapter revision / binding
candidate digests
scan attestation digest
observations
```

这些 tmp 文件是可覆盖的派生缓存，不是 data-agent 的 proposal 输出，也不是对象库或 Canon source。报告不是 Gate，也不能生成人工决定。若发现历史穿帮，向作者提供：保持只读记录 / 显式 revise 该章。只有 revise 才进入新的 v3 prepare，旧后缀按正常重写规则处理。

## 成功标准

- staged 模式：使用共享 proposal/prepare/confirm/finalize 链，最终状态由 workflow 决定。
- audit 模式：HEAD、STAGING 和 projection 均未改变，报告清楚标记审计版本。
- 两种模式均没有文风评分、旧 queue、legacy pipeline 或 index 写入。

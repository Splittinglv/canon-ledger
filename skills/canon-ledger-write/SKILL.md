---
name: canon-ledger-write
description: 按唯一 Canon v3 事务链完成指定章节：读取 HEAD、自由起草、只做长期事实检查、必要时人工确认，并以版本化请求原子发布。
---

# Canon v3 写章

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)。本 Skill 只强制长期事实一致性；文风由本轮要求、`设定集/文风提示词.md` 和模型决定。

## 完成条件

只有最终同时满足以下条件才宣布章节完成：

```text
state == ready
can_write_next == true
projection_fresh == true
chapter 已进入活动 manifest
```

## 1. Workflow 与目标章

运行 `canon-v3 status` 和 prewrite gate：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" write-gate --chapter {chapter_num} \
  --stage prewrite --format json
```

- 新项目无 CURRENT：先 `canon-v3 initialize`，重新读取 status。
- legacy cutover/recertification：停止起草，执行 snapshot 的 migrate/repair 动作。
- 当前章已有 STAGING：只恢复同一事务，禁止另开章。
- 只有 ready 且目标章位于 `allowed_write_chapters` 才继续。

规划合同就绪不能覆盖 Canon blocker。未重新认证的长期设定也不能进入本章上下文。

## 2. 写作上下文与文风

把本轮用户要求区分为：剧情要求、文风覆盖、显式事实/设定变更。剧情和文风都不能隐式 retcon。

调用 `context-agent`，传入：

```text
chapter
workflow_digest / head_hash
N-1 HEAD-bound facts
active author_axiom_digest
chapter / volume contracts
turn requirements
style override
```

context-agent 必须只消费活动 HEAD；state/index、STAGING 提议和 legacy 数据不能冒充已生效事实。

按用户偏好自由完成正文。章纲是剧情方向，缺失节点可以报告，但默认不属于事实阻断。

## 3. 固化正文和 N-1 快照

正文完成后生成 exact chapter binding，并导出 N-1 as-of snapshot：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" chapter-binding --chapter {chapter_num} \
  --out "${PROJECT_ROOT}/.canon-ledger/tmp/chapter_binding.json" --format json

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" memory-contract export-asof \
  --chapter {chapter_num} --out "${PROJECT_ROOT}/.canon-ledger/tmp/asof_snapshot.json"
```

从此正文任一字节、HEAD 或 active author axiom 变化都会使 proposal、scan 和决定失效。

## 4. 唯一 Proposal 链

严格依次调用：

1. `data-agent phase=extract`：只输出 exact FactCandidates。
2. `reviewer`：读取同一 candidate draft 与 N-1 snapshot，完整扫描五个事实维度。
3. `data-agent phase=assemble`：验证 binding、sources、support map 和 attestations，写 `.canon-ledger/tmp/canon_v3_proposal.json`。

每个 source 必须实际参与 support map。模型不得写 delta、人工队列或正史。正文明确的长期事实漏提时，重跑 extract；无锚点低概率猜测忽略。

## 5. 带版本 Prepare

调用 `canon-v3 prepare --input-file ...`。若 status 已有 STAGING，请求必须携带当前 `expected_stage_digest`；不存在时显式为 null。不能静默替换别人正在确认的事务。

- `awaiting_human`：立即执行 [`../canon-ledger-confirm/SKILL.md`](../canon-ledger-confirm/SKILL.md)。
- `ready_to_finalize`：仍由 confirm Skill 生成 exact finalize request。
- `rewrite_required`：按已确认的事实穿帮修改正文，从 binding 重跑。
- `recompile_required`：重新 binding、extract、scan、prepare。
- migration/projection/invalid：停止并执行唯一恢复动作。

## 6. 发布前与发布

precommit gate 必须读取与最新 status 相同的 `workflow_digest/stage_digest`：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" write-gate --chapter {chapter_num} \
  --stage precommit --format json
```

只有 `ready_to_finalize + can_finalize=true` 才允许 confirm Skill 使用：

```text
expected_stage_digest
transaction_hash
finalize_token
```

执行 exact finalize。版本冲突时刷新，不自动重试到新事务。响应丢失时可用完全相同的 request 幂等重试。

## 7. 最终 Gate

重新运行 status、postcommit gate 和 user report。投影未追上 HEAD 时只执行 rebuild-projection，fresh 前不得建议下一章。

## 恢复规则

- `correct`：替换 exact candidate 后重新完整扫描和 prepare。
- `omit`：同语义事实换证据再次出现时必须重新人工确认，不能自动复活。
- `rewrite`：正文摘要不变时旧 tombstone 持续生效。
- HEAD 竞争：基于新 HEAD 重建 N-1 snapshot 和 proposal。
- style 文件变化：只刷新风格上下文，不改变 Canon 事务。

## 最终报告

简要给出正文路径与 SHA、proposal、transaction/stage、人工动作数量、HEAD、projection 和最终 workflow。不要输出文风评分、原始长 JSON 或 token 统计。

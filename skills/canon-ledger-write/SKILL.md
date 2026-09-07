---
name: canon-ledger-write
description: 按唯一 Canon v3 事务链完成指定章节：读取 HEAD、自由起草、只做长期事实检查、必要时人工确认，并以版本化请求原子发布。
---

# Canon v3 写章

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)
和 [`../../references/index/reference-loading-map.md`](../../references/index/reference-loading-map.md)。本 Skill 只强制长期事实一致性；文风由本轮要求、`设定集/文风提示词.md` 和模型决定。

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
- 当前章已有 STAGING：只恢复同一事务，禁止另开章。
- 只有 ready 且目标章位于 `allowed_write_chapters` 才继续。

规划合同就绪不能覆盖 Canon blocker。未重新认证的长期设定也不能进入本章上下文。
若卷/章/审查规划合同含 `meta.planning_batch_digest`，三份值必须完全相同才作为本章
软计划输入。不一致表示刷新中断：忽略三份派生合同，从原大纲继续或运行
`/canon-ledger-plan` 重新刷新；这个 planning warning 不得伪装成 Canon blocker。

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
`memory-contract` 可附带 v3 `rag_assist` 来突出与本章目标相关的 active facts；每条必须
已回查 `active_canon` 且 `usable_as_canon=false`。缺失、过期、无 Embedding 或检索失败时
继续使用完整 HEAD-bound 上下文，不能阻断起草，也不能把“无命中”解释为“没有事实”。

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
导出结果中的 `author_axioms` 必须与 `canon_binding.head_hash`、顶层
`author_axiom_digest` 绑定，并包含每条活动 axiom 的 `axiom_key/category/value` 与可直接
用于 FactCandidate 的 `source_type=author_axiom` source。该公共视图不得含 draft
`start/end/quote`，也不得出现 STAGING proposal 或 legacy cache 的值；只有 digest 而无
`records` 不是完整 reviewer/data-agent 输入。

## 4. 唯一 Proposal 链

严格依次调用：

1. `data-agent phase=extract`：只输出 exact FactCandidates。
2. `reviewer`：读取同一 candidate draft、validator 返回的 exact
   `candidate_id -> candidate_digest` map 与 N-1 snapshot（包括其中的活动
   `author_axioms.records`），逐项回显该 map 并完整扫描五个事实维度。
3. `data-agent phase=assemble`：验证 binding、sources、support map 和 attestations，写 `.canon-ledger/tmp/canon_v3_proposal.json`。

每个 source 必须实际参与 support map。模型不得写 delta、人工队列或正史。正文明确的长期事实漏提时，重跑 extract；无锚点低概率猜测忽略。

Agent 不能手算 candidate digest 或手拼 proposal。必须依次使用公共 helper：

```text
canon-v3 agent-schema candidate-draft
canon-v3 validate-agent-output candidate-draft
canon-v3 agent-schema reviewer-output
canon-v3 validate-agent-output reviewer-output
canon-v3 assemble-proposal
```

任一 helper 报 schema、binding、digest、source/support 或 scan 不闭合时停止，修正对应
Agent artifact 后重跑；reviewer map 与 draft 不完全相同（包括审核后互换 candidate ID）
必须停止，禁止绕过 validator 直接调用 prepare。

## 5. 带版本 Prepare

调用 `canon-v3 prepare --input-file ...`。若 status 已有 STAGING，请求必须携带当前 `expected_stage_digest`；不存在时显式为 null。不能静默替换别人正在确认的事务。

- `awaiting_human`：立即执行 [`../canon-ledger-confirm/SKILL.md`](../canon-ledger-confirm/SKILL.md)。
- `ready_to_finalize`：仍由 confirm Skill 生成 exact finalize request。
- `rewrite_required`：按已确认的事实穿帮修改正文，从 binding 重跑。
- `recompile_required`：重新 binding、extract、scan、prepare。
- initialization/projection/invalid：停止并执行唯一恢复动作。

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

重新运行 status、postcommit gate 和 user report。Canon 投影未追上 HEAD 时只执行
`rebuild-projection`，fresh 前不得建议下一章。最终 workflow 已 ready 后尝试刷新可选召回层：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 retrieval rebuild
```

该命令失败、只生成 BM25 或随后显示 stale 都只写入最终报告的增强 warning；章节仍已完成，
不得重开事务、要求额外事实决定或阻止下一章。
自动重建必须遵守项目远程开关：仅有 Embedding key 不得发送文本，只有
`CANON_LEDGER_RETRIEVAL_REMOTE=1` 与非空 key 同时满足才可远程调用；项目 `.env` 的
显式 `0` 覆盖全局 `1`。

## 恢复规则

- `correct`：替换 exact candidate 后重新完整扫描和 prepare。
- `omit`：同语义事实换证据再次出现时必须重新人工确认，不能自动复活。
- `rewrite`：正文摘要不变时旧 tombstone 持续生效。
- HEAD 竞争：基于新 HEAD 重建 N-1 snapshot 和 proposal。
- style 文件变化：只刷新风格上下文，不改变 Canon 事务。

## 最终报告

简要给出正文路径与 SHA、proposal、transaction/stage、人工动作数量、HEAD、projection 和最终 workflow。不要输出文风评分、原始长 JSON 或 token 统计。

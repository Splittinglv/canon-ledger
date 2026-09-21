---
name: canon-ledger-confirm
description: 处理当前 Canon v3 章节或 author-axiom 事务的必审事实；也在作者明确要求放弃未发布 STAGING 时按 exact digest 归档。
---

# Canon v3 人工确认

## 目标

只处理 `workflow_snapshot` 中当前唯一权威事务：章节 STAGING 或 author-axiom
STAGING。作者看到版本 A，只能操作版本 A；
任何内容变化都要求刷新，不能把旧选择转接到新事务。

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)
和 [`../../references/index/reference-loading-map.md`](../../references/index/reference-loading-map.md)，执行其中的环境引导、状态与 reference 路由规则。

## 红线

- 人工展示只来自 `cases[].review_material`。
- 作者必须逐条亲自选择；禁止默认批准、批量猜测或从旧 queue/proposal 拼装材料。
- author-axiom material 必须直接展示 `proposed_category/proposed_value`。若内容实际是文风、文笔、节奏、口吻或写作偏好，不得建议 `approve` 为硬设定；说明它不属于 Canon，请作者选择 `omit|rewrite`，需要长期保存时改走 `/canon-ledger-learn`。
- 若 review material 标记 `fact_boundary_human_classification_required=true`，或章节
  requirement 含 `fact_boundary:human_classification_required`，必须向作者明确询问“是否把
  这个 exact value 作为后续章节强制依赖的客观故事事实”。此时 `approve` 是绑定当前
  candidate/record digest 的事实分类，不是普通无冲突放行；不属于客观事实时选择
  `omit|correct|rewrite`，不能因为作者愿意保留该写法就提升为 Canon。
- 只提交 case 返回的 `allowed_actions`。普通 checkpoint 通常为 `approve|rewrite`；ambiguity 通常为 `approve|omit|correct|rewrite`。
- 禁止旧 `confirm|ignore|replace`、`human-review resolve` 和 `chapter-commit --from-last-commit`。
- `projection_rebuild_required|invalid` 时不创建决定。
- 放弃/取消不等于删除；禁止直接 unlink STAGING、删不可变对象或使用兼容别名
  `cancel`。只有作者明确要求放弃当前未发布事务时才可进入 exact archive 分支。

## 1. 读取当前版本

运行 `canon-v3 status`。用户给章节号时，必须与 snapshot 的 staged chapter 相同；省略时只处理当前唯一事务。

只在以下状态继续：

- `awaiting_human`：询问 required cases。
- `ready_to_finalize`：跳到发布步骤。
- `rewrite_required|recompile_required`：停止确认，返回 snapshot 的唯一恢复动作。
- `ready`：已经完成，不重复决定。

记录本轮展示版本：

```text
workflow_digest
stage_digest
transaction_hash
head_hash
chapter
transaction_kind (`chapter|author_axiom`)
```

对每个 case 同时记录：

```text
case_key
target_digest
allowed_actions
```

章节与 author-axiom case 记录 `review_material.material_digest` 和
`decision_head_hash`，不得自行补 null 字段或混用其它 material 路径。

构造章节或 author-axiom 请求时执行唯一字段映射：

```text
status.cases[i].decision_head_hash
  -> decisions[i].expected_decision_head_hash
```

值必须逐值复制；首次决定的 `null` 也必须保留为 JSON `null`。这里不能使用活动 Canon `head_hash` 或 `parent_head`。

## 2. 作者明确放弃 STAGING

只对本轮明确的“放弃/取消当前未发布事务”请求执行。立即重读 status，
要求 `transaction_kind=chapter|author_axiom` 且 `stage_digest` 为当前 64 位值。向作者
简要确认：该未发布指针会移入非权威归档，不会进入 HEAD，之后必须重新
prepare；transaction 和 decisions 不删除。然后只调用：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 archive-staging \
  --transaction-kind "${TRANSACTION_KIND}" \
  --expected-stage-digest "${STAGE_DIGEST}"
```

不从旧回复或本地文件填 digest。若 status 在执行前改变，请求会拒绝；重新展示
新事务，不自动放弃它。成功后重读 status，报告 `archive_path`、新 primary action
和 `requires_reprepare=true`，然后结束本轮；不继续执行旧 decide/finalize。

## 3. 向作者展示

每项只展示有助于裁决的内容：

- 候选事实和所有逐字证据；
- 会写入的 compiled effects；
- exact prior facts / prior effects；
- effect 中 `inherited_fields` 标出的值来自上述 prior，不能展示为本章逐字原文；
  没有 prior 的空前态说明为“未记录”，只确认正文支持的新状态；
- 实体消歧结果、触发原因和动作后果；
- author-axiom 展示 material 顶层的 category/value；不得只展示看似安全的 axiom key 而隐藏实际 value。

每批不超过 5 项。额外人工确认可以接受，但不得把文风、剧情偏好、人物动机或无锚点猜测加入问题。

关系 case 有既有关系时，明确问作者本次是“替换旧关系”还是“保留旧关系并新增”：
展示 prior facts/effects，以及有 key 时的 `current_relationships/relationship_write_mode`。
替换只更新选中的同一 `relationship_key`（旧默认关系为 null）；并存使用新 key。
需要改 key 时提交现有 `correct` 动作，由 data-agent 保留原证据并修正候选，重新扫描和
prepare 后再确认。不要把普通“批准新事实”默认为同意删除另一种关系，不新增决定动作。

## 4. STAGING：写入 exact DecisionRequest

`transaction_kind=author_axiom` 时只允许 `approve|omit|rewrite`，使用
`canon-v3/author-axiom-decision-request/v2`；章节使用
`canon-v3/decision-request/v2`。二者字段映射相同，但 schema、文件与 CLI action
不能混用。

把本批选择写入 `.canon-ledger/tmp/canon_v3_decisions.json`：

```json
{
  "schema_version": "canon-v3/decision-request/v2",
  "expected_stage_digest": "<snapshot.stage_digest>",
  "transaction_hash": "<snapshot.transaction_hash>",
  "decisions": [
    {
      "case_key": "<case.case_key>",
      "target_digest": "<case.target_digest>",
      "material_digest": "<case.review_material.material_digest>",
      "expected_decision_head_hash": null,
      "action": "approve"
    }
  ]
}
```

示例展示的是首次决定；case 值非 null 时，把该 64 位值原样放入同一字段。

只有 `correct` 可以增加 `corrected_candidate`。修订候选必须由 data-agent 基于当前正文或 active author axiom 生成，继续满足 exact source、完整 support map、实体身份和稳定 slot 约束。

章节事务提交上述文件：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 decide \
  --input-file ".canon-ledger/tmp/canon_v3_decisions.json"
```

author-axiom 则把同形 payload 写入
`.canon-ledger/tmp/canon_v3_author_axiom_decisions.json`，把 `schema_version` 精确改为
`canon-v3/author-axiom-decision-request/v2`，并且只调用：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 author-axiom-decide \
  --input-file ".canon-ledger/tmp/canon_v3_author_axiom_decisions.json"
```

服务返回版本冲突时，丢弃本地请求，重新读取 status 并重新展示；不得修改 digest 或自动重试。

## 5. 按 STAGING 决定恢复

- `awaiting_human`：使用返回的新 `stage_digest` 继续下一批。
- `recompile_required`：把 exact correction 写回候选集合，重新 binding、全候选扫描和 prepare；旧 stage 不发布。
- `rewrite_required`：作者修改正文，从 chapter binding 完整重跑。
- `ready_to_finalize`：进入发布。

OMIT/CORRECT/REWRITE 都形成语义谱系。若同一事实换证据再次出现，只能生成重新考虑 case，不能自动复活。

## 6. STAGING exact finalize

`transaction_kind=author_axiom` 时，finalize 文件使用
`canon-v3/author-axiom-finalize-request/v2` 并保存为
`.canon-ledger/tmp/canon_v3_author_axiom_finalize.json`；章节才使用下述普通 schema/file。
axiom 发布只改变 HEAD/generation 与 active author-axiom snapshot，不得改变
`latest_chapter/allowed_write_chapters`。

从最新 status 读取 `finalize_token`，写入 `.canon-ledger/tmp/canon_v3_finalize.json`：

```json
{
  "schema_version": "canon-v3/finalize-request/v2",
  "expected_stage_digest": "<latest stage_digest>",
  "transaction_hash": "<latest transaction_hash>",
  "finalize_token": "<latest finalize_token>"
}
```

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 finalize \
  --input-file ".canon-ledger/tmp/canon_v3_finalize.json"
```

author-axiom 只调用：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 author-axiom-finalize \
  --input-file ".canon-ledger/tmp/canon_v3_author_axiom_finalize.json"
```

发布后重新读取 status。只有 `ready + can_write_next=true + projection_fresh=true` 才能宣布完成。响应丢失时只允许用同一个 finalize request 做 exact retry。

## 7. 刷新可选检索

无论本轮发布的是章节还是 author axiom，只要最终 workflow 已
`ready + projection_fresh=true`，就尝试执行一次 `canon-v3 retrieval rebuild`。它只刷新
可丢弃的 active-fact 召回层；BM25-only、远程 Embedding 失败或命令失败都只在报告中标记
增强未就绪，不得改变本轮发布成功、重开 STAGING、追加人工 case 或阻止下一章。
远程默认关闭；该自动重建只有在项目实际生效的
`CANON_LEDGER_RETRIEVAL_REMOTE=1` 且 key 非空时才能发送检索文本。项目 `.env` 的显式
`0` 必须覆盖全局 `1`。

## 成功标准

1. 每个 required case 都由作者对 exact material 做出允许动作。
2. 决定和发布请求都回显同一事务的版本摘要。
3. 没有 rewrite/recompile/pending。
4. STAGING finalize 成功，或 exact token/request 被证明已幂等发布。
5. 最终 workflow 为 ready；否则报告唯一恢复动作。

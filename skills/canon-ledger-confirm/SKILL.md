---
name: canon-ledger-confirm
description: 处理当前 Canon v3 事务的必审事实或迁移案例，并把作者选择精确绑定到其看到的 STAGING、证据和既有正史版本。
---

# Canon v3 人工确认

## 目标

只处理 `workflow_snapshot` 中当前唯一权威事务：章节 STAGING、author-axiom
STAGING，或 legacy recertification detached plan。作者看到版本 A，只能操作版本 A；
任何内容变化都要求刷新，不能把旧选择转接到新事务。

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)，执行其中的环境引导和状态规则。

## 红线

- 人工展示只来自 `cases[].review_material`。
- 作者必须逐条亲自选择；禁止默认批准、批量猜测或从旧 queue/proposal 拼装材料。
- author-axiom material 必须直接展示 `proposed_category/proposed_value`。若内容实际是文风、文笔、节奏、口吻或写作偏好，不得建议 `approve` 为硬设定；说明它不属于 Canon，请作者选择 `omit|rewrite`，需要长期保存时改走 `/canon-ledger-learn`。
- 只提交 case 返回的 `allowed_actions`。普通 checkpoint 通常为 `approve|rewrite`；ambiguity 通常为 `approve|omit|correct|rewrite`。
- 禁止旧 `confirm|ignore|replace`、`human-review resolve` 和 `chapter-commit --from-last-commit`。
- `projection_rebuild_required|invalid` 时不创建决定。
- `migration_required` 只有在 snapshot 明确给出
  `transaction_kind=legacy_recertification`、exact plan/token 与 cases 时才能继续；
  其他 migration 状态只执行 primary action。

## 1. 读取当前版本

运行 `canon-v3 status`。用户给章节号时，必须与 snapshot 的 staged chapter 相同；省略时只处理当前唯一事务。

只在以下状态继续：

- `awaiting_human`：询问 required cases。
- `ready_to_finalize`：跳到发布步骤。
- `rewrite_required|recompile_required`：停止确认，返回 snapshot 的唯一恢复动作。
- `migration_required + transaction_kind=legacy_recertification`：进入下述 legacy
  逐项确认分支，不调用普通 decide/finalize。
- `ready`：已经完成，不重复决定。

记录本轮展示版本：

```text
workflow_digest
stage_digest
transaction_hash
head_hash
chapter
transaction_kind (`chapter|author_axiom|legacy_recertification`)
```

legacy recertification 另记录：

```text
recertification_plan_digest
recertification_publish_token
```

对每个 case 同时记录：

```text
case_key
target_digest
allowed_actions
```

章节与 author-axiom case 记录 `review_material.material_digest` 和
`decision_head_hash`；legacy recertification case 记录顶层 `material_digest`，且没有
可变 decision head，不得自行补 null 字段或混用两种 material 路径。

构造章节或 author-axiom 请求时执行唯一字段映射：

```text
status.cases[i].decision_head_hash
  -> decisions[i].expected_decision_head_hash
```

值必须逐值复制；首次决定的 `null` 也必须保留为 JSON `null`。这里不能使用活动 Canon `head_hash` 或 `parent_head`。

## 2. 向作者展示

每项只展示有助于裁决的内容：

- 候选事实和所有逐字证据；
- 会写入的 compiled effects；
- exact prior facts / prior effects；
- 实体消歧结果、触发原因和动作后果；
- 若是 cutover/author-axiom recertification，展示旧来源、规范化结果和待认证字段。
- author-axiom 展示 material 顶层的 category/value；不得只展示看似安全的 axiom key 而隐藏实际 value。

每批不超过 5 项。额外人工确认可以接受，但不得把文风、剧情偏好、人物动机或无锚点猜测加入问题。

## 3. Legacy recertification：一次性 exact publish request

仅当 `transaction_kind=legacy_recertification` 时执行本节。先运行
`canon-v3 repair-cutover --dry-run`，确认返回的 current head、plan digest、publish
token 和 cases 与刚展示的 snapshot 完全相同；变化时丢弃全部旧选择并重新展示。

作者必须逐项确认所有 cases。可以每批展示不超过 5 项，但在收齐全部明确选择前不得调用
apply；禁止模型批量默认确认。写入
`.canon-ledger/tmp/canon_v3_recertification_publish.json`：

```json
{
  "schema_version": "canon-v3/legacy-recertification-publish-request/v1",
  "expected_current_head": "<snapshot.head_hash>",
  "detached_plan_digest": "<snapshot.recertification_plan_digest>",
  "publish_token": "<snapshot.recertification_publish_token>",
  "decisions": [
    {
      "schema_version": "canon-v3/legacy-recertification-decision/v1",
      "case_key": "<case.case_key>",
      "target_digest": "<case.target_digest>",
      "material_digest": "<case.material_digest>",
      "action": "confirm"
    }
  ]
}
```

`decisions` 必须与当前 cases 一一对应，不能少项、多项或沿用另一 plan。若当前确实零
case，也必须先向作者展示空 plan 摘要并取得显式发布同意，不能自动 apply。

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 repair-cutover --apply \
  --input-file ".canon-ledger/tmp/canon_v3_recertification_publish.json"
```

发布后重新读取 unified status。只有 ready 才完成；若 projection stale，只执行
`rebuild-projection`。响应丢失时只重放同一个 request。完成本分支后不要再执行普通
DecisionRequest 或 finalize。

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

## 成功标准

1. 每个 required case 都由作者对 exact material 做出允许动作。
2. 决定和发布请求都回显同一事务的版本摘要。
3. 没有 rewrite/recompile/pending。
4. STAGING finalize 或 recertification apply 成功，或 exact token/request 被证明已幂等发布。
5. 最终 workflow 为 ready；否则报告唯一恢复动作。

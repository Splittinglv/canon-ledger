# Canon v3 Reviewer Output v2

reviewer 是只读事实扫描器，不是放行者。它读取 exact candidate draft 和 N-1 HEAD，只输出 `ReviewObservation` 与 `ScanAttestation`；不得输出旧 `issues/manual_checks/blocking_count`，不得写 queue/index 或调用事务 API。

## 顶层

```json
{
  "schema_version": "canon-v3/reviewer-output/v2",
  "chapter": 1,
  "chapter_sha256": "...",
  "parent_head": "...",
  "author_axiom_digest": "...",
  "entity_registry_digest": "...",
  "candidate_digests": [],
  "observations": [],
  "scan_attestations": [],
  "extraction_incomplete": []
}
```

所有版本字段必须与调用输入完全一致。reviewer 不得自行重算或替换 candidate。

## Observation

允许类型：

- `confirmed_conflict`：本章与 exact prior 不能同时成立；必须携带 prior fact digest 和正文证据。
- `ambiguity`：有长期事实锚点但解释、实体或状态转换不唯一。
- `checkpoint`：关键永久事实、核心关系、硬规则、关键物品/秘密/时间、承诺/开放问题、retcon 或 author-axiom 认证。
- `advisory|audit`：只读提示，不获得修改 Canon 的权限。

文风、节奏、人物动机、一般因果、剧情选择、章纲履约以及无锚点低概率猜测不得输出 observation。

## ScanAttestation

唯一 complete attestation 必须绑定：

```text
chapter_sha256
parent_head
author_axiom_digest
entity_registry_digest
全部 exact candidate digests
setting/timeline/continuity/character/logic 五维
```

正文明示且会影响后文的事实缺少 candidate 时，把它加入 `extraction_incomplete`，不得返回 complete。

## 权威边界

compiler 根据 observations、typed candidates、active slots 和 policy 生成 cases。人工决定通过 DecisionRequest v2 绑定 exact stage/transaction/target/material。reviewer 的任何字段都不能直接写入 Canon。

历史章节范围审查使用同一 reviewer 输出 schema，但只生成下面的 HEAD-bound audit bundle，不创建 STAGING 或人工队列。

## HistoricalAuditBundle v1

历史审计的最小派生格式固定为：

```json
{
  "schema_version": "canon-v3/historical-audit/v1",
  "mode": "historical_audit",
  "workflow_digest": "...",
  "audited_head": "...",
  "generation": 0,
  "range": {"start_chapter": 1, "end_chapter": 1},
  "chapters": [
    {
      "chapter": 1,
      "revision": 1,
      "chapter_binding": {},
      "parent_head": "...",
      "candidate_digests": [],
      "scan_attestation_digest": "...",
      "observations": [],
      "extraction_incomplete": []
    }
  ],
  "disposition": "read_only"
}
```

`audited_head/generation/workflow_digest` 固定审计开始时的活动版本；每章记录固定被审 revision、正文 binding 和该 revision 的 parent HEAD。范围内任一章缺少这些绑定或 complete scan attestation 时，bundle 仍可作为不完整诊断保存，但必须保留 `extraction_incomplete`，不得被称为完整审计。

默认派生文件只有：

```text
.canon-ledger/tmp/canon_v3_review.json
.canon-ledger/tmp/canon_v3_historical_audit.json
.canon-ledger/tmp/canon_v3_historical_audit.md
```

reviewer 只返回 JSON，data-agent 的 `mode=historical_audit` 只向调用方返回组装后的 bundle；二者都不写审计文件。调用它们的 `canon-ledger-review` Skill 负责校验 schema 后写入上述固定 tmp 路径。后一轮审计可以覆盖这些派生缓存；需要长期保存时由作者显式导出报告。它们不是对象库、Gate、proposal、人工队列或 Canon source，compiler/prepare 不得读取。

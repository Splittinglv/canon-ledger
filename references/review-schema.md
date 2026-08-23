# Canon v3 Reviewer Output v3

reviewer 是只读事实扫描器，不是放行者。它读取 exact candidate draft 和 N-1 HEAD，只输出 `ReviewObservation` 与 `ScanAttestation`；不得输出旧 `issues/manual_checks/blocking_count`，不得写 queue/index 或调用事务 API。

## 顶层

```json
{
  "schema_version": "canon-v3/reviewer-output/v3",
  "chapter": 1,
  "chapter_sha256": "...",
  "parent_head": "...",
  "author_axiom_digest": "...",
  "entity_registry_digest": "...",
  "candidate_digest_map": {},
  "candidate_digests": [],
  "observations": [],
  "scan_attestations": [],
  "extraction_incomplete": []
}
```

所有版本字段必须与调用输入完全一致。`candidate_digest_map` 必须逐项原样回显
candidate-draft validator 返回的 exact `candidate_id -> candidate_digest`，其 values 的
唯一有序集合必须等于 `candidate_digests` 和 complete attestation 的 checked set。
reviewer 不得自行重算、替换或重新配对 candidate；runtime assemble 会与 draft 的
exact map 比较，因此审核后互换 candidate ID 必须失败。

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

历史审计开始前先生成只读的 `canon-v3/historical-revision-export/v1`。
它只沿审计开始时 CURRENT 的 manifest 祖先链解析 immutable
commit/transaction，不读取 STAGING。最小权威输入为：

```json
{
  "schema_version": "canon-v3/historical-revision-export/v1",
  "mode": "historical_audit_input",
  "disposition": "read_only",
  "authority": {
    "audited_head": "...",
    "generation": 1,
    "parent_head": "...",
    "commit_hash": "...",
    "transaction_hash": "...",
    "decision_hashes": [],
    "lineage_decision_hashes": []
  },
  "commit": {"object_hash": "...", "payload": {}},
  "transaction": {"object_hash": "...", "payload": {}},
  "decisions": [{"object_hash": "...", "payload": {}}],
  "lineage_decisions": [],
  "chapter": 1,
  "revision": 1,
  "chapter_binding": {},
  "source": {
    "status": "source_available",
    "kind": "current_manuscript|revision_archive",
    "path": "...",
    "sha256": "...",
    "bytes": 1,
    "content": "..."
  },
  "candidate_digests": [],
  "candidates": [],
  "effects": [],
  "observations": [],
  "scan_attestations": [],
  "scan_attestation_digests": [],
  "author_axioms": {},
  "entity_registry": {},
  "source_workflow_digest": "...",
  "export_digest": "..."
}
```

`source.status=source_available` 只允许两种来源：当前章正文的 SHA/字节数与
binding 完全一致，或
`.story-system/v3/revision-archive/manuscripts/<chapter_sha256>.md` 的内容再次
验证为同一 SHA。否则必须返回 `source_unavailable`、`content=null` 和原因；
不得从 candidate quote、当前改写正文、legacy index、Git 或模型记忆猜测旧正文。

新发布的 v3 chapter revision 在 HEAD CAS 前把 exact bound manuscript 写入上述
内容寻址 archive；archive 失败必须阻止发布并保留 STAGING/CURRENT。CAS 冲突
留下的未引用 archive blob 不具备 Canon 权威，后续同字节重试幂等复用。旧版本
未归档、作者显式删除或归档损坏时仍返回 `source_unavailable`，不得降级猜测。

`chapter + revision` 在截断后重发的历史中可能对应多个可达 commit；此时导出
返回 `historical_revision_ambiguous` 和 commit hashes，调用方必须提交其中一个
exact `commit_hash`。commit hash 只用于消歧，不能导出 CURRENT 祖先链之外的对象。

data-agent/reviewer 只能在上述 export 上追加审查结果，不得修改或重算其
authority、binding、candidates/effects、axiom、registry 和 source。
`HistoricalAuditBundle v1` 的最小派生格式固定为：

```json
{
  "schema_version": "canon-v3/historical-audit/v1",
  "mode": "historical_audit",
  "audited_head": "...",
  "generation": 0,
  "range": {"start_chapter": 1, "end_chapter": 1},
  "chapters": [
    {
      "chapter": 1,
      "revision": 1,
      "chapter_binding": {},
      "parent_head": "...",
      "commit_hash": "...",
      "revision_export_digest": "...",
      "source_status": "source_available",
      "candidate_digests": [],
      "scan_attestation_digest": "...",
      "observations": [],
      "extraction_incomplete": []
    }
  ],
  "disposition": "read_only"
}
```

`audited_head/generation` 固定审计开始时的活动版本；每章记录固定被审
revision、export digest、正文 binding、parent HEAD、commit hash 和 source
status。范围内任一章缺少这些绑定、`source_available` 或 complete scan
attestation 时，bundle 仍可作为不完整诊断保存，但必须保留
`extraction_incomplete`，不得被称为完整审计。

默认派生文件只有：

```text
.canon-ledger/tmp/canon_v3_review.json
.canon-ledger/tmp/canon_v3_historical_audit.json
.canon-ledger/tmp/canon_v3_historical_audit.md
```

reviewer 只返回 JSON，data-agent 的 `mode=historical_audit` 只向调用方返回组装后的 bundle；二者都不写审计文件。调用它们的 `canon-ledger-review` Skill 负责校验 schema 后写入上述固定 tmp 路径。后一轮审计可以覆盖这些派生缓存；需要长期保存时由作者显式导出报告。它们不是对象库、Gate、proposal、人工队列或 Canon source，compiler/prepare 不得读取。

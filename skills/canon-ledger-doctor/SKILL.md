---
name: canon-ledger-doctor
description: 只读诊断 Canon v3 workflow、事务对象、正文绑定、HEAD 投影和插件运行环境，并给出唯一恢复动作。
---

# Canon v3 体检

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)
和 [`../../references/index/reference-loading-map.md`](../../references/index/reference-loading-map.md)。本 Skill 只读：不修复、不安装依赖、不启动 Dashboard、不修改项目。

## 检查顺序

1. 解析项目根；不存在书项目时只报告 init 所需条件。
2. 读取 exact `canon-v3 status`；把完整 `workflow_snapshot`、
   `workflow_digest` 和 `primary_action` 当作同一版本。
3. 运行标准 doctor。它始终校验 CURRENT/manifest/可达对象、唯一 STAGING、
   author-axiom 和 HEAD-bound projection。
4. 用户要求 `--deep` 时，再检查 Dashboard 打包和额外运行环境；深度模式仍然只读。
5. 按 workflow state 解释问题，不让旧 state/index/RAG、旧 projection 日志或
   合同阶段覆盖 Canon blocker，也不让这些兼容诊断自行产生事实恢复动作。

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 status

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" doctor --format text
```

标准模式的 v3 权威检查：

- CURRENT、manifest、commit、transaction、decision 的内容寻址与引用；
- chapter 与 author-axiom STAGING 是否互斥，并与 transaction/stage digest 精确绑定；
- active author-axiom snapshot 是否绑定同一 HEAD 和 digest；
- workflow 的 HEAD/generation、可写状态和唯一 `primary_action`；
- canon projection 的 HEAD/generation freshness；
- 可选 `canon-v3 retrieval status`：ready 时展示 mode、fact/vector count 与 exact binding；
  missing/stale/invalid 时只给 `canon-v3 retrieval rebuild` 增强建议，不改变 `report.ok`、
  `can_write_next` 或 workflow primary action；
- 旧 index、`.canon-ledger/vectors.db`、RAG 和 projection log 只作为明确标记的兼容告警，
  即使可读也不能报告为当前 BM25/向量可用。

`--deep` 另外报告 Dashboard 前端打包、服务端依赖和插件运行环境。Doctor 必须分别报告
Embedding key 是否存在、`CANON_LEDGER_RETRIEVAL_REMOTE` 是否显式开启和远程能力是否实际
生效；仅有 key 仍表示本地 BM25。缺少或损坏 retrieval、旧 projection 日志不能把有效
HEAD 判成损坏，也不能触发任何 v2 补跑命令。
缺少大纲、角色卡、卷/章/审查规划合同或 legacy state/index 字段只能是规划/兼容
warning；它们不改变 `report.ok`、`can_write_next` 或唯一事实恢复动作。

## 状态对应报告

- `initialization_required + new_project`：报告 snapshot 的 initialize action。
- `awaiting_human`：报告 `/canon-ledger-confirm` 和当前 transaction/stage。
- `ready_to_finalize`：报告当前 exact finalize action，不宣称已经发布。
- `rewrite_required|recompile_required`：恢复当前章或当前 author-axiom 事务，不建议下一章。
- `projection_rebuild_required`：只报告 v3 rebuild-projection，不回退旧索引。
- `invalid`：保持只读，报告首个稳定 reason code 和受影响对象。
- `ready`：只有 `can_write_next=true && projection_fresh=true` 才报告可继续。

## 输出

先给总状态，再列：

- exact workflow/head/stage；
- 已验证正常项；
- blocker 及其影响；
- snapshot 原样给出的唯一 `primary_action`；
- 与事实权威分开的兼容/环境告警。

不把版本冲突、保守阻断、额外人工或缺少非必需 RAG 误报成正史损坏。
遇到 exact-version conflict 时只说“内容已变化，请刷新后确认”，不把旧决定自动应用
到新的 HEAD/STAGING。

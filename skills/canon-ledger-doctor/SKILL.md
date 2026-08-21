---
name: canon-ledger-doctor
description: 只读诊断 Canon v3 workflow、cutover 认证、事务对象、正文绑定、HEAD 投影和插件运行环境，并给出唯一恢复动作。
---

# Canon v3 体检

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)。本 Skill 只读：不修复、不安装依赖、不启动 Dashboard、不修改项目。

## 检查顺序

1. 解析项目根；不存在书项目时只报告 init 所需条件。
2. 读取 exact `canon-v3 status` 和 `workflow_digest`。
3. 运行标准 doctor；用户要求 `--deep` 时再执行深度对象/引用校验。
4. 按 workflow state 解释问题，不让旧 state/index/RAG 健康覆盖 Canon blocker。

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 status

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" doctor --format text
```

深度模式额外检查：

- CURRENT、manifest、commit、transaction、decision 的内容寻址与引用；
- STAGING schema、stage digest、decision heads 和 finalize token；
- active chapter bindings 与 author-axiom digest；
- legacy prefix/genesis 是否需要 recertification；
- admission、target transition、entity namespace/alias 和 custody invariants；
- canon projection 的 HEAD/generation freshness；
- Dashboard 打包、Python 依赖和可选 RAG 状态。

## 状态对应建议

- new project migration_required：initialize。
- legacy cutover：migrate/audit-cutover。
- recertification：`repair-cutover --dry-run` 固定 plan/cases，再由
  `/canon-ledger-confirm` 逐项确认；只在全部确认后执行 exact `--apply --input-file`。
- awaiting_human：`/canon-ledger-confirm N`。
- rewrite/recompile：恢复当前章，不建议下一章。
- projection stale：rebuild-projection。
- invalid：保持只读，报告首个稳定 reason code 和受影响对象。

## 输出

先给总状态，再列：

- exact workflow/head/stage；
- 已验证正常项；
- blocker 及其影响；
- 唯一恢复动作；
- 可选环境问题。

不把保守阻断、额外人工或缺少非必需 RAG 误报成正史损坏。

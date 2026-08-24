---
name: canon-ledger-dashboard
description: 启动只读 Dashboard，展示统一 Canon v3 workflow 和绑定当前 HEAD 的人物、关系、状态、时间线与开放问题视图。
---

# Canon v3 Dashboard

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)
和 [`../../references/index/reference-loading-map.md`](../../references/index/reference-loading-map.md)。Dashboard 只读，不修改项目、STAGING 或 HEAD。

## 启动前

1. 解析真实项目根。
2. 运行 `canon-v3 status`，记录 exact `workflow_digest/head_hash/generation`。
3. 确认 dashboard 模块和打包后的 frontend 存在。
4. 不默认安装依赖；缺依赖时报告用户可执行的安装命令。

启动：

```bash
if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="${CANON_LEDGER_PLUGIN_ROOT}:${PYTHONPATH}"
else
  export PYTHONPATH="${CANON_LEDGER_PLUGIN_ROOT}"
fi
"${CANON_LEDGER_PYTHON}" -m dashboard.server --project-root "${PROJECT_ROOT}"
```

可使用 `--no-browser` 或 `--port N`。

## 数据要求

启动后优先验证：

```text
/api/canon-v3/workflow
/api/canon-v3/history
/api/canon-v3/facts
/api/canon-v3/entities
/api/canon-v3/relationships
/api/canon-v3/state-changes
/api/canon-v3/obligations
```

所有事实响应必须携带同一 `{authority, head_hash, generation, workflow_digest, projection_digest, as_of_chapter}`。公共 active facts 是已经准入的 genesis、legacy cutover
基础事实、章节 effects 和 active author axioms 的去重并集；`origin=legacy_cutover` 只表示
历史来源，仍属于 active Canon，不能与未迁移的 `legacy_read_only` 混淆。人物、关系、
状态、知识、在场、持有、时间线和 obligations 从同一 public read bundle 派生，不能读取 legacy `index.db`、`state.json` 中的事实缓存或旧 commit/projection 作为当前事实。

`/api/canon-v3/facts` 默认只返回 `authority_layer=active_canon`。显式
`include_history=true` 时顶层与每条记录必须标记 `canon_history/historical` 和
`usable_as_active_fact=false`；`/api/canon-v3/history` 则分别声明 active facts 与 history
的 authority layer，不能用一个 `active_canon` 标签包住已覆盖历史。

projection stale 时事实接口返回结构化 409，包含 exact workflow、`projection_rebuild_required` 和 `primary_action`；前端不能把失败静默转换为空列表。退役的 legacy/index 分析接口返回结构化 410，并明确标记 `authority=legacy_read_only`、`usable_for_writing=false`，主导航不得调用。

Dashboard 的全部 API 必须是 GET-only。启动、读取 workflow、读取事实、查看文件和诊断前后都不得创建目录、锁文件、缓存或其他项目文件。

## Workflow 展示

侧栏和首页直接显示 exact state、HEAD、generation、目标章、`can_write_next`、当前 STAGING、人工 cases 及其 exact `allowed_actions`、唯一 `primary_action`。不得用笼统 Mainline/Fallback 替代权威状态，也不得自行推断“可继续”。

Dashboard 只展示 `primary_action`、命令和人工审核材料，不提供 decide/finalize/cancel 按钮，不提交人工决定，也不修改 STAGING 或 HEAD。需要采取动作时回到相应 CanonLedger Skill。

伏笔/开放问题页只读取 HEAD-bound `obligations` 与 `lifecycle_history` 中的 open-loop 事实，不能读取 `project_info.plot_threads`、大纲伏笔表或 legacy index。

## 成功标准

- Dashboard 可访问。
- workflow API 与 CLI 的 `workflow_digest` 相同。
- 所有事实页绑定同一 HEAD/generation。
- stale/migration/invalid 状态被明确展示，不泄漏旧 index 数据。
- 首页显示 exact workflow、STAGING、cases、`can_write_next` 和 `primary_action`。
- 伏笔页的数据源为 `/api/canon-v3/obligations`。
- Canon 事实页的数据源为 `/api/canon-v3/facts`，能显示 genesis/cutover/chapter/axiom
  origin，且不会把 STAGING、style 或 raw legacy 合入。
- Dashboard 全程只提供 GET/只读接口。

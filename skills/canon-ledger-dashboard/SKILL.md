---
name: canon-ledger-dashboard
description: 启动只读 Dashboard，展示统一 Canon v3 workflow 和绑定当前 HEAD 的人物、关系、状态、时间线与开放问题视图。
---

# Canon v3 Dashboard

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)。Dashboard 只读，不修改项目、STAGING 或 HEAD。

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
/api/canon-v3/entities
/api/canon-v3/relationships
/api/canon-v3/state-changes
```

所有事实响应必须携带同一 `{head_hash, generation}`。人物、关系、状态、知识、在场、持有、时间线和 obligations 从 fresh canon projection/history 派生，不能读取 legacy `index.db` 作为当前事实。

projection stale 时事实接口返回 409 并展示 `projection_rebuild_required`；前端不能把失败静默转换为空列表。legacy/index 只能出现在明确标记的历史诊断视图。

## Workflow 展示

首页直接显示 exact state、目标章、是否可写、当前 STAGING 和唯一恢复动作。不得用笼统 Mainline/Fallback 替代权威状态，也不得自行推断“可继续”。

## 成功标准

- Dashboard 可访问。
- workflow API 与 CLI 的 `workflow_digest` 相同。
- 所有事实页绑定同一 HEAD/generation。
- stale/migration/invalid 状态被明确展示，不泄漏旧 index 数据。
- Dashboard 全程只提供 GET/只读接口。

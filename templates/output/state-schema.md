# state.json 兼容投影说明

> `state.json` 是 Canon v2 遗留的兼容读模型，不是 Canon v3 权威来源。当前写作、查询、审核和发布不得直接修改它，也不得在 HEAD 不可用时回退读取它。

## 当前权威关系

```text
.story-system/v3/CURRENT
  -> immutable manifest / commits / decisions
  -> rebuild-projection
  -> state.json（如兼容组件需要）
```

活动状态、目标章、是否可继续和恢复动作统一读取
`canon-v3 status` 返回的 `canon-v3/workflow-snapshot/v2`。事实查询统一读取与
CURRENT 同一 HEAD 绑定的 Canon v3 projection/API。

## 兼容字段

旧工具可能仍展示下列顶层字段：

- `project_info`：书名、题材与目标规模等项目元信息；
- `progress`：旧版章节/字数投影；
- `protagonist_state`、`relationships`、`world_settings`：从历史事实派生的兼容视图；
- `plot_threads`、`chapter_meta`、`strand_tracker`：旧版规划或展示数据；
- `review_checkpoints`、`disambiguation_*`：历史审查数据，不是当前人工队列。

这些字段可能缺失、滞后或被删除重建。它们不能证明某事实已经生效，也不能覆盖
active author axioms、STAGING 或 HEAD。

## 维护规则

- 不通过 `update_state.py`、fact 型 `update-state` 或文件编辑写入当前事实。
- 投影落后时执行 `canon-v3 rebuild-projection`，不要 replay/retry 旧写链。
- 诊断时同时记录 CURRENT HEAD、projection binding 和 workflow digest；只看到
  `state.json` 内容不能判定项目健康。
- 需要作者改变长期硬设定时，使用 managed author-axiom
  prepare/decide/finalize；人物动机、文风和剧情偏好不写入事实投影。

# 新项目 Canon v3 数据流

```text
作者确认的初始化方案
  ├─ 软计划 → 大纲 / Story System contracts
  ├─ style → 设定集/文风提示词.md
  └─ 硬设定 → managed author-axiom manifest
                         │
                         ▼
                 Canon v3 genesis
                         │
                         ▼
                  CURRENT + projection
```

初始化完成的权威标志不是 `state.json` 或目录存在，而是：

```text
workflow.state=ready
can_write_next=true
projection_fresh=true
bootstrap_mode=new_project
```

`.canon-ledger/state.json`、`index.db`、摘要和 RAG 都不是正史。大纲与章合同描述未来方向，不证明事件已发生。文风文件永远排除在 author axioms 和 Canon 之外。

若目标目录已有 accepted legacy prefix，init 不得创建空 genesis；必须进入 legacy cutover。参考书拆解输出只是候选，作者确认并写入对应软计划/style/硬设定轨之前不生效。

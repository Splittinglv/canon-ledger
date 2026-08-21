# 新项目 Canon v3 数据流

```text
exact clean target + 作者确认的 init 输入
  → canon_ledger.py init <target> <title> <genre> ...
      ├─ 软计划 → 大纲 / Story System contracts
      ├─ style-only 载体 → 设定集/文风提示词.md
      └─ 已确认初始硬事实
           → 净化的 MASTER_SETTING.initial_canon
           → verified new-project snapshot
           → mode=author_axiom_snapshot 的 genesis admissions
           → immutable Canon v3 genesis / CURRENT
           → fresh HEAD-bound projection
```

统一 init 工具在生成骨架后直接尝试建立 genesis；不要默认再调一次 initialize。只有中断恢复时，对 exact target 重读 status 且它精确返回
`migration_required + bootstrap_mode=new_project + primary_action=initialize_v3`，才单独执行 `canon-v3 initialize`。

初始化完成的权威标志不是 `state.json`、骨架目录或 `bootstrap_mode=new_project`，而是：

```text
workflow.state=ready
can_write_next=true
projection_fresh=true
bootstrap_mode=canon_v3
head_hash=<CURRENT HEAD>
```

`canon-v3 author-axioms` 中，初始硬事实位于 `genesis_admissions`。尚未发生初始化后的设定变更时，manifest 的 `author_axiom_commits` 和 active snapshot 的 `records` 为空是正常的；初始事实并不伪装成一个 managed draft commit。

初始化后的长期硬设定变更走另一条受管链：

```text
.canon-ledger/tmp/author_axioms/*.json 的 exact JSON leaf
  → author-axiom proposal
  → prepare → 逐项 decide → finalize
  → HEAD 可达的 managed author-axiom commit
```

替换初始硬事实时必须同时绑定 exact `genesis_overrides`，不能靠改 live `MASTER_SETTING`、设定集文件或 init 参数重写 genesis。finalize 前的 draft 不进入 projection/query/context。

`.canon-ledger/state.json`、`index.db`、摘要和 RAG 都不是正史。大纲与章合同只描述未来方向；人物动机、剧情取舍和章纲履约不是强制事实门禁。文风文件永远 style-only，排除在 genesis admissions、author-axiom commits、HEAD 和人工 cases 之外。

目标目录已有 accepted legacy prefix 时，init 不得创建空 genesis；无 CURRENT 时进入 `legacy_cutover`，已有 CURRENT 但来源前缀失效时进入 `legacy_repair`，旧 genesis 需重新认证时进入 `recertification`。三者都只执行 workflow snapshot 的 exact primary action。

参考书拆解输出只是候选。作者确认后也必须按软计划、style-only 或硬事实轨分流，不能整包进入 genesis。

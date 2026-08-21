# index.db 兼容索引说明

> `index.db` 是可删除重建的查询/展示投影，不是 Canon v3 权威来源。任何生产 Skill 都不得把数据库行、别名命中、评分表或旧人工记录提升为 active fact。

## 当前读取边界

```text
Canon v3 HEAD + active author axioms
  -> HEAD-bound projection builder
  -> index.db / Dashboard compatibility views
```

CURRENT、manifest、不可变对象与 exact decisions 决定事实；`index.db` 只加速展示。
数据库缺失或 binding 过期时，workflow 应返回 `projection_rebuild_required`，随后执行
`canon-v3 rebuild-projection`。禁止从数据库反向修补对象库。

## 历史表族

旧版数据库可能包含：

- 章节/场景：`chapters`、`scenes`、`appearances`；
- 实体与别名：`entities`、`aliases`、`relationships`、`state_changes`；
- 规划/追读力：`override_contracts`、`chase_debt`、`debt_events`、`chapter_reading_power`；
- 可观测性：`invalid_facts`、`review_audits`、`review_metrics`、`rag_query_log`、
  `tool_call_stats`、`writing_checklist_scores`。

表存在不表示其属于当前产品门禁。评分、章纲履约、人物动机、文风和写作清单只能作为
advisory/历史数据，不能生成 Canon case。当前实际兼容 schema 以
`scripts/data_modules/index_manager.py` 为准。

## 维护规则

- 不直接执行 SQL 来接受、修改或删除正史事实。
- alias 命中不是身份批准；身份必须来自 HEAD-bound entity registry 和 exact 人工决定。
- Dashboard 应展示 authoritative workflow state，并明确区分 active、STAGING、legacy 与 style。
- 体检发现数据库损坏或过期时，只建议从同一 HEAD 重建；不建议旧
  `projections replay/retry`。

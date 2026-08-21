# 当前产品测试与 retired v2 规格

8.0 起，v2 `chapter-commit`、旧人工队列、旧投影 replay，以及从
`state.json` / `index.db` 回退事实，均不再是可调用的生产工作流。为让旧测试通过而重新开放这些
写入口，会破坏 Canon v3 的单一 HEAD、精确人工决定和原子发布边界。

默认 `pytest` 因此只运行当前产品验收，并由 `scripts/conftest.py` 明确排除依赖已退役 v2 写入口的
冻结规格。正常结果会报告 `deselected` 数量；这不是按失败动态忽略，而是固定的文件/用例清单。

旧规格仍保留在仓库中，供迁移审计和历史设计对照。需要查看它们时可以显式执行：

```bash
CANON_LEDGER_INCLUDE_RETIRED_V2_TESTS=1 python -m pytest <旧测试路径>
```

这些规格描述的是已删除产品，不能作为当前主验收；其中要求写入 legacy 数据的断言预期会被
`legacy_fact_mutation_disabled` 拒绝。legacy 的当前支持范围由 Canon v3 migration、cutover audit、
recertification 和只读解析测试覆盖。

当前发布至少运行：

```bash
python -m pytest
python scripts/run_behavior_evals.py --suite fast
python /path/to/skill-creator/scripts/quick_validate.py skills/<skill-name>
```

另需独立前向测试真实 Skill 场景，不能只检查关键词或复述设计答案。

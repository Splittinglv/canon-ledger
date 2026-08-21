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
python scripts/validate_document_links.py
python scripts/sync_plugin_version.py --check --expected-version <MANIFEST_VERSION>
python scripts/validate_plugin_package.py --strict --format json
python scripts/validate_release_notes.py --version <MANIFEST_VERSION> --previous-tag <PREVIOUS_TAG> --format json
npm --prefix dashboard/frontend run build
python /path/to/skill-creator/scripts/quick_validate.py skills/<skill-name>
```

`quick_validate.py` 要遍历全部 9 个 `skills/canon-ledger-*` 目录，不只抽查发生改动的 Skill。

## Canon v3 验收矩阵

| 风险面 | 主要可执行覆盖 |
|--------|----------------|
| typed claim、source/support 与 reviewer 完整性 | `test_canon_v3_domain.py`、`test_canon_v3_source_verifier.py`、`test_canon_v3_review.py` |
| 单一 STAGING、CAS 决定、谱系与全局不变量 | `test_canon_v3_service.py`、`test_canon_v3_global_invariants.py`、`test_canon_v3_author_axiom.py` |
| 内容寻址对象、并发与故障注入原子性 | `test_canon_v3_repository.py`、`test_canon_v3_author_axiom.py` |
| 身份命名空间、同名实例与历史边界 | `test_canon_v3_entity_registry.py` |
| projection freshness、可删除重建与 HEAD 绑定 | `test_canon_v3_projection.py` |
| v2 cutover、legacy repair、v1 recertification、stale/partial/race | `test_canon_v3_migration.py`、`test_canon_v3_cli.py` |
| 发布包、版本、release range、文档/fixture 路径 | `test_validate_plugin_package.py`、`test_sync_plugin_version.py`、`test_validate_release_notes.py`、`test_validate_document_links.py` |

另需在干净临时目录做独立前向测试：新项目定位前初始化、genesis 只接收允许的硬事实、
初始化后 workflow 为 `canon_v3/ready`、Doctor 对 clean/healthy/stale projection/legacy repair 给出
唯一正确动作，以及 checkpoint/ambiguity 的人工决定不能跨版本复用。前向测试必须真实调用 CLI
和读取产物，不能只检查关键词或复述设计答案。

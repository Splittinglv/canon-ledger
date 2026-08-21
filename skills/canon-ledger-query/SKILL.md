---
name: canon-ledger-query
description: 查询活动 Canon HEAD 中的角色、关系、规则、时间线、知识、物品持有、承诺和开放问题，并明确区分 STAGING 与 legacy 只读数据。
---

# 查询故事事实

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)。查询是只读操作，不改变 STAGING、HEAD 或 projection。

## 1. 固定查询版本

先读取 `canon-v3 status`，在结果中记录：

```text
state
workflow_digest
head_hash / generation
projection_fresh
latest_chapter
```

正常事实回答只使用活动 HEAD。若 `projection_fresh=false` 或状态为 `projection_rebuild_required`，当前公开查询面不会绕过投影直接读取对象历史：停止事实回答，只建议执行 `canon-v3 rebuild-projection`；重建失败则转 `/canon-ledger-doctor`。不能回退 state/index，也不能把 stale 投影包装成“截至 HEAD”。

## 2. 数据层分离

查询结果明确分为：

1. `active_canon`：当前 HEAD 中已生效事实，可作为后续写作依据。
2. `staged_proposal`：当前事务的待确认提议，只在用户明确询问草稿审核时展示，不能称为已发生。
3. `legacy_read_only`：迁移前旧数据，仅供定位和修复；不能与 active canon 合并回答。
4. `draft_setting`：磁盘上尚未 recertify 的硬设定草案，不能冒充 active author axiom。
5. `style`：文风偏好，仅在用户询问写法时返回，不作为事实。

任何 staged、legacy、draft setting 或 style 结果都不能覆盖 HEAD，也不能参与活动事实的肯定回答。

## 3. 只使用已有公开查询面

先按数据层选择公开 facade：

| 数据层 | 公开 facade | 约束 |
|---|---|---|
| `active_canon` | `canon-v3 history`；active hard settings 用 `canon-v3 author-axioms`；Dashboard 已启动时可用 `/api/canon-v3/facts`、`/api/canon-v3/entities`、`/api/canon-v3/relationships`、`/api/canon-v3/state-changes` | 只在 fresh projection 下返回；响应须绑定当前 `head_hash/generation`；author axioms 只来自不可变 commit |
| `staged_proposal` | `canon-v3 status` 的当前 `cases[].review_material` | 仅在用户明确问当前审核草案时展示；不是 active facts |
| `legacy_read_only` | migration/recertification 状态下的 `canon-v3 audit-cutover` | 仅返回审计实际暴露的旧前缀/准入材料；不能回答的字段直接说明“公开 facade 未提供” |
| `style` | `style-memory show` | 只返回作者文风提示词，不得与事实结果合并 |
| `draft_setting` | 当前没有可证明已认证状态的公共查询 facade | fail-closed；不得直接读设定文件后把内容称为 Canon |

调用示例：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 history

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 author-axioms

"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" style-memory show
```

legacy 审计只有在 workflow 指向 cutover/recertification 时才调用。若上述 facade 没有该种查询能力，停止并说明缺失，不扫描 object store、不通读 manifest/commit/decision 文件，也不借 `index.db`、旧 `knowledge query-*` 或 RAG 猜答案；尤其不能用 index aliases 替代 v3 entity registry。

## 4. 选择最窄 v3 视图

按问题选择最窄的 HEAD-bound/as-of 接口：

| 问题 | 查询视图 |
|---|---|
| 人物状态、身份、别名 | canonical entity + state history |
| 关系 | relationship facts/history |
| 世界规则与违反 | active rule + violation occurrences |
| 知识边界 | actor + proposition knowledge slot |
| 在场 | presence history/current physical presence |
| 物品归属 | canonical item custody |
| 承诺、开放问题 | active obligations + lifecycle history |
| 时间线 | occurrence timeline as of 指定章节 |
| 综合写作上下文 | `memory-contract load-context --chapter N`，内部截至 N-1 |

这些领域视图当前统一由 `canon-v3 history` 或 Dashboard 的 HEAD-bound 事实接口承载；不得承诺另一个尚不存在的 as-of CLI。需要精确旧章视图但公开响应没有对应 revision 时，停止并说明能力缺口。

## 5. 时间与旧章

用户问第 N 章时，明确区分：

- “写第 N 章前知道什么”：as-of N-1。
- “第 N 章发布后状态”：as-of N。
- 历史 revision：必须标记所查 manifest/head，不混入未来事实。

## 6. 输出

先回答结论，再列：

- 查询版本（HEAD/generation/as-of chapter）；
- 命中的事实及其来源章节；
- 若有歧义，列出多个 canonical instances，不自动选第一个；
- 数据属于 active、staged、legacy 还是 draft setting；
- 无结果时说明是“未记录”，不要推断成“没有发生”。

文风、剧情取舍和人物动机问题可按作者偏好回答，但不得包装为 Canon 查询结果。

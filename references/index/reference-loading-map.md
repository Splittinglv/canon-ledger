# Reference Loading Map

所有 `canon-ledger-*` Skill 首先完整读取 `references/canon-v3-skill-protocol.md`。共享协议维护环境解析、workflow 状态、版本化人工请求、事实/style/legacy 边界；各 Skill 不复制另一套状态机。

| Skill | 额外 reference | 何时读取 |
|---|---|---|
| init | `skills/canon-ledger-init/references/system-data-flow.md` | always |
| init | worldbuilding 下人物/势力/力量/规则资料 | 仅用户需要相应初始化设计时；输出先是候选，不直接入 Canon |
| plan | `references/outlining/plot-signal-vs-spoiler.md` | 拆章时；只影响软计划 |
| review | `references/review-schema.md` | staged 或 historical audit always |
| review | `skills/canon-ledger-review/references/common-mistakes.md` | 需要区分 confirmed conflict、ambiguity 与忽略项时 |
| query | `skills/canon-ledger-query/references/system-data-flow.md` | always |
| query | `skills/canon-ledger-query/references/tag-specification.md` | 用户明确询问手动标签时；标签只产候选 |

命名 CSV 可以在 init/plan/write 中按需检索，但只提供名称候选，不获得 Canon 权威。写法、节奏、爽点、调性和 Anti-AI 资料不由事实 Skill 默认加载。

Story System contracts 只携带规划方向和作者软约束。`state.json`、`index.db`、RAG、摘要和 legacy commits 不登记为事实 reference，也不能覆盖 Canon HEAD。

Dashboard/doctor/learn 不需要额外领域 reference：Dashboard 读 HEAD-bound API；doctor 读 workflow/integrity；learn 只走 style-memory。

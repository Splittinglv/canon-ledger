# Canon v3 Skill 共享协议

所有 `canon-ledger-*` Skill 在操作书项目时共享本协议。各 Skill 只补充自己的工作，不得复制或改写另一套事实状态机。

## 产品边界

- 强制约束只覆盖后续章节必须依赖的长期事实：作者硬设定、时间线、人物永久状态、关系、知识边界、真实在场、物品持有、世界规则、承诺与开放问题。
- 文风、文笔、节奏、口吻、句式、审美、人物动机、一般因果和章纲履约不进入 Canon，也不触发强制审查。
- 无正文或既有正史锚点的极低概率猜测直接忽略。
- 证据不足但会影响长期事实时可以保守阻断或增加人工确认；不得自动猜测后发布。

## 环境与项目根

以下引导适用于 POSIX shell。每个 Skill 在第一次调用 CLI 前执行一次；后续代码块复用同一 shell 中的变量。

```bash
_PLUGIN_ROOT_HINT="${CANON_LEDGER_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-}}"
if [ -z "$_PLUGIN_ROOT_HINT" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/canon-ledger"
fi
_ENV_LINES="$(python3 -X utf8 "${_PLUGIN_ROOT_HINT}/scripts/bootstrap_env.py")" || exit 1
_ENV_PARSE_OK=1
{
  IFS= read -r CANON_LEDGER_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r SCRIPTS_DIR || _ENV_PARSE_OK=0
  IFS= read -r WORKSPACE_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PROJECT_DIR || _ENV_PARSE_OK=0
  IFS= read -r CANON_LEDGER_PYTHON || _ENV_PARSE_OK=0
} <<EOF
$_ENV_LINES
EOF
[ "$_ENV_PARSE_OK" -eq 1 ] || exit 1
export CANON_LEDGER_PLUGIN_ROOT CURSOR_PLUGIN_ROOT SCRIPTS_DIR WORKSPACE_ROOT CURSOR_PROJECT_DIR CANON_LEDGER_PYTHON
unset _PLUGIN_ROOT_HINT _ENV_LINES _ENV_PARSE_OK
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
```

不得扫描缓存目录寻找另一个插件副本，不得凭当前工作目录猜项目根。

## 唯一 Workflow Authority

识别出书项目后，无论 `.story-system/v3/CURRENT` 是否存在，所有 Skill 都先执行：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 status
```

只信返回的完整 `workflow_snapshot` 及其 `workflow_digest`；合同、旧报告、`.canon-ledger/state.json`、`index.db` 和模型口头判断都不能覆盖它。

| state | 允许的事实推进动作 |
|---|---|
| `migration_required` + `bootstrap_mode=new_project` | `canon-v3 initialize` |
| `migration_required` + `bootstrap_mode=legacy_cutover` | `canon-v3 migrate` |
| `migration_required` + `bootstrap_mode=recertification` | `repair-cutover --dry-run`，再由 `/canon-ledger-confirm` 逐项确认并 apply |
| `ready` | 仅允许 `allowed_write_chapters` 中的目标章进入 plan/write/staged review |
| `ready_to_finalize` | 只允许对当前 STAGING 做 exact finalize |
| `awaiting_human` | 只允许 `/canon-ledger-confirm` 处理当前事务 |
| `rewrite_required` | 修改当前章正文并从 binding 重新运行 |
| `recompile_required` | 对当前章重新 binding、extract、scan、prepare |
| `projection_rebuild_required` | 只允许 rebuild/doctor；事实视图不得回退旧索引 |
| `invalid` | 只允许 doctor、只读诊断和 style-only 操作 |

只有 `state=ready && can_write_next=true && projection_fresh=true` 才能建议开始下一章。

## 有版本的人工操作

章节或 author-axiom STAGING 的每个 required case 必须包含当前：

```text
stage_digest
transaction_hash
target_digest
review_material.material_digest
decision_head_hash
```

人工展示只使用 `cases[].review_material`。决定请求必须原样回显这些值；任一值变化时停止并重新读取 status，禁止把旧选择自动应用到新 STAGING。

字段名在读写两侧有意不同：把 status 的 `cases[].decision_head_hash`（包括首次决定时的 JSON `null`）逐值复制到请求项的 `expected_decision_head_hash`。不得把它替换成 Canon `head_hash`、`parent_head`、空字符串或模型记忆中的上一次决定摘要。

`decide` 输入使用 `canon-v3/decision-request/v2`，顶层包含 `expected_stage_digest`、`transaction_hash` 和 `decisions`。每项包含 `case_key`、`target_digest`、`material_digest`、`expected_decision_head_hash`、`action`，只有 `correct` 可以带 `corrected_candidate`。

`finalize` 输入使用 `canon-v3/finalize-request/v2`，必须包含 `expected_stage_digest`、`transaction_hash` 和 `finalize_token`。禁止调用无版本参数的 finalize。

`prepare` 在已有 STAGING 时也必须回显 `expected_stage_digest`；不能静默替换正在确认的事务。

全项目只有一个权威待审事务：章节 `STAGING.json` 与
`AUTHOR_AXIOM_STAGING.json` 使用同一 staging lock，双向互斥。`status` 的
`transaction_kind` 决定 confirm 路由；禁止同时展示或猜测两个事务。

独立 author-axiom 通道使用：

```text
canon-v3/author-axiom-proposal/v2
canon-v3/author-axiom-decision-request/v2
canon-v3/author-axiom-finalize-request/v2
```

对应 CLI 为 `author-axiom-prepare/decide/finalize/status`。它同样精确回显
stage/transaction/target/material/decision-head/finalize token，并通过同一
CURRENT CAS 发布；但 manifest 只追加 author-axiom commit，章节列表、
`latest_chapter` 与 `allowed_write_chapters` 不变。响应丢失只用原 finalize
request 做 exact retry。

legacy v1 genesis 的 recertification 不伪造章节或 axiom STAGING。`status` 必须返回
`transaction_kind=legacy_recertification`、`head_hash`、
`recertification_plan_digest`、`recertification_publish_token` 和全部逐项 cases。
先只读运行 `repair-cutover --dry-run`，然后把作者对每个 case 的 `confirm` 原样组装为：

```text
canon-v3/legacy-recertification-publish-request/v1
  expected_current_head
  detached_plan_digest
  publish_token
  decisions[]:
    canon-v3/legacy-recertification-decision/v1
    case_key / target_digest / material_digest / action=confirm
```

只有全部 cases 均由作者明确确认后，才能执行
`repair-cutover --apply --input-file <request>`。它在统一 staging lock 下重读 legacy
来源、重新编译 detached suffix，并对 CURRENT 做 CAS；partial、stale 或并发变化一律不发布。
响应丢失只允许原请求 exact retry。任何 chapter/author-axiom STAGING 存在时，
recertification 审计与 apply 都必须报告冲突，不能出现第二个权威事务。

## 负裁决与语义谱系

`omit`、`rewrite`、`correct` 不只绑定一次候选 ID。系统还以章节内容和事实语义计算
`semantic_claim_digest`，并沿活动 manifest 的祖先保存负裁决谱系。更换引文、扩大证据窗口、
重命名候选或重跑 prepare 都不得让同一被否决事实复活；语义无法可靠判等时进入人工确认。

## Proposal 与扫描

唯一事实提议流程：

```text
exact chapter binding + N-1 HEAD + active author axioms
→ data-agent extract
→ reviewer observations / scan attestations
→ data-agent assemble
→ canon-v3 prepare
```

- 每个 source 必须被 `support_map` 使用；不得加入未参与证明的 source。
- reviewer 必须收到 exact candidate draft；不得自行重写候选。
- scan attestation 必须绑定 chapter SHA、parent HEAD、candidate set、entity registry 和 active author-axiom digest。
- 模型不能写 state/entity/timeline delta、人工队列或正史。

## 设定、规划与文风

- 卷纲、章纲和剧情目标是软计划，不是已经发生的事实。
- 新增或修改世界硬规则、人物永久设定等 author axiom 时，磁盘内容先是 draft；经过 exact proposal/人工决定/finalize 后才进入 active author-axiom manifest。
- 受管 draft 只能位于 `.canon-ledger/tmp/author_axioms/*.json`，顶层只能有
  `canon-v3/author-axiom-draft/v1` 与 `author_axioms`；每项来源绑定原始 JSON
  leaf 的文件 digest、UTF-8 byte span/quote digest、pointer 与 value digest。
- active digest/records 只来自 CURRENT 可达的不可变 author-axiom commit；
  draft 未发布时不得进入 projection/query/context，发布后即使 draft 被修改或
  删除，active authority 也不回读 live 文件。
- add/update/remove 都生成 exact 人工 case；旧 active record 未提及会形成
  remove case，不能静默删除。同语义更换 source 不会绕过负裁决谱系。
- 未重新认证的设定不得进入事实查询或写作上下文。
- `设定集/文风提示词.md` 永远属于 style-only；修改它不得改变 HEAD、workflow、migration digest、projection 或人工 case。

## Legacy 边界

生产 Skill 禁止调用：`chapter-commit`、`chapter-commit --from-last-commit`、旧 `human-review resolve`、旧 `review-pipeline` 写队列、事实型 `update-state`，以及 state/index/memory/rag/entity 的事实写命令。

legacy 数据只允许迁移编译器读取或以 `legacy_read_only` 标签查询，不能成为写作上下文或发布依据。

## 报告与恢复

最终报告面向作者，至少给出：当前状态、章节、HEAD、transaction/stage（若存在）、是否需要人工、唯一恢复动作。不要输出长 JSON、traceback、token 统计或文风评分。

版本冲突、保守阻断或多一次人工不是故障；报告“内容已变化，请刷新后确认”，不得自动重试到新版本。

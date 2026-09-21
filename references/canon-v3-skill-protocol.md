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
```

已存在或已能识别的书项目再用统一 locator 固定项目根：

```bash
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" --project-root "${WORKSPACE_ROOT}" where)"
```

不得扫描缓存目录寻找另一个插件副本，不得凭当前工作目录猜项目根。

`/canon-ledger-init` 创建**全新且尚未可识别**的目标目录时是唯一定位例外：它先记录用户明示给出的绝对目标路径，不对该未建项目运行 `where/status`，也不把当前工作区中的另一本书当作目标。统一 `canon_ledger.py init <target> <title> <genre> ...` 会创建可识别骨架并尝试建立 genesis；它返回后才对 exact target 运行 locator 和 `canon-v3 status`。目标已是书项目或包含任何旧内容时不属于 clean init，必须停止并改用新的空目录。

## 唯一 Workflow Authority

识别出书项目后，无论 `.story-system/v3/CURRENT` 是否存在，所有 Skill 都先执行：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 status
```

只信返回的完整 `workflow_snapshot` 及其 `workflow_digest`；合同、旧报告、`.canon-ledger/state.json`、`index.db` 和模型口头判断都不能覆盖它。

| state | 允许的事实推进动作 |
|---|---|
| `initialization_required` + `bootstrap_mode=new_project` | 仅对已识别、无 CURRENT 的 clean skeleton 执行 `canon-v3 initialize`；统一 init 工具已成功时不重复调用 |
| `ready` | 仅允许 `allowed_write_chapters` 中的目标章进入 plan/write/staged review |
| `ready_to_finalize` | 只允许对当前 STAGING 做 exact finalize |
| `awaiting_human` | 只允许 `/canon-ledger-confirm` 处理当前事务 |
| `rewrite_required` | 修改当前章正文并从 binding 重新运行 |
| `recompile_required` | 对当前章重新 binding、extract、scan、prepare |
| `projection_rebuild_required` | 只允许 rebuild/doctor；事实视图不得回退旧索引 |
| `invalid` | 只允许 doctor、只读诊断和 style-only 操作 |

只有 `state=ready && can_write_next=true && projection_fresh=true` 才能建议开始下一章。成功建立 CURRENT 后 `bootstrap_mode=canon_v3`；`new_project` 只表示尚待 initialize 的无 HEAD 状态，不是初始化成功标志。

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

作者明确要求放弃当前 chapter/author-axiom STAGING 时，统一路由
`/canon-ledger-confirm` 处理。必须从最新 status 逐值复制
`transaction_kind + stage_digest`，展示“未发布指针将归档、对象保留、之后必须
重新 prepare”后，只调用 `canon-v3 archive-staging` 并传入 exact
`--transaction-kind` 与 `--expected-stage-digest`。摘要变化时拒绝，exact retry 幂等。禁止直接删文件或
使用兼容别名 `cancel`。

## 负裁决与语义谱系

`omit`、`rewrite`、`correct` 不只绑定一次候选 ID。系统还以章节内容和事实语义计算
`semantic_claim_digest`，并沿活动 manifest 的祖先保存负裁决谱系。更换引文、扩大证据窗口、
重命名候选或重跑 prepare 都不得让同一被否决事实复活；语义无法可靠判等时进入人工确认。

## Proposal 与扫描

### 写作上下文交付

写前一次性生成上下文页：

```text
memory-contract load-context --chapter N --all-pages --out .canon-ledger/tmp/context_pages.json
```

该命令只加载一次 runtime，返回文件路径、页数与版本绑定。逐页读取临时 JSON 的
`pages[i]`，不要每读一页重新调用 load-context。它是可覆盖的派生材料，不是 Canon。
读取前后用 status 核对 `binding.workflow_digest/head_hash`；变化时重新导出。
需要调整单页容量时附加 `--budget-tokens B`。
`B` 是交付目标；非分页接口仍返回完整事实包，`budget.hard_over_budget=true`
只表示超出目标，不改变事实 `completeness` 或 Canon workflow。

每页使用 `canon-ledger-context-page/v1`：

- `source_completeness.status=complete` 才表示源事实完整；blocked 时仍执行原恢复流程。
- 每页 `entries[].path` 是交付上下文的 JSON Pointer，`value` 保留原值；大容器可能分拆，
  单个字符串不会截断。必须结合路径读值，不能把局部字段当成独立事实。
- 精简视图把重复事实集中到 `fact_catalog`，其它位置用 `fact_ref` 引用。人物、数值、
  前后态、关系和规则正文均保留；省略的引文定位、support map 等证明元数据仍在 Canon
  和完整 N-1 snapshot 中。审查事实证据时回查完整来源，不拿精简页作证据。
- 从第一页开始读取到 `has_more=false`；所有页必须
  使用同一 `context_digest` 和 HEAD/workflow binding。最后一页单独不等于完整上下文。
- HEAD/输入变化或改预算时从第一页重新读取，
  不拼接旧页。文风单独读取，不影响事实分页。
- 页的 `delivery.over_budget=true` 表示单条值或页元数据超过目标；按模型实际容量调大
  预算并重读，不能删掉该值，也不能把容量问题上报为穿帮。

分页不会重复调用远程检索；需要相关性排序时另用可选 retrieval。完成事实阅读后，
任务书保留全局规则与本章相关约束，其余仍可精确回查。章后五维扫描仍须完整执行；
较大的 N-1 snapshot 可按实体、规则和事实类型分批读取，不能因无召回命中跳过。
旧 `--paged --cursor ...` 接口保留用于兼容单页调用，日常批量阅读使用上述一次性导出。

唯一事实提议流程：

```text
exact chapter binding + N-1 HEAD + active author axioms
→ data-agent extract
→ reviewer observations / scan attestations
→ data-agent assemble
→ canon-v3 prepare
```

- 每个 source 必须被 `support_map` 使用；不得加入未参与证明的 source。
- reviewer 必须收到 exact candidate draft 及其 validator 返回的
  `candidate_id -> candidate_digest` map；必须逐项回显，不得自行重写或重新配对候选。
- scan attestation 必须绑定 chapter SHA、parent HEAD、candidate set、entity registry 和 active author-axiom digest。
- 模型不能写 state/entity/timeline delta、人工队列或正史。
- 力量变化的 `before` 可以省略。已有状态必须使用对应稳定 `slot_id`；runtime 从 exact
  N-1 fact 或同章紧邻 prior effect 继承前态，并标记 `inherited_fields.before`。
  当前正文明确给出的 `before` 仍须有真实 source/support，不能用继承掩盖冲突。
  没有已验证前态时保留 null 并交 checkpoint 人工确认新状态，不猜旧境界，不要求正文复述。
- 同一人物对可以有多种并存关系。`relationship_key` 只是作者确认的独立关系标识，
  不建立自动关系分类：新 key 新增并存关系，同 key 更新该关系，null 保持旧的默认关系。
  有既有关系时必须明确问作者“替换还是并存”；选择并存可用现有 `correct` 补 key，
  然后重新扫描、prepare 和确认。不要要求正文复述旧关系或手改已发布事实。

章节 Agent 必须先读 `agent-schema candidate-draft|reviewer-output`，分别通过
`validate-agent-output`，再由 runtime `assemble-proposal` 计算 digests、逐项比较 reviewer
map 与 draft map 并生成最终 strict proposal。审核后互换 candidate ID 必须失败。
author-axiom 则使用 data-agent `mode=author_axiom_proposal`、
`agent-schema/validate-agent-output author-axiom-proposal` 与唯一文件
`.canon-ledger/tmp/canon_v3_author_axiom_proposal.json`。两条路径都不允许 Agent 手算
candidate/record/effect/transaction digest 或根据文档猜 strict schema。

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
- runtime 使用 `fact-boundary/v2` 同时检查章节 candidate、初始化/设定
  leaf 与 author axiom。已知文风、文笔、动机、人格、人设、成长弧等软内容即使用
  `world_rule`、`规则` 或无害 key 包装也必须拒绝；未能由闭合结构证明的自由字段是
  `ambiguous`，必须以 exact case 明确分类或改写，不能因“未命中软关键词”自动变成
  硬事实。普通 conflict/checkpoint 批准不能覆盖这条边界。
- 此处“已知软内容”指明确的偏好字段或写作/人物设计指令；标识符内部的字母组合、
  恐惧魔法、声纹锁等世界机制不能仅因命中词语直接拒绝。混合或无法判定的语义继续
  走 exact 人工分类，不能自动当作硬事实，也不绕过现有证据校验。
- author-axiom 的 key 是标识符，不凭 `fear/voice/personality` 等名字拒绝内容；
  命名或含义有歧义时展示实际 value，由作者分类。明显的写作指令仍排除；普通人物设计
  不是客观事实时选择 omit，不因进入了审核就建议批准。
- author-axiom 或章节 case 的 review material 若标记
  `fact_boundary_human_classification_required=true` 或 reason code
  `fact_boundary:human_classification_required`，`approve` 的精确含义仅是把该版本的
  proposed value 分类为客观故事事实；candidate/source/digest 变化后必须重新分类。
- 未认证的设定不得进入事实查询或写作上下文。
- `设定集/文风提示词.md` 永远属于 style-only；修改它不得改变 HEAD、workflow、projection 或人工 case。

大纲落盘后只能通过 `canon-v3 planning refresh-contracts --chapter N`刷新卷/章/审查
三份 planning-only 合同。先 dry-run 核对 source/input/head 和共同
`planning_batch_digest`；实际刷新不能同步 MASTER/设定集、创建 STAGING 或将章纲
节点放入事实 blocker。

历史章审查只使用 `historical-export --chapter N --revision R`。export 必须是当前
HEAD 祖先可达的 exact commit/transaction/decisions 与当时 axiom/registry，不读
STAGING、Git、legacy index 或未来事实。只读 audit 不生成人工决定或改 HEAD。

## 可选检索增强

`canon-v3 retrieval` 是可删除重建的召回层，不是 Canon 查询或事实证明。磁盘投影只收录
当前 `active_canon`，并同时绑定 exact HEAD、generation、workflow、author-axiom、Canon
projection 与活动 fact-set digest；STAGING、style、设定草稿、历史失效事实和 legacy RAG
一律排除。每个命中都必须按 fact digest 回查当前 active fact，返回
`authority_layer=retrieval_assist`、`resolved_against=active_canon` 和
`usable_as_canon=false`。

- `retrieval status` 纯读检查绑定；missing/stale/invalid 只表示增强不可用。
- 远程 Embedding 默认关闭。只有 `CANON_LEDGER_RETRIEVAL_REMOTE=1` 与非空 key 同时满足
  才能发送 active Canon 检索文本；项目 `.env` 的显式 `0` 覆盖全局同名 `1`。未开启、
  未配置 key 或远程失败时使用本地 BM25，不能阻断写作、finalize 或下一章。
- `retrieval rebuild` 从当前 active fact set 原子重建并遵守上述开关。`--bm25-only` 只禁止
  该次重建生成新向量，仍可复用旧向量，不能作为持久本地模式；单次搜索需本地时使用
  `mode=bm25`，整本书禁止远程时保持开关为 `0`。
- `retrieval search --input-file .canon-ledger/tmp/<name>.json` 只消费严格 v1 请求。
  历史 as-of 查询从对应 v3 快照在内存做 BM25，不能使用当前索引泄漏未来事实。
- 检索可用于找候选事实或缩小阅读范围；事实判断仍使用返回的 `active_fact` 或更窄的
  v3 query facade。无命中不等于事实不存在，任何命中也不能代替完整五维扫描。
- 旧 `rag`、`.canon-ledger/vectors.db` 和 rerank 配置不参与 v3 检索权威。

## Legacy 边界

生产 Skill 禁止调用：`chapter-commit`、`chapter-commit --from-last-commit`、旧 `human-review resolve`、旧 `review-pipeline` 写队列、事实型 `update-state`、公开
`story-events`，以及全部 state/index/memory/rag/entity adapters。这些 adapters 即使执行
查询也可能建库、读取失绑事件或写 observation。

退役参数 `--legacy-read-only` 不开放 adapter 查询。旧数据不能成为写作上下文或发布依据，也没有导入当前产品的迁移入口。

## 派生工件与 CLI capability

可信 `canon_ledger.py` 不是任意项目文件写权限。正文 binding 与 N-1 快照只能分别
写入 `.canon-ledger/tmp/chapter_binding.json` 和
`.canon-ledger/tmp/asof_snapshot.json`；批量上下文只可写
`.canon-ledger/tmp/context_pages.json`。其它 `--out`、CURRENT/STAGING/objects、正文、
项目外路径或符号链接目标必须拒绝。prepare/decide/finalize 也只消费各 Skill 约定的
项目内 tmp JSON，不读取 raw v3 object 或 legacy cache 猜协议。

`init` target 不得位于已有书项目、插件根、`.story-system`、`.canon-ledger`、`.cursor`
或 `.git` 内部。Hook 负责提前拒绝，CLI/runtime 必须再次执行同一 capability/path 校验；
不能因 Hook 缺失而获得更宽权限。

`memory-contract query-*`、`get-open-loops|get-obligations|get-timeline` 也属于事实读取面。
当 HEAD/projection 不可用、初始化未完成、历史来源无效或读中 workflow 变化时必须非零退出并
返回 `usable_for_writing=false`；禁止用空数组、`not_found` 或空时间线伪装成“当前没有事实”。

## 报告与恢复

最终报告面向作者，至少给出：当前状态、章节、HEAD、transaction/stage（若存在）、是否需要人工、唯一恢复动作。不要输出长 JSON、traceback、token 统计或文风评分。

版本冲突、保守阻断或多一次人工不是故障；报告“内容已变化，请刷新后确认”，不得自动重试到新版本。

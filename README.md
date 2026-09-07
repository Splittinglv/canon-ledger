# 叙典 CanonLedger

[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-9.1.0-brightgreen.svg)](.cursor-plugin/plugin.json)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)

记住故事事实，不替你决定文风。

叙典是面向长篇小说的长期一致性插件。它在每章完成后核对“什么已经发生、谁知道什么、谁在哪里、物品在谁手里、时间和规则是否穿帮”，并把后续章节真正需要依赖的事实写入可验证正史。

具体的文风、文笔、视角、口吻、节奏和写作偏好由作者与当前模型自定义。你可以把整本书的长期偏好写进 `设定集/文风提示词.md`，也可以每章临时覆盖；插件不会把这些偏好变成强制事实检查。

## 产品边界

| 默认会做 | 默认不会做 |
|---|---|
| 核对作者硬设定、时间线和跨章事实 | 给文笔、AI 味、节奏或爽点评分 |
| 维护人物状态、关系、知识、在场和物品持有 | 强制某种网文腔、句式或章长 |
| 记录伏笔/开放问题和承诺 | 把人物动机或剧情取舍判成穿帮 |
| 关键正史节点和实质歧义交作者确认 | 因极低概率、无证据猜测阻断写作 |
| 章纲作为剧情方向并单独报告 | 默认强制章纲完成度 |

误判偏保守或多一次人工检查不是系统故障；没有证据的事实进入正史、关键事实绕过确认、决定错绑、重放残留和 HEAD/投影分裂才是必须阻止的问题。

## Canon v3：唯一事实事务链

模型不能直接写状态、实体、时间线或人工队列。每章只有一条生产写链：

```text
正文 + 作者硬设定
  -> FactCandidate（逐字段 SourceRef + support_map）
  -> fact-boundary/v2（objective / advisory / ambiguous）
  -> ReviewObservation + 完整 ScanAttestation
  -> canon-v3 prepare
  -> 必要时 canon-v3 decide
  -> canon-v3 finalize
  -> immutable commit/manifest
  -> 原子切换 .story-system/v3/CURRENT
  -> 按同一 HEAD 重建投影
```

data-agent 和 reviewer 不再手算 digest 或手拼最终 proposal。运行时产物分为：

```text
.canon-ledger/tmp/canon_v3_candidate_draft.json
.canon-ledger/tmp/canon_v3_reviewer_output.json
.canon-ledger/tmp/canon_v3_proposal.json   # 只由 runtime assemble
```

调用方先从 `agent-schema` 读取当前 JSON Schema，分别执行
`validate-agent-output candidate-draft|reviewer-output`，再由 `assemble-proposal`
计算 candidate digests 并产生严格 `canon-v3/proposal-batch/v2`。Reviewer output v3
必须逐项回显 runtime 的 `candidate_id -> candidate_digest` map；assemble 与 exact draft
逐项比较，审核后互换 candidate ID 也会失败。最终
proposal 绑定 parent HEAD、workflow、entity registry、active author axioms 和已有
STAGING 版本；Agent 不能绕过 runtime helper 直接填写摘要。

### 证据要求

每个候选事实的每个非空字段都必须由 `support_map` 绑定到真实来源：

- `manuscript_span`：当前章节 SHA-256、精确 UTF-8 byte `[start,end)`、逐字 quote 和 quote SHA-256。
- `author_axiom`：作者拥有的 JSON 设定文件 SHA-256、精确 JSON Pointer、叶值和 value SHA-256。

人物/组织、物品、地点分别使用 `actor`、`item`、`location` 身份命名空间。首次身份或新别名要经人工确认；同一命名空间也允许多个同名实例，必须通过 `new_instance/link_to/identity_links` 人工确认新建或消歧，绝不默认取第一个。同名人物不会自动改写同名地点或物品。重写旧章时只使用该章 N-1 已存在的身份注册，未来章节的别名不会倒灌。

只有“引文在正文里出现”并不足以入正史；角色、物品、地点、持有人、前后状态等 claim 字段必须由它绑定的来源实际支持。无法可靠判断时转人工，不能猜。

模型不再提交 `accepted_events`、`state_deltas`、`entity_deltas` 或 `timeline_events`。运行时只从通过证据验证和人工策略的 typed candidates 派生 CanonEffects。

schema 合法不等于已获事实准入。已知文风、文笔、节奏、人物动机、人格、人设、
成长弧和一般因果直接留在 advisory/style；自由状态、关系或世界规则如果不能由闭合结构
证明，会生成与 exact candidate/record digest 绑定的人工分类 case。没有命中软关键词
绝不自动等于硬事实，旧版本 active fact 缺少当前 policy 证明时项目保持只读。

所有可变事实统一经过 Active Slot Registry：跨章更新绑定 exact `prior_fact_digest`，同章连续变化绑定 `prior_effect_id`；承诺兑现、伏笔关闭必须命中 active prior。状态字段、知识命题与正文显示词分离；新承诺、新伏笔、新时间事件和规则违反按 candidate+evidence 生成独立实例，因此相同措辞不会相互覆盖。`omit/rewrite/correct` 的负裁决会随不可变 commit/manifest 谱系保留，重新 prepare 不能让它们消失。

### 完整事实扫描

进入 `prepare` 前，扫描证明必须绑定当前正文 SHA，并覆盖：

```text
setting, timeline, continuity, character, logic
```

同时必须列出全部 exact candidate digests。覆盖不完整时 fail closed；不能把 partial scan 伪装为“未发现问题”。正文明示发生、后续必须记住的事实不得静默遗漏。

## 人工确认

运行时会把模型观察与内置策略合并，并始终取最强级别。模型可以请求更多审核，但不能降低 checkpoint。

### checkpoint

关键长期节点，例如核心角色永久状态、重大关系、世界硬规则、永久力量变化、关键物品、核心秘密、重大时间变化、重大承诺/开放问题、retcon 和卷末快照。

只允许：

- `approve`：批准当前 exact candidate。
- `rewrite`：修改正文并完整重跑。

### ambiguity

有正文锚点、会影响长期事实，但系统无法唯一解释的候选。

允许：

- `approve`：接受当前候选。
- `omit`：本次不入正史；只用于真正歧义，不能静默丢弃正文明确发生的关键事实。
- `correct`：作者提供同 candidate ID 的完整修订候选，随后重新做证据校验、五维扫描、checkpoint 和 prepare。
- `rewrite`：修改正文并完整重跑。

不提供通用 `replace`。人工决定请求必须回显作者看到的 `stage_digest`、transaction、target、material、decision head，以及章节 SHA、candidate/effect/source、父 HEAD、既有事实和 policy；任一变化都会拒绝旧操作，不能把旧选择转接到新 STAGING。

`omit/rewrite/correct` 同时保存与证据呈现无关的 `semantic_claim_digest`。同一事实仅换 source、扩大引文或删除 observation 后不能自动复活；需要作者显式重新考虑。

日常命令：

```text
/canon-ledger-confirm 12
```

确认 Skill 按当前唯一事务分派：章节与 author-axiom case 先提交 exact
`decide`，达到 `ready_to_finalize` 后再提交 exact `finalize`。
`correct` 和 `rewrite` 都不会在旧 transaction 上直接发布。

若作者明确放弃当前未发布事务，不直接删除 STAGING 或不可变对象。先从
status 复制 `transaction_kind + stage_digest`，取得作者确认后执行：

```text
canon-v3 archive-staging --transaction-kind chapter|author_axiom \
  --expected-stage-digest <sha256>
```

指针会移入非权威 `staging-archive`，transaction/decision 对象保留。摘要变化时拒绝，
exact retry 幂等；之后必须重新 prepare，旧 finalize 不能复活。

## Workflow snapshot 是唯一门禁

CLI、write gate、报告、context、Skills 和 Dashboard 读取同一个 `canon-v3/workflow-snapshot/v2` 及其 `workflow_digest`。无 CURRENT 时也不得回落 legacy：

| state | 含义 | 恢复动作 |
|---|---|---|
| `ready` | CURRENT 与投影一致 | `can_write_next=true` 时可写下一章 |
| `ready_to_finalize` | transaction 已满足发布条件 | 由 `/canon-ledger-confirm` 组装 exact finalize |
| `awaiting_human` | 有 required case，HEAD 未改变 | 当场 `/canon-ledger-confirm N` |
| `rewrite_required` | 已确认事实冲突或作者选 rewrite | 修改本章并完整重跑 |
| `recompile_required` | 正文、HEAD 或候选修订变化 | 重新 binding、scan、prepare |
| `projection_rebuild_required` | 正史已发布但读模型未追上 | `canon-v3 rebuild-projection` |
| `initialization_required` | 新项目骨架尚未建立 Canon genesis | 执行同一 snapshot 的 `initialize_v3` |
| `invalid` | 内容寻址对象或引用校验失败 | 停止写作并体检 |

只有 `state=ready`、`can_write_next=true` 且 projection fresh 才能继续下一章。`ready_to_finalize`、暂存 transaction、合同就绪或旧报告里的 blocking 数量都不表示完成。

## 日常使用

### 开新书

```text
/canon-ledger-init
/canon-ledger-plan 1
/canon-ledger-write 1
```

新项目骨架完成后会从 closed `MASTER_SETTING.initial_canon` 创建
`canon-v3/genesis/v1` 原生 genesis。默认只接收
明确身份以及会约束后文的世界、规则、物品能力等硬事实；人物欲望、缺陷、人设类型、
剧情定位和生成的设定模板仍是软设计，不自动进入 Canon。后续要把某项设计变成长期硬设定，
必须走 managed author-axiom 的逐项人工决定：

```text
/canon-ledger-plan
→ 生成 managed author-axiom draft
→ author-axiom-prepare
→ /canon-ledger-confirm 逐项决定
→ author-axiom-finalize
```

`canon-v3 initialize` 只创建全新项目的 genesis；已有 HEAD 时不能用它保存或更新硬设定。
`/canon-ledger-init` 同样只接受不存在或严格空的目标；非空目录、已有 v3、
非空或 malformed 目标、symlink 目标，以及已有书项目、插件根、`.story-system`、
`.canon-ledger`、`.cursor` 或 `.git` 内部目标都在首次写入前拒绝。新项目在同级临时目录完整
构建并验证后才发布，init 不再兼任升级或就地修复。

### 写一章

```text
/canon-ledger-write 12
```

流程：整理 N-1 事实 → 按作者/模型文风起草 → 固化正文 binding → data-agent 提候选 → reviewer 五维事实扫描 → `prepare` → 当场人工确认或改正文 → `finalize` → 验证 ready。

### 规划与长期硬设定

卷纲、章纲和剧情目标是软计划，不表示事件已经发生。规划过程中新增、修改或删除世界规则、角色永久设定等硬内容时，先保存为 managed author-axiom draft，再执行 author-axiom prepare/decide/finalize；完成前，query 和写作上下文继续使用上一个 active axiom digest。这样 `/canon-ledger-plan` 不会成为第二条事实写入路径。

大纲落盘后，`canon-v3 planning refresh-contracts --chapter N --dry-run`
只读生成三份 planning-only 合同及共同 `planning_batch_digest`；去掉
`--dry-run` 后也只能写卷/章/审查合同。它不同步 MASTER/设定集，不读 legacy
state 决定卷号，不创建 STAGING，并将 review 合同中的大纲履约规则固定为
advisory。三个文件逐文件原子替换、异常时尝试回滚；共同 batch digest 用于检测
进程被杀后的混合残留，不声称跨三文件的文件系统事务。

### 审计历史章节

旧章或章节范围默认只读，使用
`canon-v3 historical-export --chapter N --revision R`。导出只沿审计开始时
CURRENT 的 manifest 祖先链读取 exact commit/transaction/decisions、当时的
author axioms 与 entity registry，不读 STAGING、legacy index、Git 或未来事实。发布章节
在 HEAD CAS 前将其 exact bound manuscript 幂等写入内容寻址 revision archive；
若历史字节仍不可用，导出明确返回 `source_unavailable`，不从引文或模型记忆重建。
只有作者明确选择 revise 才进入新的完整事实事务。

### 自定义长期文风

编辑 `设定集/文风提示词.md`，例如：

- 视角和人称；
- 句长、对话习惯和禁忌修辞；
- 希望接近的作品气质；
- 全书长期写作偏好。

优先级是：本轮用户要求 > 全书文风提示词 > 当前模型默认。这个文件不进入 Canon 事实快照，不触发一致性审核。也可以用 `/canon-ledger-learn` 追加长期偏好。

## CLI

统一入口：

```bash
python3 -X utf8 "<PLUGIN_ROOT>/scripts/canon_ledger.py" \
  --project-root "<PROJECT_ROOT>" canon-v3 <action>
```

常用 action：

```text
initialize
status
prepare --input-file .canon-ledger/tmp/canon_v3_proposal.json
decide --input-file .canon-ledger/tmp/canon_v3_decisions.json
finalize --input-file .canon-ledger/tmp/canon_v3_finalize.json
archive-staging --transaction-kind chapter|author_axiom --expected-stage-digest <sha256>
author-axiom-prepare --input-file .canon-ledger/tmp/canon_v3_author_axiom_proposal.json
author-axiom-decide --input-file .canon-ledger/tmp/canon_v3_author_axiom_decisions.json
author-axiom-finalize --input-file .canon-ledger/tmp/canon_v3_author_axiom_finalize.json
author-axiom-status
author-axioms
query snapshot|entity-state|relationships
agent-schema candidate-draft|reviewer-output|proposal-batch|author-axiom-proposal
validate-agent-output candidate-draft|reviewer-output|author-axiom-proposal
assemble-proposal
planning refresh-contracts --chapter N [--dry-run]
historical-export --chapter N --revision R [--commit-hash <sha256>]
history  # 与 query snapshot 同一净化、HEAD-bound 公开视图
rebuild-projection
retrieval status
retrieval rebuild [--bm25-only]
retrieval search --input-file .canon-ledger/tmp/retrieval_query.json
```

作者流程的派生文件只写固定项目角色：chapter binding 写
`.canon-ledger/tmp/chapter_binding.json`，N-1 snapshot 写
`.canon-ledger/tmp/asof_snapshot.json`。其它 `--out`、CURRENT/STAGING/objects、正文或
项目外路径都会由 CLI 和 Hook 同时拒绝；retrieval rebuild 只原子替换固定的
`.story-system/v3/projections/retrieval.sqlite3`。公开 `story-events` 已退役；活动事件事实使用
`canon-v3 query/history`；旧 event 不进入当前产品的事实读面。

v3 不可变对象、活动 manifest、CURRENT 和 projection binding 位于 `.story-system/v3/`。派生投影可以删除重建，不能反向成为正史来源。

## 可选向量召回（非正史）

长篇小说的事实很多时，`canon-v3 retrieval` 可以先找出可能相关的 active Canon 事实，
供 Context、Reviewer 或宽泛查询定位。它不是第二套记忆，更不是事实权威：

- 只索引当前 `active_canon`；STAGING、文风、设定草稿、已失效历史和旧
  `.canon-ledger/vectors.db` 永远不进入 v3 检索。
- 索引同时绑定 exact HEAD、generation、workflow、author-axiom、Canon projection 和
  active fact-set digest。HEAD 前进、事实删除/替换或索引被改动后，旧行立即失效。
- 每个命中返回前都会按 digest 回查当前 active fact，并标记
  `usable_as_canon=false`。相似度只是定位线索；无命中也不表示事实不存在。
- 历史 `as_of_chapter` 查询从对应历史快照做内存 BM25，不会拿当前向量泄漏未来事实。
- 远程 Embedding 默认关闭；仅存在 Embedding key 不会发送数据。未显式开启、没有 key、
  远程请求失败或索引 missing/stale/invalid 时，都使用当前 Canon 的本地 BM25。检索问题
  不阻断起草、人工确认、finalize 或下一章。

状态与重建：

```bash
python3 -X utf8 "<PLUGIN_ROOT>/scripts/canon_ledger.py" \
  --project-root "<PROJECT_ROOT>" canon-v3 retrieval status
python3 -X utf8 "<PLUGIN_ROOT>/scripts/canon_ledger.py" \
  --project-root "<PROJECT_ROOT>" canon-v3 retrieval rebuild
```

搜索请求必须是项目内 `.canon-ledger/tmp/*.json` 的严格 JSON，例如：

```json
{
  "schema_version": "canon-v3/retrieval-search-request/v1",
  "query": "林舟是否知道密门在钟楼下",
  "as_of_chapter": 18,
  "top_k": 8,
  "mode": "auto",
  "categories": []
}
```

默认配置是持久的本地模式。只有确认允许 active Canon 检索文本离开本机后，才在书项目
`.env` 同时配置：

```dotenv
CANON_LEDGER_RETRIEVAL_REMOTE=1
EMBED_BASE_URL=https://example.com/v1
EMBED_MODEL=your-embedding-model
EMBED_API_KEY=your-secret-key
```

远程端点必须兼容 OpenAI `/v1/embeddings` 请求/响应。v3 只有在远程开关为真且 key 非空时
才会发送重建文本或搜索查询；仅配置 key 不会开启远程。目标书项目 `.env` 中的
`CANON_LEDGER_RETRIEVAL_REMOTE=0` 会覆盖同名的进程或全局 `=1`，可可靠地把单本书锁定为
本地模式。端点、模型和 key 保持既有配置优先级：已定义的进程/全局环境值不会被目标项目
`.env` 覆盖，请用 `doctor` 或 Dashboard 核对实际生效值。不要提交真实 key。

章节或 author axiom 发布后，Write/Confirm 会自动尝试 `retrieval rebuild`；该重建同样遵守
远程开关。需要区分三个控制项：

- `CANON_LEDGER_RETRIEVAL_REMOTE=0`：持久禁止 v3 发起远程 Embedding，`auto` 搜索也只走
  BM25；这是整本书的本地隐私保证。
- 搜索请求的 `"mode": "bm25"`：仅保证这一次搜索不生成远程查询向量。
- `retrieval rebuild --bm25-only`：仅禁止该次重建生成新文档向量；为节省成本，它仍可复用
  索引内已有的同模型向量，也不会改变以后搜索的配置，因此不能代替持久隐私开关。

启用远程后，请按作品保密要求选择服务。v3 当前不使用 Rerank。

## 安装

需要 Python 3.10+ 和 Cursor。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r scripts/requirements.txt
python -m pip install -r dashboard/requirements.txt
```

开发或发布 Dashboard 还需要 Node.js 18+ 与 npm；干净 checkout 先安装锁定的前端依赖：

```bash
npm --prefix dashboard/frontend ci
```

本地开发推荐把仓库链接到 Cursor 插件目录：

```bash
mkdir -p ~/.cursor/plugins/local
ln -s "/absolute/path/to/canon-ledger" ~/.cursor/plugins/local/canon-ledger
```

然后执行 **Developer: Reload Window**。也可以在 Cursor **Settings → Plugins → 添加本地目录** 中选择包含 `.cursor-plugin/marketplace.json` 的仓库根目录。

插件按顺序选择显式 `CANON_LEDGER_PYTHON`、插件目录 `.venv`、`~/.cursor/canon-ledger/.venv`，最后才检查系统解释器；缺依赖时受保护写入会停止，不会静默换成不完整环境。

### 开发验收

默认 `pytest` 运行当前 Canon v3 产品验收。依赖已删除 v2 writer/replay 的冻结规格由固定
清单排除，不会为了通过它们而恢复 legacy 写入口；范围与显式审计方式见
[当前产品测试与 retired v2 规格](references/testing-current-vs-retired-v2.md)。

当前发布至少执行：

```bash
npm --prefix dashboard/frontend ci
python scripts/run_acceptance.py --mode full
python scripts/sync_plugin_version.py --check --expected-version 9.1.0
python scripts/validate_release_notes.py --version 9.1.0 --previous-tag v9.0.0 --format json
```

此外要对全部 9 个 Skill 运行 `skill-creator` 的 `quick_validate.py`。`full`
已包含全部当前 pytest testpaths 与 selection audit、真实行为链、文档链接、严格插件包、
Dashboard 测试/构建；Windows 使用 `scripts/run_tests.ps1 -Mode full`，POSIX 使用
`scripts/run_tests.sh full`。版本号变化时使用 manifest
作为唯一版本源同步命令，不把测试硬编码当成第二版本源。

### 工作区

插件仓库和书稿分开。打开书的父目录作为工作区：

```text
workspace/
├── .cursor/canon-ledger-current-project
└── 你的书名/
    ├── .story-system/
    │   └── v3/
    ├── .canon-ledger/
    ├── 正文/
    ├── 大纲/
    ├── 设定集/
    │   └── 文风提示词.md
    └── 审查报告/
```

## 版本

| 版本 | 说明 |
|------|------|
| **v9.1.0 (当前)** | 新增 HEAD-bound 可选向量/BM25 召回；命中回查 active Canon，缺失、过期或远程失败不阻断写作。 |
| **v9.0.0** | 统一事实准入证明、存量重认证、CLI/Hook capability 与完整 HEAD-bound 公共读取面；退役无绑定 legacy story-events。 |
| **v8.1.0** | 只守长期事实边界；新增 clean-only init、统一 Agent/人工协议、exact STAGING 恢复、planning/history facade、v8 软事实分析与 HEAD-bound Dashboard/验收。 |
| **v8.0.0** | Canon v3 统一正史写入、精确人工决定、managed author-axiom、fail-closed 迁移/重新认证与 HEAD-bound 投影。 |
| **v7.2.0** | 堵住正史静默改写与前缀脱节；伏笔、关系和知识边界绑定正文证据。 |
| **v7.1.0** | 新增对话式人工确认，并收紧章节提交与确认链。 |
| **v7.0.2** | 收口残留写法口径，同时保留事实型设定。 |
| **v7.0.1** | 仓库更名为 Splittinglv/canon-ledger，并补充 AI 辅助开发说明。 |
| **v7.0.0** | 更名为叙典 CanonLedger，启用独立命令、运行目录与产品身份。 |
| **v6.2.2** | 长期一致性真源可重建，文风由作者或模型决定。 |
| **v6.2.1** | 上游引擎 v6.2.1 的 Cursor 本地插件基线。 |

## 项目与许可

叙典由 Splittinglv 发起并发布，仓库为 [Splittinglv/canon-ledger](https://github.com/Splittinglv/canon-ledger)。代码、测试和文档大量使用生成式 AI 辅助完成。

代码最初从 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) v6.2.1 导入，并继续按 GNU GPL v3 发布。派生范围和基线见 [ATTRIBUTION.md](ATTRIBUTION.md)，非官方关系与许可声明见 [NOTICE.md](NOTICE.md) 和 [LICENSE](LICENSE)。

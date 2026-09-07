# Canon v3：长期一致性事务架构

状态：9.1 HEAD-bound 可选检索与公开事实边界

## 产品边界

插件只把“下一章必须能够依赖的长期事实”当作强约束。文风、文笔、节奏、审美与写作偏好可以被作者或模型自定义和长期保存，但不进入事实正史，也不作为强制检查项。

章节完成后只检查事实连续性：已经发生或未发生的事、角色在场与否、物品归属、角色知识边界、时间线、人物永久状态和世界规则。模型无法可靠判断且会影响长期事实的项目进入人工审核。误报、偏保守或多一次人工检查不属于 P1。

## P1 判定

仅下列情况是 P1：

1. 没有逐字段证据或精确人工批准的事实进入活动正史。
2. 已确认冲突或关键事实绕过了要求的人工批准。
3. 人工决定被应用到错误的章节、候选事实、正文版本、证据、父 HEAD 或既有事实版本。
4. 替换、重放或重新裁决后留下残余事实，或者同一事务重放不是幂等的。
5. 正史、历史、投影与写作上下文已经分裂，系统仍允许开始下一章。
6. 正文明示发生且后文必须记住的事实被静默丢弃。

下列情况默认不是 P1：多拦截、多一项人工审核、保守的覆盖率、重复提示、报告措辞不够精炼、极低概率但没有进入活动正史的模型误判。

## 唯一写入链

```text
正文/设定 + 模型提议
        |
        v
Typed Canon Compiler（唯一证据与策略权威）
        |
        v
Prepared Transaction
  conflict -> rewrite_required
  required -> awaiting_human
  otherwise -> ready_to_finalize
        |
        v
必要时：精确绑定的 Human Decisions
        |
        v
从原始输入完整重编译 / finalize
        |
        v
不可变 Commit Revision + Active Manifest
        |
        v
原子切换 Canon HEAD
        |
        v
按 HEAD 重建或校验投影 / workflow_snapshot
```

任何模型产物都只是提议。模型不能直接写状态差量、实体差量、时间线事件、审核队列或正史记录。

## 核心不变量

### 事实来源

每个事实效果的每个语义字段必须由以下来源之一支持：

- `manuscript_span`：章节 SHA-256、UTF-8 字节区间、引文 SHA-256。
- `author_axiom`：设定文件 SHA-256、JSON Pointer、值 SHA-256。
- 与上述精确候选绑定的有效人工批准。

只证明“引文存在”不等于证明候选事实。编译器必须校验字段支持映射；参与者、名称、地点、物品和持有人必须能从证据或已批准别名中解析。语义无法机器证明时升级人工审核，不得猜测。

### 审核策略

运行时策略取所有触发器中的最强级别；模型只能提高、不能降低。

- checkpoint 只允许 `approve` 或 `rewrite`。
- ambiguity 允许 `approve`、`omit`、`correct` 或 `rewrite`。
- `correct` 产生新候选修订并重新执行证据、冲突和 checkpoint 判断。
- 不提供通用 `replace`，避免更换身份后留下旧事实。
- 已经在正文中明确发生且必须进入长期记忆的事实，不提供静默 `ignore`。

一个被接受的 commit 不得包含 pending 项。未解决的 required 项只能停留在 staging transaction，不能切换 HEAD，也不能开始下一章。

### 决定绑定

人工决定的摘要至少绑定：

- 章节号与章节 SHA-256；
- 候选修订 ID、候选摘要与每个效果摘要；
- 所有证据与来源摘要；
- 父 Canon HEAD；
- 引用的既有事实摘要；
- 策略版本；
- action 与 replacement/correction 摘要。
- 作者实际看到的 `stage_digest`、transaction、case target 与 review material 摘要；
- 当前 decision head，以及发布时的 exact `finalize_token`。

任一绑定变化都会使决定失效，不能按相似文本、数组位置、case key 或仅 candidate ID 复用。prepare 替换已有 STAGING、decide 和 finalize 都采用 compare-and-apply；旧操作不能自动转接到新事务。

### Active Slot Registry 与状态过渡

每个 prepared effect 都必须先在精确 parent HEAD 上经过统一的 Active Slot Registry，而不是由各 fact kind 自己猜“要替换谁”：

- 新槽：`prior_fact_digest=null`；调用方不能提供未知的任意 slot。
- 跨章更新：效果写入 exact `prior_fact_digest`，review material 展示该完整旧记录。
- 同章连续过渡：后一个效果写入 exact `prior_effect_id`，形成按正文 byte order 的链；例如“先获得再转交”。
- `before|from_holder` 与当前值不一致时进入人工 checkpoint，材料同时展示旧值；允许作者确认“存在未记录过渡”，这是产品选择，不是机器静默覆盖。
- terminal（承诺兑现、伏笔关闭）必须命中 active prior；已关闭、未知或错误 slot 直接拒绝。

状态字段、规则、知识命题等“显示词可能变化”的事实将 `slot_id/canonical_field` 与正文措辞分离。正文只说“这段记忆/该承诺/此规则”时，可从 exact prior 继承展示字段；`inherited_fields` 明确记录继承来源，不能伪装成本章证据。

承诺、开放问题、时间事件和规则违反是 occurrence 实例。新实例的 slot 由 exact candidate+evidence 摘要生成，相同措辞不会互相覆盖；更新或 terminal 才复制 prior slot。规则违反引用持续规则的 `rule_slot_id`，但自身使用独立事实槽，既不删除规则，也不覆盖另一次违反。

### 实体实例与同名消歧

身份注册按 `actor|item|location` namespace 隔离，并允许同一 namespace 内同名多实例。裸别名只有在唯一时才能自动解析：

- 新称呼链接旧实体：`link_to`；
- 明确是另一个同名实例：`new_instance=true`；
- 其它事实引用歧义名字：候选顶层 `identity_links` 选择 parent canonical ID，或用 `candidate:<id>` 指向同批新注册。

所有备选实体的 exact prior facts 都进入人工材料。未显式消歧的同名引用不能发布；人工批准一次普通事实也不能偷偷把第一个同名对象当作第二个。

### 负裁决谱系

`omit|rewrite|correct` 是不可变 tombstone，不会因重新 prepare、移除 observation、章节 revision 或早章重写截断后缀而消失。除了 exact candidate digest，谱系还保存 `chapter_digest + semantic_claim_digest`；换 source、扩大引文或改 candidate ID 不能复活同一事实。STAGING 与 commit 保存 lineage decision hashes，prepare 沿 active manifest 的全部祖先收集目标章谱系：

- 同一正文摘要下，OMIT 的原候选不能复活；
- REWRITE 必须改变正文 bytes；只换一个模型候选不算改稿；
- CORRECT 必须提交人工指定的 exact replacement；
- 正文摘要变化后重新扫描、重新人工判断。

public seal 只能消费当前 authoritative STAGING 的完整 transaction、latest decision set、lineage set 和 effects；“旧批准子集自身看起来可通过”不构成发布证明。

### 不可变与原子发布

transactions、decisions、commits、manifests 使用内容寻址并且只写一次。活动历史只折叠 HEAD 指向的 manifest。所有派生投影必须标记同一个确切 HEAD；投影可以删除重建，不能反向成为正史来源。

`.story-system/v3/CURRENT` 是唯一活动指针。发布顺序为：先写并校验所有不可变对象，再以 compare-and-swap 方式原子替换 CURRENT。崩溃发生在切换前时旧 HEAD 仍完整；切换后可以仅凭新 manifest 重建全部派生数据。

章节 finalize 在 HEAD CAS 前还会将 exact chapter binding 指向的当前正文字节以
SHA-256 为名 no-clobber 写入 `.story-system/v3/revision-archive/manuscripts/`。归档失败
阻止 seal；CAS 竞争失败只可能留下无权威的内容寻址 blob，重试幂等。这保证旧章
审计有 exact 源，同时 archive 仍不是 Canon 事实权威。

## 工作流状态

gate、报告、CLI、context、Skills 与 dashboard 只能读取同一个带 `workflow_digest` 的 `workflow_snapshot`。识别出书项目后，无论 CURRENT 是否存在都走该状态机：

- `ready`：当前 HEAD、正文绑定与投影一致，可写下一章。
- `ready_to_finalize`：当前 STAGING 没有未解决 required case，只能消费 exact finalize request；尚未发布到 HEAD。
- `awaiting_human`：存在 required 决策，HEAD 未变化。
- `rewrite_required`：确认存在正文穿帮，必须先改稿。
- `recompile_required`：正文、HEAD、证据或人工 correction 已使当前编译绑定失效，必须重新 binding/extract/scan/prepare。
- `projection_rebuild_required`：HEAD 已发布但派生投影未追上，禁止下一章。
- `initialization_required`：新项目骨架尚未建立原生 Canon genesis。
- `invalid`：不可变对象、引用或摘要校验失败。

`bootstrap_mode` 只区分尚待初始化的 `new_project` 与已建立 CURRENT 的 `canon_v3`。
`initialization_required` 必须保持 `can_write_next=false`；规划合同就绪不能覆盖 Canon 状态。

独立 review 不再调用 legacy `review-pipeline/update-state`。下一章草稿复用 extract→reviewer→assemble→prepare；历史范围默认 audit-only。队列完全由 compiler 从 prepared transaction 推导。

## 公开读面、规划与历史审计

`canon-v3 query snapshot|entity-state|relationships` 和兼容名 `history` 都经过同一
HEAD-bound facade。它们要求 fresh projection，在查询前后复查 HEAD/generation，任何
invalid source 直接 fail closed。实体同名时返回 `ambiguous + requires_human_resolution`，
不把多个匹配伪装成空状态。旧 state/index/RAG/memory/entity adapters 即使名为读取也
可能初始化数据库或写 observation，因此生产入口一律拒绝，`--legacy-read-only` 只是
保留稳定错误的退役参数。所有 status/query/Doctor/Dashboard GET 路径不创建 lock、目录或其它文件。

Agent 交界使用运行时 JSON Schema：章节是 candidate-draft → reviewer-output →
runtime assemble proposal；author axiom 使用独立 `author-axiom-proposal` schema 与 validator。
Reviewer output v3 逐项绑定 validator 返回的 `candidate_id -> candidate_digest`，assemble
与 exact draft map 比较后才生成 proposal；digest 集合相同但 ID 被互换也会失败。
Agent 不计算权威 digest，也不根据文档猜 strict payload。
`fact-boundary/v2` 是章节 candidate、setting/initial 与 author-axiom 的
共同准入层。已知 advisory 不能生成 effect；闭合结构可证明的客观事实直接准入；自由
字段、关系或规则语义为 ambiguous，必须绑定 exact candidate/record 的人工分类 case。
未命中软关键词不再等于硬事实，普通 conflict/checkpoint approve 也不能绕过分类。
准入在 validator/prepare/compiler、workflow 与 public read 重复验证，不能在 projection 中静默略过。

`planning refresh-contracts` 仅在 ready/fresh 下从已落盘大纲生成卷/章/审查三份
planning-only JSON。输入、HEAD 和共同 `planning_batch_digest` 使混合版本可检测；它不写
MASTER、设定集、STAGING 或 Canon，也不把章纲履约放入 review blocker。

`historical-export` 只沿审计开始时 CURRENT 的 manifest 祖先链导出 exact
commit/transaction/decision/lineage、当时候选/effects、author axioms、entity registry 和
revision source。它不读 STAGING、Git、legacy index 或未来事实，也不生成决定或写 HEAD。

`canon-v3 retrieval` 位于公开 Canon query 之后，只承担候选召回：它把当前
`active_canon` 复制进可删除的 SQLite projection，并绑定 HEAD/generation、workflow、
author-axiom、Canon projection 与 active fact-set digest。搜索返回前再次按 fact digest
解析 current active fact；任何失绑、篡改或 HEAD 竞争都丢弃索引并降级到当前快照的
内存 BM25。历史 as-of 永远从该历史快照检索，不能读当前向量。Embedding 是可选外部增强，
默认关闭；只有 `CANON_LEDGER_RETRIEVAL_REMOTE=1` 与非空 key 同时满足才允许远程请求，
项目 `.env` 的显式 `0` 覆盖全局同名 `1`。缺 key、部分向量或远程失败不进入 workflow
blocker。STAGING、style、legacy vectors 和历史失效事实不入库，检索命中及无命中都不
构成事实证明。

## STAGING 放弃与恢复

未发布事务只能在作者明确要求放弃后，用最新 status 的
`transaction_kind + stage_digest` 执行 `archive-staging`。服务在共享 staging lock 内复查
exact digest，将 pointer 移入非权威 archive，保留不可变 transaction/decisions。摘要冲突
不移动，exact replay 幂等。归档后旧 finalize 必须失败，后续事务重新 prepare。

## Skill 与 Author Axiom 边界

9 个 Canon Skills 都是本协议的调用方，不能复制另一套状态判断。`init` 对作者确认的 `MASTER_SETTING.initial_canon` 做一次性净化和 verified native snapshot 导入，以 genesis admissions 建立 CURRENT；这不是一个 managed author-axiom commit。`plan` 的章纲是软计划；`write/review/confirm` 共用唯一事务；`query/dashboard` 只读 HEAD；`doctor` 诊断；`learn` 永远 style-only。

长期硬设定由 HEAD 可达的独立 `author_axiom_commits` 绑定。`plan` 只在受管
`.canon-ledger/tmp/author_axioms/*.json` 生成 draft；每个 JSON leaf 绑定文件、UTF-8 byte span、
JSON Pointer、quote 与 value digest。ADD/UPDATE/REMOVE 以及 genesis admission override 都生成
exact 人工 case，使用与章节事务相同的全局 staging lock 和 CURRENT CAS，但不改变章节列表。
完成 finalize 前 draft 不进入 projection/query/context；发布后读取只依赖不可变 commit，draft
可删除。章节引用 axiom 时按该章节 parent HEAD 的 active axiom set 校验，后续 axiom 更新不能让
旧章失绑。文风文件明确排除在 axiom digest、HEAD 与人工 case 外。

## 发布门槛

必须用属性测试和故障注入证明：

```text
CanonFacts <= MachineProvenFacts union ExactHumanApprovedFacts
KeyFacts intersect CanonFacts <= ExactCheckpointApprovedFacts
Replay(transaction, N) == Replay(transaction, 1)
```

另外覆盖：决定错绑拒绝、父 HEAD 竞争拒绝、发布中断保持旧 HEAD、同章重新裁决无残留、早章改写使后缀失效、所有投影 HEAD 不一致时写前门禁失败。

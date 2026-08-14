# 更新日志

## v7.1.0 - 人工确认走对话、提交链与确认链全链路加固

发版范围：`v7.0.2..v7.1.0`。

### 给作者看的变化

- 新增 `/canon-ledger-confirm N`：章末排队的歧义候选事实（谁知道了什么、谁在哪、东西在谁手里）现在直接在对话里逐条确认，看证据原文选 confirm / ignore / replace 即可，不再需要手写裁决 JSON、跑 CLI、再手动重跑提交。
- 裁决保存后系统自动重放本章提交：已确认事实升级为 `verified` 进入正史，故事资料自动重建；正文改过的章节会被拒绝重放并提示重新走写作链。
- 提交不再静默半途而废：提交时故事资料（读模型）没跟上正史会立刻报错并给出修复命令，下次写作前也会检查这种缺口并阻断，不会出现「正文提交了、资料悄悄缺一块」。
- 确认真正生效才算完成：确认报告区分「已裁决未生效」与「已生效」，只保存裁决没有重放会被点名要求补重放；重复运行确认不会把已确认事实降级回未确认。
- 确认单与内容双重绑定：正文改动或候选事实变化后，旧裁决自动失效回待确认；不同章节的同名确认单不会串单，短编号有歧义时直接报错。
- replace 裁决锁定事件类型与主体，替换表述必须携带正文证据，杜绝借「替换」注入无证据事实。
- 同一信息编号在同一章出现两种说法会直接报错；跨章说法不一致会转人工确认，作者裁决后以确认的说法为准更正故事资料。
- 静默写入护栏：状态变化（境界、位置等）改动已记录字段时必须声明旧值且与记录一致；实体不能悄悄变类型；所有带证据引用的事实必须真的出现在本章正文里。
- 写章和审查的最终报告在有待确认项时会直接给出 `/canon-ledger-confirm N` 命令。
- 起草约束更明确：占位正文、时间无故回跳、上章未闭合问题无承接、能力道具情报超出记录，都会在起草阶段就被要求避免。
- Skill 命令块自带环境自检：调用器没有在同一个 shell 会话里先跑环境引导时，会得到一条明确的中文报错，而不是变量为空的随机失败。

### 给维护者

- 新增 `chapter-commit --chapter N --from-last-commit`：从该章 commit 文件内保存的四份 artifact 重放 `build_commit`，与四个 artifact 路径参数互斥；重放前仍校验正文绑定，失效即拒绝。
- `chapter_commit.py`：`apply_projections` 抛异常，或 accepted commit 的投影状态存在 `pending`/`failed:*` 时非零退出，并输出 `projections retry --chapter N` 修复指引。
- 新增 `projection_rebuild.projection_coverage_gaps`：检测 accepted commit 未进投影清单（或快照哈希不符）的缺口；prewrite gate 以 `projection_coverage_gap` 阻断新章写作，`projection_snapshot_requires_rebuild` 在前缀有洞时强制整体重建。
- `human_review`：裁决账本记录 `candidate_fingerprint` 与 `verified_event_id/sha256`；`decision_id` 统一按 `chNNNN-` 章节命名空间（`resolve` 接受无歧义短 ID，多章同名报 `ambiguous`）；`replace` 校验事件类型、主体不变且携带 `evidence_quote`；重放按已记录内容哈希保持 `verified` 不降级。
- `chapter_commit_service`：新增 `_information_conflict_items`（同章两种 claim 硬错、跨章 claim 不一致生成合成待确认项转人工）、`_validate_state_delta_chain`（旧值链校验）、`_validate_entity_type_stability`（实体类型稳定性）；证据入章校验扩展到所有带 `evidence_quote` 的事件类型。
- `canonical_history`：人工裁决产生的 `verified` 知识事件可更正 information 行的 `canonical_claim`（人工表述优先于模型换述）。
- `user-report` 新增 `confirm` 阶段，并对比 commit provenance 的 `resolved_decision_ids` 报告「裁决已保存但尚未生效」。
- 新增受信引导脚本 `scripts/bootstrap_env.py`：复用 `export_cursor_env.py` 的清单校验，输出六行行协议。9 个 SKILL.md 的约 80 行引导样板收敛为逐字一致的 28 行短样板，新增 prompt_integrity 一致性测试防再漂移。
- 9 个 SKILL.md 的全部 59 个非引导 bash 块首行注入 `: "${VAR:?…}"` 环境守卫行（用到 `PROJECT_ROOT` 的块额外校验之）；`hooks/guard_runtime_write.py` 将与随包文本逐字一致的守卫行视为判定透明前缀，改写变体不享受豁免；prompt_integrity 与 hooks 测试固化该契约。
- `hooks/guard_runtime_write.py`：`TRUSTED_PLUGIN_SCRIPT_NAMES` 加入 `bootstrap_env.py`，`$_EXPORTER` 旧分支替换为 `_has_validated_bootstrap_block`（hint 路径执行仅放行与随包 SKILL.md 逐字一致的引导块）。
- `human-review` 子命令登记进 prompt_integrity 的 CLI 注册表。
- 删除挂空的 `references/shared/core-constraints.md`，其防幻觉硬约束并入 write skill Step 2，loading map 登记删除记录。

## v7.0.2 - 收口残留的写法口径并保住设定事实

发版范围：`v7.0.1..v7.0.2`。

### 给作者看的变化

- 设定集里含「节奏 / 氛围 / 反转」的世界规则会进入 canon，不再被当成写作教程丢掉。
- 审查只认设定、时间线、连贯、角色、逻辑五类问题；查询合同时只看卷目标、章纲节点和设定快照。
- 章节摘要不再示范钩子类型或钩子强度；面板接口也不再返回这些写法分数。
- 合法中文设定、已接受事实和总纲里的卷目标会进入合同；命名检索不再喂大模型指令。

### 给维护者

- `review-schema.md`、query skill 与 blocking 裁决对齐五维事实分类和无评分审计。
- plan 加载表不再登记冲突设计教程，并去掉已删除的 architecture 文档指针。
- Dashboard `/api/stats/chapter-trend` 不再返回 `hook_strength` / `hook_type` / `review_score`。
- `_SETTING_CRAFT_RE` 不再把「节奏 / 氛围 / 反转」当子串误杀。

## v7.0.1 - 仓库更名与开发说明校正

发版范围：`v7.0.0..v7.0.1`。

### 给作者看的变化

- 项目仓库更名为 `Splittinglv/canon-ledger`，插件功能和命令保持不变。
- 文档明确说明项目使用生成式 AI 辅助设计、实现、测试与审查，不再把当前产品描述为某个个人单独维护或创作。
- 上游来源、GNU GPL v3 许可和第三方归属继续完整保留。

### 给维护者

- 同步更新插件 manifest、marketplace、README、NOTICE、ATTRIBUTION、AUTHORS、发行说明和包校验中的仓库地址。
- `Splittinglv` 保留为项目发起与发布账号；文档不把全部内容归于单一自然人。

## v7.0.0 - 叙典 CanonLedger 产品线起点

发版范围：`v6.2.2..v7.0.0`。

### 给作者看的变化

- 产品更名为“叙典 CanonLedger”，定位为长篇小说一致性引擎，并采用生成式 AI 辅助开发与验证。
- 默认能力仍只负责设定、时间线、人物状态、关系、伏笔、承诺、章纲目标和正文绑定事实的一致性；具体文风与文笔继续由作者和当前模型决定。
- 唯一受支持的接口改为 `/canon-ledger-*`、`.canon-ledger/` 与 `CANON_LEDGER_*`；不提供旧插件接口或项目数据迁移层。
- 新建书项目继续保证设定、时间线、伏笔、章纲目标、正文绑定和可重放投影完整。

### 给维护者

- 插件产品名、slug 和发布账号改为 `CanonLedger`、`canon-ledger` 与 `Splittinglv`；产品仓库为 `Splittinglv/canon-ledger`。
- 项目自 2026-08-13 起不再跟踪上游后续版本；它不是上游官方发行版，也不表示上游对本项目提供认可或支持。
- 保留 GNU GPL v3，并新增来源、作者与第三方组件清单；历史 v6.2.1、标签、提交及发行记录保持不变。

## v6.2.2 - 长期一致性真源可重建、设定写回可验证

发版范围：`v6.2.1..v6.2.2`。

### 给作者看的变化

- 初始化设定和后续写回的角色、地点、势力、规则会进入可校验的 canon；文风提示与创作套路仍由作者或当前模型决定。
- 写第 N 章只读取正文绑定且已接受的前 N-1 章事实，人物状态、关系、时间线、伏笔和承诺不会读取未来章快照。
- 同章改稿会替换旧事实并重建全部投影；被删除的事件、状态、场景和检索内容不再残留。
- 章纲目标、必达节点和禁区进入写前门禁；空目标、缺章合同或空完成清单不能绕过提交。
- 标准、快速、最简审查具有明确覆盖范围，不再生成文笔、节奏或“AI 味”评分。
- 无 Git 项目也能生成带签名清单的完整本地快照，并在恢复后统一重建投影。

### 给维护者

- 世界规则必须提供与正文绑定一致的逐字证据，章法配方不能晋升为硬约束。
- Cursor Hook 使用受信解释器和闭合命令策略；真实 Skill 脚本块及危险变体均有运行回归。
- 发布流程要求可比较的版本 tag、干净工作区、同步版本元数据和严格包校验。

## v6.2.1 - 长篇一致性链路可验证、可恢复

这是基于上游 v6.2.1 的首个 Cursor 长期一致性基线。

### 给作者看的变化

- 默认不再套用题材套路、文风教程或未声明的金手指；插件只提供设定、时间线、人物状态、关系、伏笔和承诺的一致性约束。
- 写章事实与最终正文字节绑定。改稿后，旧审查、旧抽取、旧提交和旧备份会被判为过期，不能继续冒充有效结果。
- 世界规则、未回收伏笔、未兑现承诺和当前关系不会因章节距离、条数上限或上下文预算被静默丢弃。
- 历史检索进入默认写前上下文；没有向量模型密钥时使用本地 BM25，不会因此阻断写章。

### 给维护者

- 投影、生命周期、时间线和状态更新支持安全重试与乱序重放。
- RAG 数据按已接受提交和正文来源标记过滤，并区分语义检索与 BM25 降级。
- Cursor hook、Skill 环境引导及发布工具使用 fail-closed、数据协议和根级 `.cursor-plugin` 布局。

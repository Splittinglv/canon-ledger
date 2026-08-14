# Reference Loading Map

> 本文件记录当前 `skills/*/SKILL.md` 的实际 reference 消费关系。
> 口径：只登记 skill 明确要求直接读取的 md/template，以及明确调用 `reference_search.py` 或 `story-system` 间接消费的 CSV。
> 不登记普通项目数据读取，例如 `.canon-ledger/state.json`、`设定集/*.md`、`大纲/*.md`、`index.db`。

---

## 直接 Read 的 md/template

> 「读取方式」：**区段** = 先 `Grep` 匹配 `^#{1,4} ` 定位真实标题锚点行号，再 `Read` offset/limit 取段；**全文** = 短文件整体读。锚点用文件里的真实标题原文（含中文顿号「、」），不是计划简写。

| Skill | 阶段 | 触发 | Reference | 读取方式 | 区段锚点（区段读时匹配此真实标题） |
|-------|------|------|-----------|---------|-----------|
| canon-ledger-init | Step 1 | always | `skills/canon-ledger-init/references/system-data-flow.md` | 全文 | — |
| canon-ledger-init | Step 2 | 用户人物扁平 | `skills/canon-ledger-init/references/worldbuilding/character-design.md` | 全文 | — |
| canon-ledger-init | Step 4 | always | `skills/canon-ledger-init/references/worldbuilding/faction-systems.md` | 区段 | 当前世界观所需小节 |
| canon-ledger-init | Step 4 | 涉及修仙/玄幻/高武/异能 | `skills/canon-ledger-init/references/worldbuilding/power-systems.md` | 区段 | 力量体系对应小节 |
| canon-ledger-init | Step 4 | always | `skills/canon-ledger-init/references/worldbuilding/world-rules.md` | 全文 | — |
| canon-ledger-init | Step 6 | always | `skills/canon-ledger-init/references/worldbuilding/setting-consistency.md` | 区段 | 一致性校验小节 |
| canon-ledger-plan | Step 4 | always | `templates/output/大纲-卷节拍表.md` | 全文 | — |
| canon-ledger-plan | Step 5 | always | `templates/output/大纲-卷时间线.md` | 全文 | — |
| canon-ledger-plan | 章纲拆分 | always | `references/outlining/plot-signal-vs-spoiler.md` | 全文 | — |
| canon-ledger-write | Step 2 | 仅当作者已手写 | `设定集/文风提示词.md`（书项目内，非插件 reference） | 全文 | 只取「作者提示词」正文 |
| canon-ledger-review | Step 2 | always | `references/review-schema.md` | 全文 | — |
| canon-ledger-review | Step 6 | blocking issue 需用户决策 | `references/review/blocking-override-guidelines.md` | 全文 | — |
| canon-ledger-query | 查询识别后 | 所有查询 | `skills/canon-ledger-query/references/system-data-flow.md` | 区段 | 按查询类型取数据源优先级小节 |
| canon-ledger-query | 查询识别后 | 伏笔分析 | `skills/canon-ledger-query/references/advanced/foreshadowing.md` | 全文 | — |
| canon-ledger-query | 查询识别后 | 格式查询 | `skills/canon-ledger-query/references/tag-specification.md` | 全文 | — |

> 网文技法 md 已从插件移除。默认写章不读 CSV 技法表；显式 `--table` 仍可检索。

## CSV 检索：直接调用 `reference_search.py`

| Skill | 阶段 | 触发 | 实际调用 |
|-------|------|------|----------|
| canon-ledger-init | 角色/书名/势力设定 | 用户开始设定命名 | `--skill init --table 命名规则 --query "{命名对象} {题材}" --genre {题材}` |
| canon-ledger-plan | 卷级规划 | always | `--skill plan --table 命名规则 --query "角色命名" --genre {题材}` |
| canon-ledger-write | Step 2 | 新角色首次出场 | `--skill write --table 命名规则 --query "角色命名" --genre {题材}` |

## CSV 检索：`story-system` 间接消费

| 入口 Skill | 阶段 | 触发 | 间接消费 |
|------------|------|------|----------|
| canon-ledger-init | Story System 初始化 | init 完成后 `story-system "${GENRE}" --genre "${GENRE}" --persist --format json` | 题材只作中性分类标签；只收集一致性表（命名/人设/金手指），插件不再随包发布技法或裁决表 |
| canon-ledger-plan | runtime 合同刷新 | 规划直接落到具体章节时 `--persist --emit-runtime-contracts --chapter {chapter_num}` | 同上，并由 `RuntimeContractBuilder` 生成 volume/chapter/review 合同 |
| canon-ledger-write | 准备阶段 | 起草前 `--persist --emit-runtime-contracts --chapter {chapter_num}` | 同上；`chapter_{NNN}.json` 的 `chapter_focus` 仅作 CSV 参考，章节目标仍以章纲为准 |
| canon-ledger-review | Step 1 | 目标章缺 runtime 合同时补齐 | 同上；review 优先依据 `.story-system/reviews/chapter_{NNN}.review.json` 与 latest accepted commit |

`StorySystemEngine` 的真实数据流：

| 步骤 | 数据源 | 说明 |
|------|--------|------|
| `_route()` | 无 CSV | 只做中性题材归类：显式 genre、query 文本推断，或标记 `unclassified`。永不推荐检索表，也不注入调性/节奏预设 |
| `_collect_tables()` | 路由推荐表（当前恒为空） | 内部以 `skill="write"` 调 `reference_search.search()`；`filter_consistency_tables()` 再收窄到命名/人设/金手指 |
| `_extract_anti_patterns()` | 一致性表毒点 | 只取人物逻辑类毒点，不含路由毒点或裁决反模式 |

## 无独立 reference 的 Skill

| Skill | 说明 |
|-------|------|
| canon-ledger-dashboard | 只读面板启动流程，不加载独立 reference；核心校验接口是 `/api/story-runtime/health` 与 `/api/preflight` |
| canon-ledger-learn | 只读 state 后追加 `设定集/文风提示词.md`，不加载独立 reference 或 CSV |

## 当前非直接调用项

以下材料已从插件移除，或当前存在但没有被 `SKILL.md` 明确要求直接加载；除非后续 skill 增加触发条件，否则不计入 direct loading map：

| 文件 | 现状 |
|------|------|
| 原 `optional/canon-ledger-craft/` 下写法/追读力/Anti-AI/润色 md | 已从插件删除 |
| 原 `references/shared/core-constraints.md` | 已从插件删除。其中仍有价值的防幻觉硬约束（禁止占位正文、时间不回跳、未闭合问题承接、能力/道具/情报不超记录）已并入 `skills/canon-ledger-write/SKILL.md` Step 2 起草段 |
| CSV `场景写法` / `写作技法` / `桥段套路` / `爽点与节奏` / `裁决规则` / `题材与调性推理` | 已从插件删除。`reference_search` 仍保留表名黑名单，用于挡住作者自行放入 `references/csv/` 的写法类表 |
| `skills/canon-ledger-plan/references/outlining/conflict-design.md` | 文件仍在，但 plan skill 明确不加载冲突设计教程 |
| `skills/canon-ledger-review/references/common-mistakes.md` | 未在当前 review 流程中直接加载 |

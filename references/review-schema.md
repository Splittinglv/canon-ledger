# 审查输出 Schema

> **主服务 skill**: `canon-ledger-write`、`canon-ledger-review`
> **内容层级**: 流程闸门 / schema 定义
> **关键原则**: reviewer 输出 JSON 是审查唯一事实源；`review-pipeline` 解析并覆盖为标准 `review_result`，再生成报告与无评分 `review_audit`。主 skill 不得伪造结论或分数。

统一审查 Agent 只记录可验证的长期一致性问题。分类、维度与 `scripts/data_modules/review_schema.py` 的 `REVIEW_DIMENSIONS` / `VALID_CATEGORIES` 必须一致。

## 核心约束

- **只查五维事实**：`setting`、`timeline`、`continuity`、`character`、`logic`；其中 logic 只含明确规则下的机械矛盾
- **不评分**：不输出总分，不输出通过或失败总评
- **不确定转人工**：需要解释语义、猜动机、补隐含转场或判断规则例外时写 `manual_checks`，不得输出确定 issue
- **计划不冒充事实**：大纲节点、剧情禁区和一般写作偏离由 fulfillment 报告，默认不阻断事实提交
- **非法分类整单拒绝**：`category` 必须是上述五维之一。其它值会触发 `ValueError`，整份审查作废，不会静默改写或落入缺省分类
- **单 agent**：由 `reviewer` 输出；主流程不得口头总结代替 JSON

## 正文绑定

reviewer 输出顶层必须包含调用方传入的 `chapter_binding`（`schema_version/chapter/path/sha256/bytes`），不得自行重算或修改。`review-pipeline` 在产生报告或审计副作用前，必须确认它与当前正文字节完全一致。正文修改后旧审查自动失效，不可直接用于 chapter-commit。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| chapter | int | ✅ | 待审章节号 |
| review_mode | standard / fast / minimal | ✅ | 审查范围 |
| chapter_binding | object | ✅ | 正文绑定，原样回传 |
| issues | array | ✅ | 问题清单；无问题则为 `[]` |
| manual_checks | array | ✅ | 证据不足、容易误判、需要作者判断的检查项；永不自动阻断 |
| dimension_results | array | ✅ | 已审维度结论；顺序与模式绑定 |
| summary | string | ✅ | 中文摘要，不是评分 |

`standard` 的 `dimension_results` 必须按顺序且只能覆盖 setting / timeline / continuity / character / logic。

`fast` 必须按顺序且只能覆盖 setting / timeline / continuity / character；知识边界是默认长期一致性检查，不能跳过。
`minimal` 不得携带 issue 或 `manual_checks`，`dimension_results` 必须为空。

每条维度结论：

| 字段 | 说明 |
|------|------|
| dimension | 上述合法维度名 |
| conclusion | 证据充分且无问题写「未发现已证实的事实问题」；有问题写「发现N个已证实问题：简述」；有人工项或覆盖/可信度不足时必须明确说明，不得伪装成完整通过 |

## Issue Schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| severity | critical / high / medium / low | ✅ | 严重度 |
| category | setting / timeline / continuity / character / logic | ✅ | 问题分类，与审查维度同一枚举 |
| location | string | ✅ | 位置（如「第3段」） |
| description | string | ✅ | 问题描述 |
| evidence | string | ✅ | 原文引用或已接受事实对比 |
| fix_hint | string | ✅ | 修复方向 |
| blocking | bool | ❌ | 是否阻断；`critical` 默认 `true` |

分类含义：

- `setting`：与设定集 / `setting_canon` / 世界规则矛盾
- `timeline`：时间顺序、跨度、倒计时，以及有明确物理在场证据的地点矛盾；梦境、回忆、远程通信和提及不更新当前位置
- `continuity`：已接受提交中的未闭合问题、伏笔、承诺、状态和物品持有；不含章纲履约
- `character`：只查知识边界；不评价性格、动机、口吻或文笔
- `logic`：只查明确次数、冷却、互斥状态、物理前提等可逐字段对照的硬规则；不评价一般因果、力量观感或决策动机

文笔、钩子、场景过渡、情绪弧、对话是否书面都不是合法分类。

## Manual Check Schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| category | 五维枚举 | ✅ | 疑点所属事实维度 |
| location | string | ✅ | 正文位置 |
| description | string | ✅ | 作者需要确认什么 |
| evidence | string | ✅ | 当前已有证据；允许明确写“证据不足” |
| reason | string | ✅ | 为什么插件无法可靠自动判断 |
| options | string[] | ✅ | 供作者参考的有限判断方向，不是强制修复 |

`manual_checks` 不计入 `issues_count`、`blocking_count`，也不会让 chapter commit rejected。

## 长期事实覆盖与可信度

as-of v3 快照提供 `information`、`knowledge_by_entity`、`presence` / `presence_history`、`custody` / `custody_history`、`coverage` 和 `verification`。

- `coverage` 是完整度：`complete|partial|none`。
- `verification` 是可信度：`verified|supported|pending|unknown|legacy`。
- 只有覆盖 `complete` 且可信度 `verified` 时，字段缺失才可作为否定证据。
- `supported` 的正向记录可用于提醒一致性；只要判断需要语义解释，就转 `manual_checks`。
- `pending|unknown|legacy` 禁止据缺失断言角色不知道、不在场或不持有物品。

## 阻断规则

- 存在任何 `blocking=true` 的 issue → 不得提交章节
- 只有能与作者原始设定或 `verified` 事实逐字段对照、且两者不能同时成立的矛盾才可 blocking
- `supported` 事实引出的语义疑点默认转人工，不自动阻断
- 读不到上章摘要不是错误；第一章或无已接受上章时禁止因此 blocking

## 落库

统一审查 agent 的原始输出先写入 `review_results.json`。随后由 `review-pipeline` 覆盖为标准 `review_result`，并生成 `review_audit.json`，在 `--save-audit` 时写入 `index.db.review_audits`。

审计记录只保存检查范围与计数，不保存质量分数：

- `chapter` / `review_mode` / `review_status` / `review_degraded`
- `reviewed_dimensions` / `skipped_dimensions` / `dimension_results`
- `issues_count` / `blocking_count` / `manual_checks_count` / `severity_counts` / `categories`
- `critical_issues` / `report_file` / `notes` / `timestamp`

说明：

- 闸门决策以 `blocking=true` 和 issue 明细为准，不以任何总分或维度分为准
- `index.db.review_metrics` 是旧评分表，默认审查链不再写入

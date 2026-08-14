# 审查输出 Schema

> **主服务 skill**: `canon-ledger-write`、`canon-ledger-review`
> **内容层级**: 流程闸门 / schema 定义
> **关键原则**: reviewer 输出 JSON 是审查唯一事实源；`review-pipeline` 解析并覆盖为标准 `review_result`，再生成报告与无评分 `review_audit`。主 skill 不得伪造结论或分数。

统一审查 Agent 只记录可验证的长期一致性问题。分类、维度与 `scripts/data_modules/review_schema.py` 的 `REVIEW_DIMENSIONS` / `VALID_CATEGORIES` 必须一致。

## 核心约束

- **只查五维事实**：`setting`、`timeline`、`continuity`、`character`、`logic`
- **不评分**：不输出总分，不输出通过或失败总评
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
| dimension_results | array | ✅ | 已审维度结论；顺序与模式绑定 |
| summary | string | ✅ | 中文摘要，不是评分 |

`standard` 的 `dimension_results` 必须按顺序且只能覆盖 setting / timeline / continuity / character / logic。  
`fast` 必须按顺序且只能覆盖 setting / timeline / continuity。  
`minimal` 不得携带问题结论，`dimension_results` 必须为空。

每条维度结论：

| 字段 | 说明 |
|------|------|
| dimension | 上述合法维度名 |
| conclusion | 无问题写「未发现事实问题」；有问题写「发现N个问题：简述」，并在 `issues` 给出完整条目 |

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
- `timeline`：时间顺序、跨度、倒计时与已接受事实矛盾
- `continuity`：已接受提交中的未闭合问题、伏笔、承诺、状态，以及章合同 `must_cover_nodes` / `forbidden_zones`
- `character`：动机、知识边界、能力与已建立人设矛盾
- `logic`：因果、力量对比、决策前提不成立

文笔、钩子、场景过渡、情绪弧、对话是否书面都不是合法分类。

## 阻断规则

- 存在任何 `blocking=true` 的 issue → 不得提交章节
- `severity=critical` 自动 `blocking=true`
- 其余 severity 由审查 agent 根据是否破坏已接受事实判断
- 读不到上章摘要不是错误；第一章或无已接受上章时禁止因此 blocking

## 落库

统一审查 agent 的原始输出先写入 `review_results.json`。随后由 `review-pipeline` 覆盖为标准 `review_result`，并生成 `review_audit.json`，在 `--save-audit` 时写入 `index.db.review_audits`。

审计记录只保存检查范围与计数，不保存质量分数：

- `chapter` / `review_mode` / `review_status` / `review_degraded`
- `reviewed_dimensions` / `skipped_dimensions` / `dimension_results`
- `issues_count` / `blocking_count` / `severity_counts` / `categories`
- `critical_issues` / `report_file` / `notes` / `timestamp`

说明：

- 闸门决策以 `blocking=true` 和 issue 明细为准，不以任何总分或维度分为准
- `index.db.review_metrics` 是旧评分表，默认审查链不再写入

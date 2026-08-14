---
name: blocking-override-guidelines
purpose: review Step 6 blocking issue 用户裁决参考
---

# Blocking Override Guidelines

> 主服务 skill: `canon-ledger-review` Step 6
> 次服务 skill: `canon-ledger-write` Step 3（blocking 循环时参考）
> 内容层级: 提醒层 / 缺陷补偿层 / 知识补充层

---

## 提醒层

- 只有用户明确承担风险时才允许 override blocking issue
- override 不等于"问题不存在"，而是"用户决定接受后果"
- override 后仍应在审查报告中保留原始 issue 记录
- 审查只处理已证实的设定、时间线、连续性、角色知识边界和明确机械规则冲突。文笔、节奏、人物动机、章纲履约和 `manual_checks` 都不走 override

## 缺陷补偿层

以下情况**禁止建议 override**：

- issue 涉及与作者原始设定或 verified 记录逐字段对照的**设定冲突**
- issue 涉及有明确时间锚点、无法同时成立的**时间线冲突**
- issue 涉及**事实错误**（角色死亡后复活、已销毁道具再次出现等）
- issue 涉及**连续性断裂**（上章结尾与本章开头无法衔接）

以下情况**可以考虑 override**（但仍需用户确认）：

- issue 的严重度是 medium/low，且用户书面确认这是有意保留的未决事实，并接受后续章节必须收口

## 知识补充层

### 可 override 的典型场景

1. **作者确认的有意延迟**：用户明确说某条非阻断事实要留到后章处理，并接受审查记录保留

### 不可 override 的典型场景

1. **角色能力超出当前境界**：主角使用了尚未觉醒的能力
2. **地点穿越**：已验证的距离与时间锚点证明无法抵达，且正文没有转场机制
3. **已死角色复活**：被明确写死的角色在后续章节中出现

章纲节点缺失和禁区偏离由 fulfillment 单独报告；默认 advisory，不属于事实 override。

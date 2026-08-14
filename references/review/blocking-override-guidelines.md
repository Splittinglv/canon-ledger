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
- 审查只处理设定、时间线、连贯、角色知识边界、逻辑。文笔与节奏不是审查项，也不走 override

## 缺陷补偿层

以下情况**禁止建议 override**：

- issue 涉及**设定冲突**（角色能力、世界规则、势力关系与设定集矛盾）
- issue 涉及**时间线冲突**（事件顺序、时间跨度与已有章节矛盾）
- issue 涉及**事实错误**（角色死亡后复活、已销毁道具再次出现等）
- issue 涉及**连续性断裂**（上章结尾与本章开头无法衔接）
- issue 涉及章合同 **must_cover_nodes 未发生** 或 **forbidden_zones 被触碰**

以下情况**可以考虑 override**（但仍需用户确认）：

- issue 是**可选推进节点未显式展开**（不在 `must_cover_nodes` 中，正文已用隐含方式交代）
- issue 的严重度是 medium/low，且用户书面确认这是有意保留的未决事实，并接受后续章节必须收口

## 知识补充层

### 可 override 的典型场景

1. **可选节点未覆盖**：章纲里的可选推进在正文中隐含但未显式展开，且该节点不在 `must_cover_nodes`
2. **作者确认的有意延迟**：用户明确说某条非阻断事实要留到后章处理，并接受审查记录保留

### 不可 override 的典型场景

1. **角色能力超出当前境界**：主角使用了尚未觉醒的能力
2. **地点穿越**：上章在 A 城，本章无交代突然在 B 城
3. **已死角色复活**：被明确写死的角色在后续章节中出现
4. **必须覆盖节点缺失**：`must_cover_nodes` 在正文中没有对应发生

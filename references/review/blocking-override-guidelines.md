---
name: blocking-override-guidelines
purpose: retired v2 review override 说明
status: retired
---

# 已退役：Blocking Override

这份文件只保留旧文件名，防止历史链接把维护者带回已经删除的 v2 流程。
当前生产链没有通用 blocking override、`issue/manual_checks` 队列或按严重度绕过正史的入口。

Canon v3 的处理固定为：

- `confirmed_conflict`：修改正文并完整重跑，动作是 `rewrite`；不能批准冲突事实。
- `checkpoint`：作者只能对 exact candidate 选择 `approve` 或 `rewrite`。
- `ambiguity`：作者可选择 `approve|omit|correct|rewrite`；所有选择都精确绑定当前 STAGING、证据、HEAD 与 material digest。
- 文风、节奏、人物动机、剧情取舍和章纲履约：不生成事实 observation，也不进入人工事实队列。

当前协议见 [`../review-schema.md`](../review-schema.md) 与
[`../canon-v3-skill-protocol.md`](../canon-v3-skill-protocol.md)。任何旧文档若要求
`override`、`confirm|ignore|replace`、`human-review resolve` 或
`chapter-commit --from-last-commit`，都只能作为历史资料阅读。

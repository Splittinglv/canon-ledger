---
name: canon-ledger-write
description: 通过 Canon v3 唯一事务链写作并发布指定章节
---

执行 `canon-ledger-write` Skill。用户参数为章节号。只强制长期事实一致性，不检查文风。唯一提交路径是带 exact stage/material/token 的 `prepare → decide → finalize`；无 CURRENT 或版本冲突时不得回落 legacy。

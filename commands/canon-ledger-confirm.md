---
name: canon-ledger-confirm
description: 逐条裁决当前 Canon v3 transaction 的 checkpoint 与 ambiguity
---

执行 `canon-ledger-confirm` Skill。用户参数可为当前 staged 章节号。只展示 status 的 immutable review material，并在 decide/finalize 请求中回显 stage、transaction、target、material 和 finalize token；版本变化时刷新，禁止把旧选择应用到新 STAGING。

# Canon v3 查询数据流

```text
CURRENT
  → immutable manifest / commits / decisions
  → fresh HEAD-bound canon projection
  → public canon-v3 history / dashboard facades
  → query / context / dashboard
```

权威顺序：

1. active author-axiom manifest；
2. CURRENT 指向的 v3 manifest 与事实记录；
3. 绑定相同 HEAD/generation 的 canon projection；
4. STAGING（仅待确认展示，不是 active）；
5. legacy/state/index（只读诊断，不是当前事实）。

`.story-system` 大纲和章合同提供未来剧情方向，不能证明事件已经发生。磁盘上尚未 recertify 的长期设定属于 draft setting，不能覆盖 active author axioms。

查询第 N 章前的事实语义是 as-of N-1；查询第 N 章发布后状态语义是 as-of N。只有公开 facade 明确提供该 revision 时才能回答。任何 projection 与 HEAD 不一致时，不得回退 index.db，也不得承诺尚不存在的 HEAD-history bypass；停止事实查询并要求 `canon-v3 rebuild-projection`，失败后转 doctor。

公开分层入口固定为：active 使用 `canon-v3 history` 或 `/api/canon-v3/*` 事实接口；STAGING 只看 `canon-v3 status` 的 `cases[].review_material`；legacy 只看 cutover/recertification 状态下的 `canon-v3 audit-cutover`；style 只看 `style-memory show`。入口没有暴露的数据 fail-closed，不绕过它直接读取对象库。

角色、物品、地点身份统一由 namespace-aware entity registry 解析；alias 不唯一时返回全部候选，不能默认取第一项。

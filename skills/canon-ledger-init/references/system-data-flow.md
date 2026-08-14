---
name: system-data-flow-redirect
purpose: 重定向到权威版本
---

<context>
初始化与查询共用同一份当前数据主链说明，避免重复维护。
</context>

<instructions>

## 权威版本位置

`${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-query/references/system-data-flow.md`

## 加载方式

```bash
cat "${CANON_LEDGER_PLUGIN_ROOT}/skills/canon-ledger-query/references/system-data-flow.md"
```

## 快速参考

### 目录结构
```
项目根目录/
├── 正文/           # 章节文件
├── 大纲/           # 卷纲/章纲
├── 设定集/         # 世界观/力量体系/角色卡
└── .canon-ledger/
    ├── state.json              # 当前状态投影
    ├── index.db                # SQLite 投影
    ├── memory_scratchpad.json  # 一致性投影
    └── projection_manifest.json
```

### 当前结构核心
- `.story-system` 合同和绑定的章节提交是事实源。
- `.canon-ledger` 内的数据均为可按 accepted commits 重建的读模型。
- 章合同必须完整保留目标与必须覆盖节点。

</instructions>

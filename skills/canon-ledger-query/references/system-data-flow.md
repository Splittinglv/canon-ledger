---
name: system-data-flow
purpose: 项目初始化和状态查询时加载，理解 CanonLedger 当前数据主链
---

<context>
本文件只描述 CanonLedger 7 的当前项目结构。章节合同与已接受的章节提交是事实源；其余数据库、摘要和暂存文件均为可重建读模型。
</context>

<instructions>

## 目录约定

```text
项目根目录/
├── 正文/                         # 第NNNN章.md 或 第NNNN章-标题.md
├── 大纲/                         # 总纲、卷纲、章纲
├── 设定集/                       # 作者确认的设定材料
├── .story-system/
│   ├── MASTER_SETTING.json       # 全书合同
│   ├── volumes/volume_NNN.json   # 卷合同
│   ├── chapters/chapter_NNN.json # 章合同，必须保存 goal 与 must_cover_nodes
│   ├── reviews/                  # 绑定正文的审查工件
│   ├── commits/                  # 绑定正文的章节提交，唯一章节事实源
│   └── events/                   # accepted_events 的重放投影
└── .canon-ledger/
    ├── state.json                # 当前状态投影
    ├── index.db                  # 实体、关系、状态变化与事件投影
    ├── memory_scratchpad.json    # 硬约束和生命周期义务投影
    ├── vectors.db                # 事实检索投影
    ├── summaries/                # 章节摘要投影
    ├── projection_log.jsonl      # 投影执行日志
    └── projection_manifest.json  # 提交快照与重放水位
```

## 写作与落盘主链

1. 写作前加载 `MASTER_SETTING`、卷合同、章合同，以及目标章之前的已绑定 accepted commits。
2. 章合同中的 `chapter_directive.goal` 和 `must_cover_nodes` 必须存在；缺失时阻断提交。
3. 正文、审查、履约、消歧、提取四个工件使用同一 `chapter_binding`。
4. `ChapterCommitService` 验证绑定与履约分区后，写入 `chapter_NNN.commit.json`。
5. 只有 `meta.status=accepted` 的提交进入投影路由。
6. state、index、summary、memory、vector 五类写入器从同一提交生成读模型。

## 当前提取结构

`extraction_result` 是提交中唯一有效的提取工件，至少包含：

```json
{
  "chapter_binding": {},
  "accepted_events": [],
  "state_deltas": [],
  "entity_deltas": [],
  "timeline_events": [],
  "summary_text": ""
}
```

章节事实不得放在提交顶层，也不得放入其他记忆容器。开放悬念、读者承诺、世界规则与关系变化使用带稳定 ID 的 `accepted_events`；时间线使用 `timeline_events`。

## 读取边界

- 写作上下文以章合同、初始化合同和已绑定提交为准。
- `state.json`、`index.db`、`memory_scratchpad.json`、`vectors.db` 与摘要不得反向覆盖提交事实。
- 自由文本摘要与项目记忆默认不作为硬约束注入。
- `/canon-ledger-learn` 写入的作者显式一致性规则可随投影重建保留。
- 章提交替换、投影缺失或清空后，按章节顺序重放 accepted commits 即可恢复读模型。

## 查询速查

```bash
python "${CANON_LEDGER_PLUGIN_ROOT}/scripts/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" query progress

python "${CANON_LEDGER_PLUGIN_ROOT}/scripts/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" projections replay
```

查询结果属于读模型视图；若诊断报告显示绑定、合同或投影水位异常，应先修复当前项目工件或执行投影重放，不能绕过合同改读其他结构。

</instructions>

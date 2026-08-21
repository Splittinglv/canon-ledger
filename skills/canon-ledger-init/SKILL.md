---
name: canon-ledger-init
description: 初始化新的长篇小说项目，收集作者已确定的故事基线，生成项目骨架，并建立绑定初始硬设定的 Canon v3 genesis。
---

# 初始化长篇小说项目

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)。初始化只服务新项目；已有 accepted prefix 或 CURRENT 时不得覆盖。只有创建尚未可识别的 clean target 时可以暂不运行项目 locator/status；骨架生成后必须立即回到统一 workflow authority。

## 1. 确认 exact target

- 要求用户明示给出新书目标目录，转成绝对路径后固定；不把工作区当前绑定的另一个项目猜成目标。
- clean target 必须不存在或为空。目标中已有正文、设定、大纲、`.canon-ledger` 或 `.story-system` 时，列出内容并停止，请用户改用新目录；禁止静默合并或覆盖。
- 若目标已能识别为书项目，用该目标自己的 `canon-v3 status` 分流：`legacy_cutover|legacy_repair|recertification` 执行 snapshot 的唯一恢复动作，`canon_v3` 项目停止 init 并建议 plan/write。

## 2. 收集最小故事基线

分批补齐会改变项目方向的信息：

- 工具必需：目标目录、书名和题材。
- 可选初始事实：作者已经确定的主角/初始人物姓名、世界边界、力量规则与代价、金手指机制与不可逆限制；只能使用 init 闭合 `initial_canon` schema 实际支持的字段。
- 软计划：核心前提、人物欲望/缺陷/动机、第一卷目标、预计篇幅和平台偏好。这些可写模板或规划，但不成为章节事实审查的强制门禁。
- style-only：作者明确要求长期保留的文风、口吻、句式和写作偏好。

未确定的可选字段不传给 init 工具，不用模板默认、“无”或模型补全冒充作者事实。参考作品拆解只能由 `deconstruction-agent` 返回结构化建议，用户确认前不是 Canon，也不能写入项目。
作者已确定但 init 闭合 schema 不支持的物品、地点、关系或其它硬事实，不塞入自由文本绕过边界；先完成 clean genesis，再在写第一章前走第 6 节的 managed author-axiom 通道。

## 3. 最终方案确认

写文件前向作者展示：

- 将通过一次性 verified genesis import 成为 `author_axiom_snapshot` admissions 的初始硬事实；
- 仅作为大纲/剧情/人物表现方向的软计划；
- 仅作为 style-only 的文风偏好；
- 仍未确定、因此不会传给工具的开放项。

只有作者确认后才生成骨架。这个确认不能被模型推断代替。初始导入是 genesis admission，不是先生成 managed author-axiom commit；它的后续修改必须转入第 6 节的独立通道。

## 4. 生成骨架并建立 Genesis

统一 init 工具不依赖已存在的 `PROJECT_ROOT`；它直接消费 exact target、书名、题材和作者确认的可选参数：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" init \
  "<ABSOLUTE_NEW_PROJECT_PATH>" "<TITLE>" "<GENRE>"
```

只将作者已确认的可选值以对应 `--...` flag 追加到同一调用；未确定值不传。

它在同一次调用中创建：

```text
.canon-ledger/                 配置、日志和非权威兼容视图
.story-system/                规划合同与 Canon v3
设定集/                       作者设定与文风文件
大纲/                         总纲、卷纲和章纲
正文/                         章节正文
```

然后它把净化后的 `MASTER_SETTING.initial_canon`（`.story-system/MASTER_SETTING.json#/initial_canon`）编译成带逐项 admission receipt 的 verified new-project snapshot，以 `cutover_chapter=0` 建立 immutable genesis/CURRENT，并重建 fresh projection。

约束：

- init 只创建 `设定集/文风提示词.md` 的 style-only 载体；需长期保留的作者偏好在项目建立后交给 `/canon-ledger-learn`，不进入 genesis 或 author-axiom manifest。
- 章纲和卷纲是软计划，不成为“已经发生”的事实。
- 初始世界硬规则、角色身份等是 genesis 中 `mode=author_axiom_snapshot` 的一次性 `genesis_admissions`；此时 manifest 的 `author_axiom_commits` 和 active snapshot 的 `records` 可以为空，但 `author_axiom_digest` 仍绑定活动初始权威。
- `.canon-ledger/state.json` 只是兼容配置/投影，不能成为正史。

## 5. 定位新项目并验收

统一 init 工具返回后，再用 exact target 定位新项目：

```bash
export PROJECT_ROOT="$("${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "<ABSOLUTE_NEW_PROJECT_PATH>" where)"
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 status
```

依次运行 status、project-status 和 doctor。只有：

```text
state=ready
can_write_next=true
projection_fresh=true
bootstrap_mode=canon_v3
head_hash=<CURRENT HEAD>
```

才宣布初始化完成并建议 `/canon-ledger-plan 1`。同时读取 `canon-v3 author-axioms`：作者确认的初始硬事实应出现在 `genesis_admissions`；没有后续 managed transaction 时 `records=[]` 是正常状态。

骨架已生成但 CURRENT 未建立时报告“部分完成”。只有 status 精确返回 `migration_required + bootstrap_mode=new_project + primary_action=initialize_v3` 时才单独执行 `canon-v3 initialize`；如果发现 accepted legacy prefix，必须转 `legacy_cutover`，不能生成空 genesis 覆盖它。

## 6. 初始化后修改硬设定

live `MASTER_SETTING`、设定集或 init 输入的后续修改不会重写 genesis。新增/修改/删除长期硬设定时：

1. 由 plan/data-agent 在 `.canon-ledger/tmp/author_axioms/*.json` 生成 managed draft 和 exact leaf source；
2. 组装 `canon-v3/author-axiom-proposal/v2`；替换 genesis admission 时还必须提供 exact `genesis_overrides`；
3. 逐项 `author-axiom-prepare/decide/finalize`，产生 HEAD 可达的 managed author-axiom commit。

finalize 前变更不进入 query/context。这条通道只处理硬事实；人物动机、剧情取舍、章纲履约和文风不得变成强制 case。

## 非目标

- 不自动写第一章。
- 不把未确认的模型补全写成硬设定。
- 不把文风、节奏、人物动机、章纲履约或常见网文套路加入事实门禁。
- 不创建 legacy commit、index 事实或旧人工队列。

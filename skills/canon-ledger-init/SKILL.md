---
name: canon-ledger-init
description: 初始化新的长篇小说项目，收集作者已确定的故事基线，生成项目骨架，并建立绑定初始硬设定的 Canon v3 genesis。
---

# 初始化长篇小说项目

开始前完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md)。初始化只服务新项目；已有 accepted prefix 或 CURRENT 时不得覆盖。

## 1. 确认目标目录

- 解析用户指定目录或工作区。
- 若已有书项目，先读取 workflow：legacy 项目转 migrate/repair，v3 项目停止并建议 plan/write。
- 任何已有正文、设定或大纲都先列出并让用户决定是否另建目录；禁止静默覆盖。

## 2. 收集最小故事基线

分批补齐会改变项目方向的信息：

- 书名、题材、核心前提；
- 主角身份、长期欲望和明确限制；
- 世界边界、力量规则和关键代价；
- 初始人物、物品、地点与关系；
- 第一卷目标和预计章节范围；
- 作者明确要求的长期文风偏好。

未确定的内容保留开放，不让 Skill 自行补成硬设定。参考作品拆解只能由 `deconstruction-agent` 返回结构化建议，用户确认前不是 Canon，也不能写入项目。

## 3. 最终方案确认

写文件前向作者展示：

- 将成为初始 author axioms 的硬设定；
- 仅作为大纲/剧情方向的软计划；
- 仅作为 style-only 的文风偏好；
- 仍未确定的开放项。

只有作者确认后才生成骨架。这个确认不能被模型推断代替。

## 4. 生成项目骨架

调用统一 init 工具创建：

```text
.canon-ledger/                 配置、日志和非权威兼容视图
.story-system/                规划合同与 Canon v3
设定集/                       作者设定与文风文件
大纲/                         总纲、卷纲和章纲
正文/                         章节正文
```

约束：

- 文风写入 `设定集/文风提示词.md`，不进入 author-axiom manifest。
- 章纲和卷纲是软计划，不成为“已经发生”的事实。
- 初始世界硬规则、角色身份等写入受管 author-axiom 文件，并生成确定性 axiom manifest/digest。
- `.canon-ledger/state.json` 只是兼容配置/投影，不能成为正史。

## 5. 建立 v3 Genesis

项目文件通过结构校验后执行：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" canon-v3 initialize
```

initialize 必须绑定已确认的 author-axiom digest，写 immutable genesis/CURRENT，并重建 fresh projection。若期间发现旧 accepted commit，立即停止并转 legacy cutover，不能生成空 genesis 覆盖它。

## 6. 验收

依次运行 status、project-status 和 doctor。只有：

```text
state=ready
can_write_next=true
projection_fresh=true
bootstrap_mode=new_project
```

才宣布初始化完成并建议 `/canon-ledger-plan 1`。骨架已生成但 genesis 失败时报告“部分完成”，唯一下一步是恢复 initialize；不得声称可以写第一章。

## 非目标

- 不自动写第一章。
- 不把未确认的模型补全写成硬设定。
- 不把文风、节奏或常见网文套路加入事实合同。
- 不创建 legacy commit、index 事实或旧人工队列。

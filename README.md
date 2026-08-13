# Webnovel Writer for Cursor

[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-6.2.1-brightgreen.svg)](.cursor-plugin/plugin.json)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)

让 AI 写到几百章，依然记得住设定、接得住伏笔、守得住大纲。文风交给模型和作者。

这是 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) **v6.2.1** 的 Cursor 插件移植（GPL-3.0 衍生作品），仓库：[Splittinglv/webnovel-writer-cursor](https://github.com/Splittinglv/webnovel-writer-cursor)。**不是**上游官方发行版，也**没有**上架 Cursor 官方插件商店。出处见 [NOTICE.md](NOTICE.md)。

本仓库把默认产品收成**长期一致性状态机**：插件负责故事别写崩，不负责把句子写成某种网文腔。文风交给当前模型和作者自己的提示词。

## 产品使用逻辑

优先级始终是：

**你这轮说的要求 > `设定集/文风提示词.md` > 设定 / 时间线 / 伏笔 / 章纲 > 插件默认流程**

### 默认守什么

| 默认会做 | 默认不会做 |
|---------|-----------|
| 按总纲 / 卷纲 / 章纲推进 | 规定口吻、句式、章长、网文腔 |
| 核对设定、时间线、伏笔、角色知识边界 | 润色、Anti-AI 终检、风格适配 |
| 登记本章新事实 | 把书面语 / 口语当缺陷来改 |
| 章纲保留目标、时间、禁区 | 强制每章钩子、打脸爽点、CBN 骨架 |

写章前的合同和检索也按这个边界裁：默认只留命名、人设、金手指等一致性材料，不把桥段、爽点、场景写法塞进上下文。

### 文风怎么自定义

只改书项目里的 `设定集/文风提示词.md`，写在「作者提示词」标题下面。插件不会用词库覆盖它。

- 可以写视角、句长、对话习惯、禁忌修辞、想贴近的作品
- 留空就按当前模型自己的文风写
- 剧情、设定、伏笔不要写进这个文件，那些走大纲和设定集
- 口吻偏好也不要丢给 `/webnovel-learn`；学习命令只记跨章事实（伏笔怎么收、时间怎么接），不记文笔

已有书可把 `templates/output/设定集-文风提示词.md` 复制到该书的 `设定集/文风提示词.md`。

### 命令怎么用

日常顺序：开书父目录 → `/webnovel-init` → 按需改文风提示词 → `/webnovel-plan` → `/webnovel-write`。审查、查询、学习按需插入。

**`/webnovel-init` 开新书**

必收：书名、题材、规模（字数或章数）、主角姓名 / 欲望 / 缺陷、世界规模、力量体系。确认摘要后才生成项目。

不说就不问、不阻断：金手指、反套路、卖点公式、题材套路库、参考书拆解。想拆书当灵感，要先明确选「从参考书开始」，拆完经你确认才会写进项目。

**`/webnovel-plan 1` 拆卷拆章**

必写：卷摘要、关键人物、伏笔；每章的目标、时间锚点、章内时间跨度、与上章时间差、倒计时（无则写无）、关键实体、本章变化、本章禁区。时间线是硬约束，回跳必须标闪回。

可选，有则写、没有不补：章末钩子、爽点、CBN / CPN / CEN。不要为了凑密度给每章编一个打脸点。

**`/webnovel-write 4` 写一章**

流程：整理本章依据 → 起草 → 事实审查 → 只改事实问题 → 登记新事实 → 备份。字数跟你或大纲走，插件不规定章长。`--fast` 减轻审查；`--minimal` 跳过审查修补。

**`/webnovel-review` 查事实**

只查设定、时间线、连贯、角色动机与知识边界、逻辑。不评好不好看、像不像网文。

**`/webnovel-query` 查书内状态**

查角色、伏笔、力量、势力、运行时合同。

**`/webnovel-learn` 记跨章经验**

例如「这条伏笔必须在卷末前回收」。对话腔、句式、节奏偏好请改文风提示词。

**`/webnovel-dashboard` / `/webnovel-doctor`**

只读面板和项目体检，不参与写作。

### 工作区

插件仓库和书稿分开。打开书的**父目录**当工作区，用 `/webnovel-init` 按书名建子目录，不要把书写进本仓库。

安装只有本地：把本仓库加进 Cursor「添加本地目录」，或符号链接到 `~/.cursor/plugins/local/webnovel-writer`。`.cursor-plugin/marketplace.json` 是给本机识别用的，不是官方商店上架包。改完代码后执行 **Developer: Reload Window**。

## 核心能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 深度初始化 | `/webnovel-init` | 收集故事核，生成设定集、总纲和 Story System。金手指 / 卖点可选 |
| 卷纲规划 | `/webnovel-plan` | 基于总纲拆卷、拆章、补时间线。钩子 / 爽点 / CBN 可选 |
| 章节创作 | `/webnovel-write` | 备上下文、起草、事实审查、登记事实、备份。不改文风 |
| 质量审查 | `/webnovel-review` | 审查设定、时间线、连贯、角色动机与逻辑。不评文风 |
| 状态查询 | `/webnovel-query` | 查询角色、伏笔、力量体系和运行时状态 |
| 项目学习 | `/webnovel-learn` | 把跨章事实处理方式写入项目长期记忆 |
| 可视化面板 | `/webnovel-dashboard` | 只读浏览状态、实体图谱 |
| 项目体检 | `/webnovel-doctor` | 检查目录、数据库、RAG、依赖和 Dashboard 产物 |

## 安装

需要 **Python 3.10+** 和 Cursor。

### 1. 安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r scripts/requirements.txt
python -m pip install -r dashboard/requirements.txt
```

Cursor Agent 默认使用系统里的 `python3`。请保证该解释器已安装上述依赖，或把 `.venv/bin` 加进 PATH。

### 2. 把本仓库装成本地 Cursor 插件

Cursor 的 **Settings → Plugins → 添加本地目录** 认的是 **marketplace**，不是单独的 `plugin.json`。本仓库根目录已包含 `.cursor-plugin/marketplace.json`。

**方法 A（推荐，开发与使用同一份代码）：**

```bash
mkdir -p ~/.cursor/plugins/local
ln -s "/absolute/path/to/webnovel-writer-to-cursor" ~/.cursor/plugins/local/webnovel-writer
```

然后执行 **Developer: Reload Window**。

**方法 B：** 在 **Settings → Plugins** 里添加本地目录，选本仓库根目录（必须能看到 `.cursor-plugin/marketplace.json`）。装好后重载窗口，Agent 聊天应能看到 `/webnovel-*`。

### 3. 打开书项目的父目录当工作区

```text
/webnovel-init
```

初始化会按书名在工作区下创建子目录：

```text
workspace/
├── .cursor/webnovel-current-project
└── 你的书名/
    ├── .story-system/
    ├── .webnovel/
    ├── 正文/
    ├── 大纲/
    ├── 设定集/
    │   └── 文风提示词.md
    └── 审查报告/
```

文风、必填项和可选技法见上文「产品使用逻辑」。已有书可把 `templates/output/设定集-文风提示词.md` 复制到该书的 `设定集/文风提示词.md`。

### 4. 配置 RAG（可选）

RAG 只用于从已提交章节补查人物状态、地点、关系、规则/伏笔/承诺状态等结构化事实，不索引章节摘要或场景原文，也不生成文风、桥段或节奏建议。默认写章上下文会自动查询检索库；结果为空不会阻断写作。

进入书项目根目录，把 `.env.example` 复制为 `.env` 并填写 API Key。Embedding Key 是可选增强：未填写时，章节事实仍会建立 BM25 关键词索引并可被默认写作链召回；填写后才增加语义向量召回。Rerank Key 也可留空。

也可以把全局 Key 放在 `~/.cursor/webnovel-writer/.env`。

最小配置：

```bash
EMBED_BASE_URL=https://api-inference.modelscope.cn/v1
EMBED_MODEL=Qwen/Qwen3-Embedding-8B
EMBED_API_KEY=your_embed_api_key

RERANK_BASE_URL=https://api.jina.ai/v1
RERANK_MODEL=jina-reranker-v3
RERANK_API_KEY=your_rerank_api_key
```

### 4.1 子代理模型（可选）

写章用的 `context-agent` / `reviewer` / `data-agent`，以及拆书用的 `deconstruction-agent`，默认跟当前聊天同一个模型。

要单独指定时，改书项目里的 `.webnovel/subagent-models.json`（新书初始化会生成；旧书可从插件 `templates/output/subagent-models.json` 复制）。也可以放一份到 `~/.cursor/webnovel-writer/subagent-models.json`，作为所有书的默认。

```json
{
  "default": "inherit",
  "agents": {
    "context-agent": "inherit",
    "reviewer": "inherit",
    "data-agent": "kimi-k3-max",
    "deconstruction-agent": "inherit"
  }
}
```

`inherit` 或不写 = 不传 Task 的 `model`。填具体值时必须是 Cursor Task 当前允许的模型 id，不要用展示名。本轮对话里点名的模型优先于这个文件。

查询当前生效配置：

```bash
python3 -X utf8 "<PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" subagent-models --format json
```

### 5. 开始写

按「产品使用逻辑」走：`/webnovel-plan 1` → `/webnovel-write 1`。需要时再 `/webnovel-review`、`/webnovel-query`、`/webnovel-dashboard`。

## CLI

所有命令行工具从 `scripts/webnovel.py` 进入：

```bash
python3 -X utf8 "<PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" <子命令> [参数]
```

常用：`preflight`、`where`、`doctor`、`write-gate`、`chapter-commit`、`projections`、`subagent-models`。

插件根定位：环境变量 `WEBNOVEL_PLUGIN_ROOT` / `CURSOR_PLUGIN_ROOT`，或 `~/.cursor/plugins/local/webnovel-writer`。Skill 会先 `eval` `scripts/export_cursor_env.py`。

## 移植对照

Python 引擎、Story System、设定/大纲模板来自上游 v6.2.1。本仓库改了两件事：Cursor 宿主适配，以及默认产品收缩为长期一致性（不注入写法教程，规划不强制钩子/爽点，初始化不强制金手指与卖点公式）。

| 上游 Claude Code | 本仓库 Cursor |
|------|------|
| `.claude-plugin/plugin.json` | `.cursor-plugin/plugin.json` |
| `CLAUDE_PLUGIN_ROOT` | `CURSOR_PLUGIN_ROOT` / `WEBNOVEL_PLUGIN_ROOT` |
| `CLAUDE_PROJECT_DIR` | `CURSOR_PROJECT_DIR` |
| Agent 工具 `webnovel-writer:context-agent` | Task 工具 + `agents/*.md` |
| AskUserQuestion | AskQuestion |
| `.claude/.webnovel-current-project` | 同时写 `.cursor/webnovel-current-project` |

## 版本

| 版本 | 说明 |
|------|------|
| **v6.2.1 (当前)** | 上游引擎 v6.2.1 的 Cursor 本地插件。只守事实一致性；文风只读 `设定集/文风提示词.md` 或按模型默认写。未上架官方商店。 |

## 开源协议

GNU GPL v3。见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。

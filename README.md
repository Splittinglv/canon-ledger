# Webnovel Writer for Cursor

[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-6.2.1-brightgreen.svg)](.cursor-plugin/plugin.json)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)

让 AI 写到几百章，依然记得住设定、接得住伏笔、守得住大纲。

这是 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) **v6.2.1** 的 Cursor 插件移植（GPL-3.0 衍生作品），仓库：[Splittinglv/webnovel-writer-cursor](https://github.com/Splittinglv/webnovel-writer-cursor)。**不是**上游官方发行版，也**没有**上架 Cursor 官方插件商店。出处见 [NOTICE.md](NOTICE.md)。

## 插件现状

当前 `main` 就是正在用的插件。写章只守事实和剧情，不再把正文拧成同一套网文腔。

| 现在会做 | 现在不会做 |
|---------|-----------|
| 按总纲 / 卷纲 / 章纲推进 | 润色成「网文口感」、Anti-AI 终检、风格适配 |
| 核对设定、时间线、伏笔、角色知识边界 | 把书面语 / 口语 / 句式当缺陷来改 |
| 登记本章新事实，写入 Story System | 强制每章都安排打脸爽点 |
| 章纲保留目标、阻力、代价、时间、节点、章末钩子 | 评价或统一文风 |

文风只看书项目里的 `设定集/文风提示词.md`，作者手改。留空则按当前模型默认写。

规划仍要求每章有章末未闭合问题和钩子；爽点字段可写「无」，不算失败。

安装方式只有本地：把本仓库加进 Cursor「添加本地目录」，或符号链接到 `~/.cursor/plugins/local/webnovel-writer`。`.cursor-plugin/marketplace.json` 是给本机识别用的，不是官方商店上架包。改完代码后执行 **Developer: Reload Window**。

插件仓库和书稿要分开。打开书的**父目录**当工作区，用 `/webnovel-init` 按书名建子目录，不要把书写进本仓库。

Python 引擎、Story System、设定/大纲模板来自上游 v6.2.1。本仓库改的是 Cursor 宿主适配（skills / agents / hooks / 路径），以及写章不再改文风、规划不强制每章爽点、子代理模型可配置。

## 核心能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 深度初始化 | `/webnovel-init` | 分阶段问答，搭书的骨架、设定集、总纲和初始状态 |
| 卷纲规划 | `/webnovel-plan` | 基于总纲拆卷、拆章、补时间线 |
| 章节创作 | `/webnovel-write` | 备上下文、起草、事实审查、登记事实、备份。不改文风 |
| 质量审查 | `/webnovel-review` | 审查设定、时间线、连贯、角色动机与逻辑。不评文风 |
| 状态查询 | `/webnovel-query` | 查询角色、伏笔、节奏和运行时信息 |
| 项目学习 | `/webnovel-learn` | 把好用的写法写入项目长期记忆 |
| 可视化面板 | `/webnovel-dashboard` | 只读浏览状态、实体图谱和追读力 |
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
    │   └── 文风提示词.md   ← 只改这里控制文风
    └── 审查报告/
```

打开 `设定集/文风提示词.md`，在「作者提示词」下写下你的要求（视角、句长、禁忌修辞、想贴近的作品）。留空则按当前模型默认写。

已有书项目可把 `templates/output/设定集-文风提示词.md` 复制到该书的 `设定集/文风提示词.md`。

### 4. 配置 RAG（可选）

进入书项目根目录，把 `.env.example` 复制为 `.env` 并填写 API Key。没填 Embedding Key 也能用，系统会退回 BM25。

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

```text
/webnovel-plan 1
/webnovel-write 1
/webnovel-review 1-5
/webnovel-query 伏笔
/webnovel-dashboard
```

## CLI

所有命令行工具从 `scripts/webnovel.py` 进入：

```bash
python3 -X utf8 "<PLUGIN_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" <子命令> [参数]
```

常用：`preflight`、`where`、`doctor`、`write-gate`、`chapter-commit`、`projections`、`subagent-models`。

插件根定位：环境变量 `WEBNOVEL_PLUGIN_ROOT` / `CURSOR_PLUGIN_ROOT`，或 `~/.cursor/plugins/local/webnovel-writer`。Skill 会先 `eval` `scripts/export_cursor_env.py`。

## 移植对照

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
| **v6.2.1 (当前)** | 上游引擎 v6.2.1 的 Cursor 本地插件。写章只守事实/剧情；文风只读 `设定集/文风提示词.md`；规划不强制每章爽点；子代理模型可配置。未上架官方商店。 |

## 开源协议

GNU GPL v3。见 [LICENSE](LICENSE) 与 [NOTICE.md](NOTICE.md)。

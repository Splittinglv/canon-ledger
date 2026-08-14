# 叙典 CanonLedger

[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-7.0.0-brightgreen.svg)](.cursor-plugin/plugin.json)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)

记住故事事实，不替你决定文风。

**叙典 CanonLedger** 是由 **Splittinglv** 独立维护的长篇小说一致性引擎。它让 AI 写到几百章后仍然记得住设定、接得住伏笔、守得住章纲；具体的写作风格和文笔由作者与当前模型自定义。

项目最初基于 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) v6.2.1 移植，自 2026-08-13 起由 Splittinglv 独立维护，不跟踪上游后续版本，也不是上游的官方产品。历史派生范围和许可信息见 [NOTICE.md](NOTICE.md) 与 [ATTRIBUTION.md](ATTRIBUTION.md)。当前可访问的仓库仍为 [Splittinglv/webnovel-writer-cursor](https://github.com/Splittinglv/webnovel-writer-cursor)，仓库地址可在后续版本独立迁移。

叙典的默认产品边界是**长期一致性状态机**：插件负责故事别写崩，不负责把句子写成某种网文腔。

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

仍生效的世界规则、未回收伏笔、未兑现承诺和当前人物关系属于硬约束：不会因“只取前几条”、章节距离或上下文预算被静默丢弃。预算不足时只裁检索命中、近期索引变化等软证据；硬约束自身超限会明确阻断并报告，而不是带着残缺记忆继续写。

初始化时作者明确给出的主角欲望与缺陷、世界规模、势力、力量体系和金手指代价会写入闭合结构的 `MASTER_SETTING.initial_canon`，从首章开始进入一致性上下文；不会把初始化模板里的文风或题材处方混进去。写第 N 章时，人物状态、关系、时间线、一般事实、伏笔和承诺统一从正文绑定且已接受的第 `1..N-1` 章提交重放；历史改写不会读取未来章的当前快照。四类运行合同缺失或历史正文绑定失效时会明确阻断，不会把“读不到”伪装成“没有约束”。

规划阶段后续写回 `设定集/*.md` 的新角色、地点、势力、规则和能力限制，会在刷新 Story System 时生成带来源路径、字节数和 SHA-256 的闭合设定快照，并从下一章起进入 canon。写回使用“字段：值”、项目符号或 Markdown 表格；设定文件改了却没刷新合同时，写作上下文会报 `stale_setting_canon` 并阻断。`设定集/文风提示词.md` 以及反转、爽点、节奏、句式等创作处方不会进入这份事实快照。

同一章重新提交时，新的正文绑定提交会替换该章旧版本，并从全部当前 canonical commits 顺序重建状态、实体索引、时间线、伏笔/承诺、事件、摘要与检索库；旧版本删除的事实不会残留。`projections retry` 会识别缺失或失败的读模型，`projections replay` 始终执行隔离构建、校验、原子安装；中途失败保留原读模型，不把半套结果装进项目。

模型生成的自由文本摘要不会直接进入默认写作包；历史承接改由已绑定正文的结构化事件、状态、硬约束和事实型 RAG 提供，避免摘要里的写法建议被误当成默认文风。
模型抽取的世界规则也只能描述故事世界内的人、物、制度或环境；以章节、段落、句子、读者或作者为对象的反转、悬念、爽点等章法配方会被拒绝，不能伪装成硬 canon。

事实审查只有三种明确模式：`standard` 完整检查设定、时间线、连贯、角色与逻辑；`fast` 只检查前三项并把未检查维度标成降级；`minimal` 明确记录“跳过审查”，绝不伪装成完整通过。审查不再生成文笔评分、节奏评分或所谓 AI 味反模式。无 Git 时的本地备份会保存正文、大纲、设定集、完整 Story System 与必要运行状态，并用签名文件清单验真；恢复前先留救援快照，安装后统一重建投影。

### 文风怎么自定义

只改书项目里的 `设定集/文风提示词.md`，写在「作者提示词」标题下面。插件不会用词库覆盖它。

- 可以写视角、句长、对话习惯、禁忌修辞、想贴近的作品
- 留空就按当前模型自己的文风写
- 剧情、设定、伏笔不要写进这个文件，那些走大纲和设定集
- 口吻偏好也不要丢给 `/canon-ledger-learn`；学习命令只记跨章事实（伏笔怎么收、时间怎么接），不记文笔

已有书可把 `templates/output/设定集-文风提示词.md` 复制到该书的 `设定集/文风提示词.md`。

### 命令怎么用

日常顺序：开书父目录 → `/canon-ledger-init` → 按需改文风提示词 → `/canon-ledger-plan` → `/canon-ledger-write`。审查、查询、学习按需插入。

**`/canon-ledger-init` 开新书**

必收：书名、题材、规模（字数或章数）、主角姓名 / 欲望 / 缺陷、世界规模、力量体系。确认摘要后才生成项目。

不说就不问、不阻断：金手指、反套路、卖点公式、题材套路库、参考书拆解。想拆书当灵感，要先明确选「从参考书开始」，拆完经你确认才会写进项目。

**`/canon-ledger-plan 1` 拆卷拆章**

必写：卷摘要、关键人物、伏笔；每章的目标、时间锚点、章内时间跨度、与上章时间差、倒计时（无则写无）、关键实体、本章变化、本章禁区。时间线是硬约束，回跳必须标闪回。

可选，有则写、没有不补：章末钩子、爽点、CBN / CPN / CEN。不要为了凑密度给每章编一个打脸点。

章纲中的目标、阻力、代价、时间信息、本章变化、核心冲突、视角、关键实体、结构化节点、禁区、章末未闭合问题与钩子会完整按结构进入章合同，并作为写前必达约束；即使上下文预算裁掉章纲原文，这些结构化字段也不会被裁掉。现代章合同的目标为空时，写前门禁和直接提交都会独立阻断；成功提交会把权威目标写入 `outline_snapshot.goal`。提交门禁还会将权威 `must_cover_nodes` 与 data-agent 的完成清单逐项核对，不能用空列表绕过。章纲字段视为作者或规划模型已经授权的输入，因此不会靠关键词猜测并删除内容；插件自身仍不会往里补文风处方。要稳定指定文风与文笔，请使用本轮要求或 `设定集/文风提示词.md`。

**`/canon-ledger-write 4` 写一章**

流程：整理本章依据 → 起草 → 事实审查 → 只改事实问题 → 登记新事实 → 备份。字数跟你或大纲走，插件不规定章长。`--fast` 减轻审查；`--minimal` 跳过审查修补。

**`/canon-ledger-review` 查事实**

只查设定、时间线、连贯、角色动机与知识边界、逻辑。不评好不好看、像不像网文。审查说明、证据解释、修复方向和结论统一使用自然中文，正文证据引用保持原文；JSON 字段、固定枚举、路径和错误码等技术标识保持原样。

**`/canon-ledger-query` 查书内状态**

查角色、伏笔、力量、势力、运行时合同。

**`/canon-ledger-learn` 记跨章经验**

例如「这条伏笔必须在卷末前回收」。对话腔、句式、节奏偏好请改文风提示词。

**`/canon-ledger-dashboard` / `/canon-ledger-doctor`**

只读面板和项目体检，不参与写作。

### 工作区

插件仓库和书稿分开。打开书的**父目录**当工作区，用 `/canon-ledger-init` 按书名建子目录，不要把书写进本仓库。

安装只有本地：把本仓库加进 Cursor「添加本地目录」，或符号链接到 `~/.cursor/plugins/local/canon-ledger`。`.cursor-plugin/marketplace.json` 是给本机识别用的，不是官方商店上架包。改完代码后执行 **Developer: Reload Window**。

## 核心能力

| 能力 | 命令 | 说明 |
|------|------|------|
| 深度初始化 | `/canon-ledger-init` | 收集故事核，生成设定集、总纲和 Story System。金手指 / 卖点可选 |
| 卷纲规划 | `/canon-ledger-plan` | 基于总纲拆卷、拆章、补时间线。钩子 / 爽点 / CBN 可选 |
| 章节创作 | `/canon-ledger-write` | 备上下文、起草、事实审查、登记事实、备份。不改文风 |
| 质量审查 | `/canon-ledger-review` | 审查设定、时间线、连贯、角色动机与逻辑。不评文风 |
| 状态查询 | `/canon-ledger-query` | 查询角色、伏笔、力量体系和运行时状态 |
| 项目学习 | `/canon-ledger-learn` | 把跨章事实处理方式写入项目长期记忆 |
| 可视化面板 | `/canon-ledger-dashboard` | 只读浏览状态、实体图谱 |
| 项目体检 | `/canon-ledger-doctor` | 检查目录、数据库、RAG、依赖和 Dashboard 产物 |

## 安装

需要 **Python 3.10+** 和 Cursor。

### 1. 安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r scripts/requirements.txt
python -m pip install -r dashboard/requirements.txt
```

插件不会假定 Cursor 调用到的系统 `python3` 已安装依赖。启动脚本会依次选择：显式设置的 `CANON_LEDGER_PYTHON`、插件目录下的 `.venv`、`~/.cursor/canon-ledger/.venv`，最后才检查当前或系统解释器；只有能导入插件依赖的解释器才会被使用。

本地目录安装按上面的命令在仓库根创建 `.venv` 即可。若 Cursor 使用的是复制到缓存中的插件包，建议创建共享运行环境：

```bash
python3 -m venv ~/.cursor/canon-ledger/.venv
~/.cursor/canon-ledger/.venv/bin/python -m pip install -r "/absolute/path/to/canon-ledger/scripts/requirements.txt"
~/.cursor/canon-ledger/.venv/bin/python -m pip install -r "/absolute/path/to/canon-ledger/dashboard/requirements.txt"
```

需要固定解释器时，可在启动 Cursor 前设置 `CANON_LEDGER_PYTHON=/absolute/path/to/python`。该解释器缺少依赖时，插件会明确报错并停止受保护写入，不会静默改用一个不完整的 Python。

### 2. 把本仓库装成本地 Cursor 插件

Cursor 的 **Settings → Plugins → 添加本地目录** 认的是 **marketplace**，不是单独的 `plugin.json`。本仓库根目录已包含 `.cursor-plugin/marketplace.json`。

**方法 A（推荐，开发与使用同一份代码）：**

```bash
mkdir -p ~/.cursor/plugins/local
ln -s "/absolute/path/to/canon-ledger" ~/.cursor/plugins/local/canon-ledger
```

然后执行 **Developer: Reload Window**。

**方法 B：** 在 **Settings → Plugins** 里添加本地目录，选本仓库根目录（必须能看到 `.cursor-plugin/marketplace.json`）。装好后重载窗口，Agent 聊天应能看到 `/canon-ledger-*`。

### 3. 打开书项目的父目录当工作区

```text
/canon-ledger-init
```

初始化会按书名在工作区下创建子目录：

```text
workspace/
├── .cursor/canon-ledger-current-project
└── 你的书名/
    ├── .story-system/
    ├── .canon-ledger/
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

也可以把全局 Key 放在 `~/.cursor/canon-ledger/.env`。

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

要单独指定时，改书项目里的 `.canon-ledger/subagent-models.json`（新书初始化会生成；旧书可从插件 `templates/output/subagent-models.json` 复制）。也可以放一份到 `~/.cursor/canon-ledger/subagent-models.json`，作为所有书的默认。

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
python3 -X utf8 "<PLUGIN_ROOT>/scripts/canon_ledger.py" --project-root "<PROJECT_ROOT>" subagent-models --format json
```

### 5. 开始写

按「产品使用逻辑」走：`/canon-ledger-plan 1` → `/canon-ledger-write 1`。需要时再 `/canon-ledger-review`、`/canon-ledger-query`、`/canon-ledger-dashboard`。

## CLI

所有命令行工具从 `scripts/canon_ledger.py` 进入：

```bash
python3 -X utf8 "<PLUGIN_ROOT>/scripts/canon_ledger.py" --project-root "<PROJECT_ROOT>" <子命令> [参数]
```

常用：`preflight`、`where`、`doctor`、`write-gate`、`chapter-commit`、`projections`、`subagent-models`。

插件根定位：环境变量 `CANON_LEDGER_PLUGIN_ROOT` / `CURSOR_PLUGIN_ROOT`，或 `~/.cursor/plugins/local/canon-ledger`。Skill 把 `scripts/export_cursor_env.py` 的固定 JSON 当数据解析，不执行其输出，也不扫描缓存目录寻找脚本。

## 产品边界与历史来源

叙典采用独立产品名、独立命令和独立数据空间：`/canon-ledger-*`、`scripts/canon_ledger.py`、`.canon-ledger/` 与 `CANON_LEDGER_*` 是唯一受支持的当前接口。本版本按全新产品发布，不提供旧插件命令、环境变量、运行目录或项目数据的迁移入口。

历史派生组件、基线提交和主要修改范围见 [ATTRIBUTION.md](ATTRIBUTION.md)；原始许可和非官方关系声明见 [NOTICE.md](NOTICE.md)。测试中的剧情、章纲、文风指令和审查问题统一使用自然中文句子；JSON 字段、枚举、命令、路径、错误码与 agent 名等协议标识保留原值。

## 版本

| 版本 | 说明 |
|------|------|
| **v7.0.0 (当前)** | 更名为叙典 CanonLedger，启用独立命令、运行目录与产品身份。 |
| **v6.2.2** | 长期一致性真源可重建、设定写回可验证；文风仍由作者或模型决定。 |
| **v6.2.1** | 上游引擎 v6.2.1 的 Cursor 本地插件。只守事实一致性；文风只读 `设定集/文风提示词.md` 或按模型默认写。未上架官方商店。 |

## 开源协议

GNU GPL v3。见 [LICENSE](LICENSE)、[NOTICE.md](NOTICE.md)、[ATTRIBUTION.md](ATTRIBUTION.md) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。作者与维护关系见 [AUTHORS.md](AUTHORS.md)。

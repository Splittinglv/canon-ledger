---
name: webnovel-plan
description: 基于总纲规划卷纲、时间线和章纲，并把新增设定写回设定集。用户说规划卷纲、拆章、/webnovel-plan 时使用。
---

# Outline Planning

主 agent 职责：基于总纲增量细化卷纲/时间线/章纲，把新增设定写回设定集，并刷新 Story System 写作合同。不重做全局故事，不重写整份总纲或设定集。

## 执行原则

1. 只做增量补齐，不重写整份总纲或设定集。
2. 先锁定本卷冲突与时间跨度，再批量拆章。
3. 时间线是硬约束，所有章纲必须带时间字段。
4. 若发现总纲与设定冲突，先阻断，再等用户裁决。
5. 优先级链：用户明确要求 > 总纲核心冲突与卷末高潮 > 时间线硬约束 > skill 默认流程 > reference 建议。

## 阻断条件

- 项目根不合法或总纲缺失。
- 总纲缺少卷名 / 章节范围 / 核心冲突 / 卷末高潮 → 阻断并请求用户补全。
- Step 2 / Step 8 发现设定冲突 → 标记 `BLOCKER`，等待用户裁决。
- 批量拆章时时间回跳且未标注闪回 → 阻断当前批次。
- Step 9 验证失败 → 只重做失败批次，不覆盖整卷。

## 环境准备

```bash
# 这段引导仅适用于 POSIX shell（sh/bash/zsh）；Windows 请使用 Git Bash 或 WSL。
# 缓存安装必须使用 Cursor 注入的插件根；不扫描缓存目录寻找可执行脚本。
_PLUGIN_ROOT_HINT="${WEBNOVEL_PLUGIN_ROOT:-${CURSOR_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}}"
if [ -z "$_PLUGIN_ROOT_HINT" ]; then
  _PLUGIN_ROOT_HINT="${HOME}/.cursor/plugins/local/webnovel-writer"
fi
_EXPORTER="$(python3 -X utf8 -c '
import json, sys
from pathlib import Path
try:
    root = Path(sys.argv[1]).expanduser().resolve()
    manifest = json.loads((root / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8"))
    exporter = (root / "scripts" / "export_cursor_env.py").resolve()
except (OSError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if manifest.get("name") != "webnovel-writer":
    raise SystemExit(1)
if exporter.parent.parent != root or not exporter.is_file() or not (root / "scripts" / "webnovel.py").is_file():
    raise SystemExit(1)
print(exporter)
' "$_PLUGIN_ROOT_HINT")" || {
  echo "ERROR: 插件根不可信或安装不完整。请使用 Cursor 注入的插件根，或安装到 ~/.cursor/plugins/local/webnovel-writer" >&2
  exit 1
}
_ENV_JSON="$(python3 -X utf8 "$_EXPORTER" --format json)" || exit 1
_ENV_LINES="$(printf '%s' "$_ENV_JSON" | python3 -X utf8 -c '
import json, sys
from pathlib import Path
keys = (
    "WEBNOVEL_PLUGIN_ROOT", "CURSOR_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT",
    "SCRIPTS_DIR", "WORKSPACE_ROOT", "CURSOR_PROJECT_DIR",
)
try:
    payload = json.load(sys.stdin)
    environment = payload["environment"]
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
if payload.get("schema_version") != "webnovel-cursor-env/v1" or not isinstance(environment, dict):
    raise SystemExit(1)
if set(environment) != set(keys):
    raise SystemExit(1)
values = [environment[key] for key in keys]
if any(not isinstance(value, str) or not value or any(char in value for char in "\x00\r\n") for value in values):
    raise SystemExit(1)
if values[1] != values[0] or values[2] != values[0] or values[3] != str(Path(values[0]) / "scripts") or values[5] != values[4]:
    raise SystemExit(1)
sys.stdout.write("\n".join(values) + "\n")
')" || {
  echo "ERROR: export_cursor_env.py 返回了无效环境协议" >&2
  exit 1
}
_ENV_PARSE_OK=1
{
  IFS= read -r WEBNOVEL_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CLAUDE_PLUGIN_ROOT || _ENV_PARSE_OK=0
  IFS= read -r SCRIPTS_DIR || _ENV_PARSE_OK=0
  IFS= read -r WORKSPACE_ROOT || _ENV_PARSE_OK=0
  IFS= read -r CURSOR_PROJECT_DIR || _ENV_PARSE_OK=0
} <<EOF
$_ENV_LINES
EOF
if [ "$_ENV_PARSE_OK" -ne 1 ]; then
  echo "ERROR: 无法解析插件环境协议" >&2
  exit 1
fi
export WEBNOVEL_PLUGIN_ROOT CURSOR_PLUGIN_ROOT CLAUDE_PLUGIN_ROOT SCRIPTS_DIR WORKSPACE_ROOT CURSOR_PROJECT_DIR
unset _PLUGIN_ROOT_HINT _EXPORTER _ENV_JSON _ENV_LINES _ENV_PARSE_OK
export SKILL_ROOT="${WEBNOVEL_PLUGIN_ROOT}/skills/webnovel-plan"
export PROJECT_ROOT="$(python "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

规划开始 / 结束都运行 `placeholder-scan`；plan 阶段发现占位先警告并补齐相关文件，进入写章前不得保留当前章相关实体的 `[待...]` / `暂名` / `{占位}`。

## 读取策略（按阶段触发，不预读全部 reference）

每个 reference 只在对应 Step 触发时读取，且优先区段读：先用 `Grep` 匹配 `^#{2,4} ` 定位标题锚点行号，再用 `Read` 的 offset/limit 取目标段。

| 触发 | 读取方式 | 文件 |
|------|---------|------|
| Step 4 | 全文 | `${SKILL_ROOT}/../../templates/output/大纲-卷节拍表.md` |
| Step 5 | 全文 | `${SKILL_ROOT}/../../templates/output/大纲-卷时间线.md` |
| 章纲拆分 always | 区段 | `${SKILL_ROOT}/../../references/outlining/plot-signal-vs-spoiler.md` |
| Step 6/7 需要冲突 | 区段 | `${SKILL_ROOT}/references/outlining/conflict-design.md` |

CSV 检索只用于命名区分，不 `cat` 整表。不要检索场景写法、写作技法、桥段套路或爽点与节奏。

```bash
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 命名规则 --query "角色命名" --genre "${GENRE}"
```

## 执行流程

### Step 1：加载项目数据并确认前置条件

```bash
# 项目配置/投影状态（兼容读取，不作为写后事实真源）
cat "$PROJECT_ROOT/.webnovel/state.json"

# 总纲（全局蓝图）；确认卷名/章节范围/核心冲突/卷末高潮，不足则阻断
cat "$PROJECT_ROOT/大纲/总纲.md"

# 题材（来自 init 配置快照，后续 CSV 检索和裁决匹配依赖此值）；写后主链真源仍是 .story-system/
GENRE="$(python -X utf8 -c "import json; s=json.load(open('${PROJECT_ROOT}/.webnovel/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"
```

按需读取设定集：`设定集/世界观.md`、`设定集/力量体系.md`、`设定集/主角卡.md`、`设定集/反派设计.md`、`.webnovel/idea_bank.json`。

**跨卷状态读取**（已有已完成卷，即 `.webnovel/summaries/` 下有文件时必须执行）：

```bash
# 最近 5 章摘要
for ch in $(seq $((START_CH - 5)) $((START_CH - 1))); do
  cat "$PROJECT_ROOT/.webnovel/summaries/ch$(printf '%04d' $ch).md" 2>/dev/null
done

# 核心角色当前状态 / 核心关系当前状态 / 活跃伏笔（跨卷未回收）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" knowledge query-entity-state --entity "{protagonist_id}" --at-chapter {上一卷最后章}
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" knowledge query-relationships --entity "{protagonist_id}" --at-chapter {上一卷最后章}
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" memory-contract get-open-loops
```

### Step 2：补齐设定基线

让设定集从骨架进入"可规划、可写作"。增量补齐，不清空、不重写整文件；发现冲突先列出并阻断。

- `设定集/世界观.md`：世界边界、社会结构、关键地点用途。
- `设定集/力量体系.md`：境界链、限制、代价与冷却。
- `设定集/主角卡.md`：欲望、缺陷、初始资源与限制。
- `设定集/反派设计.md`：小/中/大反派层级与镜像关系。

### Step 3：选择目标卷并确认范围

确认卷名、章节范围、核心冲突，以及是否有特殊要求（视角、情感线、题材偏移）。

### Step 4：生成卷节拍表

加载模板 `${SKILL_ROOT}/../../templates/output/大纲-卷节拍表.md`。

硬要求：无。中段反转、危机链、卷末钩子可写，不写不算失败。

输出文件：`大纲/第{volume_id}卷-节拍表.md`

### Step 5：生成卷时间线表

加载模板 `${SKILL_ROOT}/../../templates/output/大纲-卷时间线.md`。

硬要求：必须明确时间体系与本卷时间跨度；有倒计时事件时列出并标记 D-N。

输出文件：`大纲/第{volume_id}卷-时间线.md`

### Step 6：生成卷纲骨架

必读时间线与总纲。不加载网文章法、追读力或爽点教程。

卷纲必须明确：卷摘要、关键人物、伏笔规划。Strand 分布、反派层级、约束触发、爽点密度均可选：有则写，没有不补、不因此失败。

跨卷一致性检查（非首卷必须执行）：

- 上一卷未回收的伏笔必须出现在新卷伏笔规划中（继续推进或标记回收）。
- 角色关系变化必须延续，不能当上一卷没发生过。
- 主角能力 / 境界必须承接，不回退也不跳级（除非有剧情解释）。

### Step 7：批量生成章纲

批次规则：默认 `10章/批`；复杂题材或多线并进降到 `8章/批`；简单升级流放宽到 `12章/批`；不建议单批超过 `12章`。

按需读取冲突设计。不加载追读力分类或网文章法教程。

每章必须包含：目标、时间锚点、章内时间跨度、与上章时间差、倒计时状态（无则写无）、关键实体、本章变化、本章禁区。

可选：阻力、代价、核心冲突、Strand、反派层级、视角、章末未闭合问题、钩子，以及结构化节点 `CBN`、`CPNs`、`CEN`、`必须覆盖节点`。

**爽点不是必填。** 字段可写 `无`。禁止为了凑密度给每章编一个打脸/装逼点；禁止把「本章无爽点」当成规划失败。

#### 结构化节点

节点格式统一为 `主体 | 动作/变化 | 对象/结果`。用户若自行使用 `CBN` / `CPNs` / `CEN`，按此格式写即可；插件不提供章法教程。

核心约束：

- 若使用节点：每章最多 1 个 `CBN`、若干 `CPN`、最多 1 个 `CEN`。不使用则跳过，不失败。
- 相邻章节若都有 `CEN` / `CBN`，应逻辑承接。
- `必须覆盖节点`最多 4 个；可选节点只作建议。
- `本章禁区`不超过 5 条，只写本章绝对不能发生的硬禁区，不写风格类建议。
- 向后兼容：旧项目章纲缺失上述字段时，下游流程正常执行，仅跳过结构化检查。

输出文件：`大纲/第{volume_id}卷-详细大纲.md`

### Step 8：把新增设定写回现有设定集

输入：卷节拍表、卷时间线表、卷详细大纲、现有设定集文件。

写回规则：只增量补充相关段落；新角色写入角色卡或角色组；新势力 / 地点 / 规则写入世界观或力量体系；新反派层级写入反派设计。

硬规则：若发现与总纲或既有设定冲突，标记 `BLOCKER` 并停止后续更新。

### Step 9：验证、保存并更新状态

必须通过：节拍表 / 时间线表 / 详细大纲均存在且非空；每章时间字段齐全；时间线单调递增；倒计时推进正确；新设定已回写；`BLOCKER=0`。有节点时相邻章节 `CEN -> CBN` 无明显逻辑冲突且每章`必须覆盖节点`不超过 4 个。

验证全部通过后，生成显式结构化写回文件 `大纲/第{volume_id}卷-总纲写回.json`（只写规划中显式列出的伏笔 / 开放环，禁止从卷纲自由文本推断）：

```json
{
  "next_volume_anchor": {
    "volume": 2,
    "volume_name": "下一卷卷名",
    "core_conflict": "下一卷核心冲突",
    "volume_end_climax": "下一卷卷末高潮"
  },
  "foreshadow_writeback": [
    {"content": "本卷规划明确新增的伏笔", "buried_chapter": "第10章", "payoff_chapter": "", "level": "卷级"}
  ],
  "open_loop_writeback": [
    {"content": "本卷结束后仍持续开放的问题", "buried_chapter": "", "payoff_chapter": "", "level": "持续开放环"}
  ]
}
```

执行最小总纲写回（只更新 `大纲/总纲.md` 的 V+1 卷名 / 核心冲突 / 卷末高潮与伏笔表，不生成下一卷详细大纲 / 节拍表 / 时间线 / 章纲）：

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "$PROJECT_ROOT" master-outline-sync \
  --volume {volume_id} \
  --writeback-file "大纲/第{volume_id}卷-总纲写回.json" \
  --format text
```

更新状态：

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "$PROJECT_ROOT" update-state -- \
  --volume-planned {volume_id} \
  --chapters-range "{start}-{end}"
```

### Step 10：刷新 Story System 写作合同（本次规划已落到具体章节时必须执行）

genre 从 `state.json` 初始化配置快照读取；写前主链真源是 `.story-system/` 合同树。必须先从详细大纲解析真实 `CHAPTER_GOAL`，禁止传 `{章纲目标}` / `第N章章纲目标` 这类占位文本。

```bash
GENRE="$(python -X utf8 -c "import json; s=json.load(open('${PROJECT_ROOT}/.webnovel/state.json',encoding='utf-8')); pi=s.get('project_info',{}); print(pi.get('genre') or s.get('project',{}).get('genre',''))")"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" story-system "${CHAPTER_GOAL}" \
  --genre "${GENRE}" --chapter {chapter_num} --persist --emit-runtime-contracts --format both
```

生成后必须把 `.story-system/MASTER_SETTING.json`、`.story-system/volumes/`、`.story-system/chapters/`、`.story-system/reviews/` 视为后续写作主链输入。进入写章前不得保留当前章相关实体的 `[待...]` / `暂名` / `{占位}`。

## 硬失败条件

- 节拍表 / 时间线表 / 详细大纲不存在或为空。
- 任一章节缺少时间字段；时间回跳且未标注闪回；倒计时算术冲突。
- 与总纲核心冲突或卷末高潮明显冲突。
- 存在 `BLOCKER` 未裁决。

## 恢复规则

1. 只重做失败批次，不覆盖整卷文件。
2. 最后一个批次无效时，只删除并重写该批次。
3. 仅在全部验证通过后更新状态。

## 作者友好过程提示与恢复契约

规划开始前先说明本次会经历：检查总纲与设定 -> 生成节拍表 -> 生成时间线 -> 拆章纲 -> 写回新增设定 -> 刷新写作合同。过程提示用作者语言，不直接输出原始 JSON、traceback 或长命令日志；技术详情写入 `.webnovel/logs/run_last.log`：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" run-log \
  --event plan-progress \
  --payload-json "{\"stage\": \"plan\", \"volume\": {volume_id}}" \
  --format text
```

过程提示每次不超过两行，只说当前动作和影响，例如“正在拆本卷章纲：会把每章目标、时间锚点和禁区写清楚”。少打扰确认策略：默认继续推进；只有总纲 / 设定冲突、时间线回跳、卷末钩子取舍、需要覆盖已有规划时才询问。

需要用户裁决时使用有限选项，并说明影响；例如沿用总纲 / 修改设定 / 暂停规划。卡住时必须说明卡点、已完成内容和恢复建议，例如“节拍表和时间线已保留，第 21-30 章拆分失败；重新运行 `/webnovel-plan {volume_id}` 会只重做失败批次”。

不可恢复故障才在最终报告提示 `.webnovel/logs/run_last.log`；平时只保留日志，不打扰作者。收尾必须调用作者报告 helper：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" user-report \
  --stage plan \
  --volume {volume_id} \
  --format text
```

## 作者友好最终报告契约

最终回复必须面向作者，不输出原始 JSON、traceback 或长命令日志。使用固定三段式，并以一句总状态开头：

```text
总状态：已完成 / 部分完成 / 需要你处理 / 未完成。

一、产生的文件与完成情况
- ...

二、过程中遇到的问题与异常耗时
- 已自动处理：...
- 建议确认：...
- 必须处理：...

三、下一步建议
- ...
```

必须汇报：
- `大纲/第{volume_id}卷-节拍表.md`。
- `大纲/第{volume_id}卷-时间线.md`。
- `大纲/第{volume_id}卷-详细大纲.md`。
- 新增设定写回了哪些设定集文件。
- `大纲/第{volume_id}卷-总纲写回.json`。
- `master-outline-sync`、`update-state`、Story System 合同刷新是否完成。
- 占位符、时间线、节点承接是否通过。

异常分类：
- 已自动处理：只重做失败批次、补齐非阻断占位、重跑合同刷新。
- 建议确认：新增角色名、势力名、卷末钩子需要作者看一眼。
- 必须处理：总纲 / 设定冲突、时间线回跳、`BLOCKER` 未裁决、当前章相关占位残留。

下一步建议必须使用任务化语言 + 可复制命令，例如：

```text
- 接下来可以写第一章：
  /webnovel-write 1
```

不写 token 统计；如需排查故障，只给日志路径或建议运行 `/webnovel-doctor`。

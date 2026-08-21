---
name: canon-ledger-learn
description: 把作者明确要求长期保留的文风、口吻、句式和写作偏好写入本书文风提示词；不处理故事事实或硬设定。
---

# 保存长期文风偏好

完整读取 [`../../references/canon-v3-skill-protocol.md`](../../references/canon-v3-skill-protocol.md) 的环境和 style 边界。本 Skill 是非事实例外：在大多数 workflow 状态下都可使用，但永远没有放行写作或修改 Canon 的权限。

## 流程

1. 只提取用户明确要求记住的文风、口吻、句式、叙事方式或写作偏好。参数为空且本轮没有明确偏好时停止询问。
2. 不把剧情、设定、人物事实、伏笔、时间线或 retcon 改写成文风。
3. 把条目写入 `${PROJECT_ROOT}/.canon-ledger/tmp/style-learn.json`：

```json
{"items": ["第一条文风偏好", "第二条文风偏好"]}
```

4. 只通过 style-memory 写入：

```bash
"${CANON_LEDGER_PYTHON}" -X utf8 "${SCRIPTS_DIR}/canon_ledger.py" \
  --project-root "${PROJECT_ROOT}" style-memory add-item \
  --input-file "${PROJECT_ROOT}/.canon-ledger/tmp/style-learn.json"
```

## 不变量

- 只修改 `设定集/文风提示词.md` 的作者提示词区域并去重。
- 不写 hard constraints、author axioms、事实 memory、STAGING 或 HEAD。
- 操作前后 `head_hash/workflow_digest/stage_digest/projection binding/migration digest/cases` 必须完全不变。
- 优先级始终为：本轮用户要求 > 本书文风提示词 > 模型默认。

失败时不手工拼接目标文件；报告权限问题或建议 doctor。

# 叙典 CanonLedger Dashboard 设计规范

Dashboard 是 Canon v3 的只读观察面，不是第二个工作流引擎，也不是人工裁决入口。

## 权威数据边界

- `/api/canon-v3/workflow` 直接展示公开 WorkflowSnapshot，不在前端推断状态。
- 所有事实页面只接受 fresh Canon projection/history，并展示 exact HEAD、generation 和 binding。
- projection stale、migration 或 invalid 时展示结构化错误与 `primary_action`，不能转换为空列表。
- legacy `index.db`、旧 review metrics、旧合同树和 Story Runtime 不能驱动主导航。
- 退役端点保留结构化 410，仅用于让旧客户端发现迁移方向。
- Dashboard 只发 GET；不提供 decide、finalize、cancel 或其他 Canon 写操作。

## 信息架构

### 侧栏

侧栏持续展示：

- exact workflow state；
- HEAD 短哈希与 generation；
- `can_write_next`；
- SSE 连接状态。

### 总览

总览必须直接展示：

- exact workflow state 和协议版本；
- HEAD、generation、workflow digest、authority view digest；
- 目标章、最新 Canon 章、`can_write_next`、`can_finalize`；
- 当前 STAGING、transaction kind、transaction hash；
- 人工 cases、case 状态和 exact `allowed_actions`；
- 唯一 `primary_action` 的 ID、说明和命令。

`primary_action` 仅供阅读和复制。用户应回到对应 CanonLedger Skill 执行。

### 角色图鉴

人物、状态和关系来自一个 HEAD-bound `/api/canon-v3/characters` 响应。页面展示 binding，并保留实体列表、状态历史和章节关系时间轴。读取失败时明确显示结构化 Canon 错误。

### 开放问题

开放问题页调用 `/api/canon-v3/obligations`，只展示 Canon `obligations` 和 `lifecycle_history` 中的 open-loop：

- 活跃问题；
- 提出章；
- 已回收问题、回收章和回收事实；
- fact digest 与 HEAD binding。

不从 `project_info.plot_threads`、大纲、目标章或 legacy index 推导紧急度。

### 文档浏览

文档浏览只读展示正文、大纲和设定集文件。这里的内容不是 Canon 事实视图，不能用来替代 HEAD-bound API。

### 系统状态

系统页展示：

- WorkflowSnapshot 的公开协议字段；
- Canon projection 是否 fresh；
- 当前 HEAD 可达的 v3 commit 历史；
- commit 响应与 workflow 的 HEAD/generation 是否完全一致。

系统页不展示旧五路 projection、Mainline/Fallback、旧合同树或 legacy RAG/index 统计。

## 错误展示

事实接口错误使用 `canon-v3/dashboard-error/v1`。前端至少显示：

- `code`；
- exact workflow state；
- `primary_action`；
- 是否可用于写作。

409 表示当前无法形成 fresh HEAD-bound 事实视图；410 表示 legacy Dashboard 端点已退役。两者都不能静默显示为“没有数据”。

## 视觉规范

整体使用复古像素状态面板风格：

| 变量 | 色值 | 用途 |
|---|---|---|
| `--bg-main` | `#fff7e8` | 页面背景 |
| `--bg-card` | `#fffaf0` | 卡片背景 |
| `--text-main` | `#2a220f` | 主文字 |
| `--accent-blue` | `#26a8ff` | 当前/信息 |
| `--accent-green` | `#2ec27e` | ready/fresh/accepted |
| `--accent-amber` | `#f5a524` | awaiting human/required |
| `--accent-red` | `#d7263d` | stale/invalid/rewrite |

- 卡片使用 3px 硬边框和像素阴影，不使用圆角。
- digest/命令使用等宽字体并允许断行。
- 状态不能只靠颜色表达，必须同时展示 exact 文本。
- 桌面端侧栏 240px；1180px 以下双列降为单列；820px 以下导航改为横向滚动。

## 测试要求

- API 测试验证所有事实响应共享 exact HEAD/generation/workflow/projection binding。
- stale projection 必须返回结构化 409，即使 legacy index 存在也不能泄漏旧事实。
- legacy analytics 必须返回结构化 410。
- Dashboard 路由不得出现 POST/PUT/PATCH/DELETE。
- GET 前后项目文件集合和内容哈希不变。
- 前端单测验证 open-loop 只来自 Canon obligations/lifecycle。
- `npm test` 与 `npm run build` 都必须通过，打包后的 `dist/` 与源码同步。

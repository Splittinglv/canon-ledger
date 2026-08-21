# 来源与修改说明

本文档记录叙典 CanonLedger 的代码来源、生成式 AI 辅助修改过程与主要修改范围。它补充 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)，不替代任何许可证正文或文件内已有声明。

## 上游基线

| 项目 | 信息 |
|------|------|
| 上游项目 | [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) |
| 上游作者 | lingfengQAQ |
| 上游版本 | v6.2.1 |
| 上游提交 | `59654ccaa17f240c5ae41fe51db9443284f8ca1f` |
| 上游许可证 | GNU General Public License v3.0 |
| 导入本仓库的提交 | `ffcabd4f73005fbfb4b4bbb0bf194e0cfdec5986` |
| 导入日期 | 2026-08-13 |

本项目包含对该基线的复制和修改，不能把这些内容仅描述为“受到启发”。历史派生部分与本项目整体继续按照仓库根目录的 [GNU GPL v3](LICENSE) 发布。

## 派生范围

2026-08-13 的基线导入覆盖了插件主体。当前仍应将下列目录或功能视为全部或部分派生，除非后续通过逐文件来源审计证明某个文件已经独立替换：

| 范围 | 来源关系 |
|------|----------|
| `scripts/` 与 Story System | 基于上游 Python 引擎、状态管理和数据模块修改 |
| `templates/` | 基于上游设定、总纲、卷纲、章纲及项目输出模板修改 |
| `references/` | 基于上游写作参考资料、分类和审查资料修改 |
| `dashboard/` | 基于上游 Dashboard 后端、前端源码和构建产物修改 |
| `skills/`、`agents/`、`commands/`、`rules/` | 基于上游写作工作流修改，并适配 Cursor 宿主 |
| `hooks/` 与 `.cursor-plugin/` | 在移植过程中形成的 Cursor 运行、守卫和插件清单适配；与派生插件整体一并按 GPL-3.0 发布 |
| `tests/`、`evals/` 及目录内测试 | 包含对派生行为的验证以及当前产品阶段新增的回归测试 |

该表描述的是仓库级来源关系，不声称目录中的每一行都由上游作者创作。逐次修改的作者、日期和内容以 Git 历史为准。

## 生成式 AI 辅助修改与主要范围

自 2026-08-13 起，本项目不再以同步上游版本为产品目标。Splittinglv 发起产品方向并负责发布，代码、测试、文档和审查大量使用生成式 AI 工具协作完成。主要修改包括：

- 将产品定位收敛为“长篇小说一致性引擎”，默认不规定口吻、句式、章长、爽点或网文风格；
- 建立 Canon v3 单一事实事务链，以不可变 HEAD、typed candidates、逐字段证据、完整事实扫描和 exact 人工决定约束正史发布；
- 将作者认证硬设定纳入 managed author-axiom 通道，并为旧前缀、旧 genesis 与未发布事务提供 fail-closed migration/recertification；
- 将已接受事实与最终正文字节及其章节边界绑定，使历史状态能够按 N-1 时点重放；
- 支持同章改稿后从 Canon v3 HEAD 重建状态、索引、时间线和检索投影，同时禁止 legacy writer/replay 反向修改正史；
- 将章纲目标保留为 advisory，并明确排除文风、文笔、人物动机、一般因果与剧情取舍的默认强制检查；
- 增加 Cursor 插件清单、命令、代理、Hook、运行时路径和本地安装适配；
- 使用自然中文剧情与审查句子进行行为测试，保留必要的协议字段和技术标识。

完整变更记录见 [CHANGELOG.md](CHANGELOG.md) 和 Git 历史。

## 产品与上游的关系

叙典 CanonLedger 是单独命名和发布的产品线，不跟踪上游的后续提交、版本或路线图，也不是 lingfengQAQ/webnovel-writer 的官方发行版。改变产品方向不等于抹除历史来源；任何再分发版本都应继续保留适用的 GPL 许可、上游归属和修改说明。

当前仓库地址为 <https://github.com/Splittinglv/canon-ledger>。仓库更名不改变本文件记录的历史来源。

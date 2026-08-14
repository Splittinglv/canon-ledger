# 来源与修改说明

本文档记录叙典 CanonLedger 的代码来源、独立维护边界与主要修改范围。它补充 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)，不替代任何许可证正文或文件内已有声明。

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
| `tests/`、`evals/` 及目录内测试 | 包含对派生行为的验证以及独立维护阶段新增的回归测试 |

该表描述的是仓库级来源关系，不声称目录中的每一行都由上游作者创作。逐次修改的作者、日期和内容以 Git 历史为准。

## 独立维护与主要修改

自 2026-08-13 起，Splittinglv 不再以同步上游版本为产品目标。本仓库的独立维护方向包括：

- 将产品定位收敛为“长篇小说一致性引擎”，默认不规定口吻、句式、章长、爽点或网文风格；
- 为设定、人物状态、关系、时间线、伏笔、承诺与章纲目标建立硬约束注入和写前门禁；
- 将已接受事实与最终正文字节绑定，使历史状态能够按章节边界重放；
- 支持同章改稿后从 canonical commits 重建状态、索引、时间线和检索投影；
- 增加 Cursor 插件清单、命令、代理、Hook、运行时路径和本地安装适配；
- 使用自然中文剧情与审查句子进行行为测试，保留必要的协议字段和技术标识。

完整变更记录见 [CHANGELOG.md](CHANGELOG.md) 和 Git 历史。

## 产品与上游的关系

叙典 CanonLedger 是独立维护产品，不跟踪上游的后续提交、版本或路线图，也不是 lingfengQAQ/webnovel-writer 的官方发行版。独立维护不等于抹除历史来源；任何再分发版本都应继续保留适用的 GPL 许可、上游归属和修改说明。

当前仓库地址仍为 <https://github.com/Splittinglv/webnovel-writer-cursor>。产品 slug 已采用 `canon-ledger`，仓库地址可在后续迁移；迁移不改变本文件记录的历史来源。

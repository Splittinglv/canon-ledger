# NOTICE

## 产品身份

**叙典 CanonLedger** 是长篇小说一致性引擎，由 **Splittinglv** 发起并发布，代码、测试、文档与审查过程大量使用生成式 AI 工具辅助完成。

本项目自 2026-08-13 起采用自己的产品定位和版本规划，不跟踪上游仓库的后续版本。它不是上游维护者的官方产品，也不表示上游维护者对本项目提供认可、担保或支持。

当前代码仓库为 <https://github.com/Splittinglv/canon-ledger>。

## 历史来源

本项目最初由下列项目移植而来，并继续包含其派生内容：

- 上游项目：<https://github.com/lingfengQAQ/webnovel-writer>
- 上游版本：v6.2.1
- 上游基线提交：`59654ccaa17f240c5ae41fe51db9443284f8ca1f`
- 上游作者：lingfengQAQ
- 上游许可证：GNU General Public License v3.0
- 本仓库导入提交：`ffcabd4f73005fbfb4b4bbb0bf194e0cfdec5986`（2026-08-13）

派生范围包括 Python 引擎（`scripts/`）、Story System、模板（`templates/`）、参考资料（`references/`）、Dashboard、写作工作流，以及相应的命令、代理、测试和宿主适配代码。更细的来源和修改说明见 [ATTRIBUTION.md](ATTRIBUTION.md)。

## 后续修改

在 Splittinglv 发起的产品方向下，本项目使用生成式 AI 工具辅助完成了 Cursor 宿主适配，并将默认产品边界重构为长篇小说长期一致性：保存和重放设定、人物状态、关系、时间线、伏笔、承诺、章纲目标及正文绑定事实，同时把具体写作风格与文笔留给作者和所选模型。

上述修改不改变历史派生代码的来源，也不改变本项目作为 GPL-3.0 衍生作品的许可义务。原始贡献的权利归各自权利人所有；各次修改的形成过程、日期和提交身份以 Git 历史为准。这里不会把生成式 AI 辅助内容描述为某一自然人的单独创作，整个项目继续在 GNU GPL v3 下发布。

完整许可条款见 [LICENSE](LICENSE)，第三方组件见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。分发或再修改本项目时，请保留适用的版权、许可、来源与修改声明。

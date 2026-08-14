# 第三方组件声明

叙典 CanonLedger 包含或依赖第三方开源组件。各组件仍由各自的权利人持有权利，并适用其各自许可证；仓库整体采用 GNU GPL v3 不会替换这些第三方许可证。

本清单根据 2026-08-14 仓库中实际提交的依赖文件和构建产物整理：

- 前端解析版本及许可证字段来自 `dashboard/frontend/package-lock.json`；
- 随仓库提交的 `dashboard/frontend/dist/` 含前端运行时代码，因此列出完整的前端运行时依赖闭包；
- Python 依赖只在 requirements 文件中声明版本范围，仓库没有提交相应的 `site-packages`，因此不把某个未锁定版本或许可证臆断为随包内容。

## Dashboard 前端运行时

| 包 | 锁定版本 | 许可证 | 关系 | 随包许可正文 |
|----|----------|--------|------|----------------|
| `echarts` | 6.1.0 | Apache-2.0 | 直接依赖 | [LICENSE](third_party_licenses/npm/echarts/LICENSE)、[NOTICE](third_party_licenses/npm/echarts/NOTICE)、[D3 组件许可](third_party_licenses/npm/echarts/LICENSE-d3) |
| `echarts-for-react` | 3.0.6 | MIT | 直接依赖 | [LICENSE](third_party_licenses/npm/echarts-for-react/LICENSE) |
| `react` | 19.2.8 | MIT | 直接依赖 | [LICENSE](third_party_licenses/npm/react/LICENSE) |
| `react-dom` | 19.2.8 | MIT | 直接依赖 | [LICENSE](third_party_licenses/npm/react-dom/LICENSE) |
| `react-router-dom` | 7.18.2 | MIT | 直接依赖 | [LICENSE](third_party_licenses/npm/react-router-dom/LICENSE) |
| `tslib` | 2.3.0 | 0BSD | `echarts` 的传递依赖 | [LICENSE](third_party_licenses/npm/tslib/LICENSE) |
| `zrender` | 6.1.0 | BSD-3-Clause | `echarts` 的传递依赖 | [LICENSE](third_party_licenses/npm/zrender/LICENSE) |
| `fast-deep-equal` | 3.1.3 | MIT | `echarts-for-react` 的传递依赖 | [LICENSE](third_party_licenses/npm/fast-deep-equal/LICENSE) |
| `size-sensor` | 1.0.3 | ISC | `echarts-for-react` 的传递依赖 | [LICENSE](third_party_licenses/npm/size-sensor/LICENSE) |
| `scheduler` | 0.27.0 | MIT | `react-dom` 的传递依赖 | [LICENSE](third_party_licenses/npm/scheduler/LICENSE) |
| `react-router` | 7.18.2 | MIT | `react-router-dom` 的传递依赖 | [LICENSE](third_party_licenses/npm/react-router/LICENSE) |
| `cookie` | 1.1.1 | MIT | `react-router` 的传递依赖 | [LICENSE](third_party_licenses/npm/cookie/LICENSE) |
| `set-cookie-parser` | 2.7.2 | MIT | `react-router` 的传递依赖 | [LICENSE](third_party_licenses/npm/set-cookie-parser/LICENSE) |

除 `size-sensor` 外，上表许可文件均从当前 `node_modules` 中对应的锁定包逐包保留（换行符会归一化为 LF）。`size-sensor@1.0.3` 发行包没有独立 LICENSE 文件；其 `package.json` 与上游 README 均明示声明 `ISC`，仓库按 SPDX ISC 标准正文与上游权利人标识补齐了可再分发副本。

前端构建产物保留了组件写入的许可标记，其中包括 React 的 Meta Platforms, Inc. 版权声明、React Router 的 Remix Software Inc. 版权声明，以及 ECharts 运行时依赖中的 Microsoft Corporation 和 Baidu Inc. 版权声明。不得从再分发产物中删除这些声明。

## Dashboard 前端构建工具

下列直接开发依赖记录在同一份 lockfile 中，用于重建前端；它们不是叙典自身的运行时 API：

| 包 | 锁定版本 | 许可证 |
|----|----------|--------|
| `@types/react` | 19.2.18 | MIT |
| `@types/react-dom` | 19.2.4 | MIT |
| `@vitejs/plugin-react` | 4.7.0 | MIT |
| `vite` | 6.4.3 | MIT |

构建工具的完整传递依赖、解析版本和许可证字段以 `dashboard/frontend/package-lock.json` 为准。只有在安装或再分发这些包时，才应根据实际安装集合生成对应的完整第三方许可清单。

## Python 依赖声明

仓库当前声明但不随源码树内嵌的 Python 运行时依赖为：

- `scripts/requirements.txt`：`aiohttp>=3.8.0`、`filelock>=3.0.0`、`pydantic>=2.0.0`
- `dashboard/requirements.txt`：`fastapi>=0.115.0`、`httpx>=0.27.0`、`uvicorn[standard]>=0.32.0`、`watchdog>=5.0.0`

`scripts/requirements.txt` 还声明了 pytest 及其插件作为开发和测试依赖。由于这些范围没有锁定最终解析版本，本文件不代替实际 Python 环境的许可证报告。制作可分发环境时，应以该环境真实安装的发行包元数据重新生成依赖和许可证清单。

## 核验与再分发

本文件记录的是当前仓库状态，不保证覆盖用户自行安装的可选组件、外部模型服务或未来依赖。升级 `package-lock.json`、requirements 文件或前端构建产物时，应同步更新本清单，并保留第三方包自带的 LICENSE、NOTICE 和版权标记。

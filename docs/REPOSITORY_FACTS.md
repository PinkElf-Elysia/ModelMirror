# 模镜仓库事实基线

本文只记录可由当前仓库或可重复命令证明的事实。它不是产品需求文档，也不推断目标客户、用户故事或商业目标。

基线日期：2026-07-25
基线提交：`main@93e5cc38becc7fe4f89efa113310698e6eda1971`

## 1. 已证实事实

| 领域 | 当前事实 | 证据 |
| --- | --- | --- |
| 前端 | React 19、TypeScript、Vite、React Router、Tailwind、React Flow、Recharts | `client/package.json`、`client/src/App.tsx` |
| 后端 | FastAPI、Pydantic、httpx；主要应用装配仍集中在 `server/main.py` | `server/requirements.txt`、`server/main.py` |
| 模型网关 | 优先使用 `LLM_GATEWAY_URL` / `LLM_GATEWAY_KEY`，可回退 OpenRouter | `server/main.py`、`docker-compose.yml`、`.env.example` |
| 工作流 | `/workflow` 是 classic React Flow 主入口；`/workflow-native` 是实验入口 | `client/src/App.tsx`、`server/workflow_native/` |
| Xpert | 支持草稿、不可变发布版本、Chat、Goal、Handoff、App/API、文件与记忆 | `server/xperts/`、`server/xpert_runtime/`、`client/src/pages/Xpert*.tsx` |
| Toolset | 支持 MCP、OpenAPI/OData、内置 Provider、版本固定和工具语义 | `server/toolsets/`、`server/mcp/`、`client/src/pages/ToolsetsPage.tsx` |
| Knowledge | 本地 RAG、双索引、Processor、Canvas、视觉、Evaluation、审批写入 | `server/rag/`、`client/src/pages/RagPage.tsx`、`client/src/pages/Knowledge*.tsx` |
| Data X | 文件快照、DuckDB、语义模型、指标、受限查询与提案审批 | `server/datax/`、`client/src/pages/DataX*.tsx` |
| Prompt / Plugin | Prompt Profile 支持不可变版本和斜杠命令；Plugin 是声明式本地资源包，不加载动态后端代码 | `server/prompts/`、`server/plugins/`、`client/src/pages/PromptsPage.tsx`、`client/src/pages/PluginsPage.tsx` |
| 持久化 | 多数 Runtime/Xpert 元数据使用文件型 Store；RAG 使用 Chroma/FTS，Data X 使用 DuckDB | `server/xpert_runtime/`、`server/xperts/`、`server/rag/`、`server/datax/` |
| 隔离服务 | Compose 包含 Browser 和 Sandbox sidecar；另有 new-api、server、client，可选 office-host | `docker-compose.yml` |
| 前端验证 | 只有 `dev`、`build`、`preview` 脚本，没有独立 lint/test 脚本 | `client/package.json` |
| 后端验证 | pytest 测试位于 `server/tests/` | `server/tests/`、`server/requirements.txt` |
| CI | 仓库当前没有 `.github/workflows/` | 文件系统检查 |
| PR 规范 | 仓库提供 PR 与 bug 模板 | `.github/pull_request_template.md`、`.github/ISSUE_TEMPLATE/bug_report.md` |
| Xpert 路线 | 功能扩张已冻结，仅接受安全、致命缺陷、数据兼容和既有闭环回归 | `docs/XPERT_FREEZE.md`、`docs/XPERT_ALIGNMENT.md` |
| EvoAgentX 路线 | 官方 `v0.1.4@aad19b912f640161ea07e8904d9237cd34fde5f1` 已完成来源和许可证审计；尚未复制上游代码 | `docs/EVOAGENTX_ALIGNMENT.md`、`docs/EVOAGENTX_AUDIT_V014.md`、`server/meta_agent/NOTICE.md` |

## 2. 稳定入口事实

路由以 `client/src/App.tsx` 为准，当前包括：

- 资源与工作空间：`/models`、`/studio`、`/agents`、`/mcps`、`/toolsets`、`/skills`、`/runtime`。
- Xpert：`/agents/studio`、`/agents/xpert/:xpertId/chat`、`/agents/goals`、`/agents/automations`。
- 工作流：`/workflow`、`/workflow-native`。
- 知识：`/rag`、`/rag/:kbId/pipeline`、`/rag/:kbId/evaluation`、`/rag/:kbId/inbox`。
- 数据：`/datax`、`/datax/:projectId`、`/datax/:projectId/inbox`。
- 聊天与发布：`/chat/:modelId`、`/apps/:appSlug`。
- 设置：`/settings`。

路由清单是实现事实，不代表每个入口都有相同稳定性或公开承诺。

## 3. 当前验证基线

最近一次功能基线记录与本次纯文档审计的验证范围必须分开理解：

| 验证 | 状态 | 结果 |
| --- | --- | --- |
| 功能分支 `cd client && npm.cmd run build` | 历史通过 | 2026-07-23 Harness 记录：TypeScript 与 Vite 生产构建成功 |
| 功能分支 `python -m pytest server/tests/ -q` | 历史通过 | 2026-07-23 Harness 记录：453 passed |
| 本次 `python -m pytest server/tests/test_meta_agent.py -q` | 通过 | 4 passed；现有 FastAPI lifespan deprecation warnings 4 条 |
| 本次 `git diff --check`、文档链接与敏感扫描 | 通过 | 无格式错误、断链、真实密钥或个人绝对路径 |

历史结果不能证明当前分支。后续代码改动必须重新验证；本轮不修改生产代码，
因此只执行 MetaAgent 定向基线、Diff、链接和敏感信息检查，不重建 Docker。

## 4. 已发现的文档债务

以下是已证实差距，不在本文中擅自改写：

- `docs/ARCHITECTURE.md` 仍把 Dify iframe 描述为 Workflow/RAG 主路径，与当前 classic workflow 和本地 RAG 实现冲突。
- `docs/ONBOARDING.md` 仍包含 Dify 主路径、旧入口和旧语法检查命令。
- `README.md` 的能力摘要较新，但更新时间和部分入口清单需要周期性校准。
- API 事实主要存在于 FastAPI 路由和测试中，尚无独立、版本化的完整外部 API 契约。
- 当前没有仓库级自动 CI；测试证据依赖本地执行和 PR 记录。

这些债务应按独立文档校准任务处理，避免与功能 PR 混合。

## 5. 待确认产品信息

下列内容没有足够仓库证据，本文不作推断：

| 内容 | 状态 | 负责人 |
| --- | --- | --- |
| 目标客户与优先用户角色 | 待确认 | 产品负责人 |
| 用户故事与端到端业务流程 | 待确认 | 产品负责人 |
| 商业目标、成功指标与优先级 | 待确认 | 产品负责人 |
| 组织、租户与权限模型 | 待确认 | 产品/安全负责人 |
| SLA、备份、灾备和数据保留 | 待确认 | 运维/安全负责人 |
| 外网部署与合规边界 | 待确认 | 安全/法务负责人 |

人工补充这些信息后，应建立独立 PRD/用户故事文档，并让验收标准引用明确版本。

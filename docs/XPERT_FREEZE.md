# Xpert 对齐冻结基线

最后更新日期：2026-07-25

## 1. 冻结声明

Xpert 功能对齐在 ModelMirror `main@93e5cc38becc7fe4f89efa113310698e6eda1971`
正式冻结。该提交包含 `XPERT-PLUGIN-PROMPT-03`，是进入 EvoAgentX 主线前的
Xpert 能力基线。

冻结不是停止维护。此后 Xpert 相关变更只接受：

- 安全漏洞和敏感信息泄露修复。
- 致命缺陷、数据损坏、持久化恢复和版本兼容修复。
- 已实现闭环的行为回归、依赖升级和可访问性修复。
- EvoAgentX 候选规划、评估和进化所必需的兼容适配。

下列理由不能单独启动新的 Xpert 功能轮次：

- 上游截图、菜单、节点数量或像素细节不同。
- 尚未建设企业市场、组织治理或远程 Provider 目录。
- 上游存在 ModelMirror 已有等价闭环的另一套 Runtime 实现。

历史增量和参考证据仍保留在 `XPERT_ALIGNMENT.md` 与
`XPERT_UI_REFERENCE.md`，本文件是冻结后的唯一当前状态入口。

## 2. 状态定义

| 状态 | 含义 | 允许的后续动作 |
| --- | --- | --- |
| 稳定冻结 | 已形成可运行闭环，接口和安全边界进入兼容维护 | 缺陷、安全、兼容与回归修复 |
| 维护中 | 已可使用但仍有已知单机或实验边界 | 按真实故障和数据证据修复 |
| 明确延期 | 有价值但不属于当前主线 | 只有新的产品决策才能解冻 |
| 不采用 | 与现有架构重复或违反安全、许可证边界 | 不进入实现路线 |

## 3. 稳定冻结能力

| 能力 | 稳定入口与契约 | 冻结边界 | 最小回归 |
| --- | --- | --- | --- |
| Xpert Studio | `/agents/studio`、草稿 revision、不可变发布版本、固定版本运行 | 不迁移 Xpert 的 Angular/NestJS/TypeORM 架构 | Xpert create/save/publish/run |
| Xpert Chat | `/agents/xpert/:xpertId/chat`、SSE、会话、附件、记忆、Prompt Command | 不更改既有 SSE wire format | Xpert run、context、memory |
| Workflow | `/workflow` classic React Flow、Node Registry、validate、runner | `/workflow-native` 继续是实验入口 | workflow validate、agent runtime |
| 资源绑定 | `expert/knowledge/toolset/plugin/middleware` 特殊边 | 绑定边不进入控制流、变量传播和节点调度 | resource binding、publish preflight |
| 协作执行 | Goal、AgentTask、HandoffExecutor、暂停恢复、死信和人工接管 | 单进程文件型协调器，不宣称分布式队列 | Goal、Handoff、RunRegistry |
| Toolset | MCP、OpenAPI、OData、Builtin Provider、版本固定、工具语义 | 不继续扩张 Provider 数量或企业治理 | MCP/API/OData/provider/tool semantics |
| Knowledge | Processor、Canvas、视觉、双索引、Rerank、Evaluation、审批写入 | GraphRAG、图片向量和版面坐标延期 | RAG pipeline、query、evaluation |
| Middleware | Core、HITL、Sandbox、Browser、Client、Automation、Authoring、Office、Data X | 新中间件只允许由真实缺陷或新主线需求触发 | middleware registry、policy、App preflight |
| Plugin/Skill | 声明式 Plugin、Workspace Skill 草稿、Sandbox staging、版本固定 | 不加载动态后端模块 | plugin/skill validate、publish、sandbox |
| Prompt Command | `/prompts`、不可变 Profile、`/alias`、`//` 转义 | 模板继续限制为 `{{args}}` | prompt profile、slash command |
| Data X | 文件快照、DuckDB、语义模型、指标版本、受限查询、审批提案 | 不开放任意 SQL、写回或通用 Dashboard | Data X import/model/indicator/query |
| App/API | 固定 Xpert 版本、分享 token、API key、配额、OpenAI 兼容响应 | 管理面仍是可信本地边界 | App deploy/auth/JSON/SSE |
| Runtime 安全 | Tool Policy、Audit、checkpoint、HITL、作用域隔离 | RunRegistry 仍是内存态观测索引 | policy/audit/store recovery |

稳定冻结能力可以继续被 EvoAgentX Planner、Evaluator 和 Optimizer 调用，
但不得为适配上游而绕过现有 validate、发布预检、固定版本和人工审批。

## 4. 维护中能力与已接受技术债

| 领域 | 已接受边界 | 维护触发条件 |
| --- | --- | --- |
| 文件型 Store | Xpert、Goal、Handoff、审批和 Runtime 状态以单进程文件存储 | 原子写入、恢复、revision 或数据损坏问题 |
| 后台协调器 | lease 与幂等面向单后端进程 | 重复执行、丢任务、错误恢复或资源泄漏 |
| RunRegistry | 服务重启后运行观测索引丢失，持久业务状态由各 Store 恢复 | 父子链、checkpoint 或敏感摘要错误 |
| 可信管理面 | 没有用户、组织和细粒度权限体系 | 现有本地安全边界被绕过 |
| Legacy RAG | 旧知识库继续支持原 vector-only 路径 | 兼容查询、激活或回滚失败 |
| Chat Toolset | 原始 `/api/chat` 工具模式保持 Beta 和默认关闭 | 普通聊天回归或工具安全问题 |
| workflow-native | 只承担静态实验和设计验证 | 影响 classic `/workflow` 主路径时 |
| 文档体系 | 历史增量记录较长，部分早期架构文档仍有债务 | 文档与可重复仓库事实冲突 |
| CI | 当前没有仓库级自动 CI | 进入多人并行开发或发布频率提高 |

这些技术债不会因为冻结而被描述为“已完成企业级能力”。涉及数据库迁移、
多进程调度或权限体系的修复必须先形成独立架构决策。

## 5. 明确延期

- GraphRAG、实体关系抽取、社区摘要、图检索和图向量混合。
- 图片向量、多模态 embedding、PDF 版面坐标和通用 OCR 服务。
- 企业组织权限、多租户、RBAC、审计保留策略和合规控制。
- Redis、Celery、分布式 lease、横向扩容和数据库队列。
- 远程插件市场、计费、评分、自动更新和供应链治理。
- 动态 Python/Node 后端插件和服务进程内任意代码加载。
- 任意 Code Toolset、浏览器 OAuth 流程和企业 Provider 治理。
- PostgreSQL/MySQL Data X Connector、写回、任意 SQL 和通用 Dashboard。
- 公共 App 的 Sandbox、Browser、Client Tool、Office、自动化和写入能力。
- 剩余 Xpert UI 像素级复刻、菜单数量追平和无功能收益的目录页面。

延期项只有在产品负责人给出目标、成功标准和安全边界后才能重新排序。
本文件不推断目标客户、用户故事或商业优先级。

## 6. 不采用

- 将 Xpert AGPL 源码、框架或数据库模型复制进 ModelMirror。
- 用 Nx、Angular、NestJS 或 TypeORM 替换现有 React/FastAPI 主架构。
- 引入第二套 Workflow Runner、RAG Runtime、Tool Runtime 或审批状态机。
- 让 Planner、Optimizer、Plugin 或 Agent 静默修改线上不可变版本。
- 在服务进程执行 Plugin、Skill、Code Toolset 或任意初始化脚本。
- 为兼容上游而把密钥、Prompt、工具完整结果或物理路径写入公开 API、
  checkpoint 或 audit。

## 7. 冻结后的变更门禁

Xpert 冻结区域的 PR 必须说明：

1. 触发类别：安全、致命缺陷、兼容、数据恢复、回归或 EvoAgentX 必要适配。
2. 受影响的稳定入口、Store、版本快照和公开协议。
3. 回退方式和数据兼容策略。
4. 与改动对应的最小测试及跨入口回归。
5. 是否改变 App、Goal、Handoff、Tool Policy、HITL 或资源绑定安全边界。

新增功能若不满足这些条件，应进入独立产品决策，不应伪装成冻结维护。

## 8. 回归矩阵

| 变更区域 | 必须覆盖 |
| --- | --- |
| Workflow schema/validate | workflow validate、资源绑定、middleware binding、发布预检 |
| Xpert 草稿/版本 | create/save/publish、旧版本运行、App 固定版本 |
| Agent Runtime | workflow agent、Toolset、Policy、HITL、RunRegistry |
| Goal/Handoff | 状态机、lease、暂停恢复、固定目标版本 |
| Knowledge | pipeline、query、citation、evaluation、promotion |
| Toolset/Plugin/Prompt | Store revision、不可变版本、绑定、冲突与 App 门禁 |
| Runtime Store | 原子持久化、重启恢复、作用域隔离和敏感扫描 |
| 前端主路径 | 生产构建和受影响入口人工验收 |

纯文档冻结变更至少执行 `git diff --check`、相对链接检查和敏感信息扫描。

## 9. 主线切换

Xpert 冻结后的功能主线为：

1. `EVOAGENTX-ALIGNMENT-AUDIT-01`：已完成
2. `EVOAGENTX-META-PLANNER-01`
3. `EVOAGENTX-EVALUATOR-02`
4. `EVOAGENTX-EVOLUTION-03`

EvoAgentX 只提供经过来源、许可证、依赖和测试审计的 MIT 思路或文件。
ModelMirror 的 WorkflowDefinition、classic runner、XpertVersion、Store、
Toolset、Knowledge、HITL 和发布审批始终是执行事实源。

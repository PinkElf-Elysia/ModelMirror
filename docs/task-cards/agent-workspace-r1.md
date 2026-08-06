# 任务卡：Agent Workspace 第 1 轮

## 1. 单一目标

- 本次要完成：在独立实验路径中建立 General Agent 的 Agent State、16 个内置 Skill、默认 Skillset 与五页签配置工作台。
- 本次明确不做：模型运行循环、工具执行、MCP、外部 Skill、成本/Trace/评估/进化、Vault、定时任务、画布节点与容器重建。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| 当前工作树从最新 `origin/main` 创建 | 已证实事实 | `git status --short --branch`，基线 `b11472f` |
| 现有 Skill API 支持安装、列表、内容读取和卸载 | 已证实事实 | `server/skills/api.py`、`server/tests/test_skill_integration.py` |
| PyYAML 已固定为 6.0.2 | 已证实事实 | `server/requirements.txt` |
| PenguinHarness 本地快照包含 16 个 Skill | 已证实事实 | `packages/skills/skills/*/SKILL.md` |

## 3. 影响范围

- 允许修改路径：`server/agent_workspace/`、`server/skills/`、`server/tests/test_agent_workspace_*.py`、`client/src/pages/AgentWorkspace*`、前端路由/测试配置、Docker 持久化配置、相关文档与第三方声明。
- 禁止修改路径：`/api/chat` 实现、经典/原生工作流执行器、RAG、MCP、模型路由、Xpert 发布与现有 Skill 外部安装语义。
- 影响路由/API：新增 `/agents/workbench`、`/agents/workbench/agents/:agentId`、`/api/agent-workspace/*`、`/api/skills/library`、`/api/skills/skillsets`。
- 影响持久化数据：新增独立 Agent Workspace 数据根；初始化只增不覆盖。
- 新增或升级依赖：前端新增 Vitest、jsdom 与 Testing Library；后端无新增依赖。
- 风险：公开 API、YAML/文件持久化、前端配置写入；本轮不执行命令、不调用模型、不处理密钥。

## 4. 验收标准

- General Agent 首次读取时幂等创建，再次加载不覆盖用户配置。
- Agent ID、YAML Schema、提示词占位符和工具配置均经严格校验。
- Skill Library 返回且仅返回 16 个 Penguin 内置 Skill，并报告能力状态。
- `general-agent-default` Skillset 固定引用这 16 个 Skill；Agent State 保存内容快照。
- 五个页签支持加载、编辑、保存、失败提示和未保存提示。
- 功能开关关闭时 API fail-closed，前端入口不主动展示。
- 既有 Skill 安装测试、关键 Agent/Xpert 测试和前端构建通过。

## 5. 验证矩阵

| 检查 | 命令或步骤 | 状态 |
| --- | --- | --- |
| 后端定向测试 | `python -m pytest server/tests/test_agent_workspace_*.py server/tests/test_skill_integration.py -q` | 未运行 |
| 后端回归 | 计划指定的 Meta Agent / Xpert 测试与 `server/tests/ -q` | 未运行 |
| 前端测试 | `npm.cmd run test -- --run` | 未运行 |
| 前端构建 | `npm.cmd run build` | 未运行 |
| Docker/人工验收 | 等待共享栈确认后执行 | 未运行 |
| 敏感信息扫描 | `git diff`、`git diff --check` 与关键字扫描 | 未运行 |

## 6. 回退

- 关闭 `AGENT_WORKSPACE_ENABLED`，隐藏入口并令独立 API 返回 404。
- 回退本轮文件与路由注册，不删除 Agent Workspace 持久化目录。
- 既有 `/agents`、`/api/chat`、工作流和外部 Skill API 不需要数据恢复。

# 任务卡：Agent Workspace 第 2 轮

## 1. 单一目标

- 本次要完成：为 Round 1 的 Agent State 增加持久化 Session、原生 Tool Calling、九工具执行、审批/子 Agent、三栏执行工作区和一句话生成 Agent。
- 本次明确不做：MCP、Semantic Router、Vault、定时任务、成本/Trace/评估/进化、外部 Skill、画布 Agent 节点、上传与重启恢复硬化。

## 2. 证据

| 结论 | 等级 | 证据路径或命令 |
| --- | --- | --- |
| Round 1 已合并到主分支 | 已证实事实 | PR #95，`origin/main@c899ed7` |
| Agent State 已固定九工具与 16 个内置 Skill | 已证实事实 | `server/agent_workspace/defaults.py`、`server/tests/test_agent_workspace_state.py` |
| Round 1 清单摘要未覆盖适配后正文 | 已证实事实 | 全量 Agent Workspace 测试最初 4 项失败；已更新实际 SHA-256 并增加 LF 规则 |
| 现有 `/api/chat` 的 Toolset 路径使用 JSON 决策链 | 已证实事实 | `server/main.py::stream_chat_toolset_text` |
| 浏览器可用 SSE 事件 ID 断线续传 | 已证实事实 | FastAPI 文档与仓库 `coding_runtime` SSE 实现 |
| 首次实测生成退化为固定五段式通用提示词 | 已证实事实 | 生成 Session 仅使用弱模型且首次结构校验后直接提升；未发生质量复审 |
| PenguinHarness 对照使用 DeepSeek Builder、多轮思考和完整 `agent-creation` 方法 | 已证实事实 | 本地 upstream Skill、参考 Trace 与用户提供的 NPC Agent State 截图 |
| 合格中文候选被误判为缺少 workflow | 已证实事实 | Session `9cb8ed...` 的第二章为“工作流：合规审查流程”；旧别名缺少“工作流”，且初稿/二审共用修复预算 |

## 3. 影响范围

- 允许修改路径：`server/agent_workspace/`、对应测试、Agent Workspace 前端、`server/Dockerfile`、`docker-compose.yml`、`.env.example`、本模块文档。
- 禁止修改路径：`/api/chat` 行为、经典 `/workflow`、MCP、Xpert、用户持久化数据和原始脏工作区。
- 预计文件数：约 20 个，按可独立测试的 ≤5 文件小批次实施；完整闭环跨后端持久化、网关、工具和前端，无法安全压缩为单批文件。
- 影响路由/API：扩展独立 `/api/agent-workspace`，不改造 `/api/chat`。
- 影响持久化数据：在 `AGENT_WORKSPACE_ROOT` 增加 SQLite 与 `sessions/<id>/workspace`；只增表，不删除 Agent State。
- 新增或升级依赖：无；复用 `httpx`、FastAPI、Pydantic、Pillow 和标准库 SQLite/asyncio。
- 涉及密钥/网络/文件/子进程/公开访问：是；网关密钥只在服务端请求头使用，子进程使用脱敏环境并限制到 Session Workspace。

## 4. 验收标准

### 场景 1

- Given：启用 Agent Workspace，存在 General Agent。
- When：创建 Session 并发送消息。
- Then：模型通过原生 Tool Calling 流式响应，消息/任务/事件可刷新恢复，SSE 事件序号递增。

### 场景 2

- Given：Workspace 内存在文本、图片和可运行命令。
- When：分别执行九工具、四种审批模式和一层子 Agent。
- Then：仅 Workspace 内资源可访问；越界、拒绝、超时和深度/数量超限均 fail-closed。

### 场景 3

- Given：用户提交一句中文 Agent 需求。
- When：General Agent 在隔离 staging Workspace 生成候选描述。
- Then：默认使用 `deepseek/deepseek-v4-flash-0731` Builder，初稿必须经过第二次
  工具化领域复审，并通过语言、领域章节、可操作项、知识边界和高风险证据门禁后才
  原子创建完整 Agent State；中文“工作流”等常见标题不会被词法误拒；初稿与二审
  各自使用独立的有界修复预算；冲突或无效候选不覆盖现有 Agent。

### 失败场景

- Given：网关未配置或模型明确不支持 tools。
- When：任务启动。
- Then：任务与 SSE 返回明确可恢复错误，不模拟工具成功。

## 5. 实施顺序

1. 模型/契约：SQLite Session/Task/Message/Event/Approval 与 API Schema。
2. 校验/安全：Workspace 路径、原子写、审批策略、低权限进程环境。
3. 执行：OpenAI 兼容流式工具循环、九工具、子 Agent、候选提升。
4. 前端：Session/对话/Workspace 三栏、审批、模型和运行参数控制。
5. 文档：API、边界、Docker 护栏和回退。

## 6. 验证矩阵

| 检查 | 命令或步骤 | 预期 | 状态 |
| --- | --- | --- | --- |
| 语法/类型 | `python -m py_compile server/main.py` | 通过 | 通过 |
| 目标测试 | `python -m pytest server/tests/test_agent_workspace_*.py -q` | 通过 | 43 passed；其中 runtime 18 passed |
| 回归测试 | `python -m pytest server/tests/ -q` | 通过 | 1254 passed；旧 Node 20 镜像 1 项无法导入 `.ts`，Node 24 对应 Python/TypeScript 黄金顺序复核一致 |
| 构建 | `cd client; npm.cmd run test -- --run; npm.cmd run build` | 通过 | 24 tests passed；build passed |
| Docker/人工验收 | 共享栈确认后只重建 server/client | 通过 | 未运行 |
| 敏感信息扫描 | `git diff` 与 key/token 模式检查 | 无泄漏 | 通过 |

## 7. 风险与停止条件

- 主要风险：异步任务状态竞争、SSE 重放重复、工具输出过大。
- 兼容风险：不同 OpenAI 兼容网关对流式 `tool_calls` 字段支持不完全。
- 安全风险：路径逃逸、符号链接、子进程环境泄密和跨 Session 进程控制。
- 触发停止的条件：必须扩大 `/api/chat`、挂载 Docker Socket、读取真实密钥、破坏现有持久化 Schema 或关键回归失败。
- 需要用户确认的问题：整轮门禁通过后的共享栈状态与容器重建时机。

## 8. 回退

1. 回滚本轮独立 PR；关闭 `AGENT_WORKSPACE_ENABLED=0` 可立即隐藏入口并禁用 API。
2. 不需要恢复活动版本或既有指针。
3. 不删除 Agent Workspace 命名卷；SQLite 与 Workspace 文件保留供后续恢复。
4. 回退后验证 `/api/chat`、`/agents`、`/workflow` 和健康检查。

## 9. 完成定义

- [x] 实现只覆盖声明范围。
- [x] 正常与失败路径均有验证。
- [x] 公共接口和数据影响已说明。
- [x] Diff 已审查，无用户改动被覆盖。
- [x] 无密钥、运行存储或构建产物进入提交。
- [x] 文档与 Harness 已同步。
- [x] 未知产品信息仍明确标为待确认。

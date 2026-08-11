# Agent Workspace（Round 2）

## 目标与边界

Agent Workspace 是与现有智能体市场、元智能体、经典工作流和 `/api/chat`
并列的第二套原生 Agent 路线。Round 2 在 Agent State、16 个内置 Skill 和
配置工作台之上，增加持久化 Session、原生 Tool Calling、九工具执行、审批、
一层子 Agent、三栏执行工作区与受控的一句话生成 Agent。

稳定入口：

- `/agents/workbench`：Session、对话与 Workspace 三栏执行工作区。
- `/agents/workbench/agents/:agentId`：概览、Prompt、运行参数、工具、技能五页签。
- `/api/agent-workspace/status`：不受开关拦截，用于前端决定是否显示入口。
- `/api/agent-workspace/agents`：Agent State CRUD 与默认配置恢复。
- `/api/agent-workspace/sessions`：Session 列表、创建、详情、重命名和删除。
- `/api/agent-workspace/sessions/:id/tasks`：启动原生 Tool Calling 任务。
- `/api/agent-workspace/sessions/:id/events`：带递增事件 ID 的 SSE 历史补发与实时流。
- `/api/agent-workspace/approvals/:id`：精确批准或拒绝一个 Tool Call。
- `/api/agent-workspace/sessions/:id/workspace`：受控目录、文本预览与下载。
- `/api/agent-workspace/agents/generate`：创建由 General Agent 执行的候选生成任务。
- `/api/agent-workspace/tasks/:id/retry-generation`：从干净 staging 重试失败或停止的生成任务。
- `/api/skills/library`：16 项内置 Skill 清单、来源、摘要和能力状态。
- `/api/skills/skillsets`：内置与自定义 Skillset CRUD。

`AGENT_WORKSPACE_ENABLED=0` 时，状态接口仍返回 `enabled=false`，其余独立
Agent Workspace API 返回 404，`/agents` 不显示入口。现有路由不受影响。

## 持久化

Docker 默认设置：

```text
AGENT_WORKSPACE_ROOT=/data/agent-workspace
volume=agent_workspace_data
```

目录结构：

```text
/data/agent-workspace/
├── agents/<agent_id>/
│   ├── agent_state/
│   │   ├── system_config.yaml
│   │   ├── AGENTS.md
│   │   ├── skillset_snapshot.json
│   │   ├── skills/<skill_id>/
│   │   ├── memory/
│   │   └── tools/
│   └── scratchpad/
├── sessions/<workspace_id>/
│   └── workspace/                    # 会话与一层子 Agent 共享
└── agent_workspace.sqlite3           # Session、Task、Message、Event、Approval
```

`default_agent` 的显示名为 `General Agent`，不可删除。首次读取会幂等创建；
只要 `system_config.yaml` 存在，初始化就不会覆盖名称、提示词、AGENTS.md 或
Skill 快照。写入采用同目录临时文件加 `os.replace`，更新要求匹配 revision。

配置由严格 Pydantic Schema 校验，未知 YAML 字段、未知提示词占位符、重复或
乱序工具、非法 Agent ID 都会被拒绝。YAML 只使用 `safe_load` / `safe_dump`。

## General Agent 默认值

- `version=1`，`max_turns=100`。
- `model.max_tokens=32000`，`thinking_level=medium`，`timeoutMs=120000`。
- Compaction：`128000`、`-1`、`summarize`。
- 九个工具按固定顺序保存：`read_file`、`edit_file`、`write_file`、
  `exec_command`、`input_command`、`run_subagent`、`input_subagent`、
  `read_image`、`describe_image`。
- Round 2 按该固定配置注册工具执行器；模型工具 Schema 直接交给 OpenAI 兼容
  Chat Completions，不经过 `/api/chat` 的 JSON 工具决策链。

提示词保留角色、成功标准、约束、停止规则、工具使用、系统标记与环境注入
结构，身份替换为 ModelMirror General Agent。Vault、MCP、Penguin CLI、成本、
Trace、评估和定时任务等未实现声明已删除。

## 16 个内置 Skill

默认 `general-agent-default` Skillset 精确包含以下 16 项。Skillset 同时保存
Skill ID 与 SHA-256 内容摘要；安装到 Agent 后形成快照，内置库更新不会静默
修改已有 Agent。

| 状态 | Skill |
|---|---|
| 可运行 | `agent-creation`、`bento-slides`、`data-analysis`、`skill-porting`、`software-engineering`、`web-design` |
| 环境探测 | `llamafactory`、`ollama`、`vllm` |
| 依赖缺失 | `firecrawl`（无 Vault，不注入服务端密钥） |
| 仅供查看 | `agent-evaluation`、`agenthub-models`、`agent-optimization`、`benchmark-design`、`penguin-cli`、`penguin-sdk` |

运行时只把 `ready` Skill 和环境探测通过的 `conditional` Skill 快照物化到
Session Workspace 的 `.modelmirror/skills/`。依赖缺失和仅供查看的 Skill 不会
注入模型上下文。外部第 17 个 Skill 不在内置目录、manifest、默认 Skillset 或
General Agent 快照中。现有外部 Skill 安装 API 保持不变。

## Session 与运行循环

- Session、消息、任务、审批和事件写入独立 SQLite；事件使用自增序号，SSE
  支持 `after` 与 `Last-Event-ID`，断线重连只补发、不重复启动任务。
- 每个 Session 同时最多一个活动任务。任务状态为 `pending`、`running`、
  `waiting_approval`、`completed`、`failed` 或 `stopped`。
- 模型配置继续使用 `LLM_GATEWAY_URL` / `LLM_GATEWAY_KEY`，未配置时回退
  OpenRouter。网关明确不支持工具时任务失败并返回能力错误，不模拟成功。
- 前端可逐任务覆盖模型和思考等级。审批模式写回 Session，并同步更新当前活动
  Task；切换后会立即批准或拒绝正在等待的调用。`allow-all` 在界面中必须二次确认。
- 新建 Session 可选择 Skillset；其每个成员 ID 与摘要都必须存在于所选 Agent
  State 快照中。运行时只物化该 Skillset 的可运行成员，不兼容组合会 fail-closed。
- 一句话生成沿用 PenguinHarness `agent-creation` 的完整 Agent State 流程：先读取
  Skill、生成上下文与 General Agent 配置基线，再分别写入并回读
  `system_config.yaml`、`AGENTS.md` 和 `manifest.json`。单次写文件或普通聊天不能
  触发提升。
- 一句话创建的 Builder 默认模型独立固定为
  `deepseek/deepseek-v4-flash-0731`，不跟随普通会话的全局模型偏好；界面允许用户
  显式改选。服务端请求模型字段缺省时也采用同一默认值。
- 初稿通过结构校验后必须进入第二次工具化领域复审：重新读取生成上下文、AGENTS.md
  与 manifest，重写并回读 AGENTS.md，随后才允许提升。后端质量契约验证语言一致性、
  最小内容量、章节和可操作项数量、核心行为覆盖、至少两个领域专属章节，以及高风险
  任务的证据来源与时效边界；固定五段式模板无法通过。
- 后端同时验证唯一 Agent ID、可运行 Skill、Skill frontmatter 和工具调用顺序；空
  `skill_ids` 合法，`agent-creation` 仅在目标本身确需构建 Agent 时允许安装。名称与
  描述采用候选值，`version`、模型、系统提示词、工具、压缩和 Skillset 等继承字段在
  提升前确定性恢复。校验失败会把精确原因和 `draft` / `quality_review` 阶段反馈给
  同一任务；初稿与强制二审各自拥有最多两轮独立、有界的自动修复预算，避免初稿
  修复耗尽预算后让二审回归无法修复。只有 `agent_generated` 事件代表创建成功，
  模型在对话中的“审查通过”或“等待推送”不代表后端已经提升。
- 验证通过后才从 General Agent 的已安装快照复制所选 Skill，并以
  `system_config.yaml` 作为完成标记原子提升。已存在 ID、无效候选、拒绝审批或失败
  任务都不会覆盖 Agent State。失败生成只能通过专用接口从干净 staging 重试，不能
  退化为普通聊天后误报成功。

核心事件包括 `text_delta`、`thinking_delta`、`tool_call`、`approval_waiting`、
`approval_mode_changed`、`tool_output`、`subagent_status`、
`generation_config_normalized`、`generation_validation_failed`、
`generation_quality_review_started`、`agent_generated`、
`completed`、`failed` 和 `stopped`。这些事件只
用于恢复会话执行视图，不构成 Trace、成本或评估模块。

## 工具与审批护栏

- `read_file`、`edit_file`、`write_file`、`read_image` 和 `describe_image` 只接受
  Session Workspace 内相对路径；realpath、符号链接逃逸、大小和输出上限均在
  服务端校验，写文件采用同目录临时文件加原子替换。
- `exec_command` / `input_command` 只在 Linux 容器启用。子进程使用 Docker
  镜像内的低权限 `agenttool` 用户，环境只保留固定 PATH、Workspace HOME/TMP
  和 locale，不继承网关 Key 或服务端敏感变量。
- 命令进程按 Session 登记，可轮询、输入和终止；只能控制本 Session 启动的
  进程。输出和单次等待均有边界。容器不挂载 Docker Socket，因此命令不能控制
  ModelMirror 共享栈。
- 审批模式固定为 `always-ask`（默认）、`read-only`、`allow-all`、`deny-all`。
  审批记录持久化且只能决定一次；工作台将未决审批固定显示在消息区与输入区之间，
  无需滚动到对话底部。运行中切换策略会持久化到 Session 与活动 Task 并结算未决
  审批；停止任务会取消未决审批并终止所属进程。
- 子 Agent 最大深度为 1、每个父 Session 最多 8 个，共享 Workspace、模型和
  审批策略。Round 2 支持启动、等待、后台运行和完成后的追加任务；运行中插话
  延后到 Round 3。
- `read_image` 返回本地图片元数据和受输出上限约束的数据；`describe_image`
  复用现有视觉模型回退配置。

同容器低权限用户、审批和路径校验是 v1 护栏，并非面向恶意多租户的强沙箱。
更严格的进程/资源/网络隔离属于 Round 3 安全硬化范围。

## 运维与回退

- 关闭：设置 `AGENT_WORKSPACE_ENABLED=0` 并只重建 `server`、`client`。
- 代码回退不得删除 `agent_workspace_data`，以便恢复功能后继续读取用户 State。
- 回退 Round 2 代码时保留 SQLite 与 Session Workspace；重新启用后可继续读取。
- 关闭开关不会删除 Agent State、Session、审批记录或 Workspace 文件。
- 构建镜像时必须保留 `COPY agent_workspace ./agent_workspace` 与
  `COPY skills ./skills`，否则 Router 或内置 Skill manifest 不可用。

定向检查：

```powershell
python -m pytest server/tests/test_agent_workspace_*.py -q
cd client
npm.cmd run test -- --run
npm.cmd run build
```

从“Agent State 生成”继续扩展到应用开发、Goal、证据门禁、服务运行、浏览器自测、
RAG 配方与多 Agent 工作台的分轮计划见
[Agent Workspace 后续参考边界](./references/agent-workspace-future.md)。

R3R-1 的固定上游执行内核、Shadow 安全边界、供应链证据与回退说明见
[Upstream Agent Workbench Shadow Engine](./agent-upstream-shadow.md)。

# Agent Workspace（Round 1）

## 目标与边界

Agent Workspace 是与现有智能体市场、元智能体、经典工作流和 `/api/chat`
并列的第二套原生 Agent 路线。Round 1 只交付 Agent State、配置 API、16 个
内置 Skill、Skillset 与配置工作台，不执行模型或工具。

稳定入口：

- `/agents/workbench`：Agent 列表与执行面预告。
- `/agents/workbench/agents/:agentId`：概览、Prompt、运行参数、工具、技能五页签。
- `/api/agent-workspace/status`：不受开关拦截，用于前端决定是否显示入口。
- `/api/agent-workspace/agents`：Agent State CRUD 与默认配置恢复。
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
└── sessions/                         # Round 2 预留，Round 1 不创建
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
- Round 1 不注册工具执行器，不启动命令、子 Agent 或图片模型。

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

运行时只能读取 `inject_runtime=true` 的 Skill 正文；Round 1 尚无运行时，只在
API 与 UI 中把该决定展示出来。外部第 17 个 Skill 不在内置目录、manifest、
默认 Skillset 或 General Agent 快照中。现有外部 Skill 安装 API 保持不变。

## 运维与回退

- 关闭：设置 `AGENT_WORKSPACE_ENABLED=0` 并只重建 `server`、`client`。
- 代码回退不得删除 `agent_workspace_data`，以便恢复功能后继续读取用户 State。
- Round 1 不包含 SQLite、Session、模型调用、工具执行、审批或 Workspace 文件浏览。
- 构建镜像时必须保留 `COPY agent_workspace ./agent_workspace` 与
  `COPY skills ./skills`，否则 Router 或内置 Skill manifest 不可用。

定向检查：

```powershell
python -m pytest server/tests/test_agent_workspace_*.py -q
cd client
npm.cmd run test -- --run
npm.cmd run build
```

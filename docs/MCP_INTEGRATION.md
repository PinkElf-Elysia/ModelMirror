# MCP 原生集成说明

最后更新日期：2026-07-23
维护人：模镜团队

## 0. 产品入口与预算层级

- `/mcps` 明确提供进入 `/toolsets` 的入口：前者负责 MCP 项目发现与即时连接，后者负责 MCP/API/内置 Provider 的草稿、凭据、工具语义、测试和不可变发布。
- Tavily 与 Todos 作为稳定的内置默认 Toolset 存在。Todos 无需凭据即可直接绑定；Tavily 配置凭据时更新同一 Provider 实例，不重复生成不可发现的资源。
- `XpertAgentConfig.max_concurrency` 与 `recursion_limit` 是整个 Xpert 执行树的全局预算；Toolset 的并行安全、`maxToolConcurrency`、`maxToolCalls`、`maxToolDepth` 和 `maxIterations` 是局部工具调用护栏。局部配置不能突破全局预算。

## 1. 概述

MCP（Model Context Protocol）是一套让 AI 应用通过标准协议连接外部工具、资源和上下文的机制。模镜保留原 `/mcps` 即时连接入口，并新增 `/toolsets` 的版本化 MCP Runtime。后者支持 **Stdio、Streamable HTTP 与旧 SSE 兼容**：连接后发现工具 Schema，用户显式启停和配置工具，再发布不可变版本供 Workflow、Xpert、Goal 与 Handoff 绑定。

`/toolsets` 现也承载同一版本模型下的 API Toolset。OpenAPI 3.0/3.1 与 OData v4 文档被编译为受控工具 Schema，并通过独立安全 HTTP 执行器调用；这不是 MCP transport，也不会改变 `/mcps` 的连接与安装职责。

### 1.1 版本化 MCP Toolset

- 草稿包含连接类型、URL 或 argv、凭据引用、重连策略、超时、工具前缀和逐工具配置。
- Stdio 可以直接填写 argv，也可以选择 `/mcps` 已安装项目；发布时会把解析后的 argv 固定进版本快照。
- Streamable HTTP 是远程 MCP 的主路径；旧 SSE 仅用于兼容旧服务。
- Headers 和环境变量只引用 `CredentialStore` ID。创建或轮换时明文只返回一次，定义、版本与普通 API 均不保存或返回明文。
- 连接后新发现工具默认关闭。别名、描述覆盖、默认参数、顺序和启用状态都属于草稿。
- 发布至少需要连接成功并启用一个工具。新工具不会自动进入旧版本，远端发生不兼容 Schema 漂移时旧版本调用会 fail-closed。
- 管理侧测试调用也必须经过参数校验、Tool Policy 和 Audit。

Agent 画布使用 `toolset_resource -> workflow_agent` 的 `toolset` 绑定边。该边不属于控制流，Xpert 发布会把 Toolset 固定到具体版本。旧 `mcp_tool` 和全局 Tool Registry 继续兼容。

### 1.2 API Toolset

- OpenAPI 支持 JSON/YAML 文本、UTF-8 文件和受控 URL 导入；只解析本地 `$ref`，不远程抓取引用。
- OData 支持 v4 CSDL metadata，EntitySet 查询由字段枚举、过滤操作、排序、分页和键值 DSL 编译，不允许模型直接提交 `$filter` 或任意 URL。
- none、API Key、Bearer、Basic 和 OAuth2 client credentials 共用 `CredentialStore`；凭据明文不进入 Toolset 定义或版本。
- 默认网络策略只允许公网 HTTP/HTTPS，逐次 DNS 校验并阻断回环、私网、link-local、reserved、云元数据、URL credentials 和跨域重定向。
- 新导入操作默认关闭。草稿 refresh 只生成漂移报告，不改变旧版本；写操作默认 `requires_approval=true`。
- 管理测试的写操作需要显式确认，发布 Xpert 还必须绑定覆盖该工具的 HITL 中间件。

当前不支持远程 `$ref`、multipart、浏览器 OAuth flow、OData `$batch` 或任意 HTTP 脚本。

### 1.3 内置 Provider 与工具语义

- `/toolsets` 可创建 Tavily 和 Todo Provider 实例。Tavily 的 Key 仅保存在加密 CredentialStore；Todo 复用现有 RuntimeTodoStore。
- 已发布工具固定 `sensitive`、`terminal`、`memory_mode`、`parallel_safe` 和 `public_app_allowed`，草稿修改不影响已发布 Xpert。
- 敏感工具必须由目标 Agent 的 HITL 覆盖；终点工具成功后直接结束 Agent。conversation Tool Memory 仅用于私有 Xpert 会话。
- `workflow_agent` 可开启受限并行只读调用，并通过并发、总调用数、决策轮次和嵌套深度预算避免无界执行。
- 公共 App 只允许固定版本中显式标记为公共、只读、非敏感且不使用 conversation memory 的工具，并继续要求 `allow_tools` 与 Tool Policy。

架构图：

```text
┌─────────────────────────────┐
│ React /mcps                 │
│ - McpServerCard             │
│ - 动态 JSON Schema 参数表单 │
└──────────────┬──────────────┘
               │ HTTP REST
               ▼
┌─────────────────────────────┐
│ FastAPI /api/mcp/*          │
│ - 输入校验                  │
│ - 每 IP 连接限流            │
│ - session 生命周期管理      │
└──────────────┬──────────────┘
               │ 官方 mcp Python SDK
               ▼
┌─────────────────────────────┐
│ MCPClientManager            │
│ - stdio_client              │
│ - ClientSession             │
│ - list_tools / call_tool    │
│ - sandbox cwd               │
└──────────────┬──────────────┘
               │ stdio
               ▼
┌─────────────────────────────┐
│ MCP Server 子进程           │
│ npx / python / docker 等    │
└─────────────────────────────┘
```

安全默认值：

- 后端只接受 `list[str]` 形式的命令，不使用 shell。
- 拒绝 `;`、`&&`、`|`、重定向等 shell 特殊字符。
- 子进程工作目录固定为 `server/mcp/sandboxes/`。
- 每个 IP 每分钟最多建立 5 个 MCP 连接。
- session 存储在内存中，断开连接时清理 stdio 资源和子进程。
- 每个 session TTL 为 30 分钟，后台任务每 5 分钟清理一次；查询 sessions 或 registry 时也会触发一次轻量清理。
- 全局 ToolRegistry 会聚合所有活跃 session 的工具，重名工具按首次出现保留。

## 2. 如何添加新的 MCP Server

前端 MCP 项目数据位于：

```text
client/src/data/mcpProjects.ts
```

新增条目时保留现有字段，并在支持本地 stdio 启动时添加可选字段：

```ts
command: ["npx", "-y", "@example/mcp-server"]
```

示例：

```ts
{
  id: "filesystem-mcp",
  name: "Filesystem MCP",
  repoName: "modelcontextprotocol/servers",
  repoUrl: "https://github.com/modelcontextprotocol/servers",
  description: "把受控目录内的文件读写能力交给 AI。",
  readmeSummary: "官方 npm 包描述为 MCP server for filesystem access。",
  stars: 0,
  language: "TypeScript",
  updatedAt: "2026-06-16",
  installCommand: "npx -y @modelcontextprotocol/server-filesystem <allowed-directory>",
  installNote: "模镜后端会把工作目录限制在 server/mcp/sandboxes。",
  command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "."],
  tags: ["官方示例", "文件系统", "沙盒工具"],
}
```

接入检查清单：

1. 确认包名真实存在，例如：

```bash
npm view @example/mcp-server version
```

2. 确认 MCP Server 支持 stdio。
3. 确认是否需要 API Key。需要密钥的 Server 不应把密钥写入 `command`，应改由后端环境变量白名单注入。
4. 确认工具列表能正常返回：

```bash
python server/mcp/test_manager.py
```

如果 MCP Server 只能通过远程 HTTP/SSE 使用，可以继续展示安装信息，但不要添加 `command` 字段，避免前端显示“连接”能力。

## 3. 后端 API 文档

### POST `/api/mcp/connect`

启动一个 stdio MCP Server，创建 session。

请求：

```bash
curl -X POST http://localhost:8000/api/mcp/connect \
  -H "Content-Type: application/json" \
  -d '{"server_command":["npx","-y","@playwright/mcp@latest"]}'
```

响应：

```json
{
  "session_id": "8f3d8d6cc4af4f5c9a3e7b0d0f0fd9a0",
  "tools_count": 5
}
```

常见错误：

| 状态码 | 含义 |
| --- | --- |
| 400 | 命令非法或 MCP Server 启动失败 |
| 429 | 每 IP 每分钟连接数超过 5 次 |

### GET `/api/mcp/{session_id}/tools`

获取 session 暴露的工具列表。

```bash
curl http://localhost:8000/api/mcp/<session_id>/tools
```

响应：

```json
{
  "tools": [
    {
      "name": "fetch",
      "description": "Fetch a URL",
      "inputSchema": {
        "type": "object",
        "properties": {
          "url": { "type": "string" }
        },
        "required": ["url"]
      }
    }
  ]
}
```

### GET `/api/mcp/sessions`

获取当前活跃 MCP session。

```bash
curl http://localhost:8000/api/mcp/sessions
```

响应：

```json
{
  "sessions": [
    {
      "session_id": "8f3d8d6cc4af4f5c9a3e7b0d0f0fd9a0",
      "server_command": ["npx", "-y", "@playwright/mcp@latest"],
      "status": "connected",
      "created_at": 1792137600.0,
      "uptime_seconds": 42.1,
      "idle_seconds": 3.4,
      "tools_count": 5
    }
  ]
}
```

### GET `/api/registry/tools`

获取全局 MCP 工具注册表。返回值已按工具名去重。

```bash
curl http://localhost:8000/api/registry/tools
```

响应：

```json
{
  "tools": [
    {
      "name": "fetch",
      "description": "Fetch a URL",
      "input_schema": {
        "type": "object",
        "properties": {
          "url": { "type": "string" }
        }
      },
      "server_id": "@example/mcp-server",
      "session_id": "8f3d8d6cc4af4f5c9a3e7b0d0f0fd9a0",
      "registered_at": 1792137600.0
    }
  ]
}
```

### POST `/api/mcp/{session_id}/call`

调用工具。

```bash
curl -X POST http://localhost:8000/api/mcp/<session_id>/call \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"fetch","arguments":{"url":"https://example.com"}}'
```

响应：

```json
{
  "content": [
    {
      "type": "text",
      "text": "Example Domain"
    }
  ],
  "is_error": false,
  "raw": {}
}
```

### DELETE `/api/mcp/{session_id}`

断开 session 并清理子进程。

```bash
curl -X DELETE http://localhost:8000/api/mcp/<session_id>
```

响应：

```json
{ "ok": true }
```

## 4. 前端组件说明

核心组件：

```text
client/src/components/McpServerCard.tsx
```

页面入口：

```text
client/src/pages/McpBrowserPage.tsx
```

状态流：

1. 初始状态为 `idle`。
2. 点击“连接”后进入 `connecting`，调用 `POST /api/mcp/connect`。
3. 连接成功后进入 `connected`，读取工具列表。
4. 对每个工具，根据 `inputSchema.properties` 动态生成表单：
   - `string` → 文本输入框。
   - `number` / `integer` → 数字输入框。
   - `boolean` → true/false 下拉。
   - `enum` → 下拉选择。
   - `object` / `array` → JSON 文本框。
5. 点击“执行”后调用 `/api/mcp/{session_id}/call`，结果使用 Markdown 区域展示。
6. 点击“断开连接”后调用 DELETE 并清理本地状态。

UI 状态要求：

- 连接中按钮禁用。
- 无 `command` 的项目显示“展示项目”，不可连接。
- API 失败时在卡片内展示错误。
- 未知 JSON Schema 字段不应导致页面崩溃。

## 5. 测试指南

安装依赖：

```bash
python -m pip install -r server/requirements.txt
```

运行集成测试：

```bash
python -m pytest server/tests/test_mcp_integration.py -q
```

测试覆盖：

- 成功启动本地 mock MCP Server。
- 获取工具列表。
- 调用 `fetch` 工具并验证返回 `Example Domain`。
- 错误命令启动失败。
- shell 特殊字符拒绝。
- 每 IP 连接限流。

本地 smoke 测试：

```bash
python server/mcp/test_manager.py
```

注意：`test_manager.py` 默认使用公开 npm MCP Server，可能受 npm registry 或网络影响。CI 和常规回归应优先使用 `server/tests/mock_mcp_server.py`。

## 6. 相关文件

| 文件 | 说明 |
| --- | --- |
| `server/mcp/manager.py` | MCPClientManager，负责 Stdio、Streamable HTTP 与旧 SSE session 生命周期。 |
| `server/toolsets/` | Toolset/凭据 Store、版本发布、Schema 漂移与固定版本 Provider。 |
| `client/src/pages/ToolsetsPage.tsx` | MCP Toolset 创建、连接、工具配置、测试和发布管理页。 |
| `server/tests/test_toolset_*.py` | Toolset Store、API、连接、固定版本与安全回归。 |
| `server/mcp/test_manager.py` | 外部 fetch server smoke 脚本。 |
| `server/registry/tool_registry.py` | 内存级全局工具注册表。 |
| `server/tests/mock_mcp_server.py` | 本地 mock MCP Server。 |
| `server/tests/test_mcp_integration.py` | FastAPI MCP 端点集成测试。 |
| `server/tests/test_mcp_multisession.py` | 多 session、TTL 与 ToolRegistry 集成测试。 |
| `client/src/components/McpServerCard.tsx` | 前端连接、工具表单、执行结果组件。 |
| `client/src/data/mcpProjects.ts` | MCP 项目与可选 stdio 命令数据。 |

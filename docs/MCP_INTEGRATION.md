# MCP 原生集成说明

最后更新日期：2026-08-06
维护人：模镜团队

## 0. 产品入口与预算层级

- `/mcps` 负责冻结目录中的项目发现和预置适配器连接，不提供自定义命令、URL 或 MCP Builder。`/toolsets` 的通用草稿能力仍供其他产品路径使用，但不是目录条目的配置入口。
- Tavily 与 Todos 作为稳定的内置默认 Toolset 存在。Todos 无需凭据即可直接绑定；Tavily 配置凭据时更新同一 Provider 实例，不重复生成不可发现的资源。
- `XpertAgentConfig.max_concurrency` 与 `recursion_limit` 是整个 Xpert 执行树的全局预算；Toolset 的并行安全、`maxToolConcurrency`、`maxToolCalls`、`maxToolDepth` 和 `maxIterations` 是局部工具调用护栏。局部配置不能突破全局预算。

## 1. 概述

MCP（Model Context Protocol）是一套让 AI 应用通过标准协议连接外部工具、资源和上下文的机制。`/mcps` 使用服务端受控适配器：前端只提交项目 ID，固定命令、传输、配置字段和工具策略由 `server/mcp/catalog.py` 管理。通用 `/toolsets` Runtime 支持 **Stdio、Streamable HTTP 与旧 SSE 兼容**，但目录条目不会把任意连接能力暴露给用户。

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
- none、API Key、Bearer、Basic 和 OAuth2 client credentials 共用 `CredentialStore`；凭据明文不进入 Toolset 定义或版本。目录凭据额外绑定租户、所有者、项目和槽位，旧记录只迁移到显式 `local/local`，不会成为全局凭据。
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
│ - McpCredentialPanel        │
│ - 动态 JSON Schema 参数表单 │
└──────────────┬──────────────┘
               │ HTTP REST
               ▼
┌─────────────────────────────┐
│ FastAPI /api/mcp/catalog/*  │
│ - 按项目 ID 解析固定适配器  │
│ - 功能开关与生产状态门禁    │
│ - 配置字段与工具策略校验    │
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

目录适配器安全默认值：

- 前端不能提交 `server_command`、MCP URL、Header、环境变量名或工作目录。
- 当前目录状态为 **38 ready / 53 planned / 9 blocked**；planned 与 blocked 项没有可执行命令或端点，设置环境功能开关也不能绕过状态门槛。
- 新适配器若没有显式工具读写与审批策略，工具调用会 fail-closed。
- 日志只记录项目 ID、工具名、状态和耗时，不记录参数、返回正文或 Secret。

批次 1 的三个计算适配器通过固定 `sandbox_proxy.py` 接入隔离 sidecar：

- `calculator-mcp`、`time-mcp`、`vegalite-mcp` 只运行仓库内置 Python 兼容实现，不动态下载上游代码。
- sidecar 固定为 `network_mode: none`、只读容器、UID/GID 65532、丢弃全部 capabilities 且启用 `no-new-privileges`。
- 每个 MCP 子进程再使用 Landlock 只读规则与 RLIMIT CPU、内存、文件及句柄限制；sidecar 由 cgroup 限制为 128 PIDs 和最多 6 个并发会话，调用超时 10 秒，返回上限 128 KiB。
- Vega-Lite 数据只保存在当前进程内存，拒绝远程 URL，连接结束后随临时进程和空白工作区一起清理。

批次 2 的三个可用公网适配器通过固定 `public_proxy.py` 接入独立 `mcp-public` sidecar：

- `fetch-mcp`、`quickchart-mcp`、`geowire-mcp` 只运行仓库内置 Python 兼容实现，不动态下载或执行上游代码。
- sidecar 非 root、只读根文件系统、丢弃全部 capabilities、启用 `no-new-privileges`，并使用独立 bridge 网络和 Unix socket；不挂载宿主文件或 Docker socket。
- 公网 HTTPS 请求先解析 DNS，拒绝 IP 字面量和私网、回环、链路本地及保留地址，再把连接固定到已验证地址并保留原域名 TLS 校验；每次重定向重复全部校验。
- Docker Desktop/VPN 的 RFC 2544 Fake-IP 兼容默认关闭，仅由 Compose 为公网 sidecar 显式开启 `198.18.0.0/15`；其他非公网地址仍拒绝。
- 单跳请求超时 12 秒、最多重定向 3 次、原始响应最多 2 MiB、工具输出最多 128 KiB；Nominatim 固定为每秒最多 1 次。
- Fetch 只接受公网 HTTPS 工具参数并遵守 robots.txt；QuickChart 只生成受控 URL，不写文件；GeoWire 只开放无 Key 的 OSM/OSRM 子集。
- BibiGPT 因 OAuth / API Key 要求保持 `blocked`；Airbnb 因上游公开页面 schema 漂移保持 `blocked`，两者都没有命令、端点或登录入口。

批次 3 的四个可用本地文件适配器通过固定 `file_proxy.py` 接入独立 `mcp-files` sidecar：

- `modelmirror-mcp-files:wave3-v1` 使用完整依赖锁，运行时断网且不下载代码；容器为 UID/GID 65532、只读根文件系统、`network_mode: none`、移除 capabilities、`no-new-privileges`、1 GiB/1.5 CPU/128 PIDs，最多 4 个会话。
- 服务端创建绑定租户与项目的不透明工作区。客户端不提交宿主路径、工作目录、环境变量或 URI；上传可包含多文件、文件夹和安全 ZIP，封存后输入卷只读。选择文件夹时前端先按适配器扩展名白名单预检，不支持的路径会被列出；只有用户明确点击“跳过并上传”后才提交支持的文件，ZIP 内容仍由服务端完整校验。
- 普通输入工作区闲置 24 小时后清理，产物保留 7 天；Basic Memory 工作区持久保存，删除前需要独立强确认。单文件最多 64 MiB，工作区最多 5000 个文件/512 MiB，ZIP 压缩比最多 20:1。
- 工具参数中的 `x-modelmirror-input` 只允许 `workspace-file`、`workspace-directory`、`artifact-name`，前端渲染受控选择器，不提供原始路径输入。
- `basic-memory-mcp`、`excel-mcp-server`、`git-mcp`、`markitdown-mcp` 分别锁定兼容 0.22.1、1.0.4、0.6.2、0.1.7；Manim 因依赖第 8 批任意代码执行隔离而保持 `blocked`，没有连接按钮。
- 工具 effect 为 `read`、`artifact-create`、`state-write` 或 `terminal`。写状态工具先返回 409 `approval_required`，确认端点只执行服务端冻结的参数；5 分钟过期、重连、重配、参数变化、跨会话、输入版本或状态漂移都会使审批失效。

批次 4 的十五个可用 Token 适配器通过固定 `token_proxy.py` 接入独立 `mcp-token` sidecar：

- 前端根据服务端 `setting_fields` 和 `credential_fields` 在每张 MCP 卡片内渲染独立“加密凭据”区域。用户可在原地创建、选择和撤销凭据；不跳转 Toolset 页，也不提供 OAuth 或外站登录入口。明文 Token/API Key 只在创建时通过 HTTPS 提交给服务端加密保存，之后接口仅返回脱敏元数据。
- 目录凭据固定绑定 `project_id + slot`，不会出现在通用 `/api/runtime/credentials` 列表，也不能由其他 MCP 或 Toolset 选择。配置仍只存 `credential_id`；连接时才解密并通过私有 Unix socket 单次传递。撤销凭据会立即断开关联会话并清除该项目配置。
- 连接成功只表示 MCP 传输和工具发现可用。首次只读工具调用成功后状态才从 `unverified` 变为 `verified`；认证错误或调用异常标记为 `verification-failed`，避免把无效 Token 的 transport 初始化误报为凭据正常。
- 镜像 `modelmirror-mcp-token:wave4-v1` 使用精确 npm lockfile，运行时不下载包；非 root、只读根文件系统、无宿主目录和 Docker socket、移除 capabilities、`no-new-privileges`、768 MiB/1.5 CPU/128 PIDs，最多 6 个会话。
- JSON-RPC 网关在 `tools/list` 过滤未审核工具，并在 `tools/call` 再次拒绝清单外名称；URL/URI 参数先做公网 HTTPS 与 DNS 预检。固定上游 Node 进程加载 DNS 出口守卫，内置兼容实现使用固定地址的 `SafeHttpClient`。
- `smoke_token_adapters.py` 在断网、只读容器中初始化 15 项，并核对开放工具的规范化 inputSchema SHA-256；工具缺失或 schema 漂移直接失败。
- 只读工具通过项目级 `/api/mcp/catalog/{project_id}/tools/{tool_name}/call` 调用；目录会话使用通用直接调用接口会返回 403，避免跳过凭据状态和工具策略。
- Snyk 保持 `blocked`：其本地项目扫描可能执行 Gradle/Maven，必须等待第 8 批一次性代码执行与文件授权隔离。

批次 5 的六个可用数据库适配器通过固定 `database_proxy.py` 接入远程与本地两个隔离 sidecar：

- `dbhub`、`mongodb-mcp`、`clickhouse-mcp`、`redis-mcp` 与 `supabase-mcp` 使用远程数据库 sidecar；`duckdb-mcp` 使用 `network_mode: none` 的本地 sidecar 和封存 `.duckdb` 工作区。两个服务共享固定镜像 `modelmirror-mcp-database:wave5-v1`，但不共享网络能力或会话。
- 配置表单由 `database_policy`、`setting_fields` 和 `credential_fields` 驱动，只接受数据库类型、主机、端口、库名、用户名、严格 TLS 模式或 Supabase `project_ref`。DBHub 首批仅开放 PostgreSQL、MySQL 与 MariaDB；SQL Server 因无法可靠核验 direct grant/application role 的有效写权限而不进入本批。客户端不能提交 DSN、数据库 URI、SSH 隧道、任意驱动参数、命令、Header、环境变量或工作目录。
- 目录凭据固定绑定 `tenant_id + owner_id + project_id + slot`。跨作用域读取、解析、轮换或撤销统一返回不存在；生产可设置 `MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY=true` 强制使用外部主密钥，默认本地开发行为保持兼容。当前 API 服务实例使用部署时固定的 tenant/owner，尚未接入逐请求身份传播，因此本批只按单租户本地部署发布；多用户部署继续视为阻断。
- 连接成功前执行目标、TLS、认证、原生只读模式和查询限制预检；预检失败不创建目录会话。SQL 网关只接受单条读取语句并叠加数据库原生只读会话，非 SQL 协议则使用固定命令/阶段白名单，不能依赖工具名宣称只读。
- 默认最多返回 200 行、硬上限 1000 行，数据库 statement timeout 为 15 秒，目录调用超时 20 秒，MCP 结果最多 256 KiB。MongoDB 写入/代码阶段、Redis raw command/Lua/管理命令、DuckDB 外部访问与扩展加载均在 sidecar 再次拒绝。
- Supabase 使用 Personal Access Token 和固定项目 `project_ref` 的本地 stdio 路径，并固定调用官方 Management API 的 `/database/query/read-only` 端点；不使用 OAuth，也没有外站登录按钮，迁移、函数、分支和项目管理继续关闭。
- `postgres-mcp` 与 `sqlite-mcp` 因归档或弃用的上游实现保持 `blocked`。Cognee、Graphiti、Hindsight 转入第 5B 状态化记忆计划；其持久卷、模型与费用、数据保留及写入审批尚未完成，因此没有连接入口。
- Wave 5 首轮只开放读取、结构浏览和受限查询，不开放写入审批。数据库写审批不能复用依赖文件 manifest 的批次 3 审批；后续必须绑定租户、数据库目标、会话、配置/凭据版本、冻结参数和数据库状态。

兼容层仍保留以下默认值：

- 后端只接受 `list[str]` 形式的命令，不使用 shell。
- 拒绝 `;`、`&&`、`|`、重定向等 shell 特殊字符。
- 子进程工作目录固定为 `server/mcp/sandboxes/`。
- 每个 IP 每分钟最多建立 5 个 MCP 连接。
- session 存储在内存中，断开连接时清理 stdio 资源和子进程。
- 每个 session TTL 为 30 分钟，后台任务每 5 分钟清理一次；查询 sessions 或 registry 时也会触发一次轻量清理。
- 全局 ToolRegistry 会聚合所有活跃 session 的工具，重名工具按首次出现保留。

## 2. 如何适配冻结目录中的 MCP Server

适配期间目录冻结为 100 项，不新增条目。中文展示数据与后端执行清单分别位于：

```text
client/src/data/mcpProjects.ts
client/src/data/mcpAdaptationPlan.ts
server/mcp/catalog.py
```

接入检查清单：

1. 在前后端批次映射中确认项目 ID 唯一且批次一致。
2. 核验上游版本、包名或远程端点，并在后端固定版本；不要把执行配置写进前端数据。
3. 为适配器声明连接形态、允许的非敏感配置字段、凭据槽、网络/文件权限和独立功能开关。
4. 发现全部工具并逐个标记只读、敏感、终止性和审批要求；未知工具默认不可调用。
5. 通过初始化、代表性调用、超时、重连、清理、安全和回退 Smoke 后，才能把状态从 `adapting` 改为 `ready`。
6. 共享集成栈重建前必须由维护者确认时间窗口和基线；若其他工作树仍在开发，先使用独立预览项目验收。人工验收通过后再次核对最新 `origin/main`、工作树状态、冲突和变更范围，再提交并创建 PR。

核验 npm 包示例：

```bash
npm view @example/mcp-server version
```

远程、凭据、OAuth、代码沙箱和桌面桥接的退出门槛见 [MCP_CATALOG_ROADMAP.md](./MCP_CATALOG_ROADMAP.md)。

## 3. 后端 API 文档

### 目录专用 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/mcp/catalog/adapters` | 返回 100 项安全状态，不返回命令、端点或 Secret |
| GET/POST | `/api/mcp/catalog/{project_id}/credentials` | 列出当前卡片的脱敏凭据，或创建绑定当前项目与固定槽位的加密凭据 |
| DELETE | `/api/mcp/catalog/{project_id}/credentials/{credential_id}` | 撤销当前卡片凭据，并立即断开关联会话、清除失效配置 |
| POST | `/api/mcp/catalog/{project_id}/prepare` | 使用后端固定安装配置准备已验收适配器 |
| PUT | `/api/mcp/catalog/{project_id}/configuration` | 只接受清单允许的设置、`credential_id` 绑定和适用条目的 `workspace_id`；数据库不接受 DSN/URI |
| POST | `/api/mcp/catalog/{project_id}/connect` | 按项目 ID 建立受控会话；数据库项目须先通过目标、TLS、认证与只读预检 |
| DELETE | `/api/mcp/catalog/{project_id}/session` | 断开该目录项目的会话 |
| POST | `/api/mcp/catalog/{project_id}/tools/{tool_name}/call` | 经项目工具策略调用已连接工具 |
| GET/POST | `/api/mcp/catalog/{project_id}/workspaces` | 列出或创建当前项目的受控工作区 |
| POST | `/api/mcp/catalog/{project_id}/workspaces/{workspace_id}/files` | 上传多文件、目录相对路径或安全 ZIP |
| POST | `/api/mcp/catalog/{project_id}/workspaces/{workspace_id}/seal` | 封存输入并生成不可变 manifest 摘要 |
| GET/DELETE | `/api/mcp/catalog/{project_id}/workspaces/{workspace_id}` | 查看容量、文件和产物，或按保留策略清理工作区 |
| GET | `/api/mcp/catalog/{project_id}/workspaces/{workspace_id}/artifacts/{artifact_id}/download` | 通过不透明产物 ID 下载当前项目产物 |
| POST | `/api/mcp/catalog/{project_id}/approvals/{approval_id}/confirm` | 一次性确认并执行服务端冻结的写入调用 |
| DELETE | `/api/mcp/catalog/{project_id}/approvals/{approval_id}` | 取消尚未执行的一次性审批 |

`planned` 项目的准备、连接和调用返回 `409`。配置包含命令、URL、Header、环境变量或工作目录时返回 `400`。

以下 `/api/mcp/*` session API 为现有调用方保留兼容；目录前端不再向它们提交命令或直接调用工具。

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

1. 页面先读取 `/api/mcp/catalog/adapters`；服务端未返回 `executable=true` 时连接按钮禁用。
2. 文件适配器先创建工作区，上传文件/文件夹或 ZIP；文件夹中若含不支持类型，页面先展示路径摘要并要求用户确认跳过，未确认时不发出上传请求。检查容量与到期时间后封存，才可绑定 `workspace_id`；Manim 始终只显示中文阻断说明。
3. 点击“安装”调用 `POST /api/mcp/catalog/{project_id}/prepare`，浏览器不提交安装命令。
4. 点击“连接”后进入 `connecting`，调用 `POST /api/mcp/catalog/{project_id}/connect`；未绑定已封存工作区时服务端拒绝连接。
5. 连接成功后进入 `connected`，读取工具列表。
6. 对每个工具，根据 `inputSchema.properties` 动态生成表单：
   - `string` → 文本输入框。
   - `number` / `integer` → 数字输入框。
   - `boolean` → true/false 下拉。
   - `enum` → 下拉选择。
   - `object` / `array` → JSON 文本框。
   - `x-modelmirror-input` → 工作区文件/目录选择器或受限产物名，不显示原始路径框。
7. 点击“执行”后调用目录项目的策略化工具端点；若返回 `approval_required`，中文确认对话框展示影响摘要，确认后执行被冻结参数，结果与产物下载入口在卡片内展示。
8. 点击“断开连接”后按项目 ID 调用 DELETE 并清理本地状态；工作区按自身保留策略独立存在或清理。

UI 状态要求：

- 连接中按钮禁用。
- `planned`、`adapting`、`blocked` 或 `executable=false` 的项目不可连接。
- 每张卡片显示固定批次、连接方式、风险、限制和生产验收门槛。
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
python -m pytest server/tests/test_mcp_catalog.py server/tests/test_mcp_compute_adapters.py server/tests/test_mcp_file_workspaces.py server/tests/test_mcp_multisession.py -q
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
| `server/mcp/catalog.py` | 冻结目录、固定适配器、功能开关、配置门禁和目录 API。 |
| `server/mcp/sandbox_proxy.py` | 把固定项目 ID 的 stdio 流代理到断网 sandbox sidecar。 |
| `server/mcp/public_proxy.py` | 把批次 2 固定项目 ID 的 stdio 流代理到公网策略 sidecar。 |
| `server/mcp/file_proxy.py` | 把批次 3 固定项目 ID 和服务端工作区 ID 代理到断网文件 sidecar。 |
| `server/mcp/token_proxy.py` | 移除短期凭据环境并把批次 4 固定项目 ID 与配置单次传给私有 sidecar。 |
| `server/mcp/database_proxy.py` | 移除短期数据库配置环境，按固定项目路由到批次 5 的远程或断网本地 Unix socket。 |
| `server/mcp/workspace.py` | 工作区、上传/ZIP 校验、封存 manifest、产物与保留期管理。 |
| `server/sandbox_sidecar/compute_mcp.py` | 批次 1 的三个内置 Python MCP 工具契约。 |
| `server/sandbox_sidecar/public_mcp.py` | 批次 2 的内置公网 MCP 兼容契约。 |
| `server/sandbox_sidecar/safe_http.py` | 公网 HTTPS、DNS 固定、SSRF、重定向与响应上限策略。 |
| `server/sandbox_sidecar/public_server.py` | 公网 MCP 子进程的非 root、只读 Unix-socket sidecar。 |
| `server/sandbox_sidecar/file_mcp.py` | Basic Memory、Excel、Git 与 MarkItDown 的固定本地兼容契约。 |
| `server/sandbox_sidecar/file_server.py` | 批次 3 断网 Unix-socket sidecar、四会话上限与进程清理。 |
| `server/sandbox_sidecar/Dockerfile.files` | `modelmirror-mcp-files:wave3-v1` 独立锁定镜像。 |
| `server/sandbox_sidecar/token_contracts.py` | 批次 4 私有命令、凭据注入、设置、出口域和只读工具契约。 |
| `server/sandbox_sidecar/token_server.py` | 批次 4 Unix-socket 生命周期、工具过滤、URL 预检和进程清理网关。 |
| `server/sandbox_sidecar/token_builtin.py` | Axiom、Grafana Cloud、Kagi 与 Pinecone Assistant 最小只读兼容契约。 |
| `server/sandbox_sidecar/Dockerfile.token` | `modelmirror-mcp-token:wave4-v1` 精确 npm lockfile 镜像。 |
| `server/sandbox_sidecar/database_contracts.py` | 批次 5 的结构化目标、凭据槽、工具白名单、协议只读和预检契约。 |
| `server/sandbox_sidecar/database_server.py` | 批次 5 远程/本地数据库会话、限制、清理与 fail-closed 网关。 |
| `server/sandbox_sidecar/Dockerfile.database` | `modelmirror-mcp-database:wave5-v1` 固定依赖镜像。 |
| `server/toolsets/` | Toolset/凭据 Store、版本发布、Schema 漂移与固定版本 Provider。 |
| `client/src/pages/ToolsetsPage.tsx` | MCP Toolset 创建、连接、工具配置、测试和发布管理页。 |
| `server/tests/test_toolset_*.py` | Toolset Store、API、连接、固定版本与安全回归。 |
| `server/mcp/test_manager.py` | 外部 fetch server smoke 脚本。 |
| `server/registry/tool_registry.py` | 内存级全局工具注册表。 |
| `server/tests/mock_mcp_server.py` | 本地 mock MCP Server。 |
| `server/tests/test_mcp_integration.py` | FastAPI MCP 端点集成测试。 |
| `server/tests/test_mcp_multisession.py` | 多 session、TTL 与 ToolRegistry 集成测试。 |
| `server/tests/test_mcp_catalog.py` | 100 项契约、前后端 ID、服务端配置来源与 fail-closed 测试。 |
| `server/tests/test_mcp_compute_adapters.py` | 批次 1 工具契约、输入上限、URL 拒绝与沙箱配置测试。 |
| `server/tests/test_mcp_public_adapters.py` | 批次 2 SSRF、DNS、robots、响应上限、工具契约与容器隔离测试。 |
| `server/tests/test_mcp_file_workspaces.py` | 批次 3 路径/ZIP、租户隔离、产物越权和一次性审批安全测试。 |
| `server/tests/test_mcp_token_sidecar.py` | 批次 4 清单一致性、配置契约、Secret 传递、工具过滤和 URL 预检测试。 |
| `server/tests/test_mcp_database_sidecar.py` | 批次 5 配置、租户凭据、TLS/只读预检、协议旁路、行数与超时测试。 |
| `server/mcp/smoke_file_adapters.py` | 四个文件适配器初始化、发现、代表调用、重连、源文件不变和清理 smoke。 |
| `client/src/components/McpServerCard.tsx` | 前端连接、工具表单、执行结果组件。 |
| `client/src/components/McpWorkspacePanel.tsx` | 中文工作区上传、封存、绑定、容量/到期、产物下载和清理面板。 |
| `client/src/components/McpCredentialPanel.tsx` | 中文类型化设置和加密凭据引用选择面板。 |
| `client/src/data/mcpProjects.ts` | MCP 中文展示资料；不能作为执行配置来源。 |
| `client/src/data/mcpAdaptationPlan.ts` | 前端批次、状态、连接形态和风险展示。 |

## 7. 中文目录与后续适配

`/mcps` 同时展示已通过生产验收的 Server 和按批次排队的生态项目。待适配条目只能展示中文用途、接入条件、批次和安全门槛；只有后端返回 `ready + executable` 才能连接。

来源同步、安全运行时及后续 OAuth / Secret 代理计划见 [MCP_CATALOG_ROADMAP.md](./MCP_CATALOG_ROADMAP.md)。用户自定义连接和 MCP Builder 仍为未排期远期能力，不进入当前批次。

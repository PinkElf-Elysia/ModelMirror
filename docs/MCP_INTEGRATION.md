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
- 当前目录状态为 **50 ready / 65 planned / 85 blocked**。第二阶段新增 100 项由批次 12 纳入目录，批次 13 完成初次判定，批次 14—15 将其中 5 项提升为 ready，当前为 5 ready / 51 planned / 44 blocked；批次 10 的 14 项继续 planned，批次 11 的 13 项全部 blocked。planned 与 blocked 项没有可执行命令或端点，设置环境功能开关也不能绕过状态门槛。
- 批次 13 的官方 Brave Search MCP Server v2.1.0 只开放 `brave_web_search` 与 `brave_local_search`。批次 14 新增官方 Kagi v1.0.2 的 `kagi_search_fetch`、`kagi_extract`，以及 arxiv-mcp-server v0.6.2 的 `search_papers`、`get_abstract` 原生只读兼容契约。批次 15 新增 Search1API v0.5.3 的 `search`、`news`、`trending`，以及 Live Tennis v1.4.0 的 8 项 FREE 层比分/赛程/目录工具；全部固定出口、工具 Schema、输出上限和服务端加密凭据槽，不运行可扩张能力的上游进程。
- 第一阶段的完整状态表、已交付边界与阶段二准入条件见 [MCP 适配第一阶段收口](./MCP_ADAPTER_PHASE_ONE_CLOSEOUT.md)。
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

批次 6 的四个可用有状态 SaaS 适配器通过固定 `saas_proxy.py` 接入独立 `mcp-saas` sidecar：

- `airtable-mcp`、`asana-mcp`、`gitlab-mcp` 与 `notion-mcp-server` 只运行仓库内置固定 REST 契约，不动态下载或执行上游 MCP 代码。GitLab 首批仅允许 `gitlab.com`；自建域名、任意 URL、Header、环境变量与 OAuth 回调全部关闭。
- 每张卡片在本地创建和选择加密 PAT / Integration Token，并保存固定 Base、Workspace/Project、数字 GitLab Project ID 或 Notion Data Source 作用域。连接前执行账号身份与目标资源的代表性只读预检；传输、凭据、账号和目标预检状态分别展示。
- 真实 SaaS 执行要求对应的 `MCP_CATALOG_ENABLE_<PROJECT_ID>=true` 与 `MCP_CATALOG_STATEFUL_SAAS_SINGLE_USER_ACK=true` 同时满足；Compose 中五个门禁均默认关闭。当前目录 API 仍使用部署时固定 `tenant/owner`，尚无逐请求认证主体，因此多用户共享部署不得开启。
- 只读工具遇到上游 `429` 或临时 `5xx` 时最多有界重试两次，并遵守封顶后的 `Retry-After`。写工具不自动重试：明确的限流或提供商拒绝会作为终止性失败返回，只有 `-32008`、发送后超时或连接中断等歧义结果才标记 `unknown_outcome` 并要求先到服务商核对。
- `create`、`update`、`comment` 工具均先返回 409 `approval_required`。审批不是文件工作区审批：它绑定租户、所有者、项目、会话、工具/Schema、冻结参数、配置与凭据版本、账号预检摘要、目标资源预览和服务端幂等键；连接时还会核对完整工具名与 `inputSchema` 摘要。5 分钟过期，重连、重配、轮换、状态漂移或重放都会失效。删除、归档、合并、仓库写入和批量终止性操作不暴露。
- 目录会话在管理器发布前即绑定目录所有者，通用会话列表、调用和断开接口不能发现或操作它。连接建立、调用、审批确认、解绑和凭据撤销共享生命周期门禁；连接中不能重配，解绑或撤销开始后不能排队新调用或确认旧审批。
- 卡片内“解绑账号”会先阻止新调用并等待活动读取结束，再断开会话、撤销待审批并清除配置、预检与本地执行账本；可选择撤销该卡片的本地加密凭据。它不等于在 Airtable、Asana、GitLab 或 Notion 后台撤销 Token，用户仍需在服务商后台完成上游撤销。
- `mcp-cn-commerce` 保持 `blocked`：八个平台、114 个工具、OAuth/商家授权、短期 Token 和订单/售后敏感数据尚未逐平台验收。`mem0-mcp` 保持 `blocked`：本地上游已归档，官方迁移到无固定版本的托管远程 MCP；OAuth、Schema 锁定和租户记忆作用域尚未完成。两项都不显示凭据、登录或连接入口。

批次 7 的两个可用浏览器适配器通过固定 `browser_proxy.py` 接入彼此隔离的 `mcp-browser` 执行 sidecar 与 `mcp-browser-egress` 出口 sidecar：

- `chrome-devtools-mcp` 锁定上游 1.6.0 与 Chrome for Testing 150.0.7871.24，`playwright-mcp` 锁定上游 0.0.79 与 Chromium 1237 / Chrome for Testing 152.0.7977.8。两个适配器使用各自固定的浏览器路径；sidecar 每次连接启动对应的真实上游 stdio MCP 与独立 Chromium/profile，先核对 `initialize`、完整工具名和规范化 `inputSchema` 摘要；外层网关只转发经过审核的导航、快照、元素交互与截图子集，运行时不下载包或浏览器。
- 浏览器会话匿名且临时：每项最多一个页面、50 次动作、总时长 15 分钟、闲置 5 分钟；首版目录实例只运行一个浏览器会话。断开、超时、上游 EOF 或状态不确定时都会终止浏览器进程并删除 profile、Cookie、缓存和站点存储，不保存登录态。
- 本批不采集账号凭据、不提供外站登录流程，也不继承或保存登录态。导航 URL 双层拒绝 Token、API Key、签名等敏感查询参数；URL 与动作后的页面检查还会拒绝常见登录、授权和回调路径，但 HTTPS 页面仍可能自行呈现无法由透明出口可靠识别的登录界面；用户不得在临时浏览器中输入账号、密码、OTP 或其他认证信息。
- `mcp-browser` 使用 `network_mode: none`、1 GiB 内存、1.5 CPU 与 256 PIDs；该 PID 配额仅适用于浏览器执行 sidecar，其他 MCP sidecar 不随之放宽。Chromium 只能访问同容器的 loopback HTTP/CONNECT 代理；代理携带不传给上游进程的一次性会话能力，通过私有 Unix socket 请求独立联网的 `mcp-browser-egress`。执行端和出口端都会校验目标。生产出口不信任宿主机或 Docker Desktop 的 DNS 答案，而是只向固定数值地址 `1.1.1.1` / `1.0.0.1` 建立 TLS 1.2+ 连接，以 `cloudflare-dns.com` 做 SNI/证书校验并通过 JSON DoH 查询完整 A/AAAA 集合；随后再次执行公网地址门禁并固定连接到已验证地址。显式 synthetic DNS 只供随机隔离 fixture smoke 使用，生产 Compose 固定关闭。两端均拒绝 userinfo、IP 字面量、单标签主机、非 80/443 端口及私网、回环、链路本地、保留地址和 metadata，TLS 仍使用原 hostname。首版每个会话只允许一个已审批 origin，跨域请求与重定向 fail-closed；出口最多 12 个并发隧道、累计 64 MiB，并限制闲置与绝对时长，因此依赖第三方 CDN 的页面可能显示不完整。
- 上游进程及 Chromium 后代继续受 Landlock 约束，只能读取锁定运行时并写当前 profile/截图暂存目录；`/run` 和兄弟路径不可见。为让 Chromium 自身的 user namespace sandbox 写入 `uid_map`、`gid_map` 与 `setgroups`，`/proc` 只额外开放 `WRITE_FILE`，不开放创建、删除、截断、目录写或 socket 权限；容器仍无 capabilities，敏感 procfs 路径继续由 Docker 掩蔽或只读挂载。
- 执行 sidecar 与出口 sidecar 使用由出口进程生成的一次性配对密钥，每个容器生命周期只接纳一次目录会话。两端在 Docker 重启策略生效的 10 秒窗口之后（实现使用 11 秒裕量）才发布控制密钥或 MCP socket；会话结束或任一异常都会让两个 PID1 退出并由 `unless-stopped` 成对重建，不会复用旧配对、旧页面状态、旧元素 ref 或逃逸后代。
- 单 origin 门禁覆盖锁定上游的正常 Chromium 流量与恶意网页。若锁定的上游或浏览器进程本身被完全攻陷，独立出口仍只连接经过校验的公网 IP，并继续执行端口、流量和时限门禁，但没有 TLS 终止能力，不能保证同一公网 IP 与证书下的其他虚拟主机绝对隔离；本批不把这一边界描述为浏览器 RCE 防护。
- 导航、点击和填写属于 `state-write + requires_approval`。一次性审批绑定租户、所有者、项目、目录会话、浏览器 generation、页面 revision/digest、当前 origin、工具/Schema、冻结参数和配置版本；确认前页面或会话漂移即失效。此类调用永不自动重试，发送后超时或连接中断标记为 `unknown_outcome` 并污染、关闭当前会话。若 sidecar 以 `-32011` 明确证明 DNS 或目标 URL 在调用前被策略拒绝，则旧审批进入 `rejected` 终态但临时会话保留，用户修正目标后必须发起全新的审批；跨域跳转等可能发生在调用后的策略错误仍按未知结果 fail-closed。
- 快照只产生网关签发的不透明元素 ref；点击与填写不接受 CSS、XPath 或任意文本定位器，并拒绝密码、OTP、支付和验证码字段。截图先写入上限 64 MiB 的临时共享区，后端以单一文件描述符校验 PNG、大小、链接数与摘要，再复制到仅服务端可见的可信产物目录并登记 24 小时索引；产物接口只返回不透明 ID，并允许用户下载或清理。异常临时项只按固定目录和命名规则清扫，畸形条目告警并 fail-closed，不做宽泛递归删除。网页上传、网页下载、剪贴板、Cookie/Storage 导入导出与持久化、任意脚本求值工具、CDP、扩展和本机文件全部关闭。网页自身在会话内产生的 Cookie、缓存和站点存储只存在于临时 profile，断开时一并删除。
- `puppeteer-mcp` 因官方仓库归档、危险启动参数/脚本执行面及未修复安全报告保持 `blocked`。`selenium-mcp` 因发行物许可证元数据冲突、未锁定的 root 浏览器镜像及任意参数/路径/脚本/Cookie 能力保持 `blocked`。两项都不显示安装或连接入口。

批次 8 的两个 Python 执行条目均保持 `blocked`，本批不新增代码执行 sidecar：

- `mcp-run-python` 固定核验 0.0.22。官方仓库已归档，维护方明确说明 Pyodide 代码能够执行任意 JavaScript、污染后续调用、访问运行时文件并耗尽宿主内存，因此不把该实现部署为不可信代码沙箱。
- `mcp-python-interpreter` 固定核验 PyPI 1.2.3。默认 `run_python_code` 是进程内 `inline` 执行并保留全局会话，同时公开 pip 安装、文件读写、环境选择和最长 300 秒子进程调用；发布 wheel 虽声明 MIT classifier，但携带的 `LICENSE` 文件为空。
- 两个后端 manifest 都没有镜像、命令、端点、工具策略或 enabled-by-default 路径，功能开关不能使其可执行。前端不提交代码、包名、Python 路径、工作目录、环境、文件或会话 ID，也不显示安装/连接按钮。Manim 与 Snyk 仍等待一个独立立项、可复现且维护中的代码执行边界，不能因完成目录裁决而自动解锁。

批次 9 仅把 Terraform 公共 Registry 适配为可执行项，并使用独立 `mcp-registry` sidecar：

- 固定兼容 HashiCorp Terraform MCP Server v1.2.0，只开放 Provider 最新版本/能力/文档和 Module 搜索/详情/最新版本六个只读工具；Schema 摘要为 `73a2b116bcaa257dbf158d1ab8a778d067dac2d969db7dff160372d1617e3445`。
- `mcp-registry` 不复用 `mcp-token` socket：服务端通过 `/run/modelmirror-registry-mcp/registry-mcp.sock` 发送固定项目 ID 与空 `{settings, credentials}` 握手。sidecar 只允许 `terraform-mcp`，而 Wave4 `mcp-token` 只允许原 15 项 Token 适配器。
- 运行时不接收 Token、账号或配置字段，不挂载宿主目录和 Docker socket；容器为 UID/GID 65532、只读根文件系统、`cap_drop: ALL`、`no-new-privileges`、512 MiB/1 CPU/64 PIDs，tmpfs 工作区不持久化。固定主机为 `registry.terraform.io`、禁止重定向、DNS 后固定连接地址并校验 TLS hostname；Docker Desktop 合成 DNS 仅作为固定主机的传输兼容，不形成任意 URL 输入。
- HCP Terraform、Terraform Enterprise、私有 Registry、plan、apply、destroy、run、workspace、本地状态/变量/配置和资源变更工具均不在清单；目录连接零重试且连接后立即擦除内部握手环境。
- Apify、Aiven、Bright Data、Browserbase、E2B、Stripe、Alpaca、AWS KB、ElevenLabs、MiniMax、S3 Tables、Kubernetes 与 Semgrep 均为 `blocked`，没有凭据、命令、端点或工具入口。

批次 11 的 13 个桌面/宿主条目全部保持 `blocked`，本批不新增桌面代理或运行时：

- 当前服务端没有通用桌面桥。可发布的本机连接必须先绑定可信用户会话、宿主实例与版本、应用/项目范围、工具 Schema、逐动作同意和撤销状态；不能把任意 localhost、LAN 主机、宿主路径或 Docker Socket 暴露为目录配置。
- 小红书与 OpenTabs 继承真实浏览器账号或登录态并包含发布/动态插件工具；Ableton、Blender、Binary Ninja、Ghidra 与 JetBrains 通过宿主插件和本地端口修改真实项目、场景、二进制或 IDE 状态。现有浏览器和 sidecar 沙箱都不能代替宿主侧授权。
- ChatCrystal 会导入本机编码对话，MCPVault 直接读写 Obsidian Vault，Zotero 同时读取全文并支持云端写入；当前没有受信任的本机目录 grant、内容脱敏和只读工具冻结。
- Docker MCP Gateway 是动态容器/Server/Secret/OAuth 控制面，模镜继续禁止 Docker Socket；Mobile MCP 和 XcodeBuildMCP 可安装应用、控制设备、构建/测试/调试并执行 UI 输入，没有测试专用设备或 macOS 主机证明时不能连接。
- 13 项均公开 `blocked + executable=false`，没有 `runtime_image`、`server_command`、`endpoint`、配置/凭据字段或工具策略。批次 10 仍暂缓到多租户主体边界完善后，不在本批分支中改变状态。

兼容层仍保留以下默认值：

- 后端只接受 `list[str]` 形式的命令，不使用 shell。
- 拒绝 `;`、`&&`、`|`、重定向等 shell 特殊字符。
- 子进程工作目录固定为 `server/mcp/sandboxes/`。
- 每个 IP 每分钟最多建立 5 个 MCP 连接。
- session 存储在内存中，断开连接时清理 stdio 资源和子进程。
- 每个 session TTL 为 30 分钟，后台任务每 5 分钟清理一次；查询 sessions 或 registry 时也会触发一次轻量清理。
- 全局 ToolRegistry 会聚合所有活跃 session 的工具，重名工具按首次出现保留。

## 2. 如何适配冻结目录中的 MCP Server

目录总数固定为 200 项；第二阶段新增 100 项已完成逐项目适配判定，后续状态变化仍必须单项通过安全门槛。中文展示数据与后端执行清单分别位于：

```text
client/src/data/mcpProjects.ts
client/src/data/mcpAdaptationPlan.ts
client/src/data/mcpCatalogExpansionV2.generated.ts
server/mcp/catalog.py
server/mcp/catalog_expansion_v2.py
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
| GET | `/api/mcp/catalog/adapters` | 返回 200 项安全状态，不返回命令、端点或 Secret |
| GET/POST | `/api/mcp/catalog/{project_id}/credentials` | 列出当前卡片的脱敏凭据，或创建绑定当前项目与固定槽位的加密凭据 |
| DELETE | `/api/mcp/catalog/{project_id}/credentials/{credential_id}` | 撤销当前卡片凭据，并立即断开关联会话、清除失效配置 |
| POST | `/api/mcp/catalog/{project_id}/prepare` | 使用后端固定安装配置准备已验收适配器 |
| PUT | `/api/mcp/catalog/{project_id}/configuration` | 只接受清单允许的设置、`credential_id` 绑定和适用条目的 `workspace_id`；数据库不接受 DSN/URI |
| POST | `/api/mcp/catalog/{project_id}/connect` | 按项目 ID 建立受控会话；数据库项目须先通过目标、TLS、认证与只读预检 |
| DELETE | `/api/mcp/catalog/{project_id}/session` | 断开该目录项目的会话 |
| POST | `/api/mcp/catalog/{project_id}/unbind` | 解绑当前 SaaS 账号：断开会话、作废审批并清除作用域；可选择撤销本地卡片凭据，但不声称撤销上游 Token |
| GET | `/api/mcp/catalog/{project_id}/tools` | 通过目录所有者隔离读取已审核工具；通用 session 工具接口不能读取目录会话 |
| POST | `/api/mcp/catalog/{project_id}/tools/{tool_name}/call` | 经项目工具策略调用已连接工具 |
| GET | `/api/mcp/catalog/{project_id}/browser-session` | 查看当前临时浏览器 generation、页面摘要、origin、动作额度、到期和污染状态 |
| GET | `/api/mcp/catalog/{project_id}/browser-artifacts` | 列出当前项目的不透明浏览器截图产物，不返回容器或宿主路径 |
| GET | `/api/mcp/catalog/{project_id}/browser-artifacts/{artifact_id}/download` | 下载绑定当前租户、所有者、项目与浏览器会话的截图产物 |
| DELETE | `/api/mcp/catalog/{project_id}/browser-artifacts/{artifact_id}` | 清理绑定当前租户、所有者、项目与浏览器会话的截图产物 |
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

批次 7 浏览器适配器还必须在实际 Compose 等价隔离边界中完成双 sidecar 验收。构建后先记录本地镜像 ID/摘要，再运行真实 UDS、浏览器与超时 smoke：

```bash
docker build -f server/sandbox_sidecar/Dockerfile.browser -t modelmirror-mcp-browser:wave7-v1 server/sandbox_sidecar
docker image inspect modelmirror-mcp-browser:wave7-v1 --format '{{json .RepoDigests}} {{.Id}}'
python server/sandbox_sidecar/smoke_browser_runtime.py --image modelmirror-mcp-browser:wave7-v1 --seccomp server/sandbox_sidecar/seccomp_profile.browser.json
```

成功结果必须包含 Chrome DevTools 与 Playwright 各两次导航、快照、元素交互和 PNG 登记，以及各一次真实 20 秒导航超时（`-32008 / unknown_outcome / retryable=false`）、browser/egress PID1 精确单次重启、运行目录/进程清理和最终 `cleanup=verified`。该 harness 只创建随机 `mm-wave7-runtime-smoke-*` 资源；任何失败或残留都返回非零，不能用容器健康检查代替。

批次 8 没有可执行运行时，因此验收不得以 mock 或自研 Python 执行器替代上游 smoke。聚焦测试必须断言两个项目始终为 `blocked`，公开 `executable=false`，且不存在 `runtime_image`、`server_command`、`endpoint` 和工具策略；同时保持 Manim 与 Snyk 的既有阻断。

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
| `server/mcp/saas_proxy.py` | 移除短期 SaaS 配置环境，把批次 6 固定项目与账号作用域单次传给私有 sidecar。 |
| `server/mcp/browser_proxy.py` | 把批次 7 固定项目和服务端浏览器策略单次传给私有 sidecar，不接受客户端命令、CDP 地址、启动参数或路径。 |
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
| `server/sandbox_sidecar/saas_contracts.py` | 批次 6 的固定服务、作用域、凭据槽、工具 Schema、读写与幂等契约。 |
| `server/sandbox_sidecar/saas_server.py` | 批次 6 的预检、限流、有界只读重试、写入未知结果与会话清理网关。 |
| `server/sandbox_sidecar/Dockerfile.saas` | `modelmirror-mcp-saas:wave6-v1` 独立固定契约镜像。 |
| `server/sandbox_sidecar/browser_contracts.py` | 批次 7 的上游版本、固定工具 Schema、会话额度、网络与浏览器能力契约。 |
| `server/sandbox_sidecar/browser_server.py` | 批次 7 的真实上游进程、HTTP/CONNECT 出口代理、临时 profile、状态与产物清理网关。 |
| `server/sandbox_sidecar/Dockerfile.browser` | `modelmirror-mcp-browser:wave7-v1` 锁定上游 MCP 与 Chromium 的独立镜像。 |
| `server/toolsets/` | Toolset/凭据 Store、版本发布、Schema 漂移与固定版本 Provider。 |
| `client/src/pages/ToolsetsPage.tsx` | MCP Toolset 创建、连接、工具配置、测试和发布管理页。 |
| `server/tests/test_toolset_*.py` | Toolset Store、API、连接、固定版本与安全回归。 |
| `server/mcp/test_manager.py` | 外部 fetch server smoke 脚本。 |
| `server/registry/tool_registry.py` | 内存级全局工具注册表。 |
| `server/tests/mock_mcp_server.py` | 本地 mock MCP Server。 |
| `server/tests/test_mcp_integration.py` | FastAPI MCP 端点集成测试。 |
| `server/tests/test_mcp_multisession.py` | 多 session、TTL 与 ToolRegistry 集成测试。 |
| `server/tests/test_mcp_catalog.py` | 200 项契约、前后端 ID、服务端配置来源与 fail-closed 测试。 |
| `server/tests/test_mcp_compute_adapters.py` | 批次 1 工具契约、输入上限、URL 拒绝与沙箱配置测试。 |
| `server/tests/test_mcp_public_adapters.py` | 批次 2 SSRF、DNS、robots、响应上限、工具契约与容器隔离测试。 |
| `server/tests/test_mcp_file_workspaces.py` | 批次 3 路径/ZIP、租户隔离、产物越权和一次性审批安全测试。 |
| `server/tests/test_mcp_token_sidecar.py` | 批次 4 清单一致性、配置契约、Secret 传递、工具过滤和 URL 预检测试。 |
| `server/tests/test_mcp_database_sidecar.py` | 批次 5 配置、租户凭据、TLS/只读预检、协议旁路、行数与超时测试。 |
| `server/tests/test_mcp_saas_sidecar.py` | 批次 6 固定 Host、Schema、预检、审批、限流、幂等、未知写入结果与解绑测试。 |
| `server/tests/test_mcp_browser_sidecar.py` | 批次 7 上游 Schema、DNS/SSRF、重定向、临时会话、ref 漂移、审批、未知结果和清理测试。 |
| `server/tests/test_mcp_browser_runtime_smoke.py` | 批次 7 双 sidecar 编排、资源登记/精确清理、重启、真实超时和固定安全探针的 harness 单测。 |
| `server/sandbox_sidecar/smoke_browser_runtime.py` | 在随机隔离 Docker 资源中执行两个浏览器适配器的真实交互、截图、超时、PID1 轮换与残留清理终端门禁。 |
| `server/mcp/smoke_file_adapters.py` | 四个文件适配器初始化、发现、代表调用、重连、源文件不变和清理 smoke。 |
| `client/src/components/McpServerCard.tsx` | 前端连接、工具表单、执行结果组件。 |
| `client/src/components/McpWorkspacePanel.tsx` | 中文工作区上传、封存、绑定、容量/到期、产物下载和清理面板。 |
| `client/src/components/McpCredentialPanel.tsx` | 中文类型化设置和加密凭据引用选择面板。 |
| `client/src/data/mcpProjects.ts` | MCP 中文展示资料；不能作为执行配置来源。 |
| `client/src/data/mcpAdaptationPlan.ts` | 前端批次、状态、连接形态和风险展示。 |

## 7. 中文目录与后续适配

`/mcps` 同时展示已通过生产验收的 Server 和按批次排队的生态项目。待适配条目只能展示中文用途、接入条件、批次和安全门槛；只有后端返回 `ready + executable` 才能连接。

来源同步、安全运行时及后续 OAuth / Secret 代理计划见 [MCP_CATALOG_ROADMAP.md](./MCP_CATALOG_ROADMAP.md)。用户自定义连接和 MCP Builder 仍为未排期远期能力，不进入当前批次。

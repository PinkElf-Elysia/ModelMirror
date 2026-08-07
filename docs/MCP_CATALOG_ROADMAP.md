# MCP 目录扩充与适配路线

最后更新日期：2026-08-06
维护人：模镜团队

## 1. 目标与当前范围

模镜的 MCP 页面同时承担两种职责：

1. 提供经过核验、能够在当前后端沙盒中启动的本地 stdio Server。
2. 建立中文 MCP 能力目录，让用户先了解工具用途、分类、依赖和未来接入状态。

当前目录从以下社区清单整理：

- [Awesome-MCP-ZH](https://github.com/yzfly/Awesome-MCP-ZH)
- [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)

截至 2026-08-06，目录仍冻结为 100 个 MCP 条目、18 个分类。批次 0—4 已验收的 32 项保持不变；批次 5 的 DBHub、MongoDB、ClickHouse、Redis、DuckDB 与 Supabase 已标记为 `ready`。PostgreSQL、SQLite、Cognee、Graphiti 与 Hindsight 新增为 `blocked`，连同 Airbnb、BibiGPT、Manim 与 Snyk 共 9 项阻断，其余 53 项保持 `planned`。当前状态精确为 **38 ready / 53 planned / 9 blocked**。

## 2. 当前边界与明确不实现

批次 1–5 只增加固定适配器：批次 1 使用完全断网计算 sidecar，批次 2 使用独立公网 sidecar，批次 3 使用受控上传工作区和完全断网的文件处理 sidecar，批次 4 使用固定 Token 槽和只读出口 sidecar，批次 5 使用结构化数据库配置及相互隔离的远程/本地数据库 sidecar。五批都不扩大账号授权、任意连接或任意执行能力：

- 不接受用户指定的 Python 包、解释器、命令、工作目录或其他额外运行时。
- 不实现 OAuth 回调、刷新或外站登录；批次 4—5 的卡片可把明文 Secret 单次提交到服务端加密库，但连接配置只接受不透明 `credential_id`，保存后不再返回或显示明文。
- 不打开外站认证登录页，也不提供认证按钮或深链。
- 不把 Streamable HTTP、SSE 或任意 MCP URL 暴露为目录配置；批次 2 仅由固定 stdio 适配器访问受控公共 HTTPS 服务。
- Fetch 的用户 URL 只是工具参数，只允许公网 HTTPS，并逐次执行 DNS、SSRF、重定向和响应大小校验；它不是自定义 MCP 连接入口。
- 批次 3 客户端只能提交不透明 `workspace_id`、文件 ID 和产物名，不能提交宿主路径、`cwd`、环境变量或 URI；输入文件封存后只读。
- 批次 5 客户端只能提交清单声明的数据库类型、主机名、端口、库名、用户名、严格 TLS 模式或 Supabase `project_ref`；DBHub 首批仅支持 PostgreSQL、MySQL 与 MariaDB，SQL Server 保持关闭；不接受 DSN、数据库 URI、SSH 隧道、Header、环境变量名或任意连接参数。
- 不接管 Blender、Zotero、Obsidian、JetBrains、Xcode、Ghidra 等桌面宿主。
- 不提供用户自定义 MCP 连接。
- 不提供 MCP Builder、可视化生成器或发布市场流程。

这些能力可以作为目录条目被发现，但不能出现可执行的“安装”或“连接”入口。

## 3. 目录数据契约

每个条目至少包含：

- 中文名称、用途说明和 README 摘要。
- 中文分类、标签、主要语言和来源清单。
- `local-stdio` 或 `planned` 适配状态。
- OAuth、Token、额外运行时、桌面宿主、远程传输、数据库凭证、账号绑定和系统权限等前置条件。
- 三步中文配置引导和两个典型使用示例。

数据层必须满足以下约束：

1. 前端条目不得包含可执行 `command`、MCP URL、Header 或环境变量配置。
2. `ready` 后端适配器必须有固定命令或端点、独立功能开关和显式工具策略；现有 7 项以兼容策略保持行为不变，批次 1—5 的 31 项使用完整的逐工具策略。
3. `planned` 后端适配器不得包含可执行命令或端点，环境开关不能绕过状态门禁。
4. 前端不得保存真实 Secret，也不得在仓库内出现真实凭证。
5. 上游清单只用于发现项目；能否连接必须按模镜运行边界重新核验。

## 4. 适配架构与状态契约

批次 0 引入目录专用的受控适配器层：

- 前端目录只保存中文展示资料、批次和风险说明，不再把命令或 URL 作为执行依据。
- 后端 `server/mcp/catalog.py` 是项目 ID、连接方式、固定命令、配置字段、凭据槽和功能开关的唯一执行来源。
- 前端只能按项目 ID 调用目录专用的准备、配置、连接、断开和工具调用 API，不允许提交命令、URL、Header、环境变量名或工作目录。
- `planned` 条目即使被错误设置环境开关，也不能获得可执行命令；没有显式工具策略的新适配器不能调用工具。
- 后端已有 Streamable HTTP / SSE、SSRF 校验、加密凭据引用和工具审批元数据，但“底座存在”不代表目录项目已经完成生产适配。

目录状态统一为：

- `planned`：已归入批次，尚未进入实现。
- `adapting`：正在实现和核验，前端仍不可连接。
- `ready`：安装、连接、权限、Smoke 和回退全部通过。
- `blocked`：上游、运行环境或安全门槛存在明确阻塞。

## 5. 固定批次

目录数量在适配期间冻结为 100；以下 93 项必须且只能属于一个批次。

| 批次 | 能力与条目 | 数量 | 主要退出门槛 |
| --- | --- | ---: | --- |
| 0 | 现有 Node stdio 基线与适配 Harness | 7 | 100 项契约、服务端清单、功能开关、状态 API、现有行为回归 |
| 1 | `calculator-mcp`、`time-mcp`、`vegalite-mcp` | 3 | **已实现**：非 root Python 沙箱、默认断网、只读文件、CPU/内存/时间/输出上限 |
| 2 | `bibigpt-mcp`、`fetch-mcp`、`quickchart-mcp`、`airbnb-mcp`、`geowire-mcp` | 5 | **已完成门槛判定**：Fetch、QuickChart、GeoWire 可用；BibiGPT 因 OAuth、Airbnb 因上游 schema 漂移受阻 |
| 3 | `basic-memory-mcp`、`excel-mcp-server`、`git-mcp`、`manim-mcp`、`markitdown-mcp` | 5 | **已完成门槛判定**：Basic Memory、Excel、Git、MarkItDown 可用；Manim 因任意 Python 场景执行依赖第 8 批隔离而阻断 |
| 4 | AgentQL、Brave、Exa、Firecrawl、Perplexity、Tavily、Axiom、Figma Context、Google Maps、Grafana、Graphlit、Kagi、Pinecone、Shodan、Snyk、VirusTotal | 16 | **已完成门槛判定**：15 项通过加密凭据槽、固定出口域、只读工具和 Secret 泄漏测试；Snyk 因本地构建执行依赖第 8 批隔离而阻断 |
| 5 | DBHub、PostgreSQL、MongoDB、ClickHouse、Cognee、Graphiti、Hindsight、Redis、SQLite、DuckDB、Supabase | 11 | **已完成门槛判定**：DBHub、MongoDB、ClickHouse、Redis、DuckDB、Supabase 通过结构化配置、租户凭据、协议级只读、预检与查询限制；PostgreSQL、SQLite 因归档实现受阻，三项状态化记忆转入第 5B 计划 |
| 6 | Airtable、Asana、GitLab、中国电商经营、Notion、Mem0 | 6 | 修改预览与审批、幂等、限流、账号解绑 |
| 7 | Chrome DevTools、Playwright、Puppeteer、Selenium | 4 | 临时浏览器、目标域、上传下载与会话清理边界 |
| 8 | MCP Run Python、MCP Python Interpreter | 2 | 一次性断网容器、无宿主挂载、无 Docker socket、进程资源限制 |
| 9 | Apify、Bright Data、Browserbase、E2B、Stripe、Terraform、Aiven、Alpaca、AWS KB、ElevenLabs、MiniMax、S3、Kubernetes、Semgrep | 14 | 费用/资源上限、目标预览、终止性操作强制审批 |
| 10 | Gmail、Atlassian、Google Calendar、Google Drive、Microsoft 365、OneDrive、Sentry、Azure、Box、Cloudflare、GitHub、Linear、Neon、Slack | 14 | PKCE、state、最小 scope、刷新、撤销与解绑 |
| 11 | 小红书、Ableton、Binary Ninja、Blender、Ghidra、JetBrains、ChatCrystal、Obsidian、OpenTabs、Zotero、Docker、Mobile、XcodeBuild | 13 | 版本化本机桥接、宿主校验、逐应用与目录授权 |

每批在前一批验收完成后单独建分支和 PR；批内可以逐项开启功能开关，但不能绕过整批共享的安全门槛。

批次 2 的执行结论：

- `fetch-mcp` 固定兼容上游 0.6.3，只允许公网 HTTPS，遵守 robots.txt；每次 DNS 与重定向都重新校验并固定连接地址。
- `quickchart-mcp` 固定兼容上游 1.0.6，仅开放 `generate_chart`；拒绝远程引用和脚本回调，本批不开放本地文件下载。
- `geowire-mcp` 固定兼容上游 0.6.2，只开放无需 Key 的 Nominatim / OSRM 子集；Nominatim 固定每秒最多 1 次，OSRM 仅开放受限驾车查询。
- `bibigpt-mcp` 的上游远程 MCP 现在要求 OAuth 2.1 或 API Key，转入第 10 批授权适配；当前不展示登录入口。
- `airbnb-mcp` 0.3.0 的公开搜索页数据节点发生漂移，代表调用失败；在上游恢复稳定契约并重新通过 smoke 前保持不可连接。

批次 3 的执行结论：

- 独立 `modelmirror-mcp-files:wave3-v1` 镜像预装完整锁定依赖，运行时断网且不下载代码；sidecar 使用 UID/GID 65532、只读根文件系统、`network_mode: none`、移除全部 capabilities、Landlock 和 1 GiB cgroup。
- 工作区由服务端生成不透明 ID 并绑定租户和项目；上传拒绝绝对路径、`..`、ZIP Slip、链接/设备节点、Unicode/大小写冲突和压缩炸弹，封存后输入卷只读。普通工作区闲置 24 小时清理，产物保留 7 天，Basic Memory 数据持久保存。
- `basic-memory-mcp` 锁定兼容 v0.22.1，只开放本地 Markdown 读取能力，以及需一次性确认的写入、编辑和移动；云路由、遥测、自动更新、语义模型下载和删除关闭。上游 AGPL-3.0 仅作契约核验，镜像不包含其源码或二进制。
- `excel-mcp-server` 锁定兼容 1.0.4，输入只读，图表和写入只生成新产物；`git-mcp` 锁定兼容 0.6.2，仅开放固定只读命令并关闭 hooks、credential helper、external diff/textconv、协议访问和网络功能。
- `markitdown-mcp` 锁定兼容 v0.1.7，只调用官方库的 `convert_local()`，仅接受受控文件 ID并生成 Markdown 产物；URL、URI 和宿主路径全部拒绝。
- `manim-mcp` 保留第 3 批编号但为 `blocked`，目录只显示“依赖第 8 批一次性代码执行容器”，不提供安装、连接或 Python 场景执行入口。
- `state-write` 工具首次调用返回 409 和 5 分钟一次性审批；审批绑定项目、会话、工具、工作区、输入版本和参数摘要，重连、重配、状态漂移、过期或重放均失效。

批次 4 的执行结论：

- 独立 `modelmirror-mcp-token:wave4-v1` 镜像预装精确 lockfile 中的上游包，运行时不下载代码；容器使用 UID/GID 65532、只读根文件系统、无 Docker socket、移除 capabilities、`no-new-privileges`、Landlock 临时工作区和固定资源上限。
- `/mcps` 在每张 Token MCP 卡片内提供独立“加密凭据”入口，不跳转或混入 Toolset 凭据区。新凭据由服务端固定绑定当前项目与槽位，前端保存配置时只提交不透明 `credential_id`；服务端连接时解密，经私有 Unix socket 单次交给 sidecar，响应、会话摘要和日志均不包含 Secret。
- sidecar 对工具发现和工具调用分别过滤，只允许清单中的只读工具；客户端提交的 URL 参数先经过公网 HTTPS、DNS 与 SSRF 预检。Node 上游进程还加载固定 DNS 出口守卫，4 个兼容适配器通过 `SafeHttpClient` 固定连接地址并逐跳校验。
- 每个适配器锁定“已开放工具名 + inputSchema”的规范化 SHA-256；离线 Docker smoke 发现缺失工具或参数 schema 漂移时整项阻断。
- “传输已连接”不等于凭据有效：首次只读工具调用成功后才显示“凭据已验证”，工具返回认证错误或调用异常时显示“验证失败”。凭据状态与 `updated_at` 绑定当前会话；卡片内撤销、不可解密或重新配置都会断开会话并要求重新保存。通用 `/api/mcp/{session_id}/call` 对目录会话返回 403，防止绕过项目级策略。
- Axiom 采用已归档 v0.05 的 Token 只读契约；Brave 与 Google Maps 锁定 archived reference server 契约。Figma 图片下载、Firecrawl/Tavily 长任务、Graphlit 写入、Perplexity 深度研究、Pinecone 文件管理及所有修改/删除工具均关闭。
- Snyk 1.15.2 会读取本地项目并可能启动 Gradle、Maven 等构建链，保留第 4 批编号但为 `blocked`；不提供安装、连接或外站登录入口，等待第 8 批一次性代码执行隔离。

批次 5 的执行结论：

- `modelmirror-mcp-database:wave5-v1` 由两个相互隔离的服务运行：远程 sidecar 仅承载 DBHub、MongoDB、ClickHouse、Redis 与 Supabase 的固定目标协议；本地 sidecar 运行在 `network_mode: none`，只读取 DuckDB 封存工作区。两者均为非 root、只读根文件系统、移除 capabilities、禁止 Docker socket，并通过独立 Unix socket 与 server 通信。
- 远程条目只接受服务端清单声明的结构化字段；服务端在私有握手内生成驱动配置，客户端和日志均看不到 DSN、密码、Header 或环境变量名。DuckDB 只接受不透明 `workspace_id` 与 `.duckdb` 文件 ID，不接受宿主路径或 MotherDuck/S3 等远程切库能力。
- 目录凭据按 `tenant_id + owner_id + project_id + slot` 隔离；跨租户或所有者查询统一表现为不存在。旧凭据记录安全迁移到显式 `local/local`；生产环境可强制要求外部 `MODEL_MIRROR_CREDENTIAL_MASTER_KEY`，本地开发仍可使用既有自动生成密钥模式。当前服务端只使用部署时固定的 tenant/owner，尚无逐请求身份传播，因此 Wave 5 仅面向单租户本地部署；多用户共享部署仍为发布阻断。
- “只读”由 sidecar 协议网关和数据库原生会话共同执行，不依赖前端提示或工具名称。SQL 仅允许单语句读取，MongoDB 拒绝 `$out`、`$merge`、`$where`、`$function`，Redis 不暴露 raw command、Lua、CONFIG、DEBUG 或 KEYS，DuckDB 禁止 ATTACH、COPY、INSTALL、LOAD、外部访问和扩展自动加载。
- 连接预检覆盖结构化字段、目标解析、严格 TLS、认证、原生只读模式和查询限制。默认返回 200 行、硬上限 1000 行，数据库 statement timeout 为 15 秒，目录调用超时 20 秒，内联结果最多 256 KiB；超时或预检失败时不保留可调用会话。
- Supabase 使用固定本地 stdio 适配器、Personal Access Token 与 20 位小写字母 `project_ref`，并固定调用官方 `/database/query/read-only` 端点，只开放指定项目的表、扩展和只读 SQL 能力；本批不使用 OAuth，不显示外站登录入口，也不开放迁移、函数、分支或项目管理。
- PostgreSQL 官方参考实现因归档和 npm 弃用保持 `blocked`，可使用受控 DBHub PostgreSQL 模式；SQLite 官方实现因归档且暴露写工具保持 `blocked`。Cognee、Graphiti 与 Hindsight 属于有状态记忆系统，转入“第 5B 批：状态化记忆”，在独立持久卷、模型/费用、租户数据保留与通用写审批完成前不连接。
- 本批不开放任何数据库写入、删除、DDL 或状态化记忆写工具，也不复用批次 3 的文件工作区审批冒充数据库审批。后续写能力必须另行实现数据库目标和状态绑定的一次性审批。

## 6. 验收、发布和回退

- 每个项目必须通过初始化、工具发现、代表性调用、超时、重连、断开与清理测试；Schema 漂移视为阻断。
- 安全测试按批次覆盖 Secret 泄漏、SSRF、重定向、路径越界、ZIP Slip、链接与压缩炸弹、跨项目/产物/租户越权、沙箱逃逸、TLS 降级、SQL 多语句及写入旁路、查询超时/行数、权限撤销、审批绕过和高风险操作默认关闭。
- 前端必须显示中文批次、状态、连接方式、风险、限制和门槛；后端未返回 `executable=true` 时按钮不可连接。
- 每个项目有独立服务端开关。回退只关闭开关、断开会话并清理沙箱，不删除目录条目或凭据。
- 运行日志只记录项目 ID、状态、耗时、错误类别和策略事件，不记录 Secret、完整参数或工具返回正文。
- 共享栈重建必须先确认时间窗口和基线；存在并行工作树冲突时先启动独立预览项目。人工验收通过后还要重新 fetch 并核对 `origin/main`、冲突、工作树状态和实际变更文件，确认无误后才提交 PR。

## 7. 明确不进入本路线的远期能力

- 用户自定义 MCP 连接不进入批次 0—11；目录配置只允许固定适配器声明的字段。
- MCP Builder、可视化生成器和公开发布市场不进入批次 0—11。
- 若未来立项，必须另行威胁建模、设计评审和建立独立路线，不复用目录适配功能开关直接放行。

## 8. 风险与回退

主要风险是上游项目快速变化、条目说明过期，以及把“已收录”误解为“当前可安全运行”。前端必须持续使用明确的状态标签和禁用按钮表达边界。

批次 0–4 不引入数据库迁移；批次 5 只把旧凭据 JSON 元数据显式迁移到 `local/local` 作用域，不接触外部数据库 schema 或数据。批次 2 回退时关闭对应公网项目开关并停止 `mcp-public`；批次 3 回退时关闭四个文件项目开关、撤销审批并停止 `mcp-files`；批次 4 回退时关闭 15 个 Token 项目的独立功能开关、断开目录会话并停止 `mcp-token`；批次 5 回退时关闭六个数据库项目开关、断开会话、停止远程和本地数据库 sidecar，并清理临时 DuckDB 工作区。回退不删除卡片专属加密凭据，也不修改或删除外部数据库数据；持久 Basic Memory 数据同样不自动删除。Airbnb、BibiGPT、Manim、Snyk、PostgreSQL、SQLite、Cognee、Graphiti 与 Hindsight 本来就不可执行。旧版 `/api/mcp/*` 接口保持兼容，但目录会话不能使用无项目策略的直接调用端点：

- `server/mcp/catalog.py`
- `server/mcp/sandbox_proxy.py`
- `server/mcp/public_proxy.py`
- `server/mcp/file_proxy.py`
- `server/mcp/token_proxy.py`
- `server/mcp/database_proxy.py`
- `server/mcp/workspace.py`
- `server/sandbox_sidecar/`
- `client/src/data/mcpProjects.ts`
- `client/src/data/mcpAdaptationPlan.ts`
- `client/src/pages/McpBrowserPage.tsx`
- `client/src/components/McpServerCard.tsx`

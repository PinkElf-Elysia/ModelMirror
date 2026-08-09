# MCP 目录扩充与适配路线

最后更新日期：2026-08-09
维护人：模镜团队

## 1. 目标与当前范围

模镜的 MCP 页面同时承担两种职责：

1. 提供经过核验、能够在当前后端沙盒中启动的本地 stdio Server。
2. 建立中文 MCP 能力目录，让用户先了解工具用途、分类、依赖和未来接入状态。

当前目录从以下社区清单整理：

- [Awesome-MCP-ZH](https://github.com/yzfly/Awesome-MCP-ZH)
- [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)

截至 2026-08-09，目录仍冻结为 100 个 MCP 条目、18 个分类。批次 8 的两个 Python 执行上游均未通过门槛；批次 9 仅 Terraform 公共 Registry 只读子集通过；批次 10 暂缓到多租户主体边界完善后；批次 11 的 13 个桌面/宿主条目因缺少可信本机桥接、宿主实例证明和逐应用授权而全部受阻。当前状态精确为 **45 ready / 14 planned / 41 blocked**。

批次 0—11 的第一阶段交付边界、状态分布、阶段二准入条件和可复现验收命令统一记录在 [MCP 适配第一阶段收口](./MCP_ADAPTER_PHASE_ONE_CLOSEOUT.md)。

## 2. 当前边界与明确不实现

批次 1–11 只允许固定且完成验收的适配器：批次 1 使用完全断网计算 sidecar，批次 2 使用独立公网 sidecar，批次 3 使用受控上传工作区和完全断网的文件处理 sidecar，批次 4 使用固定 Token 槽和只读出口 sidecar，批次 5 使用结构化数据库配置及相互隔离的远程/本地数据库 sidecar，批次 6 使用固定 SaaS 服务、账号作用域和资源级一次性审批 sidecar，批次 7 使用真实锁定上游、临时 Chromium 和强制出口代理；批次 8 因两个上游均未通过门槛，不新增运行时；批次 9 仅新增无凭据、固定公共 Registry 出口的 Terraform sidecar；批次 10 暂缓且批次 11 全部受阻，都不新增运行时。这些批次都不扩大任意连接或任意执行能力：

- 不接受用户指定的 Python 包、解释器、命令、工作目录或其他额外运行时。
- 不实现 OAuth 回调、刷新或外站登录；批次 4—5 的卡片可把明文 Secret 单次提交到服务端加密库，但连接配置只接受不透明 `credential_id`，保存后不再返回或显示明文。
- 不打开外站认证登录页，也不提供认证按钮或深链。
- 不把 Streamable HTTP、SSE 或任意 MCP URL 暴露为目录配置；批次 2 仅由固定 stdio 适配器访问受控公共 HTTPS 服务。
- Fetch 的用户 URL 只是工具参数，只允许公网 HTTPS，并逐次执行 DNS、SSRF、重定向和响应大小校验；它不是自定义 MCP 连接入口。
- 批次 3 客户端只能提交不透明 `workspace_id`、文件 ID 和产物名，不能提交宿主路径、`cwd`、环境变量或 URI；输入文件封存后只读。
- 批次 5 客户端只能提交清单声明的数据库类型、主机名、端口、库名、用户名、严格 TLS 模式或 Supabase `project_ref`；DBHub 首批仅支持 PostgreSQL、MySQL 与 MariaDB，SQL Server 保持关闭；不接受 DSN、数据库 URI、SSH 隧道、Header、环境变量名或任意连接参数。
- 批次 6 只接受清单声明的 Personal Access Token / Internal Integration Token 与固定资源 ID；不接受服务 URL、任意 Host、Header、环境变量、OAuth 回调或外站登录。GitLab 首批只连接 `gitlab.com`，自建 GitLab 保持未开放。
- 批次 6 的真实账号执行还需部署者显式设置单用户本地实例确认门禁；当前 API 没有逐请求认证主体传播，多用户共享部署不得开启。
- 批次 7 不接受浏览器启动参数、CDP/远程端点、profile、代理、Header、Cookie、storage、上传/下载路径或本机文件。用户只提交待审批且不含 Token、API Key、签名等敏感查询参数的公网 URL，以及网关签发的不透明元素 ref；首版不采集账号凭据、不提供外站登录流程，也不继承或保存登录态。页面仍可能自行呈现无法由透明 HTTPS 出口可靠识别的登录界面，用户不得输入账号、密码、OTP 或其他认证信息。
- 批次 8 不接受 Python 代码、包名、解释器、环境、文件、会话 ID、命令、工作目录或执行超时。两个条目均无镜像、命令、端点或工具策略；功能开关不能把 `blocked` 改为可执行。
- 批次 9 的 Terraform 适配器不接收 Token、组织、工作区、状态文件、变量、Terraform CLI 路径或任意 Registry URL；仅匿名访问固定 `registry.terraform.io`。HCP Terraform、Terraform Enterprise、私有 Registry、plan、apply、destroy、run 与资源变更能力全部不可发现、不可调用。
- 批次 10 保持 `planned`，待不可伪造的逐请求用户主体、租户隔离和 OAuth 生命周期完成后再恢复；本轮不引入其暂缓改动。
- 批次 11 不接收本机端口、LAN 主机、宿主路径、Docker Socket、浏览器登录态、IDE/DAW/逆向工具实例、设备 ID、USB、Xcode 工程、API Key 或插件配置。13 项均无镜像、命令、端点、配置/凭据字段和工具策略。
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
2. `ready` 后端适配器必须有固定命令或端点、独立功能开关和显式工具策略；现有 7 项以兼容策略保持行为不变，批次 1—7 的 37 项使用完整的逐工具策略。
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
| 6 | Airtable、Asana、GitLab、中国电商经营、Notion、Mem0 | 6 | **已完成门槛判定**：Airtable、Asana、GitLab.com、Notion 通过固定作用域、真实预检、资源级审批、限流与解绑；中国电商与 Mem0 因授权/托管契约受阻 |
| 7 | Chrome DevTools、Playwright、Puppeteer、Selenium | 4 | **已完成门槛判定**：Chrome DevTools 与 Playwright 通过真实上游 Schema、临时浏览器、固定出口、页面状态审批与清理测试；Puppeteer、Selenium 因归档安全风险与许可证/运行时边界受阻 |
| 8 | MCP Run Python、MCP Python Interpreter | 2 | **已完成门槛判定**：MCP Run Python 因维护方明确否定 Pyodide 不可信代码沙箱并归档而受阻；MCP Python Interpreter 因进程内执行、包/文件/会话能力及空 LICENSE 发布物受阻 |
| 9 | Apify、Bright Data、Browserbase、E2B、Stripe、Terraform、Aiven、Alpaca、AWS KB、ElevenLabs、MiniMax、S3、Kubernetes、Semgrep | 14 | **已完成门槛判定**：Terraform 公共 Registry 六项只读工具可用；其余 13 项因凭据出站未批准、费用无法对账、归档运行时、真实金融/云资源写入或本机/集群范围受阻 |
| 10 | Gmail、Atlassian、Google Calendar、Google Drive、Microsoft 365、OneDrive、Sentry、Azure、Box、Cloudflare、GitHub、Linear、Neon、Slack | 14 | PKCE、state、最小 scope、刷新、撤销与解绑 |
| 11 | 小红书、Ableton、Binary Ninja、Blender、Ghidra、JetBrains、ChatCrystal、Obsidian、OpenTabs、Zotero、Docker、Mobile、XcodeBuild | 13 | **已完成门槛判定**：全部依赖真实本机宿主、登录态、目录、设备或 Docker daemon；缺少版本化桥接、宿主实例证明、会话主体绑定和逐应用授权，13 项均受阻 |

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

批次 6 的执行结论：

- 独立 `modelmirror-mcp-saas:wave6-v1` 只运行仓库内置的固定 REST 兼容契约，运行时不下载上游代码。sidecar 为非 root、只读根文件系统、无宿主目录与 Docker socket、移除 capabilities，并仅通过私有 Unix socket 接收服务端生成的短期配置。
- Airtable 锁定兼容上游 MCP v1.14.0，Asana 锁定 v1.6.0，Notion 锁定 v2.5.0；三者均按 MIT 契约独立实现。GitLab 归档参考 MCP 0.6.2 不进入镜像，首批仅以自有固定 REST 契约访问 `gitlab.com`。每项只连接编译进镜像的官方 HTTPS Host，禁止重定向和任意 URL/Header。
- Airtable 绑定固定 Base，Asana 绑定 Workspace/Project，GitLab 绑定数字 Project ID，Notion 绑定固定 Data Source。连接时先执行账号身份与目标资源只读预检；预检失败不保留可调用会话。Token 由卡片内加密凭据槽保存，不使用 OAuth，也没有外站登录入口。
- 只读调用遇到 `429`、`502`、`503` 或 `504` 时最多有界重试两次，并遵守封顶后的 `Retry-After`；写调用不自动重试。明确的限流或提供商拒绝作为终止性失败返回，只有 `-32008`、发送后超时或连接中断等歧义结果才标记 `unknown_outcome`，要求用户先到服务商核对。
- `create`、`update` 与 `comment` 类工具均为 `state-write + requires_approval`。一次性审批冻结租户、所有者、项目、会话、工具/Schema、规范化参数、配置版本、凭据版本、账号预检摘要、目标资源预览和服务端幂等键；连接时完整工具名与 `inputSchema` 摘要必须匹配冻结契约。5 分钟过期，重连、重配、凭据轮换、账号或策略漂移、重复确认都会失效。删除、归档、合并、仓库写入、批量写入和其他终止性工具不暴露。
- 目录会话在管理器发布前即绑定目录所有者，通用会话接口不能发现、调用或断开。连接、配置、调用、确认、解绑与凭据撤销共享生命周期门禁：连接中拒绝重配，解绑/撤销开始后拒绝新调用和旧审批确认，并先等待活动读取结束再断开子进程。
- 项目级解绑会作废待审批、清除作用域和本地执行账本，并可撤销该卡片的本地加密凭据。移除本地 Token 不等于在服务商后台撤销，界面会明确提示用户自行完成上游撤销。
- 当前目录服务仍是部署时固定 `tenant/owner` 的单例，因此真实 SaaS 能力除项目开关外还要求 `MCP_CATALOG_STATEFUL_SAAS_SINGLE_USER_ACK=true`；默认关闭，多用户部署继续视为发布阻断。
- `mcp-cn-commerce` v0.1.5 覆盖八个平台和 114 个工具，各平台授权、域名、短期 Token 与订单/售后敏感数据无法作为单一契约验收，保持 `blocked`。Mem0 本地 v0.2.1 已归档，官方迁移到无固定版本的托管远程 MCP；在 OAuth、Schema 锁定和租户记忆作用域完成前保持 `blocked`。

批次 7 的执行结论：

- 独立 `modelmirror-mcp-browser:wave7-v1` 锁定 `chrome-devtools-mcp` 1.6.0 + Chrome for Testing 150.0.7871.24，以及 `@playwright/mcp` 0.0.79 + Chromium 1237 / Chrome for Testing 152.0.7977.8；两个适配器使用彼此独立的固定浏览器路径。每个目录连接启动真实上游 stdio MCP 与独立临时 profile；安全网关核对 `initialize`、工具名和规范化 `inputSchema`，每个适配器只公开五项经过审核的上游能力和一个网关状态工具，运行时不安装或更新依赖。
- `mcp-browser` 执行容器使用 `network_mode: none`、非 root、只读根文件系统、移除 capabilities、`no-new-privileges`、1 GiB/1.5 CPU/256 PIDs，且无 Docker socket 或宿主目录；256 PID 配额只用于该浏览器执行容器。独立 `mcp-browser-egress` 仅持有公网网络和出口 Unix socket，不挂载 MCP socket、profile 或截图产物；上游 Chromium 无法直接访问它。两者均与既有 Runtime Browser 完全分离。每会话一页、最多 50 次动作、15 分钟总时长/5 分钟闲置，首版目录实例只运行一个浏览器会话；控制密钥和 MCP socket 只在 Docker 重启策略的 10 秒保护窗口之后（实现使用 11 秒裕量）开放，任何会话结束或异常都会终止两个 PID1、清除 profile，并由 `unless-stopped` 成对重建。
- Landlock 只允许上游进程读取锁定运行时并写当前 profile/截图暂存目录，继续拒绝 `/run` 与兄弟路径。Chromium 创建自身 user namespace 时所需的 procfs 映射写入仅恢复 `WRITE_FILE`；不开放 procfs 创建、删除、截断、目录写或 socket 权限，容器仍无 capabilities，Docker 对敏感 procfs 的掩蔽与只读挂载保持不变。
- Chromium 强制通过执行容器内的 loopback HTTP/CONNECT 代理，再以不传给上游的一次性会话能力访问出口 sidecar。只允许公网 HTTP/HTTPS 80/443；生产出口不使用宿主 DNS，而是通过固定数值地址的 Cloudflare JSON DoH（TLS 1.2+、SNI/证书名 `cloudflare-dns.com`）重新解析完整 A/AAAA 集合，再执行 SSRF 门禁，连接固定到已验证地址并保留目标 hostname/SNI。Synthetic DNS 只在随机隔离 fixture smoke 中显式开启，生产 Compose 固定为 false。IP 字面量、单标签主机、userinfo、私网/回环/链路本地/保留/metadata、混合 DNS 答案、跨 origin 请求与重定向均拒绝；每会话最多 12 个并发隧道和累计 64 MiB 流量，并限制隧道闲置与绝对时长。
- 出口进程生成的一次性配对密钥只由两个 sidecar 父进程持有，不传给上游。任一 sidecar 单独重启后旧配对与旧会话均 fail-closed；运维恢复必须成对重启执行端与出口端，不恢复旧页面或元素 ref。
- 单 origin 门禁覆盖锁定上游的正常 Chromium 流量与恶意网页。若锁定的上游或浏览器进程本身被完全攻陷，独立出口仍只连接经过校验的公网 IP，并继续执行端口、流量与时限门禁，但没有 TLS 终止能力，不能保证同一公网 IP 与证书下的其他虚拟主机绝对隔离；本批不把该边界描述为浏览器 RCE 防护。
- 导航、点击和填写必须经过 `browser-session` 一次性审批。审批冻结目录会话、浏览器 generation、页面 revision/digest、origin、工具/Schema、参数和配置版本；确认时状态漂移即失效。交互调用不重试，歧义超时会标记 `unknown_outcome`、污染并关闭会话；sidecar 明确返回 `-32011` 且原因属于调用前 DNS/目标 URL 拒绝时，旧审批终止但会话保留，修正目标后必须重新审批，调用后风险原因仍不得降级为明确拒绝。截图先进入 64 MiB 临时共享卷，经后端单文件描述符校验后复制到仅服务端挂载的可信目录并持久登记 24 小时索引；可通过项目专属接口下载或清理。异常临时项仅按固定深度与已知命名清扫，畸形条目告警并 fail-closed。网页上传、网页下载、登录态保存、Cookie/Storage 导入导出与持久化、剪贴板、任意脚本求值工具、CDP、扩展与本机文件不进入本批。网页自身的 Cookie、缓存和站点存储只存在于临时 profile，断开时删除。
- Chrome DevTools MCP 1.6.0 与 Playwright MCP 0.0.79 为 `ready`。官方 Puppeteer MCP 已归档且保留危险启动/脚本入口，Selenium MCP 0.2.3 存在 MIT/ISC 许可证元数据冲突、root/漂移镜像和任意参数/路径/脚本/Cookie 面，二者保持 `blocked`，不得通过功能开关绕过。

批次 8 的执行结论：

- `mcp-run-python` 0.0.22 的官方仓库已归档。维护方明确说明 Pyodide 中的 Python 可执行任意 JavaScript、污染后续调用、读写运行时可见文件，并且 Deno 无法提供可靠内存限制；因此不构建 Deno/Pyodide sidecar，也不把实验性的 Monty 替换成同名适配器。
- `mcp-python-interpreter` 的最新已发布版本固定核验为 PyPI 1.2.3。其 `run_python_code` 默认使用 `inline`，在 MCP Server 进程内维护全局 REPL 会话；同时公开任意 pip 安装、文件读写、环境选择和最长 300 秒子进程执行。发布 wheel 的 metadata 使用 MIT classifier，但携带的 `LICENSE` 文件为空，无法形成完整可再分发来源证明。
- 两项均为 `blocked`，没有 Docker 镜像、Unix socket、宿主挂载、Docker socket、运行命令、端点或代码输入入口。第 8 批没有交付可复用的一次性代码执行边界，因此 Manim 与 Snyk 的既有阻断也不自动解除；若未来引入新的维护中执行引擎，应作为新威胁模型和新适配器重新验收，不复用这两个项目 ID 冒充上游兼容。

批次 9 的执行结论：

- `terraform-mcp` 固定为 [HashiCorp Terraform MCP Server v1.2.0](https://github.com/hashicorp/terraform-mcp-server/tree/v1.2.0) 的公共 Registry 只读兼容契约。独立 `modelmirror-mcp-registry:wave9-v1` 只开放 `get_latest_provider_version`、`get_provider_capabilities`、`get_provider_details`、`search_modules`、`get_module_details` 与 `get_latest_module_version`，工具 Schema 摘要冻结为 `73a2b116bcaa257dbf158d1ab8a778d067dac2d969db7dff160372d1617e3445`。
- `mcp-registry` 使用独立 Unix socket、非 root UID/GID 65532、只读根文件系统、移除全部 capabilities、`no-new-privileges`、512 MiB/1 CPU/64 PIDs 与空白 tmpfs 工作区；最多四个会话，调用不重连。Docker Desktop 把公网 DNS 映射到 `198.18.0.0/15` 时只允许该传输兼容地址，应用层仍把主机编译固定为 `registry.terraform.io`，保留 TLS hostname/证书校验，不接收用户 URL。
- 真实隔离验收通过六项工具：`hashicorp/random` 最新版本 `3.9.0`、3 类能力、1935 字节公开文档；`vpc` 搜索首项 `terraform-aws-modules/vpc/aws/6.6.1`，详情裁剪为 50 个输入并确认最新版本 `6.6.1`。`apply` 在真实网关调用中返回拒绝；HCP/TFE、私有 Registry、plan、destroy 与资源写入工具均不在工具清单。
- Apify 与 Aiven 因未批准账号凭据出站保持 `blocked`；Bright Data 因缺少供应商账单对账与逐项目硬预算受阻；Browserbase、E2B、Semgrep 因归档/不维护运行时受阻；Stripe 转入第十批 OAuth 与金融终止操作审批；Alpaca、AWS KB、ElevenLabs、MiniMax、S3 Tables、Kubernetes 因真实交易、云资源、付费媒体、AWS/集群作用域或写入面保持关闭。

批次 11 的执行结论：

- 本批没有创建桌面代理、宿主端插件、Docker 服务、端口转发或授权 UI。一个可发布的桥接必须先绑定不可伪造的用户会话、宿主实例/版本、应用或项目范围、工具 Schema、逐动作同意与撤销状态；现有 Office/Coding 宿主流程不能在未实现各应用协议时被声明为通用桌面桥。
- 小红书当前发布依赖本机 Chromium、Cookie/QR 登录、绝对媒体路径并包含发布/评论等真实账号写入；OpenTabs 0.0.115 复用浏览器登录态并动态开放约 2000 个工具。两者都与第七批“不继承登录态、固定工具契约”的浏览器边界冲突。
- Ableton MCP 1.3.5、Blender MCP 1.8.0、Binary Ninja MCP v1.2.1、GhidraMCP v0.2.2+ghidra12.0.4 与 JetBrains Proxy（源码 1.9.0 / npm 1.8.0）都通过本机插件或 localhost/LAN 端口控制真实桌面宿主；能力包含项目/场景/二进制修改、任意 Python 或 IDE 动作，不能由服务端容器沙箱代替宿主授权。
- ChatCrystal 0.5.8 会导入本机编码对话并调用可配置模型服务；MCPVault 0.15.0 直接读写 Vault；Zotero MCP 0.9.1 读取文献全文并可通过 Web API 写入；这些敏感目录或账号范围没有受信任本机 grant，不能用宿主路径或 Token 临时拼接。
- Docker MCP Gateway v0.43.3 是动态 Server/容器/Secret/OAuth 控制面，不是固定只读适配器；模镜继续禁止挂载 Docker Socket。Mobile MCP 1.0.2 和 XcodeBuildMCP 2.7.0 可安装应用、控制真机/模拟器、构建/测试/调试和执行 UI 输入，也没有测试专用设备与 macOS 主机证明。
- 13 项公开状态均为 `blocked + executable=false`，环境功能开关不能绕过；`runtime_image`、`server_command`、`endpoint`、配置/凭据字段和工具策略全部为空。未来若实现桌面代理，应按单一应用、单一只读子集逐项重新立项验收，而不是开启通用 localhost 或宿主路径输入。

## 6. 验收、发布和回退

- 每个项目必须通过初始化、工具发现、代表性调用、超时、重连、断开与清理测试；Schema 漂移视为阻断。浏览器条目还必须在双 sidecar 容器中走完 UDS 握手、受控导航、快照、元素交互、真实截图登记、断开与进程/profile/临时文件清理，单独的上游 `tools/list` 或容器健康检查不能作为 ready 证据。
- 安全测试按批次覆盖 Secret 泄漏、SSRF、DNS 重绑定、跨域重定向、路径越界、ZIP Slip、链接与压缩炸弹、跨项目/产物/租户越权、浏览器 profile/进程清理、元素 ref 与页面 digest 漂移、沙箱逃逸、TLS 降级、SQL 多语句及写入旁路、查询超时/行数、权限撤销、审批绕过、参数/配置/凭据漂移、限流、重试、幂等重放、未知写入结果、账号解绑和高风险操作默认关闭。
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

批次 0–4 不引入数据库迁移；批次 5 只把旧凭据 JSON 元数据显式迁移到 `local/local` 作用域，不接触外部数据库 schema 或数据。批次 2 回退时关闭对应公网项目开关并停止 `mcp-public`；批次 3 回退时关闭四个文件项目开关、撤销审批并停止 `mcp-files`；批次 4 回退时关闭 15 个 Token 项目的独立功能开关、断开目录会话并停止 `mcp-token`；批次 5 回退时关闭六个数据库项目开关、断开会话、停止远程和本地数据库 sidecar，并清理临时 DuckDB 工作区；批次 6 回退时关闭四个 SaaS 项目开关和全局单用户确认门禁、执行项目解绑并停止 `mcp-saas`；批次 7 回退时关闭两个浏览器项目开关、作废待确认操作、断开会话并停止 `mcp-browser` 与 `mcp-browser-egress`，随后清理临时 profile 与截图产物。批次 8 与批次 11 没有新增运行时，回退只需恢复目录状态与说明；批次 9 回退时先断开 Terraform 目录会话，再停止 `mcp-registry`，无需删除凭据、工作区或外部资源。回退不自动删除外部 SaaS 数据，也不声称撤销服务商 Token；卡片专属加密凭据仅在用户选择撤销时删除。Airbnb、BibiGPT、Manim、Snyk、PostgreSQL、SQLite、Cognee、Graphiti、Hindsight、中国电商经营 MCP、Mem0、Puppeteer MCP、Selenium MCP、MCP Run Python、MCP Python Interpreter 与全部批次 11 条目本来就不可执行。旧版 `/api/mcp/*` 接口保持兼容，但目录会话不能使用无项目策略的直接调用端点：

- `server/mcp/catalog.py`
- `server/mcp/sandbox_proxy.py`
- `server/mcp/public_proxy.py`
- `server/mcp/file_proxy.py`
- `server/mcp/token_proxy.py`
- `server/mcp/database_proxy.py`
- `server/mcp/saas_proxy.py`
- `server/mcp/workspace.py`
- `server/sandbox_sidecar/`
- `client/src/data/mcpProjects.ts`
- `client/src/data/mcpAdaptationPlan.ts`
- `client/src/pages/McpBrowserPage.tsx`
- `client/src/components/McpServerCard.tsx`

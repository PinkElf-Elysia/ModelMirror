# MCP 目录扩充与适配路线

最后更新日期：2026-08-02
维护人：模镜团队

## 1. 目标与当前范围

模镜的 MCP 页面同时承担两种职责：

1. 提供经过核验、能够在当前后端沙盒中启动的本地 stdio Server。
2. 建立中文 MCP 能力目录，让用户先了解工具用途、分类、依赖和未来接入状态。

当前目录从以下社区清单整理：

- [Awesome-MCP-ZH](https://github.com/yzfly/Awesome-MCP-ZH)
- [awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers)

截至 2026-08-02，前端收录 100 个 MCP 条目、18 个分类。其中只有无需 OAuth、Token、额外运行时、桌面宿主或外站认证流程的 7 个 Node stdio Server 标记为“本地 stdio 可连”；其余 93 个条目统一标记为“已收录、待适配”。

## 2. 本轮明确不实现

本轮只扩充目录、中文信息架构和适配状态，不扩大后端执行或授权能力：

- 不安装或启动 Python、Go、Rust、Java、Docker、浏览器、移动 SDK 等额外运行时。
- 不实现 OAuth 回调、Token / API Key 输入、保存、刷新或注入。
- 不打开外站认证登录页，也不提供认证按钮或深链。
- 不连接 Streamable HTTP、SSE 等远程 MCP 端点。
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
2. `ready` 后端适配器必须有固定命令或端点、独立功能开关和显式工具策略；现有 7 项以兼容策略保持行为不变。
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
| 1 | `calculator-mcp`、`time-mcp`、`vegalite-mcp` | 3 | 非 root Python 沙箱、默认断网、CPU/内存/时间/输出上限 |
| 2 | `bibigpt-mcp`、`fetch-mcp`、`quickchart-mcp`、`airbnb-mcp`、`geowire-mcp` | 5 | 公网目标、DNS 重绑定、SSRF、重定向与响应大小验证 |
| 3 | `basic-memory-mcp`、`excel-mcp-server`、`git-mcp`、`manim-mcp`、`markitdown-mcp` | 5 | 目录授权、路径越界与符号链接防护、产物清理 |
| 4 | AgentQL、Brave、Exa、Firecrawl、Perplexity、Tavily、Axiom、Figma Context、Google Maps、Grafana、Graphlit、Kagi、Pinecone、Shodan、Snyk、VirusTotal | 16 | 加密凭据槽、固定出口域、只读工具清单、Secret 泄漏测试 |
| 5 | DBHub、PostgreSQL、MongoDB、ClickHouse、Cognee、Graphiti、Hindsight、Redis、SQLite、DuckDB、Supabase | 11 | 只读账号、TLS、查询超时、行数限制、写入审批 |
| 6 | Airtable、Asana、GitLab、中国电商经营、Notion、Mem0 | 6 | 修改预览与审批、幂等、限流、账号解绑 |
| 7 | Chrome DevTools、Playwright、Puppeteer、Selenium | 4 | 临时浏览器、目标域、上传下载与会话清理边界 |
| 8 | MCP Run Python、MCP Python Interpreter | 2 | 一次性断网容器、无宿主挂载、无 Docker socket、进程资源限制 |
| 9 | Apify、Bright Data、Browserbase、E2B、Stripe、Terraform、Aiven、Alpaca、AWS KB、ElevenLabs、MiniMax、S3、Kubernetes、Semgrep | 14 | 费用/资源上限、目标预览、终止性操作强制审批 |
| 10 | Gmail、Atlassian、Google Calendar、Google Drive、Microsoft 365、OneDrive、Sentry、Azure、Box、Cloudflare、GitHub、Linear、Neon、Slack | 14 | PKCE、state、最小 scope、刷新、撤销与解绑 |
| 11 | 小红书、Ableton、Binary Ninja、Blender、Ghidra、JetBrains、ChatCrystal、Obsidian、OpenTabs、Zotero、Docker、Mobile、XcodeBuild | 13 | 版本化本机桥接、宿主校验、逐应用与目录授权 |

每批在前一批验收完成后单独建分支和 PR；批内可以逐项开启功能开关，但不能绕过整批共享的安全门槛。

## 6. 验收、发布和回退

- 每个项目必须通过初始化、工具发现、代表性调用、超时、重连、断开与清理测试；Schema 漂移视为阻断。
- 安全测试按批次覆盖 Secret 泄漏、SSRF、重定向、路径越界、沙箱逃逸、权限撤销、审批绕过和高风险操作默认关闭。
- 前端必须显示中文批次、状态、连接方式、风险、限制和门槛；后端未返回 `executable=true` 时按钮不可连接。
- 每个项目有独立服务端开关。回退只关闭开关、断开会话并清理沙箱，不删除目录条目或凭据。
- 运行日志只记录项目 ID、状态、耗时、错误类别和策略事件，不记录 Secret、完整参数或工具返回正文。

## 7. 明确不进入本路线的远期能力

- 用户自定义 MCP 连接不进入批次 0—11；目录配置只允许固定适配器声明的字段。
- MCP Builder、可视化生成器和公开发布市场不进入批次 0—11。
- 若未来立项，必须另行威胁建模、设计评审和建立独立路线，不复用目录适配功能开关直接放行。

## 8. 风险与回退

主要风险是上游项目快速变化、条目说明过期，以及把“已收录”误解为“当前可安全运行”。前端必须持续使用明确的状态标签和禁用按钮表达边界。

批次 0 不引入数据库迁移。回退时先断开目录会话，再恢复以下适配器、展示与文档文件；旧版 `/api/mcp/*` 接口保持兼容，可继续承载已有会话：

- `server/mcp/catalog.py`
- `client/src/data/mcpProjects.ts`
- `client/src/data/mcpAdaptationPlan.ts`
- `client/src/pages/McpBrowserPage.tsx`
- `client/src/components/McpServerCard.tsx`

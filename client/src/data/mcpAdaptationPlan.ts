export type McpAvailability = "planned" | "adapting" | "ready" | "blocked";
export type McpConnectionKind =
  | "local-stdio"
  | "sandboxed-stdio"
  | "remote-mcp"
  | "desktop-bridge";
export type McpRiskLevel = "low" | "medium" | "high" | "critical";
export type McpToolEffect = "read" | "artifact-create" | "state-write" | "terminal";
export type McpCredentialVerification =
  | "not-required"
  | "missing"
  | "unverified"
  | "verified"
  | "verification-failed";
export type McpSaasAccountStatus =
  | "not-applicable"
  | "blocked"
  | "unbound"
  | "unverified"
  | "verified";
export type McpDatabasePreflightStatus =
  | "not-applicable"
  | "blocked"
  | "awaiting-workspace"
  | "awaiting-configuration"
  | "unverified"
  | "verifying"
  | "verified"
  | "failed";

export interface McpBrowserPolicy {
  engine: string;
  contract_version: string;
  tool_schema_sha256: string;
  session_ttl_seconds: number;
  idle_ttl_seconds: number;
  max_pages: number;
  max_actions: number;
  max_concurrent_sessions: number;
  max_tunnels_per_session: number;
  max_egress_bytes_per_session: number;
  egress_tunnel_idle_seconds: number;
  egress_tunnel_ttl_seconds: number;
  navigation_timeout_seconds: number;
  call_timeout_seconds: number;
  max_output_bytes: number;
  max_artifact_bytes: number;
  max_artifacts_per_project: number;
  max_artifact_storage_bytes: number;
  artifact_ttl_seconds: number;
  allowed_schemes: string[];
  allowed_ports: number[];
  uploads: boolean;
  downloads: boolean;
  clipboard: boolean;
  local_files: boolean;
  cookies: boolean;
  storage: boolean;
  login_state: boolean;
  evaluate: boolean;
  cdp: boolean;
  limitations: string[];
}

export interface McpDatabasePolicy {
  mode: "remote-read-only" | "local-file-read-only";
  engine: string;
  read_only: boolean;
  tls_required: boolean;
  max_rows_default: number;
  max_rows_hard: number;
  statement_timeout_seconds: number;
  operation_timeout_seconds: number;
  preflight_checks: string[];
}

export interface McpSaasPolicy {
  provider: string;
  fixed_hosts: string[];
  preflight_checks: string[];
  rate_limit_per_minute: number;
  max_concurrent_calls: number;
  read_retry_limit: number;
  write_retry_mode: string;
  account_unbind_supported: boolean;
}

export interface McpWorkspacePolicy {
  required: boolean;
  persistent: boolean;
  max_file_bytes: number;
  max_workspace_bytes: number;
  max_files: number;
  idle_ttl_seconds: number | null;
  artifact_ttl_seconds: number;
  accepted_extensions: string[];
}

export interface McpSettingField {
  key: string;
  label: string;
  description: string;
  kind: "text" | "integer" | "enum" | "slug" | "hostname";
  required: boolean;
  default: string | number | null;
  minimum: number | null;
  maximum: number | null;
  options: Array<{ value: string; label: string }>;
  allowed_hostname_suffixes: string[];
}

export interface McpCredentialField {
  key: string;
  label: string;
  description: string;
  required: boolean;
  accepted_kinds: string[];
}

export interface McpAdaptationRecord {
  wave: number;
  availability: McpAvailability;
  connectionKind: McpConnectionKind;
  risk: McpRiskLevel;
  requiredCapabilities: string[];
  limitations: string[];
}

export interface McpCatalogAdapterStatus {
  project_id: string;
  wave: number;
  availability: McpAvailability;
  connection_kind: McpConnectionKind;
  risk: McpRiskLevel;
  required_capabilities: string[];
  limitations: string[];
  feature_enabled: boolean;
  executable: boolean;
  connected: boolean;
  session_id: string | null;
  allowed_settings: string[];
  credential_slots: string[];
  setting_fields: McpSettingField[];
  credential_fields: McpCredentialField[];
  configured: boolean;
  configured_settings: string[];
  configured_credential_slots: string[];
  configuration_values: Record<string, string | number | boolean>;
  credential_bindings: Record<string, string>;
  workspace_id?: string | null;
  credential_verification: McpCredentialVerification;
  adapter_version: string;
  runtime_image: string;
  network_policy: string;
  filesystem_policy: string;
  resource_limits: Record<string, string>;
  workspace_policy: McpWorkspacePolicy | null;
  database_policy: McpDatabasePolicy | null;
  saas_policy?: McpSaasPolicy | null;
  browser_policy?: McpBrowserPolicy | null;
  stateful_saas_gate_enabled?: boolean;
  account_status?: McpSaasAccountStatus;
  preflight_status: McpDatabasePreflightStatus;
  tool_policies: Record<
    string,
    {
      read_only: boolean;
      requires_approval: boolean;
      sensitive: boolean;
      terminal: boolean;
      effect: McpToolEffect;
    }
  >;
}

export const mcpAvailabilityLabels: Record<McpAvailability, string> = {
  planned: "已排期、待适配",
  adapting: "适配中",
  ready: "生产级可用",
  blocked: "适配受阻",
};

export const mcpConnectionKindLabels: Record<McpConnectionKind, string> = {
  "local-stdio": "本地 stdio",
  "sandboxed-stdio": "隔离 stdio",
  "remote-mcp": "远程 MCP",
  "desktop-bridge": "桌面桥接",
};

export const mcpRiskLabels: Record<McpRiskLevel, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  critical: "关键风险",
};

export const mcpCapabilityLabels: Record<string, string> = {
  "existing-node-stdio-runtime": "现有 Node stdio 运行时",
  "isolated-python-runtime": "独立 Python 沙箱",
  "resource-limits": "CPU、内存、时限与输出限制",
  "public-remote-policy": "公共远程访问策略",
  "ssrf-protection": "SSRF 与 DNS 重绑定防护",
  "dns-pinning": "DNS 解析校验与连接地址固定",
  "redirect-response-limits": "重定向与响应大小限制",
  "schema-drift-recovery": "上游工具契约漂移恢复",
  "scoped-filesystem": "受控目录授权",
  "artifact-cleanup": "产物清理",
  "path-symlink-protection": "路径穿越与符号链接防护",
  "encrypted-credential-binding": "加密凭据绑定",
  "credential-revocation-check": "凭据轮换与撤销即时失效",
  "fixed-egress-policy": "固定服务出口域名",
  "read-only-tool-policy": "只读工具策略",
  "database-read-only-policy": "数据库只读策略",
  "database-target-validation": "数据库目标校验",
  "tenant-scoped-credential-binding": "租户级加密凭据绑定",
  "structured-database-configuration": "结构化数据库连接字段",
  "native-read-only-mode": "数据库原生只读模式",
  "query-row-timeout-limits": "查询行数与超时硬限制",
  "database-preflight": "连接时数据源安全预检",
  "fixed-saas-contract": "固定 SaaS 工具契约",
  "tenant-owner-scoped-account-binding": "单租户账号范围绑定",
  "remote-resource-approval": "远程资源写入审批",
  "idempotent-write-ledger": "幂等写入账本",
  "provider-rate-limits": "上游限流护栏",
  "maintained-upstream-contract": "持续维护的上游工具契约",
  "tenant-isolated-state": "租户隔离的持久状态",
  "query-limits": "查询超时与结果限制",
  "mutating-tool-approval": "修改操作审批",
  "account-unbinding": "账号解绑",
  "ephemeral-browser": "临时浏览器会话",
  "browser-domain-policy": "浏览目标域策略",
  "browser-session-approval": "浏览器操作逐次审批",
  "browser-artifact-cleanup": "截图产物下载与清理",
  "ephemeral-code-sandbox": "一次性代码沙箱",
  "process-resource-limits": "进程资源限制",
  "cost-guardrails": "费用与资源护栏",
  "terminal-action-approval": "终止性操作审批",
  "oauth-pkce": "OAuth PKCE",
  "oauth-revocation": "授权撤销与解绑",
  "scope-review": "最小 Scope 审核",
  "versioned-desktop-bridge": "版本化桌面桥接",
  "per-app-consent": "逐应用授权",
};

export const mcpIsolationLabels: Record<string, string> = {
  disabled: "完全断网",
  "read-only-empty-workspace": "空白只读工作区",
  "validated-public-https:user-supplied-host": "用户指定公网 HTTPS；逐跳校验 DNS 与重定向",
  "allowlist:quickchart.io": "仅允许 quickchart.io",
  "allowlist:www.airbnb.com,photon.komoot.io,nominatim.openstreetmap.org":
    "仅允许 Airbnb、Photon 与 Nominatim",
  "allowlist:nominatim.openstreetmap.org,router.project-osrm.org":
    "仅允许 Nominatim 与公共 OSRM",
  "blocked:authentication-required": "已阻断：上游要求账号授权",
  "blocked:upstream-schema-drift": "已阻断：上游公开数据契约漂移",
  "sealed-input-read-only,persistent-memory-write,artifact-write":
    "封存输入只读；仅持久记忆和产物目录可写",
  "sealed-input-read-only,artifact-write": "封存输入只读；仅产物目录可写",
  "blocked:arbitrary-code-execution": "已阻断：需要任意代码执行隔离",
  "blocked:no-runtime": "已阻断：不启动运行时",
  "blocked:local-build-execution": "已阻断：可能执行本地构建链",
  "database-read-only-remote": "远程数据库仅允许受控只读连接",
  "sealed-database-read-only": "封存数据库文件只读；不接入云端数据源",
  "blocked:archived-upstream": "已阻断：上游实现已经归档",
  "blocked:stateful-memory-runtime": "已阻断：需要状态化记忆运行时与写入隔离",
  "database-egress:validated-host,admin-private-allowlist":
    "数据库目标逐次校验；私网目标仅允许服务端管理员白名单",
  "sealed-database-input-read-only,no-artifact-write":
    "封存数据库输入只读；不允许写入原文件或生成旁路产物",
  "allowlist:api.supabase.com,supabase.com": "仅允许 Supabase 官方 API 域名",
  "blocked:no-production-runtime": "已阻断：没有生产级运行时",
  "browser-egress:validated-public-http-https":
    "仅允许 DNS 固定后的公网 HTTP/HTTPS 目标（80/443 端口）；跨 origin 请求与重定向拒绝",
  "browser-profile:ephemeral-no-login-state":
    "临时匿名浏览器配置；不保存 Cookie、Storage 或登录状态",
  "browser-artifacts:screenshot-only": "仅允许生成受控截图产物",
  "blocked:unmaintained-browser-runtime": "已阻断：上游浏览器运行时已归档",
  "blocked:license-runtime-contract": "已阻断：许可证与运行时契约尚未厘清",
};

export function formatMcpCapability(capability: string) {
  return mcpCapabilityLabels[capability] ?? capability;
}

export function formatMcpIsolation(value: string) {
  return mcpIsolationLabels[value] ?? value;
}

const localStdioIds = [
  "context7",
  "filesystem-mcp",
  "youtube-transcript-mcp",
  "memory-mcp",
  "12306-mcp",
  "sequential-thinking-mcp",
  "everything-mcp",
] as const;

export const mcpAdaptationWaves: Record<number, readonly string[]> = {
  1: ["calculator-mcp", "time-mcp", "vegalite-mcp"],
  2: ["bibigpt-mcp", "fetch-mcp", "quickchart-mcp", "airbnb-mcp", "geowire-mcp"],
  3: ["basic-memory-mcp", "excel-mcp-server", "git-mcp", "manim-mcp", "markitdown-mcp"],
  4: [
    "agentql-mcp", "brave-search-mcp", "exa-mcp", "firecrawl-mcp",
    "perplexity-mcp", "tavily-mcp", "axiom-mcp", "figma-context-mcp",
    "google-maps-mcp", "grafana-mcp", "graphlit-mcp", "kagi-mcp",
    "pinecone-assistant-mcp", "shodan-mcp", "snyk-mcp", "virustotal-mcp",
  ],
  5: [
    "dbhub", "postgres-mcp", "mongodb-mcp", "clickhouse-mcp", "cognee-mcp",
    "graphiti-mcp", "hindsight-mcp", "redis-mcp", "sqlite-mcp", "duckdb-mcp",
    "supabase-mcp",
  ],
  6: ["airtable-mcp", "asana-mcp", "gitlab-mcp", "mcp-cn-commerce", "notion-mcp-server", "mem0-mcp"],
  7: ["chrome-devtools-mcp", "playwright-mcp", "puppeteer-mcp", "selenium-mcp"],
  8: ["mcp-run-python", "python-interpreter"],
  9: [
    "apify-mcp", "bright-data-mcp", "browserbase-mcp", "e2b-mcp", "stripe-mcp",
    "terraform-mcp", "aiven-mcp", "alpaca-mcp", "aws-kb-mcp", "elevenlabs-mcp",
    "minimax-mcp", "s3-mcp", "kubernetes-mcp", "semgrep-mcp",
  ],
  10: [
    "gmail-mcp", "atlassian-mcp", "google-calendar-mcp", "google-drive-mcp",
    "microsoft-365-mcp", "onedrive-mcp", "sentry-mcp", "azure-mcp", "box-mcp",
    "cloudflare-mcp", "github-mcp-server", "linear-mcp", "neon-mcp", "slack-mcp",
  ],
  11: [
    "xiaohongshu-mcp", "ableton-mcp", "binary-ninja-mcp", "blender-mcp",
    "ghidra-mcp", "jetbrains-mcp", "chatcrystal", "obsidian-mcp", "opentabs",
    "zotero-mcp", "docker-mcp", "mobile-mcp", "xcodebuild-mcp",
  ],
};

const waveMetadata: Record<
  number,
  Omit<McpAdaptationRecord, "wave" | "availability">
> = {
  1: {
    connectionKind: "sandboxed-stdio",
    risk: "low",
    requiredCapabilities: ["独立 Python 运行时", "资源上限"],
    limitations: [
      "已使用断网、非 root、只读文件系统的 Python 沙箱；只开放固定工具契约。",
      "单次调用最多 10 秒，返回最多 128 KiB，超限会被终止或拒绝。",
    ],
  },
  2: {
    connectionKind: "sandboxed-stdio",
    risk: "medium",
    requiredCapabilities: [
      "公共远程访问策略",
      "SSRF 与 DNS 重绑定防护",
      "DNS 连接地址固定",
      "重定向与响应大小限制",
    ],
    limitations: [
      "公网适配器在独立非 root、只读 sidecar 中运行；不接受浏览器提交命令、端点、Header 或环境变量。",
      "每次请求与重定向都会重新校验公网 HTTPS 目标，固定超时、响应和工具输出上限。",
    ],
  },
  3: {
    connectionKind: "sandboxed-stdio",
    risk: "medium",
    requiredCapabilities: ["目录范围授权", "产物清理"],
    limitations: [
      "使用受控上传工作区；封存后输入只读，禁止提交宿主路径、URL、环境变量或工作目录。",
      "产物进入独立可清理目录；持久写入需要一次性确认。",
    ],
  },
  4: {
    connectionKind: "sandboxed-stdio",
    risk: "medium",
    requiredCapabilities: ["加密凭据绑定", "固定出口域名", "只读工具策略"],
    limitations: [
      "仅使用当前 MCP 卡片内按项目和槽位隔离的加密凭据；目录配置不接收明文 Token、Header、命令、环境变量或 MCP URL。",
      "工具发现与调用均由 sidecar 只读白名单过滤；凭据轮换或撤销会强制断开已有会话。",
    ],
  },
  5: {
    connectionKind: "sandboxed-stdio",
    risk: "high",
    requiredCapabilities: [
      "数据库只读策略",
      "加密凭据绑定",
      "数据库目标校验",
      "查询超时与结果限制",
    ],
    limitations: [
      "浏览器只提交受控的主机、端口、库名、TLS 和用户名字段；不接受 DSN、URI、命令、环境变量或宿主路径。",
      "首轮仅发现并调用只读工具，写入、删除、迁移、管理和任意命令能力全部关闭。",
    ],
  },
  6: {
    connectionKind: "sandboxed-stdio",
    risk: "high",
    requiredCapabilities: [
      "fixed-saas-contract",
      "tenant-owner-scoped-account-binding",
      "remote-resource-approval",
      "idempotent-write-ledger",
      "provider-rate-limits",
      "account-unbinding",
      "schema-drift-recovery",
    ],
    limitations: [
      "账号凭据与资源范围只在当前卡片配置；服务端固定上游主机，不接受任意 URL、Header、命令或环境变量。",
      "写入先返回目标与影响预览，再以一次性审批和幂等键执行；限流或结果未知时不会自动重试。",
    ],
  },
  7: {
    connectionKind: "sandboxed-stdio",
    risk: "high",
    requiredCapabilities: [
      "ephemeral-browser",
      "browser-domain-policy",
      "browser-session-approval",
      "browser-artifact-cleanup",
    ],
    limitations: [
      "每次连接使用临时匿名浏览器配置，仅允许访问 DNS 固定后的公网 HTTP/HTTPS 目标（80/443 端口）；导航 URL 拒绝 Token、API Key、签名等敏感查询参数，跨 origin 请求与重定向直接拒绝。",
      "首版不提供账号凭据采集、外站登录流程或登录态保存；页面仍可能自行呈现登录界面，用户不得输入账号、密码、OTP 等认证信息。不提供网页上传/下载、剪贴板、本机文件、Cookie/Storage 导入导出与持久化、任意脚本求值或外部 CDP 工具；只允许生成受控截图产物。",
      "单 origin 门禁覆盖锁定上游的正常浏览器流量与恶意网页；若浏览器或上游进程本身被完全攻陷，独立出口仍拒绝私网和 metadata 并执行流量上限，但不能保证同一公网 IP 与证书下的虚拟主机隔离。",
    ],
  },
  8: {
    connectionKind: "sandboxed-stdio",
    risk: "critical",
    requiredCapabilities: ["一次性代码沙箱", "进程资源上限"],
    limitations: ["等待断网、无宿主挂载的一次性代码执行沙箱验证。"],
  },
  9: {
    connectionKind: "sandboxed-stdio",
    risk: "critical",
    requiredCapabilities: ["费用与资源护栏", "终止性操作审批"],
    limitations: ["等待费用上限、资源预览和终止性操作强制审批验证。"],
  },
  10: {
    connectionKind: "remote-mcp",
    risk: "high",
    requiredCapabilities: ["OAuth PKCE", "撤销与解绑", "Scope 审核"],
    limitations: ["等待 PKCE、state、最小 scope、刷新、撤销和解绑验证。"],
  },
  11: {
    connectionKind: "desktop-bridge",
    risk: "critical",
    requiredCapabilities: ["版本化桌面桥接", "逐应用同意"],
    limitations: ["等待本机桥接协议、宿主版本和逐应用授权验证。"],
  },
};

const waveFiveReadyIds = new Set([
  "dbhub",
  "mongodb-mcp",
  "clickhouse-mcp",
  "redis-mcp",
  "duckdb-mcp",
  "supabase-mcp",
]);

const waveFiveBlockedDetails: Record<string, string[]> = {
  "postgres-mcp": [
    "官方 PostgreSQL 参考实现已经归档，不再作为生产运行时部署。需要 PostgreSQL 时可使用本批受控的 DBHub 只读适配器。",
    "连接入口保持关闭；不接受数据库 URI，也不会回退到未锁定的社区包。",
  ],
  "sqlite-mcp": [
    "官方 SQLite 参考实现已经归档且包含写入能力，不满足本批生产只读门槛。",
    "连接与本地路径输入保持关闭；后续只能基于封存副本和维护中的固定适配器重新评估。",
  ],
  "cognee-mcp": [
    "Cognee 会组合 LLM、Embedding、图数据库与持久写入，不属于普通数据库只读查询。",
    "转入后续“状态化记忆”适配；在模型调用、数据保留和写入隔离通过前不开放连接。",
  ],
  "graphiti-mcp": [
    "Graphiti 需要图数据库、模型调用和时序知识写入，无法使用本批只读数据库 sidecar。",
    "转入后续“状态化记忆”适配；当前不提供凭据、连接或初始化入口。",
  ],
  "hindsight-mcp": [
    "Hindsight 的 retain、recall 与 reflect 依赖持久记忆写入及模型能力，不属于只读数据库工具。",
    "转入后续“状态化记忆”适配；数据生命周期与写入审批完成前保持阻断。",
  ],
};

const waveFiveReadyDetails: Record<string, string[]> = {
  dbhub: [
    "固定 DBHub 1.2.0，仅开放 PostgreSQL、MySQL 与 MariaDB 的只读查询；关闭 SQLite、SSH 隧道和自定义工具。",
    "连接参数由服务端生成，查询强制超时和行数上限，浏览器不能提交 DSN 或 URI。",
  ],
  "mongodb-mcp": [
    "固定 MongoDB MCP 2.0.0 并启用只读模式，仅开放集合、索引、结构和受控查询能力。",
    "Atlas 管理、创建、更新和删除工具全部关闭；连接时自动执行目标校验和代表性只读预检。",
  ],
  "clickhouse-mcp": [
    "固定 ClickHouse MCP 0.4.1，服务端强制只读会话、查询超时与结果上限。",
    "写访问、DROP、TRUNCATE 与 chDB 全部关闭；连接时自动验证目标与代表性只读能力。",
  ],
  "redis-mcp": [
    "固定 Redis MCP 0.5.1，仅允许只读 ACL 与工具白名单覆盖的键空间读取和检索。",
    "任意命令、写键、删除、过期时间修改和管理操作均不可发现或调用。",
  ],
  "duckdb-mcp": [
    "固定 DuckDB MCP 1.0.7，仅打开封存工作区中的本地 .duckdb 文件，输入始终只读。",
    "运行环境默认断网，不开放 MotherDuck、Token、宿主路径或任意文件 URI。",
  ],
  "supabase-mcp": [
    "固定 Supabase MCP 0.9.0，仅支持项目范围内的 stdio、PAT 与只读数据库能力。",
    "远程 OAuth、组织管理、迁移和写入工具继续关闭，留待第 10 批授权适配。",
  ],
};

const waveFiveSingleTenantLimitation =
  "当前仅支持部署时固定 tenant/owner 的单租户本地实例；多用户共享部署未开放。";

const waveSixReadyIds = new Set([
  "airtable-mcp",
  "asana-mcp",
  "gitlab-mcp",
  "notion-mcp-server",
]);

const waveSixReadyDetails: Record<string, string[]> = {
  "airtable-mcp": [
    "仅绑定一个受控 Base ID；读工具直接执行，记录创建和更新必须先预览目标与影响并逐次确认。",
    "Personal Access Token 在当前卡片加密保存；上游主机、凭据注入位置和资源范围由服务端固定。",
  ],
  "asana-mcp": [
    "仅绑定一个工作区与一个项目；查询可直接执行，任务和协作信息修改必须逐次预览并确认。",
    "Personal Access Token 在当前卡片加密保存；不会跳转 OAuth，也不会接收任意 API URL。",
  ],
  "gitlab-mcp": [
    "首批仅连接 gitlab.com，并绑定一个项目 ID；自建 GitLab 地址和任意主机输入保持关闭。",
    "Personal Access Token 在当前卡片加密保存；Issue、合并请求等写入必须逐次预览并确认。",
  ],
  "notion-mcp-server": [
    "仅绑定一个固定 Data Source ID；Integration 必须在 Notion 中被显式共享到该 Data Source。",
    "首批写入只允许在该 Data Source 内新建页面或更新页面属性，并且必须逐次预览目标与影响后确认。",
  ],
};

const waveSixBlockedDetails: Record<string, string[]> = {
  "mcp-cn-commerce": [
    "上游覆盖多个电商平台，依赖各平台 OAuth、商家授权和短期 Token，无法在本批固定为单一可核验账号契约。",
    "已阻断配置、凭据、连接和工具入口；等待第 10 批账号授权、刷新与撤销能力完成后再评估。",
  ],
  "mem0-mcp": [
    "已归档的本地实现无法继续锁定生产版本；当前远程实现尚不能满足固定工具 schema 与租户 Scope 隔离要求。",
    "已阻断配置、凭据、连接和写入入口；等待状态化记忆的数据保留、删除与租户隔离契约完成后再评估。",
  ],
};

const waveSixSingleTenantLimitation =
  "当前仅支持部署时固定 tenant/owner 的单租户实例；多租户共享部署保持关闭。";

const waveSevenReadyIds = new Set([
  "chrome-devtools-mcp",
  "playwright-mcp",
]);

const waveSevenReadyDetails: Record<string, string[]> = {
  "chrome-devtools-mcp": [
    "固定 Chrome DevTools MCP 1.6.0；只开放会话状态、受控导航、页面快照、点击、填写和截图产物。",
    "性能分析、Lighthouse、堆快照、文件上传、键盘透传、任意脚本求值工具、扩展和外部 CDP 全部关闭。",
  ],
  "playwright-mcp": [
    "固定 Playwright MCP 0.0.79；只开放会话状态、受控导航、结构化页面快照、点击、填写和截图产物，不接受浏览器启动参数或持久配置目录。",
    "任意代码、文件上传、网页下载、Cookie/Storage 导入导出与持久化、PDF、视觉定位、网络路由、远程 CDP 和共享会话全部关闭。",
  ],
};

const waveSevenBlockedDetails: Record<string, string[]> = {
  "puppeteer-mcp": [
    "上游官方参考实现已经归档，包含任意启动参数和页面脚本执行等高风险能力，无法锁定为持续维护的生产契约。",
    "连接、安装和浏览器进程入口保持关闭；不会回退到归档包、任意 Puppeteer 启动参数或外部 CDP。",
  ],
  "selenium-mcp": [
    "上游仓库与发布包的许可证声明不一致，容器、Node 版本和浏览器驱动契约也存在漂移，当前无法形成可复现镜像。",
    "连接、安装和 WebDriver 入口保持关闭；待许可证、进程清理和工具白名单形成可审计契约后再评估。",
  ],
};

function buildAdaptationPlan() {
  const records: Record<string, McpAdaptationRecord> = {};
  for (const projectId of localStdioIds) {
    records[projectId] = {
      wave: 0,
      availability: "ready",
      connectionKind: "local-stdio",
      risk: projectId === "filesystem-mcp" || projectId === "memory-mcp" ? "medium" : "low",
      requiredCapabilities: ["现有 Node stdio 运行时"],
      limitations: ["沿用现有本地 stdio 行为；批次 0 不扩大权限范围。"],
    };
  }
  for (const [rawWave, projectIds] of Object.entries(mcpAdaptationWaves)) {
    const wave = Number(rawWave);
    const metadata = waveMetadata[wave];
    for (const projectId of projectIds) {
      if (records[projectId]) throw new Error(`重复的 MCP 适配计划：${projectId}`);
      records[projectId] = {
        wave,
        availability:
          projectId === "bibigpt-mcp" || projectId === "airbnb-mcp" || projectId === "manim-mcp" || projectId === "snyk-mcp"
            ? "blocked"
            : wave === 5
              ? waveFiveReadyIds.has(projectId)
                ? "ready"
                : "blocked"
            : wave === 6
              ? waveSixReadyIds.has(projectId)
                ? "ready"
                : "blocked"
            : wave === 7
              ? waveSevenReadyIds.has(projectId)
                ? "ready"
                : "blocked"
            : wave <= 4
              ? "ready"
              : "planned",
        connectionKind:
          projectId === "bibigpt-mcp"
            ? "remote-mcp"
            : metadata.connectionKind,
        risk:
          projectId === "manim-mcp" ||
          projectId === "snyk-mcp" ||
          projectId === "cognee-mcp" ||
          projectId === "graphiti-mcp" ||
          projectId === "hindsight-mcp"
            ? "critical"
            : metadata.risk,
        requiredCapabilities:
          projectId === "bibigpt-mcp"
            ? ["OAuth PKCE", "授权撤销与解绑", "最小 Scope 审核"]
            : projectId === "airbnb-mcp"
              ? ["公共远程访问策略", "SSRF 防护", "上游工具契约漂移恢复"]
            : projectId === "manim-mcp"
              ? ["一次性代码沙箱", "进程资源上限"]
            : projectId === "snyk-mcp"
              ? ["一次性代码沙箱", "受控文件授权", "终止性操作审批"]
            : projectId === "postgres-mcp" || projectId === "sqlite-mcp"
              ? ["维护中的固定上游契约", "数据库只读策略", "查询超时与结果限制"]
            : projectId === "cognee-mcp" || projectId === "graphiti-mcp" || projectId === "hindsight-mcp"
              ? ["状态化记忆运行时", "模型与向量服务隔离", "持久写入审批与数据保留"]
            : [...metadata.requiredCapabilities],
        limitations:
          projectId === "bibigpt-mcp"
            ? [
                "上游远程 MCP 现在要求 OAuth 2.1 或 API Key 才能执行工具，不满足本批无凭据门槛。",
                "已转入第 10 批 OAuth 适配；在服务端授权、撤销与解绑通过前不展示外站登录入口。",
              ]
            : projectId === "airbnb-mcp"
              ? [
                  "Airbnb 0.3.0 当前公开搜索页缺少上游适配器固定依赖的数据节点，代表调用触发 schema 漂移阻断。",
                  "在上游恢复稳定公开契约并重新通过 robots.txt 与 smoke 前，不提供连接或绕过入口。",
                ]
            : projectId === "manim-mcp"
              ? [
                  "上游 Manim MCP 会执行用户提供的任意 Python 场景代码，不属于普通文件处理能力。",
                  "保留第 3 批编号，但连接入口已阻断；等待第 8 批一次性代码执行容器完成后再适配。",
                ]
            : projectId === "snyk-mcp"
              ? [
                  "Snyk MCP 会读取本地项目，并可能启动 Gradle、Maven 等构建链，超出本批 Token 只读远程检索边界。",
                  "保留第 4 批编号但关闭连接、安装与外站登录；等待第 8 批一次性代码执行隔离后再适配。",
                ]
            : waveFiveBlockedDetails[projectId]
              ? [...waveFiveBlockedDetails[projectId]]
            : waveFiveReadyDetails[projectId]
              ? [
                  ...waveFiveReadyDetails[projectId],
                  waveFiveSingleTenantLimitation,
                ]
            : waveSixBlockedDetails[projectId]
              ? [...waveSixBlockedDetails[projectId]]
            : waveSixReadyDetails[projectId]
              ? [
                  ...waveSixReadyDetails[projectId],
                  waveSixSingleTenantLimitation,
                ]
            : waveSevenBlockedDetails[projectId]
              ? [...waveSevenBlockedDetails[projectId]]
            : waveSevenReadyDetails[projectId]
              ? [
                  ...waveSevenReadyDetails[projectId],
                  ...metadata.limitations,
                ]
            : [...metadata.limitations],
      };
    }
  }
  if (Object.keys(records).length !== 100) {
    throw new Error(`MCP 适配计划必须包含 100 个条目，当前为 ${Object.keys(records).length}`);
  }
  return records;
}

export const mcpAdaptationPlan = buildAdaptationPlan();

export function getMcpAdaptation(projectId: string): McpAdaptationRecord {
  const record = mcpAdaptationPlan[projectId];
  if (!record) throw new Error(`MCP 条目缺少适配计划：${projectId}`);
  return record;
}

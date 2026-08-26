import { mcpCatalogExpansionV2 } from "./mcpCatalogExpansionV2.generated";
import { mcpCatalogExpansionV3 } from "./mcpCatalogExpansionV3.generated";

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
  remote_auth_mode?:
    | ""
    | "static_bearer"
    | "static_header"
    | "oauth_authorization_code_pkce";
  remote_review_capable?: boolean;
  remote_review_credential_ready?: boolean;
  remote_review_enabled?: boolean;
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
  "allowlist:registry.terraform.io": "仅允许匿名访问 registry.terraform.io",
  "blocked:no-production-runtime": "已阻断：没有生产级运行时",
  "browser-egress:validated-public-http-https":
    "仅允许 DNS 固定后的公网 HTTP/HTTPS 目标（80/443 端口）；跨 origin 请求与重定向拒绝",
  "browser-profile:ephemeral-no-login-state":
    "临时匿名浏览器配置；不保存 Cookie、Storage 或登录状态",
  "browser-artifacts:screenshot-only": "仅允许生成受控截图产物",
  "blocked:unmaintained-browser-runtime": "已阻断：上游浏览器运行时已归档",
  "blocked:license-runtime-contract": "已阻断：许可证与运行时契约尚未厘清",
  "blocked:credential-egress-not-approved": "已阻断：未批准账号凭据出站",
  "blocked:unreconciled-provider-cost": "已阻断：供应商费用尚无逐项目对账与硬上限",
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
    "tako-mcp",
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
    limitations: ["两个上游均未通过安全与发布物门槛，当前不提供代码执行运行时。"],
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
    requiredCapabilities: ["版本化桌面桥接", "宿主实例证明", "会话主体绑定", "逐应用同意", "终止性操作审批", "桥接撤销"],
    limitations: ["13 个上游均依赖真实本机宿主或账号状态；当前没有可信桌面配对、实例证明和逐应用授权，因此全部阻断。"],
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

const waveEightBlockedDetails: Record<string, string[]> = {
  "mcp-run-python": [
    "固定核验上游 0.0.22。维护方已归档项目，并明确说明 Pyodide 代码可执行任意 JavaScript、污染后续调用、访问运行时文件且无法可靠限制内存。",
    "连接、依赖安装、Deno/Pyodide 和代码提交入口全部关闭；不会以实验性的 Monty 或自研执行器冒充该上游。",
  ],
  "python-interpreter": [
    "固定核验 PyPI 1.2.3。默认 inline 模式会在 MCP Server 进程内执行代码并保留会话，同时开放 pip 安装、文件读写、环境选择和最长 300 秒的子进程执行。",
    "发布 wheel 声明 MIT classifier，但携带的 LICENSE 文件为空；许可证正文、一次性容器和固定 subprocess-only 契约完成前不开放任何执行或文件入口。",
  ],
};

const waveNineReadyIds = new Set(["terraform-mcp"]);

const waveNineReadyDetails: Record<string, string[]> = {
  "terraform-mcp": [
    "固定为 Terraform MCP 1.2.0 公共 Registry 只读兼容契约，仅开放 Provider 版本、能力、文档与 Module 搜索、详情、最新版本六项工具。",
    "独立 mcp-registry sidecar 仅连接 registry.terraform.io；不接收 Token，不读取 Terraform 工作区、状态文件、变量或本机配置。HCP/TFE、私有 Registry、plan、apply、destroy 与资源变更工具全部关闭。",
  ],
};

const waveNineBlockedDetails: Record<string, string[]> = {
  "apify-mcp": [
    "固定上游仍要求 Apify Token，Actor 运行与动态工具会产生外部费用；本批未批准向 Apify 发送账号凭据。",
    "Token、OAuth、Actor 运行、数据集与动态工具全部关闭；需单独批准凭据出站并建立逐项目费用上限后再评估。",
  ],
  "aiven-mcp": [
    "上游可收窄为只读 scope，但仍需向 Aiven 发送项目账号 Token，并可读取项目、服务、VPC 与套餐元数据。",
    "本批未批准账号凭据出站，配置、连接和工具入口全部关闭。",
  ],
  "bright-data-mcp": [
    "基础抓取和 Pro 工具均消耗供应商额度，当前没有可与账单对账的逐项目硬预算。",
    "Token、连接与抓取入口全部关闭；免费额度不替代费用护栏。",
  ],
  "browserbase-mcp": [
    "官方仓库已归档，托管浏览器会话还会持续消耗外部资源，无法锁定维护中的生产契约。",
    "连接、项目密钥与云浏览器入口全部关闭；浏览器能力使用第七批一次性本地适配器。",
  ],
  "e2b-mcp": [
    "官方 MCP 仓库已归档；云沙箱默认允许联网、任意安装与命令执行，并在存活期间计费。",
    "API Key、代码、依赖安装和云沙箱创建入口全部关闭。",
  ],
  "stripe-mcp": [
    "当前官方 MCP 已迁移到 mcp.stripe.com 的 OAuth 远程服务，支付、退款与订阅写入具有真实资金影响。",
    "转入第十批处理 OAuth scope、解绑和终止性金融操作审批；本批不开放登录或 API Key。",
  ],
  "alpaca-mcp": [
    "官方 v2 同时暴露真实交易、平仓与期权能力，市场数据订阅也可能产生费用；paper 默认值不能证明凭据属于模拟账户。",
    "API Key、行情、账户和订单工具全部关闭，等待账户类型证明、费用上限与金融终止操作审批。",
  ],
  "aws-kb-mcp": [
    "Bedrock Knowledge Bases 检索依赖 AWS 身份、区域与知识库范围，可能产生查询费用并返回企业敏感内容。",
    "AWS 凭据、Knowledge Base ID 与检索入口全部关闭，等待 SigV4 代理、资源白名单与 usage 对账。",
  ],
  "elevenlabs-mcp": [
    "语音与音效生成会消耗外部额度并产生媒体产物，部分工具还管理持久 Voice 资源。",
    "API Key、生成、克隆、播放、下载和删除入口全部关闭，等待费用与产物隔离。",
  ],
  "minimax-mcp": [
    "官方工具会发起付费语音、图像、视频、音乐与 Voice Clone 任务，并接受本地文件或 URL。",
    "API Key、生成、轮询、文件与产物入口全部关闭，等待价格锁定、异步账本与媒体隔离。",
  ],
  "s3-mcp": [
    "S3 Tables Server 包含 Namespace 与 Table 的创建、更新和删除能力，并依赖 AWS 凭据与资源级 IAM。",
    "AWS 凭据、资源 ARN 与工具入口全部关闭，等待固定只读工具集和 SigV4 资源白名单。",
  ],
  "kubernetes-mcp": [
    "即使禁用 destructive，仍需 kubeconfig/ServiceAccount、集群网络和 namespace 级 RBAC；日志与资源可能包含敏感数据。",
    "Kubeconfig、集群连接、exec、日志与资源入口全部关闭，等待只读 RBAC 实测与固定 namespace 绑定。",
  ],
  "semgrep-mcp": [
    "官方仓库已归档，扫描需要读取项目源码并启动本地 Semgrep 运行时，不能形成维护中的文件与执行契约。",
    "源码目录、Token、CLI 与扫描入口全部关闭。",
  ],
};

const waveElevenBlockedDetails: Record<string, string[]> = {
  "xiaohongshu-mcp": [
    "当前上游依赖本机 Chromium 登录、Cookie 和 QR 登录，并开放搜索、评论、收藏以及图文/视频发布；发布可读取宿主绝对文件路径。",
    "没有账号实例绑定、本地媒体授权和发布终止审批时，登录态、代理、Cookie、文件与全部工具入口保持关闭。",
  ],
  "ableton-mcp": [
    "1.3.5 需要向 Ableton Live 安装 Remote Script，并通过 localhost:9000 创建/删除轨道、编辑 Clip、加载设备和控制播放。",
    "缺少可证明宿主版本、当前 Live Set、端口归属与用户在场的签名桌面桥，不能连接真实音乐项目。",
  ],
  "binary-ninja-mcp": [
    "v1.2.1 是商业桌面宿主插件与 localhost:9009 桥接器，既读取反编译数据，也能定义类型、创建函数、重命名和删除注释。",
    "许可证席位、打开二进制、插件实例和写入目标无法绑定当前用户；只读工具冻结与修改审批完成前保持关闭。",
  ],
  "blender-mcp": [
    "1.8.0 通过 Blender 插件与 Socket 操作场景，并允许在宿主内执行任意 Python、读取/删除文件和下载外部资产。",
    "调用继承 Blender 进程的完整宿主权限，服务端 sidecar 无法约束；任意代码、插件与场景入口全部关闭。",
  ],
  "ghidra-mcp": [
    "v0.2.2+ghidra12.0.4 公开 70 个查询与修改工具，包括 patch_bytes、内存权限、结构、类型和符号修改；默认 localhost 模式没有 API Key。",
    "当前没有固定 Ghidra Program、强制桥接认证和二进制写入事务审批，端口扫描、多实例和全部工具均不接入。",
  ],
  "jetbrains-mcp": [
    "当前源码标签为 1.9.0、npm 包为 1.8.0；代理扫描 63342—63352 或接受 HOST/IDE_PORT，把项目读取和 IDE 动作转发到本机 HTTP API。",
    "没有配对 IDE、项目根、端口所有权和工具级同意时，自动发现、LAN 主机和代理入口全部关闭。",
  ],
  chatcrystal: [
    "0.5.8 会扫描 Claude Code、Cursor、Codex 等本机历史，调用可配置 LLM/Embedding 服务，并提供记忆检索与写回。",
    "编码对话可能包含源码、提示词和凭据；缺少导入清单、脱敏、费用/保留策略和桌面主体绑定时不开放。",
  ],
  "obsidian-mcp": [
    "上游已迁移为 bitbonsai/mcpvault 0.15.0，直接接收宿主 Vault 路径并开放读取、覆盖/追加、移动、标签修改和确认删除。",
    "上传工作区不等同实时 Vault，服务端也不接受宿主路径；逐 Vault 授权、读写分离和备份恢复完成前保持关闭。",
  ],
  opentabs: [
    "0.0.115 通过 Chrome 扩展复用已登录会话，并提供 100+ 动态插件、约 2000 个工具，可直接调用真实 Web API。",
    "动态插件确认不能替代固定 Schema、账号/Origin 绑定和外部写入账本；扩展、登录态与全部工具关闭。",
  ],
  "zotero-mcp": [
    "0.9.1 本地模式可读取文献、附件全文和批注；配置 Web API Key 后又可新增文献、更新笔记/批注并下载 PDF。",
    "没有签名桥确认本地 Zotero 实例和 Library，也未冻结仅本地只读工具与附件范围，因此保持关闭。",
  ],
  "docker-mcp": [
    "v0.43.3 是管理动态 MCP 目录、容器生命周期、Secrets 与 OAuth 的 Docker CLI 插件，不是单一固定只读工具。",
    "模镜服务端禁止挂载 Docker Socket，也不允许用户选择镜像、Server、Secret 或网络策略，因此不能接入。",
  ],
  "mobile-mcp": [
    "1.0.2 调用 adb、xcrun simctl、WebDriverAgent 或真实 USB 设备，可安装/卸载应用、输入、打开 URL、录屏并读取崩溃报告。",
    "没有设备所有权、测试专用证明、应用 allowlist 和安装/卸载终止审批时，SDK、USB、端口与设备工具全部关闭。",
  ],
  "xcodebuild-mcp": [
    "2.7.0 需要具备 Xcode 的 macOS 宿主，公开项目发现、构建、测试、清理、安装/启动、调试、日志和 UI 自动化。",
    "当前 Windows/Docker 部署没有可验真的 macOS 主机、工程范围和 Simulator/Device，全部宿主入口关闭。",
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
            : wave === 8
              ? "blocked"
            : wave === 9
              ? waveNineReadyIds.has(projectId)
                ? "ready"
                : "blocked"
            : wave === 11
              ? "blocked"
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
            : projectId === "terraform-mcp"
              ? "medium"
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
            : projectId === "terraform-mcp"
              ? ["固定服务出口域名", "只读工具策略", "Schema 漂移恢复", "进程资源限制"]
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
            : waveEightBlockedDetails[projectId]
              ? [...waveEightBlockedDetails[projectId]]
            : waveNineBlockedDetails[projectId]
              ? [...waveNineBlockedDetails[projectId]]
            : waveNineReadyDetails[projectId]
              ? [...waveNineReadyDetails[projectId]]
            : waveElevenBlockedDetails[projectId]
              ? [...waveElevenBlockedDetails[projectId]]
            : [...metadata.limitations],
      };
    }
  }
  for (const project of mcpCatalogExpansionV2) {
    if (records[project.id]) throw new Error(`重复的 MCP 适配计划：${project.id}`);
    records[project.id] = {
      ...project.adaptation,
      requiredCapabilities: [...project.adaptation.requiredCapabilities],
      limitations: [...project.adaptation.limitations],
    };
  }
  for (const project of mcpCatalogExpansionV3) {
    if (records[project.id]) throw new Error(`重复的 MCP 适配计划：${project.id}`);
    records[project.id] = {
      ...project.adaptation,
      requiredCapabilities: [...project.adaptation.requiredCapabilities],
      limitations: [...project.adaptation.limitations],
    };
  }
  if (Object.keys(records).length !== 301) {
    throw new Error(`MCP 适配计划必须包含 301 个条目，当前为 ${Object.keys(records).length}`);
  }
  return records;
}

export const mcpAdaptationPlan = buildAdaptationPlan();

export function getMcpAdaptation(projectId: string): McpAdaptationRecord {
  const record = mcpAdaptationPlan[projectId];
  if (!record) throw new Error(`MCP 条目缺少适配计划：${projectId}`);
  return record;
}

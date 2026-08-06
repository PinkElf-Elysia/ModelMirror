export type McpAvailability = "planned" | "adapting" | "ready" | "blocked";
export type McpConnectionKind =
  | "local-stdio"
  | "sandboxed-stdio"
  | "remote-mcp"
  | "desktop-bridge";
export type McpRiskLevel = "low" | "medium" | "high" | "critical";

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
  adapter_version: string;
  runtime_image: string;
  network_policy: string;
  filesystem_policy: string;
  resource_limits: Record<string, string>;
  tool_policies: Record<
    string,
    {
      read_only: boolean;
      requires_approval: boolean;
      sensitive: boolean;
      terminal: boolean;
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
  "encrypted-credential-binding": "加密凭据绑定",
  "read-only-tool-policy": "只读工具策略",
  "database-read-only-policy": "数据库只读策略",
  "query-limits": "查询超时与结果限制",
  "mutating-tool-approval": "修改操作审批",
  "account-unbinding": "账号解绑",
  "ephemeral-browser": "临时浏览器会话",
  "browser-domain-policy": "浏览目标域策略",
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
    limitations: ["等待目录授权、路径越界防护和产物清理验证。"],
  },
  4: {
    connectionKind: "remote-mcp",
    risk: "medium",
    requiredCapabilities: ["加密凭据绑定", "只读工具策略"],
    limitations: ["等待固定凭据槽、出口域名和只读工具清单验证。"],
  },
  5: {
    connectionKind: "sandboxed-stdio",
    risk: "high",
    requiredCapabilities: ["数据库只读策略", "查询限制"],
    limitations: ["等待只读账号、TLS、查询超时和结果行数限制验证。"],
  },
  6: {
    connectionKind: "remote-mcp",
    risk: "high",
    requiredCapabilities: ["修改操作审批", "账号解绑"],
    limitations: ["等待修改操作预览、审批、幂等和账号解绑验证。"],
  },
  7: {
    connectionKind: "sandboxed-stdio",
    risk: "high",
    requiredCapabilities: ["临时浏览器", "浏览域策略"],
    limitations: ["等待临时浏览器、目标域及上传下载边界验证。"],
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
          projectId === "bibigpt-mcp" || projectId === "airbnb-mcp"
            ? "blocked"
            : wave <= 2
              ? "ready"
              : "planned",
        connectionKind:
          projectId === "bibigpt-mcp"
            ? "remote-mcp"
            : metadata.connectionKind,
        risk: metadata.risk,
        requiredCapabilities:
          projectId === "bibigpt-mcp"
            ? ["OAuth PKCE", "授权撤销与解绑", "最小 Scope 审核"]
            : projectId === "airbnb-mcp"
              ? ["公共远程访问策略", "SSRF 防护", "上游工具契约漂移恢复"]
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

import {
  getMcpAdaptation,
  type McpAvailability,
  type McpConnectionKind,
  type McpRiskLevel,
} from "./mcpAdaptationPlan";
import { mcpCatalogExpansionV2 } from "./mcpCatalogExpansionV2.generated";

export const mcpCategories = [
  "浏览器与网页",
  "开发与代码",
  "版本控制",
  "数据库",
  "文件与存储",
  "数据分析",
  "效率与协作",
  "多媒体",
  "电商经营",
  "知识与记忆",
  "安全分析",
  "地理与出行",
  "通用工具",
  "云平台与运维",
  "搜索与研究",
  "通讯与协作",
  "金融与市场",
  "社交与内容",
] as const;

export type McpCategory = (typeof mcpCategories)[number];
export type McpInstallMode = "one-click" | "manual";
export type McpCatalogSourceId = "awesome-mcp-zh" | "awesome-mcp-servers";
export type McpRequirement =
  | "oauth"
  | "token"
  | "external-runtime"
  | "desktop-host"
  | "remote-transport"
  | "database-credentials"
  | "sealed-workspace"
  | "account-binding"
  | "system-permission";

export const mcpRequirementLabels: Record<McpRequirement, string> = {
  oauth: "需要 OAuth",
  token: "需要 Token / API Key",
  "external-runtime": "需要额外运行时",
  "desktop-host": "需要桌面宿主",
  "remote-transport": "需要远程传输适配",
  "database-credentials": "需要数据库凭证",
  "sealed-workspace": "需要封存工作区",
  "account-binding": "需要账号绑定",
  "system-permission": "需要系统权限",
};

export interface McpProject {
  id: string;
  name: string;
  repoName: string;
  repoUrl: string;
  category: McpCategory;
  description: string;
  readmeSummary: string;
  stars: number;
  language: string;
  verifiedAt: string;
  installMode: McpInstallMode;
  installCommand: string;
  installNote: string;
  tags: string[];
  availability: McpAvailability;
  connectionKind: McpConnectionKind;
  adaptationWave: number;
  risk: McpRiskLevel;
  requiredCapabilities: string[];
  adaptationLimitations: string[];
  requirements: McpRequirement[];
  configGuide: string[];
  usageExamples: string[];
  sources: McpCatalogSourceId[];
}

interface McpProjectSeed extends Omit<
  McpProject,
  | "availability"
  | "connectionKind"
  | "adaptationWave"
  | "risk"
  | "requiredCapabilities"
  | "adaptationLimitations"
  | "requirements"
  | "configGuide"
  | "usageExamples"
  | "sources"
> {
  requirements?: McpRequirement[];
  configGuide?: string[];
  usageExamples?: string[];
  sources?: McpCatalogSourceId[];
}

export const mcpCatalogSources = [
  {
    id: "awesome-mcp-zh",
    name: "Awesome-MCP-ZH",
    url: "https://github.com/yzfly/Awesome-MCP-ZH",
    license: "MIT",
    verifiedAt: "2026-08-02",
  },
  {
    id: "awesome-mcp-servers",
    name: "awesome-mcp-servers",
    url: "https://github.com/punkpeye/awesome-mcp-servers",
    license: "MIT",
    verifiedAt: "2026-08-02",
  },
] as const satisfies ReadonlyArray<{
  id: McpCatalogSourceId;
  name: string;
  url: string;
  license: string;
  verifiedAt: string;
}>;

const originalMcpProjectSeeds: McpProjectSeed[] = [
  {
    id: "playwright-mcp",
    name: "Playwright MCP",
    repoName: "microsoft/playwright-mcp",
    repoUrl: "https://github.com/microsoft/playwright-mcp",
    category: "浏览器与网页",
    description:
      "在一次性匿名 Chromium 会话中读取页面快照、访问公网网页并执行受控点击与填写，适合前端验收和公开页面检查。",
    readmeSummary:
      "固定微软 Playwright MCP 0.0.79，并由模镜裁剪为会话状态、受控导航、结构化快照、点击、填写和截图产物。",
    stars: 33615,
    language: "TypeScript",
    verifiedAt: "2026-08-07",
    installMode: "one-click",
    installCommand:
      '{\n  "mcpServers": {\n    "playwright": {\n      "command": "npx",\n      "args": ["@playwright/mcp@latest"]\n    }\n  }\n}',
    installNote: "模镜使用预装、锁定版本的浏览器 sidecar，不在连接时下载上游代码或浏览器。",
    tags: ["微软官方", "临时浏览器", "结构化快照"],
    usageExamples: ["检查公开页面的结构和关键文案", "在逐次确认后完成导航、点击或表单填写并留存截图"],
  },
  {
    id: "chrome-devtools-mcp",
    name: "Chrome DevTools MCP",
    repoName: "ChromeDevTools/chrome-devtools-mcp",
    repoUrl: "https://github.com/ChromeDevTools/chrome-devtools-mcp",
    category: "浏览器与网页",
    description:
      "在一次性匿名 Chrome 会话中检查页面结构，并执行受控导航、点击、填写与截图。",
    readmeSummary:
      "固定 Chrome DevTools MCP 1.6.0；本批仅开放页面读取、受控交互和截图，性能、任意脚本及外部 CDP 均关闭。",
    stars: 42000,
    language: "TypeScript",
    verifiedAt: "2026-08-07",
    installMode: "one-click",
    installCommand:
      '{\n  "mcpServers": {\n    "chrome-devtools": {\n      "command": "npx",\n      "args": ["-y", "chrome-devtools-mcp@latest", "--headless"]\n    }\n  }\n}',
    installNote:
      "模镜使用预装、锁定版本的 Chrome for Testing 和独立 sidecar，不连接宿主浏览器或复用登录状态。",
    tags: ["Chrome 官方", "临时浏览器", "页面结构"],
    usageExamples: ["核对公开页面结构和关键文案", "在逐次确认后导航并生成可下载截图"],
  },
  {
    id: "opentabs",
    name: "OpenTabs",
    repoName: "opentabs-dev/opentabs",
    repoUrl: "https://github.com/opentabs-dev/opentabs",
    category: "浏览器与网页",
    description:
      "通过已登录的浏览器会话访问 Web 应用，并用插件连接 Slack、Discord、GitHub 等服务。",
    readmeSummary:
      "OpenTabs 0.0.115 复用真实 Chrome 登录态，并通过可安装插件暴露 100+ 服务和约 2000 个动态工具。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "请按项目 README 安装 OpenTabs Chrome 扩展、MCP Server 与所需插件。",
    installNote:
      "需要浏览器扩展和本地宿主配合，不能由模镜后端单独启动，因此保留为手动配置条目。",
    tags: ["浏览器扩展", "复用登录态", "插件生态"],
  },
  {
    id: "context7",
    name: "Context7",
    repoName: "upstash/context7",
    repoUrl: "https://github.com/upstash/context7",
    category: "开发与代码",
    description:
      "给 coding agent 拉取最新库文档和代码示例，减少过期 API 和幻觉答案。",
    readmeSummary:
      "Upstash 官方项目，会把按版本匹配的文档和代码示例直接放进提示词上下文。",
    stars: 56974,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand: "npx ctx7 setup",
    installNote:
      "官方推荐用 ctx7 CLI 配置；模镜原生连接使用其 npm stdio Server。",
    tags: ["Upstash 官方", "最新文档", "代码生成"],
  },
  {
    id: "sentry-mcp",
    name: "Sentry MCP",
    repoName: "getsentry/sentry-mcp",
    repoUrl: "https://github.com/getsentry/sentry-mcp",
    category: "开发与代码",
    description:
      "用自然语言查询 Sentry 错误、性能问题和发布信息，辅助定位线上故障根因。",
    readmeSummary:
      "Sentry 官方集成，面向错误监控、Issue 分析、Tracing 和发布排障场景。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "远程端点：https://mcp.sentry.dev/mcp",
    installNote:
      "远程 Server 需要 OAuth 授权。当前模镜连接器只管理本地 stdio 进程，请在支持远程 MCP 的宿主中配置。",
    tags: ["Sentry 官方", "可观测性", "OAuth"],
  },
  {
    id: "python-interpreter",
    name: "MCP Python Interpreter",
    repoName: "yzfly/mcp-python-interpreter",
    repoUrl: "https://github.com/yzfly/mcp-python-interpreter",
    category: "开发与代码",
    description:
      "上游提供 Python 执行、包管理、文件读写和持久会话；这些能力尚未形成可安全部署的固定沙箱契约。",
    readmeSummary:
      "PyPI 1.2.3 默认在 MCP Server 进程内执行代码，并开放 pip、文件和环境选择；发布 wheel 的 LICENSE 文件为空，因此保持阻断。",
    stars: 0,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand:
      "pip install mcp-python-interpreter\nuvx mcp-python-interpreter --dir <sandbox> --python-path <python>",
    installNote:
      "不会安装或运行该发布物；待许可证正文、一次性容器和固定 subprocess-only 契约全部通过后再评估。",
    tags: ["代码执行", "高风险", "许可证待核验"],
  },
  {
    id: "github-mcp-server",
    name: "GitHub MCP Server",
    repoName: "github/github-mcp-server",
    repoUrl: "https://github.com/github/github-mcp-server",
    category: "版本控制",
    description:
      "把仓库、Issue、PR、Actions 和代码上下文接入 AI，适合研发协作与 CI 排障。",
    readmeSummary:
      "GitHub 官方 MCP Server，可读取仓库、管理 Issue/PR、分析代码并自动化工作流。",
    stars: 30510,
    language: "Go",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand:
      '{\n  "servers": {\n    "github": {\n      "type": "http",\n      "url": "https://api.githubcopilot.com/mcp/"\n    }\n  }\n}',
    installNote:
      "一键安装会保存官方远程配置快照；连接和授权需要在支持远程 MCP 的宿主中完成。",
    tags: ["GitHub 官方", "PR/Issue", "CI/CD"],
  },
  {
    id: "git-mcp",
    name: "Git MCP",
    repoName: "modelcontextprotocol/server-git",
    repoUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    category: "版本控制",
    description:
      "读取本地 Git 仓库的状态、差异、日志和分支信息，帮助 AI 分析代码历史。",
    readmeSummary:
      "Model Context Protocol 官方参考实现，使用 Python 直接操作指定的本地 Git 仓库。",
    stars: 0,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "uvx mcp-server-git --repository <repository-path>",
    installNote:
      "需要 uvx，并且必须显式选择允许访问的仓库。为避免越过模镜 MCP 沙盒边界，暂不开放一键连接。",
    tags: ["官方参考", "Git 历史", "只读分析"],
  },
  {
    id: "dbhub",
    name: "DBHub",
    repoName: "bytebase/dbhub",
    repoUrl: "https://github.com/bytebase/dbhub",
    category: "数据库",
    description:
      "用统一只读 MCP 适配器查询 PostgreSQL、MySQL 和 MariaDB。",
    readmeSummary:
      "Bytebase 社区项目；模镜固定 1.2.0，只开放只读查询、行数限制和查询超时。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "服务端固定适配器管理连接参数；浏览器不接收 DSN 或 URI。",
    installNote:
      "在卡片内分别填写受控连接字段并保存加密数据库凭据；写入、SSH 与自定义工具均关闭。",
    tags: ["多数据库", "固定 1.2.0", "只读查询"],
  },
  {
    id: "filesystem-mcp",
    name: "Filesystem MCP",
    repoName: "modelcontextprotocol/servers",
    repoUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    category: "文件与存储",
    description:
      "把受控目录内的文件读写、目录浏览等能力交给 AI，适合沙盒内的资料整理。",
    readmeSummary:
      "官方 npm Server。模镜后端会把工作目录限制在 server/mcp/sandboxes，避免访问项目根目录或私人文件。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand:
      "npx -y @modelcontextprotocol/server-filesystem <allowed-directory>",
    installNote:
      "本地 stdio 模式需要允许访问的目录；模镜统一从 MCP 沙盒目录启动。",
    tags: ["官方参考", "文件系统", "沙盒工具"],
  },
  {
    id: "markitdown-mcp",
    name: "MarkItDown MCP",
    repoName: "microsoft/markitdown",
    repoUrl: "https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp",
    category: "文件与存储",
    description:
      "把已上传的文本、PDF、Office、表格与 HTML/XML 文件转换成适合 LLM 处理的 Markdown。",
    readmeSummary:
      "Microsoft AutoGen 团队提供的轻量 Server，暴露 convert_to_markdown 工具并支持 stdio、HTTP 和 SSE。",
    stars: 0,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "pip install markitdown-mcp\nmarkitdown-mcp",
    installNote:
      "第 3 批只处理受控上传文件；图片、音频、网络 URI、宿主文件读取和网页抓取不开放。",
    tags: ["Microsoft 官方", "文档转换", "本地文件"],
  },
  {
    id: "excel-mcp-server",
    name: "Excel MCP Server",
    repoName: "yzfly/mcp-excel-server",
    repoUrl: "https://github.com/yzfly/mcp-excel-server",
    category: "数据分析",
    description:
      "读取和更新 Excel、CSV、TSV 与 JSON，支持汇总统计、数据质量检查、透视表和图表。",
    readmeSummary:
      "面向表格分析的 Python MCP Server，提供文件读取、筛选、透视、写回和可视化工具。",
    stars: 0,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "uvx mcp-excel-server",
    installNote:
      "需要 Python 数据分析依赖和 uvx。涉及本地文件读写，接入时应继续限制在 MCP 沙盒目录。",
    tags: ["Excel", "数据分析", "图表"],
  },
  {
    id: "grafana-mcp",
    name: "Grafana MCP",
    repoName: "grafana/mcp-grafana",
    repoUrl: "https://github.com/grafana/mcp-grafana",
    category: "数据分析",
    description:
      "查询 Grafana 仪表盘、数据源、Prometheus/Loki 指标和告警，辅助可观测性分析。",
    readmeSummary:
      "Grafana 官方 MCP Server，覆盖 Dashboard、Datasource、Incident、Alerting 和 OnCall 等场景。",
    stars: 0,
    language: "Go",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "请按官方 README 配置 Grafana URL、Service Account Token 与启动方式。",
    installNote:
      "需要访问 Grafana 实例的地址和凭证。密钥必须由服务端 Secret 管理，不能放入前端数据。",
    tags: ["Grafana 官方", "监控指标", "需要凭证"],
  },
  {
    id: "notion-mcp-server",
    name: "Notion MCP Server",
    repoName: "makenotion/notion-mcp-server",
    repoUrl: "https://github.com/makenotion/notion-mcp-server",
    category: "效率与协作",
    description:
      "让 AI 搜索、读取和更新 Notion 页面、数据库与评论，适合知识库和项目协作。",
    readmeSummary:
      "Notion 官方开源 MCP Server，使用 Notion API，并要求对可访问页面做明确授权。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "npx -y @notionhq/notion-mcp-server",
    installNote:
      "在卡片内加密保存 Integration Token，并填写显式共享给该 Integration 的 Data Source ID；写入仅限其中的新建页面与页面属性更新。",
    tags: ["Notion 官方", "知识库", "受控写入"],
  },
  {
    id: "blender-mcp",
    name: "Blender MCP",
    repoName: "MCPBlender/blender-mcp",
    repoUrl: "https://github.com/MCPBlender/blender-mcp",
    category: "多媒体",
    description:
      "让 AI 控制 Blender 进行 3D 建模、场景创建、材质编辑和渲染。",
    readmeSummary:
      "社区 1.8.0 项目，由 Blender 插件和 Python MCP Server 配合，包含任意宿主 Python 执行和外部资产能力。",
    stars: 22000,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "请按项目 README 安装 Blender 插件，并配置配套的 Python MCP Server。",
    installNote:
      "依赖桌面 Blender 和插件，不能在模镜后端容器中独立运行。",
    tags: ["3D 建模", "Blender 插件", "桌面宿主"],
  },
  {
    id: "youtube-transcript-mcp",
    name: "YouTube Transcript MCP",
    repoName: "kimtaeyoon83/mcp-server-youtube-transcript",
    repoUrl: "https://github.com/kimtaeyoon83/mcp-server-youtube-transcript",
    category: "多媒体",
    description:
      "提取 YouTube 视频和 Shorts 的字幕，支持语言回退、时间戳和广告片段过滤。",
    readmeSummary:
      "轻量 TypeScript MCP Server，无需外部 API Key，通过 get_transcript 工具返回字幕和元数据。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand:
      "npx -y @kimtaeyoon83/mcp-server-youtube-transcript",
    installNote: "官方 README 提供的标准 npm stdio 配置，不需要额外 API Key。",
    tags: ["YouTube", "字幕提取", "无需密钥"],
  },
  {
    id: "bibigpt-mcp",
    name: "BibiGPT MCP",
    repoName: "JimmyLv/bibigpt-skill",
    repoUrl: "https://github.com/JimmyLv/bibigpt-skill",
    category: "多媒体",
    description:
      "通过远程 MCP 总结 YouTube、Bilibili、TikTok 等平台的视频、音频和播客；当前上游调用需要账号授权。",
    readmeSummary:
      "面向中文内容消费的远程 MCP 与 Skill 组合，适合长视频、播客和音频摘要。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "远程端点：https://bibigpt.co/api/mcp",
    installNote: "上游当前要求 OAuth 2.1 或 API Key；已转入第 10 批授权适配，本批不提供登录入口。",
    tags: ["视频总结", "Bilibili", "远程 MCP"],
  },
  {
    id: "mcp-cn-commerce",
    name: "中国电商经营 MCP",
    repoName: "TonyWang-hub/mcp-cn-commerce",
    repoUrl: "https://github.com/TonyWang-hub/mcp-cn-commerce",
    category: "电商经营",
    description:
      "只读接入抖店、京东、淘宝、拼多多、快手、小红书、微信小店和巨量引擎经营数据。",
    readmeSummary:
      "Python MCP Server 套件，覆盖订单、商品、售后、库存和广告报表；所有工具默认只读。",
    stars: 0,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "pip install mcp-cn-commerce",
    installNote:
      "依赖多平台 OAuth、商家授权和短期 Token；第 10 批授权与撤销能力完成前不开放配置或连接。",
    tags: ["中国电商", "多平台授权", "适配受阻"],
  },
  {
    id: "memory-mcp",
    name: "Memory MCP",
    repoName: "modelcontextprotocol/server-memory",
    repoUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    category: "知识与记忆",
    description:
      "使用本地知识图谱保存实体、关系和观察记录，为 AI 提供跨对话持久记忆。",
    readmeSummary:
      "Model Context Protocol 官方参考实现，默认把记忆写入本地 JSONL 文件。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand: "npx -y @modelcontextprotocol/server-memory",
    installNote:
      "模镜会在 MCP 沙盒工作目录启动该 Server，使默认 memory.jsonl 保留在受控目录内。",
    tags: ["官方参考", "知识图谱", "本地记忆"],
  },
  {
    id: "chatcrystal",
    name: "ChatCrystal",
    repoName: "ZengLiangYi/ChatCrystal",
    repoUrl: "https://github.com/ZengLiangYi/ChatCrystal",
    category: "知识与记忆",
    description:
      "把 Claude Code、Cursor、Codex 等编码对话导入本地知识库，提供检索、标签图谱和 Markdown 导出。",
    readmeSummary:
      "ChatCrystal 0.5.8 会导入本机编码对话并调用可配置模型服务，MCP 模式提供敏感历史召回和写回能力。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand: "npx -y chatcrystal mcp",
    installNote: "npm 包要求 Node.js 20 或更高版本，模镜后端运行时满足该要求。",
    tags: ["编码对话", "本地知识库", "Windows"],
  },
  {
    id: "zotero-mcp",
    name: "Zotero MCP",
    repoName: "54yyyu/zotero-mcp",
    repoUrl: "https://github.com/54yyyu/zotero-mcp",
    category: "知识与记忆",
    description:
      "连接 Zotero 文献库，支持检索论文、讨论内容、生成摘要和分析引文。",
    readmeSummary:
      "Zotero MCP 0.9.1 同时支持本地全文读取与带 API Key 的云端写入、PDF 下载和可选语义索引。",
    stars: 4000,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "请按项目 README 选择本地 Zotero 或 Zotero Web API 模式。",
    installNote:
      "本地模式依赖 Zotero 桌面应用，云端模式需要 API 凭证，因此不由模镜后端自动启动。",
    tags: ["文献管理", "学术研究", "Zotero"],
  },
  {
    id: "semgrep-mcp",
    name: "Semgrep MCP",
    repoName: "semgrep/mcp",
    repoUrl: "https://github.com/semgrep/mcp",
    category: "安全分析",
    description:
      "让 AI 代理调用 Semgrep 执行代码安全扫描、规则查询和漏洞分析。",
    readmeSummary:
      "Semgrep 官方 MCP 集成，适合在代码审查和开发流程中补充静态安全分析。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "请按 Semgrep MCP README 安装 CLI，并在受控代码目录中配置 Server。",
    installNote:
      "安全扫描需要访问目标源码，有些功能还需要 Semgrep 账号或 Token；接入前应确定代码和网络边界。",
    tags: ["Semgrep 官方", "代码扫描", "安全审查"],
  },
  {
    id: "12306-mcp",
    name: "12306 MCP",
    repoName: "Joooook/12306-mcp",
    repoUrl: "https://github.com/Joooook/12306-mcp",
    category: "地理与出行",
    description:
      "查询 12306 车次、余票、经停站和中转方案，为出行规划提供实时铁路信息。",
    readmeSummary:
      "面向中文用户的 TypeScript stdio MCP Server，提供查询、筛选、过站和中转工具。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand: "npx -y 12306-mcp",
    installNote: "项目 README 提供的标准 npm stdio 启动方式，不需要额外 API Key。",
    tags: ["中国铁路", "余票查询", "无需密钥"],
  },
  {
    id: "sequential-thinking-mcp",
    name: "Sequential Thinking MCP",
    repoName: "modelcontextprotocol/server-sequential-thinking",
    repoUrl:
      "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    category: "通用工具",
    description:
      "用可修订的思考序列拆解复杂问题，适合规划、设计和需要中途校正的分析任务。",
    readmeSummary:
      "Model Context Protocol 官方参考实现，提供单一的 sequentialthinking 工具。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand: "npx -y @modelcontextprotocol/server-sequential-thinking",
    installNote: "官方 npm stdio Server，无需 API Key 或额外服务。",
    tags: ["官方参考", "问题拆解", "无需密钥"],
  },
  {
    id: "everything-mcp",
    name: "Everything MCP",
    repoName: "modelcontextprotocol/server-everything",
    repoUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/everything",
    category: "通用工具",
    description:
      "集中演示 MCP 的工具、资源、提示词、采样、日志和通知等协议能力。",
    readmeSummary:
      "Model Context Protocol 官方参考 Server，适合验证客户端兼容性和调试 MCP 功能。",
    stars: 0,
    language: "TypeScript",
    verifiedAt: "2026-08-02",
    installMode: "one-click",
    installCommand: "npx -y @modelcontextprotocol/server-everything",
    installNote: "用于协议演示和测试，不建议作为生产业务 Server 使用。",
    tags: ["官方参考", "协议测试", "无需密钥"],
  },
  {
    id: "time-mcp",
    name: "Time MCP",
    repoName: "modelcontextprotocol/server-time",
    repoUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    category: "通用工具",
    description:
      "获取任意时区的当前时间并执行时区转换，为日程和跨地区协作提供确定性时间信息。",
    readmeSummary:
      "Model Context Protocol 官方 Python 参考实现，提供本地时间查询和时区转换工具。",
    stars: 0,
    language: "Python",
    verifiedAt: "2026-08-02",
    installMode: "manual",
    installCommand: "uvx mcp-server-time",
    installNote: "需要 uvx。当前模镜容器只保证 Node/npm MCP 运行时，因此暂不开放一键连接。",
    tags: ["官方参考", "时区转换", "无需密钥"],
  },
];

interface ExpandedProjectInput {
  id: string;
  name: string;
  repoName: string;
  repoUrl: string;
  category: McpCategory;
  description: string;
  readmeSummary: string;
  stars?: number;
  language: string;
  verifiedAt?: string;
  tags: string[];
  requirements: McpRequirement[];
  usageExamples: string[];
  sources?: McpCatalogSourceId[];
}

function plannedMcp(input: ExpandedProjectInput): McpProjectSeed {
  const {
    stars = 0,
    verifiedAt = "2026-08-02",
    ...project
  } = input;
  const requirementText = input.requirements
    .map((requirement) => mcpRequirementLabels[requirement])
    .join("、");
  return {
    ...project,
    stars,
    verifiedAt,
    installMode: "manual",
    installCommand: "当前版本仅收录资料，不提供本地 stdio 安装或外站认证入口。",
    installNote: `${requirementText}。本轮不配置这些依赖，条目等待后续安全适配。`,
    configGuide: [
      `先确认接入条件：${requirementText}。`,
      "当前模镜不会收集凭证、打开外站认证，也不会启动额外运行时或桌面宿主。",
      "待对应的凭证代理、运行时隔离或远程传输适配完成后，再开放连接测试。",
    ],
    sources: input.sources ?? ["awesome-mcp-servers"],
  };
}

const expandedMcpProjectSeeds: McpProjectSeed[] = [
  plannedMcp({
    id: "firecrawl-mcp",
    name: "Firecrawl MCP",
    repoName: "mendableai/firecrawl-mcp-server",
    repoUrl: "https://github.com/mendableai/firecrawl-mcp-server",
    category: "浏览器与网页",
    description: "抓取网页、执行搜索并把站点内容整理成适合 AI 使用的 Markdown 或结构化数据。",
    readmeSummary: "Firecrawl 官方 MCP 集成，面向批量抓取、站点地图发现和网页内容抽取。",
    language: "TypeScript",
    tags: ["网页抓取", "结构化提取", "Firecrawl 官方"],
    requirements: ["token", "remote-transport"],
    usageExamples: ["汇总一个产品站点的帮助文档", "把指定页面抽取成结构化字段"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "browserbase-mcp",
    name: "Browserbase MCP",
    repoName: "browserbase/mcp-server-browserbase",
    repoUrl: "https://github.com/browserbase/mcp-server-browserbase",
    category: "浏览器与网页",
    description: "在托管浏览器中导航网页、填写表单、提取数据并执行自动化任务。",
    readmeSummary: "Browserbase 官方实现，把云端浏览器会话和网页操作能力暴露给 MCP 客户端。",
    language: "TypeScript",
    tags: ["云端浏览器", "表单自动化", "Browserbase 官方"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["自动完成网页回归流程", "从多页列表采集公开信息"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "agentql-mcp",
    name: "AgentQL MCP",
    repoName: "tinyfish-io/agentql-mcp",
    repoUrl: "https://github.com/tinyfish-io/agentql-mcp",
    category: "浏览器与网页",
    description: "用自然语言式查询从非结构化网页中提取稳定的结构化数据。",
    readmeSummary: "TinyFish 官方实现，重点解决网页元素变化时的数据定位与提取。",
    language: "TypeScript",
    tags: ["网页提取", "结构化数据", "AgentQL"],
    requirements: ["token", "remote-transport"],
    usageExamples: ["抽取商品名称、价格和库存", "把搜索结果整理为 JSON"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "bright-data-mcp",
    name: "Bright Data MCP",
    repoName: "brightdata/brightdata-mcp",
    repoUrl: "https://github.com/brightdata/brightdata-mcp",
    category: "浏览器与网页",
    description: "提供网页搜索、抓取和地理区域访问能力，面向复杂公网数据采集。",
    readmeSummary: "Bright Data 官方项目，覆盖搜索、网页解锁和结构化数据获取。",
    language: "JavaScript",
    tags: ["公网数据", "反爬处理", "Bright Data 官方"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["采集不同地区的公开页面", "为市场研究整理网页样本"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "puppeteer-mcp",
    name: "Puppeteer MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/puppeteer",
    category: "浏览器与网页",
    description: "归档的 Puppeteer MCP 参考实现，包含浏览器控制、截图和页面脚本执行能力。",
    readmeSummary: "上游已经归档且包含任意启动参数与脚本执行入口，本批不启动运行时、不提供连接或替代入口。",
    language: "TypeScript",
    tags: ["官方归档", "Puppeteer", "浏览器控制"],
    requirements: ["external-runtime", "system-permission"],
    usageExamples: ["仅查看归档项目资料", "等待有持续维护的固定工具契约后重新评估"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "selenium-mcp",
    name: "Selenium MCP",
    repoName: "angiejones/mcp-selenium",
    repoUrl: "https://github.com/angiejones/mcp-selenium",
    category: "浏览器与网页",
    description: "社区 Selenium MCP 实现，可通过 WebDriver 执行页面操作和自动化测试。",
    readmeSummary: "许可证、容器和驱动契约尚未形成一致的可复现版本，本批不启动 WebDriver 或提供连接入口。",
    language: "Python",
    tags: ["Selenium", "WebDriver", "自动化测试"],
    requirements: ["external-runtime", "system-permission"],
    usageExamples: ["仅查看社区项目资料", "等待许可证与运行时契约厘清后重新评估"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "mobile-mcp",
    name: "Mobile MCP",
    repoName: "mobile-next/mobile-mcp",
    repoUrl: "https://github.com/mobile-next/mobile-mcp",
    category: "浏览器与网页",
    description: "让 AI 操作 iOS 与 Android 模拟器或真机，执行移动端交互和测试。",
    readmeSummary: "Mobile MCP 1.0.2 通过 adb、simctl 或 WebDriverAgent 控制模拟器和真机，并开放应用安装/卸载、输入、录屏及崩溃读取。",
    language: "TypeScript",
    tags: ["移动测试", "iOS", "Android"],
    requirements: ["external-runtime", "desktop-host", "system-permission"],
    usageExamples: ["走查移动端登录流程", "采集应用页面截图"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "apify-mcp",
    name: "Apify Actors MCP",
    repoName: "apify/apify-mcp-server",
    repoUrl: "https://github.com/apify/apify-mcp-server",
    category: "浏览器与网页",
    description: "把 Apify Actors 的网页抓取、数据处理和自动化能力提供给 AI。",
    readmeSummary: "Apify 官方集成，可发现并运行 Actors，再读取其数据集结果。",
    language: "TypeScript",
    tags: ["Apify 官方", "Actors", "数据采集"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["运行现成 Actor 采集公开数据", "读取 Actor 任务结果"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "brave-search-mcp",
    name: "Brave Search MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/brave-search",
    category: "搜索与研究",
    description: "通过 Brave Search API 执行网页搜索和本地搜索。",
    readmeSummary: "MCP 官方归档参考实现，展示搜索 API 如何包装为 MCP 工具。",
    language: "TypeScript",
    tags: ["网页搜索", "Brave", "官方归档"],
    requirements: ["token", "remote-transport"],
    usageExamples: ["检索某主题的近期网页", "收集多个来源的研究线索"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "exa-mcp",
    name: "Exa MCP",
    repoName: "exa-labs/exa-mcp-server",
    repoUrl: "https://github.com/exa-labs/exa-mcp-server",
    category: "搜索与研究",
    description: "面向 AI 研究任务执行语义网页搜索、代码搜索和内容发现。",
    readmeSummary: "Exa 官方 MCP Server，强调高相关性的语义检索与网页内容返回。",
    language: "TypeScript",
    tags: ["Exa 官方", "语义搜索", "研究"],
    requirements: ["token", "remote-transport"],
    usageExamples: ["寻找某领域的权威资料", "搜索与技术问题相关的代码示例"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "tavily-mcp",
    name: "Tavily MCP",
    repoName: "tavily-ai/tavily-mcp",
    repoUrl: "https://github.com/tavily-ai/tavily-mcp",
    category: "搜索与研究",
    description: "为研究型代理提供搜索、内容抽取、站点地图和网页抓取工具。",
    readmeSummary: "Tavily 官方 MCP 集成，面向需要联网检索与证据收集的代理工作流。",
    language: "TypeScript",
    tags: ["Tavily 官方", "联网研究", "内容抽取"],
    requirements: ["token", "remote-transport"],
    usageExamples: ["为报告收集多来源证据", "提取指定网页的正文"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "kagi-mcp",
    name: "Kagi Search MCP",
    repoName: "ac3xx/mcp-servers-kagi",
    repoUrl: "https://github.com/ac3xx/mcp-servers-kagi",
    category: "搜索与研究",
    description: "使用 Kagi Search API 完成网页检索和内容摘要。",
    readmeSummary: "社区实现，将 Kagi 搜索服务包装为适合 MCP 客户端调用的工具。",
    language: "TypeScript",
    tags: ["Kagi", "网页搜索", "内容摘要"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["检索无广告网页结果", "快速整理搜索结果摘要"],
  }),
  plannedMcp({
    id: "perplexity-mcp",
    name: "Perplexity MCP",
    repoName: "ppl-ai/modelcontextprotocol",
    repoUrl: "https://github.com/ppl-ai/modelcontextprotocol",
    category: "搜索与研究",
    description: "调用 Perplexity 的联网问答能力完成搜索、研究与答案归纳。",
    readmeSummary: "Perplexity 官方 MCP 集成，面向需要实时网页信息的研究任务。",
    language: "TypeScript",
    tags: ["Perplexity 官方", "联网问答", "研究"],
    requirements: ["token", "remote-transport"],
    usageExamples: ["回答需要最新网页信息的问题", "生成带来源线索的研究摘要"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "gitlab-mcp",
    name: "GitLab MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/gitlab",
    category: "版本控制",
    description: "读取和管理 GitLab 项目、Issue、合并请求与仓库文件。",
    readmeSummary: "MCP 官方归档参考实现，展示 GitLab API 的项目协作工具接入方式。",
    language: "TypeScript",
    tags: ["GitLab", "合并请求", "官方归档"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["汇总待处理合并请求", "查询项目 Issue 和仓库文件"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "xcodebuild-mcp",
    name: "XcodeBuildMCP",
    repoName: "getsentry/XcodeBuildMCP",
    repoUrl: "https://github.com/getsentry/XcodeBuildMCP",
    category: "开发与代码",
    description: "构建、运行和测试 Xcode 项目，并管理模拟器、日志和 Apple 平台工作流。",
    readmeSummary: "Sentry 官方 2.7.0 项目，需要真实 macOS/Xcode，并开放构建、测试、清理、应用安装/启动、调试和 UI 自动化。",
    language: "TypeScript",
    tags: ["Sentry 官方", "Xcode", "Apple 开发"],
    requirements: ["external-runtime", "desktop-host", "system-permission"],
    usageExamples: ["构建并测试 iOS 工程", "读取模拟器运行日志"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "jetbrains-mcp",
    name: "JetBrains MCP",
    repoName: "JetBrains/mcp-jetbrains",
    repoUrl: "https://github.com/JetBrains/mcp-jetbrains",
    category: "开发与代码",
    description: "让编码代理访问 JetBrains IDE 的项目、编辑器和开发操作。",
    readmeSummary: "JetBrains 官方代理会发现本机 IDE HTTP 端口并转发项目读取、检查和 IDE 动作；源码与 npm 当前版本还存在发布差异。",
    language: "Kotlin",
    tags: ["JetBrains 官方", "IDE", "代码开发"],
    requirements: ["desktop-host", "external-runtime", "system-permission"],
    usageExamples: ["让代理读取 IDE 当前项目", "在 IDE 环境中执行代码导航"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "figma-context-mcp",
    name: "Figma Context MCP",
    repoName: "GLips/Figma-Context-MCP",
    repoUrl: "https://github.com/GLips/Figma-Context-MCP",
    category: "开发与代码",
    description: "读取 Figma 设计结构，为设计稿转代码提供布局、样式和组件上下文。",
    readmeSummary: "社区热门设计上下文 Server，面向 Cursor、Claude 等编码代理。",
    language: "TypeScript",
    tags: ["Figma", "设计转代码", "组件上下文"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["读取选定设计节点的布局", "生成与设计结构对应的前端代码"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "e2b-mcp",
    name: "E2B MCP",
    repoName: "e2b-dev/mcp-server",
    repoUrl: "https://github.com/e2b-dev/mcp-server",
    category: "开发与代码",
    description: "在云端隔离沙箱中执行代码、安装依赖并读取运行结果。",
    readmeSummary: "E2B 官方 MCP 集成，把受控云沙箱作为代理的代码执行环境。",
    language: "TypeScript",
    tags: ["E2B 官方", "代码沙箱", "云端执行"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["隔离运行一段 Python 代码", "验证依赖安装后的程序输出"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "mcp-run-python",
    name: "MCP Run Python",
    repoName: "pydantic/mcp-run-python",
    repoUrl: "https://github.com/pydantic/mcp-run-python",
    category: "开发与代码",
    description: "曾通过 Pyodide/Deno 执行 Python；维护方已因无法安全隔离不可信代码而归档项目。",
    readmeSummary: "Pydantic 已退休该实现，并明确警告任意 JavaScript、运行时污染、文件访问和内存耗尽风险。",
    language: "Python",
    tags: ["Pydantic", "Python", "已归档"],
    requirements: ["external-runtime", "system-permission"],
    usageExamples: ["执行可复现的数据计算", "把输入文本批量转换为结构化结果"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "neon-mcp",
    name: "Neon MCP",
    repoName: "neondatabase/mcp-server-neon",
    repoUrl: "https://github.com/neondatabase/mcp-server-neon",
    category: "数据库",
    description: "创建和管理 Neon Serverless Postgres 项目、分支与数据库资源。",
    readmeSummary: "Neon 官方 MCP Server，面向无服务器 Postgres 的管理和开发工作流。",
    language: "TypeScript",
    tags: ["Neon 官方", "Postgres", "无服务器数据库"],
    requirements: ["oauth", "token", "account-binding", "remote-transport"],
    usageExamples: ["查看 Neon 项目和数据库分支", "为开发环境创建临时数据库"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "supabase-mcp",
    name: "Supabase MCP",
    repoName: "supabase/mcp",
    repoUrl: "https://github.com/supabase/mcp",
    category: "数据库",
    description: "在指定 Supabase 项目内只读查询数据库结构、扩展和数据。",
    readmeSummary: "Supabase 社区官方 MCP；模镜固定 0.9.0、项目范围、PAT stdio 与只读能力。",
    language: "TypeScript",
    tags: ["Supabase", "固定 0.9.0", "项目只读"],
    requirements: ["token", "account-binding", "database-credentials", "remote-transport"],
    usageExamples: ["检查数据库表结构与扩展", "执行受限的只读 SQL 查询"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "mongodb-mcp",
    name: "MongoDB MCP",
    repoName: "mongodb-js/mongodb-mcp-server",
    repoUrl: "https://github.com/mongodb-js/mongodb-mcp-server",
    category: "数据库",
    description: "浏览 MongoDB 数据库、集合和索引，并执行受控只读查询与聚合。",
    readmeSummary: "MongoDB 官方 MCP Server；模镜固定 2.0.0，只开放自托管数据库读取能力。",
    language: "TypeScript",
    tags: ["MongoDB 官方", "固定 2.0.0", "只读模式"],
    requirements: ["database-credentials", "token", "system-permission"],
    usageExamples: ["查看集合结构和索引", "执行只读聚合查询"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "redis-mcp",
    name: "Redis MCP",
    repoName: "redis/mcp-redis",
    repoUrl: "https://github.com/redis/mcp-redis",
    category: "数据库",
    description: "通过只读 ACL 检查 Redis 键空间、数据结构与向量检索结果。",
    readmeSummary: "Redis 官方 MCP Server；模镜固定 0.5.1，并以只读工具白名单过滤操作。",
    language: "Python",
    tags: ["Redis 官方", "固定 0.5.1", "只读 ACL"],
    requirements: ["external-runtime", "database-credentials", "system-permission"],
    usageExamples: ["分页检查键空间和缓存内容", "读取 Hash、List、Set 与有序集合"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "clickhouse-mcp",
    name: "ClickHouse MCP",
    repoName: "ClickHouse/mcp-clickhouse",
    repoUrl: "https://github.com/ClickHouse/mcp-clickhouse",
    category: "数据库",
    description: "查询 ClickHouse 数据库、表结构和分析数据。",
    readmeSummary: "ClickHouse 官方 MCP；模镜固定 0.4.1，强制只读会话并关闭 chDB。",
    language: "Python",
    tags: ["ClickHouse 官方", "固定 0.4.1", "只读 OLAP"],
    requirements: ["external-runtime", "database-credentials", "system-permission"],
    usageExamples: ["查看数据表与字段", "执行聚合分析查询"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "aiven-mcp",
    name: "Aiven MCP",
    repoName: "Aiven-Open/mcp-aiven",
    repoUrl: "https://github.com/Aiven-Open/mcp-aiven",
    category: "数据库",
    description: "管理 Aiven 项目，并访问 PostgreSQL、Kafka、ClickHouse 与 OpenSearch 服务。",
    readmeSummary: "Aiven 官方 MCP Server，为其托管数据基础设施提供统一工具接口。",
    language: "Python",
    tags: ["Aiven 官方", "托管数据库", "Kafka"],
    requirements: ["external-runtime", "token", "account-binding", "remote-transport"],
    usageExamples: ["查看 Aiven 服务状态", "查询项目中的数据库资源"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "sqlite-mcp",
    name: "SQLite MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/sqlite",
    category: "数据库",
    description: "读取 SQLite 数据库结构、执行 SQL 并生成简单分析结论。",
    readmeSummary: "MCP 官方归档 Python 参考实现，适合了解本地数据库工具的基本设计。",
    language: "Python",
    tags: ["SQLite", "官方归档", "本地数据库"],
    requirements: ["external-runtime", "database-credentials", "system-permission"],
    usageExamples: ["检查 SQLite 表结构", "在副本上执行只读 SQL"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "postgres-mcp",
    name: "PostgreSQL MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/postgres",
    category: "数据库",
    description: "读取 PostgreSQL 表结构并执行受控的只读查询。",
    readmeSummary: "MCP 官方归档参考实现，以数据库 URI 连接 PostgreSQL 并暴露查询工具。",
    language: "TypeScript",
    tags: ["PostgreSQL", "官方归档", "只读查询"],
    requirements: ["database-credentials", "system-permission"],
    usageExamples: ["探索数据库 schema", "查询业务指标样本"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "cloudflare-mcp",
    name: "Cloudflare MCP",
    repoName: "cloudflare/mcp-server-cloudflare",
    repoUrl: "https://github.com/cloudflare/mcp-server-cloudflare",
    category: "云平台与运维",
    description: "查询和管理 Cloudflare Workers、日志、对象存储与开发资源。",
    readmeSummary: "Cloudflare 官方 MCP Server 集合，将多个云平台能力拆分为专用工具服务。",
    language: "TypeScript",
    tags: ["Cloudflare 官方", "Workers", "云平台"],
    requirements: ["oauth", "token", "account-binding", "remote-transport"],
    usageExamples: ["查看 Worker 的运行日志", "查询账户下的云资源"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "terraform-mcp",
    name: "Terraform MCP Server",
    repoName: "hashicorp/terraform-mcp-server",
    repoUrl: "https://github.com/hashicorp/terraform-mcp-server",
    category: "云平台与运维",
    description: "查询 Terraform Registry、Provider、Module 和基础设施开发信息。",
    readmeSummary: "HashiCorp 官方契约的公共 Terraform Registry 只读子集；HCP/TFE 与资源变更能力不进入本批运行时。",
    language: "Go",
    tags: ["HashiCorp 官方", "Terraform", "基础设施即代码"],
    requirements: ["external-runtime", "remote-transport"],
    usageExamples: ["查找合适的 Terraform 模块", "读取 Provider 资源文档"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "kubernetes-mcp",
    name: "Kubernetes MCP Server",
    repoName: "containers/kubernetes-mcp-server",
    repoUrl: "https://github.com/containers/kubernetes-mcp-server",
    category: "云平台与运维",
    description: "查看 Kubernetes 集群资源、工作负载、事件与日志，并执行受控运维操作。",
    readmeSummary: "面向 Kubernetes 的 MCP Server，依赖集群上下文、权限策略和本地运行环境。",
    language: "Go",
    tags: ["Kubernetes", "集群运维", "容器"],
    requirements: ["external-runtime", "token", "system-permission"],
    usageExamples: ["检查异常 Pod 和事件", "汇总命名空间资源状态"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "azure-mcp",
    name: "Azure MCP Server",
    repoName: "microsoft/mcp",
    repoUrl: "https://github.com/microsoft/mcp",
    category: "云平台与运维",
    description: "让 AI 访问 Azure 资源、文档和云服务操作。",
    readmeSummary: "Microsoft 官方 Azure MCP Server，覆盖多种 Azure 服务与开发场景。",
    language: "C#",
    tags: ["Microsoft 官方", "Azure", "云资源"],
    requirements: ["oauth", "token", "account-binding", "external-runtime"],
    usageExamples: ["查询 Azure 资源状态", "辅助排查云服务配置"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "aws-kb-mcp",
    name: "AWS Knowledge Bases MCP",
    repoName: "awslabs/mcp",
    repoUrl: "https://github.com/awslabs/mcp",
    category: "云平台与运维",
    description: "连接 AWS 服务、文档和 Bedrock Knowledge Bases 等云端能力。",
    readmeSummary: "AWS Labs MCP Server 集合，覆盖云资源、开发工具和生成式 AI 场景。",
    language: "Python",
    tags: ["AWS Labs", "Bedrock", "云服务"],
    requirements: ["external-runtime", "token", "account-binding", "remote-transport"],
    usageExamples: ["检索 AWS 官方知识", "查询 Bedrock 知识库内容"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "docker-mcp",
    name: "Docker MCP",
    repoName: "docker/mcp-gateway",
    repoUrl: "https://github.com/docker/mcp-gateway",
    category: "云平台与运维",
    description: "通过 Docker 环境发现、运行和隔离 MCP Servers。",
    readmeSummary: "Docker 官方 v0.43.3 Gateway 是可动态管理 Server、容器、Secrets 和 OAuth 的 CLI 控制面，不是固定只读适配器。",
    language: "Go",
    tags: ["Docker 官方", "容器运行时", "MCP Gateway"],
    requirements: ["external-runtime", "desktop-host", "system-permission"],
    usageExamples: ["在容器中隔离运行 MCP Server", "统一发现容器化工具"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "google-drive-mcp",
    name: "Google Drive MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/gdrive",
    category: "文件与存储",
    description: "搜索和读取 Google Drive 中的文件内容与元数据。",
    readmeSummary: "MCP 官方归档参考实现，展示 Google Drive OAuth 与文件检索接入。",
    language: "TypeScript",
    tags: ["Google Drive", "云文件", "官方归档"],
    requirements: ["oauth", "account-binding", "remote-transport"],
    usageExamples: ["搜索团队云盘资料", "读取文档内容供问答使用"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "box-mcp",
    name: "Box MCP",
    repoName: "box-community/mcp-server-box",
    repoUrl: "https://github.com/box-community/mcp-server-box",
    category: "文件与存储",
    description: "访问 Box 文件、文件夹和企业内容，支持搜索与内容读取。",
    readmeSummary: "Box 社区 MCP Server，为企业云内容管理提供工具接口。",
    language: "TypeScript",
    tags: ["Box", "企业内容", "云存储"],
    requirements: ["oauth", "token", "account-binding", "remote-transport"],
    usageExamples: ["查找企业资料", "读取指定文件的文本内容"],
    sources: ["awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "onedrive-mcp",
    name: "OneDrive MCP",
    repoName: "softeria/ms-365-mcp-server",
    repoUrl: "https://github.com/softeria/ms-365-mcp-server",
    category: "文件与存储",
    description: "通过 Microsoft Graph 访问 OneDrive、SharePoint 文件与 Office 内容。",
    readmeSummary: "Microsoft 365 社区 MCP Server，覆盖文件、邮件、日历和办公套件。",
    language: "TypeScript",
    tags: ["OneDrive", "Microsoft Graph", "Office 文件"],
    requirements: ["oauth", "account-binding", "remote-transport"],
    usageExamples: ["搜索 OneDrive 文件", "读取 SharePoint 文档内容"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "s3-mcp",
    name: "Amazon S3 MCP",
    repoName: "awslabs/mcp",
    repoUrl: "https://github.com/awslabs/mcp/tree/main/src/s3-tables-mcp-server",
    category: "文件与存储",
    description: "查询和管理 S3 对象或 S3 Tables 数据资源。",
    readmeSummary: "AWS Labs MCP Server 集合中的存储能力，依赖 AWS 身份和资源权限。",
    language: "Python",
    tags: ["AWS Labs", "S3", "对象存储"],
    requirements: ["external-runtime", "token", "account-binding", "system-permission"],
    usageExamples: ["列出受控存储桶中的对象", "查询 S3 Tables 元数据"],
    sources: ["awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "duckdb-mcp",
    name: "DuckDB MCP",
    repoName: "motherduckdb/mcp-server-motherduck",
    repoUrl: "https://github.com/motherduckdb/mcp-server-motherduck",
    category: "数据分析",
    description: "只读分析封存工作区内的本地 DuckDB 数据库文件。",
    readmeSummary: "MotherDuck 官方 MCP Server；模镜固定 1.0.7，仅开放断网本地 DuckDB 子集。",
    language: "Python",
    tags: ["DuckDB", "固定 1.0.7", "封存文件只读"],
    requirements: ["external-runtime"],
    usageExamples: ["查看本地 DuckDB 的表结构", "对封存数据执行只读 SQL 分析"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "vegalite-mcp",
    name: "Vega-Lite MCP",
    repoName: "isaacwasserman/mcp-vegalite-server",
    repoUrl: "https://github.com/isaacwasserman/mcp-vegalite-server",
    category: "数据分析",
    description: "把表格数据转换为 Vega-Lite 图表规范和可视化结果。",
    readmeSummary: "社区数据可视化 Server，用自然语言生成声明式图表配置。",
    language: "Python",
    tags: ["Vega-Lite", "数据可视化", "图表"],
    requirements: ["external-runtime"],
    usageExamples: ["把销售数据绘制成趋势图", "生成可复用的 Vega-Lite 规范"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "quickchart-mcp",
    name: "QuickChart MCP",
    repoName: "gongrzhe/Quickchart-MCP-Server",
    repoUrl: "https://github.com/GongRzhe/Quickchart-MCP-Server",
    category: "数据分析",
    description: "根据数据与图表配置生成可分享的图表图像。",
    readmeSummary: "社区 MCP Server，使用 QuickChart 服务生成 Chart.js 图表；上游仓库已归档，模镜固定兼容 1.0.6 的受控子集。",
    language: "Python",
    tags: ["QuickChart", "Chart.js", "图表生成"],
    requirements: ["external-runtime", "remote-transport"],
    usageExamples: ["生成业务指标折线图", "把分析结果转成图表图片"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "axiom-mcp",
    name: "Axiom MCP",
    repoName: "axiomhq/mcp-server-axiom",
    repoUrl: "https://github.com/axiomhq/mcp-server-axiom",
    category: "数据分析",
    description: "查询 Axiom 数据集、日志和可观测性事件，辅助分析与排障。",
    readmeSummary: "Axiom 官方 MCP Server，把事件查询与数据分析工具提供给 AI。",
    language: "TypeScript",
    tags: ["Axiom 官方", "日志分析", "可观测性"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询错误日志趋势", "汇总指定时间段的事件"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "slack-mcp",
    name: "Slack MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/slack",
    category: "通讯与协作",
    description: "读取频道、搜索消息并在 Slack 工作区内执行协作操作。",
    readmeSummary: "MCP 官方归档参考实现，展示 Slack Bot Token 与团队协作工具接入。",
    language: "TypeScript",
    tags: ["Slack", "团队消息", "官方归档"],
    requirements: ["oauth", "token", "account-binding", "remote-transport"],
    usageExamples: ["汇总频道讨论", "查找与项目相关的历史消息"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "linear-mcp",
    name: "Linear MCP",
    repoName: "jerhadf/linear-mcp-server",
    repoUrl: "https://github.com/jerhadf/linear-mcp-server",
    category: "通讯与协作",
    description: "查询和管理 Linear Issue、项目、团队与工作流状态。",
    readmeSummary: "社区 Linear MCP Server，为产品和研发协作提供 Issue 工具。",
    language: "TypeScript",
    tags: ["Linear", "Issue", "项目协作"],
    requirements: ["oauth", "token", "account-binding", "remote-transport"],
    usageExamples: ["汇总本周待处理 Issue", "更新任务状态和负责人"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "atlassian-mcp",
    name: "Atlassian MCP",
    repoName: "sooperset/mcp-atlassian",
    repoUrl: "https://github.com/sooperset/mcp-atlassian",
    category: "通讯与协作",
    description: "访问 Jira Issue 与 Confluence 页面，连接研发任务和团队知识。",
    readmeSummary: "社区热门 Atlassian MCP Server，覆盖 Jira 和 Confluence 常用操作。",
    language: "Python",
    tags: ["Jira", "Confluence", "Atlassian"],
    requirements: ["external-runtime", "oauth", "token", "account-binding"],
    usageExamples: ["汇总 Jira 冲刺任务", "检索 Confluence 技术文档"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "asana-mcp",
    name: "Asana MCP",
    repoName: "roychri/mcp-server-asana",
    repoUrl: "https://github.com/roychri/mcp-server-asana",
    category: "通讯与协作",
    description: "读取和更新 Asana 工作区中的项目、任务和协作信息。",
    readmeSummary: "社区 Asana MCP Server，将 Asana API 封装为项目管理工具。",
    language: "TypeScript",
    tags: ["Asana", "任务管理", "项目协作"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询项目逾期任务", "创建并分派新的协作任务"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "airtable-mcp",
    name: "Airtable MCP",
    repoName: "domdomegg/airtable-mcp-server",
    repoUrl: "https://github.com/domdomegg/airtable-mcp-server",
    category: "效率与协作",
    description: "检查 Airtable Base 结构并读取、创建和更新记录。",
    readmeSummary: "社区 Airtable MCP Server，提供 schema 浏览与表格记录读写工具。",
    language: "TypeScript",
    tags: ["Airtable", "表格数据库", "记录管理"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询 Airtable 业务记录", "批量更新表格字段"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "google-calendar-mcp",
    name: "Google Calendar MCP",
    repoName: "nspady/google-calendar-mcp",
    repoUrl: "https://github.com/nspady/google-calendar-mcp",
    category: "效率与协作",
    description: "查看可用时间、搜索日程并创建或更新 Google Calendar 事件。",
    readmeSummary: "社区日历 MCP Server，支持多日历查询、排期和事件管理。",
    language: "TypeScript",
    tags: ["Google Calendar", "日程", "排期"],
    requirements: ["oauth", "account-binding", "remote-transport"],
    usageExamples: ["查找团队共同空闲时间", "创建带参与人的会议日程"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "gmail-mcp",
    name: "Gmail MCP",
    repoName: "GongRzhe/Gmail-MCP-Server",
    repoUrl: "https://github.com/GongRzhe/Gmail-MCP-Server",
    category: "通讯与协作",
    description: "搜索、读取、撰写和管理 Gmail 邮件与标签。",
    readmeSummary: "社区 Gmail MCP Server，通过 Google OAuth 访问邮箱能力。",
    language: "Python",
    tags: ["Gmail", "邮件", "Google Workspace"],
    requirements: ["external-runtime", "oauth", "account-binding", "remote-transport"],
    usageExamples: ["汇总未读邮件", "起草一封项目进展邮件"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "microsoft-365-mcp",
    name: "Microsoft 365 MCP",
    repoName: "softeria/ms-365-mcp-server",
    repoUrl: "https://github.com/softeria/ms-365-mcp-server",
    category: "效率与协作",
    description: "通过 Microsoft Graph 连接 Outlook、日历、OneDrive、Excel 与办公套件。",
    readmeSummary: "社区 Microsoft 365 MCP Server，覆盖邮件、文件、日历与 Office 数据。",
    language: "TypeScript",
    tags: ["Microsoft 365", "Graph API", "Office"],
    requirements: ["oauth", "account-binding", "remote-transport"],
    usageExamples: ["汇总 Outlook 邮件和日程", "读取 OneDrive 中的工作文件"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "obsidian-mcp",
    name: "Obsidian MCP",
    repoName: "bitbonsai/mcpvault",
    repoUrl: "https://github.com/bitbonsai/mcpvault",
    category: "效率与协作",
    description: "搜索、读取和维护 Obsidian Vault 中的 Markdown 笔记、标签与元数据。",
    readmeSummary: "MCPVault 0.15.0 直接接收 Vault 路径，开放笔记读取、覆盖、移动、标签修改和确认删除。",
    language: "TypeScript",
    tags: ["Obsidian", "Markdown", "本地笔记"],
    requirements: ["desktop-host", "system-permission"],
    usageExamples: ["在笔记库中检索主题", "为笔记补充标签和 frontmatter"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "elevenlabs-mcp",
    name: "ElevenLabs MCP",
    repoName: "elevenlabs/elevenlabs-mcp",
    repoUrl: "https://github.com/elevenlabs/elevenlabs-mcp",
    category: "多媒体",
    description: "调用 ElevenLabs 生成语音、音效并管理语音内容。",
    readmeSummary: "ElevenLabs 官方 MCP Server，把语音合成和音频生成能力提供给 AI。",
    language: "Python",
    tags: ["ElevenLabs 官方", "语音合成", "音频生成"],
    requirements: ["external-runtime", "token", "account-binding", "remote-transport"],
    usageExamples: ["把中文脚本生成旁白", "为短视频生成环境音效"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "minimax-mcp",
    name: "MiniMax MCP",
    repoName: "MiniMax-AI/MiniMax-MCP",
    repoUrl: "https://github.com/MiniMax-AI/MiniMax-MCP",
    category: "多媒体",
    description: "调用 MiniMax 的文本转语音、图像生成和视频生成 API。",
    readmeSummary: "MiniMax 官方 MCP Server，为多模态内容生产提供统一工具接口。",
    language: "Python",
    tags: ["MiniMax 官方", "图像生成", "视频生成"],
    requirements: ["external-runtime", "token", "account-binding", "remote-transport"],
    usageExamples: ["为营销脚本生成配音", "根据提示词生成短视频素材"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "manim-mcp",
    name: "Manim MCP",
    repoName: "abhiemj/manim-mcp-server",
    repoUrl: "https://github.com/abhiemj/manim-mcp-server",
    category: "多媒体",
    description: "用 Manim 生成数学、科学和技术主题的程序化动画。",
    readmeSummary: "社区 Python MCP Server，把动画脚本生成与 Manim 渲染流程连接起来。",
    language: "Python",
    tags: ["Manim", "数学动画", "程序化视频"],
    requirements: ["external-runtime", "system-permission"],
    usageExamples: ["生成函数变化的教学动画", "制作算法原理演示视频"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "ableton-mcp",
    name: "Ableton MCP",
    repoName: "MCPBlender/ableton-mcp",
    repoUrl: "https://github.com/MCPBlender/ableton-mcp",
    category: "多媒体",
    description: "让 AI 控制 Ableton Live 的轨道、设备和音乐制作流程。",
    readmeSummary: "社区 1.3.5 集成，需要 Ableton Live Remote Script 与 localhost:9000 桥共同运行并修改真实项目。",
    language: "Python",
    tags: ["Ableton Live", "音乐制作", "桌面宿主"],
    requirements: ["desktop-host", "external-runtime", "system-permission"],
    usageExamples: ["创建和编排 MIDI 轨道", "调整项目中的设备参数"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "xiaohongshu-mcp",
    name: "小红书 MCP",
    repoName: "xpzouying/xiaohongshu-mcp",
    repoUrl: "https://github.com/xpzouying/xiaohongshu-mcp",
    category: "社交与内容",
    description: "围绕小红书内容搜索、发布和账号操作提供 MCP 工具。",
    readmeSummary: "当前发布依赖本机 Chromium、Cookie/QR 登录和绝对媒体路径，并包含评论、收藏及图文/视频发布。",
    language: "Go",
    tags: ["小红书", "内容运营", "中文服务"],
    requirements: ["desktop-host", "account-binding", "system-permission"],
    usageExamples: ["整理选题相关的公开笔记", "准备待人工确认的内容草稿"],
    sources: ["awesome-mcp-zh"],
  }),
  plannedMcp({
    id: "basic-memory-mcp",
    name: "Basic Memory",
    repoName: "basicmachines-co/basic-memory",
    repoUrl: "https://github.com/basicmachines-co/basic-memory",
    category: "知识与记忆",
    description: "从本地 Markdown 文件构建语义知识图谱，保存跨对话长期记忆。",
    readmeSummary: "本地优先知识管理项目，用 Markdown、实体关系和时间线组织代理记忆。",
    language: "Python",
    tags: ["本地优先", "Markdown", "语义知识图谱"],
    requirements: ["external-runtime", "system-permission"],
    usageExamples: ["保存项目决策和约束", "从历史笔记召回相关上下文"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "graphlit-mcp",
    name: "Graphlit MCP",
    repoName: "graphlit/graphlit-mcp-server",
    repoUrl: "https://github.com/graphlit/graphlit-mcp-server",
    category: "知识与记忆",
    description: "摄入网站、Slack、Google Drive、GitHub 等内容，并进行搜索与知识检索。",
    readmeSummary: "Graphlit 官方 MCP Server，为多源内容摄入、索引和 RAG 检索提供工具。",
    language: "TypeScript",
    tags: ["Graphlit 官方", "多源摄入", "RAG"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["把团队资料汇入知识项目", "跨多种来源检索证据"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "pinecone-assistant-mcp",
    name: "Pinecone Assistant MCP",
    repoName: "pinecone-io/assistant-mcp",
    repoUrl: "https://github.com/pinecone-io/assistant-mcp",
    category: "知识与记忆",
    description: "连接 Pinecone Assistant，从其知识引擎检索上下文和答案。",
    readmeSummary: "Pinecone 官方 MCP Server，把托管知识助手作为代理的检索来源。",
    language: "Rust",
    tags: ["Pinecone 官方", "向量检索", "知识助手"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询企业知识助手", "为回答补充向量检索上下文"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "mem0-mcp",
    name: "Mem0 MCP",
    repoName: "mem0ai/mem0-mcp",
    repoUrl: "https://github.com/mem0ai/mem0-mcp",
    category: "知识与记忆",
    description: "保存、检索和管理编码偏好、技术模式与长期代理记忆。",
    readmeSummary: "Mem0 官方 MCP 集成，为编码代理提供语义化长期记忆工具。",
    language: "Python",
    tags: ["Mem0 官方", "长期记忆", "编码偏好"],
    requirements: ["external-runtime", "token", "account-binding"],
    usageExamples: ["记住项目编码规范", "在新会话中召回历史实现偏好"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "cognee-mcp",
    name: "Cognee MCP",
    repoName: "topoteretes/cognee",
    repoUrl: "https://github.com/topoteretes/cognee/tree/main/cognee-mcp",
    category: "知识与记忆",
    description: "摄入多种数据源并构建 GraphRAG 记忆，支持检索和数据处理。",
    readmeSummary: "Cognee 的 MCP 模块，组合知识图谱和向量存储管理代理记忆。",
    language: "Python",
    tags: ["Cognee", "GraphRAG", "数据摄入"],
    requirements: ["external-runtime", "database-credentials", "system-permission"],
    usageExamples: ["把项目文档构建为 GraphRAG", "检索实体之间的知识关系"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "hindsight-mcp",
    name: "Hindsight",
    repoName: "vectorize-io/hindsight",
    repoUrl: "https://github.com/vectorize-io/hindsight",
    category: "知识与记忆",
    description: "用语义、关键词、图和时间检索策略管理代理长期记忆。",
    readmeSummary: "Vectorize 开源长期记忆系统，提供 retain、recall 与 reflect 等操作。",
    language: "Python",
    tags: ["长期记忆", "混合检索", "Vectorize"],
    requirements: ["external-runtime", "database-credentials", "system-permission"],
    usageExamples: ["保留任务经验供后续调用", "对历史记忆进行反思归纳"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "graphiti-mcp",
    name: "Graphiti MCP",
    repoName: "getzep/graphiti",
    repoUrl: "https://github.com/getzep/graphiti",
    category: "知识与记忆",
    description: "构建实时、时序感知的知识图谱，记录实体关系随时间的变化。",
    readmeSummary: "Zep 开源知识图谱框架，内置 MCP Server 供代理读写时序记忆。",
    language: "Python",
    tags: ["Zep", "时序知识图谱", "代理记忆"],
    requirements: ["external-runtime", "database-credentials", "system-permission"],
    usageExamples: ["记录客户关系的时间变化", "查询某决策在当时的有效上下文"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "snyk-mcp",
    name: "Snyk MCP",
    repoName: "snyk/studio-mcp",
    repoUrl: "https://github.com/snyk/studio-mcp",
    category: "安全分析",
    description: "把 Snyk 安全引擎接入代理工作流，扫描并解释代码和依赖漏洞。",
    readmeSummary: "Snyk 官方 MCP 集成，面向生成代码的实时安全检查和漏洞修复。",
    language: "TypeScript",
    tags: ["Snyk 官方", "依赖漏洞", "代码安全"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["扫描新引入依赖的风险", "解释并建议修复已知漏洞"],
    sources: ["awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "virustotal-mcp",
    name: "VirusTotal MCP",
    repoName: "BurtTheCoder/mcp-virustotal",
    repoUrl: "https://github.com/BurtTheCoder/mcp-virustotal",
    category: "安全分析",
    description: "查询 VirusTotal API，分析 URL、文件哈希、IP 和域名报告。",
    readmeSummary: "社区安全情报 MCP Server，提供常见 VirusTotal 查询工具。",
    language: "TypeScript",
    tags: ["VirusTotal", "威胁情报", "哈希分析"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询可疑文件哈希", "检查 URL 的安全报告"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "shodan-mcp",
    name: "Shodan MCP",
    repoName: "BurtTheCoder/mcp-shodan",
    repoUrl: "https://github.com/BurtTheCoder/mcp-shodan",
    category: "安全分析",
    description: "查询 Shodan 与 CVEDB，进行 IP、设备、DNS 和漏洞情报检索。",
    readmeSummary: "社区 Shodan MCP Server，面向联网资产与公开安全数据查询。",
    language: "TypeScript",
    tags: ["Shodan", "资产情报", "CVE"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询公开 IP 的服务信息", "检索某 CVE 的暴露线索"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "ghidra-mcp",
    name: "Ghidra MCP",
    repoName: "13bm/GhidraMCP",
    repoUrl: "https://github.com/13bm/GhidraMCP",
    category: "安全分析",
    description: "连接 Ghidra 执行函数检查、反编译、内存探索和二进制分析。",
    readmeSummary: "v0.2.2+ghidra12.0.4 插件与本地桥公开 70 个查询/修改工具，包括字节补丁和内存权限修改。",
    language: "Python / Java",
    tags: ["Ghidra", "逆向工程", "二进制分析"],
    requirements: ["desktop-host", "external-runtime", "system-permission"],
    usageExamples: ["解释反编译函数", "查找二进制导入和调用关系"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "binary-ninja-mcp",
    name: "Binary Ninja MCP",
    repoName: "fosdickio/binary_ninja_mcp",
    repoUrl: "https://github.com/fosdickio/binary_ninja_mcp",
    category: "安全分析",
    description: "桥接 Binary Ninja 与 AI，辅助反汇编、反编译和逆向分析。",
    readmeSummary: "GPL-3.0 的 v1.2.1 插件与 localhost 桥依赖商业宿主，并同时公开反编译读取和数据库修改能力。",
    language: "Python",
    tags: ["Binary Ninja", "逆向工程", "桌面插件"],
    requirements: ["desktop-host", "external-runtime", "system-permission"],
    usageExamples: ["分析函数控制流", "辅助理解可疑二进制"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "google-maps-mcp",
    name: "Google Maps MCP",
    repoName: "modelcontextprotocol/servers-archived",
    repoUrl: "https://github.com/modelcontextprotocol/servers-archived/tree/main/src/google-maps",
    category: "地理与出行",
    description: "查询地点、路线、距离和地点详情，为出行规划提供地图信息。",
    readmeSummary: "MCP 官方归档参考实现，展示 Google Maps API 的位置工具接入。",
    language: "TypeScript",
    tags: ["Google Maps", "路线规划", "官方归档"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["比较两个地点的通勤路线", "查询附近地点详情"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "geowire-mcp",
    name: "GeoWire MCP",
    repoName: "geowire/geowire",
    repoUrl: "https://github.com/geowire/geowire",
    category: "地理与出行",
    description: "整合 OpenStreetMap、OSRM 等位置数据，提供地理编码、路线和商圈分析。",
    readmeSummary: "位置智能网关，支持多数据源合并、来源标注与成本策略。",
    language: "TypeScript",
    tags: ["OpenStreetMap", "地理编码", "商圈分析"],
    requirements: ["remote-transport", "system-permission"],
    usageExamples: ["查询地点并计算路线", "分析指定区域的设施密度"],
    sources: ["awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "airbnb-mcp",
    name: "Airbnb MCP",
    repoName: "openbnb-org/mcp-server-airbnb",
    repoUrl: "https://github.com/openbnb-org/mcp-server-airbnb",
    category: "地理与出行",
    description: "搜索 Airbnb 房源并读取列表详情，辅助住宿研究。",
    readmeSummary: "社区 Airbnb 数据工具，通过远程网页服务提供房源搜索与详情。",
    language: "TypeScript",
    tags: ["Airbnb", "住宿搜索", "旅行规划"],
    requirements: ["remote-transport", "system-permission"],
    usageExamples: ["比较目的地的住宿选择", "整理符合条件的房源清单"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "stripe-mcp",
    name: "Stripe MCP",
    repoName: "stripe/ai",
    repoUrl: "https://github.com/stripe/ai",
    category: "金融与市场",
    description: "查询和管理 Stripe 客户、支付、订阅、退款与账单对象。",
    readmeSummary: "Stripe 官方 Agent Toolkit 包含 MCP 支持，为支付业务提供结构化工具。",
    language: "TypeScript",
    tags: ["Stripe 官方", "支付", "订阅账单"],
    requirements: ["token", "account-binding", "remote-transport"],
    usageExamples: ["查询支付和退款状态", "汇总客户订阅信息"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "alpaca-mcp",
    name: "Alpaca MCP",
    repoName: "alpacahq/alpaca-mcp-server",
    repoUrl: "https://github.com/alpacahq/alpaca-mcp-server",
    category: "金融与市场",
    description: "连接 Alpaca 市场数据和交易 API，查询行情、账户与订单。",
    readmeSummary: "Alpaca 官方 MCP Server，涉及真实金融账户与交易权限，必须严格隔离。",
    language: "Python",
    tags: ["Alpaca 官方", "市场数据", "交易"],
    requirements: ["external-runtime", "token", "account-binding", "remote-transport"],
    usageExamples: ["查询市场行情和持仓", "在人工确认前生成订单草案"],
    sources: ["awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "fetch-mcp",
    name: "Fetch MCP",
    repoName: "modelcontextprotocol/servers",
    repoUrl: "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    category: "通用工具",
    description: "获取公开 URL 内容并转换为适合语言模型阅读的文本。",
    readmeSummary: "MCP 官方归档 Python 参考实现，提供受 robots.txt 约束的网页获取工具。",
    language: "Python",
    tags: ["官方归档", "网页获取", "文本转换"],
    requirements: ["external-runtime", "remote-transport"],
    usageExamples: ["读取公开文档页面", "把网页正文转换为 Markdown"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
  plannedMcp({
    id: "calculator-mcp",
    name: "Calculator MCP",
    repoName: "wrtnlabs/calculator-mcp",
    repoUrl: "https://github.com/wrtnlabs/calculator-mcp",
    category: "通用工具",
    description: "执行确定性的数学计算，减少语言模型直接心算产生的误差。",
    readmeSummary: "社区 Node.js 计算器 MCP Server，提供基础数学运算工具；模镜使用固定 Python 兼容适配器复现其首批工具契约。",
    language: "TypeScript",
    tags: ["计算器", "确定性计算", "数学工具"],
    requirements: ["external-runtime"],
    usageExamples: ["计算复合公式结果", "在工作流中校验数值"],
    sources: ["awesome-mcp-zh", "awesome-mcp-servers"],
  }),
];

const approvedCatalogExpansionV2Seeds: McpProjectSeed[] =
  mcpCatalogExpansionV2.map(
    ({ adaptation: _adaptation, requirements, tags, usageExamples, sources, ...input }) =>
      plannedMcp({
        ...input,
        requirements: [...requirements],
        tags: [...tags],
        usageExamples: [...usageExamples],
        sources: [...sources],
      }),
  );

const originalRequirements: Partial<Record<string, McpRequirement[]>> = {
  "playwright-mcp": ["external-runtime", "system-permission"],
  "chrome-devtools-mcp": ["external-runtime", "system-permission"],
  opentabs: ["desktop-host", "system-permission"],
  "sentry-mcp": ["oauth", "account-binding", "remote-transport"],
  "python-interpreter": ["external-runtime", "system-permission"],
  "github-mcp-server": ["oauth", "token", "account-binding", "remote-transport"],
  "git-mcp": ["external-runtime", "system-permission"],
  dbhub: ["database-credentials", "system-permission"],
  "markitdown-mcp": ["external-runtime", "system-permission"],
  "excel-mcp-server": ["external-runtime", "system-permission"],
  "grafana-mcp": ["token", "account-binding", "remote-transport"],
  "notion-mcp-server": ["token", "account-binding", "remote-transport"],
  "blender-mcp": ["desktop-host", "external-runtime", "system-permission"],
  "bibigpt-mcp": ["oauth", "token", "account-binding", "remote-transport"],
  "mcp-cn-commerce": ["token", "account-binding", "remote-transport"],
  chatcrystal: ["desktop-host", "system-permission"],
  "zotero-mcp": ["desktop-host", "token", "account-binding"],
  "semgrep-mcp": ["external-runtime", "token", "system-permission"],
  "time-mcp": ["external-runtime"],
};

function normalizeMcpProject(seed: McpProjectSeed): McpProject {
  const adaptation = getMcpAdaptation(seed.id);
  const isReady = adaptation.availability === "ready";
  const isLocalStdio =
    isReady && adaptation.connectionKind === "local-stdio";
  const isBundledSandbox =
    isReady && adaptation.connectionKind === "sandboxed-stdio";
  const isPublicSandbox = isBundledSandbox && adaptation.wave === 2;
  const isFileSandbox = isBundledSandbox && adaptation.wave === 3;
  const isCredentialSandbox =
    isBundledSandbox &&
    (adaptation.wave === 4 ||
      adaptation.wave === 13 ||
      adaptation.wave === 14 ||
      adaptation.wave === 15);
  const isDatabaseSandbox = isBundledSandbox && adaptation.wave === 5;
  const isDatabaseFileSandbox = isDatabaseSandbox && seed.id === "duckdb-mcp";
  const isStatefulSaas = isReady && adaptation.wave === 6;
  const isBrowserSandbox = isBundledSandbox && adaptation.wave === 7;
  const requirements: McpRequirement[] = isCredentialSandbox
    ? seed.id === "blazickjp-arxiv-mcp-server"
      ? []
      : ["token"]
    : isDatabaseSandbox
      ? seed.id === "supabase-mcp"
        ? ["token"]
        : isDatabaseFileSandbox
          ? []
          : ["database-credentials"]
    : isStatefulSaas
      ? ["token", "account-binding", "remote-transport"]
    : isReady
    ? []
    : (seed.requirements ?? originalRequirements[seed.id] ?? ["external-runtime"]);
  const requirementText = requirements
    .map((requirement) => mcpRequirementLabels[requirement])
    .join("、");

  return {
    ...seed,
    verifiedAt: adaptation.wave === 8 ? "2026-08-09" : seed.verifiedAt,
    installMode: isLocalStdio ? "one-click" : "manual",
    installCommand: isLocalStdio
      ? seed.installCommand
      : isPublicSandbox
        ? "内置公网适配器由服务端固定部署，不接受自定义命令、端点或 Header。"
        : isDatabaseSandbox
        ? "内置数据库适配器由服务端固定部署，不接受 DSN、URI、命令或宿主路径。"
        : isStatefulSaas
          ? "内置有状态 SaaS 适配器由服务端固定部署，不接受任意 URL、Header、命令或环境变量。"
        : isBrowserSandbox
          ? "内置浏览器适配器由服务端固定部署，不接受浏览器命令、启动参数、代理、CDP 地址或宿主路径。"
        : isBundledSandbox
          ? "内置隔离适配器由服务端固定部署，无需安装命令。"
        : "当前版本仅收录资料，不提供本地 stdio 安装或外站认证入口。",
    installNote: isPublicSandbox
      ? "适配器在独立非 root、只读公网 sidecar 中运行；出口域名、DNS、重定向、超时和响应大小均由服务端控制。"
      : isFileSandbox
        ? "先在卡片中创建受控工作区并上传文件；封存后输入只读，写入和持久记忆操作由一次性确认保护。"
      : isCredentialSandbox
        ? seed.id === "blazickjp-arxiv-mcp-server"
          ? "无需凭据；服务端通过固定出口读取公开 arXiv 元数据，下载、全文读取和本地缓存工具均不可用。"
          : "直接在当前卡片的“加密凭据”区域保存 Token；凭据按项目和固定槽位隔离，固定出口、只读工具与撤销失效均由服务端执行。"
      : isDatabaseFileSandbox
        ? "先上传并封存本地 DuckDB 文件；适配器断网运行且只读打开数据文件，不接入 MotherDuck 或宿主路径。"
      : isDatabaseSandbox
        ? seed.id === "supabase-mcp"
          ? "在当前卡片填写 20 位小写英文字母 project_ref（不含数字）并保存加密 PAT；仅使用本地 stdio 和项目范围只读能力，不跳转远程 OAuth。"
          : "在当前卡片分别填写主机、端口、库名、TLS 和用户名，并保存加密数据库凭据；连接时自动执行目标校验和代表性只读预检。"
      : isStatefulSaas
        ? seed.id === "gitlab-mcp"
          ? "在当前卡片加密保存 Personal Access Token 并填写项目 ID；首批主机固定为 gitlab.com，写入先预览目标再逐次确认。"
          : seed.id === "notion-mcp-server"
            ? "在当前卡片加密保存 Integration Token，并填写已显式共享给该 Integration 的 Data Source ID；写入仅限该范围内的新建页面与页面属性更新。"
           : "在当前卡片加密保存账号凭据并填写服务端声明的资源 ID；连接预检通过后，写入先预览目标再逐次确认。"
      : isBrowserSandbox
        ? "连接会创建临时匿名 Chromium 配置；只允许 DNS 固定后的公网 HTTP/HTTPS 目标（80/443 端口），跨 origin 请求与重定向拒绝，首版仅生成截图产物。"
      : isBundledSandbox
        ? "适配器随断网 Python 沙箱镜像固定部署；浏览器不会提交命令、目录或环境变量。"
      : seed.installNote,
    availability: adaptation.availability,
    connectionKind: adaptation.connectionKind,
    adaptationWave: adaptation.wave,
    risk: adaptation.risk,
    requiredCapabilities: adaptation.requiredCapabilities,
    adaptationLimitations: adaptation.limitations,
    requirements,
    configGuide: isCredentialSandbox
      ? [
          "在条目卡片的“加密凭据”区域点击“添加加密凭据”，填写名称和 Token/API Key；明文仅用于服务端加密保存，不会回显。",
          "凭据自动绑定当前 MCP 和固定槽位，不能跨项目复用；如有 Stack、区域等字段，仅填写卡片提供的受控配置。",
          "保存配置后连接 Server；连接只代表传输可用，首次只读工具调用成功后才标记凭据已验证。撤销凭据会立即断开关联会话。",
        ]
      : isDatabaseFileSandbox
        ? [
            "在当前卡片新建受控工作区，上传本地 .duckdb 文件；页面不会读取或提交宿主目录路径。",
            "完成预检后封存并绑定工作区。封存输入只读，适配器默认断网且不连接 MotherDuck。",
            "连接 Server 时会自动执行表结构和代表性只读预检；“传输通道”和“数据源验证”会分别显示。",
          ]
      : isDatabaseSandbox
        ? [
            seed.id === "supabase-mcp"
              ? "填写 20 位小写英文字母 project_ref（不含数字），并在“加密数据库凭据”中保存当前项目的 PAT；不使用远程 OAuth。"
              : "分别填写卡片提供的主机、端口、库名、TLS 和用户名；不粘贴 DSN、URI、命令或环境变量。",
            "在当前卡片创建并选择对应的加密数据库凭据。凭据按项目和固定槽位隔离，保存后不回显明文。",
            "保存配置后连接只读 sidecar；连接时会自动校验目标并执行代表性只读预检。写入和管理工具始终关闭。",
          ]
      : isStatefulSaas
        ? [
            "在当前卡片添加并选择专属加密凭据；明文只提交一次，不能跨 MCP 或槽位复用。",
            seed.id === "gitlab-mcp"
              ? "填写服务端提供的项目 ID 字段；主机固定为 gitlab.com，不输入 URL、Header、命令或环境变量。"
              : seed.id === "notion-mcp-server"
                ? "填写固定 Data Source ID，并先在 Notion 中将该 Data Source 显式共享给 Integration；页面不接收 Notion URL。"
              : "填写服务端提供的账号与资源 ID 字段；当前没有资源发现接口，因此只显示受控 ID 输入，不虚构选择器。",
            "确认单租户边界并保存配置，然后连接并等待账号预检通过。只读工具可直接执行，写入工具必须查看目标与影响摘要后一次性确认。",
            "出现限流或结果未知时不要重复点击；先核对上游状态。解绑可只断开账号，也可同时撤销模镜内的加密凭据。",
          ]
      : isBrowserSandbox
        ? [
            "点击“连接 Server”创建一次性匿名浏览器会话；会话最多 15 分钟，闲置 5 分钟自动结束，单页最多执行 50 次操作。",
            "导航只接受公网 HTTP/HTTPS 地址和 80/443 端口；URL 查询参数不得携带 Token、API Key、签名或其他凭据，DNS 固定后连接，跨 origin 请求与重定向直接拒绝。",
            "页面状态修改必须查看目标域和动作影响摘要后确认执行一次；本批不采集账号凭据、不提供外站登录流程或保存登录态。页面仍可能自行呈现登录界面，请勿输入账号、密码、OTP 等认证信息；同时不提供网页上传/下载、剪贴板、本机文件、Cookie/Storage 导入导出与持久化、任意脚本求值或 CDP 工具。",
            "截图进入卡片内的临时产物列表，可下载或清理；断开连接会终止浏览器进程并删除临时配置。",
          ]
      : seed.configGuide ??
        (isLocalStdio
        ? [
            "点击“安装 Server”，由模镜记录并准备已核验的 npm stdio 包。",
            "安装完成后点击“连接 Server”，后端会在 MCP 沙盒目录启动进程。",
            "连接成功后展开工具表单，确认参数范围再执行；随时可以断开连接。",
          ]
        : isPublicSandbox
          ? [
              "无需安装 npm、Python 包或外部运行时；点击“连接 Server”启动服务端固定适配器。",
              "Fetch 仅访问用户明确提供的公网 HTTPS；其他项目只访问卡片列出的固定公共域名，每次重定向都会重新校验。",
              "调用受超时、robots.txt、速率、响应大小和只读工具策略限制；被策略拒绝时不会回退到不受控连接。",
            ]
        : isFileSandbox
          ? [
              "新建受控工作区，上传单个文件、文件夹或安全 ZIP；页面不会提交宿主目录路径。",
              "封存并绑定工作区后再连接 Server；工具中的文件参数只能从已上传文件列表选择。",
              "输入文件始终只读；表格写入和记忆修改会显示中文影响摘要并要求一次性确认，产物可下载和清理。",
            ]
        : isBundledSandbox
          ? [
              "无需安装 Python、uv、npm 或上游包；点击“连接 Server”即可启动受控适配器。",
              "每次连接都在断网、非 root、只读文件系统的临时沙箱内运行，结束后自动清理。",
              "工具参数、运行时间和返回大小均有限制；超出安全范围时会返回中文错误并拒绝执行。",
            ]
        : [
            `先确认接入条件：${requirementText}。`,
            "当前模镜不会收集凭证、打开外站认证，也不会启动额外运行时或桌面宿主。",
            "待对应适配器和安全校验完成后，再开放安装、授权与连接测试。",
          ]),
    usageExamples:
      seed.usageExamples ?? [
        `在工作流中处理“${seed.tags[0]}”相关任务。`,
        `结合“${seed.tags[1] ?? seed.category}”场景补充专用工具能力。`,
      ],
    sources: seed.sources ?? ["awesome-mcp-zh"],
  };
}

export const mcpProjects: McpProject[] = [
  ...originalMcpProjectSeeds,
  ...expandedMcpProjectSeeds,
  ...approvedCatalogExpansionV2Seeds,
].map(normalizeMcpProject);

import {
  REJECTED_OFFICIAL_SKILL_INSTALL_SOURCES,
  VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES,
} from "./officialSkillInstallSources.generated.ts";

export const SKILL_CATEGORIES = [
  "AI 与智能体",
  "开发与测试",
  "数据与研究",
  "自动化与集成",
  "设计与多媒体",
  "内容与办公",
  "营销与增长",
  "产品与协作",
  "安全与运维",
  "商业与专业服务",
] as const;

export type SkillCategory = (typeof SKILL_CATEGORIES)[number];
export type SkillInstallStatus = "ready" | "manual" | "pending" | "reference";

interface SkillPolicyInput {
  name: string;
  description: string;
  category?: string;
  sourceGroup?: string;
  tags?: string[];
}

const SOURCE_GROUP_CATEGORIES = new Map<string, SkillCategory>([
  ["Development and Testing", "开发与测试"],
  ["Data and Analysis", "数据与研究"],
  ["Business and Marketing", "营销与增长"],
  ["Content and Communication", "内容与办公"],
  ["Creative and Media", "设计与多媒体"],
  ["Productivity and Collaboration", "产品与协作"],
  ["Security Skills by Trail of Bits Team", "安全与运维"],
  ["Product Manager Skills by Dean Peters", "产品与协作"],
  ["Product Management Skills by Pawel Huryn", "产品与协作"],
  ["Marketing Skills by Corey Haines", "营销与增长"],
  ["Advertising Skills by Kim Barrett", "营销与增长"],
]);

const CATEGORY_RULES: Array<[SkillCategory, RegExp]> = [
  [
    "安全与运维",
    /安全|合规|security|secure|vulnerab|threat|pentest|malware|compliance|incident|privacy|forensic/i,
  ],
  [
    "商业与专业服务",
    /金融|股票|合同|法律|简历|招聘|电商|finance|financial|stock|trading|bank|legal|law\b|contract|resume|recruit|hiring|commerce|shopify|health|medical|real estate/i,
  ],
  [
    "营销与增长",
    /营销|发布|社交|marketing|seo\b|copywriting|social|advertis|campaign|brand|newsletter|growth|sales|customer|lead generation|conversion/i,
  ],
  [
    "自动化与集成",
    /浏览器与自动化|自动化|集成|automation|workflow|integration|browser|scraping|crawler|webhook|zapier|make\.com|n8n|connector/i,
  ],
  [
    "设计与多媒体",
    /界面与设计|视频创作|语音与数字人|文化创作|PPT 与演示|image|video|audio|music|voice|animation|3d\b|blender|remotion|design|figma|creative|illustrat|art\b|media|game|pptx|slides?|presentation/i,
  ],
  [
    "内容与办公",
    /内容创作|文档|知识管理|办公|document|docs?\b|docx|pdf\b|xlsx|spreadsheet|excel|notion|workspace|meeting|calendar|email|knowledge|markdown|writing|editorial|content/i,
  ],
  [
    "数据与研究",
    /分析|研究|data|analytics|analysis|visuali[sz]ation|metric|statistics|forecast|research|dataset|machine learning|jupyter|bi\b/i,
  ],
  [
    "产品与协作",
    /产品|协作|项目|product|roadmap|project|agile|scrum|jira|linear|requirements|prd\b|user stor|prioriti[sz]|stakeholder|collaboration/i,
  ],
  [
    "AI 与智能体",
    /智能体|agent|llm\b|generative ai|prompt|rag\b|mcp\b|embedding|inference|hugging face|gemini|claude|openai/i,
  ],
  [
    "安全与运维",
    /运维|devops|deploy|docker|kubernetes|terraform|infrastructure|observability|serverless|cloudflare|aws\b|azure\b|gcp\b/i,
  ],
  [
    "开发与测试",
    /工程开发|测试|前端|后端|数据库|code|coding|developer|development|git\b|github|refactor|architecture|framework|sdk\b|cli\b|typescript|javascript|python|java\b|rust\b|golang|ruby|php\b|\.net|test|testing|qa\b|playwright|cypress|pytest|database|sql\b|api\b|frontend|react|next\.js|mobile/i,
  ],
];

const DESCRIPTION_RULES: Array<[RegExp, string]> = [
  [/\bdocx\b|word document/i, "创建、编辑和检查 Word 文档"],
  [/\bpdf\b/i, "提取、整理和生成 PDF 内容"],
  [/\bxlsx\b|spreadsheet|excel/i, "分析表格并生成可复用的工作簿"],
  [/\bpptx\b|slides?|presentation/i, "规划、制作和检查演示文稿"],
  [/security|secure|vulnerab|threat|pentest|malware|compliance|privacy|安全|合规/i, "检查安全风险、权限和合规问题"],
  [/test|testing|qa\b|playwright|cypress|selenium|jest|vitest|pytest|debug|benchmark|测试/i, "编写测试、定位故障并改进代码质量"],
  [/deploy|docker|kubernetes|terraform|cloud|devops|infrastructure|运维|部署/i, "配置部署、容器和云端运行环境"],
  [/database|postgres|mysql|sqlite|sql\b|redis|mongodb|数据库/i, "设计数据库、查询和后端数据流程"],
  [/api\b|graphql|webhook|integration|connector|集成/i, "设计接口并连接外部服务"],
  [/frontend|react|next\.js|vue|svelte|css\b|tailwind|web design|前端|界面/i, "设计并实现网页界面"],
  [/mobile|flutter|expo|ios\b|android\b|react native|移动端/i, "开发和检查移动端应用"],
  [/analytics|analysis|metric|statistics|forecast|dataset|数据|分析/i, "清洗数据、分析指标并呈现结论"],
  [/research|paper|evidence|研究|论文/i, "搜集资料、比较证据并整理结论"],
  [/marketing|seo\b|campaign|newsletter|growth|营销|推广/i, "规划营销内容、搜索优化和增长活动"],
  [/sales|customer|lead generation|conversion|销售|客户/i, "改进销售、客户和转化流程"],
  [/product|roadmap|requirements|prd\b|project|产品|项目/i, "梳理需求、排定优先级并推进项目"],
  [/design|figma|illustrat|image|creative|设计|图像/i, "规划视觉方案并制作设计资源"],
  [/video|remotion|animation|视频|动画/i, "制作、编辑或优化视频"],
  [/audio|voice|music|tts|asr|语音|音频|音乐/i, "处理语音、音频或音乐"],
  [/agent|llm\b|prompt|rag\b|智能体|提示词/i, "设计智能体、提示词和知识检索流程"],
  [/mcp\b/i, "开发或使用 MCP 工具连接"],
  [/automation|workflow|browser|scraping|crawler|自动化/i, "连接工具并自动处理重复任务"],
  [/document|docs?\b|knowledge|notion|markdown|文档|知识/i, "整理文档、知识资料和办公内容"],
  [/content|writing|copywriting|editorial|内容|写作/i, "策划、撰写和发布内容"],
  [/finance|financial|stock|trading|金融|股票/i, "分析财务、市场和投资信息"],
  [/legal|law\b|contract|法律|合同/i, "审阅法律、合同和合规材料"],
  [/commerce|shopify|电商/i, "制作商品内容并优化电商流程"],
  [/collaboration|meeting|team management|协作|会议/i, "组织团队协作、会议和沟通流程"],
];

const CATEGORY_FALLBACKS: Record<SkillCategory, string> = {
  "AI 与智能体": "搭建和改进 AI 智能体工作流",
  "开发与测试": "完成开发、调试和质量检查任务",
  "数据与研究": "整理数据、开展研究并输出结论",
  "自动化与集成": "连接工具并自动处理重复工作",
  "设计与多媒体": "制作和优化视觉或多媒体内容",
  "内容与办公": "处理内容、文档和日常办公资料",
  "营销与增长": "规划营销活动并改进增长效果",
  "产品与协作": "梳理产品需求并推进团队协作",
  "安全与运维": "检查风险并维护系统稳定运行",
  "商业与专业服务": "处理商业分析和专业服务任务",
};

const ANTHROPIC_BATCH_ONE = new Set([
  "algorithmic-art",
  "brand-guidelines",
  "canvas-design",
  "doc-coauthoring",
  "docx",
  "frontend-design",
  "internal-comms",
  "mcp-builder",
  "pdf",
  "pptx",
  "skill-creator",
  "slack-gif-creator",
  "template",
  "theme-factory",
  "web-artifacts-builder",
  "webapp-testing",
  "xlsx",
]);

const OPENAI_BATCH_TWO_MISSING = new Set([
  "develop-web-game",
  "doc",
  "frontend-skill",
  "slides",
  "sora",
  "spreadsheet",
]);

const MICROSOFT_BATCH_THREE_GENERAL = new Set([
  "cloud-solution-architect",
  "continual-learning",
  "copilot-sdk",
  "entra-agent-id",
  "frontend-design-review",
  "github-issue-creator",
  "mcp-builder",
  "podcast-generation",
  "skill-creator",
]);

function officialSkillsParts(sourceUrl: string) {
  let url: URL;
  try {
    url = new URL(sourceUrl);
  } catch {
    return undefined;
  }
  const parts = url.pathname.split("/").filter(Boolean);
  if (url.hostname.toLowerCase() !== "officialskills.sh" || parts.length < 3) {
    return undefined;
  }
  return {
    owner: parts[0].toLowerCase(),
    repository: parts[1].toLowerCase(),
    slug: parts[2],
  };
}

function microsoftSkillPath(slug: string) {
  if (MICROSOFT_BATCH_THREE_GENERAL.has(slug)) return `.github/skills/${slug}`;
  if (slug === "agents-v2-py") return undefined;
  if (slug.endsWith("-dotnet")) return `.github/plugins/azure-sdk-dotnet/skills/${slug}`;
  if (slug.endsWith("-java")) return `.github/plugins/azure-sdk-java/skills/${slug}`;
  if (slug.endsWith("-py")) return `.github/plugins/azure-sdk-python/skills/${slug}`;
  if (slug.endsWith("-rust")) return `.github/plugins/azure-sdk-rust/skills/${slug}`;
  if (slug.endsWith("-ts")) return `.github/plugins/azure-sdk-typescript/skills/${slug}`;
  return undefined;
}

function policyText(input: SkillPolicyInput) {
  return [
    input.name,
    input.description,
    input.category ?? "",
    input.sourceGroup ?? "",
  ].join(" ");
}

function capabilityText(input: SkillPolicyInput) {
  return [input.name, input.description, input.category ?? ""].join(" ");
}

export function classifySkill(input: SkillPolicyInput): SkillCategory {
  const sourceGroupCategory = input.sourceGroup
    ? SOURCE_GROUP_CATEGORIES.get(input.sourceGroup)
    : undefined;
  if (sourceGroupCategory) return sourceGroupCategory;

  const searchable = policyText(input);
  return CATEGORY_RULES.find(([, pattern]) => pattern.test(searchable))?.[0] ?? "开发与测试";
}

export function hasReadableChinese(value: string) {
  return (value.match(/[\u3400-\u9fff]/g) ?? []).length >= 10;
}

function readableTopic(name: string) {
  const leafName = name.split("/").at(-1) ?? name;
  return leafName.replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
}

export function describeSkillInChinese(
  input: SkillPolicyInput,
  category = classifySkill(input),
) {
  const normalizedDescription = input.description.replace(/\s+/g, " ").trim();
  if (hasReadableChinese(normalizedDescription)) {
    return normalizedDescription.length > 180
      ? `${normalizedDescription.slice(0, 177)}...`
      : normalizedDescription;
  }

  const searchable = capabilityText(input);
  const capabilities = DESCRIPTION_RULES.filter(([pattern]) => pattern.test(searchable))
    .map(([, description]) => description)
    .filter((description, index, values) => values.indexOf(description) === index)
    .slice(0, 2);
  const primaryCapability = capabilities[0] ?? CATEGORY_FALLBACKS[category];
  const secondaryCapability = capabilities[1] ? `，并可${capabilities[1]}` : "";
  return `围绕 ${readableTopic(input.name)}，帮助你${primaryCapability}${secondaryCapability}。`;
}

export function auditedInstallSource(sourceUrl: string) {
  const verifiedSource =
    VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES[
      sourceUrl as keyof typeof VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES
    ];
  if (verifiedSource) return verifiedSource;

  const source = officialSkillsParts(sourceUrl);
  if (!source || source.repository !== "skills") return undefined;

  if (source.owner === "anthropics" && ANTHROPIC_BATCH_ONE.has(source.slug)) {
    return {
      repoUrl: "https://github.com/anthropics/skills",
      subPath: source.slug === "template" ? "template" : `skills/${source.slug}`,
    };
  }

  if (source.owner === "openai" && !OPENAI_BATCH_TWO_MISSING.has(source.slug)) {
    return {
      repoUrl: "https://github.com/openai/skills",
      subPath:
        source.slug === "imagegen"
          ? "skills/.system/imagegen"
          : `skills/.curated/${source.slug}`,
    };
  }

  if (source.owner === "microsoft") {
    const subPath = microsoftSkillPath(source.slug);
    if (subPath) {
      return {
        repoUrl: "https://github.com/microsoft/skills",
        subPath,
      };
    }
  }

  return undefined;
}

export function hasAuditedInstallMismatch(sourceUrl: string) {
  if (
    sourceUrl in REJECTED_OFFICIAL_SKILL_INSTALL_SOURCES
  ) {
    return true;
  }
  const source = officialSkillsParts(sourceUrl);
  if (!source || source.repository !== "skills") return false;
  if (source.owner === "getsentry") return true;
  if (source.owner === "openai") return OPENAI_BATCH_TWO_MISSING.has(source.slug);
  return source.owner === "microsoft" && source.slug === "agents-v2-py";
}

export function manualInstallCommand(sourceUrl: string) {
  let url: URL;
  try {
    url = new URL(sourceUrl);
  } catch {
    return "";
  }
  const parts = url.pathname.split("/").filter(Boolean);
  if (url.hostname.toLowerCase() !== "officialskills.sh" || parts.length < 3) return "";
  return `请打开来源页核对其 GitHub 仓库，再执行页面提供的 ${parts[2]} 安装命令。`;
}

export function inferInstallStatus(
  sourceUrl: string,
  hasInstallSource: boolean,
): SkillInstallStatus {
  if (hasInstallSource) return "ready";
  if (hasAuditedInstallMismatch(sourceUrl)) return "pending";
  try {
    const url = new URL(sourceUrl);
    if (url.hostname.toLowerCase() === "officialskills.sh") return "manual";
    if (
      url.hostname.toLowerCase() === "github.com" &&
      url.pathname.split("/").filter(Boolean).length === 2
    ) {
      return "pending";
    }
  } catch {
    return "reference";
  }
  return "reference";
}

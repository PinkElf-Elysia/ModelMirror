import type { SkillInstallStatus } from "./skillCatalogPolicy";
import type {
  SkillInstallSource,
  SkillProject,
  SkillProjectKind,
} from "./skillProjects";
import type { SkillSetMemberSource } from "./skillSetMembers";

export interface SkillNeedCandidate {
  id: string;
  name: string;
  category: string;
  kind: SkillProjectKind;
  description: string;
  sourceDescription?: string;
  searchDescription?: string;
  tags: string[];
  includedSkills?: string[];
  installStatus: SkillInstallStatus;
  publisher?: string;
  sourceGroup?: string;
  pathTerms?: string[];
  parentNames?: string[];
  deprecated?: boolean;
  stableNameOrder?: number;
}

export interface SkillNeedProjectTarget extends SkillNeedCandidate {
  targetType: "project";
  project: SkillProject;
}

export interface SkillNeedMemberTarget extends SkillNeedCandidate {
  targetType: "member";
  member: SkillSetMemberSource;
  installSource: SkillInstallSource;
  primarySkillSet: SkillProject;
  parentSkillSets: SkillProject[];
}

export type SkillNeedTarget = SkillNeedProjectTarget | SkillNeedMemberTarget;

export type SkillNeedMatchReasonType =
  | "name"
  | "included-skill"
  | "tag"
  | "description"
  | "category"
  | "path"
  | "parent"
  | "source";

export type SkillNeedMatchOrigin = "direct" | "expanded";

export interface SkillNeedMatchReason {
  type: SkillNeedMatchReasonType;
  label: string;
  origin: SkillNeedMatchOrigin;
  matchedTerms: string[];
}

export interface SkillNeedMatch<T extends SkillNeedCandidate = SkillNeedCandidate> {
  project: T;
  score: number;
  reasons: SkillNeedMatchReason[];
}

interface IntentGroup {
  label: string;
  trigger: RegExp;
  terms: string[];
  requiredAny?: string[];
}

const INTENT_GROUPS: IntentGroup[] = [
  {
    label: "PDF 文档",
    trigger: /\bpdf\b|便携式文档|合同.*(?:提取|填写|分析)|(?:提取|填写|分析).*合同/i,
    terms: ["pdf", "文档", "合同", "表单", "提取"],
  },
  {
    label: "电子表格",
    trigger: /\bxlsx\b|\bexcel\b|spreadsheet|电子表格|工作簿|表格分析/i,
    terms: ["xlsx", "excel", "spreadsheet", "电子表格", "工作簿", "表格", "csv"],
  },
  {
    label: "网页自动化测试",
    trigger:
      /playwright|cypress|selenium|\be2e\b|(?:网页|网站|web).*(?:测试|自动化)|(?:测试|自动化).*(?:网页|网站|web)/i,
    terms: [
      "playwright",
      "cypress",
      "selenium",
      "e2e",
      "webapp testing",
      "browser testing",
      "网页测试",
      "自动化测试",
      "testing",
      "测试",
    ],
    requiredAny: [
      "playwright",
      "cypress",
      "selenium",
      "e2e",
      "webapp testing",
      "browser testing",
      "网页测试",
      "自动化测试",
      "testing",
      "test",
      "测试",
    ],
  },
  {
    label: "前端开发",
    trigger: /react|next\.js|vue|svelte|tailwind|前端|网页界面|用户界面|\bui\b/i,
    terms: ["react", "next.js", "vue", "svelte", "tailwind", "frontend", "前端", "界面", "ui"],
  },
  {
    label: "数据库",
    trigger: /postgres|postgresql|mysql|sqlite|mongodb|redis|supabase|数据库|\bsql\b/i,
    terms: ["postgres", "postgresql", "mysql", "sqlite", "mongodb", "redis", "supabase", "database", "数据库", "sql"],
  },
  {
    label: "安全审计",
    trigger: /安全|审计|漏洞|渗透|合规|security|secure|audit|vulnerab|pentest|compliance/i,
    terms: ["安全", "审计", "漏洞", "合规", "security", "secure", "audit", "vulnerability", "pentest"],
  },
  {
    label: "数据分析",
    trigger: /数据分析|指标|可视化|统计|预测|analytics|analysis|metric|visuali[sz]|statistics|forecast/i,
    terms: ["数据", "分析", "指标", "可视化", "analytics", "analysis", "metric", "visualization", "statistics"],
  },
  {
    label: "研究",
    trigger: /研究|论文|证据|文献|调研|research|paper|evidence|literature/i,
    terms: ["研究", "论文", "证据", "文献", "research", "paper", "evidence"],
  },
  {
    label: "营销增长",
    trigger: /营销|推广|增长|广告|社交媒体|seo|marketing|campaign|growth|advertis/i,
    terms: ["营销", "推广", "增长", "广告", "seo", "marketing", "campaign", "growth", "social"],
  },
  {
    label: "产品与项目",
    trigger: /产品|项目|需求|路线图|prd|roadmap|product|project|requirements/i,
    terms: ["产品", "项目", "需求", "路线图", "prd", "roadmap", "product", "project", "requirements"],
  },
  {
    label: "自动化与集成",
    trigger: /自动化|工作流|集成|爬取|automation|workflow|integration|scraping|webhook|\bn8n\b/i,
    terms: ["自动化", "工作流", "集成", "automation", "workflow", "integration", "scraping", "webhook", "n8n"],
  },
  {
    label: "智能体与 MCP",
    trigger: /智能体|提示词|知识检索|agent|prompt|\brag\b|\bmcp\b|\bllm\b/i,
    terms: ["智能体", "提示词", "agent", "prompt", "rag", "mcp", "llm"],
  },
  {
    label: "部署与运维",
    trigger: /部署|运维|容器|云服务|deploy|devops|docker|kubernetes|terraform|cloud/i,
    terms: ["部署", "运维", "容器", "deploy", "devops", "docker", "kubernetes", "terraform", "cloud"],
  },
  {
    label: "演示文稿",
    trigger: /pptx|幻灯片|演示文稿|slides?|presentation/i,
    terms: ["pptx", "幻灯片", "演示文稿", "slides", "presentation"],
  },
  {
    label: "图像与设计",
    trigger: /图像|图片|设计|海报|插画|figma|image|design|illustrat|creative/i,
    terms: ["图像", "图片", "设计", "figma", "image", "design", "illustration", "creative"],
  },
  {
    label: "视频与音频",
    trigger: /视频|动画|音频|语音|音乐|video|animation|audio|voice|music/i,
    terms: ["视频", "动画", "音频", "语音", "音乐", "video", "animation", "audio", "voice", "music"],
  },
  {
    label: "移动应用",
    trigger: /移动端|手机应用|ios|android|flutter|expo|react native|mobile/i,
    terms: ["移动端", "ios", "android", "flutter", "expo", "react native", "mobile"],
  },
];

const FIELD_DETAILS: Array<{
  type: SkillNeedMatchReasonType;
  label: string;
  weight: number;
  values: (candidate: SkillNeedCandidate) => string[];
}> = [
  { type: "name", label: "名称", weight: 12, values: (candidate) => [candidate.name] },
  {
    type: "included-skill",
    label: "子技能",
    weight: 11,
    values: (candidate) => candidate.includedSkills ?? [],
  },
  { type: "tag", label: "标签", weight: 10, values: (candidate) => candidate.tags },
  {
    type: "description",
    label: "能力说明",
    weight: 7,
    values: (candidate) => [candidate.description],
  },
  { type: "category", label: "分类", weight: 5, values: (candidate) => [candidate.category] },
  { type: "path", label: "来源路径", weight: 4, values: (candidate) => candidate.pathTerms ?? [] },
  { type: "parent", label: "所属集合", weight: 2.5, values: (candidate) => candidate.parentNames ?? [] },
  {
    type: "source",
    label: "来源说明",
    weight: 3,
    values: (candidate) => [
      candidate.searchDescription ?? candidate.sourceDescription ?? "",
      candidate.publisher ?? "",
      candidate.sourceGroup ?? "",
    ],
  },
];

const CJK_STOP_NGRAMS = new Set([
  "一个",
  "一些",
  "可以",
  "如何",
  "希望",
  "需要",
  "我要",
  "我想",
  "帮我",
  "能够",
  "完成",
  "进行",
]);

function normalize(value: string) {
  return value
    .normalize("NFKC")
    .toLocaleLowerCase("zh-CN")
    .replace(/[_/\\-]+/g, " ")
    .replace(/[^a-z0-9+#.\u3400-\u9fff]+/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function extractQueryTerms(query: string) {
  const normalizedQuery = normalize(query.slice(0, 500));
  const directTerms = new Set(
    normalizedQuery
      .match(/[a-z0-9+#.]{2,}/g)
      ?.filter((term) => !["the", "and", "for", "with", "this", "that"].includes(term)) ?? [],
  );
  for (const chunk of normalizedQuery.match(/[\u3400-\u9fff]{2,}/g) ?? []) {
    const maxSize = Math.min(6, chunk.length);
    for (let size = 2; size <= maxSize; size += 1) {
      for (let index = 0; index <= chunk.length - size; index += 1) {
        const term = chunk.slice(index, index + size);
        if (!CJK_STOP_NGRAMS.has(term)) directTerms.add(term);
      }
    }
  }

  const activeIntents = INTENT_GROUPS.filter((intent) =>
    intent.trigger.test(normalizedQuery),
  );
  const expandedTerms = new Set<string>();
  activeIntents.forEach((intent) =>
    intent.terms.forEach((term) => {
      const normalized = normalize(term);
      if (!directTerms.has(normalized)) expandedTerms.add(normalized);
    }),
  );
  return {
    normalizedQuery,
    directTerms,
    expandedTerms,
    terms: [...directTerms, ...expandedTerms].filter((term) => term.length >= 2),
    activeIntents,
  };
}

function statusBoost(status: SkillInstallStatus) {
  if (status === "ready") return 0.5;
  if (status === "pending") return 0.25;
  if (status === "manual") return 0.1;
  return 0;
}

interface PreparedCandidate<T extends SkillNeedCandidate> {
  candidate: T;
  candidateText: string;
  fields: Array<{ detail: (typeof FIELD_DETAILS)[number]; searchable: string }>;
}

function prepareCandidate<T extends SkillNeedCandidate>(candidate: T): PreparedCandidate<T> {
  const fields = FIELD_DETAILS.map((detail) => ({
    detail,
    searchable: normalize(detail.values(candidate).join(" ")),
  }));
  return {
    candidate,
    fields,
    candidateText: normalize(fields.map((field) => field.searchable).join(" ")),
  };
}

function inverseDocumentFrequencies<T extends SkillNeedCandidate>(
  prepared: PreparedCandidate<T>[],
  terms: string[],
) {
  const frequencies = new Map<string, number>();
  for (const term of terms) {
    let count = 0;
    for (const item of prepared) {
      if (item.candidateText.includes(term)) count += 1;
    }
    frequencies.set(term, Math.log((prepared.length + 1) / (count + 1)) + 1);
  }
  return frequencies;
}

function matchCandidate<T extends SkillNeedCandidate>(
  prepared: PreparedCandidate<T>,
  query: ReturnType<typeof extractQueryTerms>,
  idf: Map<string, number>,
): SkillNeedMatch<T> | undefined {
  if (prepared.candidate.deprecated) return undefined;
  if (
    query.activeIntents.some(
      (intent) =>
        intent.requiredAny &&
        !intent.requiredAny.some((term) =>
          prepared.candidateText.includes(normalize(term)),
        ),
    )
  ) {
    return undefined;
  }
  const reasons: SkillNeedMatchReason[] = [];
  let score = 0;

  for (const field of prepared.fields) {
    if (!field.searchable) continue;
    const matchedTerms = query.terms.filter((term) => field.searchable.includes(term));
    if (matchedTerms.length === 0) continue;
    const uniqueMatches = [...new Set(matchedTerms)].sort((left, right) => {
      const originDifference =
        Number(query.directTerms.has(right)) - Number(query.directTerms.has(left));
      return originDifference || right.length - left.length || left.localeCompare(right, "zh-CN");
    });
    const strongestMatches = uniqueMatches.slice(0, 5);
    const fieldScore = strongestMatches.reduce((total, term) => {
      const originWeight = query.directTerms.has(term) ? 1.25 : 0.7;
      const lengthWeight = 1 + Math.min(term.length, 10) / 20;
      return total + (idf.get(term) ?? 1) * originWeight * lengthWeight;
    }, 0);
    score += field.detail.weight * fieldScore;
    if (
      query.normalizedQuery.length >= 3 &&
      field.searchable.includes(query.normalizedQuery)
    ) {
      score += field.detail.weight * 1.5;
    }
    const hasDirectMatch = strongestMatches.some((term) =>
      query.directTerms.has(term),
    );
    reasons.push({
      type: field.detail.type,
      label: `${field.detail.label}${hasDirectMatch ? "直接匹配" : "关联匹配"}`,
      origin: hasDirectMatch ? "direct" : "expanded",
      matchedTerms: strongestMatches.slice(0, 4),
    });
  }

  if (score < 6 || reasons.length === 0) return undefined;
  return {
    project: prepared.candidate,
    score: Number(score.toFixed(2)),
    reasons: reasons.sort((left, right) => {
      const originDifference =
        Number(right.origin === "direct") - Number(left.origin === "direct");
      if (originDifference !== 0) return originDifference;
      return (
        FIELD_DETAILS.findIndex((field) => field.type === left.type) -
        FIELD_DETAILS.findIndex((field) => field.type === right.type)
      );
    }),
  };
}

export function findSkillsForNeed<T extends SkillNeedCandidate>(
  need: string,
  candidates: readonly T[],
  limit = 6,
): SkillNeedMatch<T>[] {
  const query = extractQueryTerms(need.trim());
  if (!query.normalizedQuery || query.terms.length === 0) return [];
  const safeLimit = Math.max(1, Math.min(24, Math.floor(limit)));
  const prepared = candidates.map(prepareCandidate);
  const idf = inverseDocumentFrequencies(prepared, query.terms);
  return prepared
    .map((candidate) => matchCandidate(candidate, query, idf))
    .filter((match): match is SkillNeedMatch<T> => Boolean(match))
    .sort((left, right) => {
      const adjustedScoreDifference =
        right.score + statusBoost(right.project.installStatus) -
        (left.score + statusBoost(left.project.installStatus));
      if (adjustedScoreDifference !== 0) return adjustedScoreDifference;
      const leftStableOrder = left.project.stableNameOrder;
      const rightStableOrder = right.project.stableNameOrder;
      if (
        typeof leftStableOrder === "number" &&
        typeof rightStableOrder === "number" &&
        leftStableOrder !== rightStableOrder
      ) {
        return leftStableOrder - rightStableOrder;
      }
      const nameDifference = left.project.name.localeCompare(
        right.project.name,
        "zh-CN",
      );
      return nameDifference || left.project.id.localeCompare(right.project.id, "en");
    })
    .slice(0, safeLimit);
}

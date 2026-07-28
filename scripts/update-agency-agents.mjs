import { promises as fs } from "node:fs";
import path from "node:path";
import process from "node:process";

const UPSTREAM_COMMIT = "2ecfabf8e944ccdfed63ad8c44d5241290af6977";
const EXPECTED_AGENT_COUNT = 268;
const REPOSITORY_URL = "https://github.com/jnMetaCode/agency-agents-zh";
const AGENT_DIRECTORIES = [
  "academic",
  "design",
  "engineering",
  "finance",
  "game-development",
  "gis",
  "hr",
  "legal",
  "marketing",
  "paid-media",
  "product",
  "project-management",
  "sales",
  "security",
  "spatial-computing",
  "specialized",
  "supply-chain",
  "support",
  "testing",
];

const departmentMeta = {
  academic: { label: "学术部", emoji: "📚", color: "#10B981" },
  design: { label: "设计部", emoji: "🎨", color: "#EC4899" },
  engineering: { label: "工程部", emoji: "💻", color: "#06B6D4" },
  finance: { label: "金融部", emoji: "💰", color: "#84CC16" },
  "game-development": { label: "游戏开发部", emoji: "🎮", color: "#8B5CF6" },
  gis: { label: "GIS 部", emoji: "🗺️", color: "#14B8A6" },
  hr: { label: "人力资源部", emoji: "🤝", color: "#F97316" },
  legal: { label: "法务部", emoji: "⚖️", color: "#A78BFA" },
  marketing: { label: "营销部", emoji: "📣", color: "#F43F5E" },
  "paid-media": { label: "付费媒体部", emoji: "📈", color: "#FB7185" },
  product: { label: "产品部", emoji: "🧭", color: "#38BDF8" },
  "project-management": { label: "项目管理部", emoji: "📋", color: "#F59E0B" },
  sales: { label: "销售部", emoji: "🤝", color: "#22C55E" },
  security: { label: "安全部", emoji: "🛡️", color: "#EF4444" },
  "spatial-computing": { label: "空间计算部", emoji: "🥽", color: "#A855F7" },
  specialized: { label: "专项部", emoji: "🧠", color: "#D946EF" },
  "supply-chain": { label: "供应链部", emoji: "🚚", color: "#EAB308" },
  support: { label: "支持部", emoji: "🎧", color: "#2DD4BF" },
  testing: { label: "测试部", emoji: "🧪", color: "#60A5FA" },
};

const forbiddenScenarios = [
  "构建未来，一个 commit 一个脚印。",
  "不走寻常路的专家。",
  "让产品好看、好用、有惊喜。",
  "一个真实互动一个粉丝地增长。",
];

const genericCapabilityLabels = new Set([
  "角色",
  "个性",
  "记忆",
  "经验",
  "原则",
  "工作流程",
  "沟通风格",
  "学习与记忆",
  "成功指标",
]);

function parseArgs() {
  const sourceIndex = process.argv.indexOf("--source");
  if (sourceIndex === -1 || !process.argv[sourceIndex + 1]) {
    throw new Error(
      "Usage: node scripts/update-agency-agents.mjs --source <agency-agents-zh checkout>",
    );
  }
  return path.resolve(process.argv[sourceIndex + 1]);
}

async function collectMarkdownFiles(root, relativeDirectory) {
  const directory = path.join(root, relativeDirectory);
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const results = [];
  for (const entry of entries) {
    const relativePath = path.posix.join(relativeDirectory, entry.name);
    if (entry.isDirectory()) {
      results.push(...(await collectMarkdownFiles(root, relativePath)));
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      results.push(relativePath);
    }
  }
  return results;
}

function parseAgentList(source) {
  const agents = [];
  const rowPattern =
    /^\| `([^`]+)` \| ([^|]+?) \| (.+?) \| (原创|中国市场原创|翻译) \|$/gm;
  for (const match of source.matchAll(rowPattern)) {
    agents.push({
      id: match[1].trim(),
      name: match[2].trim(),
      expertise: match[3].trim(),
      source: match[4] === "中国市场原创" ? "原创" : match[4],
    });
  }
  return agents;
}

function cleanMarkdownCell(value) {
  return String(value ?? "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseReadmeProfiles(source) {
  const profiles = new Map();
  for (const line of source.split(/\r?\n/)) {
    const match = line.match(
      /^\| \[([^\]]+)\]\(([^)]+\.md)\) \| ([^|]+?)(?: \| ([^|]+?))? \|$/,
    );
    if (!match || match[2].startsWith("http")) continue;
    const relativePath = match[2].replace(/^\.\//, "").replaceAll("\\", "/");
    profiles.set(relativePath, {
      capabilities: splitCapabilities(match[3]),
      scenarios: cleanMarkdownCell(match[4] ?? ""),
    });
  }
  return profiles;
}

function splitCapabilities(value) {
  return cleanMarkdownCell(value)
    .split(/[、，,；;/]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .filter((item) => !genericCapabilityLabels.has(item))
    .filter((item, index, items) => items.indexOf(item) === index)
    .slice(0, 4);
}

function extractExpertiseCapabilities(expertise) {
  const focused = cleanMarkdownCell(expertise)
    .replace(/^.*?——/, "")
    .replace(/^.*?(?:精通|擅长|专注(?:于)?|负责|覆盖)/, "");
  return splitCapabilities(focused).filter(
    (item) => item.length >= 2 && item.length <= 24,
  );
}

function normalizeHeading(value) {
  return cleanMarkdownCell(value)
    .replace(/^第[一二三四五六七八九十0-9]+(?:步|阶段)[：:\s]*/, "")
    .replace(
      /^(创建|构建|提供|设计|执行|开展|确保|管理|优化|分析|开发(?!者)|实现)/,
      "",
    )
    .replace(/[。！？:：].*$/, "")
    .trim()
    .slice(0, 20);
}

function extractPromptCapabilities(prompt) {
  const coreSection =
    prompt.match(
      /##[^\n]*(?:核心使命|专业能力|主要职责|你能做什么)[^\n]*\n([\s\S]*?)(?=\n##\s|$)/,
    )?.[1] ?? prompt;
  const headings = Array.from(coreSection.matchAll(/^###\s+(.+)$/gm))
    .map((match) => normalizeHeading(match[1]))
    .filter(
      (item) =>
        item.length >= 2 &&
        item.length <= 20 &&
        !genericCapabilityLabels.has(item),
    );
  if (headings.length >= 2) {
    return Array.from(new Set(headings)).slice(0, 4);
  }

  return Array.from(prompt.matchAll(/^-\s+\*\*([^*：:]{2,20})\*\*[：:]/gm))
    .map((match) => normalizeHeading(match[1]))
    .filter((item) => item && !genericCapabilityLabels.has(item))
    .filter((item, index, items) => items.indexOf(item) === index)
    .slice(0, 4);
}

function fallbackCapabilities(agent) {
  const role = agent.name
    .replace(/(?:专家|工程师|设计师|开发者|架构师|顾问|经理|专员|师)$/, "")
    .trim();
  return [
    `${role || agent.name}方案`,
    `${role || agent.name}执行`,
    "质量检查",
  ].slice(0, 3);
}

function fallbackScenario(agent, capabilities) {
  const compact = capabilities.slice(0, 3).join("、");
  return compact || `${agent.name}相关任务`;
}

function popularityFor(id) {
  let hash = 0;
  for (const character of id) {
    hash = (hash * 31 + character.codePointAt(0)) >>> 0;
  }
  return 70 + (hash % 29);
}

function serializeTypescript(agents, departments, counts) {
  return `export interface AgentProfile {
  id: string;
  name: string;
  department: string;
  expertise: string;
  scenarios: string;
  capabilities: string[];
  source: "原创" | "翻译";
  sourcePath: string;
  sourceUrl: string;
  emoji: string;
  color: string;
  prompt: string;
  popularity: number;
}

export const AGENCY_AGENTS_UPSTREAM = {
  repository: "${REPOSITORY_URL}",
  commit: "${UPSTREAM_COMMIT}",
  count: ${EXPECTED_AGENT_COUNT},
} as const;

export const DEFAULT_AGENT_MODEL_ID = "openai/gpt-4o";

export const agents: AgentProfile[] = ${JSON.stringify(agents, null, 2)};

export const agentDepartments = ${JSON.stringify(departments, null, 2)};

export const agentDepartmentCounts: Record<string, number> = ${JSON.stringify(counts, null, 2)};
`;
}

async function main() {
  const sourceRoot = parseArgs();
  const repositoryRoot = path.resolve(import.meta.dirname, "..");
  const [agentListSource, readmeSource] = await Promise.all([
    fs.readFile(path.join(sourceRoot, "AGENT-LIST.md"), "utf8"),
    fs.readFile(path.join(sourceRoot, "README.md"), "utf8"),
  ]);
  const listedAgents = parseAgentList(agentListSource);
  if (listedAgents.length !== EXPECTED_AGENT_COUNT) {
    throw new Error(
      `Expected ${EXPECTED_AGENT_COUNT} AGENT-LIST rows, found ${listedAgents.length}.`,
    );
  }

  const relativeFiles = (
    await Promise.all(
      AGENT_DIRECTORIES.map((directory) =>
        collectMarkdownFiles(sourceRoot, directory),
      ),
    )
  ).flat();
  const pathById = new Map();
  for (const relativePath of relativeFiles) {
    const id = path.posix.basename(relativePath, ".md");
    if (pathById.has(id)) {
      throw new Error(`Duplicate agent file id: ${id}`);
    }
    pathById.set(id, relativePath);
  }
  if (pathById.size !== EXPECTED_AGENT_COUNT) {
    throw new Error(
      `Expected ${EXPECTED_AGENT_COUNT} agent files, found ${pathById.size}.`,
    );
  }

  const readmeProfiles = parseReadmeProfiles(readmeSource);
  const agents = [];
  for (const listedAgent of listedAgents) {
    const sourcePath = pathById.get(listedAgent.id);
    if (!sourcePath) {
      throw new Error(`Missing source file for ${listedAgent.id}.`);
    }
    const departmentKey = sourcePath.split("/")[0];
    const department = departmentMeta[departmentKey];
    if (!department) {
      throw new Error(`Missing department metadata for ${sourcePath}.`);
    }
    const prompt = await fs.readFile(path.join(sourceRoot, sourcePath), "utf8");
    const readmeProfile = readmeProfiles.get(sourcePath);
    const expertiseCapabilities = extractExpertiseCapabilities(
      listedAgent.expertise,
    );
    const capabilities = (
      readmeProfile?.capabilities?.length
        ? readmeProfile.capabilities
        : expertiseCapabilities.length >= 2
          ? expertiseCapabilities
          : extractPromptCapabilities(prompt)
    ).filter(Boolean);
    const finalCapabilities =
      capabilities.length >= 2
        ? capabilities.slice(0, 4)
        : fallbackCapabilities(listedAgent);
    const readmeScenario = readmeProfile?.scenarios ?? "";
    const scenarios =
      readmeScenario && !forbiddenScenarios.includes(readmeScenario)
        ? readmeScenario
        : fallbackScenario(listedAgent, finalCapabilities);

    if (!scenarios || forbiddenScenarios.includes(scenarios)) {
      throw new Error(`Invalid scenario for ${listedAgent.id}: ${scenarios}`);
    }

    agents.push({
      ...listedAgent,
      department: department.label,
      scenarios,
      capabilities: finalCapabilities,
      sourcePath,
      sourceUrl: `${REPOSITORY_URL}/blob/${UPSTREAM_COMMIT}/${sourcePath}`,
      emoji: department.emoji,
      color: department.color,
      prompt,
      popularity: popularityFor(listedAgent.id),
    });
  }

  const departments = Array.from(
    new Set(agents.map((agent) => agent.department)),
  );
  const counts = Object.fromEntries(
    departments.map((department) => [
      department,
      agents.filter((agent) => agent.department === department).length,
    ]),
  );

  await Promise.all([
    fs.writeFile(
      path.join(repositoryRoot, "client", "src", "data", "agents.ts"),
      serializeTypescript(agents, departments, counts),
      "utf8",
    ),
    fs.writeFile(
      path.join(repositoryRoot, "server", "data", "agents.json"),
      `${JSON.stringify(agents, null, 2)}\n`,
      "utf8",
    ),
  ]);

  process.stdout.write(
    `Updated ${agents.length} agents from ${UPSTREAM_COMMIT}; ${departments.length} departments.\n`,
  );
}

await main();

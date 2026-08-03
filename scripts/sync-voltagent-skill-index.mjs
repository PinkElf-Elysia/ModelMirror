import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const REPO_NAME = "VoltAgent/awesome-agent-skills";
const REPO_URL = "https://github.com/VoltAgent/awesome-agent-skills";
const DEFAULT_OUTPUT = "client/src/data/voltagentSkillCatalog.generated.json";

const SECTION_CATEGORIES = new Map([
  ["Development and Testing", "测试与质量"],
  ["Data and Analysis", "数据与分析"],
  ["Business and Marketing", "内容与营销"],
  ["Content and Communication", "内容与营销"],
  ["Creative and Media", "多媒体创作"],
  ["Productivity and Collaboration", "文档与办公"],
  ["Security Skills by Trail of Bits Team", "安全与合规"],
  ["Product Manager Skills by Dean Peters", "产品与项目"],
  ["Product Management Skills by Pawel Huryn", "产品与项目"],
  ["Marketing Skills by Corey Haines", "内容与营销"],
  ["Advertising Skills by Kim Barrett", "内容与营销"],
]);

const CATEGORY_RULES = [
  ["安全与合规", /\b(security|secure|vulnerab|threat|pentest|malware|compliance|incident|auth(?:entication)?|permission|privacy|forensic|audit)\b/i],
  ["测试与质量", /\b(test|testing|qa|quality|playwright|cypress|selenium|jest|vitest|pytest|junit|rspec|debug|profil|performance|benchmark)\b/i],
  ["云与运维", /\b(cloud|devops|deploy|deployment|docker|kubernetes|terraform|infrastructure|ci\/cd|pipeline|observability|sre|serverless|cloudflare|netlify|vercel|azure|aws|gcp)\b/i],
  ["后端与数据库", /\b(database|postgres|postgresql|mysql|sqlite|sql\b|redis|mongodb|firebase|supabase|clickhouse|duckdb|graphql|backend|api\b|webhook|server\b|cache|queue)\b/i],
  ["前端与移动", /\b(frontend|react(?: native)?|next\.js|angular|vue|svelte|flutter|expo|ios\b|android\b|mobile|css\b|tailwind|web component)\b/i],
  ["文档与办公", /\b(document|docs?\b|docx|pdf\b|xlsx|spreadsheet|excel|pptx|slides?|presentation|notion|workspace|meeting|calendar|email|knowledge|markdown)\b/i],
  ["数据与分析", /\b(data|analytics|analysis|visuali[sz]ation|metric|statistics|forecast|research|dataset|machine learning|ml\b|jupyter)\b/i],
  ["内容与营销", /\b(marketing|seo\b|content|copywriting|social|advertis|campaign|brand|newsletter|growth|sales|customer|commerce|shopify)\b/i],
  ["产品与项目", /\b(product|roadmap|project|agile|scrum|jira|linear|requirements|prd\b|user stor|prioriti[sz]|stakeholder)\b/i],
  ["多媒体创作", /\b(image|video|audio|music|voice|animation|3d\b|blender|remotion|design|figma|creative|art\b|media|game)\b/i],
  ["AI 与智能体", /\b(agent|llm\b|ai\b|prompt|rag\b|mcp\b|model|embedding|inference|hugging face|nvidia|gemini|claude)\b/i],
  ["工程开发", /\b(code|coding|developer|development|git\b|github|refactor|architecture|framework|sdk\b|cli\b|typescript|javascript|python|java\b|rust\b|golang|go\b|ruby|php\b|\.net)\b/i],
];

function parseArguments(argv) {
  const options = {
    sourceRoot: "",
    outputPath: DEFAULT_OUTPUT,
    commit: "",
    stars: 0,
    updatedAt: "",
    check: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--output") options.outputPath = argv[++index] ?? "";
    else if (value === "--commit") options.commit = argv[++index] ?? "";
    else if (value === "--stars") options.stars = Number(argv[++index] ?? 0);
    else if (value === "--updated-at") options.updatedAt = argv[++index] ?? "";
    else if (value === "--check") options.check = true;
    else if (!value.startsWith("--") && !options.sourceRoot) options.sourceRoot = value;
    else throw new Error(`Unknown argument: ${value}`);
  }

  if (!options.sourceRoot) {
    throw new Error(
      "Usage: node scripts/sync-voltagent-skill-index.mjs <source-root> [--commit <sha>] [--stars <count>] [--updated-at <YYYY-MM-DD>] [--output <path>] [--check]",
    );
  }
  return options;
}

function resolveGitCommit(sourceRoot, explicitCommit) {
  if (explicitCommit) return explicitCommit;
  return execFileSync("git", ["-C", sourceRoot, "rev-parse", "HEAD"], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  }).trim();
}

function decodeEntities(value) {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function stripMarkup(value) {
  return decodeEntities(value)
    .replace(/<[^>]+>/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function slugify(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 100);
}

function shortHash(value) {
  return createHash("sha256").update(value).digest("hex").slice(0, 8);
}

function inferCategory(section, name, description) {
  const sectionCategory = SECTION_CATEGORIES.get(section);
  if (sectionCategory) return sectionCategory;
  const searchable = `${section} ${name} ${description}`;
  return CATEGORY_RULES.find(([, pattern]) => pattern.test(searchable))?.[0] ?? "其他工具";
}

function inferKind(name, description, sourceUrl) {
  let pointsToRepositoryRoot = false;
  try {
    const url = new URL(sourceUrl);
    pointsToRepositoryRoot =
      url.hostname.toLowerCase() === "github.com" &&
      url.pathname.split("/").filter(Boolean).length === 2;
  } catch {
    pointsToRepositoryRoot = false;
  }
  const nameSignalsPackage =
    /(?:^|[\/-])(skillset|[^/]*skillpack|[^/]*skill-pack|[^/]*bundle)$/i.test(name) ||
    (pointsToRepositoryRoot && /(?:^|[\/-])[^/]*skills$/i.test(name));
  const descriptionSignalsPackage = /\b(?:suite|collection|repository|bundle|pack) of [^.]{0,80}\bskills\b|\bskills (?:suite|collection|library|bundle|pack)\b/i.test(
    description,
  );
  return nameSignalsPackage || descriptionSignalsPackage
    ? "skillset"
    : "skill";
}

function githubInstallSource(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "github.com") return null;

  const parts = url.pathname.split("/").filter(Boolean);
  if (parts.length < 5 || !["tree", "blob"].includes(parts[2])) return null;
  if (!/^(main|master)$/.test(parts[3])) return null;

  const owner = parts[0];
  const repository = parts[1].replace(/\.git$/, "");
  const subPathParts = parts.slice(4);
  if (parts[2] === "blob") {
    if (subPathParts.at(-1)?.toLowerCase() !== "skill.md") return null;
    subPathParts.pop();
  }
  if (
    !/^[A-Za-z0-9_.-]+$/.test(owner) ||
    !/^[A-Za-z0-9_.-]+$/.test(repository) ||
    subPathParts.length === 0 ||
    subPathParts.some((part) => !/^[A-Za-z0-9_.-]+$/.test(part))
  ) {
    return null;
  }

  return {
    repoUrl: `https://github.com/${owner}/${repository}`,
    subPath: subPathParts.join("/"),
  };
}

function parseReadme(readme) {
  const entries = [];
  let section = "未分类";

  for (const line of readme.split(/\r?\n/)) {
    const summaryMatch = line.match(/<summary><h3[^>]*>(.*?)<\/h3><\/summary>/i);
    if (summaryMatch) section = stripMarkup(summaryMatch[1]).replace(/\.$/, "");

    const skillMatch = line.match(
      /^- \*\*\[([^\]]+)\]\(([^)]+)\)\*\*\s+-\s+(.+)$/,
    );
    if (!skillMatch) continue;

    const name = stripMarkup(skillMatch[1]);
    const url = decodeEntities(skillMatch[2].trim());
    const description = stripMarkup(skillMatch[3]);
    entries.push({ name, url, description, section });
  }

  const seen = new Set();
  return entries.filter((entry) => {
    const key = `${entry.name}\n${entry.url}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function buildCatalog(options) {
  const sourceRoot = resolve(options.sourceRoot);
  const readmePath = resolve(sourceRoot, "README.md");
  if (!existsSync(readmePath)) throw new Error(`README.md not found: ${readmePath}`);

  const entries = parseReadme(readFileSync(readmePath, "utf8"));
  const usedIds = new Set();
  const projects = entries.map((entry) => {
    const baseId = `voltagent-${slugify(entry.name) || "skill"}`;
    const id = usedIds.has(baseId) ? `${baseId}-${shortHash(`${entry.name}\n${entry.url}`)}` : baseId;
    usedIds.add(id);
    const installSource = githubInstallSource(entry.url);
    const publisher = entry.name.includes("/") ? entry.name.split("/", 1)[0] : entry.section;
    const kind = inferKind(entry.name, entry.description, entry.url);

    return {
      id,
      name: entry.name,
      kind,
      category: inferCategory(entry.section, entry.name, entry.description),
      publisher,
      sourceGroup: entry.section,
      description: entry.description,
      sourceUrl: entry.url,
      installSource,
      tags: [
        "VoltAgent 索引",
        installSource ? "可一键安装" : "外部索引",
        kind === "skillset" ? "技能包" : publisher,
      ],
      includedSkills: [],
    };
  });

  const installableProjects = projects.filter((project) => project.installSource).length;
  return {
    source: {
      repoName: REPO_NAME,
      repoUrl: REPO_URL,
      commit: resolveGitCommit(sourceRoot, options.commit),
      updatedAt: options.updatedAt || new Date().toISOString().slice(0, 10),
      stars: options.stars,
      indexedEntries: projects.length,
      installableProjects,
      referenceProjects: projects.length - installableProjects,
      sourceGroups: new Set(projects.map((project) => project.sourceGroup)).size,
    },
    projects,
  };
}

function main() {
  const options = parseArguments(process.argv.slice(2));
  const outputPath = resolve(options.outputPath);
  const serialized = `${JSON.stringify(buildCatalog(options), null, 2)}\n`;

  if (options.check) {
    if (!existsSync(outputPath)) throw new Error(`Catalog does not exist: ${outputPath}`);
    const currentCatalog = readFileSync(outputPath, "utf8").replace(/\r\n?/g, "\n");
    if (currentCatalog !== serialized) {
      throw new Error(`Catalog is stale: ${outputPath}`);
    }
    console.log(`Catalog is current: ${outputPath}`);
    return;
  }

  writeFileSync(outputPath, serialized, "utf8");
  const catalog = JSON.parse(serialized);
  console.log(
    `Wrote ${catalog.projects.length} projects (${catalog.source.installableProjects} installable) to ${outputPath}`,
  );
}

main();

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, extname, join, relative, resolve, sep } from "node:path";

const REPO_NAME = "anbeime/skill";
const REPO_URL = "https://github.com/anbeime/skill";
const DEFAULT_OUTPUT = "client/src/data/anbeimeSkillCatalog.generated.json";

const CATEGORY_GROUPS = {
  "内容创作与发布": [
    "article-illustrator",
    "bedtime-story",
    "content-creation-publisher",
    "content-research-writer",
    "data-storytelling",
    "intelligent-content-system",
    "baoyu-format-markdown",
    "baoyu-post-to-wechat",
    "baoyu-post-to-x",
    "baoyu-url-to-markdown",
    "qiaomu-x-article-publisher",
    "wechatsync-publisher",
  ],
  "浏览器与自动化": ["chrome-automation"],
  "电商与营销": [
    "agentkit-multimedia-shopping",
    "digital-avatar-shopping-video",
    "dream-video-prompt-generator",
    "ecommerce-copywriter",
    "ecommerce-full-pipeline",
    "ecommerce-video-marketing",
    "infinitetalk-shopping-avatar",
    "pet-commerce-creator",
    "product-marketing-copywriter",
    "product-video-creator",
    "wechat-hotspot-publisher",
    "xiaohongshu-makeup",
  ],
  "界面与设计": [
    "frontend-design",
    "icon-generator",
    "pop-up-book-illustration",
    "web-design-analyzer",
    "web-to-app",
  ],
  "视频创作": [
    "historical-interview-scripts",
    "historical-science-video-prod",
    "media-processor",
    "remotion-video-enhancer",
    "three-body-video-creator",
    "video-creation-collaborator",
    "video-creation-pro",
    "video-creation-suite",
    "video-frame-extractor",
    "video-recreation",
    "video-transcript-downloader",
    "viral-video-copywriting",
  ],
  "语音与数字人": [
    "infinitetalk",
    "qwen3-asr-assistant",
    "qwen3-tts-local",
    "tts-voice-synthesis",
  ],
  "文档与研究": [
    "contract-review",
    "law-to-markdown",
    "paper-analysis-assistant",
    "pdf-processing-pro",
  ],
  "PPT 与演示": [
    "nanobanana-ppt-visualizer",
    "ppt-generator",
    "ppt-roadshow-generator",
    "pptx-generator",
  ],
  "智能体协作": ["agent-team", "multi-agent-meeting"],
  "知识管理": [
    "json-canvas",
    "obsidian-bases",
    "obsidian-markdown",
    "obsidian-skills-integrated",
  ],
  "产品与职业": ["product-manager-toolkit", "tailored-resume-generator"],
  "金融分析": ["stock-analysis"],
  "文化创作": ["poetry-music-visual"],
  "社区与社交": ["moltbook"],
};

const EXCLUDED_RELATIVE_PATHS = new Set([
  "skills/qiaomu-x-article-publisher/qiaomu-x-article-publisher-github/SKILL.md",
]);

const CATEGORY_BY_DIRECTORY = new Map(
  Object.entries(CATEGORY_GROUPS).flatMap(([category, directories]) =>
    directories.map((directory) => [directory, category]),
  ),
);

const LANGUAGE_BY_EXTENSION = new Map([
  [".md", "Markdown"],
  [".py", "Python"],
  [".ts", "TypeScript"],
  [".tsx", "TypeScript"],
  [".js", "JavaScript"],
  [".mjs", "JavaScript"],
  [".sh", "Shell"],
  [".ps1", "PowerShell"],
  [".css", "CSS"],
]);

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
      "Usage: node scripts/sync-anbeime-skill-catalog.mjs <source-root> [--commit <sha>] [--stars <count>] [--updated-at <YYYY-MM-DD>] [--output <path>] [--check]",
    );
  }
  return options;
}

function toPosixPath(value) {
  return value.split(sep).join("/");
}

function walkFiles(root) {
  const files = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === "node_modules") continue;
    const fullPath = join(root, entry.name);
    if (entry.isDirectory()) files.push(...walkFiles(fullPath));
    else if (entry.isFile()) files.push(fullPath);
  }
  return files;
}

function stripYamlQuotes(value) {
  const trimmed = value.trim();
  if (
    trimmed.length >= 2 &&
    ((trimmed.startsWith('"') && trimmed.endsWith('"')) ||
      (trimmed.startsWith("'") && trimmed.endsWith("'")))
  ) {
    return trimmed.slice(1, -1).replace(/\\"/g, '"').replace(/''/g, "'");
  }
  return trimmed;
}

function parseFrontmatter(content) {
  const normalized = content.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n");
  const match = normalized.match(/^---\s*\n([\s\S]*?)\n---(?:\s*\n|$)/);
  if (!match) return { body: normalized, values: {} };

  const lines = match[1].split("\n");
  const values = {};
  for (let index = 0; index < lines.length; index += 1) {
    const keyMatch = lines[index].match(/^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$/);
    if (!keyMatch) continue;
    const [, key, rawValue] = keyMatch;
    if (key !== "name" && key !== "description") continue;

    if (/^[>|][+-]?$/.test(rawValue.trim())) {
      const blockLines = [];
      while (index + 1 < lines.length && /^\s+/.test(lines[index + 1])) {
        blockLines.push(lines[++index].trim());
      }
      values[key] = blockLines.filter(Boolean).join(" ");
    } else {
      values[key] = stripYamlQuotes(rawValue);
    }
  }

  return { body: normalized.slice(match[0].length), values };
}

function firstHeading(body) {
  return body
    .split("\n")
    .map((line) => line.trim())
    .find((line) => /^#{1,6}\s+/.test(line))
    ?.replace(/^#{1,6}\s+/, "")
    .trim();
}

function firstParagraph(body) {
  return body
    .split("\n")
    .map((line) => line.trim())
    .find(
      (line) =>
        line &&
        !line.startsWith("#") &&
        !line.startsWith("```") &&
        !line.startsWith("<!--"),
    );
}

function normalizeSkillName(value, fallback) {
  return (value || fallback)
    .replace(/\s+/g, " ")
    .replace(/^['"]|['"]$/g, "")
    .trim();
}

function normalizeDescription(value, fallback) {
  const result = (value || fallback || "社区 Skill，安装前请检查其说明与依赖。")
    .replace(/\s+/g, " ")
    .trim();
  return result.length > 220 ? `${result.slice(0, 217)}...` : result;
}

function normalizeNameForMatch(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function normalizedContentHash(content) {
  return createHash("sha256")
    .update(content.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n"))
    .digest("hex");
}

function canonicalScore(record) {
  const directoryName = basename(dirname(record.fullPath)).toLowerCase();
  const nameMatchesDirectory = normalizeNameForMatch(record.name) === directoryName;
  const depth = record.relativePath.split("/").length;
  return (nameMatchesDirectory ? 10_000 : 0) - depth * 100 - record.relativePath.length;
}

function resolveGitCommit(sourceRoot, explicitCommit) {
  if (explicitCommit) return explicitCommit;
  try {
    return execFileSync("git", ["-C", sourceRoot, "rev-parse", "HEAD"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    throw new Error("Source is not a Git checkout. Pass --commit with the verified remote SHA.");
  }
}

function buildCatalog(options) {
  const sourceRoot = resolve(options.sourceRoot);
  const skillsRoot = join(sourceRoot, "skills");
  if (!existsSync(skillsRoot) || !statSync(skillsRoot).isDirectory()) {
    throw new Error(`Skills directory not found: ${skillsRoot}`);
  }

  const sourceFiles = walkFiles(skillsRoot);
  const skillFiles = sourceFiles.filter((file) => basename(file) === "SKILL.md");
  const parsedRecords = skillFiles
    .map((fullPath) => {
      const content = readFileSync(fullPath, "utf8");
      const { body, values } = parseFrontmatter(content);
      const directoryName = basename(dirname(fullPath));
      return {
        body,
        content,
        description: normalizeDescription(values.description, firstParagraph(body)),
        fullPath,
        hash: normalizedContentHash(content),
        name: normalizeSkillName(values.name, firstHeading(body) || directoryName),
        relativePath: toPosixPath(relative(sourceRoot, fullPath)),
      };
    })
    .filter(
      (record) =>
        !record.relativePath.includes("/skills/_template/") &&
        !record.relativePath.startsWith("skills/_template/") &&
        !EXCLUDED_RELATIVE_PATHS.has(record.relativePath) &&
        !/(?:此文件为占位文件|<占位符>)/.test(record.content),
    );

  const recordsByHash = new Map();
  for (const record of parsedRecords) {
    const group = recordsByHash.get(record.hash) ?? [];
    group.push(record);
    recordsByHash.set(record.hash, group);
  }

  const canonicalRecords = [...recordsByHash.values()].map((duplicates) =>
    [...duplicates].sort((left, right) => canonicalScore(right) - canonicalScore(left))[0],
  );

  const projects = canonicalRecords.map((record) => {
    const packageDirectory = dirname(record.fullPath);
    const directoryKey = basename(packageDirectory).toLowerCase();
    const packageFiles = sourceFiles.filter(
      (file) => file === packageDirectory || file.startsWith(`${packageDirectory}${sep}`),
    );
    const nestedSkillNames = parsedRecords
      .filter(
        (candidate) =>
          candidate.fullPath !== record.fullPath &&
          candidate.fullPath.startsWith(`${packageDirectory}${sep}`),
      )
      .map((candidate) => candidate.name)
      .filter((name, index, names) => name !== record.name && names.indexOf(name) === index);
    const languages = [
      ...new Set(
        packageFiles
          .map((file) => LANGUAGE_BY_EXTENSION.get(extname(file).toLowerCase()))
          .filter(Boolean),
      ),
    ].slice(0, 4);
    const hasDirectory = (directory) =>
      packageFiles.some((file) =>
        toPosixPath(relative(packageDirectory, file)).startsWith(`${directory}/`),
      );
    const kind = nestedSkillNames.length > 0 ? "skillset" : "skill";
    const tags =
      kind === "skillset"
        ? ["社区来源", "技能包", `${nestedSkillNames.length} 个子技能`]
        : [
            "社区来源",
            hasDirectory("scripts") ? "含脚本" : "说明型",
            hasDirectory("references")
              ? "含参考资料"
              : hasDirectory("assets")
                ? "含资源"
                : "独立技能",
          ];
    const category = CATEGORY_BY_DIRECTORY.get(directoryKey) ?? "其他工具";

    return {
      id: `anbeime-${directoryKey}`,
      name: record.name,
      kind,
      category,
      description: record.description,
      subPath: toPosixPath(relative(sourceRoot, packageDirectory)),
      language: languages.join(" / ") || "Markdown",
      tags,
      includedSkills: nestedSkillNames,
    };
  });

  projects.sort((left, right) =>
    left.category.localeCompare(right.category, "zh-CN") ||
    left.name.localeCompare(right.name, "zh-CN"),
  );

  const duplicateIds = projects.filter(
    (project, index) => projects.findIndex((candidate) => candidate.id === project.id) !== index,
  );
  if (duplicateIds.length > 0) {
    throw new Error(`Duplicate catalog ids: ${duplicateIds.map((item) => item.id).join(", ")}`);
  }

  return {
    source: {
      repoName: REPO_NAME,
      repoUrl: REPO_URL,
      commit: resolveGitCommit(sourceRoot, options.commit),
      updatedAt: options.updatedAt || new Date().toISOString().slice(0, 10),
      stars: options.stars,
      discoveredSkillFiles: skillFiles.length,
      catalogProjects: projects.length,
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
  console.log(`Wrote ${JSON.parse(serialized).projects.length} projects to ${outputPath}`);
}

main();

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const CATALOG_PATH = resolve("client/src/data/voltagentSkillCatalog.generated.json");
const OUTPUT_PATH = resolve(
  "client/src/data/officialSkillInstallSources.generated.ts",
);
const SKIP_PUBLISHERS = new Set([
  "anthropics",
  "getsentry",
  "microsoft",
  "openai",
]);
const PAGE_CONCURRENCY = 20;
const PAGE_TIMEOUT_MS = 15_000;

function readCatalog() {
  return JSON.parse(readFileSync(CATALOG_PATH, "utf8"));
}

function runGhApi(endpoint, jq) {
  const args = ["api", endpoint];
  if (jq) args.push("--jq", jq);
  return execFileSync("gh", args, {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    windowsHide: true,
  }).trim();
}

function decodeHtml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&#x2F;", "/")
    .replaceAll("\\u002F", "/")
    .replaceAll("\\/", "/");
}

function parseGithubSkillUrl(rawUrl) {
  const url = new URL(decodeHtml(rawUrl));
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (url.hostname.toLowerCase() !== "github.com" || parts.length < 5) {
    return undefined;
  }
  const [owner, repository, action, ref, ...pathParts] = parts;
  if (action !== "tree" && action !== "blob") return undefined;
  if (action === "blob" && pathParts.at(-1)?.toLowerCase() === "skill.md") {
    pathParts.pop();
  }
  const subPath = pathParts.join("/");
  if (!subPath) return undefined;
  return {
    repoKey: `${owner}/${repository}`,
    repoUrl: `https://github.com/${owner}/${repository}`,
    ref,
    subPath,
  };
}

function parseGithubRepoUrl(rawUrl) {
  const url = new URL(decodeHtml(rawUrl));
  const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
  if (url.hostname.toLowerCase() !== "github.com" || parts.length < 2) {
    return undefined;
  }
  const [owner, repository] = parts;
  return {
    repoKey: `${owner}/${repository}`,
    repoUrl: `https://github.com/${owner}/${repository}`,
    ref: undefined,
    subPath: undefined,
  };
}

function extractDeclaredSource(html, slug) {
  const normalized = decodeHtml(html);
  const candidates = [
    ...normalized.matchAll(
      /https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/(?:tree|blob)\/[^"'<>\s]+/g,
    ),
  ]
    .map((match) => parseGithubSkillUrl(match[0]))
    .filter(Boolean);
  const directSource =
    candidates.find((candidate) => candidate.subPath.split("/").at(-1) === slug) ??
    candidates.find((candidate) => candidate.subPath.includes(slug)) ??
    candidates[0];
  if (directSource) return directSource;

  const commandRepo = normalized.match(
    /npx\s+skills\s+add\s+(https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)/i,
  )?.[1];
  return commandRepo ? parseGithubRepoUrl(commandRepo) : undefined;
}

async function mapWithConcurrency(items, concurrency, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function consume() {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, () => consume()),
  );
  return results;
}

async function fetchDeclaredSources(projects) {
  return mapWithConcurrency(projects, PAGE_CONCURRENCY, async (project, index) => {
    for (let attempt = 1; attempt <= 2; attempt += 1) {
      try {
        const response = await fetch(project.sourceUrl, {
          headers: { "user-agent": "ModelMirror-Skill-Source-Audit/1.0" },
          redirect: "follow",
          signal: AbortSignal.timeout(PAGE_TIMEOUT_MS),
        });
        if (!response.ok) {
          return { project, error: `来源页返回 HTTP ${response.status}` };
        }
        const source = extractDeclaredSource(
          await response.text(),
          project.name.split("/").at(-1),
        );
        if (!source) return { project, error: "来源页未声明 GitHub Skill 子目录" };
        if ((index + 1) % 50 === 0) {
          console.error(`已读取 ${index + 1}/${projects.length} 个来源页`);
        }
        return { project, source };
      } catch (error) {
        if (attempt === 2) {
          return {
            project,
            error: error instanceof Error ? error.message : String(error),
          };
        }
      }
    }
    return { project, error: "来源页核验失败" };
  });
}

function loadRepositoryTrees(declaredResults) {
  const repositories = new Map();
  for (const result of declaredResults) {
    if (!result.source) continue;
    const key = `${result.source.repoKey}@${result.source.ref}`;
    if (!repositories.has(key)) repositories.set(key, result.source);
  }

  const trees = new Map();
  let completed = 0;
  for (const [key, source] of repositories) {
    try {
      const ref = source.ref ?? runGhApi(`repos/${source.repoKey}`, ".default_branch");
      const commit = runGhApi(
        `repos/${source.repoKey}/commits/${encodeURIComponent(ref)}`,
        ".sha",
      );
      const tree = JSON.parse(
        runGhApi(`repos/${source.repoKey}/git/trees/${commit}?recursive=1`),
      );
      const skillFiles = new Set(
        tree.tree
          .filter((entry) => entry.type === "blob" && entry.path.endsWith("SKILL.md"))
          .map((entry) => entry.path),
      );
      trees.set(key, {
        commit,
        ref,
        skillFiles,
        truncated: Boolean(tree.truncated),
      });
    } catch (error) {
      const detail = [error?.stderr, error?.stdout, error?.message]
        .filter(Boolean)
        .join(" ");
      trees.set(key, {
        error: /HTTP 404|Not Found/i.test(detail) ? "not-found" : "transient",
      });
    }
    completed += 1;
    console.error(`已核验 ${completed}/${repositories.size} 个 GitHub 仓库版本`);
  }
  return trees;
}

function buildAudit(declaredResults, trees) {
  const verified = {};
  const rejected = {};
  for (const result of declaredResults) {
    const sourceUrl = result.project.sourceUrl;
    if (!result.source) {
      rejected[sourceUrl] = { reason: result.error };
      continue;
    }
    const tree = trees.get(`${result.source.repoKey}@${result.source.ref}`);
    if (!tree || tree.error) {
      rejected[sourceUrl] = {
        reason:
          tree?.error === "not-found"
            ? "GitHub 仓库或版本不存在"
            : "GitHub 核验请求未完成",
        declaredUrl: `${result.source.repoUrl}/tree/${result.source.ref}/${result.source.subPath}`,
      };
      continue;
    }
    const declaredSkillFile = result.source.subPath
      ? `${result.source.subPath}/SKILL.md`
      : undefined;
    let subPath = result.source.subPath;
    let pathResolution = "来源页目录与固定提交一致";
    if (!declaredSkillFile || !tree.skillFiles.has(declaredSkillFile)) {
      const slug = result.project.name.split("/").at(-1).toLowerCase();
      const matchingPaths = [...tree.skillFiles].filter((path) => {
        const parts = path.split("/");
        return parts.at(-2)?.toLowerCase() === slug;
      });
      if (!tree.truncated && matchingPaths.length === 1) {
        subPath = matchingPaths[0].slice(0, -"/SKILL.md".length);
        pathResolution = "来源仓库内按唯一同名 Skill 目录修正";
      }
    }
    if (!subPath || !tree.skillFiles.has(`${subPath}/SKILL.md`)) {
      const missingPath = declaredSkillFile ?? `${result.project.name.split("/").at(-1)}/SKILL.md`;
      rejected[sourceUrl] = {
        reason: tree.truncated
          ? "GitHub 仓库树被截断，未能证明 SKILL.md 存在"
          : `固定提交中不存在 ${missingPath}`,
        declaredUrl: result.source.subPath
          ? `${result.source.repoUrl}/tree/${result.source.ref}/${result.source.subPath}`
          : result.source.repoUrl,
        verifiedCommit: tree.commit,
      };
      continue;
    }
    verified[sourceUrl] = {
      repoUrl: result.source.repoUrl,
      subPath,
      verifiedCommit: tree.commit,
      pathResolution,
    };
  }
  return { verified, rejected };
}

function renderGeneratedModule(audit) {
  const verified = Object.fromEntries(Object.entries(audit.verified).sort(([a], [b]) => a.localeCompare(b)));
  const rejected = Object.fromEntries(Object.entries(audit.rejected).sort(([a], [b]) => a.localeCompare(b)));
  return `// Generated by scripts/verify-official-skill-install-sources.mjs.\n` +
    `// Only existing VoltAgent catalog entries are checked; this file is not an external market import.\n` +
    `export const VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES = ${JSON.stringify(verified, null, 2)} as const;\n\n` +
    `export const REJECTED_OFFICIAL_SKILL_INSTALL_SOURCES = ${JSON.stringify(rejected, null, 2)} as const;\n`;
}

const catalog = readCatalog();
const projects = catalog.projects.filter((project) => {
  if (project.installSource || !project.sourceUrl.startsWith("https://officialskills.sh/")) {
    return false;
  }
  return !SKIP_PUBLISHERS.has(project.publisher.toLowerCase());
});
const declaredResults = await fetchDeclaredSources(projects);
const trees = loadRepositoryTrees(declaredResults);
const audit = buildAudit(declaredResults, trees);
const incompleteChecks = Object.values(audit.rejected).filter(
  (rejection) =>
    rejection.reason === "GitHub 核验请求未完成" ||
    /HTTP 5\d\d|fetch failed|abort|timeout/i.test(rejection.reason),
);
if (incompleteChecks.length > 0) {
  throw new Error(
    `存在 ${incompleteChecks.length} 项瞬时核验失败，保留旧证据文件并退出`,
  );
}
if (
  Object.keys(audit.verified).length + Object.keys(audit.rejected).length !==
  projects.length
) {
  throw new Error("核验结果未覆盖全部目标条目，保留旧证据文件并退出");
}
writeFileSync(OUTPUT_PATH, renderGeneratedModule(audit), "utf8");

console.log(`核验对象：${projects.length} 项`);
console.log(`可升级为一键安装：${Object.keys(audit.verified).length} 项`);
console.log(`暂不升级：${Object.keys(audit.rejected).length} 项`);
console.log(`输出：${OUTPUT_PATH}`);

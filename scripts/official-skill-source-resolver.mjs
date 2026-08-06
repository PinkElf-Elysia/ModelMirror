const OFFICIAL_SKILLS_HOST = "officialskills.sh";
const GITHUB_HOST = "github.com";

export const OFFICIAL_SOURCE_FAILURE_MESSAGES = {
  "source-page-not-found": "来源页不存在或已移除",
  "source-page-declaration-missing": "来源页没有声明可核验的 GitHub Skill 安装地址",
  "source-page-declaration-ambiguous": "来源页声明了多个不同的 GitHub Skill 安装地址，无法唯一确认",
};

export class TransientOfficialSourceError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "TransientOfficialSourceError";
  }
}

function decodeHtml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&#x2F;", "/")
    .replaceAll("&#47;", "/")
    .replaceAll("\\u002F", "/")
    .replaceAll("\\/", "/");
}

function normalizeSlug(value) {
  return String(value ?? "").trim().toLocaleLowerCase("en-US");
}

function normalizeGithubRepoUrl(owner, repository) {
  return `https://github.com/${owner}/${repository.replace(/\.git$/i, "")}`;
}

function parseGithubPathDeclaration(rawUrl) {
  try {
    const url = new URL(decodeHtml(rawUrl));
    const parts = url.pathname
      .split("/")
      .filter(Boolean)
      .map(decodeURIComponent);
    if (url.hostname.toLocaleLowerCase("en-US") !== GITHUB_HOST || parts.length < 5) {
      return undefined;
    }
    const [owner, repository, action, ...refAndPathParts] = parts;
    if (action !== "tree" && action !== "blob") return undefined;
    if (refAndPathParts.length < 2) return undefined;
    const repoUrl = normalizeGithubRepoUrl(owner, repository);
    const declaredUrl = `${repoUrl}/${action}/${refAndPathParts.map(encodeURIComponent).join("/")}`;
    return {
      repoUrl,
      action,
      refAndPath: refAndPathParts.join("/"),
      declaredUrl,
    };
  } catch {
    return undefined;
  }
}

function parseGithubRepoUrl(rawUrl) {
  try {
    const url = new URL(decodeHtml(rawUrl));
    const parts = url.pathname
      .split("/")
      .filter(Boolean)
      .map(decodeURIComponent);
    if (url.hostname.toLocaleLowerCase("en-US") !== GITHUB_HOST || parts.length !== 2) {
      return undefined;
    }
    return normalizeGithubRepoUrl(parts[0], parts[1]);
  } catch {
    return undefined;
  }
}

function deduplicate(values, keyForValue) {
  const unique = new Map();
  for (const value of values) {
    unique.set(keyForValue(value).toLocaleLowerCase("en-US"), value);
  }
  return [...unique.values()];
}

function setupSection(html) {
  const normalized = decodeHtml(html);
  const start = normalized.search(/Setup\s*&\s*Installation/i);
  if (start < 0) return undefined;
  const remaining = normalized.slice(start);
  const end = remaining.search(/What\s+This\s+Skill\s+Does/i);
  return end > 0 ? remaining.slice(0, end) : remaining;
}

function declarationTail(declaration) {
  const parts = declaration.refAndPath.split("/").filter(Boolean);
  if (
    declaration.action === "blob" &&
    parts.at(-1)?.toLocaleLowerCase("en-US") === "skill.md"
  ) {
    return parts.at(-2);
  }
  return parts.at(-1);
}

function failure(reasonCode) {
  return {
    ok: false,
    reasonCode,
    reason: OFFICIAL_SOURCE_FAILURE_MESSAGES[reasonCode],
  };
}

export function extractOfficialSkillDeclaration(html, expectedSlug) {
  const setup = setupSection(html);
  if (!setup) return failure("source-page-declaration-missing");

  const pathDeclarations = deduplicate(
    [...setup.matchAll(
      /https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/(?:tree|blob)\/[^"'<>\s]+/g,
    )]
      .map((match) => parseGithubPathDeclaration(match[0]))
      .filter(Boolean),
    (item) => item.declaredUrl,
  );
  const normalizedSlug = normalizeSlug(expectedSlug);
  const exactPathDeclarations = pathDeclarations.filter(
    (item) => normalizeSlug(declarationTail(item)) === normalizedSlug,
  );
  const selectedPath =
    exactPathDeclarations.length === 1
      ? exactPathDeclarations[0]
      : pathDeclarations.length === 1
        ? pathDeclarations[0]
        : undefined;

  const commandDeclarations = deduplicate(
    [...setup.matchAll(
      /npx\s+skills\s+add\s+(https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)(?:\.git)?(?:\s+[^\r\n<]*)?\s+--skill\s+([A-Za-z0-9_.-]+)/gi,
    )]
      .map((match) => ({
        repoUrl: parseGithubRepoUrl(match[1]),
        slug: match[2],
      }))
      .filter((item) => item.repoUrl && normalizeSlug(item.slug) === normalizedSlug),
    (item) => `${item.repoUrl}#${normalizeSlug(item.slug)}`,
  );

  if (selectedPath) {
    const conflictingCommand = commandDeclarations.some(
      (item) => item.repoUrl.toLocaleLowerCase("en-US") !== selectedPath.repoUrl.toLocaleLowerCase("en-US"),
    );
    if (conflictingCommand || exactPathDeclarations.length > 1) {
      return failure("source-page-declaration-ambiguous");
    }
    return {
      ok: true,
      candidate: {
        repoUrl: selectedPath.repoUrl,
        declaredUrl: selectedPath.declaredUrl,
        declaredAction: selectedPath.action,
        declaredRefAndPath: selectedPath.refAndPath,
        method: "source-page-declared-path",
      },
    };
  }

  if (pathDeclarations.length > 1 || commandDeclarations.length > 1) {
    return failure("source-page-declaration-ambiguous");
  }
  if (commandDeclarations.length === 1) {
    const command = commandDeclarations[0];
    return {
      ok: true,
      candidate: {
        repoUrl: command.repoUrl,
        declaredUrl: command.repoUrl,
        method: "source-page-command-exact-match",
        requiresExactName: true,
      },
    };
  }
  return failure("source-page-declaration-missing");
}

function responseHostname(response, sourceUrl) {
  try {
    return new URL(response.url || sourceUrl).hostname.toLocaleLowerCase("en-US");
  } catch {
    return "";
  }
}

function isTransientStatus(status) {
  return status === 401 || status === 403 || status === 408 || status === 425 || status === 429 || status >= 500;
}

export async function resolveOfficialSkillPage(
  project,
  { fetchImpl = fetch, timeoutMs = 30_000 } = {},
) {
  let response;
  try {
    response = await fetchImpl(project.sourceUrl, {
      headers: { "User-Agent": "ModelMirror-Skill-Source-Audit/2.0" },
      redirect: "follow",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    throw new TransientOfficialSourceError(
      `OfficialSkills 来源页读取未完成：${project.sourceUrl}`,
      { cause: error },
    );
  }

  if (response.status === 404 || response.status === 410) {
    return failure("source-page-not-found");
  }
  if (isTransientStatus(response.status) || !response.ok) {
    throw new TransientOfficialSourceError(
      `OfficialSkills 来源页请求未完成：HTTP ${response.status} ${project.sourceUrl}`,
    );
  }
  if (responseHostname(response, project.sourceUrl) !== OFFICIAL_SKILLS_HOST) {
    throw new TransientOfficialSourceError(
      `OfficialSkills 来源页重定向到未允许的域名：${response.url}`,
    );
  }
  const contentType = response.headers?.get?.("content-type") ?? "text/html";
  if (!contentType.toLocaleLowerCase("en-US").includes("text/html")) {
    throw new TransientOfficialSourceError(
      `OfficialSkills 来源页返回了非 HTML 内容：${project.sourceUrl}`,
    );
  }
  return extractOfficialSkillDeclaration(
    await response.text(),
    project.name.split("/").at(-1),
  );
}

import { spawn } from "node:child_process";
import {
  readFile,
  rename,
  writeFile,
} from "node:fs/promises";
import { basename, resolve } from "node:path";
import {
  auditedInstallSource,
  hasAuditedInstallMismatch,
  inferInstallStatus,
} from "../client/src/data/skillCatalogPolicy.ts";
import {
  OFFICIAL_SOURCE_FAILURE_MESSAGES,
  resolveOfficialSkillPage,
} from "./official-skill-source-resolver.mjs";

const OUTPUT_PATH = resolve(
  "client/src/data/skillSourceVerification.generated.ts",
);
const CONCURRENCY = 12;

const FAILURE_MESSAGES = {
  "ambiguous-skill-path": "仓库中存在多个可能的 Skill 目录，无法唯一确认安装路径",
  "declared-path-missing": "固定提交中不存在来源声明的 SKILL.md",
  "no-install-source": "来源没有可由当前安装器验证的 GitHub Skill 目录",
  "no-skill-file": "固定提交中未发现 SKILL.md",
  "ref-not-found": "来源声明的 Git 提交不存在或无法读取",
  "repository-not-found": "GitHub 仓库不存在、不可公开访问或已被移除",
  ...OFFICIAL_SOURCE_FAILURE_MESSAGES,
};

const CURATED_PROJECTS = [
  {
    id: "anthropic-pdf-skill",
    name: "PDF 文档处理技能",
    kind: "skill",
    sourceUrl: "https://github.com/anthropics/skills/tree/main/skills/pdf",
    candidate: {
      repoUrl: "https://github.com/anthropics/skills",
      subPath: "skills/pdf",
      method: "curated-path",
    },
  },
  {
    id: "anthropic-xlsx-skill",
    name: "XLSX 表格处理技能",
    kind: "skill",
    sourceUrl: "https://github.com/anthropics/skills/tree/main/skills/xlsx",
    candidate: {
      repoUrl: "https://github.com/anthropics/skills",
      subPath: "skills/xlsx",
      method: "curated-path",
    },
  },
  {
    id: "mattpocock-tdd-skill",
    name: "TypeScript TDD 技能",
    kind: "skill",
    sourceUrl: "https://github.com/mattpocock/skills/tree/main/skills/engineering/tdd",
    candidate: {
      repoUrl: "https://github.com/mattpocock/skills",
      subPath: "skills/engineering/tdd",
      method: "curated-path",
    },
  },
  {
    id: "agent-skills-standard",
    name: "Agent Skills 开放标准",
    kind: "skill",
    sourceUrl: "https://github.com/agentskills/agentskills",
    forcedStatus: "reference",
    reasonCode: "no-install-source",
  },
];

function readJson(path) {
  return readFile(resolve(path), "utf8").then(JSON.parse);
}

function normalizeRepoUrl(repoUrl) {
  return repoUrl.replace(/\.git$/i, "").replace(/\/$/, "");
}

function parseGithubRepoUrl(sourceUrl) {
  try {
    const url = new URL(sourceUrl);
    const parts = url.pathname.split("/").filter(Boolean);
    if (url.hostname.toLowerCase() !== "github.com" || parts.length !== 2) {
      return undefined;
    }
    return `https://github.com/${parts[0]}/${parts[1].replace(/\.git$/i, "")}`;
  } catch {
    return undefined;
  }
}

function cleanSubPath(subPath = "") {
  return subPath.replaceAll("\\", "/").replace(/^\/+|\/+$/g, "");
}

function skillFileFor(subPath) {
  const cleanPath = cleanSubPath(subPath);
  return cleanPath ? `${cleanPath}/SKILL.md` : "SKILL.md";
}

function skillDirectory(skillFile) {
  return skillFile === "SKILL.md"
    ? ""
    : skillFile.slice(0, -"/SKILL.md".length);
}

function failureEvidence(entry, reasonCode, extra = {}) {
  return {
    status: entry.forcedStatus ?? "pending",
    sourceUrl: entry.sourceUrl,
    reasonCode,
    reason: extra.reason ?? FAILURE_MESSAGES[reasonCode],
    ...(extra.repoUrl ? { repoUrl: extra.repoUrl } : {}),
    ...(extra.verifiedCommit ? { verifiedCommit: extra.verifiedCommit } : {}),
    ...(extra.declaredUrl ? { declaredUrl: extra.declaredUrl } : {}),
  };
}

function run(command, args, options = {}) {
  return new Promise((resolveRun) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      windowsHide: true,
      env: {
        ...process.env,
        GIT_TERMINAL_PROMPT: "0",
      },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => resolveRun({ code, stdout, stderr }));
    child.on("error", (error) =>
      resolveRun({ code: -1, stdout, stderr: `${stderr}\n${error.message}` }),
    );
  });
}

function isRepositoryNotFound(output) {
  return /repository not found|not found|does not exist|could not read Username/i.test(
    output,
  );
}

function isRefNotFound(output) {
  return /not our ref|couldn't find remote ref|unadvertised object|not a valid object name|unknown revision/i.test(
    output,
  );
}

async function mapLimit(values, limit, operation) {
  const results = new Array(values.length);
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < values.length) {
      const index = nextIndex;
      nextIndex += 1;
      results[index] = await operation(values[index], index);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(limit, values.length) }, () => worker()),
  );
  return results;
}

function isOfficialSkillsUrl(sourceUrl) {
  try {
    return new URL(sourceUrl).hostname.toLocaleLowerCase("en-US") === "officialskills.sh";
  } catch {
    return false;
  }
}

async function resolveOfficialSourceEntries(entries) {
  const targets = entries.filter(
    (entry) => entry.resolver === "officialskills-page" && !entry.candidate,
  );
  const results = await mapLimit(targets, CONCURRENCY, async (entry, index) => {
    const result = await resolveOfficialSkillPage(entry);
    if ((index + 1) % 25 === 0 || index + 1 === targets.length) {
      console.log(`已读取 ${index + 1}/${targets.length} 个 OfficialSkills 来源页`);
    }
    return [entry.id, result];
  });
  const byId = new Map(results);
  const reasonCounts = {};
  let declared = 0;
  const resolvedEntries = entries.map((entry) => {
    const result = byId.get(entry.id);
    if (!result) return entry;
    if (result.ok) {
      declared += 1;
      return { ...entry, candidate: result.candidate };
    }
    reasonCounts[result.reasonCode] = (reasonCounts[result.reasonCode] ?? 0) + 1;
    return {
      ...entry,
      reasonCode: result.reasonCode,
      reason: result.reason,
    };
  });
  return {
    entries: resolvedEntries,
    targetCount: targets.length,
    declaredCount: declared,
    reasonCounts,
  };
}

function githubRepoParts(repoUrl) {
  const url = new URL(repoUrl);
  const [owner, repository] = url.pathname.split("/").filter(Boolean);
  return { owner, repository };
}

async function fetchGithub(url, accept) {
  const response = await fetch(url, {
    headers: {
      Accept: accept,
      "User-Agent": "ModelMirror-Skill-Verifier",
      "X-Requested-With": "XMLHttpRequest",
    },
    redirect: "follow",
    signal: AbortSignal.timeout(45_000),
  });
  if (response.status === 404) return { error: "not-found" };
  if (!response.ok) {
    throw new Error(`GitHub 核验请求失败：${response.status} ${url}`);
  }
  return { response };
}

async function loadTree(repoUrl, commit) {
  const { owner, repository } = githubRepoParts(repoUrl);
  const findUrl = `https://github.com/${owner}/${repository}/find/${commit}`;
  const findResult = await fetchGithub(findUrl, "text/html");
  if (findResult.error) return { error: "ref-not-found" };
  const html = await findResult.response.text();
  const treeListMatch = html.match(
    /src=["'](\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/tree-list\/[a-f0-9]{40})["']/i,
  );
  if (!treeListMatch) {
    throw new Error(`GitHub 文件树入口缺失：${repoUrl}@${commit}`);
  }
  const treeResult = await fetchGithub(
    `https://github.com${treeListMatch[1]}`,
    "application/json",
  );
  if (treeResult.error) return { error: "ref-not-found" };
  const payload = await treeResult.response.json();
  if (!Array.isArray(payload.paths)) {
    throw new Error(`GitHub 文件树响应无效：${repoUrl}@${commit}`);
  }
  return {
    skillFiles: payload.paths.filter(
      (path) => path === "SKILL.md" || path.endsWith("/SKILL.md"),
    ),
  };
}

function parseRemoteRefs(output) {
  const refs = new Map();
  const tagObjects = new Map();
  const peeledTags = new Map();
  let headCommit;
  const addRef = (name, commit) => {
    const commits = refs.get(name) ?? new Set();
    commits.add(commit);
    refs.set(name, commits);
  };
  for (const line of output.split(/\r?\n/)) {
    const match = line.trim().match(/^([a-f0-9]{40})\s+(.+)$/i);
    if (!match) continue;
    const commit = match[1].toLocaleLowerCase("en-US");
    const refName = match[2];
    if (refName === "HEAD") {
      headCommit = commit;
      continue;
    }
    if (refName.startsWith("refs/heads/")) {
      addRef(refName.slice("refs/heads/".length), commit);
      continue;
    }
    if (refName.startsWith("refs/tags/")) {
      const shortName = refName.slice("refs/tags/".length).replace(/\^\{\}$/, "");
      if (refName.endsWith("^{}")) peeledTags.set(shortName, commit);
      else tagObjects.set(shortName, commit);
    }
  }
  for (const [name, commit] of tagObjects) {
    addRef(name, peeledTags.get(name) ?? commit);
  }
  return { refs, headCommit };
}

function resolveDeclaredRefAndPath(request, remoteRefs) {
  const directCommit = request.refAndPath.match(/^([a-f0-9]{40})(?:\/(.+))?$/i);
  let commit;
  let ref;
  let subPath;
  if (directCommit) {
    commit = directCommit[1].toLocaleLowerCase("en-US");
    ref = commit;
    subPath = directCommit[2] ?? "";
  } else {
    const candidates = [...remoteRefs.refs.entries()]
      .filter(
        ([name]) =>
          request.refAndPath === name || request.refAndPath.startsWith(`${name}/`),
      )
      .sort(([left], [right]) => right.length - left.length);
    if (candidates.length === 0) return undefined;
    [ref] = candidates[0];
    const commits = candidates[0][1];
    if (commits.size !== 1) return undefined;
    [commit] = commits;
    subPath = request.refAndPath.slice(ref.length).replace(/^\/+/, "");
  }
  if (
    request.action === "blob" &&
    subPath.split("/").at(-1)?.toLocaleLowerCase("en-US") === "skill.md"
  ) {
    subPath = subPath.slice(0, -"/SKILL.md".length);
  }
  if (!subPath) return undefined;
  return { commit, ref, subPath: cleanSubPath(subPath) };
}

async function loadRepository(repo) {
  let remoteRefs = { refs: new Map(), headCommit: undefined };
  if (repo.needsHead || repo.declaredRequests.length > 0) {
    const remote = await run(
      "git",
      repo.declaredRequests.length > 0
        ? ["ls-remote", repo.repoUrl]
        : ["ls-remote", repo.repoUrl, "HEAD"],
    );
    if (remote.code !== 0 || !remote.stdout.trim()) {
      const detail = `${remote.stderr}\n${remote.stdout}`.trim();
      if (isRepositoryNotFound(detail)) {
        return { ...repo, error: "repository-not-found" };
      }
      throw new Error(`Git 仓库核验未完成：${repo.repoUrl}\n${detail}`);
    }
    remoteRefs = parseRemoteRefs(remote.stdout);
  }
  const declaredResolutions = new Map();
  for (const request of repo.declaredRequests) {
    declaredResolutions.set(
      request.key,
      resolveDeclaredRefAndPath(request, remoteRefs),
    );
  }
  const commits = new Set(
    [
      remoteRefs.headCommit,
      ...repo.commits,
      ...[...declaredResolutions.values()].map((value) => value?.commit),
    ].filter(Boolean),
  );
  const trees = new Map();
  for (const commit of commits) {
    trees.set(commit, await loadTree(repo.repoUrl, commit));
  }
  return {
    ...repo,
    headCommit: remoteRefs.headCommit,
    declaredResolutions,
    trees,
  };
}

function parseFrontmatterName(markdown) {
  if (!markdown.startsWith("---")) return undefined;
  const end = markdown.indexOf("\n---", 3);
  if (end < 0) return undefined;
  const frontmatter = markdown.slice(3, end);
  const match = frontmatter.match(/^name\s*:\s*(.+?)\s*$/im);
  return match?.[1].trim().replace(/^['"]|['"]$/g, "");
}

async function loadFrontmatterIndex(repository, commit, skillFiles) {
  repository.frontmatterIndexes ??= new Map();
  const cached = repository.frontmatterIndexes.get(commit);
  if (cached) return cached;

  const { owner, repository: repositoryName } = githubRepoParts(
    repository.repoUrl,
  );
  const contents = await mapLimit(skillFiles, CONCURRENCY, async (skillFile) => {
    const encodedPath = skillFile
      .split("/")
      .map(encodeURIComponent)
      .join("/");
    const result = await fetchGithub(
      `https://raw.githubusercontent.com/${owner}/${repositoryName}/${commit}/${encodedPath}`,
      "text/plain",
    );
    if (result.error) {
      throw new Error(
        `固定提交中无法读取 Skill 元数据：${repository.repoUrl}@${commit}:${skillFile}`,
      );
    }
    return [skillFile, await result.response.text()];
  });
  const index = new Map();
  for (const [skillFile, content] of contents) {
    const frontmatterName = parseFrontmatterName(content);
    if (!frontmatterName) continue;
    const key = frontmatterName.toLocaleLowerCase("en-US");
    const paths = index.get(key) ?? [];
    paths.push(skillFile);
    index.set(key, paths);
  }
  repository.frontmatterIndexes.set(commit, index);
  return index;
}

async function exactFrontmatterMatches(entry, repository, commit, skillFiles) {
  const expected = new Set(
    [entry.name, entry.name.split("/").at(-1)]
      .filter(Boolean)
      .map((value) => value.trim().toLocaleLowerCase("en-US")),
  );
  const index = await loadFrontmatterIndex(repository, commit, skillFiles);
  const matches = new Set();
  for (const expectedName of expected) {
    for (const skillFile of index.get(expectedName) ?? []) {
      matches.add(skillFile);
    }
  }
  return [...matches];
}

async function resolveSkillPath(entry, repository, commit, skillFiles) {
  const declaredSubPath = entry.candidate?.subPath;
  if (declaredSubPath !== undefined) {
    const declaredSkillFile = skillFileFor(declaredSubPath);
    if (skillFiles.includes(declaredSkillFile)) {
      return {
        subPath: cleanSubPath(declaredSubPath),
        method: entry.candidate.method,
      };
    }
  } else if (!entry.candidate?.requiresExactName) {
    if (skillFiles.includes("SKILL.md")) {
      return { subPath: "", method: "repository-root" };
    }
    if (skillFiles.length === 1) {
      return {
        subPath: skillDirectory(skillFiles[0]),
        method: "unique-skill-file",
      };
    }
  }

  const slug = entry.name.split("/").at(-1)?.toLocaleLowerCase("en-US");
  const slugMatches = skillFiles.filter((skillFile) => {
    const parentName = basename(skillDirectory(skillFile));
    return parentName.toLocaleLowerCase("en-US") === slug;
  });
  if (slugMatches.length === 1) {
    return {
      subPath: skillDirectory(slugMatches[0]),
      method: entry.candidate?.requiresExactName
        ? entry.candidate.method
        : entry.candidate?.method === "source-page-declared-path"
          ? "source-page-exact-path-repair"
        : "unique-slug-path",
    };
  }

  const frontmatterMatches = await exactFrontmatterMatches(
    entry,
    repository,
    commit,
    skillFiles,
  );
  if (frontmatterMatches.length === 1) {
    return {
      subPath: skillDirectory(frontmatterMatches[0]),
      method: entry.candidate?.requiresExactName
        ? entry.candidate.method
        : entry.candidate?.method === "source-page-declared-path"
          ? "source-page-exact-path-repair"
        : "exact-frontmatter-name",
    };
  }
  return undefined;
}

function sourceStatusForUnmappedProject(project) {
  if (hasAuditedInstallMismatch(project.sourceUrl)) return "pending";
  return inferInstallStatus(project.sourceUrl, false) === "reference"
    ? "reference"
    : "pending";
}

async function buildEntries() {
  const [anbeime, voltagent] = await Promise.all([
    readJson("client/src/data/anbeimeSkillCatalog.generated.json"),
    readJson("client/src/data/voltagentSkillCatalog.generated.json"),
  ]);
  const entries = [...CURATED_PROJECTS];
  const anbeimeEntries = anbeime.projects.map((project) => ({
      id: project.id,
      name: project.name,
      kind: project.kind,
      sourceUrl: `${anbeime.source.repoUrl}/tree/${anbeime.source.commit}/${project.subPath}`,
      candidate: {
        repoUrl: anbeime.source.repoUrl,
        subPath: project.subPath,
        verifiedCommit: anbeime.source.commit,
        method: "catalog-snapshot",
      },
    }));
  entries.push(...anbeimeEntries);

  const seenInstallSources = new Set(
    [...CURATED_PROJECTS, ...anbeimeEntries]
      .filter((entry) => entry.candidate)
      .map(
        (entry) =>
          `${normalizeRepoUrl(entry.candidate.repoUrl).toLowerCase()}#${cleanSubPath(entry.candidate.subPath)}`,
      ),
  );
  for (const project of voltagent.projects) {
      const auditedSource = auditedInstallSource(project.sourceUrl);
      const candidate = project.installSource ?? auditedSource;
      if (candidate) {
        const sourceKey = `${normalizeRepoUrl(candidate.repoUrl).toLowerCase()}#${cleanSubPath(candidate.subPath)}`;
        if (seenInstallSources.has(sourceKey)) continue;
        seenInstallSources.add(sourceKey);
        entries.push({
          id: project.id,
          name: project.name,
          kind: project.kind,
          sourceUrl: project.sourceUrl,
          candidate: {
            ...candidate,
            method: candidate.verifiedCommit
              ? "previously-verified"
              : project.installSource
                ? "catalog-declared-path"
                : "audited-source-path",
          },
        });
        continue;
      }
      const rootRepo = parseGithubRepoUrl(project.sourceUrl);
      if (rootRepo) {
        entries.push({
          id: project.id,
          name: project.name,
          kind: project.kind,
          sourceUrl: project.sourceUrl,
          candidate: { repoUrl: rootRepo },
        });
        continue;
      }
      if (isOfficialSkillsUrl(project.sourceUrl)) {
        entries.push({
          id: project.id,
          name: project.name,
          kind: project.kind,
          sourceUrl: project.sourceUrl,
          resolver: "officialskills-page",
        });
        continue;
      }
      const status = sourceStatusForUnmappedProject(project);
      entries.push({
        id: project.id,
        name: project.name,
        kind: project.kind,
        sourceUrl: project.sourceUrl,
        forcedStatus: status,
        reasonCode: "no-install-source",
        reason: FAILURE_MESSAGES["no-install-source"],
      });
  }
  return entries;
}

function generateModule(evidence) {
  return `// Generated by scripts/verify-skill-install-sources.mjs. Do not edit manually.\n\nexport type SkillVerificationStatus = "verified" | "pending" | "reference";\n\nexport type SkillVerificationMethod =\n  | "audited-source-path"\n  | "catalog-declared-path"\n  | "catalog-snapshot"\n  | "curated-path"\n  | "exact-frontmatter-name"\n  | "previously-verified"\n  | "repository-root"\n  | "source-page-command-exact-match"\n  | "source-page-declared-path"\n  | "source-page-exact-path-repair"\n  | "unique-skill-file"\n  | "unique-slug-path";\n\nexport type SkillVerificationFailureCode =\n  | "ambiguous-skill-path"\n  | "declared-path-missing"\n  | "no-install-source"\n  | "no-skill-file"\n  | "ref-not-found"\n  | "repository-not-found"\n  | "source-page-declaration-ambiguous"\n  | "source-page-declaration-missing"\n  | "source-page-not-found";\n\nexport interface SkillSourceVerificationEvidence {\n  status: SkillVerificationStatus;\n  sourceUrl: string;\n  repoUrl?: string;\n  subPath?: string;\n  verifiedCommit?: string;\n  declaredUrl?: string;\n  method?: SkillVerificationMethod;\n  reasonCode?: SkillVerificationFailureCode;\n  reason?: string;\n}\n\nexport const SKILL_SOURCE_VERIFICATION = ${JSON.stringify(evidence, null, 2)} as const satisfies Record<string, SkillSourceVerificationEvidence>;\n`;
}

async function main() {
  const catalogEntries = await buildEntries();
  const officialResolution = await resolveOfficialSourceEntries(catalogEntries);
  const entries = officialResolution.entries;
  const repositories = new Map();
  for (const entry of entries) {
    if (!entry.candidate?.repoUrl) continue;
    const repoUrl = normalizeRepoUrl(entry.candidate.repoUrl);
    const key = repoUrl.toLocaleLowerCase("en-US");
    const repository = repositories.get(key) ?? {
      repoUrl,
      commits: new Set(),
      needsHead: false,
      declaredRequests: new Map(),
    };
    if (entry.candidate.verifiedCommit) {
      repository.commits.add(entry.candidate.verifiedCommit.toLowerCase());
    } else if (entry.candidate.declaredRefAndPath) {
      const request = {
        key: `${entry.candidate.declaredAction}:${entry.candidate.declaredRefAndPath}`,
        action: entry.candidate.declaredAction,
        refAndPath: entry.candidate.declaredRefAndPath,
      };
      repository.declaredRequests.set(request.key, request);
    } else {
      repository.needsHead = true;
    }
    repositories.set(key, repository);
  }

  const repositoryList = [...repositories.values()].map((repository) => ({
      ...repository,
      commits: [...repository.commits],
      declaredRequests: [...repository.declaredRequests.values()],
    }));
  console.log(
    `开始核验 ${entries.length} 条目录记录，涉及 ${repositoryList.length} 个 GitHub 仓库……`,
  );
  const loadedRepositories = await mapLimit(
      repositoryList,
      CONCURRENCY,
      (repository) => loadRepository(repository),
  );
    const repositoryByUrl = new Map(
      loadedRepositories.map((repository) => [
        repository.repoUrl.toLocaleLowerCase("en-US"),
        repository,
      ]),
    );

    const evidence = {};
    let completed = 0;
    for (const entry of entries) {
      if (!entry.candidate?.repoUrl) {
        evidence[entry.id] = failureEvidence(
          entry,
          entry.reasonCode ?? "no-install-source",
          { reason: entry.reason },
        );
        continue;
      }
      const repoUrl = normalizeRepoUrl(entry.candidate.repoUrl);
      const repository = repositoryByUrl.get(
        repoUrl.toLocaleLowerCase("en-US"),
      );
      if (!repository || repository.error) {
        evidence[entry.id] = failureEvidence(
          entry,
          repository?.error ?? "repository-not-found",
          { repoUrl, declaredUrl: entry.candidate.declaredUrl },
        );
        continue;
      }

      let effectiveEntry = entry;
      let commit = entry.candidate.verifiedCommit?.toLocaleLowerCase("en-US");
      if (!commit && entry.candidate.declaredRefAndPath) {
        const requestKey = `${entry.candidate.declaredAction}:${entry.candidate.declaredRefAndPath}`;
        const resolution = repository.declaredResolutions.get(requestKey);
        if (!resolution) {
          evidence[entry.id] = failureEvidence(entry, "ref-not-found", {
            repoUrl,
            declaredUrl: entry.candidate.declaredUrl,
          });
          continue;
        }
        commit = resolution.commit;
        effectiveEntry = {
          ...entry,
          candidate: {
            ...entry.candidate,
            subPath: resolution.subPath,
          },
        };
      }
      commit ??= repository.headCommit;
      if (!commit) {
        evidence[entry.id] = failureEvidence(entry, "ref-not-found", {
          repoUrl,
          declaredUrl: entry.candidate.declaredUrl,
        });
        continue;
      }

      const tree = repository.trees.get(commit);
      if (!tree || tree.error) {
        evidence[entry.id] = failureEvidence(
          entry,
          tree?.error ?? "ref-not-found",
          {
            repoUrl,
            verifiedCommit: commit,
            declaredUrl: entry.candidate.declaredUrl,
          },
        );
        continue;
      }
      if (tree.skillFiles.length === 0) {
        evidence[entry.id] = failureEvidence(entry, "no-skill-file", {
          repoUrl,
          verifiedCommit: commit,
          declaredUrl: entry.candidate.declaredUrl,
        });
        continue;
      }
      const resolvedPath = await resolveSkillPath(
        effectiveEntry,
        repository,
        commit,
        tree.skillFiles,
      );
      if (!resolvedPath) {
        const reasonCode = effectiveEntry.candidate.subPath
          ? "declared-path-missing"
          : "ambiguous-skill-path";
        evidence[entry.id] = failureEvidence(entry, reasonCode, {
          repoUrl,
          verifiedCommit: commit,
          declaredUrl: entry.candidate.declaredUrl,
        });
        continue;
      }
      evidence[entry.id] = {
        status: "verified",
        sourceUrl: entry.sourceUrl,
        repoUrl,
        subPath: resolvedPath.subPath,
        verifiedCommit: commit,
        ...(entry.candidate.declaredUrl
          ? { declaredUrl: entry.candidate.declaredUrl }
          : {}),
        method: resolvedPath.method,
      };
      completed += 1;
    }

    const orderedEvidence = Object.fromEntries(
      Object.entries(evidence).sort(([left], [right]) =>
        left.localeCompare(right, "en"),
      ),
    );
    const temporaryOutput = `${OUTPUT_PATH}.tmp`;
    await writeFile(temporaryOutput, generateModule(orderedEvidence), "utf8");
    await rename(temporaryOutput, OUTPUT_PATH);

    const statusCounts = Object.values(orderedEvidence).reduce(
      (counts, item) => {
        counts[item.status] += 1;
        return counts;
      },
      { verified: 0, pending: 0, reference: 0 },
    );
    const reasonCounts = Object.values(orderedEvidence).reduce((counts, item) => {
      if (item.reasonCode) {
        counts[item.reasonCode] = (counts[item.reasonCode] ?? 0) + 1;
      }
      return counts;
    }, {});
    console.log(`核验完成：${completed} 项具备固定提交安装证据`);
    console.log(
      `OfficialSkills 待核验来源页：${officialResolution.targetCount} 项，解析出 ${officialResolution.declaredCount} 项 GitHub 声明`,
    );
    console.table(officialResolution.reasonCounts);
    console.table(statusCounts);
    console.table(reasonCounts);
  console.log(`证据已写入 ${OUTPUT_PATH}`);
}

await main();

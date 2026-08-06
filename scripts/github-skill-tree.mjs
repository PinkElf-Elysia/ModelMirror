import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";

export class GithubSkillTreeTransientError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "GithubSkillTreeTransientError";
  }
}

function cleanSubPath(value = "") {
  return value.replaceAll("\\", "/").replace(/^\/+|\/+$/g, "");
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

function isWithinScope(skillFile, scopeSubPath) {
  const scope = cleanSubPath(scopeSubPath);
  return !scope || skillFile === `${scope}/SKILL.md` || skillFile.startsWith(`${scope}/`);
}

function compareCanonicalPaths(left, right) {
  const segmentDifference = left.split("/").length - right.split("/").length;
  if (segmentDifference !== 0) return segmentDifference;
  const lengthDifference = left.length - right.length;
  if (lengthDifference !== 0) return lengthDifference;
  return left.localeCompare(right, "en");
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

function parseLsTree(output) {
  const skillFiles = [];
  const directoryTrees = new Map();
  for (const token of output.split("\0")) {
    if (!token) continue;
    const match = token.match(
      /^(\d{6})\s+(blob|tree|commit)\s+([a-f0-9]{40})\t([\s\S]+)$/i,
    );
    if (!match) {
      throw new GithubSkillTreeTransientError("Git Skill 树响应格式无效");
    }
    const [, , type, objectId, path] = match;
    if (type === "commit") {
      continue;
    }
    if (type === "tree") {
      directoryTrees.set(path, objectId.toLowerCase());
    } else if (path === "SKILL.md" || path.endsWith("/SKILL.md")) {
      skillFiles.push(path);
    }
  }
  skillFiles.sort((left, right) => left.localeCompare(right, "en"));
  return { skillFiles, directoryTrees };
}

export function classifySkillScope({ skillFiles, scopeSubPath = "" }) {
  const scope = cleanSubPath(scopeSubPath);
  const scopedFiles = [...new Set(skillFiles)]
    .filter((skillFile) => isWithinScope(skillFile, scope))
    .sort((left, right) => left.localeCompare(right, "en"));
  const scopeSkillFile = skillFileFor(scope);
  const skillFileSet = new Set(scopedFiles);
  const topMemberSubPaths = [];

  for (const skillFile of scopedFiles) {
    const directory = skillDirectory(skillFile);
    if (directory === scope) continue;
    const relativeDirectory = scope
      ? directory.slice(scope.length).replace(/^\/+/, "")
      : directory;
    const parts = relativeDirectory.split("/").filter(Boolean);
    let nested = false;
    for (let index = 1; index < parts.length; index += 1) {
      const ancestorRelative = parts.slice(0, index).join("/");
      const ancestor = scope ? `${scope}/${ancestorRelative}` : ancestorRelative;
      if (skillFileSet.has(skillFileFor(ancestor))) {
        nested = true;
        break;
      }
    }
    if (!nested) topMemberSubPaths.push(directory);
  }

  if (skillFileSet.has(scopeSkillFile)) {
    return {
      status: "resolved",
      verifiedKind: scopedFiles.length > 1 ? "skillset" : "skill",
      installMode: "direct",
      skillsetMode: scopedFiles.length > 1 ? "package" : undefined,
      scopeSubPath: scope,
      resolvedSubPath: scope,
      skillDocumentCount: scopedFiles.length,
      topMemberSubPaths: [scope],
      nestedSkillCount: Math.max(0, scopedFiles.length - 1),
    };
  }

  if (topMemberSubPaths.length === 1) {
    const resolvedSubPath = topMemberSubPaths[0];
    const nestedSkillCount = scopedFiles.filter(
      (skillFile) =>
        skillFile !== skillFileFor(resolvedSubPath) &&
        skillFile.startsWith(`${resolvedSubPath}/`),
    ).length;
    return {
      status: "resolved",
      verifiedKind: nestedSkillCount > 0 ? "skillset" : "skill",
      installMode: "direct",
      skillsetMode: nestedSkillCount > 0 ? "package" : undefined,
      scopeSubPath: scope,
      resolvedSubPath,
      skillDocumentCount: scopedFiles.length,
      topMemberSubPaths,
      nestedSkillCount,
    };
  }

  if (topMemberSubPaths.length >= 2) {
    return {
      status: "resolved",
      verifiedKind: "skillset",
      installMode: "members",
      skillsetMode: "members",
      scopeSubPath: scope,
      skillDocumentCount: scopedFiles.length,
      topMemberSubPaths,
      nestedSkillCount: scopedFiles.length - topMemberSubPaths.length,
    };
  }

  return {
    status: "empty",
    scopeSubPath: scope,
    skillDocumentCount: 0,
    topMemberSubPaths: [],
    nestedSkillCount: 0,
  };
}

function memberIdFor(repoUrl, verifiedCommit, subPath) {
  const digest = createHash("sha256")
    .update(`${repoUrl.toLowerCase()}#${verifiedCommit.toLowerCase()}#${subPath}`)
    .digest("hex")
    .slice(0, 20);
  return `skillset-member-${digest}`;
}

export function buildSkillSetMembers({
  repoUrl,
  verifiedCommit,
  classification,
  skillFiles,
  directoryTrees,
}) {
  if (
    classification.status !== "resolved" ||
    classification.installMode !== "members"
  ) {
    throw new Error("只有成员模式 SkillSet 可以生成成员注册表");
  }

  const candidates = classification.topMemberSubPaths.map((subPath) => {
    const directoryTreeSha = directoryTrees.get(subPath);
    if (!directoryTreeSha) {
      throw new GithubSkillTreeTransientError(
        `Git 树缺少 SkillSet 成员目录：${repoUrl}@${verifiedCommit}:${subPath}`,
      );
    }
    const nestedSkillCount = skillFiles.filter(
      (skillFile) =>
        skillFile !== skillFileFor(subPath) && skillFile.startsWith(`${subPath}/`),
    ).length;
    return {
      id: memberIdFor(repoUrl, verifiedCommit, subPath),
      name: basename(subPath),
      repoUrl,
      subPath,
      verifiedCommit,
      directoryTreeSha,
      nestedSkillCount,
    };
  });

  const canonicalByTree = new Map();
  for (const candidate of candidates) {
    const current = canonicalByTree.get(candidate.directoryTreeSha);
    if (
      !current ||
      compareCanonicalPaths(candidate.subPath, current.subPath) < 0
    ) {
      canonicalByTree.set(candidate.directoryTreeSha, candidate);
    }
  }
  const members = [...canonicalByTree.values()].sort((left, right) =>
    left.subPath.localeCompare(right.subPath, "en"),
  );
  return {
    members,
    duplicateMemberCount: candidates.length - members.length,
  };
}

class GitSkillTreeRepository {
  constructor({ repoUrl, mirrorPath, runCommand }) {
    this.repoUrl = repoUrl;
    this.mirrorPath = mirrorPath;
    this.runCommand = runCommand;
    this.scans = new Map();
  }

  async git(args, { allowFailure = false } = {}) {
    const result = await this.runCommand("git", ["-C", this.mirrorPath, ...args]);
    if (result.code !== 0 && !allowFailure) {
      throw new GithubSkillTreeTransientError(
        `Git Skill 树核验未完成：${this.repoUrl}\n${result.stderr || result.stdout}`,
      );
    }
    return result;
  }

  async ensureCommit(commit) {
    const present = await this.git(["cat-file", "-e", `${commit}^{commit}`], {
      allowFailure: true,
    });
    if (present.code === 0) return;
    await this.git(["fetch", "--depth", "1", "origin", commit]);
  }

  async scan(commit, { requireHead = false } = {}) {
    const normalizedCommit = commit.toLowerCase();
    const cacheKey = `${normalizedCommit}:${requireHead}`;
    const cached = this.scans.get(cacheKey);
    if (cached) return cached;
    const promise = this.loadScan(normalizedCommit, requireHead);
    this.scans.set(cacheKey, promise);
    return promise;
  }

  async loadScan(commit, requireHead) {
    if (requireHead) {
      const head = await this.git(["rev-parse", "HEAD"]);
      if (head.stdout.trim().toLowerCase() !== commit) {
        throw new GithubSkillTreeTransientError(
          `SkillSet 核验期间默认分支发生变化：${this.repoUrl}`,
        );
      }
    }
    await this.ensureCommit(commit);
    const tree = await this.git(["ls-tree", "-r", "-t", "-z", commit]);
    return parseLsTree(tree.stdout);
  }
}

export class GithubSkillTreePool {
  constructor({ runCommand = run, temporaryDirectory = tmpdir() } = {}) {
    this.runCommand = runCommand;
    this.temporaryDirectory = temporaryDirectory;
    this.repositories = new Map();
    this.tempRoots = new Set();
  }

  async loadRepository(repoUrl) {
    const tempRoot = await mkdtemp(
      join(this.temporaryDirectory, "modelmirror-skill-tree-"),
    );
    this.tempRoots.add(tempRoot);
    const mirrorPath = join(tempRoot, "repository.git");
    const clone = await this.runCommand("git", [
      "clone",
      "--bare",
      "--filter=blob:none",
      "--no-tags",
      "--single-branch",
      repoUrl,
      mirrorPath,
    ]);
    if (clone.code !== 0) {
      throw new GithubSkillTreeTransientError(
        `Git SkillSet 仓库读取未完成：${repoUrl}\n${clone.stderr || clone.stdout}`,
      );
    }
    return new GitSkillTreeRepository({
      repoUrl,
      mirrorPath,
      runCommand: this.runCommand,
    });
  }

  repositoryFor(repoUrl) {
    const key = repoUrl.toLowerCase();
    const cached = this.repositories.get(key);
    if (cached) return cached;
    const repository = this.loadRepository(repoUrl);
    this.repositories.set(key, repository);
    return repository;
  }

  async scan({ repoUrl, verifiedCommit, requireHead = false }) {
    const repository = await this.repositoryFor(repoUrl);
    return repository.scan(verifiedCommit, { requireHead });
  }

  async close() {
    await Promise.all(
      [...this.tempRoots].map((tempRoot) =>
        rm(tempRoot, { recursive: true, force: true }),
      ),
    );
    this.tempRoots.clear();
  }
}

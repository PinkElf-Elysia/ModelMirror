import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const MAX_RENAME_STEPS = 16;

export const GITHUB_PATH_HISTORY_FAILURE_MESSAGES = {
  "declared-path-removed":
    "来源声明的 Skill 路径已被删除，未发现可证明的完整内容重命名",
  "declared-path-never-seen":
    "当前默认分支历史中从未出现来源声明的 SKILL.md",
  "rename-chain-ambiguous":
    "Git 历史中的 Skill 重命名链不唯一、不完整或无法安全追踪",
  "multi-skillset-install-unsupported":
    "仓库包含多个 Skill，当前安装器暂不支持把仓库根目录作为一个 SkillSet 安装",
};

export class GithubPathHistoryTransientError extends Error {
  constructor(message, options) {
    super(message, options);
    this.name = "GithubPathHistoryTransientError";
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

function parseNameStatus(output) {
  const tokens = output.split("\0");
  const changes = [];
  let index = 0;
  while (index < tokens.length) {
    const status = tokens[index++];
    if (!status) continue;
    if (/^[RC]\d{3}$/.test(status)) {
      const fromPath = tokens[index++];
      const toPath = tokens[index++];
      if (!fromPath || !toPath) break;
      changes.push({ status, fromPath, toPath });
      continue;
    }
    const path = tokens[index++];
    if (!path) break;
    changes.push({ status, path });
  }
  return changes;
}

function renameStep(commit, fromFile, toFile) {
  return {
    commit,
    fromSubPath: skillDirectory(fromFile),
    toSubPath: skillDirectory(toFile),
  };
}

export async function followExactRenameChain({
  declaredSubPath,
  currentSkillFiles,
  lookupTransition,
  maxSteps = MAX_RENAME_STEPS,
}) {
  const declaredFile = skillFileFor(declaredSubPath);
  const currentFiles = new Set(currentSkillFiles);
  const seen = new Set([declaredFile]);
  const renameChain = [];
  let currentFile = declaredFile;

  for (let stepIndex = 0; stepIndex < maxSteps; stepIndex += 1) {
    const transition = await lookupTransition(currentFile);
    if (transition.kind === "never-seen") {
      return {
        status: renameChain.length === 0 ? "never-seen" : "ambiguous",
        renameChain,
      };
    }
    if (transition.kind === "removed") {
      return {
        status: "removed",
        lastHistoryCommit: transition.commit,
        renameChain,
      };
    }
    if (transition.kind !== "renamed" || transition.candidates.length !== 1) {
      return {
        status: "ambiguous",
        lastHistoryCommit: transition.commit,
        renameChain,
      };
    }

    const targetFile = transition.candidates[0];
    if (
      !(targetFile === "SKILL.md" || targetFile.endsWith("/SKILL.md")) ||
      seen.has(targetFile)
    ) {
      return {
        status: "ambiguous",
        lastHistoryCommit: transition.commit,
        renameChain,
      };
    }
    renameChain.push(renameStep(transition.commit, currentFile, targetFile));
    if (currentFiles.has(targetFile)) {
      return {
        status: "renamed",
        subPath: skillDirectory(targetFile),
        renameChain,
      };
    }
    seen.add(targetFile);
    currentFile = targetFile;
  }

  return { status: "ambiguous", renameChain };
}

class GitHistoryRepository {
  constructor({ repoUrl, headCommit, mirrorPath, runCommand }) {
    this.repoUrl = repoUrl;
    this.headCommit = headCommit;
    this.mirrorPath = mirrorPath;
    this.runCommand = runCommand;
    this.transitions = new Map();
  }

  async git(args) {
    const result = await this.runCommand("git", [
      "-C",
      this.mirrorPath,
      ...args,
    ]);
    if (result.code !== 0) {
      throw new GithubPathHistoryTransientError(
        `Git 历史核验未完成：${this.repoUrl}@${this.headCommit}\n${result.stderr || result.stdout}`,
      );
    }
    return result.stdout;
  }

  async lookupTransition(skillFile) {
    const cached = this.transitions.get(skillFile);
    if (cached) return cached;
    const promise = this.loadTransition(skillFile);
    this.transitions.set(skillFile, promise);
    return promise;
  }

  async loadTransition(skillFile) {
    const commit = (
      await this.git([
        "log",
        "-1",
        "--format=%H",
        this.headCommit,
        "--",
        skillFile,
      ])
    ).trim();
    if (!commit) return { kind: "never-seen" };

    const parents = (
      await this.git(["rev-list", "--parents", "-n", "1", commit])
    )
      .trim()
      .split(/\s+/)
      .slice(1);
    if (parents.length !== 1) {
      return { kind: "ambiguous", commit, candidates: [] };
    }

    const changes = parseNameStatus(
      await this.git([
        "diff-tree",
        "-r",
        "-M100%",
        "--no-commit-id",
        "--name-status",
        "-z",
        parents[0],
        commit,
      ]),
    );
    const candidates = changes
      .filter(
        (change) =>
          change.status === "R100" && change.fromPath === skillFile,
      )
      .map((change) => change.toPath);
    if (candidates.length === 1) {
      return { kind: "renamed", commit, candidates };
    }
    if (candidates.length > 1) {
      return { kind: "ambiguous", commit, candidates };
    }
    return { kind: "removed", commit };
  }

  resolve(declaredSubPath, currentSkillFiles) {
    return followExactRenameChain({
      declaredSubPath,
      currentSkillFiles,
      lookupTransition: (skillFile) => this.lookupTransition(skillFile),
    });
  }
}

export class GithubSkillPathHistoryPool {
  constructor({ runCommand = run, temporaryDirectory = tmpdir() } = {}) {
    this.runCommand = runCommand;
    this.temporaryDirectory = temporaryDirectory;
    this.repositories = new Map();
    this.tempRoots = new Set();
  }

  async loadRepository(repoUrl, headCommit) {
    const tempRoot = await mkdtemp(
      join(this.temporaryDirectory, "modelmirror-skill-history-"),
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
      throw new GithubPathHistoryTransientError(
        `Git 历史仓库读取未完成：${repoUrl}\n${clone.stderr || clone.stdout}`,
      );
    }
    const head = await this.runCommand("git", [
      "-C",
      mirrorPath,
      "rev-parse",
      "HEAD",
    ]);
    if (head.code !== 0 || head.stdout.trim().toLowerCase() !== headCommit) {
      throw new GithubPathHistoryTransientError(
        `Git 历史核验期间默认分支发生变化：${repoUrl}`,
      );
    }
    return new GitHistoryRepository({
      repoUrl,
      headCommit,
      mirrorPath,
      runCommand: this.runCommand,
    });
  }

  repositoryFor(repoUrl, headCommit) {
    const normalizedCommit = headCommit.toLowerCase();
    const key = `${repoUrl.toLowerCase()}#${normalizedCommit}`;
    const existing = this.repositories.get(key);
    if (existing) return existing;
    const repository = this.loadRepository(repoUrl, normalizedCommit);
    this.repositories.set(key, repository);
    return repository;
  }

  async resolve({ repoUrl, headCommit, declaredSubPath, currentSkillFiles }) {
    const repository = await this.repositoryFor(repoUrl, headCommit);
    return repository.resolve(declaredSubPath, currentSkillFiles);
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

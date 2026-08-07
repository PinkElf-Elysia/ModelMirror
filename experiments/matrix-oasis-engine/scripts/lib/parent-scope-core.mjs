import { realpathSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

export const MODULE_PREFIX = "experiments/matrix-oasis-engine";

export class ParentScopeError extends Error {
  constructor(code) {
    super(code);
    this.name = "ParentScopeError";
    this.code = code;
  }
}

function fail(code) {
  throw new ParentScopeError(code);
}

export function parseParentScopeArgs(args) {
  if (args.length === 0) {
    fail("PARENT_SCOPE_BASE_REQUIRED");
  }

  if (args.length !== 2 || args[0] !== "--base") {
    fail("PARENT_SCOPE_ARGUMENT_ERROR");
  }

  const base = args[1];
  if (!/^[0-9a-f]{40}$/i.test(base)) {
    fail("PARENT_SCOPE_BASE_INVALID");
  }

  return { base: base.toLowerCase() };
}

function runGit(cwd, args, { acceptedStatuses = [0], failureCode = "PARENT_SCOPE_GIT_FAILED" } = {}) {
  const result = spawnSync("git", args, {
    cwd,
    encoding: null,
    windowsHide: true,
    maxBuffer: 16 * 1024 * 1024,
  });

  if (result.error || result.status === null || !acceptedStatuses.includes(result.status)) {
    fail(failureCode);
  }

  return result;
}

function decodeUtf8(buffer) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer);
  } catch {
    fail("PARENT_SCOPE_PATH_ENCODING_INVALID");
  }
}

function parseNulPaths(buffer) {
  const paths = [];
  let start = 0;

  for (let index = 0; index < buffer.length; index += 1) {
    if (buffer[index] !== 0) {
      continue;
    }

    if (index > start) {
      paths.push(decodeUtf8(buffer.subarray(start, index)));
    }
    start = index + 1;
  }

  if (start !== buffer.length) {
    fail("PARENT_SCOPE_GIT_OUTPUT_INVALID");
  }

  return paths;
}

function normalizeGitPath(candidate) {
  if (candidate.length === 0 || candidate.includes("\0")) {
    fail("PARENT_SCOPE_GIT_OUTPUT_INVALID");
  }

  const normalized = candidate.replaceAll("\\", "/");
  if (
    normalized.startsWith("/") ||
    /^[a-z]:\//i.test(normalized) ||
    normalized.split("/").some((segment) => segment === "..")
  ) {
    fail("PARENT_SCOPE_GIT_OUTPUT_INVALID");
  }

  return normalized.replace(/^\.\//, "");
}

function isInsideModule(candidate) {
  return candidate === MODULE_PREFIX || candidate.startsWith(`${MODULE_PREFIX}/`);
}

const MATRIX_OASIS_EXISTING = [
  /^client\/src\/pages\/MatrixOasisPage\.(?:css|tsx)$/i,
  /^client\/src\/assets\/matrix-oasis(?:\/|$)/i,
];

const ROOT_CONFIG = [
  /^\.env(?:\..+)?$/i,
  /^\.gitattributes$/i,
  /^\.gitignore$/i,
  /^\.node-version$/i,
  /^\.npmrc$/i,
  /^\.nvmrc$/i,
  /^AGENTS\.md$/i,
  /^bun\.lockb?$/i,
  /^npm-shrinkwrap\.json$/i,
  /^package(?:-lock)?\.json$/i,
  /^pnpm-lock\.yaml$/i,
  /^pyproject\.toml$/i,
  /^requirements(?:-[^.\/]+)?\.txt$/i,
  /^tsconfig(?:\.[^.\/]+)?\.json$/i,
  /^uv\.lock$/i,
  /^vite\.config\.[^/]+$/i,
  /^yarn\.lock$/i,
];

export function classifyParentPath(candidate) {
  const normalized = normalizeGitPath(candidate);

  if (isInsideModule(normalized)) {
    return null;
  }

  if (MATRIX_OASIS_EXISTING.some((pattern) => pattern.test(normalized))) {
    return "PARENT_GUARD_MATRIX_OASIS_CHANGED";
  }

  if (
    /^(?:docker-compose(?:\.[^/]*)?\.ya?ml|\.dockerignore)$/i.test(normalized) ||
    /(?:^|\/)(?:Dockerfile(?:\.[^/]*)?|[^/]+\.Dockerfile(?:\.[^/]*)?)$/i.test(normalized)
  ) {
    return "PARENT_GUARD_DOCKER_CHANGED";
  }

  if (/^\.github(?:\/|$)/i.test(normalized)) {
    return "PARENT_GUARD_GITHUB_CHANGED";
  }

  if (/^client(?:\/|$)/i.test(normalized)) {
    return "PARENT_GUARD_CLIENT_CHANGED";
  }

  if (/^server(?:\/|$)/i.test(normalized)) {
    return "PARENT_GUARD_SERVER_CHANGED";
  }

  if (!normalized.includes("/") && ROOT_CONFIG.some((pattern) => pattern.test(normalized))) {
    return "PARENT_GUARD_ROOT_CONFIG_CHANGED";
  }

  return "PARENT_SCOPE_PATH_OUTSIDE_MODULE";
}

function readGitRoot(moduleRoot) {
  const result = runGit(moduleRoot, ["rev-parse", "--show-toplevel"]);
  const output = decodeUtf8(result.stdout).trim();
  if (output.length === 0) {
    fail("PARENT_SCOPE_GIT_OUTPUT_INVALID");
  }

  try {
    return realpathSync(output);
  } catch {
    fail("PARENT_SCOPE_GIT_ROOT_INVALID");
  }
}

function assertModuleLocation(moduleRoot, gitRoot) {
  let resolvedModuleRoot;
  try {
    resolvedModuleRoot = realpathSync(moduleRoot);
  } catch {
    fail("PARENT_SCOPE_MODULE_ROOT_INVALID");
  }

  const relative = path.relative(gitRoot, resolvedModuleRoot).replaceAll(path.sep, "/");
  if (relative === "") {
    fail("PARENT_SCOPE_STANDALONE_UNSUPPORTED");
  }
  if (relative !== MODULE_PREFIX) {
    fail("PARENT_SCOPE_MODULE_LOCATION_INVALID");
  }
}

function assertBase(gitRoot, base) {
  const exists = runGit(gitRoot, ["cat-file", "-e", `${base}^{commit}`], {
    acceptedStatuses: [0, 1, 128],
  });
  if (exists.status !== 0) {
    fail("PARENT_SCOPE_BASE_NOT_FOUND");
  }

  const ancestor = runGit(gitRoot, ["merge-base", "--is-ancestor", base, "HEAD"], {
    acceptedStatuses: [0, 1],
  });
  if (ancestor.status === 1) {
    fail("PARENT_SCOPE_BASE_NOT_ANCESTOR");
  }
}

function collectPaths(gitRoot, base) {
  const commands = [
    ["committed", ["diff", "--name-only", "--no-renames", "-z", `${base}...HEAD`, "--"]],
    ["unstaged", ["diff", "--name-only", "--no-renames", "-z", "--"]],
    ["staged", ["diff", "--cached", "--name-only", "--no-renames", "-z", "--"]],
    ["untracked", ["ls-files", "--others", "--exclude-standard", "-z", "--"]],
  ];
  const collected = [];

  for (const [source, args] of commands) {
    const result = runGit(gitRoot, args);
    for (const candidate of parseNulPaths(result.stdout)) {
      collected.push({ source, path: normalizeGitPath(candidate) });
    }
  }

  return collected;
}

export function checkParentScope({ moduleRoot, base, expectedBase }) {
  if (!/^[0-9a-f]{40}$/i.test(base ?? "")) {
    fail("PARENT_SCOPE_BASE_INVALID");
  }
  if (!/^[0-9a-f]{40}$/i.test(expectedBase ?? "")) {
    fail("PARENT_SCOPE_FIXED_BASE_INVALID");
  }
  if (base.toLowerCase() !== expectedBase.toLowerCase()) {
    fail("PARENT_SCOPE_BASE_MISMATCH");
  }

  const gitRoot = readGitRoot(moduleRoot);
  assertModuleLocation(moduleRoot, gitRoot);
  assertBase(gitRoot, base);

  const paths = collectPaths(gitRoot, base);
  const violations = [];
  const seen = new Set();

  for (const entry of paths) {
    const code = classifyParentPath(entry.path);
    if (!code) {
      continue;
    }

    const key = `${code}\0${entry.source}\0${entry.path}`;
    if (!seen.has(key)) {
      seen.add(key);
      violations.push({ code, source: entry.source, path: entry.path });
    }
  }

  if (violations.length > 0) {
    const error = new ParentScopeError(violations[0].code);
    error.violations = violations;
    throw error;
  }

  return {
    status: "ok",
    checkedEntries: paths.length,
    uniqueChangedPaths: new Set(paths.map((entry) => entry.path)).size,
  };
}

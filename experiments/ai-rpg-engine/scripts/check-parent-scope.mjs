import { readFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { ALLOWED_CHANGE_PREFIXES, FIXED_BASE } from "./check-boundary.mjs";

const GENERATED_SEGMENTS = new Set(["node_modules", "dist", "coverage", "logs", "test-reports"]);

function runGit(repositoryRoot, args, allowExitOne = false) {
  const result = spawnSync("git", args, {
    cwd: repositoryRoot,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
    windowsHide: true,
  });
  if (result.error || (result.status !== 0 && !(allowExitOne && result.status === 1))) {
    const error = new Error("git operation failed");
    error.code = "PARENT_SCOPE_GIT_ERROR";
    throw error;
  }
  return result;
}

function nulPaths(output) {
  return output.split("\u0000").filter(Boolean).map((entry) => entry.replaceAll("\\", "/"));
}

export function parseBaseArgument(args) {
  if (args.length !== 2 || args[0] !== "--base" || !/^[0-9a-f]{40}$/u.test(args[1])) {
    const error = new Error("invalid arguments");
    error.code = "PARENT_SCOPE_ARGUMENT_ERROR";
    throw error;
  }
  if (args[1] !== FIXED_BASE) {
    const error = new Error("base mismatch");
    error.code = "PARENT_SCOPE_FIXED_BASE_MISMATCH";
    throw error;
  }
  return args[1];
}

export function validateChangedPaths(paths) {
  const diagnostics = [];
  for (const rawPath of [...new Set(paths)].sort()) {
    const candidate = rawPath.replaceAll("\\", "/");
    if (candidate.startsWith("/") || /^[A-Za-z]:\//u.test(candidate) || candidate.split("/").includes("..")) {
      diagnostics.push({ code: "PARENT_SCOPE_UNSAFE_PATH", path: "" });
      continue;
    }
    if (!ALLOWED_CHANGE_PREFIXES.some((prefix) => candidate.startsWith(prefix))) {
      diagnostics.push({ code: "PARENT_SCOPE_OUTSIDE_ALLOWLIST", path: candidate });
      continue;
    }
    const segments = candidate.split("/");
    if (segments.some((segment) => GENERATED_SEGMENTS.has(segment))) {
      diagnostics.push({ code: "PARENT_SCOPE_GENERATED_PATH", path: candidate });
    }
  }
  return diagnostics.sort((left, right) => `${left.code}\u0000${left.path}`.localeCompare(`${right.code}\u0000${right.path}`));
}

export function collectChangedPaths(repositoryRoot, base) {
  const baseExists = runGit(repositoryRoot, ["cat-file", "-e", `${base}^{commit}`], true);
  if (baseExists.status !== 0) {
    const error = new Error("base missing");
    error.code = "PARENT_SCOPE_BASE_MISSING";
    throw error;
  }
  const ancestor = runGit(repositoryRoot, ["merge-base", "--is-ancestor", base, "HEAD"], true);
  if (ancestor.status !== 0) {
    const error = new Error("base is not ancestor");
    error.code = "PARENT_SCOPE_BASE_NOT_ANCESTOR";
    throw error;
  }
  const commands = [
    ["diff", "--name-only", "-z", `${base}...HEAD`],
    ["diff", "--cached", "--name-only", "-z"],
    ["diff", "--name-only", "-z"],
    ["ls-files", "--others", "--exclude-standard", "-z"],
  ];
  const paths = commands.flatMap((args) => nulPaths(runGit(repositoryRoot, args).stdout));
  return [...new Set(paths)].sort();
}

function main() {
  try {
    const base = parseBaseArgument(process.argv.slice(2));
    const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const repositoryRoot = path.resolve(moduleRoot, "../..");
    const policy = JSON.parse(readFileSync(path.join(moduleRoot, "module-boundary.json"), "utf8"));
    if (JSON.stringify(policy.repositoryChangePolicy?.allowedPrefixes) !== JSON.stringify(ALLOWED_CHANGE_PREFIXES)) {
      const error = new Error("policy drift");
      error.code = "PARENT_SCOPE_POLICY_DRIFT";
      throw error;
    }
    const paths = collectChangedPaths(repositoryRoot, base);
    const diagnostics = validateChangedPaths(paths);
    if (diagnostics.length > 0) {
      console.error(`RPG01_PARENT_SCOPE_FAILED count=${diagnostics.length}`);
      for (const item of diagnostics) console.error(`${item.code} ${item.path}`.trimEnd());
      process.exitCode = 1;
      return;
    }
    console.log(`RPG01_PARENT_SCOPE_OK changed=${paths.length}`);
  } catch (error) {
    console.error(error?.code ?? "PARENT_SCOPE_OPERATIONAL_ERROR");
    process.exitCode = error?.code === "PARENT_SCOPE_ARGUMENT_ERROR" ? 2 : 1;
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) main();

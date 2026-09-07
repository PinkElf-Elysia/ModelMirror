import { createHash } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const FIXED_BASE = "a43cfa389e1785a95f04a006ba26550a5a36965e";
export const REQUIRED_BRANCH = "codex/ai-rpg-rpg02-content";
export const ALLOWED_PREFIXES = Object.freeze(["docs/ai-rpg-experiment/", "experiments/ai-rpg-engine/"]);
const MODULE_PREFIX = "experiments/ai-rpg-engine/";
const GENERATED = new Set(["node_modules", "dist", "coverage", "logs", "test-reports", ".rpg02-work"]);
const SOURCE_EXTENSIONS = new Set([".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"]);
const TEXT_EXTENSIONS = new Set([...SOURCE_EXTENSIONS, ".json", ".md", ".toml", ".yaml", ".yml"]);
const NETWORK_PACKAGES = new Set(["http", "http2", "https", "net", "tls", "dgram", "dns", "undici", "axios", "got", "ky", "superagent", "openai", "@anthropic-ai/sdk", "@google/generative-ai"]);
const SECRET_PATTERNS = [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/u, /\bsk-[A-Za-z0-9_-]{20,}\b/u, /\bgh[pousr]_[A-Za-z0-9]{30,}\b/u, /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/u];
const FROZEN_PATHS = Object.freeze([
  "experiments/ai-rpg-engine/src/index.mjs",
  "experiments/ai-rpg-engine/tests/contracts.test.mjs",
  "experiments/ai-rpg-engine/tests/boundary.test.mjs",
  "experiments/ai-rpg-engine/fixtures/minimal.turn-exchange.json",
  "experiments/ai-rpg-engine/fixtures/bai-yu-ling-yin.player-setup.json",
  "experiments/ai-rpg-engine/fixtures/plugin-manifests.json",
  "experiments/ai-rpg-engine/fixtures/zero-plugin.card-package.json",
  "experiments/ai-rpg-engine/scripts/check-boundary.mjs",
  "experiments/ai-rpg-engine/scripts/check-parent-scope.mjs",
  "experiments/ai-rpg-engine/scripts/verify-rpg01.mjs",
  "experiments/ai-rpg-engine/docs/RPG01_STATUS.json",
  "experiments/ai-rpg-engine/docs/RPG01_ACCEPTANCE.md",
  "experiments/ai-rpg-engine/docs/CONTRACTS.md",
  "experiments/ai-rpg-engine/docs/THIRD_PARTY.json",
  "docs/ai-rpg-experiment/PROBE_LEDGER.json"
]);

const normalize = (value) => value.replaceAll("\\", "/");
const item = (code, relativePath = "") => ({ code, path: normalize(relativePath) });
const sortItems = (values) => [...new Map(values.map((value) => [`${value.code}\0${value.path}`, value])).values()]
  .sort((a, b) => `${a.code}\0${a.path}`.localeCompare(`${b.code}\0${b.path}`));

function inside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`));
}

function runGit(repositoryRoot, args, allowOne = false) {
  const result = spawnSync("git", args, { cwd: repositoryRoot, encoding: "utf8", windowsHide: true, maxBuffer: 32 * 1024 * 1024 });
  if (result.error || (result.status !== 0 && !(allowOne && result.status === 1))) throw Object.assign(new Error("git failed"), { code: "RPG02_GIT_ERROR" });
  return result;
}

function fixedSpawn(command, args, cwd) {
  const result = spawnSync(command, args, { cwd, encoding: "utf8", windowsHide: true, maxBuffer: 32 * 1024 * 1024 });
  return { status: result.status, error: Boolean(result.error), stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
}

export function runFixedRpg02Gates({ moduleRoot, repositoryRoot }) {
  const groups = [
    ["contracts", 28, ["tests/contracts.test.mjs"]],
    ["boundary", 7, ["tests/rpg02-boundary.test.mjs"]],
    ["content", 43, ["tests/content-compile.test.mjs", "tests/content-e2e.test.mjs", "tests/content-extract.test.mjs", "tests/content-golden.test.mjs", "tests/content-player.test.mjs", "tests/content-provenance.test.mjs", "tests/content-source.test.mjs"]],
    ["archive", 18, ["tests/archive-bundle.test.mjs", "tests/archive-zip.test.mjs"]]
  ];
  const diagnostics = [], counts = {};
  const agentVersion = /^npm\/([^\s]+)/u.exec(process.env.npm_config_user_agent ?? "")?.[1] ?? "";
  const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
  const npm = agentVersion ? { status: 0, error: false, stdout: agentVersion } : fixedSpawn(npmCommand, ["--version"], moduleRoot);
  if (npm.error || npm.status !== 0 || npm.stdout.trim() !== "11.16.0") return { ok: false, diagnostics: [item("RPG02_NPM_VERSION")], counts, npm: npm.stdout.trim() };
  for (const [name, expected, files] of groups) {
    const result = fixedSpawn(process.execPath, ["--test", "--test-reporter=spec", ...files], moduleRoot);
    const match = result.stdout.match(/ℹ tests (\d+)/u), passed = result.stdout.match(/ℹ pass (\d+)/u), failed = result.stdout.match(/ℹ fail (\d+)/u), skipped = result.stdout.match(/ℹ skipped (\d+)/u), cancelled = result.stdout.match(/ℹ cancelled (\d+)/u), todo = result.stdout.match(/ℹ todo (\d+)/u);
    const count = match ? Number(match[1]) : null; counts[name] = count;
    if (result.error || result.status !== 0 || count !== expected || Number(passed?.[1] ?? -1) !== expected || Number(failed?.[1] ?? -1) !== 0 || Number(skipped?.[1] ?? -1) !== 0 || Number(cancelled?.[1] ?? -1) !== 0 || Number(todo?.[1] ?? -1) !== 0) return { ok: false, diagnostics: [item("RPG02_FIXED_SUITE_FAILED", name)], counts, npm: npm.stdout.trim() };
  }
  const diff = fixedSpawn("git", ["diff", "--check"], repositoryRoot);
  if (diff.error || diff.status !== 0) diagnostics.push(item("RPG02_DIFF_CHECK_FAILED"));
  return { ok: diagnostics.length === 0, diagnostics: sortItems(diagnostics), counts, npm: npm.stdout.trim() };
}

export function readFixedRpg02Head(repositoryRoot) {
  const result = runGit(repositoryRoot, ["rev-parse", "HEAD"]);
  return result.stdout.trim();
}

function nulPaths(output) {
  return output.split("\0").filter(Boolean).map(normalize);
}

export function validateChangedPaths(paths) {
  const diagnostics = [];
  for (const raw of [...new Set(paths)].sort()) {
    const candidate = normalize(raw);
    const segments = candidate.split("/");
    if (candidate.startsWith("/") || /^[A-Za-z]:\//u.test(candidate) || segments.includes("..")) diagnostics.push(item("RPG02_UNSAFE_CHANGED_PATH"));
    else if (!ALLOWED_PREFIXES.some((prefix) => candidate.startsWith(prefix))) diagnostics.push(item("RPG02_CHANGE_OUTSIDE_ALLOWLIST", candidate));
    else if (segments.some((segment) => GENERATED.has(segment))) diagnostics.push(item("RPG02_GENERATED_PATH_CHANGED", candidate));
  }
  return sortItems(diagnostics);
}

export function validateLinkTarget(moduleRoot, linkPath, resolvedPath = null) {
  if (resolvedPath === null) return [item("RPG02_BROKEN_SYMLINK", normalize(path.relative(moduleRoot, linkPath)))];
  return inside(moduleRoot, resolvedPath) ? [] : [item("RPG02_EXTERNAL_SYMLINK", normalize(path.relative(moduleRoot, linkPath)))];
}

// Replaces strings, templates and comments while retaining line positions. This prevents
// policy words embedded in RPG schemas or fixtures from being treated as executable code.
export function maskNonCode(source) {
  let output = "", state = "code", quote = "", escaped = false;
  for (let i = 0; i < source.length; i += 1) {
    const c = source[i], n = source[i + 1];
    if (state === "line") { if (c === "\n") { state = "code"; output += c; } else output += " "; continue; }
    if (state === "block") { if (c === "*" && n === "/") { output += "  "; i += 1; state = "code"; } else output += c === "\n" ? "\n" : " "; continue; }
    if (state === "string") { output += c === "\n" ? "\n" : " "; if (escaped) escaped = false; else if (c === "\\") escaped = true; else if (c === quote) state = "code"; continue; }
    if (c === "/" && n === "/") { output += "  "; i += 1; state = "line"; continue; }
    if (c === "/" && n === "*") { output += "  "; i += 1; state = "block"; continue; }
    if (c === "'" || c === '"' || c === "`") { output += " "; state = "string"; quote = c; escaped = false; continue; }
    output += c;
  }
  return output;
}

function importSpecifiers(source) {
  const masked = maskNonCode(source);
  const found = [];
  const pattern = /\b(?:import|export)\s+(?:[^;\n]*?\s+from\s*)?(["'])([^"']+)\1|\brequire\s*\(\s*(["'])([^"']+)\3\s*\)|\bimport\s*\(\s*(["'])([^"']+)\5\s*\)/gu;
  // The mask proves candidate keywords are code; the original supplies literal contents.
  for (const match of source.matchAll(pattern)) {
    if (/\b(?:import|export|require)\b/u.test(masked.slice(match.index, match.index + 12))) found.push(match[2] ?? match[4] ?? match[6]);
  }
  return found;
}

export function analyzeSourceText(relativePath, source, policy) {
  const diagnostics = [];
  const moduleRelative = normalize(relativePath).replace(new RegExp(`^${MODULE_PREFIX}`), "");
  const masked = maskNonCode(source);
  const isContent = (policy.sourceLayers?.contentPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix));
  const isTooling = (policy.sourceLayers?.toolingPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix));
  for (const specifier of importSpecifiers(source)) {
    if (/^(?:[A-Za-z]:[\\/]|\\\\|\/)/u.test(specifier)) diagnostics.push(item("RPG02_ABSOLUTE_IMPORT", relativePath));
    else if (specifier.startsWith(".")) {
      const resolved = path.resolve(path.dirname(path.join("C:/module", moduleRelative)), specifier);
      if (!inside("C:/module", resolved)) diagnostics.push(item("RPG02_PARENT_IMPORT", relativePath));
      else if (isContent) {
        const target = normalize(path.relative("C:/module", resolved));
        if (!target.startsWith("content/") && target !== "src/index.mjs") diagnostics.push(item("RPG02_CONTENT_LAYER_IMPORT", relativePath));
      }
    } else {
      const bare = specifier.startsWith("node:") ? specifier.slice(5) : specifier;
      if (NETWORK_PACKAGES.has(bare)) diagnostics.push(item("RPG02_NETWORK_IMPORT", relativePath));
      if (isContent && !(policy.sourceLayers?.contentAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG02_CONTENT_DEPENDENCY", relativePath));
      if (isTooling && specifier.startsWith("node:") && !(policy.sourceLayers?.toolingAllowedBuiltins ?? []).includes(bare)) diagnostics.push(item("RPG02_TOOLING_BUILTIN", relativePath));
      if (isTooling && !specifier.startsWith("node:") && !(policy.sourceLayers?.toolingAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG02_TOOLING_DEPENDENCY", relativePath));
      if (isTooling && bare === "child_process" && !(policy.sourceLayers?.subprocessEntrypoints ?? []).includes(moduleRelative) && !(policy.sourceLayers?.frozenLegacySubprocessFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG02_SUBPROCESS_IMPORT_OUTSIDE_GATE", relativePath));
    }
  }
  if (/\b(?:import|require)\s*\(\s*[^\s"']/u.test(masked)) diagnostics.push(item("RPG02_DYNAMIC_LOAD", relativePath));
  if (/\b(?:eval|Function)\s*\(|\bvm\s*\./u.test(masked)) diagnostics.push(item("RPG02_SOURCE_EXECUTION", relativePath));
  if (isContent && /`(?:\\.|[^`])*\$\{/su.test(source)) diagnostics.push(item("RPG02_TEMPLATE_INTERPOLATION", relativePath));
  if (/\b(?:fetch|WebSocket|XMLHttpRequest|EventSource)\s*\(/u.test(masked)) diagnostics.push(item("RPG02_NETWORK_GLOBAL", relativePath));
  if (isContent && /\bprocess\s*\.\s*env\b/u.test(masked)) diagnostics.push(item("RPG02_CONTENT_ENV", relativePath));
  if (/\b(?:spawn|spawnSync|exec|execFile|fork)\s*\(/u.test(masked) && !(policy.sourceLayers?.subprocessEntrypoints ?? []).includes(moduleRelative) && !(policy.sourceLayers?.frozenLegacySubprocessFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG02_SUBPROCESS_OUTSIDE_GATE", relativePath));
  return sortItems(diagnostics);
}

function policyDiagnostics(policy, bootstrap) {
  const diagnostics = [];
  if (policy.schemaVersion !== 1 || policy.moduleId !== "ai-rpg-engine") diagnostics.push(item("RPG02_POLICY_ID"));
  if (policy.activeRound !== "RPG-02" || policy.activeRoundBaselineSha !== FIXED_BASE || policy.requiredBranch !== REQUIRED_BRANCH) diagnostics.push(item("RPG02_POLICY_ROUND"));
  if (JSON.stringify(policy.repositoryChangePolicy?.allowedPrefixes) !== JSON.stringify(ALLOWED_PREFIXES)) diagnostics.push(item("RPG02_POLICY_ALLOWLIST"));
  if (policy.parentIntegration !== "none" || JSON.stringify(policy.allowedParentInteractions) !== "[]") diagnostics.push(item("RPG02_POLICY_PARENT"));
  const expected = bootstrap ? { ajv: "8.20.0" } : { ajv: "8.20.0", parse5: "8.0.1", acorn: "8.18.0", yauzl: "3.4.0", fflate: "0.8.3" };
  if (!bootstrap && JSON.stringify(policy.dependencyPolicy?.allowedProductionDependencies) !== JSON.stringify(expected)) diagnostics.push(item("RPG02_POLICY_DEPENDENCIES"));
  if (JSON.stringify(policy.dependencyPolicy?.forbiddenProtocols) !== JSON.stringify(["file:", "link:"])) diagnostics.push(item("RPG02_POLICY_PROTOCOLS"));
  return diagnostics;
}

async function walk(root, moduleRoot, repositoryRoot, policy, diagnostics) {
  for (const entry of (await fsp.readdir(root, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const absolute = path.join(root, entry.name);
    const relative = normalize(path.relative(repositoryRoot, absolute));
    const stat = await fsp.lstat(absolute);
    if (stat.isSymbolicLink()) {
      let resolved = null; try { resolved = await fsp.realpath(absolute); } catch {}
      diagnostics.push(...validateLinkTarget(moduleRoot, absolute, resolved)); continue;
    }
    if (stat.isDirectory()) { if (!GENERATED.has(entry.name)) await walk(absolute, moduleRoot, repositoryRoot, policy, diagnostics); continue; }
    const extension = path.extname(entry.name.toLowerCase());
    if (!TEXT_EXTENSIONS.has(extension)) continue;
    const source = await fsp.readFile(absolute, "utf8");
    if (SECRET_PATTERNS.some((pattern) => pattern.test(source))) diagnostics.push(item("RPG02_SECRET_CONTENT", relative));
    if (SOURCE_EXTENSIONS.has(extension)) diagnostics.push(...analyzeSourceText(relative, source, policy));
  }
}

function sha256(filePath) { return createHash("sha256").update(fs.readFileSync(filePath)).digest("hex").toUpperCase(); }
function fixedBlobSha256(repositoryRoot, relative) {
  const result = spawnSync("git", ["show", FIXED_BASE + ":" + relative], { cwd: repositoryRoot, windowsHide: true, encoding: null, maxBuffer: 32 * 1024 * 1024 });
  if (result.error || result.status !== 0) return null;
  return createHash("sha256").update(result.stdout).digest("hex").toUpperCase();
}

export async function auditRpg02({ moduleRoot, repositoryRoot, policy, baseline, bootstrap = false }) {
  const diagnostics = policyDiagnostics(policy, bootstrap);
  const branch = runGit(repositoryRoot, ["branch", "--show-current"]).stdout.trim();
  const baseExists = runGit(repositoryRoot, ["cat-file", "-e", `${FIXED_BASE}^{commit}`], true);
  const ancestor = baseExists.status === 0 ? runGit(repositoryRoot, ["merge-base", "--is-ancestor", FIXED_BASE, "HEAD"], true) : baseExists;
  if (baseExists.status !== 0 || ancestor.status !== 0) diagnostics.push(item("RPG02_BASE_NOT_ANCESTOR"));
  if (branch !== REQUIRED_BRANCH) diagnostics.push(item("RPG02_BRANCH_DRIFT"));
  const commands = [["diff", "--name-only", "-z", FIXED_BASE], ["diff", "--cached", "--name-only", "-z"], ["diff", "--name-only", "-z"], ["ls-files", "--others", "--exclude-standard", "-z"]];
  diagnostics.push(...validateChangedPaths(commands.flatMap((args) => nulPaths(runGit(repositoryRoot, args).stdout))));
  for (const relative of FROZEN_PATHS) {
    const expected = baseline.fileHashes?.[relative], blob = fixedBlobSha256(repositoryRoot, relative);
    if (!expected || blob !== expected || sha256(path.join(repositoryRoot, relative)) !== expected) diagnostics.push(item("RPG02_FROZEN_HASH_DRIFT", relative));
  }
  const tracked = nulPaths(runGit(repositoryRoot, ["ls-files", "-z"]).stdout);
  for (const relative of tracked) {
    if (!ALLOWED_PREFIXES.some((prefix) => relative.startsWith(prefix))) continue;
    const segments = relative.split("/");
    if (segments.some((segment) => GENERATED.has(segment))) diagnostics.push(item("RPG02_TRACKED_GENERATED", relative));
    const name = segments.at(-1).toLowerCase();
    if (policy.forbiddenTrackedFileNames.includes(name) || policy.forbiddenTrackedExtensions.includes(path.extname(name))) diagnostics.push(item("RPG02_TRACKED_SECRET_OR_BINARY", relative));
  }
  const packageJson = JSON.parse(await fsp.readFile(path.join(moduleRoot, "package.json"), "utf8"));
  const expectedPackage = bootstrap ? { ajv: "8.20.0" } : policy.dependencyPolicy.allowedProductionDependencies;
  if (JSON.stringify(packageJson.dependencies ?? {}) !== JSON.stringify(expectedPackage)) diagnostics.push(item("RPG02_PACKAGE_DEPENDENCIES", `${MODULE_PREFIX}package.json`));
  if (JSON.stringify(packageJson).includes('"file:') || JSON.stringify(packageJson).includes('"link:')) diagnostics.push(item("RPG02_LOCAL_DEPENDENCY_PROTOCOL", `${MODULE_PREFIX}package.json`));
  await walk(moduleRoot, moduleRoot, repositoryRoot, policy, diagnostics);
  return { ok: diagnostics.length === 0, diagnostics: sortItems(diagnostics) };
}

async function main() {
  const args = process.argv.slice(2);
  const bootstrap = args.length === 1 && args[0] === "--bootstrap";
  if (!bootstrap && args.length !== 0) { console.error("RPG02_ARGUMENT_ERROR"); process.exitCode = 2; return; }
  const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const repositoryRoot = path.resolve(moduleRoot, "../..");
  try {
    const policy = JSON.parse(await fsp.readFile(path.join(moduleRoot, "module-boundary.json"), "utf8"));
    const baseline = JSON.parse(await fsp.readFile(path.join(moduleRoot, "docs", "RPG02_BASELINE.json"), "utf8"));
    const report = await auditRpg02({ moduleRoot, repositoryRoot, policy, baseline, bootstrap });
    if (!report.ok) { console.error(`RPG02_BOUNDARY_FAILED count=${report.diagnostics.length}`); for (const value of report.diagnostics) console.error(`${value.code} ${value.path}`.trimEnd()); process.exitCode = 1; return; }
    if (!bootstrap) {
      const contracts = spawnSync(process.execPath, ["--test", "tests/contracts.test.mjs"], { cwd: moduleRoot, encoding: "utf8", windowsHide: true });
      if (contracts.error || contracts.status !== 0 || !/tests 28\b/u.test(contracts.stdout)) { console.error("RPG02_FROZEN_CONTRACT_GATE_FAILED"); process.exitCode = 1; return; }
    }
    console.log(`RPG02_BOUNDARY_OK mode=${bootstrap ? "bootstrap" : "complete"} contracts=${bootstrap ? "frozen_by_hash" : "28"}`);
  } catch (error) { console.error(error?.code ?? "RPG02_BOUNDARY_OPERATIONAL_ERROR"); process.exitCode = 2; }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();

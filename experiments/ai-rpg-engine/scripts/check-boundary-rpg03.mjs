import { createHash } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const FIXED_BASE = "80221379cec850a2b25f5eeeb410233062f3e1ea";
export const REQUIRED_BRANCH = "codex/ai-rpg-rpg03-runtime";
export const ALLOWED_PREFIXES = Object.freeze(["docs/ai-rpg-experiment/", "experiments/ai-rpg-engine/"]);
export const ALLOWED_EXACT_PATHS = Object.freeze(["docs/MODEL_PROVIDER_CONTROL_PLANE.md", "server/main.py", "server/tests/test_provider_chat_stable_chat.py"]);
const MODULE_PREFIX = "experiments/ai-rpg-engine/";
const GENERATED = new Set(["node_modules", "dist", "coverage", "logs", "test-reports", ".rpg02-work", ".rpg03-work"]);
const SOURCE_EXTENSIONS = new Set([".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"]);
const TEXT_EXTENSIONS = new Set([...SOURCE_EXTENSIONS, ".json", ".md", ".py", ".toml", ".yaml", ".yml"]);
const SECRET_PATTERNS = [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/u, /\bsk-[A-Za-z0-9_-]{20,}\b/u, /\bgh[pousr]_[A-Za-z0-9]{30,}\b/u, /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/u];
const NETWORK_BUILTINS = new Set(["http", "http2", "https", "net", "tls", "dgram", "dns"]);

const normalize = (value) => value.replaceAll("\\", "/");
const item = (code, relativePath = "") => ({ code, path: normalize(relativePath) });
const sortItems = (values) => [...new Map(values.map((value) => [`${value.code}\0${value.path}`, value])).values()]
  .sort((a, b) => `${a.code}\0${a.path}`.localeCompare(`${b.code}\0${b.path}`));
function inside(root, target) { const relative = path.relative(root, target); return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`)); }
function runGit(root, args, allowOne = false) { const result = spawnSync("git", args, { cwd: root, encoding: "utf8", windowsHide: true, maxBuffer: 32 * 1024 * 1024 }); if (result.error || (result.status !== 0 && !(allowOne && result.status === 1))) throw Object.assign(new Error("git failed"), { code: "RPG03_GIT_ERROR" }); return result; }
function nulPaths(output) { return output.split("\0").filter(Boolean).map(normalize); }
function allowedPath(candidate) { return ALLOWED_PREFIXES.some((prefix) => candidate.startsWith(prefix)) || ALLOWED_EXACT_PATHS.includes(candidate); }

export function validateChangedPaths(paths) {
  const diagnostics = [];
  for (const candidate of [...new Set(paths.map(normalize))].sort()) {
    const segments = candidate.split("/");
    if (candidate.startsWith("/") || /^[A-Za-z]:\//u.test(candidate) || segments.includes("..")) diagnostics.push(item("RPG03_UNSAFE_CHANGED_PATH"));
    else if (!allowedPath(candidate)) diagnostics.push(item("RPG03_CHANGE_OUTSIDE_ALLOWLIST", candidate));
    else if (segments.some((segment) => GENERATED.has(segment))) diagnostics.push(item("RPG03_GENERATED_PATH_CHANGED", candidate));
  }
  return sortItems(diagnostics);
}

export function validateLinkTarget(moduleRoot, linkPath, resolvedPath = null) {
  if (resolvedPath === null) return [item("RPG03_BROKEN_SYMLINK", normalize(path.relative(moduleRoot, linkPath)))];
  return inside(moduleRoot, resolvedPath) ? [] : [item("RPG03_EXTERNAL_SYMLINK", normalize(path.relative(moduleRoot, linkPath)))];
}

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

function lexicalTokens(source) {
  const tokens = [];
  for (let i = 0; i < source.length;) {
    const c = source[i], n = source[i + 1];
    if (/\s/u.test(c)) { i += 1; continue; }
    if (c === "/" && n === "/") { i += 2; while (i < source.length && source[i] !== "\n") i += 1; continue; }
    if (c === "/" && n === "*") { i += 2; while (i < source.length && !(source[i] === "*" && source[i + 1] === "/")) i += 1; i += 2; continue; }
    if (c === "'" || c === '"' || c === "`") {
      const quote = c; let value = "", escaped = false, interpolation = false; i += 1;
      while (i < source.length) {
        const x = source[i];
        if (escaped) { value += x; escaped = false; i += 1; continue; }
        if (x === "\\") { escaped = true; i += 1; continue; }
        if (quote === "`" && x === "$" && source[i + 1] === "{") interpolation = true;
        if (x === quote) { i += 1; break; }
        value += x; i += 1;
      }
      tokens.push({ kind: quote === "`" ? "template" : "string", value, interpolation }); continue;
    }
    if (/[A-Za-z_$]/u.test(c)) { const start = i; i += 1; while (/[A-Za-z0-9_$]/u.test(source[i] ?? "")) i += 1; tokens.push({ kind: "word", value: source.slice(start, i) }); continue; }
    tokens.push({ kind: "punct", value: c }); i += 1;
  }
  return tokens;
}

function staticSpecifiers(tokens) {
  const found = [];
  for (let i = 0; i < tokens.length; i += 1) {
    if (tokens[i].kind !== "word" || !["import", "export"].includes(tokens[i].value)) continue;
    if (tokens[i].value === "import" && tokens[i + 1]?.value === "(") continue;
    if (tokens[i].value === "import" && tokens[i + 1]?.kind === "string") { found.push(tokens[i + 1].value); continue; }
    for (let j = i + 1; j < tokens.length && tokens[j].value !== ";"; j += 1) {
      if (tokens[j].kind === "word" && tokens[j].value === "from" && tokens[j + 1]?.kind === "string") { found.push(tokens[j + 1].value); break; }
      if (j > i + 1 && tokens[j].kind === "word" && ["import", "export"].includes(tokens[j].value)) break;
    }
  }
  return found;
}

function layerFor(moduleRelative, policy) {
  const node = (policy.sourceLayers.runtimeNodePrefixes ?? []).some((prefix) => moduleRelative === prefix || moduleRelative.startsWith(prefix));
  const runtime = (policy.sourceLayers.runtimeCorePrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix));
  return { content: (policy.sourceLayers.contentPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix)), node, runtime: runtime && !node, tooling: (policy.sourceLayers.toolingPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix)) };
}

export function analyzeSourceText(relativePath, source, policy) {
  const diagnostics = [], moduleRelative = normalize(relativePath).replace(new RegExp(`^${MODULE_PREFIX}`), ""), layer = layerFor(moduleRelative, policy);
  const masked = maskNonCode(source), tokens = lexicalTokens(source), specifiers = staticSpecifiers(tokens);
  if (/\bimport\s*\(/u.test(masked)) diagnostics.push(item("RPG03_DYNAMIC_LOAD", relativePath));
  if (/\b(?:eval|Function)\s*\(|\bnew\s+Function\s*\(|\bvm\s*\./u.test(masked)) diagnostics.push(item("RPG03_SOURCE_EXECUTION", relativePath));
  if (/\b(?:spawn|spawnSync|exec|execFile|fork)\s*\(/u.test(masked) && !(policy.sourceLayers.subprocessEntrypoints ?? []).includes(moduleRelative) && !(policy.sourceLayers.frozenLegacySubprocessFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG03_SUBPROCESS_OUTSIDE_GATE", relativePath));
  if (/\b(?:fetch|WebSocket|XMLHttpRequest|EventSource)\s*\(/u.test(masked) && !layer.node && !(policy.sourceLayers.testLoopbackNetworkFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG03_NETWORK_GLOBAL", relativePath));
  if (/\bprocess\s*\.\s*env\b/u.test(masked) && (layer.runtime || layer.content)) diagnostics.push(item("RPG03_PURE_LAYER_ENV", relativePath));
  if (layer.runtime && tokens.some((token) => token.kind === "template" && token.interpolation)) diagnostics.push(item("RPG03_RUNTIME_TEMPLATE_INTERPOLATION", relativePath));
  for (const specifier of specifiers) {
    if (/^(?:[A-Za-z]:[\\/]|\\\\|\/)/u.test(specifier)) diagnostics.push(item("RPG03_ABSOLUTE_IMPORT", relativePath));
    else if (specifier.startsWith(".")) {
      const resolved = path.resolve(path.dirname(path.join("C:/module", moduleRelative)), specifier);
      if (!inside("C:/module", resolved)) diagnostics.push(item("RPG03_PARENT_IMPORT", relativePath));
      else if (layer.content) { const target = normalize(path.relative("C:/module", resolved)); if (!target.startsWith("content/") && target !== "src/index.mjs") diagnostics.push(item("RPG03_CONTENT_LAYER_IMPORT", relativePath)); }
      else if (layer.runtime) { const target = normalize(path.relative("C:/module", resolved)); if (target === "runtime/node.mjs" || target.startsWith("runtime/node/") || target.startsWith("tooling/") || target.startsWith("scripts/") || target.startsWith("tests/")) diagnostics.push(item("RPG03_RUNTIME_LAYER_IMPORT", relativePath)); }
    } else {
      const builtin = specifier.startsWith("node:") ? specifier.slice(5) : null;
      if (layer.runtime && !(policy.sourceLayers.runtimeCoreAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG03_RUNTIME_CORE_DEPENDENCY", relativePath));
      if (layer.content && !(policy.sourceLayers.contentAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG03_CONTENT_DEPENDENCY", relativePath));
      if (layer.node && builtin && !(policy.sourceLayers.runtimeNodeAllowedBuiltins ?? []).includes(builtin)) diagnostics.push(item("RPG03_RUNTIME_NODE_BUILTIN", relativePath));
      if (layer.node && !builtin && !(policy.sourceLayers.runtimeNodeAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG03_RUNTIME_NODE_DEPENDENCY", relativePath));
      if ((layer.runtime || layer.content) && builtin && NETWORK_BUILTINS.has(builtin)) diagnostics.push(item("RPG03_PURE_LAYER_NETWORK", relativePath));
      const exactLoopbackTest = (policy.sourceLayers.testLoopbackNetworkFiles ?? []).includes(moduleRelative);
      if (layer.tooling && builtin && !(policy.sourceLayers.toolingAllowedBuiltins ?? []).includes(builtin) && !(exactLoopbackTest && NETWORK_BUILTINS.has(builtin))) diagnostics.push(item("RPG03_TOOLING_BUILTIN", relativePath));
      if (layer.tooling && !builtin && !(policy.sourceLayers.toolingAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG03_TOOLING_DEPENDENCY", relativePath));
      if (layer.tooling && builtin === "child_process" && !(policy.sourceLayers.subprocessEntrypoints ?? []).includes(moduleRelative) && !(policy.sourceLayers.frozenLegacySubprocessFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG03_SUBPROCESS_IMPORT_OUTSIDE_GATE", relativePath));
    }
  }
  return sortItems(diagnostics);
}

function policyDiagnostics(policy) {
  const diagnostics = [];
  if (policy.schemaVersion !== 1 || policy.moduleId !== "ai-rpg-engine" || policy.activeRound !== "RPG-03" || policy.activeRoundBaselineSha !== FIXED_BASE || policy.requiredBranch !== REQUIRED_BRANCH) diagnostics.push(item("RPG03_POLICY_ID_OR_ROUND"));
  if (JSON.stringify(policy.repositoryChangePolicy?.allowedPrefixes) !== JSON.stringify(ALLOWED_PREFIXES) || JSON.stringify(policy.repositoryChangePolicy?.allowedExactPaths) !== JSON.stringify(ALLOWED_EXACT_PATHS)) diagnostics.push(item("RPG03_POLICY_ALLOWLIST"));
  if (policy.parentIntegration !== "controlled-modelmirror-chat" || JSON.stringify(policy.allowedParentInteractions) !== JSON.stringify(["managed-chat-http"])) diagnostics.push(item("RPG03_POLICY_PARENT"));
  if (JSON.stringify(policy.dependencyPolicy?.forbiddenProtocols) !== JSON.stringify(["file:", "link:"])) diagnostics.push(item("RPG03_POLICY_PROTOCOLS"));
  return diagnostics;
}
function sha256Bytes(value) { return createHash("sha256").update(value).digest("hex").toUpperCase(); }
function fixedBlob(repositoryRoot, relative) { const result = spawnSync("git", ["show", `${FIXED_BASE}:${relative}`], { cwd: repositoryRoot, windowsHide: true, encoding: null, maxBuffer: 32 * 1024 * 1024 }); return result.error || result.status !== 0 ? null : result.stdout; }

async function scanTree(root, moduleRoot, repositoryRoot, policy, diagnostics) {
  for (const entry of (await fsp.readdir(root, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (GENERATED.has(entry.name)) continue;
    const absolute = path.join(root, entry.name), relative = normalize(path.relative(repositoryRoot, absolute)), stat = await fsp.lstat(absolute);
    if (stat.isSymbolicLink()) { let resolved = null; try { resolved = await fsp.realpath(absolute); } catch {} diagnostics.push(...validateLinkTarget(moduleRoot, absolute, resolved)); continue; }
    if (stat.isDirectory()) { await scanTree(absolute, moduleRoot, repositoryRoot, policy, diagnostics); continue; }
    const extension = path.extname(entry.name.toLowerCase()); if (!TEXT_EXTENSIONS.has(extension)) continue;
    const source = await fsp.readFile(absolute, "utf8");
    if (SECRET_PATTERNS.some((pattern) => pattern.test(source))) diagnostics.push(item("RPG03_SECRET_CONTENT", relative));
    if (SOURCE_EXTENSIONS.has(extension)) diagnostics.push(...analyzeSourceText(relative, source, policy));
  }
}

export async function auditRpg03({ moduleRoot, repositoryRoot, policy, baseline, bootstrap = false }) {
  const diagnostics = policyDiagnostics(policy), branch = runGit(repositoryRoot, ["branch", "--show-current"]).stdout.trim();
  const base = runGit(repositoryRoot, ["cat-file", "-e", `${FIXED_BASE}^{commit}`], true), ancestor = base.status === 0 ? runGit(repositoryRoot, ["merge-base", "--is-ancestor", FIXED_BASE, "HEAD"], true) : base;
  if (base.status !== 0 || ancestor.status !== 0) diagnostics.push(item("RPG03_BASE_NOT_ANCESTOR"));
  if (branch !== REQUIRED_BRANCH) diagnostics.push(item("RPG03_BRANCH_DRIFT"));
  const changes = [["diff", "--name-only", "-z", FIXED_BASE], ["diff", "--cached", "--name-only", "-z"], ["diff", "--name-only", "-z"], ["ls-files", "--others", "--exclude-standard", "-z"]];
  diagnostics.push(...validateChangedPaths(changes.flatMap((args) => nulPaths(runGit(repositoryRoot, args).stdout))));
  const frozen = baseline.frozenFiles ?? {}, mutable = new Set(baseline.mutableHistoricalFiles ?? []);
  const baselineModuleFiles = runGit(repositoryRoot, ["ls-tree", "-r", "--name-only", FIXED_BASE, "--", MODULE_PREFIX]).stdout.split(/\r?\n/u).filter(Boolean).map(normalize);
  const expectedFrozen = [...baselineModuleFiles.filter((relative) => !mutable.has(relative)), "docs/ai-rpg-experiment/PROBE_LEDGER.json"].sort();
  const recordedFrozen = Object.keys(frozen).sort();
  if (recordedFrozen.length !== 67 || JSON.stringify(recordedFrozen) !== JSON.stringify(expectedFrozen)) diagnostics.push(item("RPG03_FROZEN_SET"));
  for (const [relative, expected] of Object.entries(frozen)) {
    const blob = fixedBlob(repositoryRoot, relative), workspace = await fsp.readFile(path.join(repositoryRoot, relative)).catch(() => null);
    if (!blob || !workspace || sha256Bytes(blob) !== expected || sha256Bytes(workspace) !== expected) diagnostics.push(item("RPG03_FROZEN_HASH_DRIFT", relative));
  }
  for (const [relative, expected] of Object.entries(baseline.parentBaselineHashes ?? {})) {
    const blob = fixedBlob(repositoryRoot, relative);
    if (!blob || sha256Bytes(blob) !== expected) diagnostics.push(item("RPG03_PARENT_BASELINE_HASH_DRIFT", relative));
  }
  const packageJson = JSON.parse(await fsp.readFile(path.join(moduleRoot, "package.json"), "utf8"));
  if (packageJson.version !== (bootstrap ? "0.2.0" : "0.3.0")) diagnostics.push(item("RPG03_PACKAGE_VERSION"));
  if (JSON.stringify(packageJson.dependencies ?? {}) !== JSON.stringify(policy.dependencyPolicy.allowedProductionDependencies)) diagnostics.push(item("RPG03_PACKAGE_DEPENDENCIES"));
  if (JSON.stringify(packageJson).includes('"file:') || JSON.stringify(packageJson).includes('"link:')) diagnostics.push(item("RPG03_LOCAL_DEPENDENCY_PROTOCOL"));
  const tracked = nulPaths(runGit(repositoryRoot, ["ls-files", "-z"]).stdout);
  for (const relative of tracked.filter(allowedPath)) {
    const segments = relative.split("/"), name = segments.at(-1).toLowerCase();
    if (segments.some((segment) => GENERATED.has(segment))) diagnostics.push(item("RPG03_TRACKED_GENERATED", relative));
    if (policy.forbiddenTrackedFileNames.includes(name) || policy.forbiddenTrackedExtensions.includes(path.extname(name))) diagnostics.push(item("RPG03_TRACKED_SECRET_OR_BINARY", relative));
  }
  await scanTree(moduleRoot, moduleRoot, repositoryRoot, policy, diagnostics);
  return { ok: diagnostics.length === 0, diagnostics: sortItems(diagnostics) };
}

async function main() {
  const args = process.argv.slice(2), bootstrap = args.length === 1 && args[0] === "--bootstrap";
  if (!bootstrap && args.length !== 0) { console.error("RPG03_ARGUMENT_ERROR"); process.exitCode = 2; return; }
  const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), repositoryRoot = path.resolve(moduleRoot, "../..");
  try {
    const policy = JSON.parse(await fsp.readFile(path.join(moduleRoot, "module-boundary.json"), "utf8"));
    const baseline = JSON.parse(await fsp.readFile(path.join(moduleRoot, "docs", "RPG03_BASELINE.json"), "utf8"));
    const report = await auditRpg03({ moduleRoot, repositoryRoot, policy, baseline, bootstrap });
    if (!report.ok) { console.error(`RPG03_BOUNDARY_FAILED count=${report.diagnostics.length}`); report.diagnostics.forEach((value) => console.error(`${value.code} ${value.path}`.trimEnd())); process.exitCode = 1; return; }
    console.log(`RPG03_BOUNDARY_OK mode=${bootstrap ? "bootstrap" : "complete"} frozen=67`);
  } catch (error) { console.error(error?.code ?? "RPG03_BOUNDARY_OPERATIONAL_ERROR"); process.exitCode = 2; }
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();

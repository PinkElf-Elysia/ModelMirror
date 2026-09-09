import { createHash } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

export const FIXED_BASE = "1b280ed257a45672c4a3dc03745fcb585685faf9";
export const REQUIRED_BRANCH = "codex/ai-rpg-rpg04-context";
export const ALLOWED_PREFIXES = Object.freeze(["docs/ai-rpg-experiment/", "experiments/ai-rpg-engine/"]);
export const ALLOWED_EXACT_PATHS = Object.freeze([]);
const MODULE_PREFIX = "experiments/ai-rpg-engine/";
const GENERATED = new Set(["node_modules", "dist", "coverage", "logs", "test-reports", ".rpg02-work", ".rpg03-work", ".rpg04-work"]);
const SOURCE_EXTENSIONS = new Set([".cjs", ".js", ".jsx", ".mjs", ".ts", ".tsx"]);
const TEXT_EXTENSIONS = new Set([...SOURCE_EXTENSIONS, ".html", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"]);
const SECRET_PATTERNS = [/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/u, /\bsk-[A-Za-z0-9_-]{20,}\b/u, /\bgh[pousr]_[A-Za-z0-9]{30,}\b/u, /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/u];
const NETWORK_BUILTINS = new Set(["http", "http2", "https", "net", "tls", "dgram", "dns"]);

const normalize = (value) => value.replaceAll("\\", "/");
const item = (code, relativePath = "") => ({ code, path: normalize(relativePath) });
const sortItems = (values) => [...new Map(values.map((value) => [`${value.code}\0${value.path}`, value])).values()]
  .sort((a, b) => `${a.code}\0${a.path}`.localeCompare(`${b.code}\0${b.path}`));
function inside(root, target) { const relative = path.relative(root, target); return relative === "" || (!path.isAbsolute(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`)); }
function runGit(root, args, allowOne = false) { const result = spawnSync("git", args, { cwd: root, encoding: "utf8", windowsHide: true, maxBuffer: 32 * 1024 * 1024 }); if (result.error || (result.status !== 0 && !(allowOne && result.status === 1))) throw Object.assign(new Error("git failed"), { code: "RPG04_GIT_ERROR" }); return result; }
function nulPaths(output) { return output.split("\0").filter(Boolean).map(normalize); }
function allowedPath(candidate) { return ALLOWED_PREFIXES.some((prefix) => candidate.startsWith(prefix)) || ALLOWED_EXACT_PATHS.includes(candidate); }

export function validateChangedPaths(paths) {
  const diagnostics = [];
  for (const candidate of [...new Set(paths.map(normalize))].sort()) {
    const segments = candidate.split("/");
    if (candidate.startsWith("/") || /^[A-Za-z]:\//u.test(candidate) || segments.includes("..")) diagnostics.push(item("RPG04_UNSAFE_CHANGED_PATH"));
    else if (!allowedPath(candidate)) diagnostics.push(item("RPG04_CHANGE_OUTSIDE_ALLOWLIST", candidate));
    else if (segments.some((segment) => GENERATED.has(segment))) diagnostics.push(item("RPG04_GENERATED_PATH_CHANGED", candidate));
  }
  return sortItems(diagnostics);
}

export function validateLinkTarget(moduleRoot, linkPath, resolvedPath = null) {
  if (resolvedPath === null) return [item("RPG04_BROKEN_SYMLINK", normalize(path.relative(moduleRoot, linkPath)))];
  return inside(moduleRoot, resolvedPath) ? [] : [item("RPG04_EXTERNAL_SYMLINK", normalize(path.relative(moduleRoot, linkPath)))];
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
  return { content: (policy.sourceLayers.contentPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix)), context: (policy.sourceLayers.contextPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix)), node, runtime: runtime && !node, tooling: (policy.sourceLayers.toolingPrefixes ?? []).some((prefix) => moduleRelative.startsWith(prefix)) };
}

export function analyzeSourceText(relativePath, source, policy) {
  const diagnostics = [], moduleRelative = normalize(relativePath).replace(new RegExp(`^${MODULE_PREFIX}`), ""), layer = layerFor(moduleRelative, policy);
  const masked = maskNonCode(source), tokens = lexicalTokens(source), specifiers = staticSpecifiers(tokens);
  if (/\bimport\s*\(/u.test(masked)) diagnostics.push(item("RPG04_DYNAMIC_LOAD", relativePath));
  if (/\b(?:eval|Function)\s*\(|\bnew\s+Function\s*\(|\bvm\s*\./u.test(masked)) diagnostics.push(item("RPG04_SOURCE_EXECUTION", relativePath));
  if (/\b(?:spawn|spawnSync|exec|execFile|fork)\s*\(/u.test(masked) && !(policy.sourceLayers.subprocessEntrypoints ?? []).includes(moduleRelative) && !(policy.sourceLayers.frozenLegacySubprocessFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG04_SUBPROCESS_OUTSIDE_GATE", relativePath));
  const pure = layer.runtime || layer.content || layer.context;
  const networkAdapter = (policy.sourceLayers.networkAdapterFiles ?? []).includes(moduleRelative);
  if (/\b(?:fetch|WebSocket|XMLHttpRequest|EventSource)\s*\(/u.test(masked) && !networkAdapter && !(policy.sourceLayers.testLoopbackNetworkFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG04_NETWORK_GLOBAL", relativePath));
  if (/\bprocess\s*\.\s*env\b/u.test(masked) && pure) diagnostics.push(item("RPG04_PURE_LAYER_ENV", relativePath));
  if ((layer.runtime || layer.context) && tokens.some((token) => token.kind === "template" && token.interpolation)) diagnostics.push(item("RPG04_RUNTIME_TEMPLATE_INTERPOLATION", relativePath));
  for (const specifier of specifiers) {
    if (/^(?:[A-Za-z]:[\\/]|\\\\|\/)/u.test(specifier)) diagnostics.push(item("RPG04_ABSOLUTE_IMPORT", relativePath));
    else if (specifier.startsWith(".")) {
      const resolved = path.resolve(path.dirname(path.join("C:/module", moduleRelative)), specifier);
      if (!inside("C:/module", resolved)) diagnostics.push(item("RPG04_PARENT_IMPORT", relativePath));
      else if (layer.content) { const target = normalize(path.relative("C:/module", resolved)); if (!target.startsWith("content/") && target !== "src/index.mjs") diagnostics.push(item("RPG04_CONTENT_LAYER_IMPORT", relativePath)); }
      else if (layer.runtime) { const target = normalize(path.relative("C:/module", resolved)); if (target === "runtime/node.mjs" || target.startsWith("runtime/node/") || target.startsWith("tooling/") || target.startsWith("scripts/") || target.startsWith("tests/")) diagnostics.push(item("RPG04_RUNTIME_LAYER_IMPORT", relativePath)); }
      else if (layer.context) { const target = normalize(path.relative("C:/module", resolved)); if (!target.startsWith("context/") && !(policy.sourceLayers.contextAllowedModuleImports ?? []).includes(target)) diagnostics.push(item("RPG04_CONTEXT_LAYER_IMPORT", relativePath)); }
    } else {
      const builtin = specifier.startsWith("node:") ? specifier.slice(5) : null;
      if (layer.runtime && !(policy.sourceLayers.runtimeCoreAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG04_RUNTIME_CORE_DEPENDENCY", relativePath));
      if (layer.content && !(policy.sourceLayers.contentAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG04_CONTENT_DEPENDENCY", relativePath));
      if (layer.context && !(policy.sourceLayers.contextAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG04_CONTEXT_DEPENDENCY", relativePath));
      if (layer.node && builtin && !(policy.sourceLayers.runtimeNodeAllowedBuiltins ?? []).includes(builtin)) diagnostics.push(item("RPG04_RUNTIME_NODE_BUILTIN", relativePath));
      if (layer.node && !builtin && !(policy.sourceLayers.runtimeNodeAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG04_RUNTIME_NODE_DEPENDENCY", relativePath));
      if (pure && builtin && NETWORK_BUILTINS.has(builtin)) diagnostics.push(item("RPG04_PURE_LAYER_NETWORK", relativePath));
      if (layer.node && builtin && NETWORK_BUILTINS.has(builtin) && !networkAdapter) diagnostics.push(item("RPG04_NETWORK_IMPORT_OUTSIDE_ADAPTER", relativePath));
      const exactLoopbackTest = (policy.sourceLayers.testLoopbackNetworkFiles ?? []).includes(moduleRelative);
      if (layer.tooling && builtin && !(policy.sourceLayers.toolingAllowedBuiltins ?? []).includes(builtin) && !(exactLoopbackTest && NETWORK_BUILTINS.has(builtin))) diagnostics.push(item("RPG04_TOOLING_BUILTIN", relativePath));
      if (layer.tooling && !builtin && !(policy.sourceLayers.toolingAllowedPackages ?? []).includes(specifier)) diagnostics.push(item("RPG04_TOOLING_DEPENDENCY", relativePath));
      if (layer.tooling && builtin === "child_process" && !(policy.sourceLayers.subprocessEntrypoints ?? []).includes(moduleRelative) && !(policy.sourceLayers.frozenLegacySubprocessFiles ?? []).includes(moduleRelative)) diagnostics.push(item("RPG04_SUBPROCESS_IMPORT_OUTSIDE_GATE", relativePath));
    }
  }
  return sortItems(diagnostics);
}

export function analyzeFileText(relativePath, source, policy) {
  const normalized = normalize(relativePath), name = path.basename(normalized).toLowerCase(), extension = path.extname(name);
  const diagnostics = [];
  if (policy.forbiddenTrackedFileNames.includes(name) || policy.forbiddenTrackedExtensions.includes(extension)) diagnostics.push(item("RPG04_SECRET_OR_BINARY_PATH", normalized));
  if (SECRET_PATTERNS.some((pattern) => pattern.test(source))) diagnostics.push(item("RPG04_SECRET_CONTENT", normalized));
  if (SOURCE_EXTENSIONS.has(extension)) diagnostics.push(...analyzeSourceText(normalized, source, policy));
  return sortItems(diagnostics);
}

function policyDiagnostics(policy) {
  const diagnostics = [];
  if (policy.schemaVersion !== 1 || policy.moduleId !== "ai-rpg-engine" || policy.activeRound !== "RPG-04" || policy.activeRoundBaselineSha !== FIXED_BASE || policy.requiredBranch !== REQUIRED_BRANCH) diagnostics.push(item("RPG04_POLICY_ID_OR_ROUND"));
  if (JSON.stringify(policy.repositoryChangePolicy?.allowedPrefixes) !== JSON.stringify(ALLOWED_PREFIXES) || JSON.stringify(policy.repositoryChangePolicy?.allowedExactPaths) !== JSON.stringify(ALLOWED_EXACT_PATHS)) diagnostics.push(item("RPG04_POLICY_ALLOWLIST"));
  if (policy.parentIntegration !== "controlled-modelmirror-chat" || JSON.stringify(policy.allowedParentInteractions) !== JSON.stringify(["managed-chat-http"])) diagnostics.push(item("RPG04_POLICY_PARENT"));
  if (JSON.stringify(policy.dependencyPolicy?.forbiddenProtocols) !== JSON.stringify(["file:", "link:"])) diagnostics.push(item("RPG04_POLICY_PROTOCOLS"));
  return diagnostics;
}
function sha256Bytes(value) { return createHash("sha256").update(value).digest("hex").toUpperCase(); }
function fixedBlob(repositoryRoot, relative) { const result = spawnSync("git", ["show", `${FIXED_BASE}:${relative}`], { cwd: repositoryRoot, windowsHide: true, encoding: null, maxBuffer: 32 * 1024 * 1024 }); return result.error || result.status !== 0 ? null : result.stdout; }
function baselineFileHash(repositoryRoot, relative) { const blob = fixedBlob(repositoryRoot, relative); return blob ? sha256Bytes(blob) : null; }
function indexBlob(repositoryRoot, relative) { const result = spawnSync("git", ["show", `:${relative}`], { cwd: repositoryRoot, windowsHide: true, encoding: null, maxBuffer: 32 * 1024 * 1024 }); return result.error || result.status !== 0 ? null : result.stdout; }
export function validateFrozenBytes(relative, expected, baselineBytes, indexBytes, workspaceBytes) {
  return [baselineBytes, indexBytes, workspaceBytes].every((value) => value && sha256Bytes(value) === expected) ? [] : [item("RPG04_FROZEN_HASH_DRIFT", relative)];
}
const canonicalDependencies = (value) => JSON.stringify(Object.fromEntries(Object.entries(value ?? {}).sort(([a], [b]) => a.localeCompare(b))));
export function validateDependencySet(current, declared, baseline) {
  const expected = canonicalDependencies(baseline);
  return canonicalDependencies(current) === expected && canonicalDependencies(declared) === expected ? [] : [item("RPG04_PACKAGE_DEPENDENCIES", `${MODULE_PREFIX}package.json`)];
}

async function scanTree(root, containmentRoot, repositoryRoot, policy, diagnostics) {
  for (const entry of (await fsp.readdir(root, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (GENERATED.has(entry.name)) continue;
    const absolute = path.join(root, entry.name), relative = normalize(path.relative(repositoryRoot, absolute)), stat = await fsp.lstat(absolute);
    if (stat.isSymbolicLink()) { let resolved = null; try { resolved = await fsp.realpath(absolute); } catch {} diagnostics.push(...validateLinkTarget(containmentRoot, absolute, resolved)); continue; }
    if (stat.isDirectory()) { await scanTree(absolute, containmentRoot, repositoryRoot, policy, diagnostics); continue; }
    const lowerName = entry.name.toLowerCase(), extension = path.extname(lowerName);
    if (policy.forbiddenTrackedFileNames.includes(lowerName) || policy.forbiddenTrackedExtensions.includes(extension)) diagnostics.push(item("RPG04_SECRET_OR_BINARY_PATH", relative));
    if (!TEXT_EXTENSIONS.has(extension)) continue;
    const source = await fsp.readFile(absolute, "utf8");
    diagnostics.push(...analyzeFileText(relative, source, policy));
  }
}

export async function auditRpg04({ moduleRoot, repositoryRoot, policy, baseline, bootstrap = false }) {
  const diagnostics = policyDiagnostics(policy), branch = runGit(repositoryRoot, ["branch", "--show-current"]).stdout.trim();
  const base = runGit(repositoryRoot, ["cat-file", "-e", `${FIXED_BASE}^{commit}`], true), ancestor = base.status === 0 ? runGit(repositoryRoot, ["merge-base", "--is-ancestor", FIXED_BASE, "HEAD"], true) : base;
  if (base.status !== 0 || ancestor.status !== 0) diagnostics.push(item("RPG04_BASE_NOT_ANCESTOR"));
  if (branch !== REQUIRED_BRANCH) diagnostics.push(item("RPG04_BRANCH_DRIFT"));
  const changes = [["diff", "--name-only", "-z", FIXED_BASE], ["diff", "--cached", "--name-only", "-z"], ["diff", "--name-only", "-z"], ["ls-files", "--others", "--exclude-standard", "-z"]];
  diagnostics.push(...validateChangedPaths(changes.flatMap((args) => nulPaths(runGit(repositoryRoot, args).stdout))));
  const mutable = new Set([
    `${MODULE_PREFIX}.gitignore`, `${MODULE_PREFIX}AGENTS.md`, `${MODULE_PREFIX}README.md`,
    `${MODULE_PREFIX}module-boundary.json`, `${MODULE_PREFIX}package.json`, `${MODULE_PREFIX}package-lock.json`,
    `${MODULE_PREFIX}runtime/node/http.mjs`
  ]);
  const baselineModuleFiles = runGit(repositoryRoot, ["ls-tree", "-r", "--name-only", FIXED_BASE, "--", MODULE_PREFIX]).stdout.split(/\r?\n/u).filter(Boolean).map(normalize);
  const frozenPaths = baselineModuleFiles.filter((relative) => !mutable.has(relative));
  frozenPaths.push("docs/ai-rpg-experiment/PROBE_LEDGER.json");
  for (const relative of frozenPaths.sort()) {
    const expected = baselineFileHash(repositoryRoot, relative);
    const blob = fixedBlob(repositoryRoot, relative), staged = indexBlob(repositoryRoot, relative), workspace = await fsp.readFile(path.join(repositoryRoot, relative)).catch(() => null);
    diagnostics.push(...validateFrozenBytes(relative, expected, blob, staged, workspace));
  }
  const packageJson = JSON.parse(await fsp.readFile(path.join(moduleRoot, "package.json"), "utf8"));
  if (packageJson.version !== (bootstrap ? "0.3.0" : "0.4.0")) diagnostics.push(item("RPG04_PACKAGE_VERSION"));
  const baselinePackageBytes = fixedBlob(repositoryRoot, `${MODULE_PREFIX}package.json`);
  const baselineDependencies = baselinePackageBytes ? JSON.parse(baselinePackageBytes.toString("utf8")).dependencies : null;
  diagnostics.push(...validateDependencySet(packageJson.dependencies, policy.dependencyPolicy.allowedProductionDependencies, baselineDependencies));
  if (JSON.stringify(packageJson).includes('"file:') || JSON.stringify(packageJson).includes('"link:')) diagnostics.push(item("RPG04_LOCAL_DEPENDENCY_PROTOCOL"));
  const tracked = nulPaths(runGit(repositoryRoot, ["ls-files", "-z"]).stdout);
  for (const relative of tracked.filter(allowedPath)) {
    const segments = relative.split("/"), name = segments.at(-1).toLowerCase();
    if (segments.some((segment) => GENERATED.has(segment))) diagnostics.push(item("RPG04_TRACKED_GENERATED", relative));
    if (policy.forbiddenTrackedFileNames.includes(name) || policy.forbiddenTrackedExtensions.includes(path.extname(name))) diagnostics.push(item("RPG04_TRACKED_SECRET_OR_BINARY", relative));
  }
  await scanTree(moduleRoot, moduleRoot, repositoryRoot, policy, diagnostics);
  const researchRoot = path.join(repositoryRoot, "docs", "ai-rpg-experiment");
  await scanTree(researchRoot, researchRoot, repositoryRoot, policy, diagnostics);
  return { ok: diagnostics.length === 0, diagnostics: sortItems(diagnostics) };
}

async function main() {
  const args = process.argv.slice(2), bootstrap = args.length === 1 && args[0] === "--bootstrap";
  if (!bootstrap && args.length !== 0) { console.error("RPG04_ARGUMENT_ERROR"); process.exitCode = 2; return; }
  const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), ".."), repositoryRoot = path.resolve(moduleRoot, "../..");
  try {
    const policy = JSON.parse(await fsp.readFile(path.join(moduleRoot, "module-boundary.json"), "utf8"));
    const baseline = JSON.parse(await fsp.readFile(path.join(moduleRoot, "docs", "RPG04_BASELINE.json"), "utf8"));
    const report = await auditRpg04({ moduleRoot, repositoryRoot, policy, baseline, bootstrap });
    if (!report.ok) { console.error(`RPG04_BOUNDARY_FAILED count=${report.diagnostics.length}`); report.diagnostics.forEach((value) => console.error(`${value.code} ${value.path}`.trimEnd())); process.exitCode = 1; return; }
    console.log(`RPG04_BOUNDARY_OK mode=${bootstrap ? "bootstrap" : "complete"} frozen=baseline-blobs`);
  } catch (error) { console.error(error?.code ?? "RPG04_BOUNDARY_OPERATIONAL_ERROR"); process.exitCode = 2; }
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();

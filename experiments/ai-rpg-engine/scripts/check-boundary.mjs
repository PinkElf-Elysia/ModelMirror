import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const FIXED_BASE = "06ef51ae8d58c4e33029f02ab7263e24066734b2";
export const ALLOWED_CHANGE_PREFIXES = Object.freeze([
  "docs/ai-rpg-experiment/",
  "experiments/ai-rpg-engine/",
]);

const FORBIDDEN_NETWORK_MODULES = new Set([
  "http", "http2", "https", "net", "tls", "dgram", "dns",
  "node:http", "node:http2", "node:https", "node:net", "node:tls",
  "node:dgram", "node:dns", "undici", "axios", "got", "ky", "superagent",
  "openai", "@anthropic-ai/sdk", "@google/generative-ai",
]);
const RUNTIME_GLOBAL_PATTERN = /\b(?:fetch|WebSocket|XMLHttpRequest|EventSource)\s*\(|\bnavigator\s*\.\s*sendBeacon\s*\(|\b(?:globalThis|window|self)\s*\[\s*["'](?:fetch|WebSocket|XMLHttpRequest|EventSource)["']\s*\]/u;
const NON_LITERAL_LOADER_PATTERN = /\b(?:import|require)\s*\(\s*(?!["'])/u;
const SECRET_PATTERNS = [
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/u,
  /\bsk-[A-Za-z0-9_-]{20,}\b/u,
  /\bgh[pousr]_[A-Za-z0-9]{30,}\b/u,
  /\bxox[baprs]-[A-Za-z0-9-]{20,}\b/u,
];
const TEXT_EXTENSIONS = new Set([".cjs", ".js", ".json", ".md", ".mjs", ".toml", ".ts", ".tsx", ".yaml", ".yml"]);
const EXECUTABLE_EXTENSIONS = new Set([".cjs", ".js", ".mjs", ".ts", ".tsx"]);
const IMPORT_PATTERN = /(?:\bimport|\bexport)\s+(?:[^"']*?\s+from\s+)?["']([^"']+)["']|\bimport\s*\(\s*["']([^"']+)["']\s*\)|\brequire\s*\(\s*["']([^"']+)["']\s*\)/gu;

function normalize(relativePath) {
  return relativePath.split(path.sep).join("/");
}

function diagnostic(code, relativePath = "") {
  return Object.freeze({ code, path: normalize(relativePath) });
}

function sortDiagnostics(diagnostics) {
  return [...new Map(diagnostics
    .map((item) => [`${item.code}\u0000${item.path}`, item])
    .sort(([left], [right]) => left.localeCompare(right))).values()];
}

function isInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

function policyDiagnostics(policy) {
  const diagnostics = [];
  if (policy?.schemaVersion !== 1) diagnostics.push(diagnostic("BOUNDARY_POLICY_SCHEMA_VERSION"));
  if (policy?.moduleId !== "ai-rpg-engine") diagnostics.push(diagnostic("BOUNDARY_POLICY_MODULE_ID"));
  if (policy?.moduleRoot !== "." || policy?.moduleRootResolution !== "directory-containing-module-boundary") {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_MODULE_ROOT"));
  }
  if (policy?.activeRound !== "RPG-01" || policy?.activeRoundBaselineSha !== FIXED_BASE) {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_FIXED_BASE"));
  }
  const prefixes = policy?.repositoryChangePolicy?.allowedPrefixes;
  if (JSON.stringify(prefixes) !== JSON.stringify(ALLOWED_CHANGE_PREFIXES)) {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_ALLOWED_PREFIXES"));
  }
  if (policy?.parentIntegration !== "none" || JSON.stringify(policy?.allowedParentInteractions) !== "[]") {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_PARENT_INTEGRATION"));
  }
  if (policy?.networkPolicy?.runtime !== "none" || policy?.networkPolicy?.verification !== "none" ||
      policy?.networkPolicy?.modelCalls !== "none" || policy?.networkPolicy?.websiteProbes !== "none") {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_NETWORK"));
  }
  const allowedDependencies = policy?.dependencyPolicy?.allowedProductionDependencies;
  if (JSON.stringify(allowedDependencies) !== JSON.stringify({ ajv: "8.20.0" })) {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_DEPENDENCIES"));
  }
  if (JSON.stringify(policy?.dependencyPolicy?.forbiddenProtocols) !== JSON.stringify(["file:", "link:"])) {
    diagnostics.push(diagnostic("BOUNDARY_POLICY_DEPENDENCY_PROTOCOLS"));
  }
  return diagnostics;
}

async function walkTree(treeRoot, repositoryRoot, policy, diagnostics) {
  let entries;
  try {
    entries = await fs.readdir(treeRoot, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }

  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const absolute = path.join(treeRoot, entry.name);
    const relative = normalize(path.relative(repositoryRoot, absolute));
    if (entry.name === "node_modules") continue;

    const stat = await fs.lstat(absolute);
    if (stat.isSymbolicLink()) {
      let resolved;
      try {
        resolved = await fs.realpath(absolute);
      } catch {
        diagnostics.push(diagnostic("BOUNDARY_BROKEN_SYMLINK", relative));
        continue;
      }
      const resolvedTreeRoot = await fs.realpath(treeRoot);
      if (!isInside(resolvedTreeRoot, resolved)) {
        diagnostics.push(diagnostic("BOUNDARY_EXTERNAL_SYMLINK", relative));
      }
      continue;
    }
    if (stat.isDirectory()) {
      if (policy.generatedPaths.includes(entry.name) && entry.name !== "node_modules") {
        diagnostics.push(diagnostic("BOUNDARY_GENERATED_PATH", relative));
        continue;
      }
      await walkTree(absolute, repositoryRoot, policy, diagnostics);
      continue;
    }
    if (!stat.isFile()) continue;

    const lowerName = entry.name.toLowerCase();
    const extension = path.extname(lowerName);
    if (policy.forbiddenTrackedFileNames.includes(lowerName)) {
      diagnostics.push(diagnostic("BOUNDARY_SECRET_FILENAME", relative));
    }
    if (policy.forbiddenTrackedExtensions.includes(extension)) {
      diagnostics.push(diagnostic("BOUNDARY_FORBIDDEN_EXTENSION", relative));
    }
    if (!TEXT_EXTENSIONS.has(extension) && !policy.forbiddenTrackedFileNames.includes(lowerName)) continue;

    const content = await fs.readFile(absolute, "utf8");
    if (SECRET_PATTERNS.some((pattern) => pattern.test(content))) {
      diagnostics.push(diagnostic("BOUNDARY_SECRET_CONTENT", relative));
    }
    if (!EXECUTABLE_EXTENSIONS.has(extension)) continue;

    for (const match of content.matchAll(IMPORT_PATTERN)) {
      const specifier = match[1] ?? match[2] ?? match[3];
      if (!specifier) continue;
      if (/^(?:[A-Za-z]:[\\/]|\\\\|\/)/u.test(specifier)) {
        diagnostics.push(diagnostic("BOUNDARY_ABSOLUTE_IMPORT", relative));
        continue;
      }
      if (specifier.startsWith(".")) {
        const resolved = path.resolve(path.dirname(absolute), specifier);
        const moduleRoot = path.resolve(repositoryRoot, "experiments", "ai-rpg-engine");
        if (!isInside(moduleRoot, resolved)) diagnostics.push(diagnostic("BOUNDARY_PARENT_IMPORT", relative));
        continue;
      }
      if (FORBIDDEN_NETWORK_MODULES.has(specifier)) diagnostics.push(diagnostic("BOUNDARY_NETWORK_MODULE", relative));
      const inRuntime = relative.startsWith("experiments/ai-rpg-engine/src/");
      if (inRuntime && (specifier.startsWith("node:") || FORBIDDEN_NETWORK_MODULES.has(specifier))) {
        diagnostics.push(diagnostic("BOUNDARY_RUNTIME_IO_MODULE", relative));
      }
      if (inRuntime && !specifier.startsWith("node:") && specifier !== "ajv" && specifier !== "ajv/dist/2020.js") {
        diagnostics.push(diagnostic("BOUNDARY_RUNTIME_DEPENDENCY", relative));
      }
    }

    const self = relative === "experiments/ai-rpg-engine/scripts/check-boundary.mjs";
    if (!self && RUNTIME_GLOBAL_PATTERN.test(content)) diagnostics.push(diagnostic("BOUNDARY_NETWORK_GLOBAL", relative));
    if (!self && NON_LITERAL_LOADER_PATTERN.test(content)) diagnostics.push(diagnostic("BOUNDARY_NON_LITERAL_LOADER", relative));
    if (relative.startsWith("experiments/ai-rpg-engine/src/") && /\bprocess\s*\.\s*env\b/u.test(content)) {
      diagnostics.push(diagnostic("BOUNDARY_RUNTIME_ENV", relative));
    }
  }
}

function findForbiddenProtocol(value, protocols) {
  if (typeof value === "string") return protocols.some((protocol) => value.startsWith(protocol));
  if (Array.isArray(value)) return value.some((entry) => findForbiddenProtocol(entry, protocols));
  if (value && typeof value === "object") return Object.values(value).some((entry) => findForbiddenProtocol(entry, protocols));
  return false;
}

async function auditPackage(moduleRoot, repositoryRoot, policy, diagnostics) {
  const packagePath = path.join(moduleRoot, "package.json");
  let packageJson;
  try {
    packageJson = JSON.parse(await fs.readFile(packagePath, "utf8"));
  } catch (error) {
    if (error?.code === "ENOENT") return;
    diagnostics.push(diagnostic("BOUNDARY_PACKAGE_INVALID", normalize(path.relative(repositoryRoot, packagePath))));
    return;
  }
  const packageRelative = normalize(path.relative(repositoryRoot, packagePath));
  if (JSON.stringify(packageJson.dependencies ?? {}) !== JSON.stringify({ ajv: "8.20.0" })) {
    diagnostics.push(diagnostic("BOUNDARY_PACKAGE_DEPENDENCIES", packageRelative));
  }
  for (const section of ["devDependencies", "optionalDependencies", "peerDependencies"]) {
    if (Object.keys(packageJson[section] ?? {}).length > 0) diagnostics.push(diagnostic("BOUNDARY_PACKAGE_EXTRA_DEPENDENCY", packageRelative));
  }
  if (findForbiddenProtocol(packageJson, policy.dependencyPolicy.forbiddenProtocols)) {
    diagnostics.push(diagnostic("BOUNDARY_PACKAGE_LOCAL_PROTOCOL", packageRelative));
  }
  for (const script of Object.values(packageJson.scripts ?? {})) {
    if (/(?:\bnode\s+-e\b|\bpowershell(?:\.exe)?\s+-command\b|\bcmd(?:\.exe)?\s+\/c\b|\b(?:sh|bash)\s+-c\b|\bcurl(?:\.exe)?\b|\bwget\b)/iu.test(script)) {
      diagnostics.push(diagnostic("BOUNDARY_PACKAGE_SCRIPT_UNBOUNDED", packageRelative));
      break;
    }
  }

  const lockPath = path.join(moduleRoot, "package-lock.json");
  try {
    const lockJson = JSON.parse(await fs.readFile(lockPath, "utf8"));
    if (findForbiddenProtocol(lockJson, policy.dependencyPolicy.forbiddenProtocols)) {
      diagnostics.push(diagnostic("BOUNDARY_LOCK_LOCAL_PROTOCOL", normalize(path.relative(repositoryRoot, lockPath))));
    }
  } catch (error) {
    if (error?.code !== "ENOENT") diagnostics.push(diagnostic("BOUNDARY_LOCK_INVALID", normalize(path.relative(repositoryRoot, lockPath))));
  }
}

export async function auditBoundary({ moduleRoot, repositoryRoot = path.resolve(moduleRoot, "../.."), policy }) {
  const diagnostics = policyDiagnostics(policy);
  const expectedModuleRoot = path.resolve(repositoryRoot, "experiments", "ai-rpg-engine");
  if (path.resolve(moduleRoot) !== expectedModuleRoot) diagnostics.push(diagnostic("BOUNDARY_MODULE_LOCATION"));
  await walkTree(path.resolve(moduleRoot), path.resolve(repositoryRoot), policy, diagnostics);
  await walkTree(path.resolve(repositoryRoot, "docs", "ai-rpg-experiment"), path.resolve(repositoryRoot), policy, diagnostics);
  await auditPackage(path.resolve(moduleRoot), path.resolve(repositoryRoot), policy, diagnostics);
  const sorted = sortDiagnostics(diagnostics);
  return Object.freeze({ ok: sorted.length === 0, diagnostics: Object.freeze(sorted) });
}

async function main() {
  const args = process.argv.slice(2);
  let rootArgument = null;
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--root" && args[index + 1]) {
      rootArgument = args[index + 1];
      index += 1;
    } else {
      console.error("BOUNDARY_ARGUMENT_ERROR");
      process.exitCode = 2;
      return;
    }
  }
  const moduleRoot = rootArgument
    ? path.resolve(rootArgument)
    : path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  try {
    const policy = JSON.parse(await fs.readFile(path.join(moduleRoot, "module-boundary.json"), "utf8"));
    const report = await auditBoundary({ moduleRoot, policy });
    if (report.ok) {
      console.log("RPG01_BOUNDARY_OK");
    } else {
      console.error(`RPG01_BOUNDARY_FAILED count=${report.diagnostics.length}`);
      for (const item of report.diagnostics) console.error(`${item.code} ${item.path}`.trimEnd());
      process.exitCode = 1;
    }
  } catch {
    console.error("BOUNDARY_OPERATIONAL_ERROR");
    process.exitCode = 2;
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();

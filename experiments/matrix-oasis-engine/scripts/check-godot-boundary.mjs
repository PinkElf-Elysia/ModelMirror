import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const godotRoot = path.join(moduleRoot, "apps", "runtime-godot");

const FORBIDDEN_CAPABILITIES = Object.freeze([
  ["GODOT_FIRST_PARTY_NETWORK", new RegExp(
    `\\b(?:${[
      ["HTTP", "Request"].join(""),
      ["HTTP", "Client"].join(""),
      ["Web", "Socket"].join(""),
      ["StreamPeer", "TCP"].join(""),
      ["PacketPeer", "UDP"].join(""),
      ["ENet", "MultiplayerPeer"].join(""),
      ["TCP", "Server"].join(""),
    ].join("|")})\\b`,
    "u",
  )],
  ["GODOT_FIRST_PARTY_PROCESS", /\bOS\s*\.\s*(?:execute|create_process|create_instance)\s*\(/u],
  ["GODOT_FIRST_PARTY_ENVIRONMENT", /\bOS\s*\.\s*(?:get_environment|has_environment)\s*\(/u],
  ["GODOT_FIRST_PARTY_DYNAMIC_SCRIPT", /\b(?:Script|GDScript)\s*\.\s*new\s*\(/u],
  ["GODOT_FIRST_PARTY_FILESYSTEM_WRITE", /\b(?:DirAccess\s*\.\s*(?:make_dir|make_dir_recursive|remove_absolute|rename_absolute|copy_absolute)|ResourceSaver\s*\.\s*save)\s*\(/u],
]);

export class GodotBoundaryError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotBoundaryError";
    this.code = code;
  }
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function collectScripts(root = godotRoot) {
  const scripts = [];
  const stack = [root];
  const excludedVendors = new Set([
    path.resolve(root, "addons", "gdUnit4"),
    path.resolve(root, "addons", "gdgs"),
  ]);
  while (stack.length > 0) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    entries.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
    for (const entry of entries) {
      const absolute = path.join(current, entry.name);
      if (entry.isSymbolicLink()) {
        throw new GodotBoundaryError("GODOT_FIRST_PARTY_SYMLINK");
      }
      if (entry.isDirectory()) {
        if (!excludedVendors.has(path.resolve(absolute)) && entry.name !== ".godot") {
          stack.push(absolute);
        }
        continue;
      }
      if (entry.isFile() && entry.name.endsWith(".gd") && isContained(root, absolute)) {
        scripts.push(absolute);
      }
    }
  }
  return scripts.sort();
}

function hasUnsafeAbsoluteLiteral(source) {
  const literals = source.matchAll(/(["'])(.*?)\1/gsu);
  for (const match of literals) {
    const value = match[2];
    const prefix = source.slice(Math.max(0, match.index - 32), match.index);
    const safeJsonPointer = /^\/(?:runtimePack|receipt|scenePack|spatialAssembly|analysisRequest|verificationRequest|nodeContexts|placements)(?:\/[^\r\n]*)?$/u.test(value) ||
      /^\/(?:prepared|options|actionId|runtime)$/u.test(value) ||
      /^\/snapshot(?:\/(?:pack|status|stepCount|variables))?$/u.test(value) ||
      (/\b(?:path|action_path)\s*\+\s*$/u.test(prefix) && /^(?:\/[A-Za-z][A-Za-z0-9]*)+$/u.test(value)) ||
      (value === "/" && /\btrim_prefix\s*\(\s*$/u.test(prefix));
    if (safeJsonPointer) {
      continue;
    }
    if (
      /^[A-Za-z]:[\\/]/u.test(value) ||
      /^(?:\\\\|\/\/)[^/\\]/u.test(value) ||
      /^\/(?!\/)/u.test(value) ||
      /^file:/iu.test(value)
    ) {
      return true;
    }
  }
  return false;
}

function hasUnsafeDynamicLoad(source) {
  const trustedConstants = new Set();
  for (const match of source.matchAll(/^\s*const\s+([A-Z][A-Z0-9_]*)\s*(?::=|=)\s*["']res:\/\/[^"']+["']/gmu)) {
    trustedConstants.add(match[1]);
  }
  for (const match of source.matchAll(/\b(?:load|preload|ResourceLoader\s*\.\s*load)\s*\(([^\n)]*)/gu)) {
    const argument = match[1].trim().split(",", 1)[0].trim();
    if (!/^["']res:\/\//u.test(argument) && !trustedConstants.has(argument)) {
      return true;
    }
  }
  return false;
}

function hasUnsafeFileOpen(source, relativePath) {
  for (const match of source.matchAll(/\bFileAccess\s*\.\s*open\s*\(([^\n)]*)/gu)) {
    const args = match[1];
    const staticResourceRead = /^\s*["']res:\/\//u.test(args) &&
      /\bFileAccess\s*\.\s*READ\b/u.test(args);
    const approvedRuntimeRead = relativePath === "runtime/runtime_artifact_loader.gd" &&
      /^\s*approved_path\s*,\s*FileAccess\s*\.\s*READ\s*$/u.test(args);
    const approvedSceneRead = relativePath === "scene_binding/scene_artifact_loader.gd" &&
      /^\s*path\s*,\s*FileAccess\s*\.\s*READ\s*$/u.test(args);
    const approvedAnalysisRead = relativePath === "spatial_analysis/environment_analyzer.gd" &&
      /^\s*path\s*,\s*FileAccess\s*\.\s*READ\s*$/u.test(args);
    const approvedAnalysisWrite = relativePath === "spatial_analysis/environment_analyzer.gd" &&
      /^\s*paths\["output"\]\s*,\s*FileAccess\s*\.\s*WRITE\s*$/u.test(args);
    const approvedVerificationRead = relativePath === "spatial_solution_verification/solution_verifier.gd" &&
      /^\s*path\s*,\s*FileAccess\s*\.\s*READ\s*$/u.test(args);
    const approvedVerificationWrite = relativePath === "spatial_solution_verification/solution_verifier.gd" &&
      /^\s*path\s*,\s*FileAccess\s*\.\s*WRITE\s*$/u.test(args);
    const approvedEvidenceRead = relativePath === "runtime_evidence/runtime_evidence_runner.gd" &&
      /^\s*path\s*,\s*FileAccess\s*\.\s*READ\s*$/u.test(args);
    const approvedEvidenceWrite = relativePath === "runtime_evidence/runtime_evidence_runner.gd" &&
      /^\s*OUTPUT_PATH\s*,\s*FileAccess\s*\.\s*WRITE\s*$/u.test(args);
    if (!staticResourceRead && !approvedRuntimeRead && !approvedSceneRead &&
      !approvedAnalysisRead && !approvedAnalysisWrite && !approvedVerificationRead && !approvedVerificationWrite &&
      !approvedEvidenceRead && !approvedEvidenceWrite) {
      return true;
    }
  }
  return false;
}

function hasUnsafeImageSave(source, relativePath) {
  for (const match of source.matchAll(/\bimage\s*\.\s*save_png\s*\(([^\n)]*)/gu)) {
    const approvedEvidencePng = relativePath === "runtime_evidence/runtime_evidence_runner.gd" &&
      /^\s*["']res:\/\/runtime_evidence\/["']\s*\+\s*relative\s*$/u.test(match[1]);
    if (!approvedEvidencePng) return true;
  }
  return false;
}

function isApprovedR20BridgeCapability(code, source, relativePath) {
  if (relativePath !== "npc_authority_prototype/npc_authority_lab.gd" ||
      !["GODOT_FIRST_PARTY_NETWORK", "GODOT_FIRST_PARTY_ENVIRONMENT"].includes(code)) {
    return false;
  }
  const environmentCalls = [...source.matchAll(/\bOS\s*\.\s*(get_environment|has_environment)\s*\(\s*([^\n)]*)\)/gu)]
    .map((match) => Object.freeze({ method: match[1], argument: match[2].trim() }));
  return /const LOOPBACK_BASE := "http:\/\/127\.0\.0\.1:43120\/v1\/"/u.test(source) &&
    /@onready var _request: HTTPRequest = \$AuthorityRequest/u.test(source) &&
    !/\bHTTPRequest\s*\.\s*new\s*\(/u.test(source) &&
    !/\b(?:WebSocket|StreamPeerTCP|PacketPeerUDP|ENetMultiplayerPeer|TCPServer)\b/u.test(source) &&
    !/\b(?:HTTPClient\.new|connect_to_host|request_raw)\b/u.test(source) &&
    [...source.matchAll(/\b_request\.request\s*\(/gu)].length === 1 &&
    /var error := _request\.request\(LOOPBACK_BASE \+ route, headers, method, body\)/u.test(source) &&
    /route not in \["command", "arrived", "mirror", "reset", "verify"\]/u.test(source) &&
    !/\bhttps?:\/\/(?!127\.0\.0\.1:43120\/v1\/)/u.test(source) &&
    environmentCalls.length === 1 &&
    environmentCalls[0].method === "get_environment" &&
    environmentCalls[0].argument === "SESSION_TOKEN_ENV" &&
    /const SESSION_TOKEN_ENV := "MATRIX_OASIS_R20_SESSION_TOKEN"/u.test(source);
}

export function auditGodotBoundary({ root = godotRoot } = {}) {
  const resolvedRoot = fs.realpathSync(root);
  const violations = [];
  for (const absolute of collectScripts(resolvedRoot)) {
    const relativePath = path.relative(resolvedRoot, absolute).replaceAll("\\", "/");
    const source = fs.readFileSync(absolute, "utf8");
    for (const [code, pattern] of FORBIDDEN_CAPABILITIES) {
      if (pattern.test(source) && !isApprovedR20BridgeCapability(code, source, relativePath)) {
        violations.push({ code, path: relativePath });
      }
    }
    if (hasUnsafeAbsoluteLiteral(source)) {
      violations.push({ code: "GODOT_FIRST_PARTY_ABSOLUTE_PATH", path: relativePath });
    }
    if (hasUnsafeDynamicLoad(source)) {
      violations.push({ code: "GODOT_FIRST_PARTY_DYNAMIC_LOAD", path: relativePath });
    }
    if (hasUnsafeFileOpen(source, relativePath)) {
      violations.push({ code: "GODOT_FIRST_PARTY_FILESYSTEM_WRITE", path: relativePath });
    }
    if (hasUnsafeImageSave(source, relativePath)) {
      violations.push({ code: "GODOT_FIRST_PARTY_FILESYSTEM_WRITE", path: relativePath });
    }
  }
  violations.sort((left, right) => left.path === right.path
    ? left.code < right.code ? -1 : left.code > right.code ? 1 : 0
    : left.path < right.path ? -1 : 1);
  return Object.freeze({
    ok: violations.length === 0,
    checked: collectScripts(resolvedRoot).length,
    violations: Object.freeze(violations.map((item) => Object.freeze(item))),
  });
}

function isDirectExecution() {
  return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}

if (isDirectExecution()) {
  try {
    const report = auditGodotBoundary();
    if (!report.ok) {
      for (const violation of report.violations) {
        console.error(`${violation.code}\t${violation.path}`);
      }
      process.exitCode = 1;
    } else {
      console.log(`GODOT_BOUNDARY_OK checked=${report.checked}`);
    }
  } catch (error) {
    const code = error instanceof GodotBoundaryError
      ? error.code
      : "GODOT_BOUNDARY_OPERATIONAL_ERROR";
    console.error(code);
    process.exitCode = 2;
  }
}

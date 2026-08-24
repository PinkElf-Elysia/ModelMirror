import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { createServer as createNodeServer } from "node:http";
import { validatePrototypeCreatorQualificationJson } from "@matrix-oasis/prototype-creator-qualification-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

export const PROTOTYPE_HOST = "127.0.0.1";
export const PROTOTYPE_HOST_PORT = 43_110;
export const PROTOTYPE_HOST_ORIGIN = `http://${PROTOTYPE_HOST}:${PROTOTYPE_HOST_PORT}`;
export const PROTOTYPE_HOST_MARKER = "MATRIX_OASIS_R10_PROTOTYPE_HOST";
export const R16_PROTOTYPE_HOST_MARKER = "MATRIX_OASIS_R16_PROTOTYPE_HOST";

const TERMINAL = new Set(["ready", "failed"]);
const STATES = new Set([
  "awaiting_model_approval", "generating", "awaiting_asset_approval", "acquiring",
  "normalizing", "spatializing", "assembling", "qualifying", "ready", "failed",
]);
const R16_CACHE_LEVELS = new Set(["qualified", "evidence-only", "solved-only", "source-only"]);
const R16_QUALIFICATION_SUBPHASES = Object.freeze(["analyzing", "solving", "verifying", "evidencing"]);
const COOKIE_NAME = "matrix_oasis_r10_session";
const RUN_ID = /^r10-run-[1-9][0-9]*$/u;
const HASH = /^sha256:[0-9a-f]{64}$/u;
const HASH_ID = /^[0-9a-f]{64}$/u;
const SOURCE_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const CODE = /^[A-Z][A-Z0-9_]{2,127}$/u;
const POINTER = /^(?:\/(?:[^~/]|~0|~1)*)*$/u;
const SAFE_MODEL = /^[A-Za-z0-9._/-]{1,128}$/u;
const SAFE_HOST = /^(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?|localhost)(?::[1-9][0-9]{0,4})?$/u;
const BODY_LIMIT = 65_536;
const PROMPT_LIMIT = 32_768;
const WEB_ASSET_LIMIT = 4 * 1024 * 1024;
const SEC_FETCH_SITE_HEADER = ["sec", ["fet", "ch"].join(""), "site"].join("-");
const WEB_PATH = /^(?:\/(?:index\.html)?|\/assets\/[A-Za-z0-9._-]+\.(?:css|js))$/u;
const WEB_CONTENT_TYPES = new Set([
  "text/html; charset=utf-8",
  "text/css; charset=utf-8",
  "text/javascript; charset=utf-8",
]);

export class PrototypeHostOperationalError extends Error {
  constructor(code = "PROTOTYPE_HOST_INTERNAL_ERROR") {
    super(code); this.name = "PrototypeHostOperationalError"; this.code = code;
  }
}

const hash = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const encode = (value) => new TextEncoder().encode(value);
const exactKeys = (value, keys) => value !== null && typeof value === "object" && !Array.isArray(value) &&
  Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const frozen = (value) => {
  if (value === null || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) frozen(child);
  return Object.freeze(value);
};

function staticDiagnostics(value, fallback) {
  const source = Array.isArray(value) && value.length > 0 ? value : [{ code: fallback, path: "" }];
  const output = [];
  for (const item of source.slice(0, 64)) {
    const code = typeof item?.code === "string" && CODE.test(item.code) ? item.code : fallback;
    const path = typeof item?.path === "string" && item.path.length <= 512 && POINTER.test(item.path) ? item.path : "";
    const key = `${code}\0${path}`;
    if (!output.some((diagnostic) => `${diagnostic.code}\0${diagnostic.path}` === key)) {
      output.push(Object.freeze({ phase: "host", severity: "error", code, path, message: code }));
    }
  }
  return Object.freeze(output);
}

function failure(code, status = 400) {
  return { status, body: { ok: false, diagnostics: staticDiagnostics(null, code) } };
}

function parseConfiguration(value) {
  if (!exactKeys(value, ["endpointHost", "model", "modelReady", "assetsReady", "godotReady"]) ||
      typeof value.endpointHost !== "string" || !SAFE_HOST.test(value.endpointHost) ||
      typeof value.model !== "string" || !SAFE_MODEL.test(value.model) ||
      [value.modelReady, value.assetsReady, value.godotReady].some((item) => typeof item !== "boolean")) {
    throw new PrototypeHostOperationalError();
  }
  return frozen({ ...value });
}

function parseOperations(value, profile) {
  const baseNames = ["findCache", "generate", "describeAssets", "acquire", "publish", "launch", "recover", "stopLaunch"];
  const pendingNames = ["persistPending", "recoverPending", "discardPending"];
  const qualificationNames = ["qualify"];
  const names = [...baseNames, ...pendingNames];
  const r16Names = [...names, ...qualificationNames];
  const baseOnly = exactKeys(value, baseNames) && baseNames.every((name) => typeof value[name] === "function");
  const withPending = exactKeys(value, names) && names.every((name) => typeof value[name] === "function");
  const withQualification = exactKeys(value, r16Names) && r16Names.every((name) => typeof value[name] === "function");
  if (profile === "r16" ? !withQualification : ((!baseOnly && !withPending) || (profile === "r12" && !withPending))) {
    throw new PrototypeHostOperationalError();
  }
  return value;
}

function parseWebAssets(value) {
  if (value === undefined) return new Map();
  if (!(value instanceof Map) || value.size > 32) throw new PrototypeHostOperationalError();
  const output = new Map();
  for (const [assetPath, asset] of value) {
    if (typeof assetPath !== "string" || !WEB_PATH.test(assetPath) || output.has(assetPath) ||
        !exactKeys(asset, ["contentType", "bytes"]) || !WEB_CONTENT_TYPES.has(asset.contentType) ||
        !(asset.bytes instanceof Uint8Array) || asset.bytes.byteLength < 1 || asset.bytes.byteLength > WEB_ASSET_LIMIT) {
      throw new PrototypeHostOperationalError();
    }
    output.set(assetPath, Object.freeze({ contentType: asset.contentType, bytes: Uint8Array.from(asset.bytes) }));
  }
  return output;
}

function approvalHash(value) {
  return hash(encode(JSON.stringify(value)));
}

function publicApproval(value) {
  if (!value) return null;
  return { ...value.summary, approvalHash: value.hash, approved: value.approved };
}

function publicQualification(value) {
  return value === null ? null : frozen({
    profile: "matrix-oasis.creator-solved-evidence/1",
    cacheLevel: value.cacheLevel,
    subphase: value.subphase,
    attempt: value.attempt,
    reusedQualification: value.reusedQualification,
    solutionSha256: value.summary?.solutionSha256 ?? null,
    evidence: value.summary?.evidence ?? null,
  });
}

function publicRun(run, profile) {
  const value = {
    id: run.id,
    status: run.status,
    cacheHit: run.cacheHit,
    diagnostics: run.diagnostics,
    modelApproval: publicApproval(run.modelApproval),
    assetApproval: publicApproval(run.assetApproval),
    resultRunId: run.resultRunId,
  };
  if (profile === "r16") value.qualification = publicQualification(run.qualification);
  return frozen(value);
}

function sessionCookie(token) {
  return `${COOKIE_NAME}=${token}; Path=/api; HttpOnly; SameSite=Strict; Max-Age=28800`;
}

function cookieValue(header) {
  if (typeof header !== "string") return null;
  for (const part of header.split(";")) {
    const [name, ...rest] = part.trim().split("=");
    if (name === COOKIE_NAME && rest.length === 1 && /^[a-f0-9]{64}$/u.test(rest[0])) return rest[0];
  }
  return null;
}

function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  return timingSafeEqual(Buffer.from(left, "ascii"), Buffer.from(right, "ascii"));
}

async function readJsonBody(request) {
  const contentType = request.headers["content-type"];
  if (typeof contentType !== "string" || !/^application\/json(?:\s*;\s*charset=utf-8)?$/iu.test(contentType)) return failure("PROTOTYPE_HOST_CONTENT_TYPE_INVALID", 415);
  const declared = request.headers["content-length"];
  if (declared !== undefined && (!/^[0-9]+$/u.test(declared) || Number(declared) > BODY_LIMIT)) return failure("PROTOTYPE_HOST_BODY_TOO_LARGE", 413);
  const chunks = []; let total = 0;
  try {
    for await (const chunk of request) {
      total += chunk.length; if (total > BODY_LIMIT) return failure("PROTOTYPE_HOST_BODY_TOO_LARGE", 413);
      chunks.push(chunk);
    }
    const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, total));
    return { ok: true, value: JSON.parse(text) };
  } catch { return failure("PROTOTYPE_HOST_BODY_INVALID"); }
}

function writeJson(response, status, body, headers = {}) {
  const text = `${JSON.stringify(body)}\n`;
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
    "x-content-type-options": "nosniff", "content-length": Buffer.byteLength(text), ...headers });
  response.end(text);
}

function writeWebAsset(response, requestMethod, asset) {
  const headers = {
    "content-type": asset.contentType,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self'",
    "content-length": asset.bytes.byteLength,
  };
  response.writeHead(200, headers);
  response.end(requestMethod === "HEAD" ? undefined : asset.bytes);
}

function apiOriginAllowed(request, expectedHost, expectedOrigin) {
  if (request.headers.host !== expectedHost) return false;
  if (request.headers.origin === expectedOrigin) return true;
  return request.method === "GET" && request.headers.origin === undefined &&
    [undefined, "none", "same-origin"].includes(request.headers[SEC_FETCH_SITE_HEADER]);
}

function parseRecovery(value, profile) {
  if (value === undefined) return null;
  if (profile !== "r12" || !exactKeys(value, ["summary", "execute"]) || typeof value.execute !== "function") {
    throw new PrototypeHostOperationalError();
  }
  const summary = value.summary;
  const recoveryScopeValid = (summary?.maxWorldGets === 1 && summary?.maxDownloads === 3) ||
    (summary?.maxWorldGets === 0 && summary?.maxDownloads === 0);
  if (!exactKeys(summary, ["model", "worldIdSha256", "maxCreates", "maxPolls", "maxWorldGets", "maxDownloads", "creditLimit", "usdLimitCents"]) ||
      summary.model !== "marble-1.1" || !HASH.test(summary.worldIdSha256) || summary.maxCreates !== 0 || summary.maxPolls !== 0 ||
      !recoveryScopeValid || summary.creditLimit !== 0 || summary.usdLimitCents !== 0) {
    throw new PrototypeHostOperationalError();
  }
  return { summary: frozen(summary), execute: value.execute };
}

function parseWorldDiscovery(value, profile) {
  if (value === undefined) return null;
  if (profile !== "r12" || !exactKeys(value, ["summary", "execute", "recover"]) ||
      typeof value.execute !== "function" || typeof value.recover !== "function") {
    throw new PrototypeHostOperationalError();
  }
  const summary = value.summary;
  if (!exactKeys(summary, ["provider", "operation", "model", "pageSize", "status", "sortBy", "maxRequests",
    "maxCreates", "maxPolls", "maxWorldGets", "maxDownloads", "creditLimit", "usdLimitCents"]) ||
      summary.provider !== "world-labs-marble" || summary.operation !== "worlds:list" || summary.model !== "marble-1.1" ||
      summary.pageSize !== 100 || summary.status !== "SUCCEEDED" || summary.sortBy !== "created_at" || summary.maxRequests !== 1 ||
      summary.maxCreates !== 0 || summary.maxPolls !== 0 || summary.maxWorldGets !== 0 || summary.maxDownloads !== 0 ||
      summary.creditLimit !== 0 || summary.usdLimitCents !== 0) {
    throw new PrototypeHostOperationalError();
  }
  return { summary: frozen(summary), execute: value.execute, recover: value.recover };
}

function sanitizeWorldDiscoveryResult(value) {
  if (!exactKeys(value, ["ok", "worlds", "counts"]) || value.ok !== true || !Array.isArray(value.worlds) || value.worlds.length > 100 ||
      !exactKeys(value.counts, ["listRequests", "creates", "polls", "worldGets", "downloads"]) ||
      value.counts.listRequests !== 1 || value.counts.creates !== 0 || value.counts.polls !== 0 ||
      value.counts.worldGets !== 0 || value.counts.downloads !== 0) return null;
  const candidates = [];
  const privateWorldIds = new Map();
  for (const item of value.worlds) {
    if (!exactKeys(item, ["worldId", "createdAt", "updatedAt", "model", "worldPrompt", "assets"]) ||
        typeof item.worldId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/u.test(item.worldId) || item.model !== "marble-1.1" ||
        typeof item.createdAt !== "string" || typeof item.updatedAt !== "string" ||
        typeof item.worldPrompt !== "string" || item.worldPrompt.length < 1 || encode(item.worldPrompt).length > PROMPT_LIMIT ||
        !exactKeys(item.assets, ["panorama", "collider", "spatialSource"]) ||
        [item.assets.panorama, item.assets.collider, item.assets.spatialSource].some((entry) => typeof entry !== "boolean")) return null;
    const worldIdSha256 = hash(encode(item.worldId));
    if (privateWorldIds.has(worldIdSha256)) return null;
    privateWorldIds.set(worldIdSha256, item.worldId);
    candidates.push(frozen({
      worldIdSha256,
      promptSha256: hash(encode(item.worldPrompt)),
      createdAt: item.createdAt,
      updatedAt: item.updatedAt,
      model: "marble-1.1",
      assets: { ...item.assets },
    }));
  }
  return { candidates: frozen(candidates), privateWorldIds };
}

function sanitizeRecovered(value) {
  if (!Array.isArray(value)) return [];
  const output = [];
  for (const item of value.slice(0, 100)) {
    if (!exactKeys(item, ["runId", "promptSha256", "model"]) ||
        typeof item.runId !== "string" || !/^[0-9a-f]{64}-[0-9a-f]{64}$/u.test(item.runId) ||
        typeof item.promptSha256 !== "string" || !HASH.test(item.promptSha256) ||
        typeof item.model !== "string" || !/^[A-Za-z0-9._/-]{1,128}$/u.test(item.model)) continue;
    output.push(item.runId);
  }
  return output;
}

function sanitizePendingApproval(value) {
  if (!exactKeys(value, ["blueprintSha256", "marble", "meshy"]) || !HASH.test(value.blueprintSha256) ||
      !exactKeys(value.marble, ["model", "environmentPrompt", "recovered", "maxCreates", "maxPolls", "maxDownloads", "creditLimit", "usdLimitCents"]) ||
      value.marble.model !== "marble-1.1" || typeof value.marble.environmentPrompt !== "string" ||
      value.marble.environmentPrompt.length < 1 || encode(value.marble.environmentPrompt).length > PROMPT_LIMIT ||
      typeof value.marble.recovered !== "boolean" ||
      !exactKeys(value.meshy, ["model", "briefs", "maxTasks", "creditLimit"]) || value.meshy.model !== "meshy-6" ||
      !Array.isArray(value.meshy.briefs) || value.meshy.briefs.length > 6) return null;
  const expectedMarble = value.marble.recovered
    ? [0, 0, 0, 0, 0]
    : [1, 180, 3, 1600, 150];
  const meshyRecovered = value.meshy.maxTasks === 0 && value.meshy.creditLimit === 0;
  if ([value.marble.maxCreates, value.marble.maxPolls, value.marble.maxDownloads,
    value.marble.creditLimit, value.marble.usdLimitCents].some((item, index) => item !== expectedMarble[index]) ||
      (!meshyRecovered && (value.meshy.maxTasks !== value.meshy.briefs.length * 2 ||
        value.meshy.creditLimit !== value.meshy.briefs.length * 30))) return null;
  const briefs = [];
  for (const brief of value.meshy.briefs) {
    if (!exactKeys(brief, ["id", "kind", "prompt"]) || typeof brief.id !== "string" || brief.id.length < 1 || brief.id.length > 128 ||
        !["prop", "character-placeholder"].includes(brief.kind) || typeof brief.prompt !== "string" ||
        brief.prompt.length < 1 || encode(brief.prompt).length > PROMPT_LIMIT) return null;
    briefs.push({ id: brief.id, kind: brief.kind, prompt: brief.prompt });
  }
  return frozen({
    blueprintSha256: value.blueprintSha256,
    marble: { ...value.marble },
    meshy: { model: value.meshy.model, briefs, maxTasks: value.meshy.maxTasks, creditLimit: value.meshy.creditLimit },
  });
}

function sanitizePendingRecovered(value, model) {
  if (!exactKeys(value, ["runs"]) || !Array.isArray(value.runs) || value.runs.length > 1) return [];
  const output = [];
  for (const item of value.runs) {
    if (!exactKeys(item, ["promptSha256", "model", "artifacts", "approval"]) || !HASH.test(item.promptSha256) ||
        item.model !== model || item.artifacts === null || typeof item.artifacts !== "object" || Array.isArray(item.artifacts)) continue;
    const approval = sanitizePendingApproval(item.approval);
    if (approval !== null) output.push({ promptSha256: item.promptSha256, model: item.model,
      artifacts: item.artifacts, approval });
  }
  return output;
}

function sanitizeR16Qualification(value) {
  let canonical;
  try { canonical = canonicalizeJsonValue(value); } catch { return null; }
  const report = validatePrototypeCreatorQualificationJson(canonical);
  if (report?.valid !== true || !Array.isArray(report.diagnostics) || report.diagnostics.length !== 0 ||
      value.evidence.medianFpsMilli !== Math.floor(1_000_000_000 / value.evidence.medianFrameMicros)) return null;
  return frozen({ promptSha256: value.promptSha256, model: value.model, sourceRunId: value.sourceRunId,
    solutionSha256: value.hashes.spatialSolutionSha256, evidence: { ...value.evidence },
    qualificationRunId: hash(encode(canonical)).slice(7) });
}

function sanitizeR16Cache(value) {
  if (value?.ok !== true || !R16_CACHE_LEVELS.has(value.cacheLevel)) return null;
  if (value.cacheLevel === "qualified") {
    if (!exactKeys(value, ["ok", "cacheLevel", "qualificationRunId", "qualification"]) ||
        !HASH_ID.test(value.qualificationRunId)) return null;
    const summary = sanitizeR16Qualification(value.qualification);
    return summary === null || summary.qualificationRunId !== value.qualificationRunId ? null : frozen({ cacheLevel: value.cacheLevel,
      qualificationRunId: value.qualificationRunId, summary });
  }
  if (!exactKeys(value, ["ok", "cacheLevel", "sourceRunId", "expectedSolutionSha256"]) ||
      !SOURCE_RUN_ID.test(value.sourceRunId) ||
      (value.expectedSolutionSha256 !== null && !HASH.test(value.expectedSolutionSha256))) return null;
  return frozen({ cacheLevel: value.cacheLevel, sourceRunId: value.sourceRunId,
    expectedSolutionSha256: value.expectedSolutionSha256 });
}

function sanitizeR16QualificationResult(value) {
  if (!exactKeys(value, ["ok", "cacheLevel", "reusedQualification", "qualificationRunId", "qualification"]) ||
      value.ok !== true || !R16_CACHE_LEVELS.has(value.cacheLevel) || typeof value.reusedQualification !== "boolean" ||
      !HASH_ID.test(value.qualificationRunId)) return null;
  const summary = sanitizeR16Qualification(value.qualification);
  return summary === null || summary.qualificationRunId !== value.qualificationRunId ? null : frozen({ cacheLevel: value.cacheLevel, reusedQualification: value.reusedQualification,
    qualificationRunId: value.qualificationRunId, summary });
}

function sanitizeR16Recovered(value, model) {
  if (!exactKeys(value, ["currentRunId", "runs"]) ||
      (value.currentRunId !== null && !HASH_ID.test(value.currentRunId)) ||
      !Array.isArray(value.runs) || value.runs.length > 100) return { currentRunId: null, runs: [] };
  const output = [];
  let partialSeen = false;
  for (const item of value.runs) {
    if (!exactKeys(item, ["promptSha256", "model", "cache"]) || !HASH.test(item.promptSha256) ||
        item.model !== model) continue;
    const cache = sanitizeR16Cache(item.cache);
    if (cache === null || (cache.cacheLevel === "qualified" &&
        (cache.summary.promptSha256 !== item.promptSha256 || cache.summary.model !== item.model)) ||
        (cache.cacheLevel !== "qualified" && partialSeen)) continue;
    if (cache.cacheLevel !== "qualified") partialSeen = true;
    output.push(frozen({ promptSha256: item.promptSha256, model: item.model, cache }));
  }
  const qualifiedIds = output.filter((item) => item.cache.cacheLevel === "qualified")
    .map((item) => item.cache.qualificationRunId);
  return frozen({ currentRunId: qualifiedIds.includes(value.currentRunId) ? value.currentRunId : null, runs: output });
}

export function createPrototypeHost({ configuration, operations, webAssets, profile = "r10", createServer = createNodeServer,
  port = PROTOTYPE_HOST_PORT, recovery = undefined, worldDiscovery = undefined }) {
  if (!["r10", "r12", "r16"].includes(profile)) throw new PrototypeHostOperationalError();
  const config = parseConfiguration(configuration); const op = parseOperations(operations, profile);
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) throw new PrototypeHostOperationalError();
  const hostHeader = `${PROTOTYPE_HOST}:${port}`;
  const hostOrigin = `http://${hostHeader}`;
  const recoveryConfig = parseRecovery(recovery, profile);
  const worldDiscoveryConfig = parseWorldDiscovery(worldDiscovery, profile);
  const creatorAssets = parseWebAssets(webAssets);
  if (typeof createServer !== "function") throw new PrototypeHostOperationalError();
  let server = null; let sessionToken = null; let runCounter = 0; let currentRunId = null;
  let background = Promise.resolve(); let godotActive = false;
  const runs = new Map();
  const recoveryState = recoveryConfig === null ? null : {
    status: "awaiting_approval", approved: false, diagnostics: Object.freeze([]),
    summary: recoveryConfig.summary, hash: approvalHash(recoveryConfig.summary),
  };
  const worldDiscoveryState = worldDiscoveryConfig === null ? null : {
    status: "awaiting_approval", approved: false, diagnostics: Object.freeze([]), candidates: Object.freeze([]),
    summary: worldDiscoveryConfig.summary, hash: approvalHash(worldDiscoveryConfig.summary), privateWorldIds: new Map(), recovery: null,
  };

  const publicRecovery = () => recoveryState === null ? null : frozen({
    ...recoveryState.summary,
    status: recoveryState.status,
    diagnostics: recoveryState.diagnostics,
    approvalHash: recoveryState.hash,
    approved: recoveryState.approved,
  });

  const publicWorldDiscovery = () => worldDiscoveryState === null ? null : frozen({
    ...worldDiscoveryState.summary,
    statusState: worldDiscoveryState.status,
    diagnostics: worldDiscoveryState.diagnostics,
    candidates: worldDiscoveryState.candidates,
    recovery: worldDiscoveryState.recovery === null ? null : {
      ...worldDiscoveryState.recovery.summary,
      status: worldDiscoveryState.recovery.status,
      diagnostics: worldDiscoveryState.recovery.diagnostics,
      approvalHash: worldDiscoveryState.recovery.hash,
      approved: worldDiscoveryState.recovery.approved,
    },
    approvalHash: worldDiscoveryState.hash,
    approved: worldDiscoveryState.approved,
  });

  const finishRecovery = async () => {
    try {
      const result = await recoveryConfig.execute();
      if (!result?.ok) {
        recoveryState.status = "failed";
        recoveryState.diagnostics = staticDiagnostics(result?.diagnostics, "PROTOTYPE_HOST_RECOVERY_FAILED");
        return;
      }
      recoveryState.status = "ready";
    } catch {
      recoveryState.status = "failed";
      recoveryState.diagnostics = staticDiagnostics(null, "PROTOTYPE_HOST_RECOVERY_FAILED");
    }
  };

  const finishWorldDiscovery = async () => {
    try {
      const result = await worldDiscoveryConfig.execute();
      if (!result?.ok) {
        worldDiscoveryState.status = "failed";
        worldDiscoveryState.diagnostics = staticDiagnostics(result?.diagnostics, "PROTOTYPE_HOST_WORLD_DISCOVERY_FAILED");
        return;
      }
      const sanitized = sanitizeWorldDiscoveryResult(result);
      if (sanitized === null) {
        worldDiscoveryState.status = "failed";
        worldDiscoveryState.diagnostics = staticDiagnostics(null, "PROTOTYPE_HOST_WORLD_DISCOVERY_FAILED");
        return;
      }
      worldDiscoveryState.candidates = sanitized.candidates;
      worldDiscoveryState.privateWorldIds = sanitized.privateWorldIds;
      worldDiscoveryState.status = "ready";
    } catch {
      worldDiscoveryState.status = "failed";
      worldDiscoveryState.diagnostics = staticDiagnostics(null, "PROTOTYPE_HOST_WORLD_DISCOVERY_FAILED");
    }
  };

  const finishWorldRecovery = async () => {
    const recovery = worldDiscoveryState.recovery;
    try {
      const worldId = worldDiscoveryState.privateWorldIds.get(recovery.summary.worldIdSha256);
      if (typeof worldId !== "string") throw new PrototypeHostOperationalError();
      const result = await worldDiscoveryConfig.recover(worldId);
      if (!result?.ok) {
        recovery.status = "failed";
        recovery.diagnostics = staticDiagnostics(result?.diagnostics, "PROTOTYPE_HOST_RECOVERY_FAILED");
        return;
      }
      recovery.status = "ready";
    } catch {
      recovery.status = "failed";
      recovery.diagnostics = staticDiagnostics(null, "PROTOTYPE_HOST_RECOVERY_FAILED");
    }
  };

  const failRun = (run, diagnostics, fallback) => {
    run.status = "failed"; run.diagnostics = staticDiagnostics(diagnostics, fallback);
    run.prompt = null; run.artifacts = null; run.acquisition = null; run.qualificationSource = null;
  };

  const qualificationState = (cacheLevel) => ({
    cacheLevel,
    subphase: null,
    attempt: 0,
    reusedQualification: false,
    summary: null,
  });

  const finishQualification = async (run, qualificationSource) => {
    if (profile !== "r16" || godotActive || !qualificationSource ||
        qualificationSource.cacheLevel === "qualified") {
      return failRun(run, null, godotActive ? "PROTOTYPE_HOST_GODOT_ACTIVE" : "PROTOTYPE_HOST_QUALIFICATION_FAILED");
    }
    const phaseOrder = new Map(R16_QUALIFICATION_SUBPHASES.map((name, index) => [name, index]));
    const firstPhase = qualificationSource.cacheLevel === "source-only" ? "analyzing" : "verifying";
    let lastPhase = -1;
    let lastAttempt = 0;
    run.status = "qualifying";
    run.qualification = qualificationState(qualificationSource.cacheLevel);
    godotActive = true;
    try {
      const qualified = await op.qualify({
        sourceRunId: qualificationSource.sourceRunId,
        expectedSolutionSha256: qualificationSource.expectedSolutionSha256,
        onStage(stage) {
          if (!exactKeys(stage, ["stage", "subphase", "attempt"]) || stage.stage !== "qualifying" ||
              !phaseOrder.has(stage.subphase) || !Number.isInteger(stage.attempt) || stage.attempt < 0 || stage.attempt > 2) {
            throw new PrototypeHostOperationalError("PROTOTYPE_HOST_QUALIFICATION_STAGE_INVALID");
          }
          const nextPhase = phaseOrder.get(stage.subphase);
          if ((lastPhase === -1 && stage.subphase !== firstPhase) || nextPhase < lastPhase ||
              (nextPhase === lastPhase && (stage.attempt < lastAttempt || stage.attempt > lastAttempt + 1)) ||
              (nextPhase > lastPhase && stage.subphase === "evidencing" && stage.attempt !== 0)) {
            throw new PrototypeHostOperationalError("PROTOTYPE_HOST_QUALIFICATION_STAGE_INVALID");
          }
          lastPhase = nextPhase;
          lastAttempt = stage.attempt;
          run.qualification.subphase = stage.subphase;
          run.qualification.attempt = stage.attempt;
        },
      });
      if (!qualified?.ok) return failRun(run, qualified?.diagnostics, "PROTOTYPE_HOST_QUALIFICATION_FAILED");
      const safe = sanitizeR16QualificationResult(qualified);
      if (safe === null || safe.cacheLevel !== qualificationSource.cacheLevel || safe.reusedQualification || lastPhase < 0 ||
          safe.summary.sourceRunId !== qualificationSource.sourceRunId || safe.summary.promptSha256 !== run.promptSha256 ||
          safe.summary.model !== run.model) {
        return failRun(run, null, "PROTOTYPE_HOST_QUALIFICATION_FAILED");
      }
      run.status = "ready";
      run.resultRunId = safe.qualificationRunId;
      run.cacheHit = false;
      run.qualification = { cacheLevel: safe.cacheLevel, subphase: null,
        attempt: safe.summary.evidence.attempt, reusedQualification: false, summary: safe.summary };
      currentRunId = safe.qualificationRunId;
      run.prompt = null; run.artifacts = null; run.acquisition = null; run.qualificationSource = null;
    } catch {
      failRun(run, null, "PROTOTYPE_HOST_QUALIFICATION_FAILED");
    } finally {
      godotActive = false;
    }
  };

  const assetApprovalSummary = (description) => {
    const recoveredEnvironment = profile !== "r10" && description.environmentCached === true;
    const recoveredAssets = profile !== "r10" && description.assetsCached === true;
    return {
      blueprintSha256: description.blueprintSha256,
      marble: { model: "marble-1.1", environmentPrompt: description.environmentPrompt, recovered: recoveredEnvironment,
        maxCreates: recoveredEnvironment ? 0 : 1, maxPolls: recoveredEnvironment ? 0 : 180,
        maxDownloads: recoveredEnvironment ? 0 : profile === "r10" ? 2 : 3,
        creditLimit: recoveredEnvironment ? 0 : 1600, usdLimitCents: recoveredEnvironment ? 0 : 150 },
      meshy: { model: "meshy-6", briefs: description.briefs.map((brief) => ({ id: brief.id, kind: brief.kind, prompt: brief.prompt })),
        maxTasks: recoveredAssets ? 0 : description.briefs.length * 2,
        creditLimit: recoveredAssets ? 0 : description.briefs.length * 30 },
    };
  };

  const finishGeneration = async (run) => {
    try {
      const generated = await op.generate({ prompt: run.prompt });
      if (!generated?.ok) return failRun(run, generated?.diagnostics, "PROTOTYPE_HOST_GENERATION_FAILED");
      const description = await op.describeAssets({ artifacts: generated.artifacts });
      if (!description?.ok || !HASH.test(description.blueprintSha256) || typeof description.environmentPrompt !== "string" ||
          !Array.isArray(description.briefs) || description.briefs.length > 6) {
        return failRun(run, description?.diagnostics, "PROTOTYPE_HOST_GENERATION_FAILED");
      }
      run.artifacts = generated.artifacts;
      const summary = assetApprovalSummary(description);
      if (profile !== "r10") await op.persistPending({ promptSha256: run.promptSha256, model: run.model,
        artifacts: run.artifacts, approval: summary });
      run.assetApproval = { summary: frozen(summary), hash: approvalHash(summary), approved: false };
      run.status = "awaiting_asset_approval";
    } catch { failRun(run, null, "PROTOTYPE_HOST_INTERNAL_ERROR"); }
  };

  const finishAssets = async (run) => {
    const reject = async (diagnostics, fallback) => {
      if (profile !== "r10") {
        let summary = run.assetApproval.summary;
        try {
          const description = await op.describeAssets({ artifacts: run.artifacts });
          if (description?.ok && HASH.test(description.blueprintSha256) && typeof description.environmentPrompt === "string" &&
              Array.isArray(description.briefs) && description.briefs.length <= 6) summary = assetApprovalSummary(description);
        } catch { /* retain the previous content-bound approval if offline refresh fails */ }
        run.status = "awaiting_asset_approval";
        run.diagnostics = staticDiagnostics(diagnostics, fallback);
        run.assetApproval = { summary: frozen(summary), hash: approvalHash(summary), approved: false };
        run.prompt = null;
        run.acquisition = null;
        return;
      }
      return failRun(run, diagnostics, fallback);
    };
    try {
      const acquired = await op.acquire({ artifacts: run.artifacts, approval: run.assetApproval.summary,
        onStage(stage) {
          if (stage === "normalizing" && run.status === "acquiring") run.status = "normalizing";
          if (stage === "spatializing" && ["acquiring", "normalizing"].includes(run.status)) run.status = "spatializing";
        } });
      if (!acquired?.ok) return reject(acquired?.diagnostics, "PROTOTYPE_HOST_ACQUISITION_FAILED");
      run.acquisition = acquired; run.status = "assembling";
      const published = await op.publish({ prompt: run.prompt, promptSha256: run.promptSha256, model: run.model,
        artifacts: run.artifacts, acquisition: acquired });
      if (!published?.ok || typeof published.runId !== "string" || !/^[0-9a-f]{64}-[0-9a-f]{64}$/u.test(published.runId)) {
        return reject(published?.diagnostics, "PROTOTYPE_HOST_ASSEMBLY_FAILED");
      }
      if (profile !== "r10") {
        try { await op.discardPending({ promptSha256: run.promptSha256, model: run.model }); }
        catch { /* a verified ready run wins over a stale pending checkpoint during recovery */ }
      }
      if (profile === "r16") {
        run.qualificationSource = frozen({ cacheLevel: "source-only", sourceRunId: published.runId,
          expectedSolutionSha256: null });
        await finishQualification(run, run.qualificationSource);
      } else {
        run.status = "ready"; run.resultRunId = published.runId; run.cacheHit = false; currentRunId = published.runId;
        run.prompt = null; run.artifacts = null; run.acquisition = null;
      }
    } catch { await reject(null, "PROTOTYPE_HOST_INTERNAL_ERROR"); }
  };

  const startBackground = (task) => {
    background = Promise.resolve().then(task).catch(() => {}).finally(() => {});
  };

  async function createRun(body) {
    if (!exactKeys(body, ["prompt"]) || typeof body.prompt !== "string") return failure("PROTOTYPE_HOST_PROMPT_INVALID");
    let promptBytes;
    try { promptBytes = encode(body.prompt); } catch { return failure("PROTOTYPE_HOST_PROMPT_INVALID"); }
    if (promptBytes.length < 1 || promptBytes.length > PROMPT_LIMIT || body.prompt.trim().length < 1 ||
        new TextDecoder("utf-8", { fatal: true }).decode(promptBytes) !== body.prompt) return failure("PROTOTYPE_HOST_PROMPT_INVALID");
    if ([...runs.values()].some((run) => !TERMINAL.has(run.status)) || (profile === "r16" && godotActive)) {
      return failure(godotActive ? "PROTOTYPE_HOST_GODOT_ACTIVE" : "PROTOTYPE_HOST_RUN_ACTIVE", 409);
    }
    const id = `r10-run-${++runCounter}`; const promptSha256 = hash(promptBytes);
    let cached;
    try {
      cached = await op.findCache(profile !== "r10"
        ? { promptSha256, model: config.model, prompt: body.prompt }
        : { promptSha256, model: config.model });
    }
    catch { return failure("PROTOTYPE_HOST_INTERNAL_ERROR", 500); }
    const run = { id, status: "awaiting_model_approval", cacheHit: false, diagnostics: Object.freeze([]),
      modelApproval: null, assetApproval: null, resultRunId: null, prompt: body.prompt, promptSha256,
      model: config.model, artifacts: null, acquisition: null,
      qualification: profile === "r16" ? qualificationState(null) : null, qualificationSource: null };
    const r16Cache = profile === "r16" ? sanitizeR16Cache(cached) : null;
    if (profile === "r16" && cached?.ok && r16Cache === null) return failure("PROTOTYPE_HOST_CACHE_INVALID", 500);
    if (r16Cache?.cacheLevel === "qualified" && r16Cache.summary.promptSha256 === promptSha256 &&
        r16Cache.summary.model === config.model) {
      run.status = "ready"; run.cacheHit = true; run.resultRunId = r16Cache.qualificationRunId;
      run.qualification = { cacheLevel: "qualified", subphase: null, attempt: r16Cache.summary.evidence.attempt,
        reusedQualification: true, summary: r16Cache.summary };
      run.prompt = null; currentRunId = r16Cache.qualificationRunId;
    } else if (r16Cache?.cacheLevel === "qualified") {
      return failure("PROTOTYPE_HOST_CACHE_INVALID", 500);
    } else if (r16Cache !== null) {
      run.status = "qualifying";
      run.qualification = qualificationState(r16Cache.cacheLevel);
      run.qualificationSource = r16Cache;
      run.prompt = null;
    } else if (cached?.ok && typeof cached.runId === "string" && SOURCE_RUN_ID.test(cached.runId)) {
      run.status = "ready"; run.cacheHit = true; run.resultRunId = cached.runId; run.prompt = null; currentRunId = cached.runId;
    } else {
      const summary = { endpointHost: config.endpointHost, model: config.model, maxRequests: 3,
        maxUsdCents: 100, prompt: body.prompt, promptSha256 };
      run.modelApproval = { summary: frozen(summary), hash: approvalHash(summary), approved: false };
    }
    runs.set(id, run);
    if (r16Cache !== null && r16Cache.cacheLevel !== "qualified") {
      startBackground(() => finishQualification(run, r16Cache));
    }
    return { status: 201, body: { ok: true, run: publicRun(run, profile) } };
  }

  function approveModel(run, body) {
    if (!config.modelReady) return failure("PROTOTYPE_HOST_MODEL_CONFIG_NOT_READY", 409);
    if (run.status !== "awaiting_model_approval" || run.modelApproval?.approved) return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
    if (!exactKeys(body, ["approvalHash"]) || body.approvalHash !== run.modelApproval.hash) return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
    run.modelApproval.approved = true; run.status = "generating"; startBackground(() => finishGeneration(run));
    return { status: 202, body: { ok: true, run: publicRun(run, profile) } };
  }

  function approveAssets(run, body) {
    if (run.status !== "awaiting_asset_approval" || run.assetApproval?.approved) return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
    const offlineOnly = run.assetApproval.summary.marble.maxCreates === 0 && run.assetApproval.summary.meshy.maxTasks === 0;
    if (!config.assetsReady && !offlineOnly) return failure("PROTOTYPE_HOST_ASSET_CONFIG_NOT_READY", 409);
    if (!exactKeys(body, ["approvalHash"]) || body.approvalHash !== run.assetApproval.hash) return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
    run.assetApproval.approved = true; run.diagnostics = []; run.status = "acquiring"; startBackground(() => finishAssets(run));
    return { status: 202, body: { ok: true, run: publicRun(run, profile) } };
  }

  async function launch(run) {
    if (!config.godotReady) return failure("PROTOTYPE_HOST_GODOT_NOT_READY", 409);
    if (run.status !== "ready" || !run.resultRunId) return failure("PROTOTYPE_HOST_RUN_NOT_READY", 409);
    if (godotActive) return failure("PROTOTYPE_HOST_GODOT_ACTIVE", 409);
    if (profile === "r16" && [...runs.values()].some((item) => !TERMINAL.has(item.status))) {
      return failure("PROTOTYPE_HOST_RUN_ACTIVE", 409);
    }
    godotActive = true;
    try {
      const result = await op.launch({ runId: run.resultRunId });
      if (!result?.ok) return failure("PROTOTYPE_HOST_GODOT_FAILED", 502);
      return { status: 202, body: { ok: true, runId: run.resultRunId } };
    } catch { return failure("PROTOTYPE_HOST_GODOT_FAILED", 502); }
    finally { godotActive = false; }
  }

  async function route(request) {
    let url;
    try { url = new URL(request.url, hostOrigin); } catch { return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404); }
    if (url.origin !== hostOrigin || url.search || url.hash) {
      return failure("PROTOTYPE_HOST_ORIGIN_INVALID", 403);
    }
    if (request.headers.host !== hostHeader) {
      return failure("PROTOTYPE_HOST_ORIGIN_INVALID", 403);
    }
    if (["GET", "HEAD"].includes(request.method) && creatorAssets.has(url.pathname)) {
      return { status: 200, webAsset: creatorAssets.get(url.pathname) };
    }
    if (!url.pathname.startsWith("/api/") || !apiOriginAllowed(request, hostHeader, hostOrigin)) {
      return failure("PROTOTYPE_HOST_ORIGIN_INVALID", 403);
    }
    if (request.method === "GET" && url.pathname === "/api/bootstrap") {
      sessionToken ??= randomBytes(32).toString("hex");
      const body = { marker: profile === "r16" ? R16_PROTOTYPE_HOST_MARKER : PROTOTYPE_HOST_MARKER,
        readiness: { model: config.modelReady, assets: config.assetsReady, godot: config.godotReady }, currentRunId,
        runs: [...runs.values()].filter((run) => run.status === "ready" || !TERMINAL.has(run.status))
          .map((run) => publicRun(run, profile)) };
      if (profile === "r16") body.qualificationProfile = "matrix-oasis.creator-solved-evidence/1";
      if (profile === "r12") {
        body.recovery = publicRecovery();
        body.worldDiscovery = publicWorldDiscovery();
      }
      return { status: 200, headers: { "set-cookie": sessionCookie(sessionToken) }, body };
    }
    if (!safeEqual(cookieValue(request.headers.cookie), sessionToken)) return failure("PROTOTYPE_HOST_SESSION_INVALID", 401);
    if (request.method === "GET" && url.pathname === "/api/runs/current") {
      const run = [...runs.values()].findLast((item) => item.resultRunId === currentRunId && item.status === "ready") ?? null;
      return { status: 200, body: { ok: true, currentRunId, run: run ? publicRun(run, profile) : null } };
    }
    if (request.method === "POST" && url.pathname === "/api/recovery/approve") {
      if (recoveryState === null) return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404);
      const parsed = await readJsonBody(request); if (!parsed.ok) return parsed;
      if (recoveryState.status !== "awaiting_approval" || recoveryState.approved) return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
      if (!exactKeys(parsed.value, ["approvalHash"]) || parsed.value.approvalHash !== recoveryState.hash) {
        return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
      }
      recoveryState.approved = true;
      recoveryState.status = "recovering";
      startBackground(finishRecovery);
      return { status: 202, body: { ok: true, recovery: publicRecovery() } };
    }
    if (request.method === "POST" && url.pathname === "/api/world-discovery/approve") {
      if (worldDiscoveryState === null) return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404);
      const parsed = await readJsonBody(request); if (!parsed.ok) return parsed;
      if (worldDiscoveryState.status !== "awaiting_approval" || worldDiscoveryState.approved) {
        return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
      }
      if (!exactKeys(parsed.value, ["approvalHash"]) || parsed.value.approvalHash !== worldDiscoveryState.hash) {
        return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
      }
      worldDiscoveryState.approved = true;
      worldDiscoveryState.status = "querying";
      startBackground(finishWorldDiscovery);
      return { status: 202, body: { ok: true, worldDiscovery: publicWorldDiscovery() } };
    }
    if (request.method === "POST" && url.pathname === "/api/world-discovery/prepare-recovery") {
      if (worldDiscoveryState === null) return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404);
      const parsed = await readJsonBody(request); if (!parsed.ok) return parsed;
      if (worldDiscoveryState.status !== "ready" || worldDiscoveryState.recovery !== null ||
          !exactKeys(parsed.value, ["worldIdSha256"]) || !worldDiscoveryState.privateWorldIds.has(parsed.value.worldIdSha256)) {
        return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
      }
      const summary = frozen({ worldIdSha256: parsed.value.worldIdSha256, maxCreates: 0, maxPolls: 0,
        maxWorldGets: 1, maxDownloads: 3, creditLimit: 0, usdLimitCents: 0 });
      worldDiscoveryState.recovery = { summary, status: "awaiting_approval", diagnostics: Object.freeze([]),
        hash: approvalHash(summary), approved: false };
      return { status: 200, body: { ok: true, worldDiscovery: publicWorldDiscovery() } };
    }
    if (request.method === "POST" && url.pathname === "/api/world-discovery/approve-recovery") {
      if (worldDiscoveryState === null || worldDiscoveryState.recovery === null) return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404);
      const parsed = await readJsonBody(request); if (!parsed.ok) return parsed;
      const recovery = worldDiscoveryState.recovery;
      if (recovery.status !== "awaiting_approval" || recovery.approved) return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
      if (!exactKeys(parsed.value, ["approvalHash"]) || parsed.value.approvalHash !== recovery.hash) {
        return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
      }
      recovery.approved = true;
      recovery.status = "recovering";
      startBackground(finishWorldRecovery);
      return { status: 202, body: { ok: true, worldDiscovery: publicWorldDiscovery() } };
    }
    const match = /^\/api\/runs\/(r10-run-[1-9][0-9]*)(?:\/(approve-model|approve-assets|launch))?$/u.exec(url.pathname);
    if (request.method === "GET" && match && !match[2]) {
      const run = runs.get(match[1]); return run ? { status: 200, body: { ok: true, run: publicRun(run, profile) } } : failure("PROTOTYPE_HOST_RUN_UNKNOWN", 404);
    }
    if (request.method === "POST" && url.pathname === "/api/runs") {
      const parsed = await readJsonBody(request); return parsed.ok ? createRun(parsed.value) : parsed;
    }
    if (request.method === "POST" && match?.[2]) {
      const run = runs.get(match[1]); if (!run) return failure("PROTOTYPE_HOST_RUN_UNKNOWN", 404);
      const parsed = await readJsonBody(request); if (!parsed.ok) return parsed;
      if (match[2] === "approve-model") return approveModel(run, parsed.value);
      if (match[2] === "approve-assets") return approveAssets(run, parsed.value);
      if (match[2] === "launch" && exactKeys(parsed.value, [])) return launch(run);
      return failure("PROTOTYPE_HOST_BODY_INVALID");
    }
    return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404);
  }

  async function handle(request, response) {
    try {
      const result = await route(request);
      if (result.webAsset) writeWebAsset(response, request.method, result.webAsset);
      else writeJson(response, result.status, result.body, result.headers);
    }
    catch { const result = failure("PROTOTYPE_HOST_INTERNAL_ERROR", 500); writeJson(response, result.status, result.body); }
  }

  return Object.freeze({
    async start() {
      if (server) throw new PrototypeHostOperationalError();
      const restored = await op.recover();
      let recoveredPartial = null;
      if (profile === "r16") {
        const safe = sanitizeR16Recovered(restored, config.model);
        for (const item of safe.runs.filter(({ cache }) => cache.cacheLevel === "qualified")) {
          const id = `r10-run-${++runCounter}`;
          runs.set(id, { id, status: "ready", cacheHit: true, diagnostics: Object.freeze([]), modelApproval: null,
            assetApproval: null, resultRunId: item.cache.qualificationRunId, prompt: null,
            promptSha256: item.promptSha256, model: item.model, artifacts: null, acquisition: null,
            qualification: { cacheLevel: "qualified", subphase: null, attempt: item.cache.summary.evidence.attempt,
              reusedQualification: true, summary: item.cache.summary }, qualificationSource: null });
        }
        currentRunId = safe.currentRunId;
        recoveredPartial = safe.runs.find(({ cache }) => cache.cacheLevel !== "qualified") ?? null;
      } else {
        const safe = sanitizeRecovered(restored?.runs);
        for (const resultRunId of safe) {
          const id = `r10-run-${++runCounter}`;
          runs.set(id, { id, status: "ready", cacheHit: true, diagnostics: Object.freeze([]), modelApproval: null,
            assetApproval: null, resultRunId, prompt: null, promptSha256: null, model: config.model,
            artifacts: null, acquisition: null, qualification: null, qualificationSource: null });
        }
        currentRunId = typeof restored?.currentRunId === "string" && safe.includes(restored.currentRunId) ? restored.currentRunId : null;
      }
      if (profile !== "r10") {
        const pending = sanitizePendingRecovered(await op.recoverPending(), config.model);
        for (const item of pending) {
          const id = `r10-run-${++runCounter}`;
          runs.set(id, { id, status: "awaiting_asset_approval", cacheHit: false, diagnostics: Object.freeze([]),
            modelApproval: null, assetApproval: { summary: item.approval, hash: approvalHash(item.approval), approved: false },
            resultRunId: null, prompt: null, promptSha256: item.promptSha256, model: item.model,
            artifacts: item.artifacts, acquisition: null,
            qualification: profile === "r16" ? qualificationState(null) : null, qualificationSource: null });
        }
        if (profile === "r16" && pending.length === 0 && recoveredPartial !== null) {
          const id = `r10-run-${++runCounter}`;
          runs.set(id, { id, status: "qualifying", cacheHit: false, diagnostics: Object.freeze([]), modelApproval: null,
            assetApproval: null, resultRunId: null, prompt: null, promptSha256: recoveredPartial.promptSha256,
            model: recoveredPartial.model, artifacts: null, acquisition: null,
            qualification: qualificationState(recoveredPartial.cache.cacheLevel),
            qualificationSource: recoveredPartial.cache });
        }
      }
      server = createServer((request, response) => { void handle(request, response); });
      await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, PROTOTYPE_HOST, resolve); });
      if (profile === "r16") {
        const pendingQualification = [...runs.values()].find((run) => run.status === "qualifying");
        if (pendingQualification) startBackground(() => finishQualification(pendingQualification, pendingQualification.qualificationSource));
      }
      return frozen({ host: PROTOTYPE_HOST, port, origin: hostOrigin });
    },
    async stop() {
      await background; try { await op.stopLaunch(); } catch { /* static shutdown */ }
      if (server) await new Promise((resolve) => server.close(resolve)); server = null;
    },
  });
}

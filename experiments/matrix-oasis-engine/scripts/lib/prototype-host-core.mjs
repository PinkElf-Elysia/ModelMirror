import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { createServer as createNodeServer } from "node:http";

export const PROTOTYPE_HOST = "127.0.0.1";
export const PROTOTYPE_HOST_PORT = 43_110;
export const PROTOTYPE_HOST_ORIGIN = `http://${PROTOTYPE_HOST}:${PROTOTYPE_HOST_PORT}`;
export const PROTOTYPE_HOST_MARKER = "MATRIX_OASIS_R10_PROTOTYPE_HOST";

const TERMINAL = new Set(["ready", "failed"]);
const STATES = new Set([
  "awaiting_model_approval", "generating", "awaiting_asset_approval", "acquiring",
  "normalizing", "assembling", "ready", "failed",
]);
const COOKIE_NAME = "matrix_oasis_r10_session";
const RUN_ID = /^r10-run-[1-9][0-9]*$/u;
const HASH = /^sha256:[0-9a-f]{64}$/u;
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

function parseOperations(value) {
  const names = ["findCache", "generate", "describeAssets", "acquire", "publish", "launch", "recover", "stopLaunch"];
  if (!exactKeys(value, names) || names.some((name) => typeof value[name] !== "function")) throw new PrototypeHostOperationalError();
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

function publicRun(run) {
  return frozen({
    id: run.id,
    status: run.status,
    cacheHit: run.cacheHit,
    diagnostics: run.diagnostics,
    modelApproval: publicApproval(run.modelApproval),
    assetApproval: publicApproval(run.assetApproval),
    resultRunId: run.resultRunId,
  });
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

function apiOriginAllowed(request) {
  if (request.headers.host !== `${PROTOTYPE_HOST}:${PROTOTYPE_HOST_PORT}`) return false;
  if (request.headers.origin === PROTOTYPE_HOST_ORIGIN) return true;
  return request.method === "GET" && request.headers.origin === undefined &&
    [undefined, "none", "same-origin"].includes(request.headers[SEC_FETCH_SITE_HEADER]);
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

export function createPrototypeHost({ configuration, operations, webAssets, createServer = createNodeServer }) {
  const config = parseConfiguration(configuration); const op = parseOperations(operations);
  const creatorAssets = parseWebAssets(webAssets);
  if (typeof createServer !== "function") throw new PrototypeHostOperationalError();
  let server = null; let sessionToken = null; let runCounter = 0; let currentRunId = null;
  let background = Promise.resolve(); let launchActive = false;
  const runs = new Map();

  const failRun = (run, diagnostics, fallback) => {
    run.status = "failed"; run.diagnostics = staticDiagnostics(diagnostics, fallback);
    run.prompt = null; run.artifacts = null; run.acquisition = null;
  };

  const finishGeneration = async (run) => {
    try {
      const generated = await op.generate({ prompt: run.prompt });
      if (!generated?.ok) return failRun(run, generated?.diagnostics, "PROTOTYPE_HOST_GENERATION_FAILED");
      const description = await op.describeAssets({ artifacts: generated.artifacts });
      if (!description?.ok || !HASH.test(description.blueprintSha256) || typeof description.environmentPrompt !== "string" ||
          !Array.isArray(description.briefs) || description.briefs.length > 2) {
        return failRun(run, description?.diagnostics, "PROTOTYPE_HOST_GENERATION_FAILED");
      }
      run.artifacts = generated.artifacts;
      const summary = {
        blueprintSha256: description.blueprintSha256,
        marble: { model: "marble-1.1", environmentPrompt: description.environmentPrompt,
          maxCreates: 1, maxPolls: 180, maxDownloads: 2, creditLimit: 1600, usdLimitCents: 150 },
        meshy: { model: "meshy-6", briefs: description.briefs.map((brief) => ({ id: brief.id, kind: brief.kind, prompt: brief.prompt })),
          maxTasks: description.briefs.length * 2, creditLimit: description.briefs.length * 30 },
      };
      run.assetApproval = { summary: frozen(summary), hash: approvalHash(summary), approved: false };
      run.status = "awaiting_asset_approval";
    } catch { failRun(run, null, "PROTOTYPE_HOST_INTERNAL_ERROR"); }
  };

  const finishAssets = async (run) => {
    try {
      const acquired = await op.acquire({ artifacts: run.artifacts, approval: run.assetApproval.summary,
        onStage(stage) { if (stage === "normalizing" && run.status === "acquiring") run.status = "normalizing"; } });
      if (!acquired?.ok) return failRun(run, acquired?.diagnostics, "PROTOTYPE_HOST_ACQUISITION_FAILED");
      run.acquisition = acquired; run.status = "assembling";
      const published = await op.publish({ prompt: run.prompt, artifacts: run.artifacts, acquisition: acquired });
      if (!published?.ok || typeof published.runId !== "string" || !/^[0-9a-f]{64}-[0-9a-f]{64}$/u.test(published.runId)) {
        return failRun(run, published?.diagnostics, "PROTOTYPE_HOST_ASSEMBLY_FAILED");
      }
      run.status = "ready"; run.resultRunId = published.runId; run.cacheHit = false; currentRunId = published.runId;
      run.prompt = null; run.artifacts = null; run.acquisition = null;
    } catch { failRun(run, null, "PROTOTYPE_HOST_INTERNAL_ERROR"); }
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
    if ([...runs.values()].some((run) => !TERMINAL.has(run.status))) return failure("PROTOTYPE_HOST_RUN_ACTIVE", 409);
    const id = `r10-run-${++runCounter}`; const promptSha256 = hash(promptBytes);
    let cached;
    try { cached = await op.findCache({ promptSha256, model: config.model }); }
    catch { return failure("PROTOTYPE_HOST_INTERNAL_ERROR", 500); }
    const run = { id, status: "awaiting_model_approval", cacheHit: false, diagnostics: Object.freeze([]),
      modelApproval: null, assetApproval: null, resultRunId: null, prompt: body.prompt, artifacts: null, acquisition: null };
    if (cached?.ok && typeof cached.runId === "string" && /^[0-9a-f]{64}-[0-9a-f]{64}$/u.test(cached.runId)) {
      run.status = "ready"; run.cacheHit = true; run.resultRunId = cached.runId; run.prompt = null; currentRunId = cached.runId;
    } else {
      const summary = { endpointHost: config.endpointHost, model: config.model, maxRequests: 3,
        maxUsdCents: 100, prompt: body.prompt, promptSha256 };
      run.modelApproval = { summary: frozen(summary), hash: approvalHash(summary), approved: false };
    }
    runs.set(id, run); return { status: 201, body: { ok: true, run: publicRun(run) } };
  }

  function approveModel(run, body) {
    if (!config.modelReady) return failure("PROTOTYPE_HOST_MODEL_CONFIG_NOT_READY", 409);
    if (run.status !== "awaiting_model_approval" || run.modelApproval?.approved) return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
    if (!exactKeys(body, ["approvalHash"]) || body.approvalHash !== run.modelApproval.hash) return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
    run.modelApproval.approved = true; run.status = "generating"; startBackground(() => finishGeneration(run));
    return { status: 202, body: { ok: true, run: publicRun(run) } };
  }

  function approveAssets(run, body) {
    if (!config.assetsReady) return failure("PROTOTYPE_HOST_ASSET_CONFIG_NOT_READY", 409);
    if (run.status !== "awaiting_asset_approval" || run.assetApproval?.approved) return failure("PROTOTYPE_HOST_APPROVAL_INVALID", 409);
    if (!exactKeys(body, ["approvalHash"]) || body.approvalHash !== run.assetApproval.hash) return failure("PROTOTYPE_HOST_APPROVAL_STALE", 409);
    run.assetApproval.approved = true; run.status = "acquiring"; startBackground(() => finishAssets(run));
    return { status: 202, body: { ok: true, run: publicRun(run) } };
  }

  async function launch(run) {
    if (!config.godotReady) return failure("PROTOTYPE_HOST_GODOT_NOT_READY", 409);
    if (run.status !== "ready" || !run.resultRunId) return failure("PROTOTYPE_HOST_RUN_NOT_READY", 409);
    if (launchActive) return failure("PROTOTYPE_HOST_GODOT_ACTIVE", 409);
    launchActive = true;
    try {
      const result = await op.launch({ runId: run.resultRunId });
      if (!result?.ok) return failure("PROTOTYPE_HOST_GODOT_FAILED", 502);
      return { status: 202, body: { ok: true, runId: run.resultRunId } };
    } catch { return failure("PROTOTYPE_HOST_GODOT_FAILED", 502); }
    finally { launchActive = false; }
  }

  async function route(request) {
    let url;
    try { url = new URL(request.url, PROTOTYPE_HOST_ORIGIN); } catch { return failure("PROTOTYPE_HOST_ROUTE_INVALID", 404); }
    if (url.origin !== PROTOTYPE_HOST_ORIGIN || url.search || url.hash) {
      return failure("PROTOTYPE_HOST_ORIGIN_INVALID", 403);
    }
    if (request.headers.host !== `${PROTOTYPE_HOST}:${PROTOTYPE_HOST_PORT}`) {
      return failure("PROTOTYPE_HOST_ORIGIN_INVALID", 403);
    }
    if (["GET", "HEAD"].includes(request.method) && creatorAssets.has(url.pathname)) {
      return { status: 200, webAsset: creatorAssets.get(url.pathname) };
    }
    if (!url.pathname.startsWith("/api/") || !apiOriginAllowed(request)) {
      return failure("PROTOTYPE_HOST_ORIGIN_INVALID", 403);
    }
    if (request.method === "GET" && url.pathname === "/api/bootstrap") {
      sessionToken ??= randomBytes(32).toString("hex");
      return { status: 200, headers: { "set-cookie": sessionCookie(sessionToken) }, body: { marker: PROTOTYPE_HOST_MARKER,
        readiness: { model: config.modelReady, assets: config.assetsReady, godot: config.godotReady }, currentRunId,
        runs: [...runs.values()].filter((run) => run.status === "ready" || !TERMINAL.has(run.status)).map(publicRun) } };
    }
    if (!safeEqual(cookieValue(request.headers.cookie), sessionToken)) return failure("PROTOTYPE_HOST_SESSION_INVALID", 401);
    if (request.method === "GET" && url.pathname === "/api/runs/current") {
      const run = [...runs.values()].findLast((item) => item.resultRunId === currentRunId && item.status === "ready") ?? null;
      return { status: 200, body: { ok: true, currentRunId, run: run ? publicRun(run) : null } };
    }
    const match = /^\/api\/runs\/(r10-run-[1-9][0-9]*)(?:\/(approve-model|approve-assets|launch))?$/u.exec(url.pathname);
    if (request.method === "GET" && match && !match[2]) {
      const run = runs.get(match[1]); return run ? { status: 200, body: { ok: true, run: publicRun(run) } } : failure("PROTOTYPE_HOST_RUN_UNKNOWN", 404);
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
      const restored = await op.recover(); const safe = sanitizeRecovered(restored?.runs);
      for (const resultRunId of safe) {
        const id = `r10-run-${++runCounter}`;
        runs.set(id, { id, status: "ready", cacheHit: true, diagnostics: Object.freeze([]), modelApproval: null,
          assetApproval: null, resultRunId, prompt: null, artifacts: null, acquisition: null });
      }
      currentRunId = typeof restored?.currentRunId === "string" && safe.includes(restored.currentRunId) ? restored.currentRunId : null;
      server = createServer((request, response) => { void handle(request, response); });
      await new Promise((resolve, reject) => { server.once("error", reject); server.listen(PROTOTYPE_HOST_PORT, PROTOTYPE_HOST, resolve); });
      return frozen({ host: PROTOTYPE_HOST, port: PROTOTYPE_HOST_PORT, origin: PROTOTYPE_HOST_ORIGIN });
    },
    async stop() {
      await background; try { await op.stopLaunch(); } catch { /* static shutdown */ }
      if (server) await new Promise((resolve) => server.close(resolve)); server = null;
    },
  });
}

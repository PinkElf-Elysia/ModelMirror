import { PrototypeEnvironmentPipelineOperationalError } from "./operational.mjs";

export const MARBLE_PROVIDER_ENDPOINT =
  "https://api.worldlabs.ai/marble/v1";
export const MARBLE_PROVIDER_MODEL = "marble-1.1";
export const MARBLE_PROVIDER_LIMITS = Object.freeze({
  timeoutMs: 120_000,
  responseBytes: 1024 * 1024,
  panoramaBytes: 64 * 1024 * 1024,
  colliderBytes: 32 * 1024 * 1024,
  spzBytes: 64 * 1024 * 1024,
  pollAttempts: 180,
  pollIntervalMs: 10_000,
  promptCharacters: 2000,
});

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const APPROVED_OFFICIAL_ASSET_HOSTS = new Set([
  "assets.worldlabs.ai",
  "cdn.marble.worldlabs.ai",
  "cdn.worldlabs.ai",
  "storage.cloud.google.com",
  "storage.googleapis.com",
]);
const REMOTE_ID = /^[A-Za-z0-9_-]{1,128}$/u;
const HOST_NAME = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/u;
const JSON_CONTENT = /^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/iu;
const providerStates = new WeakMap();

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value) || value instanceof Uint8Array) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function failure(code) {
  return deepFreeze({
    ok: false,
    diagnostics: [{ phase: "provider", severity: "error", code, path: "", message: code }],
  });
}

function captureRecord(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let prototype;
  let descriptors;
  try {
    prototype = Object.getPrototypeOf(value);
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const allowed = new Set([...required, ...optional]);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some((key) => typeof key !== "string" || !allowed.has(key)) ||
    required.some((key) => !Object.hasOwn(descriptors, key))
  ) return null;
  const output = Object.create(null);
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (!descriptor.enumerable || descriptor.get !== undefined || descriptor.set !== undefined || !Object.hasOwn(descriptor, "value")) return null;
    output[key] = descriptor.value;
  }
  return output;
}

function captureArray(value) {
  if (!Array.isArray(value)) return null;
  let descriptors;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
  const length = descriptors.length?.value;
  if (!Number.isSafeInteger(length) || length < 0 || Reflect.ownKeys(descriptors).length !== length + 1) return null;
  const output = [];
  for (let index = 0; index < length; index += 1) {
    const descriptor = descriptors[String(index)];
    if (!descriptor || !descriptor.enumerable || descriptor.get !== undefined || descriptor.set !== undefined || !Object.hasOwn(descriptor, "value")) return null;
    output.push(descriptor.value);
  }
  return output;
}

function wellFormedText(value) {
  if (typeof value !== "string") return false;
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function parseEndpoint(value) {
  if (typeof value !== "string") return null;
  let url;
  try { url = new URL(value); } catch { return null; }
  if (url.username || url.password || url.search || url.hash || url.pathname.endsWith("/")) return null;
  const official = url.href === MARBLE_PROVIDER_ENDPOINT;
  const loopback = url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname) && url.port !== "" && url.pathname === "/marble/v1";
  return official || loopback ? { url, official, loopback } : null;
}

function parseAssetHosts(value, loopbackHost) {
  const values = captureArray(value);
  if (!values || values.length < 1 || values.length > 8) return null;
  const hosts = new Set();
  for (const host of values) {
    if (typeof host !== "string" || host !== host.toLowerCase() || hosts.has(host)) return null;
    if (loopbackHost !== null) {
      if (host !== loopbackHost) return null;
    } else if (!HOST_NAME.test(host) || !APPROVED_OFFICIAL_ASSET_HOSTS.has(host)) return null;
    hosts.add(host);
  }
  return hosts;
}

function parseConfiguration(config) {
  const captured = captureRecord(config, ["endpoint", "apiKey", "allowedAssetHosts"], ["timeoutMs", "pollIntervalMs"]);
  if (!captured) throw new PrototypeEnvironmentPipelineOperationalError();
  const endpoint = parseEndpoint(captured.endpoint);
  if (!endpoint || typeof captured.apiKey !== "string" || captured.apiKey.length < 1 || captured.apiKey.length > 8192 || !/\S/u.test(captured.apiKey)) {
    throw new PrototypeEnvironmentPipelineOperationalError();
  }
  const assetHosts = parseAssetHosts(captured.allowedAssetHosts, endpoint.loopback ? endpoint.url.hostname : null);
  const timeoutMs = captured.timeoutMs ?? MARBLE_PROVIDER_LIMITS.timeoutMs;
  const pollIntervalMs = captured.pollIntervalMs ?? MARBLE_PROVIDER_LIMITS.pollIntervalMs;
  if (
    !assetHosts ||
    !Number.isSafeInteger(timeoutMs) || timeoutMs < 10 || timeoutMs > MARBLE_PROVIDER_LIMITS.timeoutMs ||
    !Number.isSafeInteger(pollIntervalMs) || pollIntervalMs < 0 || pollIntervalMs > MARBLE_PROVIDER_LIMITS.pollIntervalMs ||
    (endpoint.official && (timeoutMs !== MARBLE_PROVIDER_LIMITS.timeoutMs || pollIntervalMs !== MARBLE_PROVIDER_LIMITS.pollIntervalMs)) ||
    typeof globalThis.fetch !== "function"
  ) throw new PrototypeEnvironmentPipelineOperationalError();
  return { endpoint, apiKey: captured.apiKey, assetHosts, timeoutMs, pollIntervalMs, fetchImpl: globalThis.fetch };
}

async function readBytes(response, maximum) {
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const parsed = Number(contentLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > maximum) return { ok: false, tooLarge: true };
  }
  if (!response.body || typeof response.body.getReader !== "function") return { ok: false };
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      if (!(result.value instanceof Uint8Array)) return { ok: false };
      length += result.value.byteLength;
      if (length > maximum) {
        await reader.cancel().catch(() => {});
        return { ok: false, tooLarge: true };
      }
      chunks.push(result.value);
    }
  } catch { return { ok: false }; }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return { ok: true, bytes };
}

function timeoutFetch(state, url, init) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), state.timeoutMs);
  return state.fetchImpl(url, { ...init, redirect: "manual", signal: controller.signal })
    .then((response) => ({ ok: true, response }))
    .catch((error) => ({ ok: false, timeout: controller.signal.aborted || error?.name === "AbortError" }))
    .finally(() => clearTimeout(timer));
}

function httpFailure(status) {
  if (status >= 300 && status < 400) return failure("MARBLE_PROVIDER_REDIRECT");
  if (status === 402) return failure("MARBLE_PROVIDER_CREDIT_LIMIT");
  if (status === 429) return failure("MARBLE_PROVIDER_RATE_LIMITED");
  return failure("MARBLE_PROVIDER_HTTP_ERROR");
}

async function requestJson(state, url, init) {
  const fetched = await timeoutFetch(state, url, init);
  if (!fetched.ok) return fetched.timeout ? failure("MARBLE_PROVIDER_TIMEOUT") : failure("MARBLE_PROVIDER_NETWORK_ERROR");
  if (!fetched.response.ok) return httpFailure(fetched.response.status);
  if (!JSON_CONTENT.test(fetched.response.headers.get("content-type") ?? "")) return failure("MARBLE_PROVIDER_RESPONSE_INVALID");
  const read = await readBytes(fetched.response, MARBLE_PROVIDER_LIMITS.responseBytes);
  if (!read.ok) return read.tooLarge ? failure("MARBLE_PROVIDER_RESPONSE_TOO_LARGE") : failure("MARBLE_PROVIDER_RESPONSE_INVALID");
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(read.bytes);
    return { ok: true, value: JSON.parse(text) };
  } catch { return failure("MARBLE_PROVIDER_RESPONSE_INVALID"); }
}

function remoteId(value) {
  return typeof value === "string" && REMOTE_ID.test(value) ? value : null;
}

function operationResult(value, expectedOperationId = null) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return failure("MARBLE_PROVIDER_RESPONSE_INVALID");
  const operationId = remoteId(value.operation_id);
  if (!operationId || (expectedOperationId !== null && operationId !== expectedOperationId) || typeof value.done !== "boolean") return failure("MARBLE_PROVIDER_RESPONSE_INVALID");
  if (value.error !== null && value.error !== undefined) return deepFreeze({ ok: true, status: "failed", operationId, worldId: null });
  if (!value.done) return deepFreeze({ ok: true, status: "pending", operationId, worldId: null });
  const response = value.response && typeof value.response === "object" && !Array.isArray(value.response) ? value.response : null;
  const metadata = value.metadata && typeof value.metadata === "object" && !Array.isArray(value.metadata) ? value.metadata : null;
  const worldId = remoteId(response?.world_id ?? response?.id ?? metadata?.world_id);
  return worldId
    ? deepFreeze({ ok: true, status: "succeeded", operationId, worldId })
    : failure("MARBLE_PROVIDER_RESPONSE_INVALID");
}

function assetUrl(state, value) {
  if (typeof value !== "string") return null;
  let url;
  try { url = new URL(value); } catch { return null; }
  if (url.username || url.password || url.hash || url.pathname.length < 2) return null;
  const loopback = state.endpoint.loopback && url.protocol === "http:" && url.hostname === state.endpoint.url.hostname && url.port === state.endpoint.url.port;
  const official = !state.endpoint.loopback && url.protocol === "https:" && url.port === "" && state.assetHosts.has(url.hostname.toLowerCase());
  return loopback || official ? url : null;
}

function worldResult(state, value, expectedWorldId, includeSpatialSource) {
  const world = value?.world && typeof value.world === "object" && !Array.isArray(value.world) ? value.world : value;
  if (!world || typeof world !== "object" || Array.isArray(world)) return failure("MARBLE_PROVIDER_RESPONSE_INVALID");
  const worldId = remoteId(world.world_id ?? world.id);
  const panoramaUrl = assetUrl(state, world.assets?.imagery?.pano_url);
  const colliderUrl = assetUrl(state, world.assets?.mesh?.collider_mesh_url);
  if (worldId !== expectedWorldId || world.model !== MARBLE_PROVIDER_MODEL || !panoramaUrl || !colliderUrl) return failure("MARBLE_PROVIDER_ASSET_URL_INVALID");
  if (!includeSpatialSource) return { ok: true, panoramaUrl, colliderUrl };
  const spzUrl = assetUrl(state, world.assets?.splats?.spz_urls?.full_res);
  const metricScaleFactor = world.assets?.splats?.semantics_metadata?.metric_scale_factor;
  const groundPlaneOffset = world.assets?.splats?.semantics_metadata?.ground_plane_offset;
  if (!spzUrl || typeof metricScaleFactor !== "number" || !Number.isFinite(metricScaleFactor) || metricScaleFactor <= 0 ||
      typeof groundPlaneOffset !== "number" || !Number.isFinite(groundPlaneOffset)) {
    return failure("MARBLE_PROVIDER_SPATIAL_SOURCE_INVALID");
  }
  return { ok: true, panoramaUrl, colliderUrl, spzUrl, metricScaleFactor, groundPlaneOffset };
}

async function download(state, url, maximum) {
  const fetched = await timeoutFetch(state, url, { method: "GET", headers: { accept: "application/octet-stream,image/png,model/gltf-binary" } });
  if (!fetched.ok) return fetched.timeout ? failure("MARBLE_PROVIDER_TIMEOUT") : failure("MARBLE_PROVIDER_NETWORK_ERROR");
  if (!fetched.response.ok) return httpFailure(fetched.response.status);
  const read = await readBytes(fetched.response, maximum);
  if (!read.ok) return read.tooLarge ? failure("MARBLE_PROVIDER_DOWNLOAD_TOO_LARGE") : failure("MARBLE_PROVIDER_RESPONSE_INVALID");
  return { ok: true, bytes: read.bytes };
}

export function createMarbleWorldProvider(config) {
  const state = parseConfiguration(config);
  const provider = Object.freeze({ provider: "marble", model: MARBLE_PROVIDER_MODEL });
  providerStates.set(provider, state);
  return provider;
}

async function acquireMarble(provider, prompt, includeSpatialSource) {
  const state = providerStates.get(provider);
  if (!state || !wellFormedText(prompt) || prompt.length < 1 || prompt.length > MARBLE_PROVIDER_LIMITS.promptCharacters || !/\S/u.test(prompt)) {
    return failure("MARBLE_PROVIDER_REQUEST_INVALID");
  }
  const headers = { "WLT-Api-Key": state.apiKey, "content-type": "application/json", accept: "application/json" };
  const createUrl = new URL(`${state.endpoint.url.pathname}/worlds:generate`, state.endpoint.url);
  const created = await requestJson(state, createUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      display_name: "matrix-oasis-prototype-environment",
      model: MARBLE_PROVIDER_MODEL,
      world_prompt: { type: "text", text_prompt: prompt },
      permission: { allow_id_access: false, allowed_readers: [], allowed_writers: [], public: false },
    }),
  });
  if (!created.ok) return created;
  const creation = operationResult(created.value);
  if (!creation.ok) return creation;
  let worldId = creation.worldId;
  let polls = 0;
  if (creation.status === "failed") return failure("MARBLE_PROVIDER_GENERATION_FAILED");
  while (worldId === null && polls < MARBLE_PROVIDER_LIMITS.pollAttempts) {
    if (polls > 0 || state.pollIntervalMs > 0) await new Promise((resolve) => setTimeout(resolve, state.pollIntervalMs));
    polls += 1;
    const operationUrl = new URL(`${state.endpoint.url.pathname}/operations/${creation.operationId}`, state.endpoint.url);
    const response = await requestJson(state, operationUrl, { method: "GET", headers: { "WLT-Api-Key": state.apiKey, accept: "application/json" } });
    if (!response.ok) return response;
    const operation = operationResult(response.value, creation.operationId);
    if (!operation.ok) return operation;
    if (operation.status === "failed") return failure("MARBLE_PROVIDER_GENERATION_FAILED");
    worldId = operation.worldId;
  }
  if (worldId === null) return failure("MARBLE_PROVIDER_POLL_LIMIT");
  const worldUrl = new URL(`${state.endpoint.url.pathname}/worlds/${worldId}`, state.endpoint.url);
  const fetchedWorld = await requestJson(state, worldUrl, { method: "GET", headers: { "WLT-Api-Key": state.apiKey, accept: "application/json" } });
  if (!fetchedWorld.ok) return fetchedWorld;
  const world = worldResult(state, fetchedWorld.value, worldId, includeSpatialSource);
  if (!world.ok) return world;
  const panorama = await download(state, world.panoramaUrl, MARBLE_PROVIDER_LIMITS.panoramaBytes);
  if (!panorama.ok) return panorama;
  const collider = await download(state, world.colliderUrl, MARBLE_PROVIDER_LIMITS.colliderBytes);
  if (!collider.ok) return collider;
  const spz = includeSpatialSource
    ? await download(state, world.spzUrl, MARBLE_PROVIDER_LIMITS.spzBytes)
    : null;
  if (includeSpatialSource && !spz.ok) return spz;
  return Object.freeze({
    ok: true,
    panoramaBytes: panorama.bytes,
    colliderBytes: collider.bytes,
    ...(includeSpatialSource ? {
      spzBytes: spz.bytes,
      metricScaleFactor: world.metricScaleFactor,
      groundPlaneOffset: world.groundPlaneOffset,
    } : {}),
    counts: Object.freeze({ creates: 1, polls, worldGets: 1, downloads: includeSpatialSource ? 3 : 2 }),
  });
}

export async function acquireMarbleEnvironment(provider, prompt) {
  return await acquireMarble(provider, prompt, false);
}

export async function acquireMarbleEnvironmentWithSpatialSource(provider, prompt) {
  return await acquireMarble(provider, prompt, true);
}

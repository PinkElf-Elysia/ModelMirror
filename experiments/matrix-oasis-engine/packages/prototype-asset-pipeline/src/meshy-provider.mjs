export const MESHY_PROVIDER_ENDPOINT =
  "https://api.meshy.ai/openapi/v2/text-to-3d";
export const MESHY_PROVIDER_MODEL = "meshy-6";
export const MESHY_PROVIDER_LIMITS = Object.freeze({
  timeoutMs: 120_000,
  responseBytes: 1024 * 1024,
  rawGlbBytes: 128 * 1024 * 1024,
  promptCharacters: 600,
  taskIdCharacters: 128,
});
import { PrototypeAssetPipelineOperationalError } from "./operational.mjs";

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const TASK_ID = /^[A-Za-z0-9_-]{1,128}$/;
const JSON_CONTENT = /^application\/(?:[a-z0-9.+-]+\+)?json(?:\s*;|$)/i;

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    deepFreeze(child);
  }
  return Object.freeze(value);
}

function failure(code) {
  return deepFreeze({
    ok: false,
    diagnostics: [
      { phase: "provider", severity: "error", code, path: "", message: code },
    ],
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
    throw new PrototypeAssetPipelineOperationalError();
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const allowed = new Set([...required, ...optional]);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some((key) => typeof key !== "string" || !allowed.has(key)) ||
    required.some((key) => !Object.hasOwn(descriptors, key))
  ) {
    return null;
  }
  const output = Object.create(null);
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (
      !descriptor.enumerable ||
      descriptor.get !== undefined ||
      descriptor.set !== undefined ||
      !Object.hasOwn(descriptor, "value")
    ) {
      return null;
    }
    output[key] = descriptor.value;
  }
  return output;
}

function captureKnownRecord(value, required, optional = []) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let prototype;
  let descriptors;
  try {
    prototype = Object.getPrototypeOf(value);
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    throw new PrototypeAssetPipelineOperationalError();
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.some((key) => typeof key !== "string") ||
    required.some((key) => !Object.hasOwn(descriptors, key))
  ) {
    return null;
  }
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (
      !descriptor.enumerable ||
      descriptor.get !== undefined ||
      descriptor.set !== undefined ||
      !Object.hasOwn(descriptor, "value")
    ) {
      return null;
    }
  }
  const output = Object.create(null);
  for (const key of [...required, ...optional]) {
    if (Object.hasOwn(descriptors, key)) output[key] = descriptors[key].value;
  }
  return output;
}

function isWellFormedText(value) {
  if (typeof value !== "string") return false;
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function parseEndpoint(value) {
  if (typeof value !== "string") return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/openapi/v2/text-to-3d"
  ) {
    return null;
  }
  const official = url.href === MESHY_PROVIDER_ENDPOINT;
  const loopback =
    url.protocol === "http:" &&
    LOOPBACK_HOSTS.has(url.hostname) &&
    url.port !== "";
  return official || loopback ? { url, official, loopback } : null;
}

function parseDownloadUrl(value, allowLoopback) {
  if (typeof value !== "string") return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.username || url.password || url.hash) return null;
  if (
    url.protocol === "https:" &&
    url.hostname === "assets.meshy.ai" &&
    url.port === "" &&
    url.pathname.length > 1
  ) {
    return url;
  }
  if (
    allowLoopback &&
    url.protocol === "http:" &&
    LOOPBACK_HOSTS.has(url.hostname) &&
    url.port !== "" &&
    url.pathname.length > 1
  ) {
    return url;
  }
  return null;
}

function parseConfiguration(config) {
  const captured = captureRecord(config, ["endpoint", "apiKey"], ["timeoutMs"]);
  if (!captured) throw new PrototypeAssetPipelineOperationalError();
  const endpoint = parseEndpoint(captured.endpoint);
  if (
    !endpoint ||
    typeof captured.apiKey !== "string" ||
    captured.apiKey.length < 1 ||
    captured.apiKey.length > 8192 ||
    !/\S/.test(captured.apiKey)
  ) {
    throw new PrototypeAssetPipelineOperationalError();
  }
  const timeoutMs = captured.timeoutMs ?? MESHY_PROVIDER_LIMITS.timeoutMs;
  if (
    !Number.isSafeInteger(timeoutMs) ||
    timeoutMs < 10 ||
    timeoutMs > MESHY_PROVIDER_LIMITS.timeoutMs ||
    (endpoint.official && timeoutMs !== MESHY_PROVIDER_LIMITS.timeoutMs)
  ) {
    throw new PrototypeAssetPipelineOperationalError();
  }
  const fetchImpl = globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    throw new PrototypeAssetPipelineOperationalError();
  }
  return { endpoint, apiKey: captured.apiKey, timeoutMs, fetchImpl };
}

async function readBytes(response, maximum) {
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const parsed = Number(contentLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > maximum) {
      return { ok: false, tooLarge: true };
    }
  }
  if (!response.body || typeof response.body.getReader !== "function") {
    return { ok: false, invalid: true };
  }
  const reader = response.body.getReader();
  const chunks = [];
  let length = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      if (!(result.value instanceof Uint8Array)) {
        return { ok: false, invalid: true };
      }
      length += result.value.byteLength;
      if (length > maximum) {
        await reader.cancel().catch(() => {});
        return { ok: false, tooLarge: true };
      }
      chunks.push(result.value);
    }
  } catch {
    return { ok: false, invalid: true };
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { ok: true, bytes };
}

function httpFailure(status) {
  if (status >= 300 && status < 400) return failure("MESHY_PROVIDER_REDIRECT");
  if (status === 429) return failure("MESHY_PROVIDER_RATE_LIMITED");
  return failure("MESHY_PROVIDER_HTTP_ERROR");
}

function timeoutFetch(fetchImpl, url, init, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetchImpl(url, { ...init, signal: controller.signal })
    .then((response) => ({ ok: true, response }))
    .catch((error) => ({
      ok: false,
      timeout: controller.signal.aborted || error?.name === "AbortError",
    }))
    .finally(() => clearTimeout(timer));
}

async function requestJson(configuration, url, init) {
  const fetched = await timeoutFetch(
    configuration.fetchImpl,
    url,
    { ...init, redirect: "manual" },
    configuration.timeoutMs,
  );
  if (!fetched.ok) {
    return fetched.timeout
      ? failure("MESHY_PROVIDER_TIMEOUT")
      : failure("MESHY_PROVIDER_NETWORK_ERROR");
  }
  if (!fetched.response.ok) return httpFailure(fetched.response.status);
  const contentType = fetched.response.headers.get("content-type") ?? "";
  if (!JSON_CONTENT.test(contentType)) {
    return failure("MESHY_PROVIDER_RESPONSE_INVALID");
  }
  const read = await readBytes(fetched.response, MESHY_PROVIDER_LIMITS.responseBytes);
  if (!read.ok) {
    return read.tooLarge
      ? failure("MESHY_PROVIDER_RESPONSE_TOO_LARGE")
      : failure("MESHY_PROVIDER_RESPONSE_INVALID");
  }
  let text;
  let value;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(read.bytes);
    value = JSON.parse(text);
  } catch {
    return failure("MESHY_PROVIDER_RESPONSE_INVALID");
  }
  return { ok: true, value };
}

function creationResult(value) {
  const captured = captureRecord(value, ["result"], []);
  if (!captured || typeof captured.result !== "string" || !TASK_ID.test(captured.result)) {
    return failure("MESHY_PROVIDER_RESPONSE_INVALID");
  }
  return deepFreeze({ ok: true, taskId: captured.result });
}

function statusResult(value, allowLoopback) {
  const captured = captureKnownRecord(
    value,
    ["status", "progress"],
    [
      "id", "type", "model_urls", "thumbnail_url", "prompt", "started_at",
      "created_at", "finished_at", "texture_urls", "preceding_tasks",
      "task_error", "consumed_credits", "expires_at", "video_url",
      "alpha_thumbnail_url",
    ],
  );
  if (
    !captured ||
    typeof captured.status !== "string" ||
    !["PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED", "CANCELED"].includes(captured.status) ||
    !Number.isSafeInteger(captured.progress) ||
    captured.progress < 0 ||
    captured.progress > 100
  ) {
    return failure("MESHY_PROVIDER_RESPONSE_INVALID");
  }
  let consumedCredits = null;
  if (captured.consumed_credits !== undefined) {
    if (!Number.isSafeInteger(captured.consumed_credits) || captured.consumed_credits < 0) {
      return failure("MESHY_PROVIDER_RESPONSE_INVALID");
    }
    consumedCredits = captured.consumed_credits;
  }
  let status = "pending";
  let glbUrl = null;
  if (captured.status === "FAILED" || captured.status === "CANCELED") {
    status = "failed";
  } else if (captured.status === "SUCCEEDED") {
    const modelUrls = captureKnownRecord(captured.model_urls, ["glb"]);
    const parsedUrl = modelUrls
      ? parseDownloadUrl(modelUrls.glb, allowLoopback)
      : null;
    if (!parsedUrl || captured.progress !== 100) {
      return failure("MESHY_PROVIDER_RESPONSE_INVALID");
    }
    status = "succeeded";
    glbUrl = parsedUrl.href;
  }
  return deepFreeze({
    ok: true,
    task: { status, progress: captured.progress, glbUrl, consumedCredits },
  });
}

function requestInput(value, key, maximum) {
  const captured = captureRecord(value, [key], []);
  if (!captured) return null;
  const text = captured[key];
  return typeof text === "string" &&
    text.length >= 1 &&
    text.length <= maximum &&
    /\S/.test(text) &&
    isWellFormedText(text)
    ? text
    : null;
}

export function createMeshyTextTo3DProvider(config) {
  const configuration = parseConfiguration(config);
  const authorization = `Bearer ${configuration.apiKey}`;
  const headers = Object.freeze({
    authorization,
    "content-type": "application/json",
    accept: "application/json",
  });

  async function create(body) {
    const response = await requestJson(configuration, configuration.endpoint.url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    return response.ok ? creationResult(response.value) : response;
  }

  return Object.freeze({
    provider: "meshy",
    model: MESHY_PROVIDER_MODEL,
    async createPreview(request) {
      const prompt = requestInput(
        request,
        "prompt",
        MESHY_PROVIDER_LIMITS.promptCharacters,
      );
      if (prompt === null) return failure("MESHY_PROVIDER_REQUEST_INVALID");
      return create({
        mode: "preview",
        prompt,
        model_type: "standard",
        ai_model: MESHY_PROVIDER_MODEL,
        should_remesh: true,
        topology: "triangle",
        target_polycount: 50_000,
        moderation: true,
        target_formats: ["glb"],
      });
    },
    async createRefine(request) {
      const previewTaskId = requestInput(
        request,
        "previewTaskId",
        MESHY_PROVIDER_LIMITS.taskIdCharacters,
      );
      if (previewTaskId === null || !TASK_ID.test(previewTaskId)) {
        return failure("MESHY_PROVIDER_REQUEST_INVALID");
      }
      return create({
        mode: "refine",
        preview_task_id: previewTaskId,
        ai_model: MESHY_PROVIDER_MODEL,
        texture_resolution: "2k",
        enable_pbr: false,
        remove_lighting: true,
        moderation: true,
        target_formats: ["glb"],
      });
    },
    async getTask(request) {
      const taskId = requestInput(
        request,
        "taskId",
        MESHY_PROVIDER_LIMITS.taskIdCharacters,
      );
      if (taskId === null || !TASK_ID.test(taskId)) {
        return failure("MESHY_PROVIDER_REQUEST_INVALID");
      }
      const url = new URL(`${configuration.endpoint.url.pathname}/${taskId}`, configuration.endpoint.url);
      const response = await requestJson(configuration, url, {
        method: "GET",
        headers: { authorization, accept: "application/json" },
      });
      return response.ok
        ? statusResult(response.value, configuration.endpoint.loopback)
        : response;
    },
    async downloadGlb(request) {
      const captured = captureRecord(request, ["url"], []);
      const url = captured
        ? parseDownloadUrl(captured.url, configuration.endpoint.loopback)
        : null;
      if (!url) return failure("MESHY_PROVIDER_DOWNLOAD_URL_INVALID");
      const fetched = await timeoutFetch(
        configuration.fetchImpl,
        url,
        { method: "GET", redirect: "manual" },
        configuration.timeoutMs,
      );
      if (!fetched.ok) {
        return fetched.timeout
          ? failure("MESHY_PROVIDER_TIMEOUT")
          : failure("MESHY_PROVIDER_NETWORK_ERROR");
      }
      if (!fetched.response.ok) return httpFailure(fetched.response.status);
      const read = await readBytes(
        fetched.response,
        MESHY_PROVIDER_LIMITS.rawGlbBytes,
      );
      if (!read.ok) {
        return read.tooLarge
          ? failure("MESHY_PROVIDER_DOWNLOAD_TOO_LARGE")
          : failure("MESHY_PROVIDER_RESPONSE_INVALID");
      }
      return Object.freeze({ ok: true, bytes: read.bytes });
    },
  });
}

import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import readline from "node:readline";
import { chromium } from "playwright";

import {
  NetworkPolicyError,
  domainMatches,
  resolvePublicHost,
  validatePublicUrl,
  validatePublicWebSocketUrl,
} from "./policy.mjs";

const SOCKET_PATH = process.env.BROWSER_SOCKET_PATH || "/run/modelmirror-browser/browser.sock";
const DATA_ROOT = process.env.BROWSER_DATA_ROOT || "/browser-data";
const SANDBOX_ROOT = process.env.SANDBOX_WORKSPACE_ROOT || "/sandbox-workspaces";
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_TEXT_CHARS = 24000;
const SESSION_IDLE_MS = 30 * 60 * 1000;
const SENSITIVE_FIELD = /(password|passwd|passcode|card|credit|cvv|cvc|otp|one.?time|verification)/i;

const sessions = new Map();
const operationLocks = new Map();
let browser;
let proxyServer;
let proxyPort;

class BrowserSidecarError extends Error {
  constructor(message, code = "browser_operation_failed") {
    super(message);
    this.code = code;
  }
}

function safeId(value, name = "id") {
  const clean = String(value || "").trim();
  if (!/^[A-Za-z0-9_.:-]{1,240}$/.test(clean)) {
    throw new BrowserSidecarError(`${name} is invalid.`, "browser_invalid_argument");
  }
  return clean;
}

function boundedInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Math.max(minimum, Math.min(Number.isFinite(parsed) ? parsed : fallback, maximum));
}

function safeFilename(value) {
  const clean = path.basename(String(value || "download.bin")).replace(/[^A-Za-z0-9._ -]/g, "_");
  return clean.slice(0, 180) || "download.bin";
}

async function inspectDownload(target, filename) {
  const extension = path.extname(filename).toLowerCase();
  const dangerous = new Set([".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".msi", ".scr"]);
  if (dangerous.has(extension)) {
    throw new BrowserSidecarError("Executable downloads are not allowed.", "browser_download_type_denied");
  }
  const file = await fs.open(target, "r");
  const buffer = Buffer.alloc(512);
  const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
  await file.close();
  const head = buffer.subarray(0, bytesRead);
  if (
    (head.length >= 2 && head[0] === 0x4d && head[1] === 0x5a) ||
    (head.length >= 4 && head[0] === 0x7f && head.subarray(1, 4).toString() === "ELF")
  ) {
    throw new BrowserSidecarError("Executable downloads are not allowed.", "browser_download_type_denied");
  }
  const signatures = {
    ".pdf": head.subarray(0, 5).toString() === "%PDF-",
    ".png": head.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])),
    ".jpg": head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff,
    ".jpeg": head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff,
    ".webp": head.subarray(0, 4).toString() === "RIFF" && head.subarray(8, 12).toString() === "WEBP",
    ".zip": head.subarray(0, 2).toString() === "PK",
    ".docx": head.subarray(0, 2).toString() === "PK",
    ".xlsx": head.subarray(0, 2).toString() === "PK",
    ".pptx": head.subarray(0, 2).toString() === "PK",
  };
  if (extension in signatures && !signatures[extension]) {
    throw new BrowserSidecarError("Downloaded file content does not match its filename.", "browser_download_mime_mismatch");
  }
  const contentTypes = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".zip": "application/zip",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".json": "application/json",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
  };
  if ([".json", ".txt", ".md", ".csv"].includes(extension) && head.includes(0)) {
    throw new BrowserSidecarError("Downloaded text file contains binary content.", "browser_download_mime_mismatch");
  }
  return contentTypes[extension] || "application/octet-stream";
}

function safePageUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    if (!["http:", "https:"].includes(parsed.protocol)) return "";
    return `${parsed.origin}${parsed.pathname || "/"}`.slice(0, 2000);
  } catch {
    return "";
  }
}

function sessionDir(sessionId) {
  return path.join(DATA_ROOT, safeId(sessionId, "session_id"));
}

function safeSandboxPath(relativePath) {
  const clean = String(relativePath || "").replaceAll("\\", "/").replace(/^\/+/, "");
  if (!clean || clean.split("/").some((item) => !item || item === "." || item === "..")) {
    throw new BrowserSidecarError("Unsafe upload path.", "browser_upload_denied");
  }
  if (!(clean.startsWith("inputs/") || clean.startsWith("artifacts/"))) {
    throw new BrowserSidecarError("Uploads must come from inputs/ or artifacts/.", "browser_upload_denied");
  }
  return clean;
}

function safeDataPath(relativePath) {
  const clean = String(relativePath || "").replaceAll("\\", "/").replace(/^\/+/, "");
  if (!clean || clean.split("/").some((item) => !item || item === "." || item === "..")) {
    throw new BrowserSidecarError("Unsafe browser artifact path.", "browser_upload_denied");
  }
  return clean;
}

async function startProxy() {
  const server = http.createServer(async (request, response) => {
    try {
      const target = await validatePublicUrl(request.url || "");
      const upstream = http.request(
        {
          hostname: target.addresses[0],
          port: target.port,
          method: request.method,
          path: `${target.parsed.pathname}${target.parsed.search}`,
          headers: { ...request.headers, host: target.parsed.host },
          timeout: 30000,
        },
        (upstreamResponse) => {
          response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
          upstreamResponse.pipe(response);
        },
      );
      upstream.on("error", () => response.destroy());
      request.pipe(upstream);
    } catch (error) {
      response.writeHead(403, { "content-type": "text/plain; charset=utf-8" });
      response.end(error instanceof Error ? error.message : "Network request denied.");
    }
  });

  server.on("connect", async (request, clientSocket, head) => {
    try {
      const authority = String(request.url || "");
      const separator = authority.lastIndexOf(":");
      if (separator <= 0) throw new NetworkPolicyError("Invalid CONNECT target.");
      const hostname = authority.slice(0, separator).replace(/^\[|\]$/g, "");
      const port = boundedInt(authority.slice(separator + 1), 443, 1, 65535);
      const addresses = await resolvePublicHost(hostname);
      const upstream = net.connect({ host: addresses[0], port }, () => {
        clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (head.length) upstream.write(head);
        upstream.pipe(clientSocket);
        clientSocket.pipe(upstream);
      });
      upstream.on("error", () => clientSocket.destroy());
    } catch {
      clientSocket.end("HTTP/1.1 403 Forbidden\r\n\r\n");
    }
  });

  server.on("upgrade", (_request, socket) => {
    socket.end("HTTP/1.1 403 Forbidden\r\n\r\n");
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Unable to start browser egress proxy.");
  proxyServer = server;
  proxyPort = address.port;
}

async function launchBrowser() {
  await startProxy();
  browser = await chromium.launch({
    headless: true,
    proxy: { server: `http://127.0.0.1:${proxyPort}` },
    args: [
      "--disable-dev-shm-usage",
      "--disable-quic",
      "--disable-features=WebRtcHideLocalIpsWithMdns,WebRtcAllowInputVolumeAdjustment,WebRtcRemoteEventLog",
      "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    ],
  });
}

function trackPage(state, page) {
  page.setDefaultTimeout(state.timeoutMs);
  if (!state.pages.includes(page)) state.pages.push(page);
  page.on("dialog", (dialog) => void dialog.dismiss().catch(() => undefined));
  page.on("close", () => {
    state.pages = state.pages.filter((item) => item !== page && !item.isClosed());
    if (state.activePage === page) state.activePage = state.pages.at(-1);
    state.refs.clear();
    if (!state.closing && state.pages.length === 0) {
      void state.context.newPage().catch(() => undefined);
    }
  });
  page.on("crash", () => void page.close().catch(() => undefined));
}

async function ensureSession(payload) {
  const sessionId = safeId(payload.session_id, "session_id");
  const existing = sessions.get(sessionId);
  if (existing) {
    existing.maxPages = boundedInt(payload.max_pages, existing.maxPages, 1, 3);
    existing.maxActions = boundedInt(payload.max_actions, existing.maxActions, 1, 100);
    existing.timeoutMs = boundedInt(
      payload.navigation_timeout_seconds,
      existing.timeoutMs / 1000,
      5,
      120,
    ) * 1000;
    existing.allowedDomains = Array.isArray(payload.allowed_domains)
      ? payload.allowed_domains.slice(0, 100)
      : existing.allowedDomains;
    existing.blockedDomains = Array.isArray(payload.blocked_domains)
      ? payload.blocked_domains.slice(0, 100)
      : existing.blockedDomains;
    existing.grantedDomains = Array.isArray(payload.granted_domains)
      ? payload.granted_domains.slice(0, 100)
      : existing.grantedDomains;
    existing.activePage.setDefaultTimeout(existing.timeoutMs);
    existing.lastActiveAt = Date.now();
    return existing;
  }
  const root = sessionDir(sessionId);
  await fs.mkdir(path.join(root, "screenshots"), { recursive: true });
  await fs.mkdir(path.join(root, "downloads"), { recursive: true });
  const storagePath = path.join(root, "storage-state.json");
  let storageState;
  try {
    await fs.access(storagePath);
    storageState = storagePath;
  } catch {
    storageState = undefined;
  }
  const context = await browser.newContext({
    serviceWorkers: "block",
    acceptDownloads: true,
    storageState,
    viewport: { width: 1440, height: 960 },
    ignoreHTTPSErrors: false,
  });
  let state;
  await context.route("**/*", async (route) => {
    try {
      const target = await validatePublicUrl(route.request().url());
      const request = route.request();
      if (
        state &&
        state.blockedDomains.some((rule) => domainMatches(target.hostname, rule))
      ) {
        throw new NetworkPolicyError("Domain is blocked by the Agent configuration.");
      }
      if (
        state &&
        state.allowedDomains.length &&
        !state.allowedDomains.some((rule) => domainMatches(target.hostname, rule))
      ) {
        throw new NetworkPolicyError("Domain is outside the Agent allowlist.");
      }
      if (
        state &&
        request.isNavigationRequest() &&
        request.frame() === state.activePage.mainFrame() &&
        !state.grantedDomains.some((rule) => domainMatches(target.hostname, rule))
      ) {
        throw new NetworkPolicyError("Top-level domain has not been approved.");
      }
      await route.continue();
    } catch {
      await route.abort("blockedbyclient");
    }
  });
  await context.routeWebSocket("**/*", async (webSocketRoute) => {
    try {
      if (!state) {
        throw new NetworkPolicyError("Browser session policy is not ready.");
      }
      const target = await validatePublicWebSocketUrl(webSocketRoute.url());
      assertConfiguredHostname(state, target.hostname);
      webSocketRoute.connectToServer();
    } catch {
      await webSocketRoute.close({ code: 1008, reason: "Network policy denied" });
    }
  });
  const page = await context.newPage();
  state = {
    sessionId,
    root,
    storagePath,
    context,
    pages: [],
    activePage: page,
    refs: new Map(),
    refRevision: 0,
    maxPages: boundedInt(payload.max_pages, 3, 1, 3),
    maxActions: boundedInt(payload.max_actions, 100, 1, 100),
    actionCount: 0,
    timeoutMs: boundedInt(payload.navigation_timeout_seconds, 30, 5, 120) * 1000,
    allowedDomains: Array.isArray(payload.allowed_domains) ? payload.allowed_domains.slice(0, 100) : [],
    blockedDomains: Array.isArray(payload.blocked_domains) ? payload.blocked_domains.slice(0, 100) : [],
    grantedDomains: Array.isArray(payload.granted_domains) ? payload.granted_domains.slice(0, 100) : [],
    lastActiveAt: Date.now(),
    closing: false,
  };
  trackPage(state, page);
  context.on("page", async (newPage) => {
    state.pages = state.pages.filter((item) => !item.isClosed());
    trackPage(state, newPage);
    if (state.pages.length > state.maxPages) {
      await newPage.close().catch(() => undefined);
      state.pages = state.pages.filter((item) => !item.isClosed());
      return;
    }
    state.activePage = newPage;
    state.refs.clear();
  });
  sessions.set(sessionId, state);
  const restoreUrl = String(payload.restore_url || "").trim();
  if (restoreUrl) {
    try {
      const target = await assertConfiguredDomain(state, restoreUrl);
      if (state.grantedDomains.some((rule) => domainMatches(target.hostname, rule))) {
        await page.goto(target.parsed.toString(), {
          waitUntil: "domcontentloaded",
          timeout: state.timeoutMs,
        });
      }
    } catch {
      // Storage state is still useful when the previous page cannot be restored.
    }
  }
  return state;
}

async function persistSession(state) {
  await state.context.storageState({ path: state.storagePath });
}

function assertActionBudget(state) {
  state.lastActiveAt = Date.now();
  state.actionCount += 1;
  if (state.actionCount > state.maxActions) {
    throw new BrowserSidecarError("Browser action limit reached.", "browser_action_limit");
  }
}

async function assertConfiguredDomain(state, rawUrl) {
  const target = await validatePublicUrl(rawUrl);
  assertConfiguredHostname(state, target.hostname);
  return target;
}

function assertConfiguredHostname(state, hostname) {
  if (state.blockedDomains.some((rule) => domainMatches(hostname, rule))) {
    throw new BrowserSidecarError("Domain is blocked by the Agent configuration.", "browser_domain_blocked");
  }
  if (state.allowedDomains.length && !state.allowedDomains.some((rule) => domainMatches(hostname, rule))) {
    throw new BrowserSidecarError("Domain is outside the Agent allowlist.", "browser_domain_not_allowed");
  }
}

function pageSummary(page) {
  const url = safePageUrl(page.url());
  return { url, domain: url ? new URL(url).hostname : "", title: "" };
}

async function withTitle(page) {
  const summary = pageSummary(page);
  try {
    summary.title = String(await page.title()).slice(0, 500);
  } catch {
    summary.title = "";
  }
  return summary;
}

async function buildSnapshot(state) {
  state.refs.clear();
  state.refRevision += 1;
  const page = state.activePage;
  const aria = String(await page.locator("body").ariaSnapshot({ timeout: state.timeoutMs })).slice(0, MAX_TEXT_CHARS);
  const candidates = page.locator("a,button,input,textarea,select,[role]");
  const count = Math.min(await candidates.count(), 200);
  const items = [];
  for (let index = 0; index < count; index += 1) {
    const locator = candidates.nth(index);
    if (!(await locator.isVisible().catch(() => false))) continue;
    const ref = `r${state.refRevision}_${items.length + 1}`;
    const role = String(await locator.getAttribute("role").catch(() => "") || "").slice(0, 80);
    const tag = String(await locator.getAttribute("type").catch(() => "") || "element");
    const name = String(
      (await locator.getAttribute("aria-label").catch(() => "")) ||
      (await locator.getAttribute("name").catch(() => "")) ||
      (await locator.innerText().catch(() => "")) ||
      (await locator.getAttribute("placeholder").catch(() => "")) ||
      "",
    ).trim().replace(/\s+/g, " ").slice(0, 200);
    state.refs.set(ref, locator);
    items.push({ ref, role: role || tag, name });
  }
  return { ...(await withTitle(page)), aria, refs: items };
}

function refLocator(state, ref) {
  const locator = state.refs.get(String(ref || ""));
  if (!locator) throw new BrowserSidecarError("Browser ref is stale; take a new snapshot.", "browser_stale_ref");
  return locator;
}

async function assertNonSensitive(locator) {
  const values = await Promise.all([
    locator.getAttribute("type").catch(() => ""),
    locator.getAttribute("name").catch(() => ""),
    locator.getAttribute("id").catch(() => ""),
    locator.getAttribute("aria-label").catch(() => ""),
    locator.getAttribute("placeholder").catch(() => ""),
    locator.getAttribute("autocomplete").catch(() => ""),
  ]);
  if (values.some((value) => SENSITIVE_FIELD.test(String(value || "")))) {
    throw new BrowserSidecarError("Sensitive credential, payment, and verification fields cannot be automated.", "browser_sensitive_field_denied");
  }
}

async function assertClickDestination(state, locator) {
  const raw = String(
    (await locator.getAttribute("href").catch(() => "")) ||
    (await locator.getAttribute("formaction").catch(() => "")) ||
    "",
  ).trim();
  if (!raw) return;
  let targetUrl;
  try {
    targetUrl = new URL(raw, state.activePage.url()).toString();
  } catch {
    return;
  }
  const target = await assertConfiguredDomain(state, targetUrl);
  if (!state.grantedDomains.some((rule) => domainMatches(target.hostname, rule))) {
    throw new BrowserSidecarError(
      `Navigation to ${target.hostname} requires browser_navigate domain approval first.`,
      "browser_domain_approval_required",
    );
  }
}

async function dispatch(payload) {
  const action = String(payload.action || "");
  if (action === "health") return { ok: true, service: "browser", chromium: true, policy: "public_only" };
  const state = await ensureSession(payload);
  if (action === "ensure_session") return { ok: true, session: await withTitle(state.activePage) };
  if (action === "close_session") {
    state.closing = true;
    await persistSession(state);
    await state.context.close();
    sessions.delete(state.sessionId);
    return { ok: true, status: "closed" };
  }
  assertActionBudget(state);
  const page = state.activePage;
  if (action === "navigate") {
    const target = await assertConfiguredDomain(state, payload.url);
    await page.goto(target.parsed.toString(), { waitUntil: "domcontentloaded", timeout: state.timeoutMs });
    await persistSession(state);
    state.refs.clear();
    return { ok: true, page: await withTitle(page) };
  }
  if (action === "snapshot") return { ok: true, snapshot: await buildSnapshot(state) };
  if (action === "read") {
    const text = String(await page.locator("body").innerText({ timeout: state.timeoutMs })).slice(0, MAX_TEXT_CHARS);
    return { ok: true, page: await withTitle(page), text, truncated: text.length >= MAX_TEXT_CHARS };
  }
  if (action === "click") {
    const locator = refLocator(state, payload.ref);
    await assertClickDestination(state, locator);
    await locator.click({ timeout: state.timeoutMs });
  }
  else if (action === "fill") {
    const locator = refLocator(state, payload.ref);
    await assertNonSensitive(locator);
    await locator.fill(String(payload.value || "").slice(0, 20000), { timeout: state.timeoutMs });
  } else if (action === "select") {
    await refLocator(state, payload.ref).selectOption(String(payload.value || "").slice(0, 500));
  } else if (action === "press") {
    const key = String(payload.key || "").slice(0, 80);
    if (!/^[A-Za-z0-9+_-]{1,80}$/.test(key)) throw new BrowserSidecarError("Unsupported key.", "browser_invalid_argument");
    await refLocator(state, payload.ref).press(key);
  } else if (action === "hover") await refLocator(state, payload.ref).hover();
  else if (action === "scroll") {
    const deltaY = boundedInt(payload.delta_y, 600, -5000, 5000);
    await page.mouse.wheel(0, deltaY);
  } else if (action === "wait") {
    await page.waitForTimeout(boundedInt(payload.milliseconds, 1000, 50, 10000));
  } else if (action === "screenshot") {
    const artifactId = safeId(payload.artifact_id || `shot_${crypto.randomUUID()}`, "artifact_id");
    const relativePath = `${state.sessionId}/screenshots/${artifactId}.png`;
    const target = path.join(DATA_ROOT, relativePath);
    await page.screenshot({ path: target, fullPage: Boolean(payload.full_page) });
    const info = await fs.stat(target);
    const limit = boundedInt(payload.download_limit_bytes, 50 * 1024 * 1024, 1, 50 * 1024 * 1024);
    if (info.size > limit) {
      await fs.rm(target, { force: true });
      throw new BrowserSidecarError("Screenshot exceeds the configured size limit.", "browser_artifact_too_large");
    }
    return { ok: true, artifact: { artifact_id: artifactId, relative_path: relativePath, filename: `${artifactId}.png`, size_bytes: info.size, content_type: "image/png" }, page: await withTitle(page) };
  } else if (action === "upload_file") {
    let source;
    let base;
    if (payload.browser_artifact_relative_path) {
      const relative = safeDataPath(payload.browser_artifact_relative_path);
      source = path.resolve(DATA_ROOT, relative);
      base = path.resolve(DATA_ROOT);
    } else {
      const workspaceId = safeId(payload.workspace_id, "workspace_id");
      const relative = safeSandboxPath(payload.relative_path);
      source = path.resolve(SANDBOX_ROOT, workspaceId, relative);
      base = path.resolve(SANDBOX_ROOT, workspaceId);
    }
    const [realSource, realBase] = await Promise.all([fs.realpath(source), fs.realpath(base)]);
    if (!realSource.startsWith(`${realBase}${path.sep}`)) throw new BrowserSidecarError("Unsafe upload path.", "browser_upload_denied");
    const stat = await fs.lstat(source);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new BrowserSidecarError("Upload source must be a regular file.", "browser_upload_denied");
    const locator = refLocator(state, payload.ref);
    await assertNonSensitive(locator);
    await locator.setInputFiles(source);
  } else if (action === "download") {
    const artifactId = safeId(payload.artifact_id || `download_${crypto.randomUUID()}`, "artifact_id");
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: state.timeoutMs }),
      refLocator(state, payload.ref).click({ timeout: state.timeoutMs }),
    ]);
    const filename = safeFilename(download.suggestedFilename());
    const relativePath = `${state.sessionId}/downloads/${artifactId}-${filename}`;
    const target = path.join(DATA_ROOT, relativePath);
    await download.saveAs(target);
    const info = await fs.stat(target);
    const limit = boundedInt(payload.download_limit_bytes, 50 * 1024 * 1024, 1, 50 * 1024 * 1024);
    if (info.size > limit) {
      await fs.rm(target, { force: true });
      throw new BrowserSidecarError("Download exceeds the configured size limit.", "browser_artifact_too_large");
    }
    let contentType;
    try {
      contentType = await inspectDownload(target, filename);
    } catch (error) {
      await fs.rm(target, { force: true });
      throw error;
    }
    return { ok: true, artifact: { artifact_id: artifactId, relative_path: relativePath, filename, size_bytes: info.size, content_type: contentType }, page: await withTitle(page) };
  } else if (action === "close_page") {
    if (state.pages.length === 1) {
      await page.setContent("<!doctype html><title>Blank</title>");
    } else {
      await page.close();
      state.pages = state.pages.filter((item) => item !== page);
      state.activePage = state.pages.at(-1);
    }
  } else {
    throw new BrowserSidecarError("Unknown browser action.", "browser_unknown_action");
  }
  await persistSession(state);
  return { ok: true, page: await withTitle(state.activePage) };
}

async function dispatchIdempotent(payload) {
  const durableActions = new Set([
    "click",
    "fill",
    "select",
    "press",
    "screenshot",
    "upload_file",
    "download",
    "close_page",
  ]);
  const action = String(payload.action || "");
  if (!durableActions.has(action) || !payload.operation_id) return dispatch(payload);
  const state = await ensureSession(payload);
  const operationId = safeId(payload.operation_id, "operation_id");
  const directory = path.join(state.root, "operations");
  const cachePath = path.join(directory, `${operationId.replace(/[:]/g, "_")}.json`);
  const loadCached = async () => {
    try {
      return JSON.parse(await fs.readFile(cachePath, "utf8"));
    } catch {
      return null;
    }
  };
  const cached = await loadCached();
  if (cached?.state === "completed" && cached.response) {
    return { ...cached.response, replayed: true };
  }
  if (cached?.state === "running" || cached?.state === "failed") {
    throw new BrowserSidecarError(
      "A previous browser write has an uncertain outcome and will not be replayed.",
      "browser_operation_uncertain",
    );
  }
  // Read cache files from the first Browser runtime revision.
  if (cached?.ok) return { ...cached, replayed: true };
  const active = operationLocks.get(operationId);
  if (active) return { ...(await active), replayed: true };
  const run = (async () => {
    await fs.mkdir(directory, { recursive: true });
    const persist = async (value) => {
      const temporary = `${cachePath}.tmp-${process.pid}-${crypto.randomUUID()}`;
      await fs.writeFile(temporary, JSON.stringify(value), { encoding: "utf8", mode: 0o600 });
      await fs.rename(temporary, cachePath);
    };
    await persist({ state: "running", started_at: Date.now() });
    try {
      const response = await dispatch(payload);
      await persist({ state: "completed", response, completed_at: Date.now() });
      return response;
    } catch (error) {
      await persist({
        state: "failed",
        code: error?.code || "browser_operation_failed",
        error: String(error?.message || error).slice(0, 1000),
        completed_at: Date.now(),
      }).catch(() => undefined);
      throw error;
    }
  })();
  operationLocks.set(operationId, run);
  try {
    return await run;
  } finally {
    operationLocks.delete(operationId);
  }
}

async function handleConnection(socket) {
  let received = 0;
  const lines = readline.createInterface({ input: socket, crlfDelay: Infinity });
  for await (const line of lines) {
    received += Buffer.byteLength(line);
    if (received > MAX_REQUEST_BYTES) {
      socket.end(JSON.stringify({ ok: false, code: "request_too_large", error: "Browser request is too large." }) + "\n");
      return;
    }
    try {
      const response = await dispatchIdempotent(JSON.parse(line));
      socket.write(JSON.stringify(response) + "\n");
    } catch (error) {
      socket.write(JSON.stringify({
        ok: false,
        code: error?.code || "browser_operation_failed",
        error: String(error?.message || error).slice(0, 1000),
      }) + "\n");
    }
  }
}

async function shutdown() {
  for (const state of sessions.values()) {
    state.closing = true;
    await persistSession(state).catch(() => undefined);
    await state.context.close().catch(() => undefined);
  }
  await browser?.close().catch(() => undefined);
  proxyServer?.close();
  process.exit(0);
}

await fs.mkdir(path.dirname(SOCKET_PATH), { recursive: true });
await fs.mkdir(DATA_ROOT, { recursive: true });
await fs.rm(SOCKET_PATH, { force: true });
await launchBrowser();
const udsServer = net.createServer((socket) => void handleConnection(socket));
udsServer.listen(SOCKET_PATH, async () => {
  await fs.chmod(SOCKET_PATH, 0o666);
});
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);

setInterval(() => {
  const cutoff = Date.now() - SESSION_IDLE_MS;
  for (const state of sessions.values()) {
    if (state.lastActiveAt >= cutoff) continue;
    void (async () => {
      state.closing = true;
      await persistSession(state).catch(() => undefined);
      await state.context.close().catch(() => undefined);
      sessions.delete(state.sessionId);
    })();
  }
}, 60_000).unref();

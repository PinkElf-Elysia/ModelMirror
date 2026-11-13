import { AsyncLocalStorage } from "node:async_hooks";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Readable } from "node:stream";
import { createHash } from "node:crypto";
import { canonical } from "../catalog.mjs";
import { validate } from "./public/config.mjs";
import {
  assembleMods,
  modCatalog,
  resolveMods,
} from "../mod-panel/catalog.mjs";
import { project } from "./node-job.mjs";
const context = new AsyncLocalStorage();
const digest = (v) => createHash("sha256").update(canonical(v)).digest("hex");
const publicFiles = new Set([
  "editor.html",
  "editor.css",
  "editor.mjs",
  "config.mjs",
  "entry.mjs",
  "appearance.mjs",
  "engine.mjs",
  "worker.mjs",
  "replacement.mjs",
  "worldbook-ui.mjs",
  "worldbook-ui.css",
]);
const allowedPath =
  /^\/rpg-app\/(?:(earth|god|cultivation\/chat|ancient\/chat|lord-of-mysteries\/chat|blue-fish\/chat|douluo\/chat|dragon-raja\/chat|jinyong\/chat|ai-girls\/chat|medieval\/chat|elderland\/chat|harry-potter\/chat|xingjian)\/api\/sessions\/([a-zA-Z0-9-]{1,80})\/send|cultivation\/api\/(?:sessions\/([a-f0-9-]{36})\/turns|summary-v2\/(earth|god|cultivation)\/sessions\/([a-f0-9-]{36})))$/;
function reply(res, status, msg) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify({ error: msg }));
}
export function installCustomConfig(server, origins, directory) {
  const listeners = server.listeners("request");
  for (const cb of listeners) server.removeListener("request", cb);
  const pass = (req, res) => {
    for (const cb of listeners) cb.call(server, req, res);
  };
  server.on("request", async (req, res) => {
    const path = req.url?.split("?")[0] || "";
    if (req.method === "GET" && path.startsWith("/rpg-app/custom-config/")) {
      const name = path.slice("/rpg-app/custom-config/".length);
      if (!publicFiles.has(name)) return reply(res, 404, "NOT_FOUND");
      try {
        const bytes = await readFile(
          new URL("public/" + name, import.meta.url),
        );
        res.writeHead(200, {
          "Content-Type": name.endsWith(".html")
            ? "text/html; charset=utf-8"
            : name.endsWith(".css")
              ? "text/css"
              : "text/javascript; charset=utf-8",
          "Cache-Control": "no-cache",
          "X-Content-Type-Options": "nosniff",
          "Content-Security-Policy":
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; object-src 'none'; frame-ancestors 'self'",
        });
        res.end(bytes);
      } catch {
        reply(res, 500, "CONFIG_ASSET_UNAVAILABLE");
      }
      return;
    }
    if (req.method === "GET" && path === "/rpg-app/mod-panel/catalog") {
      try {
        const items = await modCatalog();
        res.writeHead(200, {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-cache",
        });
        res.end(JSON.stringify({ items }));
      } catch {
        reply(res, 503, "MOD_CATALOG_UNAVAILABLE");
      }
      return;
    }
    const modRequest = req.headers["x-rpg-mod-panel"] !== undefined;
    const configRequest = req.headers["x-rpg-custom-config"] !== undefined;
    if (!modRequest && !configRequest) return pass(req, res);
    const match = allowedPath.exec(path);
    if (
      !match ||
      (modRequest && !match?.[2]) ||
      req.method !== "POST" ||
      req.headers["content-type"] !== "application/json" ||
      req.headers["sec-fetch-site"] === "cross-site" ||
      !origins.includes(req.headers.origin)
    )
      return reply(res, 403, "CONFIG_REQUEST_DENIED");
    try {
      const chunks = [];
      let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 16 * 1024 * 1024) return reply(res, 413, "CONFIG_TOO_LARGE");
        chunks.push(chunk);
      }
      const envelope = JSON.parse(Buffer.concat(chunks).toString());
      const { request } = envelope;
      const profiles = configRequest ? envelope.profiles : [];
      const mods = modRequest
        ? await resolveMods(match[1].split("/")[0], envelope.modIds)
        : [];
      if (
        modRequest &&
        Object.keys(envelope).sort().join(",") !==
          (configRequest ? "modIds,profiles,request" : "modIds,request")
      )
        throw Error("MOD_REQUEST_INVALID");
      if (
        !Array.isArray(profiles) ||
        (configRequest && profiles.length < 1) ||
        profiles.length > 2 ||
        !request ||
        typeof request !== "object"
      )
        throw Error("配置请求无效");
      for (const profile of profiles) {
        validate(profile);
        if (profile.desktop || profile.mobile || profile.css)
          throw Error("显示设置不可发送");
      }
      if (match[5] && !["send", "refresh"].includes(request.action))
        return reply(res, 400, "CONFIG_ACTION_INVALID");
      const sessionId = match[2] || match[3] || match[5],
        key = request.operationId ?? request.requestId;
      const ids = new Set(),
        memories = new Set();
      if (match[2] && typeof key === "string") {
        ids.add(key);
        ids.add(digest({ id: sessionId, key, purpose: "story" }));
        ids.add(digest({ id: sessionId, key, stageId: "story" }));
      }
      if (match[3])
        ids.add(sessionId + "-" + (request.revision + 1) + "-story");
      if (match[5])
        ids.add(
          sessionId +
            "-" +
            request.revision +
            "-" +
            (request.action === "refresh" ? "refresh" : "story"),
        );
      for (const stageId of ["memory", "summary-before", "summary-after"])
        memories.add(digest({ id: sessionId, key, stageId }));
      memories.add(digest({ id: sessionId, key, purpose: "summary" }));
      if (match[3])
        memories.add(sessionId + "-" + (request.revision + 1) + "-memory");
      if (match[5])
        for (const stage of ["small", "memory"])
          memories.add(sessionId + "-" + request.revision + "-" + stage);
      const body = Buffer.from(JSON.stringify(request));
      const forwarded = Readable.from([body]);
      Object.assign(forwarded, {
        method: req.method,
        url: req.url,
        headers: { ...req.headers, "content-length": String(body.length) },
        socket: req.socket,
      });
      context.run(
        {
          directory,
          key,
          sessionId,
          ids,
          memories,
          profiles,
          mods,
          seed: parseInt(
            digest([sessionId, key ?? request.revision]).slice(0, 8),
            16,
          ),
        },
        () => pass(forwarded, res),
      );
    } catch {
      if (!res.headersSent) reply(res, 400, "CONFIG_REQUEST_INVALID");
    }
  });
}
export async function configFetch(url, options = {}, fetcher = fetch) {
  const ctx = context.getStore();
  if (
    !ctx ||
    options.method !== "POST" ||
    !String(url).split("?")[0].endsWith("/chat/completions")
  )
    return fetcher(url, options);
  const body = JSON.parse(options.body);
  if (body.sessionId && ctx.sessionId !== body.sessionId)
    return fetcher(url, options);
  const memory =
    body.sessionId &&
    ctx.memories.has(body.requestId) &&
    ctx.profiles.some((c) => c.rules.some((r) => r.enabled && !r.renderOnly));
  if (body.sessionId && !ctx.ids.has(body.requestId) && !memory)
    return fetcher(url, options);
  const projected = !ctx.profiles.length
    ? body.messages
    : await project({
        kind: memory ? "memory-source" : "story",
        configs: ctx.profiles,
        seed: ctx.seed,
        messages: body.messages,
      });
  const messages =
    !memory && ctx.mods.length ? assembleMods(projected, ctx.mods) : projected;
  // The card's original request hash and the effective provider input are recorded
  // separately by the authenticated bridge. Auxiliary requests retain their own policy.
  const adapted = body.sessionId
    ? { ...body, customStoryMessages: messages }
    : { ...body, messages };
  if (!body.sessionId && ctx.directory) {
    const folder = join(ctx.directory, "user-custom-config", "evidence");
    await mkdir(folder, { recursive: true, mode: 0o700 });
    const record = {
      sessionId: ctx.sessionId,
      operationId: ctx.key,
      sourceMessages: digest(body.messages),
      effectiveMessages: digest(messages),
    };
    await writeFile(
      join(folder, digest([ctx.sessionId, ctx.key]) + ".json"),
      JSON.stringify(record),
      { flag: "wx", mode: 0o600 },
    );
  }
  const headers = body.sessionId
    ? { ...options.headers, "X-Rpg-Effective-Messages": "1" }
    : options.headers;
  const response = await fetcher(url, {
    ...options,
    headers,
    body: JSON.stringify(adapted),
  });
  if (body.sessionId && response.ok) {
    const { receipt } = await response.clone().json();
    if (receipt?.effectiveRequestHash !== digest({ ...body, messages }))
      throw Error("CONFIG_RECEIPT_UNVERIFIED");
  }
  return response;
}

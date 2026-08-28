import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

import { collectProxyResponseHeaders } from "./server-headers.mjs";

const port = Number(process.env.PORT || 80);
const apiTarget = process.env.API_TARGET || "http://server:8000";
const distDir = path.resolve("dist");

function normalizePublicManagementUrl(value) {
  const configured = String(value || "").trim().replace(/\/+$/, "");
  if (!configured) return "";
  try {
    const url = new URL(configured);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      return "";
    }
    return url.toString();
  } catch {
    return "";
  }
}

const runtimeConfig = JSON.stringify({
  newApiWebUrl: normalizePublicManagementUrl(process.env.NEWAPI_WEB_URL),
});

const contentTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "application/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".ico", "image/x-icon"],
]);

function safeFilePath(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const normalized = path.normalize(decoded).replace(/^(\.\.[/\\])+/, "");
  return path.join(distDir, normalized);
}

function copyProxyHeaders(headers) {
  const result = {};
  for (const [key, value] of Object.entries(headers)) {
    if (!value) continue;
    const lowered = key.toLowerCase();
    if (
      ["host", "connection", "content-length", "transfer-encoding"].includes(
        lowered,
      )
    ) {
      continue;
    }
    result[key] = value;
  }
  return result;
}

async function proxyApi(req, res) {
  const target = new URL(req.url || "/", apiTarget);
  const response = await fetch(target, {
    method: req.method,
    headers: copyProxyHeaders(req.headers),
    body: req.method === "GET" || req.method === "HEAD" ? undefined : req,
    duplex: "half",
  });

  res.writeHead(response.status, collectProxyResponseHeaders(response.headers));

  if (!response.body) {
    res.end();
    return;
  }

  for await (const chunk of response.body) {
    res.write(chunk);
  }
  res.end();
}

async function serveStatic(req, res) {
  const requestPath = new URL(req.url || "/", "http://localhost").pathname;
  const requestedPath = safeFilePath(req.url || "/");
  const filePath =
    existsSync(requestedPath) && statSync(requestedPath).isFile()
      ? requestedPath
      : path.join(distDir, "index.html");

  const extension = path.extname(filePath);
  const headers = {
    "Content-Type": contentTypes.get(extension) || "application/octet-stream",
  };
  if (requestPath === "/forms" || requestPath.startsWith("/forms/")) {
    Object.assign(headers, {
      "Cache-Control": "no-store",
      "Content-Security-Policy": [
        "default-src 'self'",
        "base-uri 'none'",
        "connect-src 'self'",
        "font-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "img-src 'self' data:",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
      ].join("; "),
      "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=()",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "X-Robots-Tag": "noindex, nofollow",
    });
  }
  res.writeHead(200, headers);

  createReadStream(filePath).pipe(res);
}

function serveRuntimeConfig(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD" });
    res.end();
    return;
  }
  res.writeHead(200, {
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
  });
  res.end(req.method === "HEAD" ? undefined : runtimeConfig);
}

createServer(async (req, res) => {
  try {
    const requestPath = new URL(req.url || "/", "http://localhost").pathname;
    if (requestPath === "/runtime-config.json") {
      serveRuntimeConfig(req, res);
      return;
    }
    if ((req.url || "").startsWith("/api/")) {
      await proxyApi(req, res);
      return;
    }

    await serveStatic(req, res);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Server error";
    const canWriteErrorBody = !res.headersSent;
    if (canWriteErrorBody) {
      res.writeHead(500, { "Content-Type": "application/json; charset=utf-8" });
    }
    if (!res.writableEnded) {
      res.end(canWriteErrorBody ? JSON.stringify({ error: message }) : undefined);
    }
  }
}).listen(port, "0.0.0.0", async () => {
  const index = await readFile(path.join(distDir, "index.html"), "utf-8");
  if (!index.includes("模镜")) {
    console.warn("ModelMirror title not found in static bundle.");
  }
  console.log(`ModelMirror client listening on ${port}`);
});

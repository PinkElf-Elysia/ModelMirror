import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

const port = Number(process.env.PORT || 80);
const apiTarget = process.env.API_TARGET || "http://server:8000";
const distDir = path.resolve("dist");

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

  const headers = {};
  response.headers.forEach((value, key) => {
    if (!["content-encoding", "transfer-encoding", "connection"].includes(key)) {
      headers[key] = value;
    }
  });

  res.writeHead(response.status, headers);

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
  const requestedPath = safeFilePath(req.url || "/");
  const filePath =
    existsSync(requestedPath) && statSync(requestedPath).isFile()
      ? requestedPath
      : path.join(distDir, "index.html");

  const extension = path.extname(filePath);
  res.writeHead(200, {
    "Content-Type": contentTypes.get(extension) || "application/octet-stream",
  });

  createReadStream(filePath).pipe(res);
}

createServer(async (req, res) => {
  try {
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

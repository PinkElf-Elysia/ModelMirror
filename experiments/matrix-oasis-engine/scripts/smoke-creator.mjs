import assert from "node:assert/strict";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { preview } from "vite";

const LOOPBACK_HOST = "127.0.0.1";
const STABLE_MARKERS = Object.freeze([
  "MATRIX_OASIS_R0_ISOLATED_SHELL",
  "MATRIX_OASIS_R2_REFERENCE_SIMULATOR",
]);
const MAX_WAIT_MS = 10_000;

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const creatorRoot = path.join(moduleRoot, "apps", "creator-web");

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, LOOPBACK_HOST, () => {
      const address = server.address();
      assert(address && typeof address === "object", "Unable to resolve smoke port");
      const { port } = address;
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

async function closePreview(previewServer) {
  await new Promise((resolve, reject) => {
    previewServer.httpServer.close((error) => (error ? reject(error) : resolve()));
  });
}

async function readUntilReady(url) {
  const deadline = Date.now() + MAX_WAIT_MS;
  let lastError;

  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2_000) });
      const html = await response.text();
      return { response, html };
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }

  throw new Error(`Creator preview was not ready within ${MAX_WAIT_MS}ms`, {
    cause: lastError,
  });
}

const port = await reservePort();
const url = `http://${LOOPBACK_HOST}:${port}/`;
let previewServer;

try {
  previewServer = await preview({
    root: creatorRoot,
    base: "./",
    configFile: false,
    preview: {
      host: LOOPBACK_HOST,
      port,
      strictPort: true,
      open: false,
    },
  });

  const { response, html } = await readUntilReady(url);
  assert.equal(response.status, 200, `Expected HTTP 200 from ${url}`);
  for (const marker of STABLE_MARKERS) {
    assert.match(html, new RegExp(marker), `Stable marker is missing: ${marker}`);
  }
  console.log(
    `CREATOR_SMOKE_OK status=${response.status} markers=${STABLE_MARKERS.join(",")}`,
  );
} finally {
  if (previewServer) {
    await closePreview(previewServer);
  }
}

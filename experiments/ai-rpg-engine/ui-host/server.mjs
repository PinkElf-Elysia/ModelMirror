import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const COMMANDS = new Set(['draft.save', 'draft.read', 'draft.export', 'import.validate', 'journey.create', 'journey.read', 'journey.generate', 'journey.cancel', 'journey.regenerate', 'journey.delete-latest', 'journey.records']);
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' };
const HEADERS = {
  'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Cross-Origin-Resource-Policy': 'same-origin',
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Cache-Control': 'no-store',
};
const defaultDist = fileURLToPath(new URL('../ui/dist/', import.meta.url));
const plain = value => value && typeof value === 'object' && !Array.isArray(value);
function error(status, code) { return Object.assign(new Error(code), { status, code }); }

// Preload a bounded, immutable build snapshot. Request paths never become file paths.
async function loadAssets(dist) {
  const root = await fs.realpath(dist), assets = new Map();
  let total = 0;
  async function visit(relative) {
    const absolute = path.join(root, relative), stat = await fs.lstat(absolute);
    if (stat.isSymbolicLink()) throw error(500, 'BUILD_SYMLINK_REJECTED');
    const resolved = await fs.realpath(absolute);
    if (resolved !== root && !resolved.startsWith(root + path.sep)) throw error(500, 'BUILD_PATH_REJECTED');
    if (stat.isDirectory()) {
      for (const entry of await fs.readdir(absolute)) await visit(path.join(relative, entry));
    } else if (stat.isFile()) {
      const type = TYPES[path.extname(relative)];
      if (!type || stat.size > 16 * 1024 * 1024) throw error(500, 'BUILD_ASSET_REJECTED');
      const bytes = await fs.readFile(absolute);
      total += bytes.length;
      if (total > 32 * 1024 * 1024 || assets.size >= 256) throw error(500, 'BUILD_SIZE_REJECTED');
      assets.set('/' + relative.split(path.sep).join('/'), { bytes, type });
    } else throw error(500, 'BUILD_ASSET_REJECTED');
  }
  await visit('index.html');
  try { await visit('assets'); } catch (cause) { if (cause.code !== 'ENOENT') throw cause; }
  assets.set('/', assets.get('/index.html'));
  return assets;
}
function body(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0, settled = false;
    req.on('data', chunk => {
      size += chunk.length;
      if (size > maxBytes) {
        if (!settled) { settled = true; reject(error(413, 'REQUEST_TOO_LARGE')); }
        return;
      }
      if (!settled) chunks.push(chunk);
    });
    req.on('end', () => { if (!settled) { settled = true; resolve(Buffer.concat(chunks).toString('utf8')); } });
    req.on('error', () => { if (!settled) { settled = true; reject(error(400, 'REQUEST_INTERRUPTED')); } });
    req.on('aborted', () => { if (!settled) { settled = true; reject(error(400, 'REQUEST_INTERRUPTED')); } });
  });
}
function safeJson(text) {
  try {
    return JSON.parse(text, (key, value) => {
      if (['__proto__', 'prototype', 'constructor'].includes(key)) throw Error();
      return value;
    });
  } catch { throw error(400, 'JSON_INVALID'); }
}
export async function startUiHost({ port = 18405, dist = defaultDist, service, maxBytes = 262144 } = {}) {
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw error(400, 'PORT_INVALID');
  if (!Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > 1048576) throw error(400, 'LIMIT_INVALID');
  const assets = await loadAssets(dist);
  let origin;
  const server = http.createServer({ maxHeaderSize: 8192, requestTimeout: 15000, headersTimeout: 10000, keepAliveTimeout: 1000 }, async (req, res) => {
    const send = (status, value) => {
      if (res.destroyed || res.writableEnded) return;
      res.writeHead(status, { ...HEADERS, 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(value));
    };
    try {
      if (req.headers.host !== new URL(origin).host) throw error(403, 'HOST_REJECTED');
      if (req.headers.origin && req.headers.origin !== origin) throw error(403, 'ORIGIN_REJECTED');
      if (req.headers['sec-fetch-site'] && !['same-origin', 'none'].includes(req.headers['sec-fetch-site'])) throw error(403, 'SITE_REJECTED');
      const route = req.url ?? '';
      if (!route.startsWith('/') || /[%\\?#\x00-\x20]/u.test(route) || route.split('/').includes('..') || route.includes('//')) throw error(400, 'PATH_REJECTED');
      if (!['GET', 'POST'].includes(req.method)) throw error(405, 'METHOD_REJECTED');
      if (route === '/api/bootstrap' || route === '/api/command') {
        if (req.headers['x-rpg-client'] !== '1') throw error(403, 'CLIENT_HEADER_REQUIRED');
        if (route === '/api/bootstrap' && req.method === 'GET') {
          if (!service?.bootstrap) throw error(503, 'SERVICE_NOT_READY');
          return send(200, await service.bootstrap());
        }
        if (route !== '/api/command' || req.method !== 'POST') throw error(405, 'METHOD_REJECTED');
        if (req.headers.origin !== origin) throw error(403, 'ORIGIN_REQUIRED');
        if (req.headers['content-type'] !== 'application/json') throw error(415, 'CONTENT_TYPE_REJECTED');
        if (req.headers['content-encoding']) throw error(415, 'CONTENT_ENCODING_REJECTED');
        const length = req.headers['content-length'];
        if (length && (!/^\d+$/u.test(length) || Number(length) > maxBytes)) throw error(413, 'REQUEST_TOO_LARGE');
        const input = safeJson(await body(req, maxBytes));
        if (!plain(input) || Object.keys(input).some(key => !['command', 'payload'].includes(key)) || !COMMANDS.has(input.command) || !plain(input.payload)) throw error(400, 'COMMAND_INVALID');
        if (!service?.command) throw error(503, 'SERVICE_NOT_READY');
        return send(200, await service.command(input.command, input.payload));
      }
      if (req.method !== 'GET') throw error(405, 'METHOD_REJECTED');
      const asset = assets.get(route);
      if (!asset) throw error(404, 'NOT_FOUND');
      res.writeHead(200, { ...HEADERS, 'Content-Type': asset.type, 'Content-Length': asset.bytes.length });
      res.end(asset.bytes);
    } catch (cause) {
      // Only explicitly classified public codes may cross this boundary.
      const status = Number.isInteger(cause?.status) && cause.status >= 400 && cause.status < 500 ? cause.status : 503;
      const code = typeof cause?.code === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/u.test(cause.code) && cause.status ? cause.code : 'HOST_UNAVAILABLE';
      send(status, { error: code });
    }
  });
  server.maxConnections = 32;
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => { server.removeListener('error', reject); resolve(); });
  });
  origin = 'http://127.0.0.1:' + server.address().port;
  return Object.freeze({
    origin,
    close: () => new Promise((resolve, reject) => { server.close(cause => cause ? reject(cause) : resolve()); server.closeAllConnections(); }),
  });
}

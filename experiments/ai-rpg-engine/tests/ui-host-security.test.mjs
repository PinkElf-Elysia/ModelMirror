import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startUiHost } from '../ui-host/server.mjs';

const root = fileURLToPath(new URL('../.rpg04-work/', import.meta.url));
async function harness(t, service = { bootstrap: async () => ({ journeys: [], drafts: [], capabilities: [] }), command: async (command, payload) => ({ command, payload }) }, maxBytes = 1024) {
  await fs.mkdir(root, { recursive: true });
  const dist = await fs.mkdtemp(path.join(root, 'rpg05-host-test-'));
  await fs.writeFile(path.join(dist, 'index.html'), '<main>本地页面</main>');
  const host = await startUiHost({ port: 0, dist, service, maxBytes });
  t.after(async () => { await host.close(); const resolved = await fs.realpath(dist); const parent = await fs.realpath(root); if (!resolved.startsWith(parent + path.sep) || !path.basename(resolved).startsWith('rpg05-host-test-')) throw Error('CLEANUP_SCOPE_REJECTED'); await fs.rm(resolved, { recursive: true }); });
  const request = (url, options = {}) => fetch(host.origin + url, options);
  const post = (payload, extra = {}) => request('/api/command', { method: 'POST', headers: { origin: host.origin, 'content-type': 'application/json', 'x-rpg-client': '1', ...extra }, body: JSON.stringify(payload) });
  return { ...host, request, post };
}
test('built page and bootstrap are same-origin; responses carry restrictive headers', async t => {
  const h = await harness(t);
  const page = await h.request('/');
  assert.equal(page.status, 200);
  assert.match(await page.text(), /本地页面/u);
  assert.match(page.headers.get('content-security-policy'), /frame-ancestors 'none'/u);
  assert.match(page.headers.get('content-security-policy'), /connect-src 'self'/u);
  assert.equal(page.headers.get('access-control-allow-origin'), null);
  assert.equal(page.headers.get('cache-control'), 'no-store');
  const state = await h.request('/api/bootstrap', { headers: { 'x-rpg-client': '1' } });
  assert.deepEqual(await state.json(), { journeys: [], drafts: [], capabilities: [] });
});
test('reject hostile origin, missing origin, DNS rebinding host and cross-site fetches', async t => {
  const h = await harness(t);
  for (const headers of [{ origin: 'https://elsewhere.example' }, { host: 'elsewhere.example' }, { 'sec-fetch-site': 'cross-site' }]) {
    const status = await new Promise((resolve, reject) => {
      const req = http.get(h.origin, { headers }, res => { res.resume(); resolve(res.statusCode); });
      req.on('error', reject);
    });
    assert.equal(status, 403, JSON.stringify(headers));
  }
  assert.equal((await h.post({ command: 'draft.save', payload: {} }, { origin: '' })).status, 403);
  assert.equal((await h.request('/api/bootstrap')).status, 403);
});
test('only declared commands and JSON envelopes are admitted', async t => {
  const h = await harness(t);
  for (const payload of [
    { command: 'shell', payload: {} }, { command: 'draft.save', payload: [], path: '../secret' },
    { command: 'draft.save', payload: {} , modelAddress: 'https://elsewhere.example' },
    JSON.parse('{"command":"draft.save","payload":{"__proto__":{"admin":true}}}'),
  ]) assert.equal((await h.post(payload)).status, 400);
  assert.equal((await h.post({ command: 'draft.save', payload: {} }, { 'content-type': 'text/plain' })).status, 415);
  assert.equal((await h.request('/', { method: 'OPTIONS' })).status, 405);
  assert.equal((await h.post({ command: 'draft.save', payload: { name: '测试' } })).status, 200);
});
test('encoded, backslash and traversal paths cannot reach files or source', async t => {
  const h = await harness(t);
  for (const raw of ['/../package.json', '/%2e%2e/package.json', '/..%5cpackage.json', '/..\\package.json', '//outside', '/?path=secret', '/api/command?x=1']) {
    const status = await new Promise((resolve, reject) => {
      const req = http.get(h.origin, { path: raw }, res => { res.resume(); resolve(res.statusCode); });
      req.on('error', reject);
    });
    assert.equal(status, 400, raw);
  }
  for (const raw of ['/package.json', '/src/main.tsx', '/.env', '/runtime/core.mjs']) assert.equal((await h.request(raw)).status, 404);
});
test('oversized bodies rejected; internal errors never expose host text', async t => {
  const h = await harness(t, { command: async () => { throw Error('private host system prompt and paths'); } });
  assert.equal((await h.post({ command: 'draft.save', payload: { text: 'x'.repeat(2048) } })).status, 413);
  const error = await h.post({ command: 'draft.save', payload: {} });
  assert.equal(error.status, 503);
  assert.deepEqual(await error.json(), { error: 'HOST_UNAVAILABLE' });
});
test('missing service fails closed without a model adapter', async t => {
  const h = await harness(t, {});
  assert.equal((await h.request('/api/bootstrap', { headers: { 'x-rpg-client': '1' } })).status, 503);
  assert.equal((await h.post({ command: 'journey.generate', payload: {} })).status, 503);
});

test('chunked bodies cannot bypass the request limit', async t => {
  const h = await harness(t);
  const status = await new Promise((resolve, reject) => {
    const req = http.request(h.origin + '/api/command', { method: 'POST', headers: { origin: h.origin, 'content-type': 'application/json', 'x-rpg-client': '1', 'transfer-encoding': 'chunked' } }, res => { res.resume(); resolve(res.statusCode); });
    req.on('error', reject);
    req.write('{"command":"draft.save","payload":{"text":"');
    req.write('x'.repeat(2048));
    req.end('"}}');
  });
  assert.equal(status, 413);
});

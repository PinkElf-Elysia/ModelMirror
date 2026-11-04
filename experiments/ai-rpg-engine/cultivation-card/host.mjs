import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const root = new URL('./', import.meta.url);
const digest = text => createHash('sha256').update(text).digest('hex');

export async function assembleOpening(archive) {
  if (typeof archive !== 'string' || !archive.trim() || archive.length > 64000) {
    throw Object.assign(new Error('INVALID_ARCHIVE'), { status: 400 });
  }
  const content = await readFile(new URL('author-system.txt', root), 'utf8');
  return [{ role: 'system', content }, { role: 'user', content: archive }];
}

export async function cultivationHttp({ route, method, data, res, send, runtime=null }) {
  if (route.startsWith('api/summary-v2/')) {
    const {summaryV2Http}=await import('../studio/auto-summary-v2.mjs');
    return summaryV2Http({route,method,data,send});
  }
  if (method === 'GET' && route === 'api/status') return send(200, runtime ? await runtime.status() : {enabled:false,remaining:0});
  if (runtime && method === 'POST' && route === 'api/sessions') return send(201,await runtime.create(data));
  const session = route.match(/^api\/sessions\/([a-f0-9-]{36})(\/turns)?$/);
  if (runtime && session && method === 'GET' && !session[2]) return send(200,await runtime.view(session[1]));
  if (runtime && session && method === 'POST' && session[2]) return send(200,await runtime.turn(session[1],data));
  if (method === 'POST' && route === 'api/prepare') {
    const messages = await assembleOpening(data?.archive);
    return send(200, { prepared: true, systemHash: digest(messages[0].content), archiveHash: digest(messages[1].content) });
  }
  if (method !== 'GET' || route !== '') return send(404, {error:'NOT_FOUND'});
  const html = await bundledOpening();
  res.setHeader('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'; frame-ancestors 'self'");
  res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
  res.end(html);
}

// The opaque iframe needs self-contained art to work without cross-site requests.
let bundled;
async function bundledOpening() {
  if (bundled) return bundled;
  const html = await readFile(new URL('opening.html', root), 'utf8');
  const names = [...new Set(html.match(/assets\/[a-f0-9]{24}\.jpg/g))];
  const art = new Map(await Promise.all(names.map(async name => {
    const bytes = await readFile(new URL(name, root));
    return [name, 'data:image/jpeg;base64,' + bytes.toString('base64')];
  })));
  bundled = html.replace(/assets\/[a-f0-9]{24}\.jpg/g, name => art.get(name));
  return bundled;
}

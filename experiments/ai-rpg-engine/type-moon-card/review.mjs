import {readFile} from 'node:fs/promises';
import {assembleTurn, catalogEntries, selectEntries, servantBody, systemPrompt, timelines, worldbookEntries} from './assembly.mjs';

const prefix = '/type-moon-review';
const base = systemPrompt();
const servantLabel = c => 'No.' + c.no + ' ' + c.className + ' ' + c.name;
const defaults = {polar: 'FGO终章·南极'};
const catalog = [
  ...worldbookEntries.map(e => ({
    title: e.displayName,
    anchor: 'P' + e.start + (e.end === e.start ? '' : '–' + e.end),
    keys: e.matchKeys,
    query: [defaults[e.scope] || timelines[e.scope]?.[0], e.displayName].filter(Boolean).join('，'),
  })),
  ...catalogEntries.map(c => ({
    title: c.numberedName, anchor: 'P917', keys: [servantLabel(c), ...c.matchKeys], query: c.numberedName,
  })),
];
const page = await readFile(new URL('./review.html', import.meta.url));

export function installReview(server, origin) {
  const previous = server.listeners('request');
  server.removeAllListeners('request');
  server.on('request', (req, res) => {
    const path = new URL(req.url, origin);
    if (![prefix, prefix + '/catalog', prefix + '/run'].includes(path.pathname)) {
      for (const handler of previous) handler.call(server, req, res);
      return;
    }
    const json = (status, value) => {
      res.writeHead(status, {'Content-Type': 'application/json; charset=utf-8'});
      res.end(JSON.stringify(value));
    };
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
    if (req.headers.host !== new URL(origin).host || req.headers['sec-fetch-site'] === 'cross-site') return json(403, {error: '仅供本机审核'});
    if (req.method !== 'GET') return json(405, {error: '方法不支持'});
    if (path.pathname === prefix) {
      res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
      return res.end(page);
    }
    if (path.pathname === prefix + '/catalog') return json(200, catalog);
    const input = path.searchParams.get('input')?.trim();
    if (!input || input.length > 2000) return json(400, {error: '请输入 1 至 2000 字'});
    const selection = selectEntries([], input);
    const system = assembleTurn(base, [], input).messages[0].content;
    const entries = [
      ...worldbookEntries.filter(e => selection.entries.has(e.start)).map(e => ({slot: e.start, title: e.displayName, body: e.body, keys: e.matchKeys, scope: defaults[e.scope] || timelines[e.scope]?.[0]})),
      ...catalogEntries.filter(c => selection.servants.has(c.no)).map(c => ({slot: 917, title: servantLabel(c), body: servantBody(c), keys: [...new Set(['No.' + c.no, c.numberedName, ...c.matchKeys])],
        sources: [
          ['Mooncell 词条版本', c.sourceUrl],
          ['设定集人物资料', c.materialProfileUrl],
          ...(c.materialNpUrls || []).map((url, i) => ['设定集宝具资料 ' + (i + 1), url]),
        ].filter(([, url]) => url),})),
    ].sort((a, b) => a.slot - b.slot);
    return json(200, {entries, system, baseLength: base.length});
  });
}

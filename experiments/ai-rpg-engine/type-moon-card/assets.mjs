import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import dialogue from './resources/dialogue-atlas.json' with {type:'json'};

const prefix = '/rpg-app/type-moon/';
const local = path => new URL(path, import.meta.url);
const [template, css, script, data] = await Promise.all(
  ['public/index.html', 'public/archive.css', 'public/archive.js', 'resources/archive-ui.json']
    .map(file => readFile(local(file), 'utf8')),
);
const archive = JSON.parse(data);
const publicData = {...archive, npcs: archive.npcs.map(({source, ...npc}) => npc)};
const safeData = JSON.stringify(publicData).replaceAll('<', '\\u003c');
const html = template.replace('/* ARCHIVE_STYLE */', () => css)
  .replace('/* ARCHIVE_DATA */', () => safeData)
  .replace('/* ARCHIVE_SCRIPT */', () => script);
const digest = text => "'sha256-" + createHash('sha256').update(text).digest('base64') + "'";
const scriptPolicy = [digest(safeData), digest(script)].join(' ');
const frames=Object.values(dialogue.characters).flatMap(({avatar,portrait,untrusted})=>[avatar,portrait,untrusted?.avatar,untrusted?.portrait].filter(Boolean));
const files = new Set(['cover.webp','fate.mp3', ...frames, ...archive.npcs.map(npc => 'portraits/' + npc.image)]);

export function isTypeMoonAsset(path) {
  return path.startsWith(prefix) && files.has(path.slice(prefix.length));
}

export async function serveTypeMoon(req, res, path, origins = []) {
  if (req.method !== 'GET' || (path !== prefix && !isTypeMoonAsset(path))) return false;
  if (path === prefix) {
    const allowed = [...new Set(origins.map(origin => new URL(origin).origin))].join(' ');
    res.setHeader('Content-Security-Policy', `sandbox allow-scripts; default-src 'none'; script-src ${scriptPolicy}; style-src ${digest(css)}; img-src 'self' ${allowed}; media-src 'self' ${allowed}; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'`);
    res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'X-Content-Type-Options': 'nosniff'});
    res.end(html);
  } else {
    const bytes = await readFile(local('public/' + path.slice(prefix.length)));
    res.writeHead(200, {'Content-Type': path === prefix+'fate.mp3' ? 'audio/mpeg' : 'image/webp', 'Content-Length': bytes.length,
      'Cross-Origin-Resource-Policy': 'cross-origin', 'X-Content-Type-Options': 'nosniff'});
    res.end(bytes);
  }
  return true;
}

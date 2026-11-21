import {readFile} from 'node:fs/promises';

const files = {
  'index.html': 'text/html; charset=utf-8',
  'opening.jpg': 'image/jpeg',
  'background.jpg': 'image/jpeg',
  'cover.avif': 'image/avif',
};

export async function serveAsset(req, res) {
  const path = new URL(req.url, 'http://local').pathname;
  const prefix = '/rpg-app/daomu/';
  if (!['GET','HEAD'].includes(req.method) || !path.startsWith(prefix)) return false;
  const file = path.slice(prefix.length) || 'index.html';
  if (!Object.hasOwn(files, file)) return false;
  const body = await readFile(new URL('./public/' + file, import.meta.url));
  res.setHeader('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
  res.writeHead(200, {'Content-Type': files[file], 'Content-Length': body.length, 'Access-Control-Allow-Origin':'*'});
  res.end(req.method === 'HEAD' ? undefined : body);
  return true;
}

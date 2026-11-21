import {readFile} from 'node:fs/promises';
import {builderHtml} from './view.mjs';

const assets = new Map([
  ['cover.jpg', 'image/jpeg'],
  ['card.css', 'text/css; charset=utf-8'],
  ['bridge.js', 'text/javascript; charset=utf-8'],
  ['fonts/display.ttf', 'font/ttf'],
  ['fonts/serif.woff', 'font/woff'],
  ['fonts/sans.woff', 'font/woff'],
]);
const root = '/rpg-app/modern-life/';
const sandbox = "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'";

export async function modernLifeAsset(req, res) {
  const path = new URL(req.url, 'http://local').pathname;
  if (!['GET', 'HEAD'].includes(req.method) || !path.startsWith(root)) return false;
  const file = path.slice(root.length);
  const page = file === '' || file === 'index.html';
  if (!page && !assets.has(file)) return false;
  const bytes = page ? Buffer.from(builderHtml) : await readFile(new URL('./public/' + file, import.meta.url));
  res.writeHead(200, {
    'Content-Type': page ? 'text/html; charset=utf-8' : assets.get(file),
    'Content-Length': bytes.length,
    'Access-Control-Allow-Origin': '*',
    'Content-Security-Policy': sandbox,
  });
  res.end(req.method === 'HEAD' ? undefined : bytes);
  return true;
}

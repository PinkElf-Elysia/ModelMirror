import {readFile} from 'node:fs/promises';
import {extname} from 'node:path';
const publicDir = new URL('./public/', import.meta.url);
const types={'.html':'text/html; charset=utf-8','.webp':'image/webp','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.mp3':'audio/mpeg','.woff2':'font/woff2'};
export async function starRailAsset(req,res) {
  const prefix='/rpg-app/star-rail/';
  const path=new URL(req.url,'http://rpg').pathname;
  if (!['GET','HEAD'].includes(req.method)||!path.startsWith(prefix)) return false;
  const file=path.slice(prefix.length)||'index.html';
  if (!['index.html','title.webp','logo.webp'].includes(file) && !/^assets\/[a-z0-9_-]+\/[a-z0-9_-]+\.(webp|png|jpe?g|mp3|woff2)$/i.test(file)) return false;
  let bytes;
  try { bytes=await readFile(new URL(file,publicDir)); }
  catch (e) { if (e.code==='ENOENT') return false; throw e; }
  res.setHeader('Access-Control-Allow-Origin','*');
  res.setHeader('Cross-Origin-Resource-Policy','cross-origin');
  res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'; font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
  res.writeHead(200,{'Content-Type':types[extname(file)],'Content-Length':bytes.length});
  res.end(req.method==='HEAD'?undefined:bytes);
  return true;
}

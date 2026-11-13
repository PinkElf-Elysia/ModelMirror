import {readFile} from 'node:fs/promises';
const base=new URL('./public/',import.meta.url);
const assets=JSON.parse(await readFile(new URL('./resources/ASSETS.json',import.meta.url),'utf8'));
const types=new Map(assets.map(a=>[a.file,a.mediaType]));
export const isElderlandAsset=path=>path.startsWith('/rpg-app/elderland/')&&types.has(path.slice('/rpg-app/elderland/'.length));
export async function serveElderland(req,res,path,origins=[]){
 const file=path.slice('/rpg-app/elderland/'.length)||'index.html';
 if(req.method!=='GET'||(file!=='index.html'&&!types.has(file)))return false;
 if(file==='index.html'){
  const sources=[...new Set(origins.map(o=>new URL(o).origin))].join(' ');
  res.setHeader('Content-Security-Policy',`sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src ${sources} data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'`);
 }else res.setHeader('Cross-Origin-Resource-Policy','cross-origin');
 const bytes=await readFile(new URL(file,base));
 res.writeHead(200,{'Content-Type':types.get(file)||'text/html; charset=utf-8','Content-Length':bytes.length});res.end(bytes);return true;
}

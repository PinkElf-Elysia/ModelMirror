import {readFile} from 'node:fs/promises';
const types={'index.html':'text/html; charset=utf-8','opening.jpg':'image/jpeg','world.jpg':'image/jpeg','display.woff':'font/woff','body.woff':'font/woff'};
export async function narutoAsset(req,res){
 const path=new URL(req.url,'http://local').pathname;
 if(!['GET','HEAD'].includes(req.method)||!path.startsWith('/rpg-app/naruto/'))return false;
 const file=path.slice('/rpg-app/naruto/'.length)||'index.html';
 if(!Object.hasOwn(types,file))return false;
 const bytes=await readFile(new URL('./public/'+file,import.meta.url));
 res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; media-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
 res.setHeader('Access-Control-Allow-Origin','*');
 res.writeHead(200,{'Content-Type':types[file],'Content-Length':bytes.length});
 res.end(req.method==='HEAD'?undefined:bytes);
 return true;
}

import {readFile} from 'node:fs/promises';
const types={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','bridge.js':'text/javascript; charset=utf-8','adaptation.css':'text/css; charset=utf-8','logo.png':'image/png','vendor/mdi.css':'text/css; charset=utf-8','vendor/sortable.min.js':'text/javascript; charset=utf-8','vendor/materialdesignicons-webfont.woff2':'font/woff2'};
export async function blueFishAsset(req,res){
 const path=new URL(req.url,'http://local').pathname;
 if(req.method!=='GET'||!path.startsWith('/rpg-app/blue-fish/'))return false;
 const file=path.slice('/rpg-app/blue-fish/'.length)||'index.html';
 if(!Object.hasOwn(types,file))return false;
 const bytes=await readFile(new URL('./public/'+file,import.meta.url));
 res.setHeader('Content-Security-Policy',"sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
 // Public, credential-free font resources must load inside the opaque sandbox.
 res.setHeader('Access-Control-Allow-Origin','*');
 res.writeHead(200,{'Content-Type':types[file]});res.end(bytes);return true;
}

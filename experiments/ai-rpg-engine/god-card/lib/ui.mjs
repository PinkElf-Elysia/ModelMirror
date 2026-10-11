import {readFile} from 'node:fs/promises';
import {resolve,sep} from 'node:path';
import {pathToFileURL} from 'node:url';
import {commentaryAssets} from '../../card-replica/lib/card-presentation.mjs';
const imageRoutes=new Set(Object.values(commentaryAssets).map(path=>path.replace(/^\.\//,'')));
const god=new URL('../',import.meta.url),dist=new URL('../../card-replica/dist/',import.meta.url);
const files=new Map([
 ['setup/index.html',[new URL('public/index.html',god),'text/html; charset=utf-8']],
 ['setup/styles.css',[new URL('public/styles.css',god),'text/css; charset=utf-8']],
 ['setup/app.mjs',[new URL('public/app.mjs',god),'text/javascript; charset=utf-8']],
 ['lib/setup.mjs',[new URL('lib/setup.mjs',god),'text/javascript; charset=utf-8']],
 ['', [new URL('god.html',dist),'text/html; charset=utf-8']]
]);
// Public document only: no session data, credentials or API response is embedded.
// Serve this at the preview edge; all API requests retain the host's origin guard.
export async function godDocumentNavigation(req,res,cardDist=null){
 if(req.method!=='GET'||req.headers['sec-fetch-mode']!=='navigate'||req.headers['sec-fetch-dest']!=='document')return false;
 if(new URL(req.url,'http://preview').pathname!=='/rpg-app/god/')return false;
 res.setHeader('Cache-Control','no-store');
 res.setHeader('X-Content-Type-Options','nosniff');
 res.setHeader('Referrer-Policy','no-referrer');
 return godUi('',res,cardDist);
}
export async function godUi(route,res,cardDist=null){
 const output=cardDist?pathToFileURL(resolve(cardDist)+sep):dist;
 let file=files.get(route);
 if(route==='')file=[new URL('god.html',output),'text/html; charset=utf-8'];
 if(!file&&/^assets\/[a-zA-Z0-9_.-]+\.(js|css)$/.test(route))file=[new URL(route,output),route.endsWith('.css')?'text/css; charset=utf-8':'text/javascript; charset=utf-8'];
 if(imageRoutes.has(route))file=[new URL(route,output),route.endsWith('.webp')?'image/webp':route.endsWith('.png')?'image/png':'image/jpeg'];
 if(!file)return false;
 const bytes=await readFile(file[0]);
 res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
 res.writeHead(200,{'Content-Type':file[1]});res.end(bytes);return true;
}

import {readFile} from 'node:fs/promises';
const assets=new Map([['','text/html; charset=utf-8'],['background.jpg','image/jpeg'],['opening.jpg','image/jpeg'],['fonts.css','text/css; charset=utf-8'],...Array.from({length:9},(_,i)=>['fonts/font-'+i+'.woff2','font/woff2'])]);
export const isMedievalAsset=path=>assets.has(path)&&path!=='';
export async function serveMedievalAsset(req,res,path){
 if(req.method!=='GET'||!assets.has(path))return false;
 const bytes=await readFile(new URL('./public/'+(path||'index.html'),import.meta.url));
 if(path==='')res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
 else res.setHeader('Access-Control-Allow-Origin','*');
 res.writeHead(200,{'Content-Type':assets.get(path)});res.end(bytes);return true;
}

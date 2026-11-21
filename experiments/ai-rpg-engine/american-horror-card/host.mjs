import {readFile} from 'node:fs/promises';
const types={'index.html':'text/html; charset=utf-8','background.jpg':'image/jpeg','fonts.css':'text/css; charset=utf-8'};
export async function americanHorrorAsset(req,res){
 const path=new URL(req.url,'http://local').pathname;
 if(!['GET','HEAD'].includes(req.method)||!path.startsWith('/rpg-app/american-horror/'))return false;
 const file=path.slice('/rpg-app/american-horror/'.length)||'index.html';
 const isFont=/^fonts\/[a-f0-9]{16}\.woff2$/.test(file);
 if(!isFont && !Object.hasOwn(types,file))return false;
 const pathRoot=isFont?'../harry-potter-card/public/':file==='fonts.css'?'../slime-card/public/':'./public/';
 let bytes;
 try { bytes=await readFile(new URL(pathRoot+file,import.meta.url)); }
 catch (error) { if(error.code==='ENOENT')return false; throw error; }
 res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; media-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
 res.setHeader('Access-Control-Allow-Origin','*');
 res.writeHead(200,{'Content-Type':isFont?'font/woff2':types[file],'Content-Length':bytes.length});
 res.end(req.method==='HEAD'?undefined:bytes);
 return true;
}

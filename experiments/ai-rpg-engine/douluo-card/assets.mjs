import {stat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {pipeline} from 'node:stream/promises';
import {fileURLToPath} from 'node:url';
const root=new URL('./public/',import.meta.url);
const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.jpg':'image/jpeg','.mp3':'audio/mpeg','.mp4':'video/mp4','.woff2':'font/woff2','.ttf':'font/ttf'};
export async function serveCard(req,res,path){
 if(path!==''&&!/^assets\/(?:asset-\d+\.(?:jpg|mp3|mp4)|font-\d+\.(?:woff2|ttf)|fonts\.css)$/.test(path)){res.writeHead(404);res.end();return;}
 const file=new URL(path||'index.html',root),type=types[file.pathname.slice(file.pathname.lastIndexOf('.'))];
 try{
  const {size}=await stat(file);let start=0,end=size-1,status=200;
  if(req.headers.range){
   const m=/^bytes=(\d+)-(\d*)$/.exec(req.headers.range);
   if(!m||!Number.isSafeInteger(+m[1])||+m[1]>=size||(m[2]&&(+m[2]<+m[1]||!Number.isSafeInteger(+m[2])))){res.writeHead(416,{'Content-Range':`bytes */${size}`});res.end();return;}
   start=+m[1];end=m[2]?Math.min(+m[2],end):end;status=206;
  }
  if(!path)res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; media-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
  res.setHeader('Access-Control-Allow-Origin','*');
  res.writeHead(status,{'Content-Type':type,'X-Content-Type-Options':'nosniff','Accept-Ranges':'bytes','Content-Length':end-start+1,...(status===206?{'Content-Range':`bytes ${start}-${end}/${size}`}:{})});
  await pipeline(createReadStream(fileURLToPath(file),{start,end}),res);
 }catch(error){if(!res.headersSent){res.writeHead(error.code==='ENOENT'?404:500);res.end();}else res.destroy();}
}

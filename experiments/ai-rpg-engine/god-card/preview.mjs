import http from 'node:http';
import {readFile} from 'node:fs/promises';
const port=Number(process.argv[2]??18505);
if(!Number.isInteger(port)||port<1024||port>65535)throw new Error('Invalid loopback port');
const routes=new Map([
 ['/', ['public/index.html','text/html; charset=utf-8']],
 ['/styles.css',['public/styles.css','text/css; charset=utf-8']],
 ['/app.mjs',['public/app.mjs','text/javascript; charset=utf-8']],
 ['/lib/setup.mjs',['lib/setup.mjs','text/javascript; charset=utf-8']]
]);
const server=http.createServer(async(req,res)=>{
 const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
 'Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
 'Permissions-Policy':'camera=(), microphone=(), geolocation=()'};
 if(req.method!=='GET'&&req.method!=='HEAD'){res.writeHead(405,headers).end();return;}
 if(req.url==='/api/status'){res.writeHead(200,{...headers,'Content-Type':'application/json'}).end(JSON.stringify({cardId:'god',phase:'B1-ui-preview',providerEnabled:false,dispatchCount:0}));return;}
 const route=routes.get(req.url);
 if(!route){res.writeHead(404,headers).end('Not found');return;}
 try{const data=await readFile(new URL(route[0],import.meta.url));res.writeHead(200,{...headers,'Content-Type':route[1]});res.end(req.method==='HEAD'?undefined:data);}catch{res.writeHead(500,headers).end('Preview resource unavailable');}
});
server.listen(port,'127.0.0.1',()=>console.log('God card B1 UI preview http://127.0.0.1:'+port));

// Isolated B5 real candidate. No automatic dispatch, no shared deployment.
import http from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,join,extname,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../studio/server.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const directory=join(root,'experiments/ai-rpg-engine/.rpg04-work/memory-palace-b5/data');
const origin='http://127.0.0.1:18497';
const authorization=JSON.parse(await readFile(join(directory,'../AUTHORIZATION.json'),'utf8'));
if(authorization.id!=='rpg-memory-palace-b5-20260922'||authorization.generationLimit!==4||!authorization.noRetry)throw Error('AUTHORIZATION_MISMATCH');
await mkdir(directory,{recursive:true});
const host=await start({memoryPalace:true,port:18495,directory,enabled:false,limit:4,modelControl:{baseURL:'http://127.0.0.1:18493/',serviceToken:process.env.RPG_S2S_TOKEN,enabled:true},origins:[origin,'http://127.0.0.1:18495']});
const publicRoot=join(root,'client/dist');
const server=http.createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,origin);
  if(url.pathname.startsWith('/rpg-app/')){const upstream=http.request({hostname:'127.0.0.1',port:18495,path:req.url,method:req.method,headers:req.headers},r=>{res.writeHead(r.statusCode,r.headers);r.pipe(res);});upstream.on('error',()=>{res.writeHead(502);res.end('Local RPG host unavailable');});req.pipe(upstream);return;}
  if(req.method!=='GET'){res.writeHead(405);res.end();return;}
  if(url.pathname.startsWith('/api/')){res.writeHead(404,{'Content-Type':'application/json'});res.end('{"error":"isolated-rpg-preview"}');return;}
  const path=resolve(publicRoot,'.'+decodeURIComponent(url.pathname));if(!path.startsWith(publicRoot+sep)){if(url.pathname!=='/'){res.writeHead(403);res.end();return;}}
  let bytes,file=path;try{bytes=await readFile(file);}catch{if(extname(url.pathname))throw Error('not-found');file=join(publicRoot,'index.html');bytes=await readFile(file);}
  res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png'}[extname(file)]||'application/octet-stream'),'Cache-Control':'no-store'});res.end(bytes);
 }catch{res.writeHead(404);res.end('Not found');}
});
await new Promise((ok,no)=>{server.once('error',no);server.listen(18497,'127.0.0.1',ok);});
await writeFile(join(directory,'preview.json'),JSON.stringify({pid:process.pid,origin,hostPort:18495,mode:'real-controlled',realLimit:4},null,2));
console.log('M3 B5 controlled preview '+origin+'/rpg/plugins');
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{server.close();host.server.close();});

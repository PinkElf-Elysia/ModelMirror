import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,sep,extname} from 'node:path';
import {SessionStore,offlineGenerate,projection} from './lib/store.mjs';
import {defaults,loadFrozen,worldbookRules} from './lib/assembly.mjs';
import {safeHtml} from './lib/render.mjs';
const base=fileURLToPath(new URL('.',import.meta.url));
export async function createHost({port=18411,directory=resolve(base,'.local/sessions'),generate=offlineGenerate,realGenerate=null}={}){
 loadFrozen();const store=new SessionStore(directory,generate,realGenerate);await store.init();
 const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");
  const send=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(data));};
  try{
   const address=server.address();const authority=`127.0.0.1:${address.port}`,origin=`http://${authority}`;
   if(req.headers.host!==authority||req.headers['sec-fetch-site']==='cross-site'||(req.headers.origin&&req.headers.origin!==origin))return send(403,{error:'非法来源'});
   if(req.method!=='GET'&&(req.headers.origin!==origin||req.headers['content-type']!=='application/json'))return send(403,{error:'来源或类型未通过检查'});
   const url=new URL(req.url,origin);let data={};
   if(req.method==='POST'){let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>1_000_000)return send(413,{error:'请求过大'});chunks.push(chunk);}try{data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{return send(400,{error:'JSON 无效'});}}
   if(req.method==='GET'&&url.pathname==='/api/status')return send(200,{mode:realGenerate?'controlled-real-and-offline':'offline-only',providerEnabled:!!realGenerate,defaults,worldbook:worldbookRules().map(({text,...r})=>r)});
   if(url.pathname==='/api/sessions'){
    if(req.method==='GET')return send(200,await store.list());
    if(req.method==='POST')return send(201,projection(await store.create(data)));
   }
   const match=url.pathname.match(/^\/api\/sessions\/([a-zA-Z0-9-]+)(?:\/(send|cancel))?$/);
   const project=s=>({...projection(s),turns:s.turns.map(t=>({...t,html:safeHtml(t.raw)}))});
   if(match){if(req.method==='GET'&&!match[2])return send(200,project(await store.read(match[1])));if(req.method==='POST'&&match[2]==='send')return send(200,project(await store.send(match[1],data)));if(req.method==='POST'&&match[2]==='cancel')return send(200,store.cancel(match[1]));}
   if(url.pathname.startsWith('/api/')||req.method!=='GET')return send(404,{error:'未开放此接口'});
   const path=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname.slice(1));
   if(!/^(index\.html|assets\/[\w.-]+)$/.test(path))return send(404,{error:'资源不存在'});
   const root=resolve(base,'dist'),filename=resolve(root,path);if(!filename.startsWith(root+sep))return send(403,{error:'路径越界'});
   const body=await readFile(filename);res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8'}[extname(filename)]||'application/octet-stream')});res.end(body);
  }catch(e){send(e.status||(e.code==='ENOENT'?404:400),{error:e.status?e.message:'请求未完成，请核对输入或本地服务状态；不会自动重试。'});}
 });
 await new Promise((ok,fail)=>{server.once('error',fail);server.listen(port,'127.0.0.1',ok);});
 return {server,store,port:server.address().port};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const arg=process.argv.indexOf('--port');const port=arg>=0?Number(process.argv[arg+1]):18411;
 if(!Number.isInteger(port)||port<1024||port>65535)throw Error('Invalid port');
 const {port:actual}=await createHost({port});console.log(`Local card preview http://127.0.0.1:${actual}/ (provider disabled)`);
}

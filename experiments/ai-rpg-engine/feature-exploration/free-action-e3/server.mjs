import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {need} from '../../agent-core/contracts.mjs';import {archiveSource,httpSource} from '../free-action-e2/source.mjs';
import {DraftStore} from './store.mjs';import {createBuilder} from './context.mjs';import {MockProvider,SAMPLES} from './provider.mjs';
const assets={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css']};
export async function startServer({port=18881,directory,source,recoverDeadOwner=false}={}){
 need(Number.isInteger(port)&&port>=0&&port<=65535,'PORT');
 source??=archiveSource(fileURLToPath(new URL('../../.rpg03-work/e3-source/session-1.json',import.meta.url)));
 await source.read();const provider=new MockProvider(),builder=await createBuilder();
 const store=await DraftStore.open(directory??fileURLToPath(new URL('../../.rpg03-work/e3-preview',import.meta.url)),{source,builder,provider,recoverDeadOwner});
 const server=createServer(async(req,res)=>{
  const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  try{
   const origins=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];need(origins.includes(req.headers.host),'ORIGIN');
   if(req.headers.origin)need(origins.some(o=>req.headers.origin==='http://'+o),'ORIGIN');
   res.setHeader('Content-Security-Policy',"default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");res.setHeader('X-Content-Type-Options','nosniff');
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&assets[path]){const [file,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});return res.end(await readFile(new URL('./public/'+file,import.meta.url)));}
   if(req.method==='GET'&&path==='/api/view')return json(200,{...await store.view(),samples:SAMPLES,mode:'offline-simulated'});
   if(req.method==='GET'&&path==='/api/export')return json(200,await store.export());
   if(req.method!=='POST'||path!=='/api/draft')return json(404,{error:'NOT_FOUND'});
   need(req.headers['x-rpg-e3']==='proposal-only'&&req.headers['content-type']?.startsWith('application/json'),'ORIGIN');
   let raw='';for await(const part of req){raw+=part;need(Buffer.byteLength(raw)<=10000,'REQUEST_SIZE');}
   const input=JSON.parse(raw);need(input&&Object.keys(input).every(k=>['kind','operationId','expectedHash','text','changes'].includes(k)),'REQUEST_FIELDS');
   return json(200,input.kind==='reconcile'?await store.reconcile():await store.command(input));
  }catch(e){json(400,{error:e.code||e.message});}
 });
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await store.close();throw e;}
 return {server,store,provider,url:'http://127.0.0.1:'+server.address().port+'/',close:async()=>{await new Promise(ok=>server.close(ok));await store.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const at=k=>{const i=process.argv.indexOf(k);return i<0?null:process.argv[i+1];};
 const source=at('--source-url')?httpSource(at('--source-url'),at('--source-session')):undefined;
 const app=await startServer({port:Number(at('--port')??18881),source,recoverDeadOwner:process.argv.includes('--recover-dead-owner')});
 console.log('E3 offline proposal preview '+app.url);
 for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await app.close();process.exit(0);});
}

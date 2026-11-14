import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {need} from '../../agent-core/contracts.mjs';import {archiveSource,httpSource} from './source.mjs';
import {createContextBuilder,SAMPLES} from './context.mjs';import {MockProposalProvider} from './model.mjs';import {ProposalStore} from './store.mjs';
const assets={'/':['./public/index.html','text/html'],'/app.mjs':['./public/app.mjs','text/javascript'],'/style.css':['../free-action/public/style.css','text/css'],'/e2.css':['./public/e2.css','text/css']};
export async function startServer({port=18862,directory,source,recoverDeadOwner=false}={}){
 need(Number.isInteger(port)&&port>=0&&port<=65535,'PORT');
 source??=archiveSource(fileURLToPath(new URL('../../.rpg03-work/e2-source/session-1.json',import.meta.url)));
 await source.read();const provider=new MockProposalProvider(),builder=await createContextBuilder();
 const store=await ProposalStore.open(directory??fileURLToPath(new URL('../../.rpg03-work/e2-preview',import.meta.url)),{source,builder,provider,recoverDeadOwner});
 const server=createServer(async(req,res)=>{
  const json=(status,v)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(v));};
  try{
   const origins=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];
   need(origins.includes(req.headers.host),'ORIGIN');
   if(req.headers.origin)need(origins.some(o=>req.headers.origin==='http://'+o),'ORIGIN');
   res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Content-Security-Policy',"default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&assets[path]){const [file,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});return res.end(await readFile(new URL(file,import.meta.url)));}
   if(req.method==='GET'&&path==='/api/view')return json(200,{...await store.view(),samples:SAMPLES,realCalls:0,simulatedCalls:provider.calls,mode:'offline-simulated'});
   if(req.method==='GET'&&path==='/api/export')return json(200,await store.export());
   if(req.method!=='POST'||path!=='/api/draft')return json(404,{error:'NOT_FOUND'});
   need(req.headers['content-type']?.startsWith('application/json')&&req.headers['x-rpg-e2']==='proposal-only','ORIGIN');
   let raw='';for await(const chunk of req){raw+=chunk;need(Buffer.byteLength(raw)<12000,'REQUEST_SIZE');}const data=JSON.parse(raw);
   need(Object.keys(data).every(k=>['command','input','expectedHash','proposalHash','quantity'].includes(k)),'REQUEST_FIELDS');
   if(data.command==='reconcile')return json(200,await store.reconcile());
   if(data.command==='revise')return json(200,await store.revise(data.input,data.expectedHash));
   if(data.command==='quantity')return json(200,await store.quantity(data.quantity,data.expectedHash,data.proposalHash));
   if(['confirm','revoke','cancel'].includes(data.command))return json(200,await store.action(data.command,data.expectedHash,data.proposalHash));
   return json(400,{error:'COMMAND_UNSUPPORTED'});
  }catch(e){json(400,{error:e.code||e.message});}
 });
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await store.close();throw e;}
 return {server,store,provider,url:'http://127.0.0.1:'+server.address().port+'/',close:async()=>{await new Promise(ok=>server.close(ok));await store.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const at=flag=>{const i=process.argv.indexOf(flag);return i<0?null:process.argv[i+1];};
 const url=at('--source-url'),session=at('--source-session');
 const app=await startServer({port:Number(at('--port')??18862),source:url?httpSource(url,session):undefined,recoverDeadOwner:process.argv.includes('--recover-dead-owner')});
 console.log('E2 proposal only: '+app.url);for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await app.close();process.exit(0);});
}

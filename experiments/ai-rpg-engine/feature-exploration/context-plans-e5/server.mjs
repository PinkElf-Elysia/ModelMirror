import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {join,resolve} from 'node:path';import {fileURLToPath} from 'node:url';
import {openSources} from '../context-actions-e4/source.mjs';import {PlanStore} from './store.mjs';import {need,keys} from '../../agent-core/contracts.mjs';
const assets={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css']};
export async function startServer({port=18916,directory=fileURLToPath(new URL('../../.rpg03-work/e5-preview',import.meta.url)),offlinePreview=false,recoverDeadOwner=false}={}){
 need(offlinePreview===true,'E5_PREVIEW_DISABLED');need(Number.isInteger(port)&&port>=0&&port<=65535,'E5_PORT');
 const sources=await openSources(join(directory,'sources')),store=await PlanStore.open(join(directory,'plans'),sources,{recoverDeadOwner});
 const server=createServer(async(req,res)=>{
  const json=(code,v)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(v));};
  try{
   const allowed=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];need(allowed.includes(req.headers.host),'E5_ORIGIN');
   if(req.headers.origin)need(allowed.some(o=>'http://'+o===req.headers.origin),'E5_ORIGIN');
   res.setHeader('Content-Security-Policy',"default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");res.setHeader('X-Content-Type-Options','nosniff');
   const u=new URL(req.url,'http://'+req.headers.host),id=u.searchParams.get('adapter')??'cultivation';
   if(req.method==='GET'&&assets[u.pathname]){const [file,type]=assets[u.pathname];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});res.end(await readFile(new URL('./public/'+file,import.meta.url)));return;}
   if(req.method==='GET'&&u.pathname==='/api/view')return json(200,await store.view(id));
   if(req.method==='GET'&&u.pathname==='/api/export'){const p=await store.export(id);res.setHeader('Content-Disposition','attachment; filename="e5-plan-preview.json"');return json(200,p);}
   if(req.method!=='POST'||u.pathname!=='/api/plan')return json(404,{error:'NOT_FOUND'});
   need(req.headers['x-rpg-e5']==='plan-only'&&req.headers['content-type']?.startsWith('application/json'),'E5_ORIGIN');
   let raw='';for await(const chunk of req){raw+=chunk;need(Buffer.byteLength(raw)<=24000,'E5_SIZE');}
   const c=JSON.parse(raw);
   if(c.kind==='reconcile'){keys(c,['kind','adapterId']);return json(200,await store.reconcile(c.adapterId));}
   return json(200,await store.command(c));
  }catch(e){json(400,{error:e.code||e.message});}
 });
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await store.close();throw e;}
 return {server,store,sources,url:'http://127.0.0.1:'+server.address().port+'/',close:async()=>{await new Promise(ok=>server.close(ok));await store.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const get=k=>{const i=process.argv.indexOf(k);return i<0?undefined:process.argv[i+1];};
 const app=await startServer({port:Number(get('--port')??18916),directory:get('--data'),offlinePreview:process.argv.includes('--offline-preview'),recoverDeadOwner:process.argv.includes('--recover-dead-owner')});
 console.log('E5 offline preview '+app.url);for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}

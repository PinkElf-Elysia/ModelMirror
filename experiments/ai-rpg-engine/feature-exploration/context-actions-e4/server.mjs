import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {join,resolve} from 'node:path';import {fileURLToPath} from 'node:url';
import {openSources} from './source.mjs';import {DraftStore} from './store.mjs';import {need} from '../../agent-core/contracts.mjs';
const assets={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css']};
export async function startServer({port=18886,directory=fileURLToPath(new URL('../../.rpg03-work/e4-preview',import.meta.url)),offlinePreview=false,recoverDeadOwner=false}={}){
 need(offlinePreview===true,'E4_PREVIEW_DISABLED');need(Number.isInteger(port)&&port>=0&&port<=65535,'E4_PORT');
 const sources=await openSources(join(directory,'sources')),store=await DraftStore.open(join(directory,'drafts'),sources,{recoverDeadOwner});
 const server=createServer(async(req,res)=>{
  const json=(code,v)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(v));};
  try{
   const origins=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];need(origins.includes(req.headers.host),'E4_ORIGIN');
   if(req.headers.origin)need(origins.some(o=>'http://'+o===req.headers.origin),'E4_ORIGIN');
   res.setHeader('Content-Security-Policy',"default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   res.setHeader('X-Content-Type-Options','nosniff');
   const u=new URL(req.url,'http://'+req.headers.host),path=u.pathname,id=u.searchParams.get('adapter')??'cultivation';
   if(req.method==='GET'&&assets[path]){const [file,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});return res.end(await readFile(new URL('./public/'+file,import.meta.url)));}
   if(req.method==='GET'&&path==='/api/view')return json(200,await store.view(id));
   if(req.method==='GET'&&path==='/api/inspect')return json(200,await store.inspect(id));
   if(req.method==='GET'&&path==='/api/export'){const result=await store.export(id);res.setHeader('Content-Disposition','attachment; filename="e4-intent.json"');return json(200,result);}
   if(req.method!=='POST'||path!=='/api/draft')return json(404,{error:'NOT_FOUND'});
   need(req.headers['x-rpg-e4']==='proposal-only'&&req.headers['content-type']?.startsWith('application/json'),'E4_ORIGIN');
   let raw='';for await(const part of req){raw+=part;need(Buffer.byteLength(raw)<=16000,'E4_REQUEST_SIZE');}
   const cmd=JSON.parse(raw);
   if(cmd.kind==='reconcile'){need(Object.keys(cmd).every(k=>['adapterId','kind'].includes(k)),'E4_REQUEST_FIELDS');return json(200,await store.reconcile(cmd.adapterId));}
   return json(200,await store.command(cmd));
  }catch(e){json(400,{error:e.code||e.message});}
 });
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await store.close();throw e;}
 return {server,store,sources,url:'http://127.0.0.1:'+server.address().port+'/',close:async()=>{await new Promise(ok=>server.close(ok));await store.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const get=k=>{const i=process.argv.indexOf(k);return i<0?undefined:process.argv[i+1];};
 const app=await startServer({port:Number(get('--port')??18886),directory:get('--data'),offlinePreview:process.argv.includes('--offline-preview'),recoverDeadOwner:process.argv.includes('--recover-dead-owner')});
 console.log('E4 offline preview '+app.url);for(const sig of ['SIGINT','SIGTERM'])process.once(sig,async()=>{await app.close();process.exit(0);});
}

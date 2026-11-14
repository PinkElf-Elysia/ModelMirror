import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {openRuntime} from './composition.mjs';import {inspectHandoff} from './e2.mjs';import {need} from '../contracts.mjs';
const assets={'/':'index.html','/app.mjs':'app.mjs','/style.css':'style.css'};
export async function startServer({directory,port=18871,offlinePreview=false}={}){
 need(Number.isInteger(port)&&port>=0&&port<=65535,'CORE_PORT');
 const runtime=await openRuntime(directory||fileURLToPath(new URL('../../.rpg03-work/actions-r5-preview',import.meta.url)),{offlinePreview});
 const server=createServer(async(req,res)=>{
  const json=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  try{
   const allowed=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];need(allowed.includes(req.headers.host),'CORE_ORIGIN');
   res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&assets[path]){
    const file=assets[path];res.writeHead(200,{'Content-Type':({'html':'text/html','mjs':'text/javascript','css':'text/css'})[file.split('.').at(-1)]+'; charset=utf-8','Cache-Control':'no-store'});
    return res.end(await readFile(new URL('./public/'+file,import.meta.url)));
   }
   if(req.method==='GET'&&path==='/api/bootstrap')return json(200,{mode:'offline',adapters:runtime.registry.list.map(a=>({id:a.id,name:a.name,initial:a.initial(),rule:{id:a.rule.id,label:a.rule.label}}))});
   const match=path.match(/^\/api\/([a-z-]+)\/sessions(?:\/([a-zA-Z0-9-]+)(?:\/([a-z-]+))?)?$/);
   if(!match)return json(404,{error:'NOT_FOUND'});const [,adapterId,id,action]=match,h=runtime.get(adapterId);
   if(req.method==='GET'&&id){if(!action)return json(200,await h.view(id));if(action==='evidence')return json(200,await h.evidence(id));}
   need(req.method==='POST','CORE_METHOD');
   need(req.headers['content-type']?.startsWith('application/json')&&req.headers['x-rpg-actions']==='offline-preview','CORE_ORIGIN');
   need(!req.headers.origin||allowed.some(x=>req.headers.origin==='http://'+x),'CORE_ORIGIN');
   const chunks=[];let size=0;for await(const c of req){size+=c.length;need(size<=2*1024*1024,'CORE_REQUEST_CAPACITY');chunks.push(c);}
   let data;try{data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{need(false,'CORE_JSON');}
   let result;
   if(!id)result=await h.create(data);
   else if(['execute','propose','cancel'].includes(action))result=await h[action](id,data);
   else if(action==='narrate')result=await h.narrate(id,data.options);
   else if(action==='turn')result=await h.turn(id,{operationId:data.operationId,revision:data.revision,input:data.input},data.options);
   else if(action==='recover')result=await h.recover(id);
   else if(action==='e2')return json(200,inspectHandoff(data,await h.store.read(id)));
   else if(['enabled','override','discard','fork'].includes(action))result=await h.action(id,action,data);
   else return json(404,{error:'NOT_FOUND'});
   return json(200,{id:result.id,revision:result.revision,stateRevision:result.stateRevision,pending:result.pending});
  }catch(e){json(400,{error:e.code||e.message});}
 });
 server.requestTimeout=30000;
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await runtime.close();throw e;}
 return {server,runtime,url:'http://127.0.0.1:'+server.address().port+'/',async close(){await new Promise(ok=>server.close(ok));await runtime.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const i=process.argv.indexOf('--port'),app=await startServer({offlinePreview:process.argv.includes('--offline-preview'),port:i<0?18871:Number(process.argv[i+1])});
 console.log('R5 OFFLINE preview '+app.url);
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}

import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {openRuntime} from './composition.mjs';import {need} from './contracts.mjs';
const assets={'/':'index.html','/app.mjs':'app.mjs','/style.css':'style.css'},types={html:'text/html',mjs:'text/javascript',css:'text/css'};
export async function startServer({directory,port=18841,offlinePreview=false,runtimeFactory=openRuntime,publicFiles=null,viewProjection=async v=>v}={}){
 need(Number.isInteger(port)&&port>=0&&port<=65535,'CORE_PORT');
 const runtime=await runtimeFactory(directory||fileURLToPath(new URL('../.rpg03-work/agent-core-r3',import.meta.url)),{offlinePreview});
 const server=createServer(async(req,res)=>{
  const json=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  try{
   const localPort=server.address().port,allowed=['127.0.0.1:'+localPort,'localhost:'+localPort];need(allowed.includes(req.headers.host),'CORE_ORIGIN');
   res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&assets[path]){const file=assets[path];res.writeHead(200,{'Content-Type':types[file.split('.').at(-1)]+'; charset=utf-8','Cache-Control':'no-store'});return res.end(await readFile(publicFiles?.[path]??new URL('./public/'+file,import.meta.url)));}
   if(req.method==='GET'&&path==='/api/bootstrap')return json(200,{mode:'offline-preview',realCalls:0,adapters:runtime.registry.list.map(a=>({id:a.id,name:a.name,description:a.description,opening:a.opening,initial:a.initial(),binding:a.binding,ownership:a.ownership,scenarios:a.scenarios,contextChoices:a.contextChoices}))});
   const match=path.match(/^\/api\/([a-z-]+)\/sessions(?:\/([a-zA-Z0-9-]+)(?:\/([a-z-]+))?)?$/);
   if(!match)return json(404,{error:'NOT_FOUND'});const [,adapterId,id,action]=match,host=runtime.get(adapterId);
   if(req.method==='GET'&&id){
    if(action==='progress')return json(200,{text:host.progress.get(id)||null});
    if(action==='evidence')return json(200,await host.evidence(id));
    if(!action)return json(200,await viewProjection(await host.view(id),host));
   }
   if(req.method!=='POST')return json(404,{error:'NOT_FOUND'});
   need(req.headers['content-type']?.startsWith('application/json')&&req.headers['x-rpg-core']==='offline-preview','CORE_ORIGIN');
   need(!req.headers.origin||allowed.some(h=>req.headers.origin==='http://'+h),'CORE_ORIGIN');
   const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;need(size<=4*1024*1024,'CORE_REQUEST_CAPACITY');chunks.push(chunk);}
   let data;try{data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{need(false,'CORE_JSON');}
   if(!id)return json(201,await host.create(data));
   if(action==='turn')return json(200,await host.turn(id,{operationId:data.operationId,revision:data.revision,input:data.input},data.options));
   if(action==='preview')return json(200,await host.preview(id,data.input,data.options));
   if(action==='recover')return json(200,await host.recover(id));
   if(action==='query'){const r=await host.store.read(id);return json(200,host.adapter.query.execute(r.state,r.stateRevision,data));}
   if(['override','fork','enabled','discard'].includes(action))return json(200,await host.action(id,action,data));
   return json(404,{error:'NOT_FOUND'});
  }catch(e){json(e.status||400,{error:e.code||e.message});}
 });
 server.requestTimeout=30000;
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await runtime.close();throw e;}
 const close=async()=>{await new Promise(ok=>server.close(ok));await runtime.close();};
 return {server,runtime,close,url:'http://127.0.0.1:'+server.address().port+'/'};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const portArg=process.argv.indexOf('--port'),app=await startServer({offlinePreview:process.argv.includes('--offline-preview'),port:portArg>=0?Number(process.argv[portArg+1]):18841});
 console.log('RPG Core R3 OFFLINE preview: '+app.url);
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}

import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {openRuntime} from './composition.mjs';
import {need,keys} from '../contracts.mjs';
const assets={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css']};
export async function startServer({directory=fileURLToPath(new URL('../../.rpg03-work/actors-r6-preview',import.meta.url)),port=18891,offlinePreview=false}={}){
 need(Number.isInteger(port)&&port>=0&&port<=65535,'BRIDGE_PORT');
 const runtime=await openRuntime(directory,{offlinePreview});
 const server=createServer(async(req,res)=>{
  const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  try{
   const allowed=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];
   need(allowed.includes(req.headers.host),'BRIDGE_ORIGIN');
   res.setHeader('X-Content-Type-Options','nosniff');
   res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&assets[path]){
    const [file,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});
    return res.end(await readFile(new URL('./public/'+file,import.meta.url)));
   }
   const match=path.match(/^\/api\/sessions(?:\/([A-Za-z0-9-]+)(?:\/([a-z-]+))?)?$/);need(match,'BRIDGE_ROUTE');
   const [,key,action]=match,h=runtime.host;
   if(req.method==='GET'&&key&&!action)return json(200,await h.view(key));
   if(req.method==='GET'&&key&&action==='evidence')return json(200,{kind:'offline-fixture',bridge:await h.read(key),world:await h.world.evidence((await h.read(key)).worldId),realCalls:0,costUSD:0});
   need(req.method==='POST'&&req.headers['content-type']?.startsWith('application/json')&&req.headers['x-rpg-bridge']==='offline-preview','BRIDGE_ORIGIN');
   need(!req.headers.origin||allowed.some(host=>req.headers.origin==='http://'+host),'BRIDGE_ORIGIN');
   let size=0;const chunks=[];for await(const c of req){size+=c.length;need(size<=16384,'BRIDGE_CAPACITY');chunks.push(c);}
   const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));let out;
   if(!key){keys(data,['operationId','inventory']);out=await h.create(data);}
   else if(action==='execute'){keys(data,['operationId','revision']);out=await h.worldAction(key,data);
    if(out.tasks[data.operationId]?.status==='completed'){await h.narrateWorld(key);out=await h.deliver(key);}
   }else if(action==='deliver'){keys(data,[]);out=await h.deliver(key);}
   else if(action==='respond'){keys(data,['operationId','actorId','input']);out=await h.respond(key,data);}
   else if(action==='recover'){keys(data,['taskId']);out=await h.recover(key,data.taskId);if(out.tasks[data.taskId]?.purpose==='world-action'&&out.tasks[data.taskId].status==='completed'){await h.narrateWorld(key);out=await h.deliver(key);}}
   else need(false,'BRIDGE_ROUTE');
   json(200,await h.view(out.id));
  }catch(e){json(400,{error:e.code||'BRIDGE_REQUEST_FAILED'});}
 });
 server.requestTimeout=30000;
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await runtime.close();throw e;}
 return {server,runtime,url:'http://127.0.0.1:'+server.address().port+'/',async close(){await new Promise(ok=>server.close(ok));await runtime.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const i=process.argv.indexOf('--port'),app=await startServer({port:i<0?18891:Number(process.argv[i+1]),offlinePreview:process.argv.includes('--offline-preview')});
 console.log('R6 OFFLINE preview '+app.url);
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}

import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {join,resolve} from 'node:path';import {fileURLToPath} from 'node:url';
import {SceneChoices} from './choices.mjs';
import {openScene} from './bootstrap.mjs';import {keys,need} from '../contracts.mjs';
const assets={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css']};
export async function startServer({sceneChoices=false,directory=fileURLToPath(new URL(sceneChoices?'../../.rpg03-work/scene-r10-preview':'../../.rpg03-work/scene-r9-preview',import.meta.url)),port=sceneChoices?18924:18918,offlinePreview=false,limits,delayMs=60}={}){
 need(offlinePreview===true&&Number.isInteger(port)&&port>=0&&port<=65535,'SCENE_OFFLINE_ONLY');
 const runtime=await openScene(directory,{offlinePreview,limits,delayMs});
 const bridge=sceneChoices?new SceneChoices(runtime,join(directory,'choices')):null;
 const view=key=>bridge?bridge.access(key):runtime.view(key);
 const server=createServer(async(req,res)=>{
  const json=(status,x)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(x));};
  try{
   const hosts=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];need(hosts.includes(req.headers.host),'R9_ORIGIN');
   res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&bridge&&path==='/choices.mjs'){res.writeHead(200,{'Content-Type':'text/javascript; charset=utf-8'});return res.end(await readFile(new URL('./public/choices.mjs',import.meta.url)));}
   if(req.method==='GET'&&assets[path]){const [f,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});return res.end(await readFile(new URL('./public/'+f,import.meta.url)));}
   const m=path.match(/^\/api\/runs(?:\/([A-Za-z0-9-]+)(?:\/([a-z-]+))?)?$/);need(m,'R9_ROUTE');const [,key,action]=m;
   if(req.method==='GET'&&!key){const list=await runtime.list();if(bridge)list.sceneChoices=true;return json(200,list);}
   if(req.method==='GET'&&key&&!action)return json(200,await view(key));
   need(req.method==='POST'&&req.headers['content-type']?.startsWith('application/json')&&req.headers['x-rpg-scene']==='offline-preview','R9_ORIGIN');
   need(!req.headers.origin||hosts.some(h=>req.headers.origin==='http://'+h),'R9_ORIGIN');
   const chunks=[];let size=0;for await(const c of req){size+=c.length;need(size<=32768,'R9_CAPACITY');chunks.push(c);}const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));
   let out;
   if(!key){keys(data,['operationId','adapterId']);out=await runtime.create(data);}
   else if(bridge&&action==='choice-edit')return json(200,await bridge.access(key,action,data));
   else if(bridge&&action==='choice-send')return json(200,await bridge.send(key,data));
   else if(bridge&&action==='cancel'){
    keys(data,['operationId','revision','proposalId','proposalHash']);
    out=await runtime.exclusive(key,async()=>{const run=await runtime.run(key);await runtime.ready(run);
     await runtime.port(run).store.cancel(run.worldId,data);return runtime.view(key);});
   }
   else if(action==='submit'){if(bridge)need(typeof data.operationId==='string'&&!data.operationId.startsWith('choice-'),'CHOICE_RESERVED');keys(data,['operationId','ownerId','input','strategy','fault']);need(data.ownerId!==runtime.compiled.entrypoints.narration,'R9_NARRATE_ENTRY');out=await runtime.submit(key,data);}
   else if(action==='confirm')out=await runtime.confirm(key,data);
   else if(action==='narrate'){keys(data,['strategy','fault']);out=await runtime.narrate(key,data);}
   else if(action==='recover'){keys(data,['taskId']);out=await runtime.recover(key,data.taskId);}
   else if(action==='dismiss'){keys(data,['taskId']);out=await runtime.dismiss(key,data.taskId);}
   else if(action==='adopt'){keys(data,['taskId']);out=await runtime.adopt(key,data.taskId);}
   else if(action==='finish'){keys(data,['operationId','reason']);out=await runtime.finish(key,data);}

   else if(action==='checkpoint'){keys(data,['operationId']);out=await runtime.checkpoint(key,data);}
   else if(action==='fork'){keys(data,['operationId','checkpointId']);out=await runtime.fork(key,data);}
   else need(false,'R9_ROUTE');json(200,bridge?await view(out.id):out);
  }catch(e){json(400,{error:e.code||'R9_REQUEST_FAILED'});}
 });server.requestTimeout=30000;
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await runtime.close();throw e;}
 return {runtime,server,url:'http://127.0.0.1:'+server.address().port+'/',async close(){await new Promise(ok=>server.close(ok));await runtime.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const at=process.argv.indexOf('--port'),dt=process.argv.indexOf('--data'),sceneChoices=process.argv.includes('--scene-choices');const app=await startServer({sceneChoices,
offlinePreview:process.argv.includes('--offline-preview'),...(at<0?{}:{port:Number(process.argv[at+1])}),...(dt<0?{}:{directory:resolve(process.argv[dt+1])})});
 console.log((sceneChoices?'R10':'R9')+' offline preview '+app.url);for(const s of ['SIGINT','SIGTERM'])process.once(s,async()=>{await app.close();process.exit(0);});
}

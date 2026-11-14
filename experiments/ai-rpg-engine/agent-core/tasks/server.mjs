import {createServer} from 'node:http';import {readFile} from 'node:fs/promises';import {resolve} from 'node:path';import {fileURLToPath} from 'node:url';
import {openRuntime} from './composition.mjs';import {need,keys} from '../contracts.mjs';
const assets={'/':['index.html','text/html'],'/app.mjs':['app.mjs','text/javascript'],'/style.css':['style.css','text/css']};
export async function startServer({directory=fileURLToPath(new URL('../../.rpg03-work/tasks-r7-preview',import.meta.url)),port=18895,offlinePreview=false,limits,delayMs=250}={}){
 need(offlinePreview===true&&Number.isInteger(port)&&port>=0&&port<=65535,'R7_PREVIEW_DISABLED');
 const runtime=await openRuntime(directory,{offlinePreview:true,limits,delayMs});
 const server=createServer(async(req,res)=>{
  const json=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  try{
   const allowed=['127.0.0.1:'+server.address().port,'localhost:'+server.address().port];need(allowed.includes(req.headers.host),'R7_ORIGIN');
   res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   const path=new URL(req.url,'http://'+req.headers.host).pathname;
   if(req.method==='GET'&&assets[path]){const [file,type]=assets[path];res.writeHead(200,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store'});return res.end(await readFile(new URL('./public/'+file,import.meta.url)));}
   const match=path.match(/^\/api\/runs(?:\/([A-Za-z0-9-]+)(?:\/([a-z-]+))?)?$/);need(match,'R7_ROUTE');const [,key,action]=match;
   if(req.method==='GET'&&!key)return json(200,await runtime.list());
   if(req.method==='GET'&&key&&!action)return json(200,await runtime.view(key));
   need(req.method==='POST'&&req.headers['content-type']?.startsWith('application/json')&&req.headers['x-rpg-task']==='offline-preview','R7_ORIGIN');
   need(!req.headers.origin||allowed.some(h=>req.headers.origin==='http://'+h),'R7_ORIGIN');
   const chunks=[];let size=0;for await(const c of req){size+=c.length;need(size<=16384,'R7_CAPACITY');chunks.push(c);}const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));let out;
   if(!key){keys(data,['operationId','adapterId','inventory']);out=await runtime.create(data);}
   else if(action==='execute')out=await runtime.execute(key,data);
   else if(action==='respond'){keys(data,['operationId','actorId','input','strategy','fault']);out=await runtime.respond(key,data);}
   else if(action==='dialogue'){keys(data,['operationId','input']);out=await runtime.dialogue(key,data);}
   else if(action==='recover'){keys(data,['taskId']);out=await runtime.recover(key,data.taskId);}
   else if(action==='resume-narration'){keys(data,[]);out=await runtime.resumeNarration(key);}
   else if(action==='finish'){keys(data,['operationId','reason']);out=await runtime.finishReceipt(key,data);}
   else need(false,'R7_ROUTE');json(200,out);
  }catch(e){json(400,{error:e.code||'R7_REQUEST_FAILED'});}
 });server.requestTimeout=30000;
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}catch(e){await runtime.close();throw e;}
 return {runtime,server,url:'http://127.0.0.1:'+server.address().port+'/',async close(){await new Promise(ok=>server.close(ok));await runtime.close();}};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const at=process.argv.indexOf('--port'),dt=process.argv.indexOf('--data');const app=await startServer({offlinePreview:process.argv.includes('--offline-preview'),port:at<0?18895:Number(process.argv[at+1]),...(dt<0?{}:{directory:resolve(process.argv[dt+1])})});
 console.log('R7 offline preview '+app.url);for(const s of ['SIGINT','SIGTERM'])process.once(s,async()=>{await app.close();process.exit(0);});
}

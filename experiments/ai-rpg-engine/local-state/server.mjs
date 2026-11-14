import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {LocalStateHost} from './host.mjs';
import {exampleState} from './example.mjs';
import {SCENARIOS} from './simulator.mjs';
import {queryState} from './rules.mjs';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {need} from './definition.mjs';
const assets={'/':'index.html','/app.mjs':'app.mjs','/style.css':'style.css'};
const types={html:'text/html',mjs:'text/javascript',css:'text/css'};
export async function startServer({directory,port=18821,enabled=false,hostFactory=options=>LocalStateHost.open(options),scenarios=SCENARIOS}={}){
 const host=await hostFactory({directory:directory||fileURLToPath(new URL('../.rpg03-work/local-state-v1',import.meta.url)),enabled});
 const server=createServer(async(req,res)=>{
  const json=(status,data)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  try{
   const localPort=server.address().port,allowed=['127.0.0.1:'+localPort,'localhost:'+localPort];
   need(allowed.includes(req.headers.host),'STATE_ORIGIN');
   const url=new URL(req.url,'http://'+req.headers.host),path=url.pathname;
   res.setHeader('X-Content-Type-Options','nosniff');
   res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
   if(req.method==='GET'&&assets[path]){
    const file=assets[path],body=await readFile(new URL('./public/'+file,import.meta.url));
    res.writeHead(200,{'Content-Type':types[file.split('.').at(-1)]+'; charset=utf-8','Cache-Control':'no-store'});return res.end(body);
   }
   if(req.method==='GET'&&path==='/api/bootstrap')return json(200,{state:exampleState(),profile:empty(),scenarios,mode:host.mode||'offline-only',realCalls:0});
   const match=path.match(/^\/api\/sessions\/([a-zA-Z0-9-]+)(?:\/([a-z-]+))?$/);
   if(req.method==='GET'&&match&&match[2]==='progress')return json(200,{text:host.progress?.get(match[1])||null});
   if(req.method==='GET'&&match&&!match[2])return json(200,await host.view(match[1]));
   if(req.method==='GET'&&match&&match[2]==='evidence')return json(200,await host.evidence(match[1]));
   if(req.method!=='POST')return json(404,{error:'NOT_FOUND'});
   need(req.headers['content-type']?.startsWith('application/json')&&req.headers['x-local-state']==='preview','STATE_ORIGIN');
   need(!req.headers.origin||allowed.some(h=>req.headers.origin==='http://'+h),'STATE_ORIGIN');
   let body='',bytes=0;for await(const chunk of req){bytes+=chunk.length;need(bytes<=4*1024*1024,'STATE_REQUEST_TOO_LARGE');body+=chunk;}
   let data;try{data=JSON.parse(body);}catch{throw Error('STATE_JSON');}
   if(path==='/api/sessions')return json(201,await host.create(data));
   if(!match)return json(404,{error:'NOT_FOUND'});
   const [,id,action]=match;
   if(action==='turn')return json(200,await host.turn(id,{operationId:data.operationId,revision:data.revision,input:data.input},data.options));
   if(action==='preview')return json(200,await host.preview(id,data));
   if(action==='recover')return json(200,await host.recover(id));
   if(action==='query')return json(200,queryState((await host.store.read(id)).state,data));
   if(action==='override')return json(200,await host.store.override(id,data));
   if(action==='discard')return json(200,await host.store.discard(id,data));
   if(action==='enabled')return json(200,await host.store.enabled(id,data));
   if(action==='fork')return json(200,await host.store.fork(id,data));
   return json(404,{error:'NOT_FOUND'});
  }catch(e){json(e.status||400,{error:e.code||e.message});}
 });
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,'127.0.0.1',ok);});}
 catch(e){await host.close();throw e;}
 const close=async()=>{await new Promise(ok=>server.close(ok));await host.close();};
 return {server,host,close,url:'http://127.0.0.1:'+server.address().port+'/'};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const enabled=process.argv.includes('--offline-preview');
 const app=await startServer({enabled});console.log('Local-state OFFLINE preview: '+app.url);
 for(const signal of ['SIGINT','SIGTERM'])process.once(signal,async()=>{await app.close();process.exit(0);});
}

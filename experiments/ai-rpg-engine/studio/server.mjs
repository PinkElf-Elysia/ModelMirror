import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,join,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {offlineGenerate,projection} from '../card-replica/lib/store.mjs';
import {defaults,worldbookRules} from '../card-replica/lib/assembly.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';
import {createUiService} from '../ui-host/service.mjs';
import {createRecordStore} from '../ui-host/storage.mjs';
import {createProvider,models} from './provider.mjs';
import {createLegacyEngine} from './legacy-engine.mjs';
import {VersionedSessionStore} from './versioned-store.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {canonical,sha,PLUGIN_ID} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
export async function start({port=18420,host='127.0.0.1',directory=resolve(root,'.rpg04-work/studio'),key='',enabled=false,limit=1,origins=[],fetcher}={}){
 const provider=await createProvider({directory:join(directory,'dispatches'),key,enabled,limit,fetcher});
 const earth=new VersionedSessionStore(join(directory,'earth'),offlineGenerate,enabled&&key?(args)=>provider.generate('earth',args):null);await earth.init();
 const records=await createRecordStore(join(directory,'rpg05'));const legacy=createUiService({store:records,engine:createLegacyEngine(records,provider)});
 let plugins=null,pluginError=null;
 try {plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:async id=>{
  const s=await earth.read(id);
  return {id:s.id,cardId:'earth',revision:s.revision,completedTurns:s.history.length/2,
   resourceHash:sha(canonical({characterText:s.characterText,world:s.world,params:s.params,mode:s.mode,runtime:s.runtime?.hash||null})),
   busy:earth.running.has(id),pending:Object.values(s.requests).some(r=>r.status==='pending')};
 }});}catch(e){pluginError=e.code?.startsWith('PLUGIN_')?e.code:'PLUGIN_HOST_UNAVAILABLE';}

 const server=http.createServer(async(req,res)=>{
  const send=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(value));};
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
  try{
   const allowed=new Set(origins.length?origins:[`http://127.0.0.1:${server.address().port}`]);
   if(req.headers['sec-fetch-site']==='cross-site'||(req.headers.origin&&!allowed.has(req.headers.origin)))return send(403,{error:'ORIGIN_REJECTED'});
   if(req.method!=='GET'&&(!allowed.has(req.headers.origin)||req.headers['content-type']!=='application/json'))return send(403,{error:'ORIGIN_REJECTED'});
   const pathname=new URL(req.url,'http://rpg').pathname;
   if(req.method==='GET'&&pathname==='/rpg-app/api/status')return send(200,{cards:await Promise.all(Object.keys(models).map(async id=>({id,...models[id],...await provider.status(id)})))});
   let data={};
   if(req.method==='POST'){let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>1000000)return send(413,{error:'REQUEST_TOO_LARGE'});chunks.push(chunk);}data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}
   if(pathname==='/rpg-app/api/plugins'&&req.method==='GET')return plugins?send(200,await plugins.catalog()):send(503,{error:pluginError});
   const pluginAction=pathname.match(/^\/rpg-app\/api\/plugins\/([a-z0-9.-]+)\/(install|uninstall)$/);
   if(pluginAction&&req.method==='POST'){
    if(pluginAction[1]!==PLUGIN_ID)return send(404,{error:'PLUGIN_UNKNOWN'});
    return plugins?send(200,await plugins.change(pluginAction[2],data)):send(503,{error:pluginError});
   }
   const match=pathname.match(/^\/rpg-app\/(earth|rpg05)\/(.*)$/);if(!match)return send(404,{error:'NOT_FOUND'});
   const [,card,route]=match;
   if(card==='earth'){
    const pluginSession=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/plugins\/rpg\.branch-save(?:\/(enable|disable))?$/);
    if(pluginSession){
     if(!plugins)return send(503,{error:pluginError});
     if(req.method==='GET'&&!pluginSession[2])return send(200,await plugins.sessionStatus(pluginSession[1]));
     if(req.method==='POST'&&pluginSession[2]){
      if(data?.sessionId!==pluginSession[1])return send(400,{error:'PLUGIN_SESSION_BINDING'});
      return send(200,await plugins.change(pluginSession[2],data));
     }
    }

    const view=s=>({...projection(s),runtime:earth.status(s),...(s.provenance?{parentId:s.provenance.parentId,branchTurn:s.provenance.turn}:{}),turns:s.turns.map(t=>({...t,html:safeHtml(t.raw)}))});
    const branchRoute=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/(branches|plugins\/rpg\.branch-save\/nodes)$/);
    if(branchRoute){
     if(req.method==='POST'&&branchRoute[2]==='branches'){const result=await earth.createBranch(branchRoute[1],data,plugins);return send(result.replayed?200:201,{...result,session:view(result.session)});}
     if(req.method==='GET'&&branchRoute[2]==='plugins/rpg.branch-save/nodes')return send(200,await earth.nodes(branchRoute[1],plugins));
    }
    if(req.method==='GET'&&route==='api/status'){const budget=await provider.status('earth');return send(200,{providerEnabled:budget.enabled,mode:budget.enabled?'controlled-real-and-offline':'offline-only',defaults:{...defaults,max_tokens:16384},worldbook:worldbookRules().map(({text,...r})=>r),budget,model:models.earth.model});}
    if(route==='api/sessions'){if(req.method==='GET')return send(200,await earth.list());if(req.method==='POST')return send(201,view(await earth.create(data)));}
    const session=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)(?:\/(send|cancel))?$/);
    if(session){if(req.method==='GET'&&!session[2])return send(200,view(await earth.read(session[1])));if(req.method==='POST'&&session[2]==='send')return send(200,view(await earth.send(session[1],data)));if(req.method==='POST'&&session[2]==='cancel')return send(200,earth.cancel(session[1]));}
   }else{
    if(req.method==='GET'&&route==='api/bootstrap')return send(200,await legacy.bootstrap());
    if(req.method==='POST'&&route==='api/command')return send(200,await legacy.command(data.command,data.payload));
   }
   if(req.method!=='GET'||!/^$|^index\.html$|^assets\/[a-zA-Z0-9_.-]+$/.test(route))return send(404,{error:'NOT_FOUND'});
   const file=join(root,card==='earth'?'card-replica/dist':'ui/dist',route||'index.html');const bytes=await readFile(file);
   res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'}[extname(file)]||'application/octet-stream')});res.end(bytes);
  }catch(e){send(e.status||(e.code==='ENOENT'?404:400),{error:e.status?(e.code||e.message):'REQUEST_FAILED'});}
 });
 server.on('close',()=>{void plugins?.close().catch(()=>{});});
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,host,ok);});}catch(e){await plugins?.close();throw e;}
 return {server,provider,earth,legacy,plugins};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 let key=process.env.OPENROUTER_API_KEY||'';
 if(!key&&process.env.RPG_CREDENTIAL_FILE){const env=await readFile(process.env.RPG_CREDENTIAL_FILE,'utf8');const line=env.split(/\r?\n/).find(x=>/^OPENROUTER_API_KEY\s*=/.test(x));key=(line?.slice(line.indexOf('=')+1)||'').trim().replace(/^['"]|['"]$/g,'');}
 await start({port:Number(process.env.PORT||18420),host:process.env.RPG_BIND||'127.0.0.1',directory:resolve(process.env.RPG_DATA_DIR||join(root,'.rpg04-work/studio')),key,enabled:process.env.RPG_ENABLED==='true',limit:Number(process.env.RPG_CALL_LIMIT_PER_CARD||1),origins:(process.env.RPG_PUBLIC_ORIGINS||'').split(',').filter(Boolean)});
 console.log('RPG service started; credentials are server-only; no automatic dispatch.');
}

import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,join,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {SessionStore,offlineGenerate,projection} from '../card-replica/lib/store.mjs';
import {defaults,worldbookRules} from '../card-replica/lib/assembly.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';
import {createUiService} from '../ui-host/service.mjs';
import {createRecordStore} from '../ui-host/storage.mjs';
import {createProvider,models} from './provider.mjs';
import {createLegacyEngine} from './legacy-engine.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
export async function start({port=18420,host='127.0.0.1',directory=resolve(root,'.rpg04-work/studio'),key='',enabled=false,limit=1,origins=[],fetcher}={}){
 const provider=await createProvider({directory:join(directory,'dispatches'),key,enabled,limit,fetcher});
 const earth=new SessionStore(join(directory,'earth'),offlineGenerate,enabled&&key?(args)=>provider.generate('earth',args):null);await earth.init();
 const records=await createRecordStore(join(directory,'rpg05'));const legacy=createUiService({store:records,engine:createLegacyEngine(records,provider)});
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
   const match=pathname.match(/^\/rpg-app\/(earth|rpg05)\/(.*)$/);if(!match)return send(404,{error:'NOT_FOUND'});
   const [,card,route]=match;let data={};
   if(req.method==='POST'){let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>1000000)return send(413,{error:'REQUEST_TOO_LARGE'});chunks.push(chunk);}data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}
   if(card==='earth'){
    const view=s=>({...projection(s),turns:s.turns.map(t=>({...t,html:safeHtml(t.raw)}))});
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
 await new Promise((ok,no)=>{server.once('error',no);server.listen(port,host,ok);});return {server,provider,earth,legacy};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 let key=process.env.OPENROUTER_API_KEY||'';
 if(!key&&process.env.RPG_CREDENTIAL_FILE){const env=await readFile(process.env.RPG_CREDENTIAL_FILE,'utf8');const line=env.split(/\r?\n/).find(x=>/^OPENROUTER_API_KEY\s*=/.test(x));key=(line?.slice(line.indexOf('=')+1)||'').trim().replace(/^['"]|['"]$/g,'');}
 await start({port:Number(process.env.PORT||18420),host:process.env.RPG_BIND||'127.0.0.1',directory:resolve(process.env.RPG_DATA_DIR||join(root,'.rpg04-work/studio')),key,enabled:process.env.RPG_ENABLED==='true',limit:Number(process.env.RPG_CALL_LIMIT_PER_CARD||1),origins:(process.env.RPG_PUBLIC_ORIGINS||'').split(',').filter(Boolean)});
 console.log('RPG service started; credentials are server-only; no automatic dispatch.');
}

import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {blueFishAsset} from './host.mjs';
import {systemPrompt} from './assembly.mjs';
import {createArchiveHost} from './session-host.mjs';
import {REVIEWED_PLUGIN_IDS} from '../plugins/catalog.mjs';
import {archivePluginHttp} from '../studio/archive-plugin-http.mjs';
import {transcriptHttp} from '../studio/transcript.mjs';
export function blueFishOptions(env){return env.RPG_BLUE_FISH_CHAT_ENABLED==='true'?{enabled:true,limit:Number(env.RPG_BLUE_FISH_CHAT_CALL_LIMIT||0),baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN}:null;}
// New-card mount deliberately leaves the session-fingerprinted studio host unchanged.
export function installBlueFish(server,{directory,control=null,origins=[],ownDirectory,autoSummaryV2=true,transcript=true,messageTools=true,cardDist=new URL('../card-replica/dist/',import.meta.url)}={}){
 const previous=server.listeners('request');server.removeAllListeners('request');
 const features={transcript,messageTools,illustrations:false};let pending;
 const getHost=()=>pending??=(async()=>{
  const dir=join(directory,'archive-chat','blue-fish',control?'real':'offline'),lease=await ownDirectory?.(join(dir,'plugins'));let host;
  try{host=await createArchiveHost(dir,control,{cardId:'blue-fish',name:'蓝色大肥鱼 · 创作助手',system:systemPrompt(),autoSummaryV2,pluginFeatures:features});await lease?.remember();return {...host,close:async()=>{try{await host.plugins.close();}finally{await lease?.fd.close();}}};}
  catch(e){try{await host?.plugins.close();}finally{await lease?.fd.close();}pending=null;throw e;}
 })();
 server.once('close',()=>{void pending?.then(h=>h.close()).catch(()=>{});});
 server.on('request',(req,res)=>{
  const url=new URL(req.url,'http://rpg');
  if(!url.pathname.startsWith('/rpg-app/blue-fish/')){for(const fn of previous)fn.call(server,req,res);return;}
  const send=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(value));};
  void(async()=>{
   res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');
   if(await blueFishAsset(req,res))return;
   const allowed=new Set(origins.length?origins:[`http://127.0.0.1:${server.address().port}`]);
   if(req.headers['sec-fetch-site']==='cross-site'||(req.headers.origin&&!allowed.has(req.headers.origin)))return send(403,{error:'ORIGIN_REJECTED'});
   const route=url.pathname.slice('/rpg-app/blue-fish/chat/'.length);
   if(!url.pathname.startsWith('/rpg-app/blue-fish/chat/'))return send(404,{error:'NOT_FOUND'});
   const transfer=route.match(/^api\/sessions\/([a-zA-Z0-9-]{1,80})\/transcript\/(import|export)$/);
   if(req.method!=='GET'&&(!allowed.has(req.headers.origin)||req.headers['content-type']!==(transfer?.[2]==='import'?'text/plain':'application/json')))return send(403,{error:'ORIGIN_REJECTED'});
   if(req.method==='GET'&&route===''){
    const file=cardDist instanceof URL?new URL('index.html',cardDist):join(cardDist,'index.html');
    const html=(await readFile(file,'utf8')).replace(/(src|href)="\.\/assets\//g,'$1="/rpg-app/earth/assets/');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'self'");
    res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});return res.end(html);
   }
   const host=await getHost();
   if(transfer&&transcript)return transcriptHttp({req,res,send,match:['blue-fish/chat',...transfer.slice(1)],store:host.store,plugins:host.plugins});
   let data={};if(req.method==='POST'){let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>1000000)return send(413,{error:'REQUEST_TOO_LARGE'});chunks.push(chunk);}data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}
   if(route==='api/plugins'&&req.method==='GET')return send(200,await host.plugins.catalog());
   const install=route.match(/^api\/plugins\/(rpg\.[a-z-]+)\/(install|uninstall|upgrade)$/);
   if(install&&req.method==='POST'&&data?.pluginId===install[1]&&REVIEWED_PLUGIN_IDS.includes(install[1]))return send(200,await host.plugins.change(install[2],data));
   if(await archivePluginHttp({route,req,res,data,send,cardId:'blue-fish',host,control,features}))return;
   await host.handle({route,method:req.method,data,plugins:host.plugins,send,query:url.searchParams});
  })().catch(e=>{if(!res.headersSent)send(e.status||500,{error:e.code||'BLUE_FISH_UNAVAILABLE'});else res.destroy();});
 });
}

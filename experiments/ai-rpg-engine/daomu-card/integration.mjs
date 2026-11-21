import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {serveAsset} from './assets.mjs';
import {fixedPrompt} from './assembly.mjs';
import {createArchiveHost} from './session-host.mjs';
import {createSpeech} from '../studio/speech.mjs';
import {createVoiceLibrary} from '../studio/voice-library.mjs';
import {REVIEWED_PLUGIN_IDS,SPEECH_ID,VOICE_LIBRARY_ID} from '../plugins/catalog.mjs';
import {archivePluginHttp} from '../studio/archive-plugin-http.mjs';
import {transcriptHttp} from '../studio/transcript.mjs';
export function daomuOptions(env){return env.RPG_DAOMU_CHAT_ENABLED==='true'?{enabled:true,limit:Number(env.RPG_DAOMU_CHAT_CALL_LIMIT||0),baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN}:null;}
// New-card mount deliberately leaves the session-fingerprinted studio host unchanged.
export function installDaomu(server,{directory,control=null,origins=[],ownDirectory,autoSummaryV2=true,transcript=true,messageTools=true,speech=null,cardDist=new URL('../card-replica/dist/',import.meta.url)}={}){
 const previous=server.listeners('request');server.removeAllListeners('request');
 const features={transcript,messageTools,illustrations:false,speech:!!speech};let pending;
 const speechHandler=createSpeech(join(directory,'speech'),speech),voiceHandler=createVoiceLibrary(join(directory,'voice-library'),speech?.voices);
 const getHost=()=>pending??=(async()=>{
  const dir=join(directory,'archive-chat','daomu',control?'real':'offline'),lease=await ownDirectory?.(join(dir,'plugins'));let host;
  try{host=await createArchiveHost(dir,control,{cardId:'daomu',name:'盗墓笔记大世界',system:fixedPrompt,autoSummaryV2,pluginFeatures:features});await lease?.remember();return {...host,close:async()=>{try{await lease?.prepareClose?.();await host.plugins.close();}finally{await lease?.fd.close();}}};}
  catch(e){try{await lease?.prepareClose?.();await host?.plugins.close();}finally{await lease?.fd.close();}pending=null;throw e;}
 })();
 server.once('close',()=>{void pending?.then(h=>h.close()).catch(()=>{});});
 server.on('request',(req,res)=>{
  const url=new URL(req.url,'http://rpg');
  if(!url.pathname.startsWith('/rpg-app/daomu/')){for(const fn of previous)fn.call(server,req,res);return;}
  const send=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(value));};
  void(async()=>{
   res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');
   if(await serveAsset(req,res))return;
   const allowed=new Set(origins.length?origins:[`http://127.0.0.1:${server.address().port}`]);
   if(req.headers['sec-fetch-site']==='cross-site'||(req.headers.origin&&!allowed.has(req.headers.origin)))return send(403,{error:'ORIGIN_REJECTED'});
   const route=url.pathname.slice('/rpg-app/daomu/chat/'.length);
   if(!url.pathname.startsWith('/rpg-app/daomu/chat/'))return send(404,{error:'NOT_FOUND'});
   const transfer=route.match(/^api\/sessions\/([a-zA-Z0-9-]{1,80})\/transcript\/(import|export)$/);
   if(req.method!=='GET'&&(!allowed.has(req.headers.origin)||req.headers['content-type']!==(transfer?.[2]==='import'?'text/plain':'application/json')))return send(403,{error:'ORIGIN_REJECTED'});
   if(req.method==='GET'&&route===''){
    const file=cardDist instanceof URL?new URL('index.html',cardDist):join(cardDist,'index.html');
    const html=(await readFile(file,'utf8')).replace(/(src|href)="\.\/assets\//g,'$1="/rpg-app/earth/assets/');
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; img-src 'self' data:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'self'");
    res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});return res.end(html);
   }
   const host=await getHost();
   if(transfer&&transcript)return transcriptHttp({req,res,send,match:['daomu/chat',...transfer.slice(1)],store:host.store,plugins:host.plugins});
   let data={};if(req.method==='POST'){const bodyLimit=/\/voice-library\/create$/.test(route)?14001024:1000000;let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>bodyLimit)return send(413,{error:'REQUEST_TOO_LARGE'});chunks.push(chunk);}data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}
   const voiceRoute=route.match(/^api\/sessions\/([a-zA-Z0-9-]{1,80})\/(speech|voice-library)\/([a-zA-Z0-9-]+)$/);
   if(voiceRoute&&speech){const [,id,kind,action]=voiceRoute;return (kind==='speech'?speechHandler:voiceHandler)({card:'daomu',id,action,method:req.method,data,query:url.searchParams,...host,res,send});}
   if(route==='api/plugins'&&req.method==='GET')return send(200,await host.plugins.catalog());
   const install=route.match(/^api\/plugins\/(rpg\.[a-z-]+)\/(install|uninstall|upgrade)$/);
   if(install&&req.method==='POST'&&data?.pluginId===install[1]&&[...REVIEWED_PLUGIN_IDS,SPEECH_ID,VOICE_LIBRARY_ID].includes(install[1]))return send(200,await host.plugins.change(install[2],data));
   if(await archivePluginHttp({route,req,res,data,send,cardId:'daomu',host,control,features}))return;
   await host.handle({route,method:req.method,data,plugins:host.plugins,send,query:url.searchParams});
  })().catch(e=>{if(!res.headersSent)send(e.status||500,{error:e.code||'DAOMU_UNAVAILABLE'});else res.destroy();});
 });
}

import {attachSessionLifecycle,sessionLifecycleHttp} from './session-lifecycle.mjs';
import {cultivationHttp} from '../cultivation-card/host.mjs';
import {MemorySessionStore} from './memory-session-store.mjs';
import {memoryHostTransport} from './memory-host.mjs';
import {memoryHttp,memoryProjection} from './memory-http.mjs';
import {SummarySessionStore} from './summary-session-store.mjs';
import {summaryHostTransport} from './summary-host.mjs';
import {summaryHttp,summaryProjection} from './summary-http.mjs';
import {loadReviewedPlugins} from '../plugins/catalog.mjs';
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,join,extname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {offlineGenerate,projection} from '../card-replica/lib/store.mjs';
import {defaults,worldbookRules} from '../card-replica/lib/assembly.mjs';
import {cardDisplay} from '../card-replica/lib/card-presentation.mjs';
import {createUiService} from '../ui-host/service.mjs';
import {createRecordStore} from '../ui-host/storage.mjs';
import {createProvider,models} from './provider.mjs';
import {createLegacyEngine} from './legacy-engine.mjs';
import {VersionedSessionStore} from './versioned-store.mjs';
import {ModelSessionStore} from './model-session-store.mjs';
import {HistorySessionStore} from './history-session-store.mjs';
import {generateHistoryFixed,generateHistoryControlled} from './history-provider.mjs';
import {createControlledProvider} from './controlled-provider.mjs';
import {previewFetch} from './message-preview.mjs';
import {settleStoryFailure} from './story-failure.mjs';
import {createIllustrationWriter} from './illustration-rewrite.mjs';
import {createIllustrations,imageService} from './illustrations.mjs';
import {transcriptHttp} from './transcript.mjs';
import {forkMessage,storyProgress} from './message-tools.mjs';
import {archivePluginHttp} from './archive-plugin-http.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedCatalog,ILLUSTRATIONS_ID,MESSAGE_TOOLS_ID,TRANSCRIPT_ID,canonical,sha,REVIEWED_PLUGIN_IDS,MODEL_SELECTOR_ID} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../',import.meta.url));
export function archiveChatOptions(env){
 const control=prefix=>env[prefix+'_ENABLED']==='true'
   ?{baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN,enabled:true,limit:Number(env[prefix+'_CALL_LIMIT']||0)}:null;
 const rewrite=control('RPG_ILLUSTRATION_REWRITE');
 return {rewriteControl:rewrite&&{...rewrite,model:env.RPG_ILLUSTRATION_REWRITE_MODEL},xingjianControl:control('RPG_XINGJIAN'),archiveControls:{
   jinyong:control('RPG_JINYONG_CHAT'),ancient:control('RPG_ANCIENT_CHAT'),cultivation:control('RPG_CULTIVATION_CHAT'),'lord-of-mysteries':control('RPG_MYSTERIES_CHAT'),douluo:control('RPG_DOULUO_CHAT'),'dragon-raja':control('RPG_DRAGON_CHAT')
 }};
}
export async function start({port=18420,host='127.0.0.1',directory=resolve(root,'.rpg04-work/studio'),key='',enabled=false,limit=1,origins=[],fetcher,modelControl=null,historyWindow=false,rollingSummary=false,memoryPalace=false,autoSummaryV2=false,godCard=false,godBranches=false,godPlugins=false,godControl=null,godGenerate=offlineGenerate,cardDist=null,cultivationControl=null,xingjianControl=null,archiveControls={},messageTools=false,transcript=false,rewriteControl=null,illustrationService=null,ownArchiveDirectory=null,generationCards=null}={}){
 if(memoryPalace)rollingSummary=true;
 if(rollingSummary)historyWindow=true;
 const cultivation=cultivationControl?await (await import('../cultivation-card/runtime.mjs')).createCultivationRuntime(join(directory,'cultivation'),cultivationControl):null;
 const fixedProgress=new Map();
 const providerOptions={directory:join(directory,'dispatches'),key,enabled,limit,fetcher};
 const provider=await createProvider(providerOptions);
 const controlOptions=modelControl?{...modelControl,directory:join(directory,'dispatches'),limit}:null;
 const originalControl=controlOptions?await createControlledProvider(controlOptions):null;
 const control=historyWindow&&originalControl?{...originalControl,generate:(card,args)=>args.assertHistoryPolicy?generateHistoryControlled(controlOptions,card,args):originalControl.generate(card,args)}:originalControl;
 const evidence=control?.evidence||(historyWindow?(await createControlledProvider({directory:join(directory,'dispatches'),enabled:false,limit})).evidence:null);
 const fixedGenerate=enabled&&key?async(args)=>{
  const id=JSON.stringify([args.sessionId,args.requestId]);
  const options=messageTools?{...providerOptions,fetcher:previewFetch(fetcher||fetch,text=>fixedProgress.set(id,text),key,models.earth)}:providerOptions;
  try{
   if(historyWindow&&args.assertHistoryPolicy)return await generateHistoryFixed(options,args);
   const transport=messageTools?await createProvider(options):provider;
   return await transport.generate('earth',args);
  }finally{fixedProgress.delete(id);}
 }:null;
 const earth=memoryPalace?new MemorySessionStore(join(directory,'earth'),offlineGenerate):rollingSummary?new SummarySessionStore(join(directory,'earth'),offlineGenerate,null,{control:null}):historyWindow?new HistorySessionStore(join(directory,'earth'),offlineGenerate,fixedGenerate,{control,evidence}):control?new ModelSessionStore(join(directory,'earth'),offlineGenerate,fixedGenerate,{control,evidence:control.evidence}):new VersionedSessionStore(join(directory,'earth'),offlineGenerate,fixedGenerate);await earth.init();attachSessionLifecycle(earth);
 const summaryTransport=rollingSummary?await (memoryPalace?memoryHostTransport:summaryHostTransport)({directory,control:controlOptions,limit,store:earth,offlineGenerate}):null;
 if(rollingSummary)earth.control=summaryTransport;
 const earthBudget=async()=>{if(rollingSummary)return summaryTransport.status();const b=await provider.status('earth');return control?{...b,enabled:b.enabled||(await (rollingSummary?summaryTransport:control).status()).enabled}:b;};
 const generationHost=generationCards?await (await import('./generation-cards.mjs')).createGenerationCards({...generationCards,transcript,illustrations:!!illustrationService}):null;
 const records=await createRecordStore(join(directory,'rpg05'));const legacy=createUiService({store:records,engine:createLegacyEngine(records,provider)});
 let godHost=null;
 if(godCard){const {createGodHost}=await import('../god-card/lib/http.mjs');godHost=await createGodHost({directory:join(directory,'god'),generate:godGenerate,branches:godBranches,features:godPlugins,realControl:godControl,autoSummaryV2});}
 if(godHost)attachSessionLifecycle(godHost.store);
 let plugins=null,pluginError=null;
 try {plugins=await createPluginService({directory:join(directory,'plugins'),recoverDeadOwner:!!generationCards&&generationCards.recoverDeadOwner!==false,loadCatalog:async()=>{
  const catalog=await (godHost?.loadPlugins?godHost.loadPlugins({rollingSummary,memoryPalace}):loadReviewedPlugins({rollingSummary,memoryPalace}));
  if(illustrationService)catalog.push(await loadReviewedCatalog(ILLUSTRATIONS_ID));
  if(transcript)catalog.push(await loadReviewedCatalog(TRANSCRIPT_ID));
  if(messageTools)catalog.push(await loadReviewedCatalog(MESSAGE_TOOLS_ID));
  return catalog;
 },lookupSession:async id=>{
  if(godHost){try{return await godHost.pluginSession(id);}catch(e){if(e.status!==404&&e.code!=='ENOENT')throw e;}}
  if(rollingSummary)return earth.pluginSession(id);
  const s=await earth.read(id);
  return {id:s.id,cardId:'earth',revision:s.revision,completedTurns:s.history.length/2,
   resourceHash:sha(canonical({characterText:s.characterText,world:s.world,params:s.params,mode:s.mode,runtime:s.runtime?.hash||null})),
   historyWindowCompatible:historyWindow&&s.runtime?.format===3&&earth.status(s).compatible,
   busy:earth.running.has(id),pending:Object.values(s.requests).some(r=>r.status==='pending')||!!s.historyWindow?.pending};
 }});}catch(e){pluginError=e.code?.startsWith('PLUGIN_')?e.code:'PLUGIN_HOST_UNAVAILABLE';}

 if(godHost&&plugins)godHost.connect?.(plugins);
 if(historyWindow)earth.plugins=plugins;
 if(rollingSummary&&plugins){if(memoryPalace){earth.autoSummaryV2=autoSummaryV2;earth.connectMemory(plugins,summaryTransport);}else earth.connectSummary(plugins,summaryTransport);}
 async function openArchive(directory,create){
  const lease=await ownArchiveDirectory?.(join(directory,'plugins'));
  let card;
  try{
   card=await create(directory);
   await lease?.remember();
   return {...card,async close(){try{await card.plugins.close();}finally{await lease?.fd.close();}}};
  }catch(error){
   try{await card?.plugins.close();}finally{await lease?.fd.close();}
   throw error;
  }
 }
 const archiveFeatures={messageTools,transcript,illustrations:!!illustrationService};
 let xingjianPromise;
 const xingjian=()=>xingjianPromise??=import('../xingjian-card/host.mjs').then(m=>openArchive(join(directory,'xingjian',xingjianControl?'real':'offline'),dir=>m.createXingjian(dir,xingjianControl,autoSummaryV2,archiveFeatures))).catch(e=>{xingjianPromise=null;throw e;});
 const archiveHosts=new Map();
 const archiveNames={jinyong:'金庸大世界','dragon-raja':'龙族大世界',douluo:'斗罗大陆 · 魂师觉醒台','ancient':'古代人生 · 命运卷轴','lord-of-mysteries':'诡秘之主大世界','cultivation':'超级修仙大世界'};
 async function archiveHost(id){
  if(!archiveHosts.has(id)){
   const init=async()=>{
    const system=id==='jinyong'?(await import('../jinyong-card/assembly.mjs')).assemble({input:'档案'}).messages[0].content:
     id==='douluo'?(await import('../douluo-card/assembly.mjs')).assemble({input:'档案'}).messages[0].content:
     id==='ancient'?(await import('../ancient-card/assembly.mjs')).assemble({input:'档案'})[0].content:
     id==='cultivation'?(await (await import('../cultivation-card/host.mjs')).assembleOpening('档案'))[0].content:
     id==='dragon-raja'?(await import('../dragon-raja-card/assembly.mjs')).assemble({input:'档案'}).messages[0].content:
     (await import('../lord-of-mysteries-card/assembly.mjs')).assemble({input:'档案'}).messages[0].content;
    const control=archiveControls[id];
    const {createArchiveHost}=id==='jinyong'?{createArchiveHost:(await import('../jinyong-card/host.mjs')).createJinyongHost}:await import('../xingjian-card/host.mjs');
    return openArchive(join(directory,'archive-chat',id,control?'real':'offline'),dir=>createArchiveHost(dir,control,{cardId:id,name:archiveNames[id],system,autoSummaryV2,pluginFeatures:archiveFeatures}));
   };
   archiveHosts.set(id,init().catch(error=>{archiveHosts.delete(id);throw error;}));
  }
  return archiveHosts.get(id);
 }
 const illustrations=createIllustrations(join(directory,'illustrations'),illustrationService,illustrationService&&await createIllustrationWriter(directory,rewriteControl));
 const server=http.createServer(async(req,res)=>{
  const send=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(value));};
  res.setHeader('Cache-Control','no-store');res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','no-referrer');
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
  try{
   const assetPath=new URL(req.url,'http://rpg').pathname;
   // Opaque sandbox frames send cross-site/null-origin media requests. Only
   // the explicitly named public assets may bypass the API origin guard.
   if(req.method==='GET'&&assetPath.startsWith('/rpg-app/douluo/assets/')){
    const {serveCard}=await import('../douluo-card/assets.mjs');
    return await serveCard(req,res,assetPath.slice('/rpg-app/douluo/'.length));
   }
   const allowed=new Set(origins.length?origins:[`http://127.0.0.1:${server.address().port}`]);
   if(req.headers['sec-fetch-site']==='cross-site'||(req.headers.origin&&!allowed.has(req.headers.origin)))return send(403,{error:'ORIGIN_REJECTED'});
   const transfer=new URL(req.url,'http://rpg').pathname.match(/^\/rpg-app\/(earth|god|xingjian|(?:ancient|cultivation|lord-of-mysteries|douluo|dragon-raja|jinyong)\/chat)\/api\/sessions\/([a-zA-Z0-9-]{1,80})\/transcript\/(import|export)$/);
   if(req.method!=='GET'&&(!allowed.has(req.headers.origin)||req.headers['content-type']!==(transfer?.[3]==='import'?'text/plain':'application/json')))return send(403,{error:'ORIGIN_REJECTED'});
   const pathname=new URL(req.url,'http://rpg').pathname;
   if (req.method === 'GET' && ['/rpg-app/ancient/', '/rpg-app/ancient/landscape.jpg'].includes(pathname)) {
    const image = pathname.endsWith('.jpg');
    const bytes = await readFile(join(root, 'ancient-card', image ? 'landscape.jpg' : 'index.html'));
    if (!image) res.setHeader('Content-Security-Policy', "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
    res.writeHead(200, {'Content-Type': image ? 'image/jpeg' : 'text/html; charset=utf-8'});
    return res.end(bytes);
   }
   if(generationHost&&req.method==='GET'&&pathname==='/rpg-app/api/status')return send(200,{cards:await Promise.all([...generationHost.hosts.values()].map(h=>h.http.publicStatus()))});
   if(req.method==='GET'&&pathname==='/rpg-app/api/status')return send(200,{cards:[{id:'jinyong',title:archiveNames.jinyong,enabled:!!archiveControls.jinyong},{id:'dragon-raja',title:archiveNames['dragon-raja'],enabled:!!archiveControls['dragon-raja']},{id:'douluo',title:archiveNames.douluo,enabled:!!archiveControls.douluo},{id:'lord-of-mysteries',title:archiveNames['lord-of-mysteries'],enabled:!!archiveControls['lord-of-mysteries']},{id:'ancient',title:'古代人生 · 命运卷轴',enabled:false,remaining:0,used:0},{id:'cultivation',title:'超级修仙大世界',...(cultivation?await cultivation.status():{enabled:false,remaining:0,used:0})},...await Promise.all(Object.keys(models).map(async id=>({id,...models[id],...await (id==='earth'?earthBudget():provider.status(id))}))),...(godHost?[await godHost.publicStatus()]:[])]});
   if(transfer&&transcript){
    const host=generationHost?.hosts.get(transfer[1].split('/')[0])?{store:generationHost.hosts.get(transfer[1].split('/')[0]).store,plugins:generationHost.plugins}:transfer[1]==='earth'?{store:earth,plugins}:transfer[1]==='god'?{store:godHost?.store,plugins}:transfer[1]==='xingjian'?await xingjian():await archiveHost(transfer[1].split('/')[0]);
    attachSessionLifecycle(host.store);return await transcriptHttp({req,res,send,match:transfer.slice(1),store:host.store,plugins:host.plugins});
   }
   let data={};
   if(req.method==='POST'){let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>1000000)return send(413,{error:'REQUEST_TOO_LARGE'});chunks.push(chunk);}data=JSON.parse(Buffer.concat(chunks).toString('utf8'));}
   const typedPicture=pathname.match(/^\/rpg-app\/(earth|god|xingjian|ancient|cultivation|lord-of-mysteries)\/(?:chat\/)?api\/sessions\/([a-zA-Z0-9-]{1,80})\/illustrations\/([a-zA-Z0-9-]+)$/);
   if(generationHost&&typedPicture&&illustrationService)return await illustrations({card:typedPicture[1],id:typedPicture[2],action:typedPicture[3],method:req.method,data,plugins:generationHost.plugins,store:generationHost.hosts.get(typedPicture[1]).store,res,send});
   if(generationHost&&await generationHost.handle({pathname,method:req.method,data,query:new URL(req.url,'http://rpg').searchParams,send}))return;
   const chatRoute=pathname.match(/^\/rpg-app\/(ancient|lord-of-mysteries|cultivation|douluo|dragon-raja|jinyong)\/chat\/(.*)$/);
   if(chatRoute||pathname.startsWith('/rpg-app/xingjian/')){
    const route=chatRoute?chatRoute[2]:pathname.slice('/rpg-app/xingjian/'.length);
    if(req.method==='GET' && ((!chatRoute&&route==='card')||route==='')){
     const isSetup=route==='card';
     // These pages share the Earth bundle. Its chunks must resolve through the
     // static asset route even when the document itself is nested under /chat/.
     const bytes=isSetup?await (await import('../xingjian-card/setup.mjs')).renderSetup():
      (await readFile(join(cardDist||join(root,'card-replica/dist'),'index.html'),'utf8')).replace(/(src|href)="\.\/assets\//g,'$1="/rpg-app/earth/assets/').replace(/<title>[^<]*<\/title>/, '<title>'+(chatRoute?archiveNames[chatRoute[1]]:'行间')+'</title>');
     res.setHeader('Content-Security-Policy',isSetup?"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; frame-ancestors 'self'":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'self'");
     res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});return res.end(bytes);
    }
    const card=chatRoute?await archiveHost(chatRoute[1]):await xingjian();
    if(route==='api/plugins'&&req.method==='GET')return send(200,await card.plugins.catalog());
    const install=route.match(/^api\/plugins\/(rpg\.[a-z-]+)\/(install|uninstall)$/);
    if(install&&req.method==='POST'&&data?.pluginId===install[1]&&REVIEWED_PLUGIN_IDS.includes(install[1]))return send(200,await card.plugins.change(install[2],data));
    const cardId=chatRoute?chatRoute[1]:'xingjian';
    if(await archivePluginHttp({route,req,res,data,send,cardId,host:card,control:chatRoute?archiveControls[cardId]:xingjianControl,features:archiveFeatures,illustrations}))return;
    return await card.handle({route,method:req.method,data,plugins:card.plugins,send,query:new URL(req.url,'http://rpg').searchParams});
   }
   if(req.method==='GET'&&pathname.startsWith('/rpg-app/douluo/')){
    const {serveCard}=await import('../douluo-card/assets.mjs');
    return await serveCard(req,res,pathname.slice('/rpg-app/douluo/'.length));
   }
   const dragonAsset=pathname.match(/^\/rpg-app\/dragon-raja\/(|background\.jpg|awakening\.gif)$/);
   if(dragonAsset&&req.method==='GET'){
    const file=dragonAsset[1]||'index.html';
    if(file==='index.html')res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'none'; frame-ancestors 'self'");
    const bytes=await readFile(join(root,'dragon-raja-card/public',file));
    res.writeHead(200,{'Content-Type':file.endsWith('.gif')?'image/gif':file.endsWith('.jpg')?'image/jpeg':'text/html; charset=utf-8'});return res.end(bytes);
   }
   const jinyongAsset=pathname.match(/^\/rpg-app\/jinyong\/(|background\.jpg)$/);
   if(jinyongAsset&&req.method==='GET'){
    const image=!!jinyongAsset[1];
    if(!image)res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'");
    const bytes=await readFile(join(root,'jinyong-card/public',jinyongAsset[1]||'index.html'));
    res.writeHead(200,{'Content-Type':image?'image/jpeg':'text/html; charset=utf-8'});return res.end(bytes);
   }
   const mysteryAsset=pathname.match(/^\/rpg-app\/lord-of-mysteries\/(|world-map\.jpg|fog-background\.jpg)$/);
   if(mysteryAsset&&req.method==='GET'){
    const image=!!mysteryAsset[1];
    if(!image)res.setHeader('Content-Security-Policy',"sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; form-action 'none'; frame-ancestors 'self'");
    const bytes=await readFile(join(root,'lord-of-mysteries-card/public',mysteryAsset[1]||'index.html'));
    res.writeHead(200,{'Content-Type':image?'image/jpeg':'text/html; charset=utf-8'});return res.end(bytes);
   }
   const picture=pathname.match(/^\/rpg-app\/(earth|god)\/api\/sessions\/([a-zA-Z0-9-]{1,80})\/illustrations\/([a-zA-Z0-9-]+)$/);
   if(picture&&illustrationService)return await illustrations({card:picture[1],id:picture[2],action:picture[3],method:req.method,data,plugins,store:picture[1]==='earth'?earth:godHost?.store,res,send});
   if(pathname.startsWith('/rpg-app/cultivation/'))return await cultivationHttp({route:pathname.slice('/rpg-app/cultivation/'.length),method:req.method,data,res,send,runtime:cultivation});
   if (req.method === 'POST' && pathname === '/rpg-app/ancient/api/opening') {
    if (typeof data?.input !== 'string' || !data.input.trim() || data.input.length > 20000)
      return send(400, {error: 'INVALID_OPENING'});
    const {assemble} = await import('../ancient-card/assembly.mjs');
    const messages = assemble({input: data.input});
    return send(200, {cardId: 'ancient', providerEnabled: false, openingHash: sha(messages[1].content), requestHash: sha(JSON.stringify(messages))});
   }
   if(pathname==='/rpg-app/api/plugins'&&req.method==='GET')return plugins?send(200,await plugins.catalog()):send(503,{error:pluginError});
   const pluginAction=pathname.match(/^\/rpg-app\/api\/plugins\/([a-z0-9.-]+)\/(install|uninstall)$/);
   if(pluginAction&&req.method==='POST'){
    if(!REVIEWED_PLUGIN_IDS.includes(pluginAction[1]))return send(404,{error:'PLUGIN_UNKNOWN'});
    if(data?.pluginId!==pluginAction[1])return send(400,{error:'PLUGIN_ID_BINDING'});
    return plugins?send(200,await plugins.change(pluginAction[2],data)):send(503,{error:pluginError});
   }
   const lifecycleRoute=pathname.match(/^\/rpg-app\/(earth|god)\/(api\/.*)$/);
   if(lifecycleRoute){const store=lifecycleRoute[1]==='earth'?earth:godHost?.store;if(store&&await sessionLifecycleHttp({route:lifecycleRoute[2],method:req.method,data,store,send}))return;}
   const messageTools=pathname.match(/^\/rpg-app\/(earth|god)\/api\/sessions\/([a-zA-Z0-9-]+)\/message-tools\/(fork|progress)$/);
   if(messageTools){
    const [,card,id,action]=messageTools,store=card==='earth'?earth:godHost?.store;
    if(!plugins||!store)return send(404,{error:'PLUGIN_HOST_UNAVAILABLE'});
    if(action==='fork'&&req.method==='POST')return send(200,await forkMessage(store,plugins,id,data));
    if(action==='progress'&&req.method==='GET'){
      const grant=await plugins.sessionStatus(id,'rpg.message-tools');
      if(!grant.enabled)return send(403,{error:'PLUGIN_NOT_AUTHORIZED'});
      const params=new URL(req.url,'http://rpg').searchParams;
      const key=params.get('request'),child=params.get('fork');
      if(child && (!key || (child!==store.archive.identity(id,key) && child!=='rewrite-'+sha(canonical([id,key])))))
        return send(403,{error:'MESSAGE_FORK_BINDING_INVALID'});
      return send(200,await storyProgress(store,child||id,key,card==='earth'?modelControl:godControl,card==='earth'?fixedProgress:null));
    }
    return send(405,{error:'METHOD_NOT_ALLOWED'});
   }
   if(godHost&&pathname.startsWith('/rpg-app/god/')){if(req.method==='GET'&&godPlugins){const {godUi}=await import('../god-card/lib/ui.mjs');if(await godUi(pathname.slice('/rpg-app/god/'.length),res,cardDist))return;}await godHost.handle({route:pathname.slice('/rpg-app/god/'.length),method:req.method,data,plugins,send,query:new URL(req.url,'http://rpg').searchParams});return;}
   const match=pathname.match(/^\/rpg-app\/(earth|rpg05)\/(.*)$/);if(!match)return send(404,{error:'NOT_FOUND'});
   const [,card,route]=match;
   if(card==='earth'){
    const pluginSession=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/plugins\/([a-z0-9.-]+)(?:\/(enable|disable))?$/);
    if(pluginSession){
     await earth.read(pluginSession[1]);
     if(!REVIEWED_PLUGIN_IDS.includes(pluginSession[2]))return send(404,{error:'PLUGIN_UNKNOWN'});
     if(!plugins)return send(503,{error:pluginError});
     if(req.method==='GET'&&!pluginSession[3])return send(200,await plugins.sessionStatus(pluginSession[1],pluginSession[2]));
     if(req.method==='POST'&&pluginSession[3]){
      if(data?.sessionId!==pluginSession[1])return send(400,{error:'PLUGIN_SESSION_BINDING'});
      if(data?.pluginId!==pluginSession[2])return send(400,{error:'PLUGIN_ID_BINDING'});
      return send(200,await plugins.change(pluginSession[3],data));
     }
    }

    const view=s=>({...projection(s),...(s.importedTurns?{importedTurns:s.importedTurns}:{}),...summaryProjection(s),...memoryProjection(s),runtime:earth.status(s),...(s.historyWindow?{historyWindow:{config:s.historyWindow.config,configRevision:s.historyWindow.revision,pendingOperationId:s.historyWindow.pending}}:{}),...(s.modelState?{modelSelection:{revision:s.modelState.revision,current:s.modelState.current}}:{}),...(s.provenance?{parentId:s.provenance.parentId,branchTurn:s.provenance.turn}:{}),turns:s.turns.map(t=>({...t,html:cardDisplay(t.raw,'earth')}))});
    if(memoryPalace&&await memoryHttp({route,method:req.method,data,query:new URL(req.url,'http://rpg').searchParams,store:earth,plugins,view,send}))return;
    if(rollingSummary&&await summaryHttp({route,method:req.method,data,query:new URL(req.url,'http://rpg').searchParams,store:earth,plugins,view,send}))return;
    const historyRoute=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/history-window$/);
    if(historyRoute){
     if(!historyWindow)return send(409,{error:'HISTORY_RUNTIME_INCOMPATIBLE'});
     const id=historyRoute[1],query=new URL(req.url,'http://rpg').searchParams;
     if([...query.keys()].some(k=>k!=='operationId')||query.getAll('operationId').length>1)return send(400,{error:'HISTORY_OPERATION_INVALID'});
     if(req.method==='GET')return send(200,{...await earth.historyWindowStatus(id,plugins),...(query.has('operationId')?{operation:await earth.historyOperation(id,query.get('operationId'))}:{})});
     if(req.method==='POST'){if(query.size)return send(400,{error:'HISTORY_OPERATION_INVALID'});const r=await earth.saveHistoryWindow(id,data,plugins);return send(200,{...r,session:view(r.session)});}
    }
    const modelRoute=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/(model-catalog|model-selection)$/);
    if(modelRoute){
     if(!control&&!rollingSummary)return send(503,{error:'CONTROL_DISABLED'});
     const id=modelRoute[1];
     if(req.method==='GET'&&modelRoute[2]==='model-catalog')return send(200,{models:await earth.catalog(id,plugins)});
     if(req.method==='GET'&&modelRoute[2]==='model-selection'){const s=await earth.read(id),grant=plugins?await plugins.sessionStatus(id,MODEL_SELECTOR_ID):null;let usable=false;try{await earth.requireSelectable(s);usable=true;}catch{}return send(200,{modelSelection:s.modelState?{revision:s.modelState.revision,current:s.modelState.current}:null,runtime:earth.status(s),canSelect:usable&&!!grant?.enabled&&!earth.running.has(id)&&!earth.locks.has(id)&&(rollingSummary&&s.mode==='offline'||(await (rollingSummary?summaryTransport:control).status()).enabled)});}
     if(req.method==='POST'&&modelRoute[2]==='model-selection'){const r=await earth.select(id,data,plugins);return send(200,{...r,session:view(r.session)});}
    }
    const branchRoute=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/(branches|plugins\/rpg\.branch-save\/nodes)$/);
    if(branchRoute){
     await earth.read(branchRoute[1]);
     if(req.method==='POST'&&branchRoute[2]==='branches'){const result=await earth.createBranch(branchRoute[1],data,plugins);return send(result.replayed?200:201,{...result,session:view(result.session)});}
     if(req.method==='GET'&&branchRoute[2]==='plugins/rpg.branch-save/nodes')return send(200,await earth.nodes(branchRoute[1],plugins));
    }
    if(req.method==='GET'&&route==='api/status'){const budget=await earthBudget();return send(200,{providerEnabled:budget.enabled,mode:budget.enabled?'controlled-real-and-offline':'offline-only',defaults:{...defaults,max_tokens:16384},worldbook:worldbookRules().map(({text,...r})=>r),budget,model:models.earth.model,...(rollingSummary?{contextPolicy:'default:complete-raw-history; actual policy is per-session',summarySupported:true,memorySupported:memoryPalace}: {})});}
    if(route==='api/sessions'){if(req.method==='GET')return send(200,await earth.list());if(req.method==='POST')return send(201,view(await earth.create(data)));}
    const recovery=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/recover-story$/);
    if(recovery&&req.method==='POST'&&Object.keys(data).join(',')==='requestId')
      return send(200,view(await settleStoryFailure(earth,control,join(directory,'dispatches'),recovery[1],data.requestId)));
    const session=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)(?:\/(send|cancel))?$/);
    if(session){if(req.method==='GET'&&!session[2])return send(200,view(await earth.read(session[1])));if(req.method==='POST'&&session[2]==='send'){const before=await earth.read(session[1]);if(rollingSummary&&[4,5].includes(before.runtime?.format)){const operation=await earth.send(session[1],data);return send(200,{...view(await earth.read(session[1])),operation:{status:operation.status,error:operation.error}});}const sent=await earth.send(session[1],data);
     const settled=sent.runtime?.format===2&&sent.requests[data.requestId]?.status==='pending'?
       await settleStoryFailure(earth,control,join(directory,'dispatches'),session[1],data.requestId):sent;
     return send(200,view(settled));}if(req.method==='POST'&&session[2]==='cancel')return send(200,earth.cancel(session[1]));}
   }else{
    if(req.method==='GET'&&route==='api/bootstrap')return send(200,await legacy.bootstrap());
    if(req.method==='POST'&&route==='api/command')return send(200,await legacy.command(data.command,data.payload));
   }
   if(req.method!=='GET'||!/^$|^index\.html$|^assets\/[a-zA-Z0-9_.-]+$/.test(route))return send(404,{error:'NOT_FOUND'});
   const file=join(card==='earth'&&cardDist?resolve(cardDist):join(root,card==='earth'?'card-replica/dist':'ui/dist'),route||'index.html');const bytes=await readFile(file);
   res.writeHead(200,{'Content-Type':({'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.jpg':'image/jpeg','.webp':'image/webp'}[extname(file)]||'application/octet-stream')});res.end(bytes);
  }catch(e){send(e.status||(e.code==='ENOENT'?404:400),{error:e.status?(e.code||e.message):'REQUEST_FAILED'});}
 });
 server.on('close',()=>{void generationHost?.close().catch(()=>{});for(const host of archiveHosts.values())void host.then(card=>card.close()).catch(()=>{});void xingjianPromise?.then(card=>card.close()).catch(()=>{});void plugins?.close().catch(()=>{});});
 try{await new Promise((ok,no)=>{server.once('error',no);server.listen(port,host,ok);});}catch(e){await plugins?.close();throw e;}
 return {server,provider,earth,legacy,plugins,control,generationHost,god:godHost?.store||null};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 let key=process.env.OPENROUTER_API_KEY||'';
 if(!key&&process.env.RPG_CREDENTIAL_FILE){const env=await readFile(process.env.RPG_CREDENTIAL_FILE,'utf8');const line=env.split(/\r?\n/).find(x=>/^OPENROUTER_API_KEY\s*=/.test(x));key=(line?.slice(line.indexOf('=')+1)||'').trim().replace(/^['"]|['"]$/g,'');}
 await start({...archiveChatOptions(process.env),illustrationService:process.env.RPG_IMAGE_ENABLED==='false'?null:imageService(process.env.RPG_CONTROL_URL||'http://127.0.0.1:8000/'),transcript:process.env.RPG_TRANSCRIPT_ENABLED!=='false',messageTools:process.env.RPG_MESSAGE_TOOLS_ENABLED!=='false',cultivationControl:process.env.RPG_CULTIVATION_ENABLED==='true'?{baseURL:process.env.RPG_CONTROL_URL,serviceToken:process.env.RPG_S2S_TOKEN,enabled:true,limit:Number(process.env.RPG_CULTIVATION_CALL_LIMIT||0)}:null,godPlugins:process.env.RPG_GOD_PLUGINS_ENABLED==='true',godBranches:process.env.RPG_GOD_BRANCHES_ENABLED==='true',godCard:process.env.RPG_GOD_CARD_ENABLED==='true',memoryPalace:process.env.RPG_MEMORY_PALACE_ENABLED==='true',rollingSummary:process.env.RPG_ROLLING_SUMMARY_ENABLED==='true',historyWindow:process.env.RPG_HISTORY_WINDOW_ENABLED==='true',port:Number(process.env.PORT||18420),host:process.env.RPG_BIND||'127.0.0.1',directory:resolve(process.env.RPG_DATA_DIR||join(root,'.rpg04-work/studio')),key,enabled:process.env.RPG_ENABLED==='true',limit:Number(process.env.RPG_CALL_LIMIT_PER_CARD||1),origins:(process.env.RPG_PUBLIC_ORIGINS||'').split(',').filter(Boolean),modelControl:process.env.RPG_MODEL_SELECTION_ENABLED==='true'?{baseURL:process.env.RPG_CONTROL_URL,serviceToken:process.env.RPG_S2S_TOKEN,enabled:process.env.RPG_ENABLED==='true'}:null});
 console.log('RPG service started; credentials are server-only; no automatic dispatch.');
}

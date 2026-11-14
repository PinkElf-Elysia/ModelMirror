import {exactPurchase} from './action-parser.mjs';
import {aggregateMeasurements} from './measurement.mjs';
import {inheritMemory,commitMemory,validateMemory,nextMemoryStage,needsLargeSummary,extraStages} from './memory.mjs';
import {join} from 'node:path';
import {ShopStore} from './shop-store.mjs';
import {StageRunner} from './stage-runner.mjs';
import {createMockProvider} from './mock-provider.mjs';
import {rules,binding,adaptationReport,narrationIssue,stripManagedPanels} from './earth-adapter.mjs';
import {initialAssets,purchaseResult} from './shop-rules.mjs';
import {assembleShopContext,validateContext,displayShopReply} from './context.mjs';
import {clone,hash,requireValue,id,exact,fail} from './contracts.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {loadReviewedCatalog} from '../plugins/catalog.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';
import {cardPresentation} from '../card-replica/lib/card-presentation.mjs';
const displayHtml=raw=>{try{return cardPresentation(raw,'earth').html;}catch{return safeHtml(raw);}};
const PLUGIN='rpg.shop';
const sourcePending=s=>!!s.memoryPalace?.pending||!!s.rollingSummary?.pending||!!s.historyWindow?.pending||!!s.memoryTasks?.pendingSettings||!!s.summaryTasks?.pendingSettings||Object.values(s.requests||{}).some(x=>['pending','unknown'].includes(x.status))||['summaryTasks','memoryTasks'].some(k=>Object.values(s[k]?.operations||{}).some(x=>['pending','unknown'].includes(x.status)));
export async function createShopHost({directory,sourceLoader,provider=createMockProvider(),recoverDeadOwner=false}){
 const store=await ShopStore.open({directory:join(directory,'sessions'),rules,binding,recoverDeadOwner});
 const jobs=new Map();let plugins;
 const runner=new StageRunner({store,provider,assemble:assembleShopContext,onSuccess:commitMemory,validate:(raw,r,p,eventId)=>{
  if(extraStages.includes(p))return validateMemory(raw,r,p,eventId);
  if(p==='narration')return narrationIssue(raw,r.assets);
  try{const v=JSON.parse(raw);if(v.kind==='purchase'&&exact(v,['kind','productId','quantity'])&&rules.products.some(p=>p.id===v.productId)&&Number.isSafeInteger(v.quantity)&&v.quantity>0)return null;
   if(v.kind==='clarify'&&exact(v,['kind','message'])&&typeof v.message==='string'&&v.message.length<1000)return null;
   if(v.kind==='story'&&exact(v,['kind']))return null;
  }catch{}return 'SHOP_INTENT_INVALID';
 }});
 try{plugins=await createPluginService({directory:join(directory,'plugins'),recoverDeadOwner,loadCatalog:()=>loadReviewedCatalog(PLUGIN),lookupSession:async key=>{
  const r=await store.read(key);return {id:r.id,cardId:'earth',revision:r.revision,completedTurns:r.projection.length,resourceHash:hash([r.binding,r.source]),shopCompatible:true};
 }});}catch(e){await store.close();throw e;}
 const entry=await loadReviewedCatalog(PLUGIN);
 async function grant(action,key,operationId){
  const c=await plugins.catalog(),r=key?await store.read(key):null;
  if(key&&(await plugins.sessionStatus(key,PLUGIN)).enabled===(action==='enable'))return;
  operationId='grant-'+hash([operationId,action,r?.revision,c.revision]).slice(0,40);
  const p={operationId,pluginId:PLUGIN,version:entry.manifest.version,artifactSha256:entry.artifactSha256,manifestSha256:entry.manifestSha256,expectedRegistryRevision:c.revision,
   ...(key?{sessionId:key,expectedSessionRevision:r.revision}:{}),...(action==='enable'?{permissions:entry.manifest.permissions}:{})};
  await plugins.change(action,p);
 }
 async function authorize(key,request,action){
  const ticket=await plugins.invoke({pluginId:PLUGIN,sessionId:key,capability:'session.shop.purchase',input:{actionHash:hash(request)}});
  return plugins.commitResult(ticket,action);
 }
 async function source(key){
  if(key==='example')return {id:'example-earth',world:'表世界',characterText:rules.opening.replaceAll('\\n','\n'),history:[],revision:0,pending:false,memory:inheritMemory()};
  requireValue(id(key),'SHOP_SOURCE_INVALID');
  if(key.startsWith('shop-'))throw fail('SHOP_USE_CHECKPOINT');
  const s=await sourceLoader(key);
  requireValue(s.world==='表世界','SHOP_WORLD_UNSUPPORTED');
  return {id:s.id,world:s.world,characterText:s.characterText||'',history:clone(s.history||[]),revision:s.revision,pending:!!sourcePending(s),options:clone(s.params||{}),
   enabledRuleIds:clone(s.enabledRuleIds||[]),memory:inheritMemory(s)};
 }
 async function activate(input){
  requireValue(exact(input,['operationId','sourceId','initial','confirmed','context'])&&id(input.operationId),'SHOP_ACTIVATION_INVALID');
  const target='shop-'+hash([input.sourceId==='example'?'example-earth':input.sourceId,input.operationId]).slice(0,40);
  let existing=null;try{existing=await store.read(target);}catch(e){if(e.code!=='SHOP_SESSION_NOT_FOUND')throw e;}
  if(existing){requireValue(existing.activationInputHash===hash(input),'SHOP_OPERATION_COLLISION');await grant(existing.enabled?'enable':'disable',existing.id,'activate-'+input.operationId);return view(await store.read(existing.id));}
  const original=await source(input.sourceId),context=await validateContext(input.context);
  const c=await plugins.catalog();if(!c.plugins[0].installed)await grant('install',null,'install-'+hash(input.operationId).slice(0,24));
  const r=await store.activate({operationId:input.operationId,source:{...original,activationInputHash:hash(input),memory:{...original.memory,...Object.fromEntries(['summaryEnabled','memoryEnabled'].filter(k=>context[k]!==undefined).map(k=>[k,context[k]]))},context:{...original.context,...context}},initial:input.initial,confirmed:input.confirmed,sourceKind:input.sourceId==='example'?'example':'user-confirmed'});
  await grant('enable',r.id,'enable-'+hash([r.id,input.operationId]).slice(0,24));
  // Initial offline selections use zero-priced simulation only; real providers require explicit configuration.
  if(provider.mode==='simulated'&&r.stages.narration.primary===null){
   await store.update(r.id,n=>{if(!n.stages.narration.primary)for(const p of Object.keys(n.stages))n.stages[p]={primary:'mock-narrator',backup:null,cap:0};});
  }
  return view(await store.read(r.id));
 }
 async function view(r){
  const events=await Promise.all(r.events.map(async e=>{
   let html=null,displayError=null;if(e.narration.raw){try{html=displayHtml(await displayShopReply(e.narration.raw,r));}catch{displayError='配置渲染失败，显示原文；交易记录未改变。';html=displayHtml(stripManagedPanels(e.narration.raw));}}
   return {...e,displayError,narration:{...e.narration,html}};
  }));
  return {id:r.id,revision:r.revision,source:r.source,binding:r.binding,rules:r.rules,assets:r.assets,stateRevision:r.stateRevision,enabled:r.enabled,active:r.active,
   events,stages:r.stages,usage:r.usage,measurement:aggregateMeasurements(Object.values(r.stageRuns).flatMap(s=>s.attempts)),memory:{summaryEnabled:r.memory.summaryEnabled,memoryEnabled:r.memory.memoryEnabled,summary:r.memory.summary,entries:r.memory.entries},mode:provider.mode,models:await provider.catalog(),context:{hash:r.session.context.hash,mods:r.session.context.mods?.map(m=>m.id)||[],profiles:r.session.context.profiles?.length||0},
   stageRuns:Object.values(r.stageRuns).map(s=>({id:s.id,purpose:s.purpose,status:s.status,error:s.error,startedAt:s.startedAt,completedAt:s.completedAt,attempts:s.attempts.map(a=>({id:a.request.id,model:a.request.model.id,status:a.status,costMicros:a.costMicros,reservation:a.reservation,usage:a.receipt?.usage||null,measurement:a.measurement||null}))})),opening:r.session.characterText,appearance:(r.session.context.profiles||[]).map(p=>Object.fromEntries(['css','desktop','mobile','showBackground','showAvatar'].map(k=>[k,p[k]])))};
 }
 async function resolveIntent(key,eventId){
  const r=await store.read(key),run=r.stageRuns[eventId+'-intent'];if(run?.status!=='succeeded')return r;
  const event=r.events.find(x=>x.id===eventId);if(event.kind!=='input')return r;
  const v=JSON.parse(run.result.raw);
  return store.update(key,n=>{
   const e=n.events.find(x=>x.id===eventId);if(e.kind!=='input')return;
   requireValue(n.active===eventId&&n.stateRevision===run.stateRevision,'SHOP_STATE_STALE');
   if(v.kind==='purchase'){
    const result=purchaseResult(n.assets,n.rules,v);Object.assign(e,{...result,kind:'purchase',before:clone(n.assets),after:clone(result.assets)});delete e.assets;
    if(result.outcome==='committed'){n.assets=result.assets;n.stateRevision++;}
    e.stateAfter=n.stateRevision;e.narration.status=result.outcome==='committed'?'ready':'not-required';
    if(result.outcome!=='committed')n.active=null;
   }else if(v.kind==='clarify'){
    e.kind='clarify';e.outcome='clarify';e.narration={status:'complete',raw:v.message,error:null,attempts:[]};
    n.projection.push({eventId,input:e.input,raw:v.message,stateRevision:n.stateRevision});n.active=null;
   }else{e.kind='story';e.narration.status='ready';}
  });
 }
 async function executeProcess(key,eventId,recover=false){
  let r=await store.read(key),e=r.events.find(x=>x.id===eventId);requireValue(e,'SHOP_EVENT_NOT_FOUND');
  if(e.kind==='input'){await runner.run(key,eventId,'intent',recover);r=await resolveIntent(key,eventId);e=r.events.find(x=>x.id===eventId);}
  if(r.active===eventId&&e.kind!=='input'){
   if(needsLargeSummary(r)){
    await store.update(key,n=>{n.events.find(e=>e.id===eventId).pendingStage='summary-large';});
    r=await runner.run(key,eventId,'summary-large',recover);
    if(r.stageRuns[eventId+'-summary-large']?.status!=='succeeded')return view(r);
   }
   await store.update(key,n=>{n.events.find(e=>e.id===eventId).pendingStage='narration';});
   await runner.run(key,eventId,'narration',recover);
  }
  r=await store.read(key);
  if(r.active===eventId&&r.stageRuns[eventId+'-narration']?.status==='succeeded'){
   for(let p=nextMemoryStage(r,eventId);p;p=nextMemoryStage(r,eventId)){
    await store.update(key,n=>{n.events.find(e=>e.id===eventId).pendingStage=p;});
    r=await runner.run(key,eventId,p,recover);if(r.stageRuns[eventId+'-'+p]?.status!=='succeeded')return view(r);
   }
   r=await store.update(key,n=>{n.active=null;const e=n.events.find(e=>e.id===eventId);e.pendingStage=null;e.memoryCheckpoint=clone(n.memory);});
  }
  return view(r);
 }
 function process(key,eventId,recover=false){
  if(jobs.has(key))return jobs.get(key);
  const job=executeProcess(key,eventId,recover).finally(()=>jobs.delete(key));jobs.set(key,job);return job;
 }
 function schedule(key,eventId){
  void process(key,eventId).catch(async error=>{
   if(!String(error.code||'').startsWith('OFFLINE_'))await store.update(key,r=>{const e=r.events.find(e=>e.id===eventId);if(e&&r.active===eventId&&!Object.values(r.stageRuns).some(s=>s.eventId===eventId&&['pending','unknown'].includes(s.status))){e.narration.status='failed';e.narration.error=String(error.code||error.message).slice(0,160);}});
  }).catch(()=>{/* Storage failures are reconciled through the original record. */});
 }
 async function input(key,request){
  requireValue(exact(request,['operationId','revision','input'])&&typeof request.input==='string'&&request.input.trim()&&request.input.length<=20000,'SHOP_INPUT_INVALID');
  const apply=()=>store.change(key,request,'input',r=>{
   requireValue(r.enabled&&!r.active,'SHOP_TURN_PENDING',409);
   const direct=exactPurchase(request.input,r.rules.products);
   const e={id:request.operationId,kind:direct?'purchase':/(买|购买|来点|来些|带些|带点)/.test(request.input)?'input':'story',input:request.input,outcome:'story',before:clone(r.assets),after:clone(r.assets),stateBefore:r.stateRevision,stateAfter:r.stateRevision,at:new Date().toISOString(),ruleVersion:rules.version,narration:{status:'ready',raw:null,attempts:[],error:null}};
   if(direct){const result=purchaseResult(r.assets,r.rules,direct);Object.assign(e,result,{before:clone(r.assets),after:clone(result.assets)});delete e.assets;if(result.outcome==='committed'){r.assets=result.assets;r.stateRevision++;e.stateAfter=r.stateRevision;}else e.narration.status='not-required';}
   r.events.push(e);r.active=e.outcome==='rejected'?null:e.id;return e;
  });
  const prior=await store.read(key),result=prior.operations[request.operationId]?await apply():await authorize(key,request,apply);
  if(result.record.active===result.receipt.id)schedule(key,result.receipt.id);
  return view(result.record);
 }
 const api={
  store,runner,plugins,source,activate,process,view,
  async handle({pathname,method,data,query,send}){
   const prefix='/rpg-app/earth/api/shop';if(!pathname.startsWith(prefix))return false;
   const path=pathname.slice(prefix.length);
   if(path==='/status'&&method==='GET'){send(200,{available:true,mode:provider.mode,rules,binding,models:await provider.catalog(),adaptation:adaptationReport()});return true;}
   if(path==='/source'&&method==='GET'){const s=await source(query.get('id'));send(200,{id:s.id,revision:s.revision,pending:s.pending,initial:query.get('id')==='example'?initialAssets(rules):null,memory:{summaryEnabled:s.memory.summaryEnabled,memoryEnabled:s.memory.memoryEnabled},requiresConfirmation:true});return true;}
   if(path==='/activate'&&method==='POST'){send(201,await activate(data));return true;}
   if(path==='/preview-transcript'&&method==='POST'){
    requireValue(data?.format==='modelmirror.rpg.shop-transcript/1'&&typeof data.opening==='string'&&Array.isArray(data.turns)&&data.turns.length<=1000&&data.turns.every(t=>typeof t.input==='string'&&typeof t.raw==='string'&&t.raw.length<=200000),'SHOP_TRANSCRIPT_INVALID');
    send(200,{opening:data.opening,turns:data.turns.map(t=>({input:t.input,html:displayHtml(t.raw)})),readonly:true});return true;
   }
   const m=path.match(/^\/sessions\/(shop-[a-f0-9]{40})(?:\/(purchase|input|recover|retry|reconcile|skip|override|enabled|configure|fork|export))?$/);
   if(!m)throw fail('SHOP_ROUTE_UNKNOWN',404);
   const [,key,action]=m;
   if(method==='GET'&&!action){send(200,await view(await store.read(key)));return true;}
   if(method==='GET'&&action==='export'){const r=await store.read(key);send(200,{format:'modelmirror.rpg.shop-transcript/1',binding:r.binding,opening:r.session.characterText,events:r.events,turns:r.projection,usage:r.usage,stageRuns:r.stageRuns,measurement:aggregateMeasurements(Object.values(r.stageRuns).flatMap(s=>s.attempts))});return true;}
   requireValue(method==='POST','SHOP_METHOD_INVALID');
   let result;
   if(action==='purchase'){
    const prior=await store.read(key),recorded=prior.operations[data.operationId];
    if(recorded)result=await store.purchase(key,data);
    else result=await authorize(key,data,()=>store.purchase(key,data));
    if(result.record.active===result.receipt.id)schedule(key,result.receipt.id);
    send(200,await view(result.record));return true;
   }
   if(action==='input'){send(200,await input(key,data));return true;}
   if(action==='recover'){
    requireValue(exact(data,['eventId']));await store.reconcile(key);send(200,await process(key,data.eventId,true));return true;
   }
   if(action==='reconcile'){requireValue(exact(data,[])||exact(data,['operationId'])&&id(data.operationId));await store.reconcile(key);const r=await store.read(key);send(200,{...await view(r),operationRecorded:data.operationId?!!r.operations[data.operationId]:null});return true;}
   if(action==='retry'){result=await runner.retry(key,data);schedule(key,data.eventId);send(200,await view(result.record));return true;}
   if(action==='skip')result=await store.endReceiptOnly(key,data);
   else if(action==='override')result=await store.override(key,data);
   else if(action==='configure')result=await runner.configure(key,data);
   else if(action==='enabled'){
    result=await store.enabled(key,data);
    await grant(result.record.enabled?'enable':'disable',key,'grant-'+hash([key,data.operationId]).slice(0,24));
   }else if(action==='fork'){
    const child=await store.fork(key,data);await grant(child.enabled?'enable':'disable',child.id,'fork-enable-'+hash(child.id).slice(0,24));send(201,await view(child));return true;
   }else throw fail('SHOP_ROUTE_UNKNOWN',404);
   send(200,await view(result.record));return true;
  },
  async close(){await Promise.allSettled([...jobs.values()]);await plugins.close();await store.close();}
 };
 return api;
}

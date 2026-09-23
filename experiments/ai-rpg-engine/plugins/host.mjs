import {mkdir,open,readFile,rename,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {loadReviewedPlugins,PLUGIN_ID,MODEL_SELECTOR_ID,HISTORY_WINDOW_ID,ROLLING_SUMMARY_ID,MEMORY_PALACE_ID,REVIEWED_PLUGIN_IDS,canonical,sha,fail} from './catalog.mjs';

import {validConfig} from './history-window.mjs';
import {validSettings,validEdit} from './rolling-summary.mjs';
import {validChange as validMemoryChange,validSettings as validMemorySettings} from './memory-palace.mjs';

const id = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(value);
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === keys.slice().sort().join(',');
const bindingKeys = ['version','artifactSha256','manifestSha256','installationId'];
const sameBinding = (a,b) => a && b && ['version','artifactSha256','manifestSha256'].every(k=>a[k]===b[k]);
const isBinding = b => exact(b,bindingKeys) && /^\d+\.\d+\.\d+$/.test(b.version) && hash(b.artifactSha256) && hash(b.manifestSha256) && id(b.installationId);
const record = v => v && typeof v==='object' && !Array.isArray(v);
const emptyPlugin = () => ({installed:null,grants:{}});
const emptyState = () => ({format:2,revision:0,plugins:{},operations:{}});
const validPlugin = p => exact(p,['installed','grants']) && (p.installed===null||isBinding(p.installed)) && record(p.grants) &&
 Object.entries(p.grants).every(([key,g])=>id(key) && exact(g,['cardId','resourceHash','installationId','permissions','epoch','active']) &&
  id(g.cardId) && hash(g.resourceHash) && id(g.installationId) && id(g.epoch) && typeof g.active==='boolean' &&
  Array.isArray(g.permissions) && g.permissions.every(p=>typeof p==='string'));
function validState(s) {
 return exact(s,['format','revision','plugins','operations']) && s.format===2 && integer(s.revision) && record(s.plugins) &&
  Object.entries(s.plugins).every(([key,p])=>REVIEWED_PLUGIN_IDS.includes(key)&&validPlugin(p)) && record(s.operations) &&
  Object.entries(s.operations).every(([key,o])=>id(key) && exact(o,['hash','revision']) && hash(o.hash) && integer(o.revision) && o.revision<=s.revision);
}
function normalizeState(s) {
 if(s?.format===1){
  if(!exact(s,['format','revision','installed','grants','operations']) || !validPlugin({installed:s.installed,grants:s.grants}) ||
     Object.values(s.grants).some(g=>g.cardId!=='earth'))throw Error('invalid legacy');
  s={format:2,revision:s.revision,plugins:{[PLUGIN_ID]:{installed:s.installed,grants:s.grants}},operations:s.operations};
 }
 if(!validState(s))throw Error('invalid');
 return s;
}
// The loader is a trusted host injection, never a frontend/package entrypoint.
function entriesFrom(value) {
 const entries=new Map();
 for(const entry of Array.isArray(value)?value:[value]){
  const pluginId=entry?.manifest?.id||entry?.id;
  if(!REVIEWED_PLUGIN_IDS.includes(pluginId)||entries.has(pluginId))throw fail('PLUGIN_CATALOG_INVALID');
  entries.set(pluginId,entry);
 }
 return entries;
}
async function atomicWrite(path,value) {
 const temporary=path+'.'+randomUUID()+'.tmp';let file;
 try {file=await open(temporary,'wx',0o600);await file.writeFile(JSON.stringify(value,null,2)+'\n');await file.sync();await file.close();file=null;await rename(temporary,path);}
 finally {if(file)await file.close();await unlink(temporary).catch(e=>{if(e.code!=='ENOENT')throw e;});}
}
function outputValid(pluginId,capability,value,session,input) {
 if(pluginId===MEMORY_PALACE_ID){
  if(capability==='ui.memory-action')return exact(input,[])&&canonical(value)===canonical({kind:'memory-action',action:'memory-settings',label:'记忆宫殿'});
  if(capability==='session.memory.revise')return validMemoryChange(input)&&canonical(value)===canonical({kind:'memory-revision',sessionId:session.id,...input});
  if(capability==='session.memory.configure')return validMemorySettings(input)&&canonical(value)===canonical({kind:'memory-configuration',sessionId:session.id,...input});
  return capability==='session.memory.request'&&exact(input,[])&&canonical(value)===canonical({kind:'memory-request',sessionId:session.id});
 }
 if(pluginId===ROLLING_SUMMARY_ID){
  if(capability==='ui.summary-action')return exact(value,['kind','action','label'])&&value.kind==='summary-action'&&value.action==='summary-settings'&&value.label==='自动总结';
  if(capability==='session.summary.configure')return validSettings(input)&&canonical(value)===canonical({kind:'summary-configuration',sessionId:session.id,...input});
  if(capability==='session.summary.revise')return validEdit(input)&&canonical(value)===canonical({kind:'summary-revision',sessionId:session.id,...input});
  return capability==='session.summary.request'&&exact(input,[])&&canonical(value)===canonical({kind:'summary-request',sessionId:session.id});
 }
 if(pluginId===HISTORY_WINDOW_ID){
  if(capability==='ui.history-action')return exact(value,['kind','action','label'])&&value.kind==='history-action'&&value.action==='history-settings'&&value.label==='历史窗口';
  return capability==='session.history.configure'&&validConfig(input)&&exact(value,['kind','sessionId','turns','includeInitialCharacter'])&&value.kind==='history-configuration'&&value.sessionId===session.id&&value.turns===input.turns&&value.includeInitialCharacter===input.includeInitialCharacter;
 }
 if(pluginId===MODEL_SELECTOR_ID){
  if(capability==='ui.model-action')return exact(value,['kind','action','label']) && value.kind==='model-action' && value.action==='select-model' && value.label==='选择模型';
  if(capability==='model.catalog.read')return exact(value,['kind','sessionId']) && value.kind==='model-catalog-request' && value.sessionId===session.id;
  return capability==='session.model.select' && exact(value,['kind','sessionId','selectionId','selectionRevision']) &&
   value.kind==='model-selection-request' && value.sessionId===session.id && value.selectionId===input.selectionId && value.selectionRevision===input.selectionRevision &&
   typeof value.selectionId==='string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$/.test(value.selectionId) && integer(value.selectionRevision);
 }
 if(pluginId!==PLUGIN_ID)return false;
 if(capability==='ui.message-action')return exact(value,['kind','action','label','available']) && value.kind==='message-action' && value.action==='branch' && value.label==='分支' && value.available===(session.completedTurns>0);
 return exact(value,['kind','sessionId','turn','name']) && value.kind==='branch-request' && value.sessionId===session.id &&
  integer(value.turn) && value.turn>0 && value.turn<=session.completedTurns && typeof value.name==='string' && value.name.trim()===value.name && value.name.length>0 && value.name.length<=80;
}

// One owning host per directory. Arbitrary/plugin-supplied code is never loaded.
export async function createPluginService({directory,lookupSession,loadCatalog=loadReviewedPlugins,timeoutMs=1000}) {
 if(typeof lookupSession!=='function' || !Number.isInteger(timeoutMs) || timeoutMs<1 || timeoutMs>5000)throw fail('PLUGIN_HOST_CONFIGURATION');
 const entries=entriesFrom(await loadCatalog());await mkdir(directory,{recursive:true});
 const lockPath=join(directory,'owner.lock'),statePath=join(directory,'registry.json');let lock;
 try {lock=await open(lockPath,'wx',0o600);await lock.writeFile(JSON.stringify({pid:process.pid}));}
 catch(e){if(lock){await lock.close();await unlink(lockPath);}throw fail(e.code==='EEXIST'?'PLUGIN_STORE_ALREADY_OWNED':'PLUGIN_STORE_UNAVAILABLE');}
 let state,legacyBytes=null;
 try {try{const bytes=await readFile(statePath,'utf8');state=JSON.parse(bytes);if(state?.format===1)legacyBytes=bytes;}catch(e){if(e.code!=='ENOENT')throw e;state=emptyState();}state=normalizeState(state);}
 catch {await lock.close();await unlink(lockPath);throw fail('PLUGIN_STORE_INVALID');}
 let queue=Promise.resolve(),closed=false;
 const running=new Set(),tickets=new Map(),faults=new Map();
 const keyFor=(pluginId,sessionId)=>pluginId+':'+sessionId;
 const pluginState=pluginId=>state.plugins[pluginId]||emptyPlugin();
 function entryFor(pluginId){const e=entries.get(pluginId);if(!e)throw fail('PLUGIN_UNKNOWN',404);if(e.error)throw fail(e.error,503);return e;}
 async function preserveLegacy(){
  if(legacyBytes===null)return;
  const backup=join(directory,'registry.v1.json');let file;
  try{file=await open(backup,'wx',0o600);await file.writeFile(legacyBytes);await file.sync();}
  catch(e){if(e.code!=='EEXIST'||await readFile(backup,'utf8')!==legacyBytes)throw e;}
  finally{if(file)await file.close();}
 }
 const serial = action => {
  if(closed)return Promise.reject(fail('PLUGIN_HOST_CLOSED'));
  const next=queue.then(action);queue=next.catch(()=>{});return next;
 };
 async function sessionFor(sessionId,entry) {
  if(!id(sessionId))throw fail('PLUGIN_SESSION_INVALID',400);
  const s=await lookupSession(sessionId);
  if(!s || s.id!==sessionId || !integer(s.revision) || !integer(s.completedTurns) || !hash(s.resourceHash))throw fail('PLUGIN_SESSION_INVALID',400);
  if(!entry.manifest.compatibleCards.includes(s.cardId))throw fail('PLUGIN_CARD_INCOMPATIBLE');
  if(entry.manifest.id===MEMORY_PALACE_ID&&s.memoryPalaceCompatible!==true)throw fail('MEMORY_RUNTIME_INCOMPATIBLE');
  if(entry.manifest.id===ROLLING_SUMMARY_ID&&s.rollingSummaryCompatible!==true)throw fail('SUMMARY_RUNTIME_INCOMPATIBLE');
  if(entry.manifest.id===HISTORY_WINDOW_ID&&s.historyWindowCompatible!==true)throw fail('HISTORY_RUNTIME_INCOMPATIBLE');
  return s;
 }
 function authorized(s,entry) {
  const p=pluginState(entry.manifest.id),g=Object.hasOwn(p.grants,s.id)?p.grants[s.id]:null;
  if(!sameBinding(p.installed,{version:entry.manifest.version,...entry}))throw fail('PLUGIN_NOT_INSTALLED');
  if(!g?.active || g.installationId!==p.installed.installationId || g.cardId!==s.cardId || g.resourceHash!==s.resourceHash ||
      canonical(g.permissions)!==canonical(entry.manifest.permissions))throw fail('PLUGIN_NOT_AUTHORIZED',403);
  return g;
 }
 function publicCatalog(){return {revision:state.revision,plugins:[...entries].map(([pluginId,entry])=>{
  const p=pluginState(pluginId);
  return {...(entry.error?{id:pluginId,lastError:entry.error,available:false}:structuredClone(entry.manifest)),
   manifestSha256:entry.manifestSha256,installed:p.installed!==null,
   compatibleInstallation:!entry.error&&!!sameBinding(p.installed,{version:entry.manifest.version,...entry}),
   installedVersion:p.installed?.version||null,dataLocation:'当前 RPG 服务的本地插件数据目录',execution:'first-party-trusted; not a sandbox'};
 })};}
 const host={
  catalog:async()=>publicCatalog(),
  // Explicitly inactive is different from an unreadable/mismatched authorization.
  async historyAuthorization(sessionId){return host.contextAuthorization(sessionId,HISTORY_WINDOW_ID);},
  async memoryAuthorization(sessionId){return host.contextAuthorization(sessionId,MEMORY_PALACE_ID);},
  async summaryAuthorization(sessionId){return host.contextAuthorization(sessionId,ROLLING_SUMMARY_ID);},
  async contextAuthorization(sessionId,pluginId){
   if(![HISTORY_WINDOW_ID,ROLLING_SUMMARY_ID,MEMORY_PALACE_ID].includes(pluginId))throw fail('PLUGIN_UNKNOWN',404);
   if(closed)throw fail('PLUGIN_HOST_CLOSED');
   const entry=entryFor(pluginId),s=await sessionFor(sessionId,entry);
   const p=pluginState(pluginId),g=Object.hasOwn(p.grants,sessionId)?p.grants[sessionId]:null;
   let enabled=false;
   if(p.installed){
    if(!sameBinding(p.installed,{version:entry.manifest.version,...entry}))throw fail('PLUGIN_VERSION_MISMATCH');
    if(g?.active){authorized(s,entry);enabled=true;}
   }else if(g?.active)throw fail('PLUGIN_AUTHORIZATION_UNKNOWN');
   return {enabled,revision:sha(canonical({installed:p.installed,grant:g,resourceHash:s.resourceHash}))};
  },
  // Serialize the final authorization check and synchronous network start against
  // revoke/uninstall. Return the response promise boxed: do not hold the queue
  // while the model is generating, so cancellation/revocation stays available.
  async startHistoryDispatch(sessionId,expectedAuthorizationRevision,start){
   if(!hash(expectedAuthorizationRevision)||typeof start!=='function')throw fail('HISTORY_DISPATCH_GUARD_REQUIRED');
   return serial(async()=>{
    const current=await host.historyAuthorization(sessionId);
    if(current.revision!==expectedAuthorizationRevision)throw fail('HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH');
    return {response:start()};
   });
  },
  async guardSummaryTask(sessionId,expectedRevision,action){
   if(!hash(expectedRevision)||typeof action!=='function')throw fail('SUMMARY_DISPATCH_GUARD_REQUIRED');
   return serial(async()=>{const auth=await host.summaryAuthorization(sessionId);if(auth.revision!==expectedRevision)throw fail('SUMMARY_AUTHORIZATION_CHANGED');return action(auth);});
  },
  // One registry queue fences all context grants against final dispatch/publication.
  async guardMemoryTask(sessionId,expected,action){
   if(!exact(expected,['memory','summary','history'])||Object.values(expected).some(a=>!a||!hash(a.revision))||typeof action!=='function')throw fail('MEMORY_DISPATCH_GUARD_REQUIRED');
   return serial(async()=>{
    for(const [name,id] of [['memory',MEMORY_PALACE_ID],['summary',ROLLING_SUMMARY_ID],['history',HISTORY_WINDOW_ID]]){
     const a=await host.contextAuthorization(sessionId,id);if(a.revision!==expected[name].revision)throw fail('MEMORY_AUTHORIZATION_CHANGED');
    }
    return action();
   });
  },
  async sessionStatus(sessionId,pluginId=PLUGIN_ID){
   const entry=entryFor(pluginId),s=await sessionFor(sessionId,entry);let enabled=false;
   try{authorized(s,entry);enabled=true;}catch{}
   return {pluginId,registryRevision:state.revision,sessionRevision:s.revision,installed:!!pluginState(pluginId).installed,enabled,lastError:faults.get(keyFor(pluginId,sessionId))||null};
  },
  async change(action,payload){
   const sessionAction=['enable','disable'].includes(action);
   const fields=['operationId','pluginId','version','artifactSha256','manifestSha256','expectedRegistryRevision',...(sessionAction?['sessionId','expectedSessionRevision']:[]),...(action==='enable'?['permissions']:[])];
   if(!['install','uninstall','enable','disable'].includes(action) || !exact(payload,fields) || !id(payload.operationId) || !REVIEWED_PLUGIN_IDS.includes(payload.pluginId) ||
      !integer(payload.expectedRegistryRevision) || !hash(payload.artifactSha256) || !hash(payload.manifestSha256) || typeof payload.version!=='string' ||
      (sessionAction&&(!id(payload.sessionId)||!integer(payload.expectedSessionRevision))))throw fail('PLUGIN_OPERATION_INVALID',400);
   const input=structuredClone(payload),operationHash=sha(canonical({action,payload:input}));
   return serial(async()=>{
    const prior=Object.hasOwn(state.operations,input.operationId)?state.operations[input.operationId]:null;
    if(prior){if(prior.hash!==operationHash)throw fail('PLUGIN_OPERATION_COLLISION');return {operationId:input.operationId,revision:prior.revision,replayed:true};}
    const pluginId=input.pluginId,p=pluginState(pluginId),entry=entries.get(pluginId);
    if(!entry)throw fail('PLUGIN_UNKNOWN',404);
    if(input.expectedRegistryRevision!==state.revision)throw fail('PLUGIN_REGISTRY_CONFLICT');
    if(Object.keys(state.operations).length>=10000)throw fail('PLUGIN_OPERATION_LIMIT');
    if(['install','enable'].includes(action)){
     const fresh=entriesFrom(await loadCatalog()).get(pluginId);
     if(!fresh||fresh.error||entry.error)throw fail('PLUGIN_PACKAGE_UNAVAILABLE',503);
     if(!sameBinding(input,{version:fresh.manifest.version,...fresh}) || !sameBinding(input,{version:entry.manifest.version,...entry}))throw fail('PLUGIN_VERSION_MISMATCH');
    }else if(!sameBinding(input,p.installed))throw fail('PLUGIN_VERSION_MISMATCH');
    const next=structuredClone(state);next.plugins[pluginId]??=emptyPlugin();const target=next.plugins[pluginId];
    if(action==='install'){
     if(p.installed)throw fail('PLUGIN_ALREADY_INSTALLED');
     target.installed={version:input.version,artifactSha256:input.artifactSha256,manifestSha256:input.manifestSha256,installationId:randomUUID()};
    }else if(action==='uninstall'){
     target.installed=null;for(const g of Object.values(target.grants)){g.active=false;g.epoch=randomUUID();}
    }else{
     const s=await sessionFor(input.sessionId,entryFor(pluginId));
     if(s.revision!==input.expectedSessionRevision)throw fail('PLUGIN_SESSION_CONFLICT');
     if(!sameBinding(input,p.installed))throw fail('PLUGIN_NOT_INSTALLED');
     if(action==='enable'){
      if(s.busy||s.pending)throw fail('PLUGIN_SESSION_BUSY');
      if(canonical(input.permissions)!==canonical(entry.manifest.permissions))throw fail('PLUGIN_PERMISSION_MISMATCH',403);
     }
     target.grants[input.sessionId]={cardId:s.cardId,resourceHash:s.resourceHash,installationId:p.installed.installationId,permissions:entry.manifest.permissions.slice(),epoch:randomUUID(),active:action==='enable'};
    }
    next.revision++;next.operations[input.operationId]={hash:operationHash,revision:next.revision};
    try{await preserveLegacy();await atomicWrite(statePath,next);}catch{throw fail('PLUGIN_PERSIST_FAILED',503);}
    state=next;legacyBytes=null;
    return {operationId:input.operationId,revision:state.revision,replayed:false};
   });
  },
  async invoke({pluginId=PLUGIN_ID,sessionId,capability,input={}}){
   if(closed)throw fail('PLUGIN_HOST_CLOSED');
   const entry=entryFor(pluginId),key=keyFor(pluginId,sessionId);
   if(!entry.manifest.capabilities.includes(capability))throw fail('PLUGIN_CAPABILITY_DENIED',403);
   if(running.has(key))throw fail('PLUGIN_INVOKE_BUSY');
   running.add(key);let timer;const controller=new AbortController();
   try{
    const s=await sessionFor(sessionId,entry),g=authorized(s,entry);
    if(s.busy||s.pending)throw fail('PLUGIN_SESSION_BUSY');
    const lease={sessionId,sessionRevision:s.revision,resourceHash:s.resourceHash,installationId:pluginState(pluginId).installed.installationId,epoch:g.epoch};
    let cleanInput;try{cleanInput=JSON.parse(canonical(input));if(canonical(cleanInput).length>(pluginId===MEMORY_PALACE_ID?16384:4096))throw Error();}catch{throw fail('PLUGIN_INPUT_INVALID',400);}
    const safeSession={id:s.id,revision:s.revision,completedTurns:s.completedTurns};
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(fail('PLUGIN_TIMEOUT'));},timeoutMs);});
    const value=await Promise.race([Promise.resolve().then(()=>entry.invoke({capability,session:structuredClone(safeSession),input:cleanInput,signal:controller.signal})).catch(()=>{throw fail('PLUGIN_EXECUTION_FAILED');}),timeout]);
    let proposal;try{proposal=JSON.parse(canonical(value));}catch{throw fail('PLUGIN_OUTPUT_INVALID');}
    if(!outputValid(pluginId,capability,proposal,safeSession,cleanInput))throw fail('PLUGIN_OUTPUT_INVALID');
    const result={ticket:randomUUID(),pluginId,version:entry.manifest.version,artifactSha256:entry.artifactSha256,manifestSha256:entry.manifestSha256,capability,...lease,proposal};
    if(!tickets.has(pluginId))tickets.set(pluginId,new Map());const issued=tickets.get(pluginId);
    issued.set(result.ticket,sha(canonical(result)));if(issued.size>64)issued.delete(issued.keys().next().value);
    await host.validateResult(result);faults.delete(key);return result;
   }catch(e){const code=typeof e.code==='string'&&e.code.startsWith('PLUGIN_')?e.code:'PLUGIN_EXECUTION_FAILED';faults.set(key,code);throw fail(code,e.status||409);}
   finally{clearTimeout(timer);controller.abort();running.delete(key);}
  },
  async validateResult(result){
   const issued=tickets.get(result?.pluginId);
   if(closed||!result||!issued?.has(result.ticket)||issued.get(result.ticket)!==sha(canonical(result)))throw fail('PLUGIN_RESULT_INVALID');
   const entry=entryFor(result.pluginId),s=await sessionFor(result.sessionId,entry),g=authorized(s,entry);
   if(s.revision!==result.sessionRevision||s.resourceHash!==result.resourceHash||g.epoch!==result.epoch||pluginState(result.pluginId).installed.installationId!==result.installationId||s.busy||s.pending)throw fail('PLUGIN_RESULT_STALE');
   return structuredClone(result);
  },
  async commitResult(result,commit){
   if(typeof commit!=='function'||!['session.branch.prepare','session.model.select','session.history.configure','session.summary.configure','session.summary.revise','session.summary.request','session.memory.revise','session.memory.configure','session.memory.request'].includes(result?.capability))throw fail('PLUGIN_COMMIT_INVALID');
   return serial(async()=>{await host.validateResult(result);try{return await commit();}finally{tickets.get(result.pluginId)?.delete(result.ticket);}});
  },
  async close(){if(closed)return;closed=true;await queue;tickets.clear();await lock.close();await unlink(lockPath);},
 };
 return host;
}

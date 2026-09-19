import {mkdir,open,readFile,rename,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {loadReviewedCatalog,PLUGIN_ID,canonical,sha,fail} from './catalog.mjs';

const id = value => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(value);
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === keys.slice().sort().join(',');
const bindingKeys = ['version','artifactSha256','manifestSha256','installationId'];
const sameBinding = (a,b) => a && b && ['version','artifactSha256','manifestSha256'].every(k=>a[k]===b[k]);
const isBinding = b => exact(b,bindingKeys) && /^\d+\.\d+\.\d+$/.test(b.version) && hash(b.artifactSha256) && hash(b.manifestSha256) && id(b.installationId);
const emptyState = () => ({format:1,revision:0,installed:null,grants:{},operations:{}});
function validState(s) {
 return exact(s,['format','revision','installed','grants','operations']) && s.format===1 && integer(s.revision) &&
  (s.installed===null || isBinding(s.installed)) && exact(s.grants,Object.keys(s.grants||{})) && exact(s.operations,Object.keys(s.operations||{})) &&
  Object.entries(s.grants).every(([key,g])=>id(key) && exact(g,['cardId','resourceHash','installationId','permissions','epoch','active']) &&
    g.cardId==='earth' && hash(g.resourceHash) && id(g.installationId) && id(g.epoch) && typeof g.active==='boolean' &&
    Array.isArray(g.permissions) && g.permissions.every(p=>typeof p==='string')) &&
  Object.entries(s.operations).every(([key,o])=>id(key) && exact(o,['hash','revision']) && hash(o.hash) && integer(o.revision) && o.revision<=s.revision);
}
async function atomicWrite(path,value) {
 const temporary=path+'.'+randomUUID()+'.tmp';let file;
 try {file=await open(temporary,'wx',0o600);await file.writeFile(JSON.stringify(value,null,2)+'\n');await file.sync();await file.close();file=null;await rename(temporary,path);}
 finally {if(file)await file.close();await unlink(temporary).catch(e=>{if(e.code!=='ENOENT')throw e;});}
}
function outputValid(capability,value,session) {
 if(capability==='ui.message-action')return exact(value,['kind','action','label','available']) && value.kind==='message-action' && value.action==='branch' && value.label==='分支' && value.available===(session.completedTurns>0);
 return exact(value,['kind','sessionId','turn','name']) && value.kind==='branch-request' && value.sessionId===session.id &&
  integer(value.turn) && value.turn>0 && value.turn<=session.completedTurns && typeof value.name==='string' && value.name.trim()===value.name && value.name.length>0 && value.name.length<=80;
}

// One owning host per directory. Arbitrary/plugin-supplied code is never loaded.
export async function createPluginService({directory,lookupSession,loadCatalog=loadReviewedCatalog,timeoutMs=1000}) {
 if(typeof lookupSession!=='function' || !Number.isInteger(timeoutMs) || timeoutMs<1 || timeoutMs>5000)throw fail('PLUGIN_HOST_CONFIGURATION');
 const entry=await loadCatalog();await mkdir(directory,{recursive:true});
 const lockPath=join(directory,'owner.lock'),statePath=join(directory,'registry.json');let lock;
 try {lock=await open(lockPath,'wx',0o600);await lock.writeFile(JSON.stringify({pid:process.pid}));}
 catch(e){if(lock){await lock.close();await unlink(lockPath);}throw fail(e.code==='EEXIST'?'PLUGIN_STORE_ALREADY_OWNED':'PLUGIN_STORE_UNAVAILABLE');}
 let state;
 try {try{state=JSON.parse(await readFile(statePath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;state=emptyState();}if(!validState(state))throw Error('invalid');}
 catch {await lock.close();await unlink(lockPath);throw fail('PLUGIN_STORE_INVALID');}
 let queue=Promise.resolve(),closed=false;
 const running=new Set(),tickets=new Map(),faults=new Map();
 const serial = action => {
  if(closed)return Promise.reject(fail('PLUGIN_HOST_CLOSED'));
  const next=queue.then(action);queue=next.catch(()=>{});return next;
 };
 async function sessionFor(sessionId) {
  if(!id(sessionId))throw fail('PLUGIN_SESSION_INVALID',400);
  const s=await lookupSession(sessionId);
  if(!s || s.id!==sessionId || !integer(s.revision) || !integer(s.completedTurns) || !hash(s.resourceHash))throw fail('PLUGIN_SESSION_INVALID',400);
  if(!entry.manifest.compatibleCards.includes(s.cardId))throw fail('PLUGIN_CARD_INCOMPATIBLE');
  return s;
 }
 function authorized(s) {
  const g=Object.hasOwn(state.grants,s.id)?state.grants[s.id]:null;
  if(!sameBinding(state.installed,{version:entry.manifest.version,...entry}))throw fail('PLUGIN_NOT_INSTALLED');
  if(!g?.active || g.installationId!==state.installed.installationId || g.cardId!==s.cardId || g.resourceHash!==s.resourceHash ||
      canonical(g.permissions)!==canonical(entry.manifest.permissions))throw fail('PLUGIN_NOT_AUTHORIZED',403);
  return g;
 }
 function publicCatalog(){return {revision:state.revision,plugins:[{...structuredClone(entry.manifest),manifestSha256:entry.manifestSha256,installed:state.installed!==null,compatibleInstallation:!!sameBinding(state.installed,{version:entry.manifest.version,...entry}),installedVersion:state.installed?.version||null,dataLocation:'当前 RPG 服务的本地插件数据目录',execution:'first-party-trusted; not a sandbox'}]};}
 const host={
  catalog:async()=>publicCatalog(),
  async sessionStatus(sessionId){const s=await sessionFor(sessionId);let enabled=false;try{authorized(s);enabled=true;}catch{}return {pluginId:PLUGIN_ID,registryRevision:state.revision,sessionRevision:s.revision,installed:!!state.installed,enabled,lastError:faults.get(sessionId)||null};},
  async change(action,payload){
   const sessionAction=['enable','disable'].includes(action);
   const fields=['operationId','pluginId','version','artifactSha256','manifestSha256','expectedRegistryRevision',...(sessionAction?['sessionId','expectedSessionRevision']:[]),...(action==='enable'?['permissions']:[])];
   if(!['install','uninstall','enable','disable'].includes(action) || !exact(payload,fields) || !id(payload.operationId) || payload.pluginId!==PLUGIN_ID ||
      !integer(payload.expectedRegistryRevision) || !hash(payload.artifactSha256) || !hash(payload.manifestSha256) || typeof payload.version!=='string' ||
      (sessionAction&&(!id(payload.sessionId)||!integer(payload.expectedSessionRevision))))throw fail('PLUGIN_OPERATION_INVALID',400);
   const input=structuredClone(payload),operationHash=sha(canonical({action,payload:input}));
   return serial(async()=>{
    const prior=Object.hasOwn(state.operations,input.operationId)?state.operations[input.operationId]:null;
    if(prior){if(prior.hash!==operationHash)throw fail('PLUGIN_OPERATION_COLLISION');return {operationId:input.operationId,revision:prior.revision,replayed:true};}
    if(input.expectedRegistryRevision!==state.revision)throw fail('PLUGIN_REGISTRY_CONFLICT');
    if(Object.keys(state.operations).length>=10000)throw fail('PLUGIN_OPERATION_LIMIT');
    if(['install','enable'].includes(action)){
     const fresh=await loadCatalog();
     if(!sameBinding(input,{version:fresh.manifest.version,...fresh}) || !sameBinding(input,{version:entry.manifest.version,...entry}))throw fail('PLUGIN_VERSION_MISMATCH');
    }else if(!sameBinding(input,state.installed))throw fail('PLUGIN_VERSION_MISMATCH');
    const next=structuredClone(state);
    if(action==='install'){
     if(state.installed)throw fail('PLUGIN_ALREADY_INSTALLED');
     next.installed={version:input.version,artifactSha256:input.artifactSha256,manifestSha256:input.manifestSha256,installationId:randomUUID()};
    }else if(action==='uninstall'){
     next.installed=null;for(const g of Object.values(next.grants)){g.active=false;g.epoch=randomUUID();}
    }else{
     const s=await sessionFor(input.sessionId);
     if(s.revision!==input.expectedSessionRevision)throw fail('PLUGIN_SESSION_CONFLICT');
     if(!sameBinding(input,state.installed))throw fail('PLUGIN_NOT_INSTALLED');
     if(action==='enable'){
      if(s.busy||s.pending)throw fail('PLUGIN_SESSION_BUSY');
      if(canonical(input.permissions)!==canonical(entry.manifest.permissions))throw fail('PLUGIN_PERMISSION_MISMATCH',403);
     }
     next.grants[input.sessionId]={cardId:s.cardId,resourceHash:s.resourceHash,installationId:state.installed.installationId,permissions:entry.manifest.permissions.slice(),epoch:randomUUID(),active:action==='enable'};
    }
    next.revision++;next.operations[input.operationId]={hash:operationHash,revision:next.revision};
    try{await atomicWrite(statePath,next);}catch{throw fail('PLUGIN_PERSIST_FAILED',503);}
    state=next;
    return {operationId:input.operationId,revision:state.revision,replayed:false};
   });
  },
  async invoke({sessionId,capability,input={}}){
   if(closed)throw fail('PLUGIN_HOST_CLOSED');
   if(!entry.manifest.capabilities.includes(capability))throw fail('PLUGIN_CAPABILITY_DENIED',403);
   if(running.has(sessionId))throw fail('PLUGIN_INVOKE_BUSY');
   running.add(sessionId);let timer;const controller=new AbortController();
   try{
    const s=await sessionFor(sessionId),g=authorized(s);
    if(s.busy||s.pending)throw fail('PLUGIN_SESSION_BUSY');
    const lease={sessionId,sessionRevision:s.revision,resourceHash:s.resourceHash,installationId:state.installed.installationId,epoch:g.epoch};
    let cleanInput;try{cleanInput=JSON.parse(canonical(input));if(canonical(cleanInput).length>4096)throw Error();}catch{throw fail('PLUGIN_INPUT_INVALID',400);}
    const safeSession={id:s.id,revision:s.revision,completedTurns:s.completedTurns};
    const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(fail('PLUGIN_TIMEOUT'));},timeoutMs);});
    const value=await Promise.race([Promise.resolve().then(()=>entry.invoke({capability,session:structuredClone(safeSession),input:cleanInput,signal:controller.signal})).catch(()=>{throw fail('PLUGIN_EXECUTION_FAILED');}),timeout]);
    let proposal;try{proposal=JSON.parse(canonical(value));}catch{throw fail('PLUGIN_OUTPUT_INVALID');}
    if(!outputValid(capability,proposal,safeSession))throw fail('PLUGIN_OUTPUT_INVALID');
    const result={ticket:randomUUID(),pluginId:PLUGIN_ID,version:entry.manifest.version,artifactSha256:entry.artifactSha256,manifestSha256:entry.manifestSha256,capability,...lease,proposal};
    tickets.set(result.ticket,sha(canonical(result)));if(tickets.size>64)tickets.delete(tickets.keys().next().value);
    await host.validateResult(result);faults.delete(sessionId);return result;
   }catch(e){const code=typeof e.code==='string'&&e.code.startsWith('PLUGIN_')?e.code:'PLUGIN_EXECUTION_FAILED';faults.set(sessionId,code);throw fail(code,e.status||409);}
   finally{clearTimeout(timer);controller.abort();running.delete(sessionId);}
  },
  async validateResult(result){
   if(closed||!result||!tickets.has(result.ticket)||tickets.get(result.ticket)!==sha(canonical(result)))throw fail('PLUGIN_RESULT_INVALID');
   const s=await sessionFor(result.sessionId),g=authorized(s);
   if(s.revision!==result.sessionRevision||s.resourceHash!==result.resourceHash||g.epoch!==result.epoch||state.installed.installationId!==result.installationId||s.busy||s.pending)throw fail('PLUGIN_RESULT_STALE');
   return structuredClone(result);
  },
  async commitResult(result,commit){
   if(typeof commit!=='function'||result?.capability!=='session.branch.prepare')throw fail('PLUGIN_COMMIT_INVALID');
   return serial(async()=>{await host.validateResult(result);try{return await commit();}finally{tickets.delete(result.ticket);}});
  },
  async close(){if(closed)return;closed=true;await queue;tickets.clear();await lock.close();await unlink(lockPath);},
 };
 return host;
}

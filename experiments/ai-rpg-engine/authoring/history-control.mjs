import {readFile, lstat, rename, open} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SessionRecords} from './session-storage.mjs';
import {canonical, keys, isId, isHash, sha256, fail} from './contracts.mjs';
import {createAuthoringPluginBridge} from './runtime-plugins.mjs';
import {DEFAULT_CONFIG, validConfig, selectHistory} from '../plugins/history-window.mjs';

export const HISTORY_MODE = 'offline-fixture-context';
export const HISTORY_PLUGIN_ID = 'rpg.history-window';
export const HISTORY_CHANGE_LIMIT = 1000;
const digest = value => sha256(canonical(value));
const same = (a,b) => canonical(a) === canonical(b);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const controls = new WeakSet();
export const emptyHistoryState = (initialConfig=null) => ({revision:0,config:{...(initialConfig??DEFAULT_CONFIG)},changes:[],...(initialConfig?{initialConfig:structuredClone(initialConfig)}:{})});

function authorizationValue(value) {
  keys(value,['history','summary','memory']);
  for (const item of Object.values(value)) {
    keys(item,['enabled','revision']);
    if (typeof item.enabled !== 'boolean' || !isHash(item.revision)) fail('HISTORY_AUTHORIZATION_INVALID');
  }
  return value;
}
export function contextPolicyRevision(history, authorization) {
  authorizationValue(authorization);
  return digest({revision:history.revision,config:history.config,authorization});
}
export function validateHistoryIntent(input) {
  canonical(input); keys(input,['sessionId','operationId','expectedRevision','expectedHistoryRevision','expectedContextPolicyRevision','config']);
  if (!/^session-[a-f0-9]{64}$/.test(input.sessionId) || !isId(input.operationId) || !integer(input.expectedRevision) ||
      !integer(input.expectedHistoryRevision) || !isHash(input.expectedContextPolicyRevision) || !validConfig(input.config)) fail('HISTORY_CONFIGURATION_INVALID');
  return structuredClone(input);
}
export function historyStateAt(history, revision) {
  if (!integer(revision) || revision > history.revision) fail('HISTORY_REQUEST_CORRUPT');
  return {revision,config:revision ? history.changes[revision-1].intent.config : {...(history.initialConfig??DEFAULT_CONFIG)},...(history.initialConfig?{initialConfig:structuredClone(history.initialConfig)}:{})};
}
export function validateHistoryState(state, sessionId, sessionRevision, resourceHash, initialConfig=null) {
  keys(state,['revision','config','changes',...(initialConfig?['initialConfig']:[])]);
  if(initialConfig&&(!validConfig(initialConfig)||!same(state.initialConfig,initialConfig)))fail('HISTORY_STATE_CORRUPT');
  if (!integer(state.revision) || !Array.isArray(state.changes) || state.changes.length !== state.revision ||
      state.revision > HISTORY_CHANGE_LIMIT || !validConfig(state.config)) fail('HISTORY_STATE_CORRUPT');
  const ids=new Set(); let previous=emptyHistoryState(initialConfig), lastSessionRevision=-1;
  for (const [i,change] of state.changes.entries()) {
    keys(change,['revision','intent','intentHash','authorization','receipt']);
    const intent=validateHistoryIntent(change.intent), receipt=change.receipt;
    authorizationValue(change.authorization);
    keys(receipt,['pluginId','version','artifactSha256','manifestSha256','capability','sessionId','sessionRevision','resourceHash','installationId','epoch','proposal']);
    if (change.revision !== i+1 || intent.sessionId !== sessionId || intent.expectedHistoryRevision !== i ||
        intent.expectedRevision <= lastSessionRevision || intent.expectedRevision >= sessionRevision || ids.has(intent.operationId) ||
        change.intentHash !== digest(intent) || !change.authorization.history.enabled ||
        contextPolicyRevision(previous,change.authorization) !== intent.expectedContextPolicyRevision ||
        receipt.pluginId !== HISTORY_PLUGIN_ID || receipt.version !== '2.0.0' || receipt.capability !== 'session.history.configure' ||
        receipt.sessionId !== sessionId || receipt.sessionRevision !== intent.expectedRevision || receipt.resourceHash !== resourceHash || !isId(receipt.epoch) || !isId(receipt.installationId) ||
        ![receipt.artifactSha256,receipt.manifestSha256,receipt.resourceHash].every(isHash) ||
        !same(receipt.proposal,{kind:'history-configuration',sessionId,...intent.config})) fail('HISTORY_STATE_CORRUPT');
    ids.add(intent.operationId);lastSessionRevision=intent.expectedRevision;
    previous={revision:change.revision,config:intent.config};
  }
  if (!same(state.config,previous.config)) fail('HISTORY_STATE_CORRUPT');
  return state;
}
export function selectAuthoringHistory(historyState, authorization, turns, openingText) {
  authorizationValue(authorization);
  if (authorization.summary.enabled || authorization.memory.enabled) fail('AUTHORING_PLUGIN_EXECUTION_UNAVAILABLE');
  const selected=selectHistory({history:turns.flatMap(t=>[{role:'user',content:t.userMessage},{role:'assistant',content:t.assistant}]),
    characterText:openingText,enabled:authorization.history.enabled,config:historyState.config});
  return {history:selected.history,policy:{mode:HISTORY_MODE,revision:contextPolicyRevision(historyState,authorization),
    history:authorization.history.enabled?'window':'full',pluginExecution:authorization.history.enabled,billedCalls:0,
    configurationRevision:historyState.revision,authorization:structuredClone(authorization),selection:selected.record}};
}
export function replayAuthoringHistory(state, policy, turns, openingText, intent) {
  keys(policy,['mode','revision','history','pluginExecution','billedCalls','configurationRevision','authorization','selection']);
  const at=historyStateAt(state,policy.configurationRevision);
  const selected=selectAuthoringHistory(at,policy.authorization,turns,openingText);
  if (!same(selected.policy,policy) || policy.revision !== intent.expectedContextPolicyRevision ||
      state.changes.some(c=>(c.revision<=at.revision)!==(c.intent.expectedRevision<intent.expectedRevision))) fail('HISTORY_REQUEST_CORRUPT');
  return selected.history;
}
export function requireHistoryControl(control) {
  if (!controls.has(control)) fail('HISTORY_CONTROL_HOST_REQUIRED');
  return control;
}

// Host-only factory: the grant registry is the existing reviewed plugin service.
// A document, HTTP payload or arbitrary callback cannot manufacture this controller.
export async function createAuthoringHistoryControl({directory,lookupSession,resolveRuntime,recoverDeadOwner=false}) {
  // A separate host lease permits explicit, identity-checked dead-owner recovery
  // without changing the reviewed registry or silently reclaiming a live owner.
  const lease=await SessionRecords.open(join(directory,'ownership'),{recoverDeadOwner,scope:'authoring-history-control'});
  let service;
  try {
    const registry=join(directory,'registry'), lock=join(registry,'owner.lock');
    let registryStat;try{registryStat=await lstat(registry);}catch(error){if(error.code!=='ENOENT')throw error;}
    if(registryStat&&(!registryStat.isDirectory()||registryStat.isSymbolicLink()))fail('HISTORY_REGISTRY_UNSAFE');
    let stat;try{stat=await lstat(lock);}catch(error){if(error.code!=='ENOENT')throw error;}
    if(stat){
      if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||stat.size>1024)fail('HISTORY_OWNER_UNKNOWN');
      const bytes=await readFile(lock), prior=JSON.parse(bytes.toString('utf8'));keys(prior,['pid']);
      if(!Number.isSafeInteger(prior.pid)||prior.pid<1)fail('HISTORY_OWNER_UNKNOWN');
      try{process.kill(prior.pid,0);fail('HISTORY_STORE_BUSY');}
      catch(error){if(error.code!=='ESRCH'){if(error.code==='HISTORY_STORE_BUSY')throw error;fail('HISTORY_OWNER_UNKNOWN');}}
      if(!recoverDeadOwner)fail('HISTORY_OWNER_RECOVERY_REQUIRED');
      if(!bytes.equals(await readFile(lock)))fail('HISTORY_OWNER_CHANGED');
      await rename(lock,join(registry,'owner-dead-'+randomUUID()+'.json'));
    }
    const bridge=await createAuthoringPluginBridge({directory:registry,lookupSession,resolveRuntime});service=bridge.pluginService;
  } catch(error){await lease.close();throw error;}
  const readAuthorization=async id=>({history:await service.historyAuthorization(id),summary:await service.summaryAuthorization(id),memory:await service.memoryAuthorization(id)});
  const control={
    pluginService:service,
    // Read the original registry receipt; never call change() to discover it.
    // The service queue fences the read, and disk/memory revision disagreement
    // requires an explicit restart rather than reporting a possibly stale grant.
    async operationReceipt(sessionId, action, payload) {
      const auth=await readAuthorization(sessionId);
      return service.guardMemoryTask(sessionId,auth,async()=>{
        const file=join(directory,'registry','registry.json');
        let stat;try{stat=await lstat(file);}catch(error){if(error.code!=='ENOENT')throw error;}
        const catalog=await service.catalog();
        if(!stat){if(catalog.revision!==0)fail('HISTORY_REGISTRY_CORRUPT');return null;}
        if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||stat.size>32*1024*1024)fail('HISTORY_REGISTRY_UNSAFE');
        const handle=await open(file,'r');
        let bytes;
        try{
          const current=await handle.stat();
          if(current.ino!==stat.ino||current.dev!==stat.dev||current.nlink!==1)fail('HISTORY_REGISTRY_UNSAFE');
          bytes=Buffer.alloc(stat.size+1);
          let size=0;
          while(size<bytes.length){const part=await handle.read(bytes,size,bytes.length-size,null);if(!part.bytesRead)break;size+=part.bytesRead;}
          if(size!==stat.size)fail('HISTORY_REGISTRY_CORRUPT');bytes=bytes.subarray(0,size);
        }finally{await handle.close();}
        const state=JSON.parse(bytes.toString('utf8'));
        keys(state,['format','revision','plugins','operations']);
        if(state.format!==2||state.revision!==catalog.revision)fail('HISTORY_REGISTRY_RESTART_REQUIRED');
        const prior=Object.hasOwn(state.operations,payload.operationId)?state.operations[payload.operationId]:null;
        if(!prior)return null;
        keys(prior,['hash','revision']);
        if(prior.hash!==digest({action,payload}))fail('HISTORY_OPERATION_COLLISION');
        if(!integer(prior.revision)||prior.revision>state.revision)fail('HISTORY_REGISTRY_CORRUPT');
        return {operationId:payload.operationId,revision:prior.revision};
      });
    },
    async authorization(id) {
      const auth=await readAuthorization(id);
      return service.guardMemoryTask(id,auth,()=>structuredClone(auth));
    },
    async commitConfiguration(input, history, commit) {
      const result=await service.invoke({pluginId:HISTORY_PLUGIN_ID,sessionId:input.sessionId,capability:'session.history.configure',input:input.config});
      return service.commitResult(result,async()=>{
        // commitResult holds the registry queue while session CAS is persisted.
        const authorization=await readAuthorization(input.sessionId);
        if (contextPolicyRevision(history,authorization)!==input.expectedContextPolicyRevision) fail('HISTORY_POLICY_CONFLICT');
        const {ticket,...receipt}=result;
        return commit({revision:history.revision+1,intent:input,intentHash:digest(input),authorization,receipt});
      });
    },
    async startDispatch(id, authorization, start) {
      // Keep grant mutations available while the provider is running. A boxed
      // promise preserves the existing dispatch guard's synchronous-start fence.
      try { return await service.guardMemoryTask(id,authorization,()=>({response:start()})); }
      catch (error) { if (error.code==='MEMORY_AUTHORIZATION_CHANGED') fail('HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH'); throw error; }
    },
    async close(){try{await service.close();}finally{await lease.close();}},
  };
  controls.add(control);return Object.freeze(control);
}

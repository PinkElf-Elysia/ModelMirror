import {join} from 'node:path';
import {SessionRecords} from './session-storage.mjs';
import {AuthoringError,canonical,keys,isHash,sha256,fail} from './contracts.mjs';
import {HISTORY_PLUGIN_ID,requireHistoryControl,validateHistoryIntent} from './history-control.mjs';

const digest=value=>sha256(canonical(value));
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const actions=['install','uninstall','enable','disable','configure'];
const settled=['applied','not_applied'];
const FORMAT='modelmirror.rpg.history-settings/1';
const CAPACITY=2000;
export function validateHistorySettingsIntent(value){
  canonical(value);
  keys(value,['action','sessionId','operationId','expectedRevision','expectedHistoryRevision','expectedContextPolicyRevision','expectedRegistryRevision',...(value.action==='configure'?['config']:[]),...(value.action==='enable'?['permissions']:[])]);
  if(!actions.includes(value.action)||!/^session-[a-f0-9]{64}$/.test(value.sessionId)||
    !/^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(value.operationId)||
    ![value.expectedRevision,value.expectedHistoryRevision,value.expectedRegistryRevision].every(integer)||!isHash(value.expectedContextPolicyRevision))fail('HISTORY_SETTINGS_INVALID');
  if(value.action==='configure')validateHistoryIntent(configurationIntent(value));
  if(value.action==='enable'&&(!Array.isArray(value.permissions)||value.permissions.some(p=>typeof p!=='string')))fail('HISTORY_PERMISSION_INVALID');
  return structuredClone(value);
}
function configurationIntent(input){
  const {action,expectedRegistryRevision,...intent}=input;return intent;
}
function safeFailure(error){
  if(error instanceof AuthoringError)throw error;
  if(/^PLUGIN_[A-Z_]+$/.test(error?.code||''))fail(error.code);
  fail('HISTORY_SETTINGS_STORAGE_UNAVAILABLE');
}

// HTTP configuration adapter only: no provider, request replay, or quota authority.
export async function createHistorySettings({directory,host,control,resolveRuntime,recoverDeadOwner=false,fault=async()=>{}}){
  requireHistoryControl(control);
  const records=await SessionRecords.open(join(directory,'operations'),{recoverDeadOwner,scope:'authoring-history-settings',fault:(phase,record)=>fault('journal:'+phase,record)});
  let queue=Promise.resolve(),closed=false;
  const serial=action=>{
    if(closed)fail('HISTORY_SETTINGS_CLOSED');
    const result=queue.then(action).catch(safeFailure);queue=result.catch(()=>{});return result;
  };
  async function journal(){
    const record=await records.get('operations');
    if(!record)return {hash:null,data:{format:FORMAT,operations:[]}};
    keys(record.data,['format','operations']);
    if(record.data.format!==FORMAT||!Array.isArray(record.data.operations)||record.data.operations.length>CAPACITY)fail('HISTORY_SETTINGS_CORRUPT');
    const ids=new Set();
    for(const op of record.data.operations){
      keys(op,['intent','hash','payload','state','receipt']);validateHistorySettingsIntent(op.intent);
      if(op.hash!==digest(op.intent)||ids.has(op.intent.operationId)||!['prepared',...settled].includes(op.state))fail('HISTORY_SETTINGS_CORRUPT');
      ids.add(op.intent.operationId);
    }
    return record;
  }
  async function view(sessionId){
    const current=await host.read(sessionId),catalog=await control.pluginService.catalog();
    const plugin=catalog.plugins.find(p=>p.id===HISTORY_PLUGIN_ID);
    const s=current.session;
    if(!s.history||!plugin)fail('HISTORY_CONTROL_HOST_REQUIRED');
    const runtime=await resolveRuntime({workId:s.binding.workId,versionId:s.binding.versionId});
    runtime.requireBinding(s.binding);
    const pending=(await journal()).data.operations.find(o=>o.state==='prepared');
    return {sessionId:s.id,workId:s.binding.workId,versionId:s.binding.versionId,sessionRevision:s.revision,
      history:structuredClone(s.history),effectivePolicy:current.effectivePolicy,busy:current.busy,pending:current.pending,
      registryRevision:catalog.revision,plugin:{id:plugin.id,name:plugin.name,version:plugin.version,permissions:plugin.permissions,
        installed:plugin.installed,compatibleInstallation:plugin.compatibleInstallation},
      suggestion:runtime.configuration().document.settings.defaultMemory,unconfirmed:pending?structuredClone(pending.intent):null,
      realProvider:false,billedCalls:0};
  }
  function checkVersions(input,current){
    if(input.expectedRevision!==current.sessionRevision)fail('OFFLINE_REVISION_CONFLICT');
    if(input.expectedHistoryRevision!==current.history.revision)fail('HISTORY_REVISION_CONFLICT');
    if(input.expectedContextPolicyRevision!==current.effectivePolicy.revision)fail('HISTORY_POLICY_CONFLICT');
    if(input.expectedRegistryRevision!==current.registryRevision)fail('PLUGIN_REGISTRY_CONFLICT');
    if(['enable','configure'].includes(input.action)){
      if(current.busy)fail('OFFLINE_SESSION_BUSY');
      if(current.pending)fail('OFFLINE_RESULT_PENDING');
      if(!current.plugin.installed)fail('PLUGIN_NOT_INSTALLED');
    }
    if(input.action==='configure'&&!current.effectivePolicy.authorization.history.enabled)fail('PLUGIN_NOT_AUTHORIZED');
    if(input.action==='enable'&&canonical(input.permissions)!==canonical(current.plugin.permissions))fail('PLUGIN_PERMISSION_MISMATCH');
    if(input.action==='install'&&current.plugin.installed)fail('PLUGIN_ALREADY_INSTALLED');
    if(['uninstall','disable'].includes(input.action)&&!current.plugin.installed)fail('PLUGIN_NOT_INSTALLED');
  }
  async function originalReceipt(op){
    if(op.intent.action==='configure'){
      const {session}=await host.read(op.intent.sessionId);
      const change=session.history.changes.find(c=>c.intent.operationId===op.intent.operationId);
      if(!change)return null;
      if(canonical(change.intent)!==canonical(configurationIntent(op.intent)))fail('HISTORY_OPERATION_COLLISION');
      return {operationId:op.intent.operationId,historyRevision:change.revision,changeHash:digest(change)};
    }
    return control.operationReceipt(op.intent.sessionId,op.intent.action,op.payload);
  }
  async function response(op,replayed=false){
    return {state:op.state,intent:structuredClone(op.intent),receipt:structuredClone(op.receipt),replayed,settings:await view(op.intent.sessionId)};
  }
  return Object.freeze({
    read:sessionId=>serial(()=>view(sessionId)),
    assertSettled:()=>serial(async()=>{if((await journal()).data.operations.some(o=>o.state==='prepared'))fail('HISTORY_SETTINGS_RESULT_PENDING');}),
    change(value){
      const input=validateHistorySettingsIntent(value);
      return serial(async()=>{
        let log=await journal();
        let op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
        if(op){
          if(op.hash!==digest(input))fail('HISTORY_OPERATION_COLLISION');
          if(op.state==='applied')return response(op,true);
          if(op.state==='prepared')fail('HISTORY_SETTINGS_RESULT_UNKNOWN');
        }
        if(log.data.operations.some(o=>o.state==='prepared'))fail('HISTORY_SETTINGS_RESULT_PENDING');
        const current=await view(input.sessionId);checkVersions(input,current);
        const {session}=await host.read(input.sessionId);
        if(input.operationId===session.create.operationId||session.operations.some(o=>o.operationId===input.operationId)||
          session.models.changes.some(c=>c.intent.operationId===input.operationId)||session.history.changes.some(c=>c.intent.operationId===input.operationId))fail('HISTORY_OPERATION_COLLISION');
        const catalog=await control.pluginService.catalog(),plugin=catalog.plugins.find(p=>p.id===HISTORY_PLUGIN_ID);
        const payload=input.action==='configure'?null:{operationId:input.operationId,pluginId:HISTORY_PLUGIN_ID,version:plugin.version,
          artifactSha256:plugin.artifactSha256,manifestSha256:plugin.manifestSha256,expectedRegistryRevision:input.expectedRegistryRevision,
          ...(['enable','disable'].includes(input.action)?{sessionId:input.sessionId,expectedSessionRevision:input.expectedRevision}:{}),
          ...(input.action==='enable'?{permissions:plugin.permissions}: {})};
        if(!op){
          if(log.data.operations.length>=CAPACITY)fail('HISTORY_SETTINGS_CAPACITY');
          op={intent:input,hash:digest(input),payload,state:'prepared',receipt:null};log.data.operations.push(op);
        }else{op.state='prepared';op.payload=payload;op.receipt=null;}
        log=await records.put('operations',log.hash,log.data);
        op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
        try{
          await fault('beforeChange',structuredClone(input));
          if(input.action==='configure')await host.configureHistory(configurationIntent(input));
          else await control.pluginService.change(input.action,payload);
          await fault('afterChange',structuredClone(input));
          op.receipt=await originalReceipt(op);
          if(!op.receipt)fail('HISTORY_SETTINGS_RESULT_UNKNOWN');
          op.state='applied';await records.put('operations',log.hash,log.data);
        }catch{fail('HISTORY_SETTINGS_RESULT_UNKNOWN');}
        return response(op);
      });
    },
    reconcile(value){
      const input=validateHistorySettingsIntent(value);
      return serial(async()=>{
        // Local disk settlement only. No change(), configureHistory() or provider call.
        await records.reconcile();await host.reconcileStorage();
        const log=await journal(),op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
        if(!op)return {state:'not_submitted',intent:input,receipt:null,settings:await view(input.sessionId)};
        if(op.hash!==digest(input))fail('HISTORY_OPERATION_COLLISION');
        if(op.state==='prepared'){
          op.receipt=await originalReceipt(op);op.state=op.receipt?'applied':'not_applied';
          await records.put('operations',log.hash,log.data);
        }
        return response(op,true);
      });
    },
    async close(){closed=true;await queue;await records.close();},
  });
}

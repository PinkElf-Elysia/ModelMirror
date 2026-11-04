import {join} from 'node:path';
import {SessionRecords} from './session-storage.mjs';
import {AuthoringError,canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {requireHistoryControl} from './history-control.mjs';
import {assertSummaryIdFree} from './summary-jobs.mjs';

export const SUMMARY_PLUGIN_ID='rpg.rolling-summary';
const format='modelmirror.rpg.summary-settings/1',limit=2000;
const digest=v=>sha256(canonical(v)),integer=v=>Number.isSafeInteger(v)&&v>=0;
const actions=['install','uninstall','enable','disable'];
export function validateSummarySettingsIntent(v){
  canonical(v);keys(v,['action','sessionId','operationId','expectedRevision','expectedSummaryRevision','expectedModelRevision','expectedContextPolicyRevision','expectedRegistryRevision',...(v.action==='enable'?['permissions']:[])]);
  if(!actions.includes(v.action)||!/^session-[a-f0-9]{64}$/.test(v.sessionId)||!isId(v.operationId)||
    ![v.expectedRevision,v.expectedSummaryRevision,v.expectedModelRevision,v.expectedRegistryRevision].every(integer)||!isHash(v.expectedContextPolicyRevision))fail('SUMMARY_SETTINGS_INVALID');
  if(v.action==='enable'&&(!Array.isArray(v.permissions)||v.permissions.some(p=>typeof p!=='string')))fail('SUMMARY_PERMISSION_INVALID');
  return structuredClone(v);
}
function safe(error){if(error instanceof AuthoringError)throw error;if(/^PLUGIN_[A-Z_]+$/.test(error?.code||''))fail(error.code);fail('SUMMARY_SETTINGS_STORAGE_UNAVAILABLE');}
// This adapter controls grants only. It cannot dispatch, certify, or add quota.
export async function createSummarySettings({directory,host,control,recoverDeadOwner=false,fault=async()=>{}}){
  requireHistoryControl(control);
  const records=await SessionRecords.open(join(directory,'operations'),{recoverDeadOwner,scope:'authoring-summary-settings',fault:(phase,record)=>fault('journal:'+phase,record)});
  let queue=Promise.resolve(),closed=false;
  const serial=fn=>{if(closed)fail('SUMMARY_SETTINGS_CLOSED');const work=queue.then(fn).catch(safe);queue=work.catch(()=>{});return work;};
  async function journal(){
    const r=await records.get('operations');if(!r)return {hash:null,data:{format,operations:[]}};
    keys(r.data,['format','operations']);if(r.data.format!==format||!Array.isArray(r.data.operations)||r.data.operations.length>limit)fail('SUMMARY_SETTINGS_CORRUPT');
    const ids=new Set();
    for(const op of r.data.operations){keys(op,['intent','hash','payload','state','receipt']);validateSummarySettingsIntent(op.intent);
      if(op.hash!==digest(op.intent)||ids.has(op.intent.operationId)||!['prepared','applied','not_applied'].includes(op.state))fail('SUMMARY_SETTINGS_CORRUPT');
      ids.add(op.intent.operationId);}
    return r;
  }
  async function view(id){
    const current=await host.read(id),catalog=await control.pluginService.catalog(),plugin=catalog.plugins.find(p=>p.id===SUMMARY_PLUGIN_ID),s=current.session;
    if(!s.summary||!plugin)fail('SUMMARY_CONTROL_HOST_REQUIRED');
    const pending=(await journal()).data.operations.find(o=>o.state==='prepared');
    return {sessionId:id,sessionRevision:s.revision,summaryRevision:s.summary.snapshot.revision,modelRevision:s.models.revision,
      model:s.models.current.summary,effectivePolicy:current.effectivePolicy,busy:current.busy,pending:current.pending,
      registryRevision:catalog.revision,plugin:{id:plugin.id,name:plugin.name,version:plugin.version,permissions:plugin.permissions,installed:plugin.installed,compatibleInstallation:plugin.compatibleInstallation},
      summaryStatus:current.summaryStatus,coveredThrough:s.summary.snapshot.versions.at(-1)?.coveredThrough??0,completedTurns:s.turns.length,
      unconfirmed:pending?structuredClone(pending.intent):null,realProvider:false,billedCalls:0};
  }
  function check(input,v){
    if(input.expectedRevision!==v.sessionRevision)fail('OFFLINE_REVISION_CONFLICT');
    if(input.expectedSummaryRevision!==v.summaryRevision)fail('SUMMARY_REVISION_CONFLICT');
    if(input.expectedModelRevision!==v.modelRevision)fail('MODEL_REVISION_CONFLICT');
    if(input.expectedContextPolicyRevision!==v.effectivePolicy.revision)fail('SUMMARY_POLICY_CONFLICT');
    if(input.expectedRegistryRevision!==v.registryRevision)fail('PLUGIN_REGISTRY_CONFLICT');
    if(input.action==='enable'){
      if(v.busy)fail('OFFLINE_SESSION_BUSY');if(v.pending)fail('SUMMARY_RESULT_PENDING');
      if(!v.model)fail('SUMMARY_MODEL_REQUIRED');
      if(canonical(input.permissions)!==canonical(v.plugin.permissions))fail('PLUGIN_PERMISSION_MISMATCH');
    }
    if(input.action==='install'&&v.plugin.installed)fail('PLUGIN_ALREADY_INSTALLED');
    if(input.action!=='install'&&!v.plugin.installed)fail('PLUGIN_NOT_INSTALLED');
  }
  const receipt=op=>control.operationReceipt(op.intent.sessionId,op.intent.action,op.payload);
  const response=async(op,replayed=false)=>({state:op.state,intent:structuredClone(op.intent),receipt:structuredClone(op.receipt),replayed,settings:await view(op.intent.sessionId)});
  return Object.freeze({
    read:id=>serial(()=>view(id)),
    assertSettled:()=>serial(async()=>{if((await journal()).data.operations.some(o=>o.state==='prepared'))fail('SUMMARY_SETTINGS_RESULT_PENDING');}),
    assertOperationAvailable:id=>serial(async()=>{if((await journal()).data.operations.some(o=>o.intent.operationId===id))fail('SUMMARY_SETTINGS_OPERATION_COLLISION');}),
    change(value){const input=validateSummarySettingsIntent(value);return serial(async()=>{
      let log=await journal(),op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
      if(op){if(op.hash!==digest(input))fail('SUMMARY_SETTINGS_OPERATION_COLLISION');if(op.state==='applied')return response(op,true);if(op.state==='prepared')fail('SUMMARY_SETTINGS_RESULT_UNKNOWN');}
      if(log.data.operations.some(o=>o.state==='prepared'))fail('SUMMARY_SETTINGS_RESULT_PENDING');
      const current=await view(input.sessionId);check(input,current);
      const {session}=await host.read(input.sessionId);assertSummaryIdFree(session,input.operationId);
      const catalog=await control.pluginService.catalog(),plugin=catalog.plugins.find(p=>p.id===SUMMARY_PLUGIN_ID);
      const payload={operationId:input.operationId,pluginId:plugin.id,version:plugin.version,artifactSha256:plugin.artifactSha256,manifestSha256:plugin.manifestSha256,expectedRegistryRevision:input.expectedRegistryRevision,
        ...(['enable','disable'].includes(input.action)?{sessionId:input.sessionId,expectedSessionRevision:input.expectedRevision}:{}),...(input.action==='enable'?{permissions:plugin.permissions}:{})};
      if(!op){if(log.data.operations.length>=limit)fail('SUMMARY_SETTINGS_CAPACITY');op={intent:input,hash:digest(input),payload,state:'prepared',receipt:null};log.data.operations.push(op);}
      else{op.state='prepared';op.payload=payload;op.receipt=null;}
      log=await records.put('operations',log.hash,log.data);op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
      try{await fault('beforeChange',structuredClone(input));await control.pluginService.change(input.action,payload);await fault('afterChange',structuredClone(input));
        op.receipt=await receipt(op);if(!op.receipt)fail('SUMMARY_SETTINGS_RESULT_UNKNOWN');op.state='applied';await records.put('operations',log.hash,log.data);
      }catch{fail('SUMMARY_SETTINGS_RESULT_UNKNOWN');}
      return response(op);
    });},
    reconcile(value){const input=validateSummarySettingsIntent(value);return serial(async()=>{
      await records.reconcile();await host.reconcileStorage();const log=await journal(),op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
      if(!op)return {state:'not_submitted',intent:input,receipt:null,settings:await view(input.sessionId)};
      if(op.hash!==digest(input))fail('SUMMARY_SETTINGS_OPERATION_COLLISION');
      if(op.state==='prepared'){op.receipt=await receipt(op);op.state=op.receipt?'applied':'not_applied';await records.put('operations',log.hash,log.data);}
      return response(op,true);
    });},
    async close(){closed=true;await queue;await records.close();},
  });
}

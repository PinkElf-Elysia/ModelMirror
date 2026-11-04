import {join} from 'node:path';
import {SessionRecords} from './session-storage.mjs';
import {AuthoringError,canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {requireHistoryControl} from './history-control.mjs';
import {assertJointIdFree} from './joint-session-state.mjs';

export const JOINT_PLUGINS=Object.freeze({model:'rpg.model-selector',history:'rpg.history-window',summary:'rpg.rolling-summary',memory:'rpg.memory-palace',branch:'rpg.branch-save'});
const format='modelmirror.rpg.joint-settings/1',limit=2000;
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),integer=v=>Number.isSafeInteger(v)&&v>=0;
const actions=['install','uninstall','enable','disable','configure'];
export function jointSettingsIntent(value){
 canonical(value);
 keys(value,['purpose','action','sessionId','operationId','expectedRevision','expectedModelRevision','expectedHistoryRevision','expectedSummaryRevision','expectedMemoryRevision',
  'expectedContextPolicyRevision','expectedRegistryRevision',...(value.action==='enable'?['permissions']:[]),...(value.action==='configure'?['config']:[])]);
 if(!Object.hasOwn(JOINT_PLUGINS,value.purpose)||!actions.includes(value.action)||!/^session-[a-f0-9]{64}$/.test(value.sessionId)||!isId(value.operationId)||
  ![value.expectedRevision,value.expectedModelRevision,value.expectedHistoryRevision,value.expectedSummaryRevision,value.expectedMemoryRevision,value.expectedRegistryRevision].every(integer)||
  !isHash(value.expectedContextPolicyRevision))fail('JOINT_SETTINGS_INVALID');
 if(value.action==='enable'&&(!Array.isArray(value.permissions)||value.permissions.some(p=>typeof p!=='string')))fail('PLUGIN_PERMISSION_MISMATCH');
 if(value.action==='configure'){
  if(value.purpose!=='history')fail('JOINT_SETTINGS_INVALID');keys(value.config,['turns','includeInitialCharacter']);
  if(!integer(value.config.turns)||value.config.turns<1||value.config.turns>20||typeof value.config.includeInitialCharacter!=='boolean')fail('HISTORY_SETTING_INVALID');
 }
 return clone(value);
}
function safe(e){if(e instanceof AuthoringError)throw e;if(/^PLUGIN_[A-Z_]+$/.test(e?.code||''))fail(e.code);fail('JOINT_SETTINGS_STORAGE_UNAVAILABLE');}
// Journal of explicit grants, separate from model selection and execution. No
// provider, quota mutation, certification or transport is reachable here.
export async function createJointSettings({directory,host,control,resolveRuntime,recoverDeadOwner=false,fault=async()=>{}}){
 requireHistoryControl(control);
 const records=await SessionRecords.open(join(directory,'operations'),{recoverDeadOwner,scope:'authoring-joint-settings',fault:(phase,record)=>fault('journal:'+phase,record)});
 let queue=Promise.resolve(),closed=false;
 const serial=fn=>{if(closed)fail('JOINT_SETTINGS_CLOSED');const p=queue.then(fn).catch(safe);queue=p.catch(()=>{});return p;};
 async function journal(){
  const r=await records.get('operations');if(!r)return {hash:null,data:{format,operations:[]}};
  keys(r.data,['format','operations']);if(r.data.format!==format||!Array.isArray(r.data.operations)||r.data.operations.length>limit)fail('JOINT_SETTINGS_CORRUPT');
  const ids=new Set();
  for(const op of r.data.operations){
   keys(op,['intent','hash','payload','state','receipt']);jointSettingsIntent(op.intent);
   if(op.hash!==digest(op.intent)||ids.has(op.intent.operationId)||!['prepared','applied','not_applied'].includes(op.state)||
    (op.state==='applied'?op.receipt===null:op.receipt!==null))fail('JOINT_SETTINGS_CORRUPT');ids.add(op.intent.operationId);
   if(op.intent.action==='configure'){
    keys(op.payload,['sessionId','operationId','expectedRevision','expectedHistoryRevision','expectedContextPolicyRevision','config']);
    if(op.payload.sessionId!==op.intent.sessionId||op.payload.operationId!==op.intent.operationId||op.payload.expectedRevision!==op.intent.expectedRevision||
     op.payload.expectedHistoryRevision!==op.intent.expectedHistoryRevision||!isHash(op.payload.expectedContextPolicyRevision)||canonical(op.payload.config)!==canonical(op.intent.config))fail('JOINT_SETTINGS_CORRUPT');
   }else if(op.payload.operationId!==op.intent.operationId||op.payload.pluginId!==JOINT_PLUGINS[op.intent.purpose]||op.payload.expectedRegistryRevision!==op.intent.expectedRegistryRevision)fail('JOINT_SETTINGS_CORRUPT');
  }
  return r;
 }
 async function view(id){
  const v=await host.read(id),s=v.session,catalog=await control.pluginService.catalog(),plugins={};
  for(const [purpose,pluginId]of Object.entries(JOINT_PLUGINS)){
   const p=catalog.plugins.find(p=>p.id===pluginId);if(!p)fail('JOINT_CONTROL_HOST_REQUIRED');
   plugins[purpose]={id:p.id,name:p.name,version:p.version,permissions:p.permissions,installed:p.installed,compatibleInstallation:p.compatibleInstallation,...(['branch','model'].includes(purpose)?{enabled:(await control.pluginService.sessionStatus(id,pluginId)).enabled}:{})};
  }
  const pending=(await journal()).data.operations.find(o=>o.state==='prepared');
  const runtime=await resolveRuntime({workId:s.binding.workId,versionId:s.binding.versionId});
  return {sessionId:id,sessionRevision:s.revision,modelRevision:s.models.revision,historyRevision:s.history.revision,summaryRevision:s.summary.snapshot.revision,memoryRevision:s.memory.snapshot.revision,
   registryRevision:catalog.revision,plugins,models:s.models.current,history:s.history.config,suggestion:runtime.configuration().document.settings.defaultMemory,
   effectivePolicy:v.effectivePolicy,busy:v.busy,pending:v.pending,summaryCoveredThrough:s.summary.snapshot.versions.at(-1)?.coveredThrough??0,
   memoryProcessedThrough:s.memory.snapshot.processedThrough,completedTurns:s.turns.length,budget:v.budget,
   unconfirmed:pending?clone(pending.intent):null,realProvider:host.readiness.realProvider===true};
 }
 function check(i,v){
  for(const [expected,actual,code]of [['expectedRevision','sessionRevision','OFFLINE_REVISION_CONFLICT'],['expectedModelRevision','modelRevision','MODEL_REVISION_CONFLICT'],
   ['expectedHistoryRevision','historyRevision','HISTORY_REVISION_CONFLICT'],['expectedSummaryRevision','summaryRevision','SUMMARY_REVISION_CONFLICT'],
   ['expectedMemoryRevision','memoryRevision','MEMORY_REVISION_CONFLICT'],['expectedRegistryRevision','registryRevision','PLUGIN_REGISTRY_CONFLICT']])if(i[expected]!==v[actual])fail(code);
  if(i.expectedContextPolicyRevision!==v.effectivePolicy.revision)fail('MEMORY_POLICY_CONFLICT');
  const p=v.plugins[i.purpose];
  if(['enable','configure'].includes(i.action)){
   if(v.busy)fail('OFFLINE_SESSION_BUSY');if(v.pending)fail('JOINT_RUN_PENDING');
   if(!p.installed)fail('PLUGIN_NOT_INSTALLED');
   if(['summary','memory'].includes(i.purpose)&&!v.models[i.purpose])fail(i.purpose==='memory'?'MEMORY_MODEL_REQUIRED':'SUMMARY_MODEL_REQUIRED');
  }
  if(i.action==='configure'&&!v.effectivePolicy.authorization.history.enabled)fail('PLUGIN_NOT_AUTHORIZED');
  if(i.action==='enable'&&canonical(i.permissions)!==canonical(p.permissions))fail('PLUGIN_PERMISSION_MISMATCH');
  if(i.action==='install'&&p.installed)fail('PLUGIN_ALREADY_INSTALLED');
  if(i.action!=='install'&&!p.installed)fail('PLUGIN_NOT_INSTALLED');
 }
 async function original(op){
  if(op.intent.action!=='configure')return control.operationReceipt(op.intent.sessionId,op.intent.action,op.payload);
  const {session:s}=await host.read(op.intent.sessionId),change=s.history.changes.find(c=>c.intent.operationId===op.intent.operationId);
  if(!change)return null;if(canonical(change.intent)!==canonical(op.payload))fail('JOINT_SETTINGS_OPERATION_COLLISION');
  return {operationId:op.intent.operationId,historyRevision:change.revision,changeHash:digest(change)};
 }
 const response=async(op,replayed=false)=>({state:op.state,intent:clone(op.intent),receipt:clone(op.receipt),replayed,settings:await view(op.intent.sessionId)});
 return Object.freeze({
  read:id=>serial(()=>view(id)),
  assertSettled:()=>serial(async()=>{if((await journal()).data.operations.some(o=>o.state==='prepared'))fail('JOINT_SETTINGS_RESULT_PENDING');}),
  assertOperationAvailable:id=>serial(async()=>{if((await journal()).data.operations.some(o=>o.intent.operationId===id))fail('JOINT_SETTINGS_OPERATION_COLLISION');}),
  change(value){const input=jointSettingsIntent(value);return serial(async()=>{
   let log=await journal(),op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
   if(op){if(op.hash!==digest(input))fail('JOINT_SETTINGS_OPERATION_COLLISION');if(op.state==='applied')return response(op,true);if(op.state==='prepared')fail('JOINT_SETTINGS_RESULT_UNKNOWN');}
   if(log.data.operations.some(o=>o.state==='prepared'))fail('JOINT_SETTINGS_RESULT_PENDING');
   const v=await view(input.sessionId);check(input,v);const current=await host.read(input.sessionId);assertJointIdFree(current.session,input.operationId);await host.assertOperationAvailable?.(input.sessionId,input.operationId);
   const catalog=await control.pluginService.catalog(),p=catalog.plugins.find(p=>p.id===JOINT_PLUGINS[input.purpose]);
   const payload=input.action==='configure'?{sessionId:input.sessionId,operationId:input.operationId,expectedRevision:input.expectedRevision,expectedHistoryRevision:input.expectedHistoryRevision,
    expectedContextPolicyRevision:current.historyConfigurationPolicyRevision,config:clone(input.config)}:{operationId:input.operationId,pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,
    expectedRegistryRevision:input.expectedRegistryRevision,...(['enable','disable'].includes(input.action)?{sessionId:input.sessionId,expectedSessionRevision:input.expectedRevision}:{}),...(input.action==='enable'?{permissions:p.permissions}:{})};
   if(!op){if(log.data.operations.length>=limit)fail('JOINT_SETTINGS_CAPACITY');op={intent:input,hash:digest(input),payload,state:'prepared',receipt:null};log.data.operations.push(op);}
   else {op.payload=payload;op.state='prepared';op.receipt=null;}
   log=await records.put('operations',log.hash,log.data);op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
   try{await fault('beforeChange',clone(input));if(input.action==='configure')await host.configureHistory(payload);else await control.pluginService.change(input.action,payload);
    await fault('afterChange',clone(input));op.receipt=await original(op);if(!op.receipt)fail('JOINT_SETTINGS_RESULT_UNKNOWN');op.state='applied';await records.put('operations',log.hash,log.data);
   }catch{fail('JOINT_SETTINGS_RESULT_UNKNOWN');}
   return response(op);
  });},
  reconcile(value){const input=jointSettingsIntent(value);return serial(async()=>{
   await records.reconcile();await host.reconcileStorage();const log=await journal(),op=log.data.operations.find(o=>o.intent.operationId===input.operationId);
   if(!op)return {state:'not_submitted',intent:input,receipt:null,settings:await view(input.sessionId)};
   if(op.hash!==digest(input))fail('JOINT_SETTINGS_OPERATION_COLLISION');
   if(op.state==='prepared'){op.receipt=await original(op);op.state=op.receipt?'applied':'not_applied';await records.put('operations',log.hash,log.data);}
   return response(op,true);
  });},
  async close(){closed=true;await queue;await records.close();},
 });
}

import {join} from 'node:path';
import {HistoryBranchArchive} from './history-branch-archive.mjs';
import {sendHistory} from './history-dispatch.mjs';
import {atomicJson} from './branch-archive.mjs';
import {randomUUID} from 'node:crypto';
import {ModelSessionStore} from './model-session-store.mjs';
import {validateParameters} from '../card-replica/lib/assembly.mjs';
import {models} from './provider.mjs';
import {initialModelState,fixedChoice,requireModelState,controlledChoice,validChoice} from './model-runtime.mjs';
import {historyRuntime,bindHistoryRuntime,historyRuntimeStatus} from './history-runtime.mjs';
import {initialHistoryState,requireHistoryState,effectiveHistoryPolicy,validSave,configFrom,operationId,exact,revision,requireHistoryEvidence} from './history-state.mjs';
import {assembleHistoryRequest} from './history-assembly.mjs';
import {canonical,sha,fail,HISTORY_WINDOW_ID,MODEL_SELECTOR_ID} from '../plugins/catalog.mjs';
export class HistorySessionStore extends ModelSessionStore {
 constructor(...args){super(...args);this.archive=new HistoryBranchArchive(join(this.directory,'branches'));}
 status(s){return historyRuntimeStatus(s);}
 async requireRuntime(s){
  if(s.runtime?.format!==3)return super.requireRuntime(s);
  const status=this.status(s);if(!status.compatible)throw fail(status.code);
  requireModelState(s);requireHistoryState(s);requireHistoryEvidence(s);await historyRuntime.verify();
 }
 async create(input){
  if(!input||Object.keys(input).some(k=>!['characterText','world','params','mode'].includes(k)))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const {characterText,world,mode}=input;
  if(mode!=='offline'&&mode!=='real')throw fail('PROVIDER_DISABLED',403);
  if(mode==='real'&&!this.realGenerate&&!(await this.control?.status?.())?.enabled)throw fail('PROVIDER_DISABLED',403);
  if(typeof characterText!=='string'||!characterText.trim()||characterText.length>200000||!['表世界','里世界'].includes(world))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const params=validateParameters({...input.params,max_tokens:input.params?.max_tokens??16384});
  if(Object.entries(models.earth.parameters).some(([k,v])=>params[k]!==v))throw fail('MODEL_PARAMETERS_INCOMPATIBLE');
  await historyRuntime.verify();
  const s={id:randomUUID(),name:(characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'未命名角色').slice(0,100),created:new Date().toISOString(),characterText,world,params,mode,revision:0,history:[],turns:[],requests:{},modelState:initialModelState(fixedChoice()),historyWindow:initialHistoryState()};
  s.runtime=bindHistoryRuntime(s);await this.write(s);return s;
 }
 async requireHistorySession(s){if(s.runtime?.format!==3)throw fail('HISTORY_RUNTIME_INCOMPATIBLE');await this.requireRuntime(s);return requireHistoryState(s);}
 async pluginSession(id){
  const s=await this.read(id);
  return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha(s.runtime?.hash||'unverified'),historyWindowCompatible:s.runtime?.format===3&&this.status(s).compatible,busy:this.running.has(id),pending:Object.values(s.requests).some(r=>r.status==='pending')||!!s.historyWindow?.pending};
 }
 async historyWindowStatus(id,plugins){
  const s=await this.read(id);
  if(s.runtime?.format!==3)return {compatible:false,reason:'HISTORY_RUNTIME_INCOMPATIBLE',enabled:false};
  const state=await this.requireHistorySession(s);
  if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  const auth=await plugins.historyAuthorization(id),policy=effectiveHistoryPolicy(s,auth);
  return {compatible:true,reason:null,enabled:auth.enabled,sessionRevision:s.revision,configRevision:state.revision,config:structuredClone(state.config),pendingOperationId:state.pending,effectivePolicy:policy,historyPolicyRevision:policy.revision};
 }
 async saveHistoryWindow(id,input,plugins){
  if(!validSave(input))throw fail('HISTORY_OPERATION_INVALID',400);
  input=structuredClone(input);
  return this.exclusive(id,async()=>{
   const s=await this.read(id),h=await this.requireHistorySession(s),operationHash=sha(canonical(input));
   const prior=Object.hasOwn(h.operations,input.operationId)?h.operations[input.operationId]:null;
   if(prior){if(prior.hash!==operationHash)throw fail('HISTORY_OPERATION_COLLISION');if(prior.status==='pending'){prior.status='not-applied';h.pending=null;await this.write(s);}return {session:s,operation:prior,replayed:true};}
   if(h.pending)throw fail('HISTORY_OPERATION_UNCONFIRMED');
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('HISTORY_UNCONFIRMED_REQUEST');
   if(s.revision!==input.expectedSessionRevision||h.revision!==input.expectedConfigRevision)throw fail('HISTORY_CONFIGURATION_CONFLICT');
   if(Object.keys(h.operations).length>=10000)throw fail('HISTORY_OPERATION_LIMIT');
   if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
   const ticket=await plugins.invoke({pluginId:HISTORY_WINDOW_ID,sessionId:id,capability:'session.history.configure',input:configFrom(input)});
   const staged=structuredClone(s);staged.historyWindow.pending=input.operationId;
   staged.historyWindow.operations[input.operationId]={hash:operationHash,input:structuredClone(input),status:'pending',configRevision:h.revision};
   h.config=configFrom(input);h.revision++;s.revision++;
   const operation=h.operations[input.operationId]={hash:operationHash,input:structuredClone(input),status:'complete',configRevision:h.revision};
   // Revocation and final publication share the host queue. A crash after intent leaves
   // old settings plus a recoverable pending operation, never half of the new settings.
   await plugins.commitResult(ticket,async()=>{await this.write(staged);await this.publishHistoryConfiguration(s);});
   return {session:s,operation,replayed:false};
  });
 }
 async publishHistoryConfiguration(s){return this.write(s);}
 async recoverHistoryOperation(id,key){
  if(!operationId(key))throw fail('HISTORY_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id),h=await this.requireHistorySession(s),op=Object.hasOwn(h.operations,key)?h.operations[key]:null;
   if(!op)return {operationId:key,status:'not-found'};
   if(op.status==='pending'){
    // Serialized disk read proves this intent did not publish. Query resolves it,
    // without reapplying configuration or granting any capability.
    op.status='not-applied';h.pending=null;await this.write(s);
   }
   return {operationId:key,...structuredClone(op)};
  });
 }
 async prepareHistoryRequest(id,{input,expectedHistoryPolicyRevision},plugins){
  return this.exclusive(id,async()=>{
   const s=await this.read(id),h=await this.requireHistorySession(s);
   if(h.pending)throw fail('HISTORY_OPERATION_UNCONFIRMED');
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('HISTORY_UNCONFIRMED_REQUEST');
   if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
   const policy=effectiveHistoryPolicy(s,await plugins.historyAuthorization(id));
   if(policy.revision!==expectedHistoryPolicyRevision)throw fail('HISTORY_POLICY_CONFLICT');
   return assembleHistoryRequest(s,input,policy);
  });
 }
 async historyOperation(id,key){
  if(!operationId(key))throw fail('HISTORY_OPERATION_INVALID',400);
  const s=await this.read(id),h=await this.requireHistorySession(s);
  return Object.hasOwn(h.operations,key)?{operationId:key,...structuredClone(h.operations[key])}:{operationId:key,status:'not-found'};
 }
 async requireSelectable(s){
  if(s.runtime?.format!==3)return super.requireSelectable(s);
  await this.requireRuntime(s);
  if(requireHistoryState(s).pending)throw fail('HISTORY_OPERATION_UNCONFIRMED');
  if(!this.control)throw fail('CONTROL_DISABLED');
  if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('MODEL_UNCONFIRMED_REQUEST');
 }
 async select(id,input,plugins){
  const existing=await this.read(id);if(existing.runtime?.format!==3)return super.select(id,input,plugins);
  if(!exact(input,['operationId','expectedSessionRevision','selectionRevision','selectionId','catalogRevision'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!revision(input.selectionRevision)||typeof input.selectionId!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(input.selectionId)||typeof input.catalogRevision!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(input.catalogRevision))throw fail('MODEL_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id);if(s.runtime?.format!==3)throw fail('HISTORY_RUNTIME_INCOMPATIBLE');
   const state=requireModelState(s),operationHash=sha(canonical(input));
   const prior=Object.hasOwn(state.operations,input.operationId)?state.operations[input.operationId]:null;
   if(prior){if(prior.hash!==operationHash)throw fail('MODEL_OPERATION_COLLISION');return {session:s,operation:prior,replayed:true};}
   await this.requireSelectable(s);
   if(s.revision!==input.expectedSessionRevision||state.revision!==input.selectionRevision)throw fail('MODEL_SELECTION_CONFLICT');
   if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
   const result=await plugins.invoke({pluginId:MODEL_SELECTOR_ID,sessionId:id,capability:'session.model.select',input:{selectionId:input.selectionId,selectionRevision:input.selectionRevision}});
   const options=await this.control.catalog();
   const selected=options.find(m=>m.selectionId===input.selectionId&&m.selectionRevision===input.catalogRevision&&m.available);
   if(!selected)throw fail('MODEL_SELECTION_UNAVAILABLE');
   const next=controlledChoice(selected);if(!validChoice(next))throw fail('MODEL_SELECTION_INVALID');
   state.revision++;state.current=next;state.timeline.push({revision:state.revision,choice:structuredClone(next)});
   const operation={hash:operationHash,revision:state.revision,selection:structuredClone(next)};
   state.operations[input.operationId]=operation;
   const path=s.provenance?this.archive.path(s.id):this.path(s.id);
   const value=s.provenance?{...await this.archive.read(s.id),session:s}:s;
   await atomicJson(path,value,publish=>plugins.commitResult(result,async()=>{await this.requireRuntime(await this.read(id));return publish();}));
   return {session:s,operation,replayed:false};
  });
 }
 async send(id,input,plugins=this.plugins){const s=await this.read(id);return s.runtime?.format===3?sendHistory(this,id,input,plugins):super.send(id,input);}
 async createBranch(id,input,plugins){
  const existing=await this.read(id);if(existing.runtime?.format!==3)return super.createBranch(id,input,plugins);
  if(!exact(input,['operationId','expectedSessionRevision','turn','name'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!Number.isSafeInteger(input.turn)||input.turn<1||typeof input.name!=='string'||!input.name.trim()||input.name!==input.name.trim()||input.name.length>80)throw fail('BRANCH_OPERATION_INVALID',400);
  if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  return this.exclusive(id,async()=>{
   const operation={sessionId:id,...input},prior=await this.archive.find(operation);
   if(prior)return {session:await this.read(prior.session.id),replayed:true,snapshotHash:prior.snapshotHash};
   const s=await this.read(id);await this.requireRuntime(s);
   if(requireHistoryState(s).pending)throw fail('HISTORY_OPERATION_UNCONFIRMED');
   if(s.revision!==input.expectedSessionRevision)throw fail('BRANCH_SESSION_CONFLICT');
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('BRANCH_UNCONFIRMED_REQUEST');
   const ticket=await plugins.invoke({sessionId:id,capability:'session.branch.prepare',input:{turn:input.turn,name:input.name}});
   const envelope=this.archive.prepare(s,operation);
   await this.archive.publish(envelope,publish=>plugins.commitResult(ticket,async()=>{await this.requireRuntime(await this.read(id));return publish();}));
   return {session:envelope.session,replayed:false,snapshotHash:envelope.snapshotHash};
  });
 }
}

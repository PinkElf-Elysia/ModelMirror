import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {SummarySessionStore} from './summary-session-store.mjs';
import {MemoryBranchArchive} from './memory-branch-archive.mjs';
import {memoryRuntime,bindMemoryRuntime,memoryRuntimeStatus} from './memory-runtime.mjs';
import {initialMemoryState,requireMemoryState,memoryOperations} from './memory-state.mjs';
import {initialMemoryTasks,requireMemoryTasks,memoryTaskBlock,memoryContextPolicy} from './memory-task-state.mjs';
import {createMemoryTasks} from './memory-tasks.mjs';
import {assembleMemoryRequest,commitMemoryStory,settleMemoryStory,requireMemoryEvidence} from './memory-assembly.mjs';
import {assembleSummaryRequest,commitSummaryStory,requireContextEvidence} from './summary-assembly.mjs';
import {createSummaryTasks,summaryTaskBlock,requireTaskState} from './summary-tasks.mjs';
import {initialSummaryState,requireSummaryState,effectiveContextPolicy} from './summary-state.mjs';
import {initialHistoryState,requireHistoryState,exact,operationId,revision} from './history-state.mjs';
import {initialModelState,fixedChoice,requireModelState,controlledChoice,validChoice} from './model-runtime.mjs';
import {validateParameters} from '../card-replica/lib/assembly.mjs';
import {models} from './provider.mjs';
import {canonical,sha,fail,MODEL_SELECTOR_ID} from '../plugins/catalog.mjs';
const bridgePending=s=>Object.entries(s.memoryLegacyInputs||{}).some(([key,o])=>o.status==='prepared'&&!s.summaryTasks?.operations?.[key]);
const policyBlock=(s,a)=>bridgePending(s)||memoryTaskBlock(s)||summaryTaskBlock(s)||s.memoryPalace.pending||s.historyWindow.pending||s.rollingSummary.pending?'MEMORY_TASK_UNCONFIRMED':s.memoryTasks.blocked&&(s.memoryTasks.operations[s.memoryTasks.blocked].blockedPurpose==='memory'?a.memory.enabled:a.summary.enabled)||s.summaryTasks?.blocked&&a.summary.enabled?'MEMORY_UPDATE_REQUIRES_EXPLICIT_ACTION':null;
const taskInput=x=>exact(x,['operationId','expectedSessionRevision','expectedMemoryRevision','expectedContextPolicyRevision','input'])&&operationId(x.operationId)&&revision(x.expectedSessionRevision)&&revision(x.expectedMemoryRevision)&&typeof x.expectedContextPolicyRevision==='string'&&/^[a-f0-9]{64}$/.test(x.expectedContextPolicyRevision)&&typeof x.input==='string'&&!!x.input.trim()&&x.input.length<=20000;
export class MemorySessionStore extends SummarySessionStore{
 constructor(...args){super(...args);this.archive=new MemoryBranchArchive(join(this.directory,'branches'));}
 status(s){return memoryRuntimeStatus(s);}
 async requireRuntime(s){
  if(s.runtime?.format!==5)return super.requireRuntime(s);
  const status=this.status(s);if(!status.compatible)throw fail(status.code);
  requireModelState(s);requireHistoryState(s);requireSummaryState(s);requireMemoryState(s);requireMemoryTasks(s);requireTaskState(s);requireMemoryEvidence(s);
  for(const [key,o] of Object.entries(s.memoryLegacyInputs||{}))if(!exact(o,['input','hash','mapped','authorization','status'])||!['prepared','not-applied'].includes(o.status)||!taskInput(o.input)||key!==o.input.operationId||o.hash!==sha(canonical(o.input))||!exact(o.mapped,['operationId','expectedSessionRevision','expectedContextPolicyRevision','input'])||o.mapped.operationId!==key||o.mapped.input!==o.input.input||!revision(o.mapped.expectedSessionRevision)||!/^[a-f0-9]{64}$/.test(o.mapped.expectedContextPolicyRevision)||!exact(o.authorization,['memory','summary','history'])||o.authorization.memory.enabled!==false)throw fail('MEMORY_LEGACY_INPUT_INVALID');
  await memoryRuntime.verify();
 }
 async create(input){
  if(!input||Object.keys(input).some(k=>!['characterText','world','params','mode'].includes(k)))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const {characterText,world,mode}=input;if(!['real','offline'].includes(mode))throw fail('PROVIDER_DISABLED',403);
  if(mode==='real'&&!(await this.control?.status?.())?.enabled)throw fail('PROVIDER_DISABLED',403);
  if(typeof characterText!=='string'||!characterText.trim()||characterText.length>200000||!['表世界','里世界'].includes(world))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const params=validateParameters({...input.params,max_tokens:input.params?.max_tokens??16384});if(Object.entries(models.earth.parameters).some(([k,v])=>params[k]!==v))throw fail('MODEL_PARAMETERS_INCOMPATIBLE');
  await memoryRuntime.verify();const s={id:randomUUID(),name:(characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'未命名角色').slice(0,100),created:new Date().toISOString(),characterText,world,params,mode,revision:0,history:[],turns:[],requests:{},modelState:initialModelState(fixedChoice()),historyWindow:initialHistoryState(),rollingSummary:initialSummaryState(),memoryPalace:initialMemoryState(),memoryTasks:initialMemoryTasks()};
  s.runtime=bindMemoryRuntime(s);await this.write(s);return s;
 }
 connectMemory(plugins,transport){
  if(this.memoryTasks||this.summaryTasks)throw fail('MEMORY_HOST_ALREADY_CONNECTED');this.plugins=plugins;this.control=transport;
  const admit=async s=>{if(s.mode!=='real'&&!transport.supportsOffline)throw fail('MEMORY_OFFLINE_TRANSPORT_REQUIRED');};
  const legacyAdmit=async s=>{await admit(s);if(s.runtime.format===5){const a=await this.memoryAuthorizations(s.id);if(a.memory.enabled||memoryTaskBlock(s))throw fail('MEMORY_TASK_UNCONFIRMED');for(const [key,o] of Object.entries(s.memoryLegacyInputs||{}))if(o.status==='prepared'&&!s.summaryTasks?.operations?.[key]&&canonical(o.authorization)!==canonical(a))throw fail('MEMORY_AUTHORIZATION_CHANGED');}};
  // The unchanged M2 coordinator receives the same purposes, messages and call
  // order. Only its trusted budget adapter maps purposes onto shared stage leases.
  const legacyTransport={...transport,budget:{...transport.budget,reserve:(group,id,purposes)=>transport.budget.reserve(group,id,purposes.map(purpose=>({stageId:purpose,purpose})))}};
  this.summaryTasks=createSummaryTasks({store:this,plugins,transport:legacyTransport,admit:legacyAdmit,buildStory:async(s,input,p)=>{if(s.runtime.format===5){requireMemoryEvidence(s);const full=memoryContextPolicy(s,await this.memoryAuthorizations(s.id));if(full.memoryEnabled||full.baseRevision!==p.revision)throw fail('MEMORY_AUTHORIZATION_CHANGED');return assembleMemoryRequest(s,input,full);}requireContextEvidence(s);return {...assembleSummaryRequest(s,input,p),input};},commitStory:(s,t,r)=>s.runtime.format===5?commitMemoryStory(s,t,r):commitSummaryStory(s,t,r)});
  const coordinator=createMemoryTasks({store:this,plugins,transport,buildStory:async(s,input,p)=>{await admit(s);requireMemoryEvidence(s);return assembleMemoryRequest(s,input,p);},commitStory:commitMemoryStory,settleStory:settleMemoryStory,legacySend:(s,x)=>this.sendWithoutMemory(s.id,x)});
  this.memoryTasks={...coordinator,status:async id=>{const x=await coordinator.status(id),s=await this.read(id),a=await this.memoryAuthorizations(id),reason=policyBlock(s,a);return {...x,effectivePolicy:reason?{...x.effectivePolicy,ready:false,reason}:x.effectivePolicy};},send:async(id,input)=>{await admit(await this.read(id));return coordinator.send(id,input);},update:async(id,input)=>{await admit(await this.read(id));return coordinator.update(id,input);}};
  this.memoryEdits=memoryOperations(this,plugins);return this.memoryTasks;
 }
 async memoryAuthorizations(id){return {memory:await this.plugins.memoryAuthorization(id),summary:await this.plugins.summaryAuthorization(id),history:await this.plugins.historyAuthorization(id)};}
 async pluginSession(id){
  const s=await this.read(id);if(s.runtime?.format!==5)return super.pluginSession(id);
  const compatible=this.status(s).compatible;
  return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha(s.runtime.hash),memoryPalaceCompatible:compatible,historyWindowCompatible:compatible,rollingSummaryCompatible:compatible,busy:this.running.has(id),pending:bridgePending(s)||memoryTaskBlock(s)||summaryTaskBlock(s)||!!s.memoryPalace.pending||!!s.historyWindow.pending||!!s.rollingSummary.pending||Object.values(s.requests).some(r=>r.status==='pending')};
 }
 async requireSummarySession(s){if(s.runtime?.format!==5)return super.requireSummarySession(s);await this.requireRuntime(s);if(memoryTaskBlock(s)||s.memoryPalace.pending)throw fail('MEMORY_TASK_UNCONFIRMED');return requireSummaryState(s);}
 async requireHistorySession(s){if(s.runtime?.format!==5)return super.requireHistorySession(s);await this.requireSelectable(s);return requireHistoryState(s);}
 async requireSelectable(s){
  if(s.runtime?.format!==5)return super.requireSelectable(s);await this.requireRuntime(s);
  if(bridgePending(s)||memoryTaskBlock(s)||summaryTaskBlock(s)||s.memoryPalace.pending||s.historyWindow.pending||s.rollingSummary.pending||Object.values(s.requests).some(r=>r.status==='pending'))throw fail('MEMORY_TASK_UNCONFIRMED');
  if(!this.control)throw fail('CONTROL_DISABLED');
 }
 async summaryStatus(id,plugins=this.plugins){
  const s=await this.read(id);if(s.runtime?.format!==5)return super.summaryStatus(id,plugins);
  await this.requireRuntime(s);const a=await this.memoryAuthorizations(id),h=s.rollingSummary,policy=memoryContextPolicy(s,a);
  return {compatible:true,enabled:a.summary.enabled,sessionRevision:s.revision,summaryRevision:h.revision,config:structuredClone(h.config),activeVersionId:h.activeVersionId,versions:structuredClone(h.versions),pendingOperationId:h.pending,taskState:structuredClone(requireTaskState(s)),effectivePolicy:policyBlock(s,a)?{...policy,ready:false,reason:policyBlock(s,a)}:policy};
 }
 async historyWindowStatus(id,plugins=this.plugins){
  const s=await this.read(id);if(s.runtime?.format!==5)return super.historyWindowStatus(id,plugins);
  const summary=await this.summaryStatus(id,plugins),h=s.historyWindow,p=summary.effectivePolicy.historyPolicy;
  return {compatible:true,enabled:p.enabled,sessionRevision:s.revision,configRevision:h.revision,config:structuredClone(h.config),pendingOperationId:h.pending,effectivePolicy:p,historyPolicyRevision:p.revision,temporarilyOverridden:summary.enabled,effectiveContextMode:summary.effectivePolicy.mode};
 }
 async catalog(id,plugins=this.plugins){
  const s=await this.read(id);if(s.runtime?.format!==5)return super.catalog(id,plugins);await this.requireSelectable(s);
  const ticket=await plugins.invoke({pluginId:MODEL_SELECTOR_ID,sessionId:id,capability:'model.catalog.read'}),list=await this.control.catalog({sessionId:id});await plugins.validateResult(ticket);return list;
 }
 async send(id,input){
  const s=await this.read(id);if(s.runtime?.format!==5)return super.send(id,input);if(!this.memoryTasks)throw fail('MEMORY_HOST_NOT_CONNECTED');
  if(!taskInput(input))throw fail('MEMORY_TASK_INPUT_INVALID',400);
  await this.requireRuntime(s);const prior=s.memoryLegacyInputs?.[input.operationId];if(prior){if(prior.hash!==sha(canonical(input)))throw fail('MEMORY_OPERATION_COLLISION');if(prior.status==='not-applied')return {status:'not-applied',replayed:true};return this.summaryTasks.send(id,prior.mapped);}
  if(Object.hasOwn(s.memoryTasks.operations,input.operationId))return this.memoryTasks.send(id,input);
  return (await this.plugins.memoryAuthorization(id)).enabled?this.memoryTasks.send(id,input):this.sendWithoutMemory(id,input);
 }
 // A bridge intent is written before the unchanged M2 coordinator starts. If no
 // coordinator operation exists, disk proves no Provider dispatch could occur.
 // Recovery closes that intent; it never retries it with different grants.
 async recoverMemoryOperation(id,key){
  if(!operationId(key))throw fail('MEMORY_OPERATION_INVALID',400);
  const s=await this.read(id);await this.requireRuntime(s);if(s.runtime?.format!==5)throw fail('MEMORY_RUNTIME_INCOMPATIBLE');
  if(Object.hasOwn(s.memoryTasks.operations,key))return this.memoryTasks.recover(id,key);
  if(Object.hasOwn(s.summaryTasks?.operations||{},key))return this.summaryTasks.recover(id,key);
  return this.exclusive(id,async()=>{const current=await this.read(id);await this.requireRuntime(current);const o=current.memoryLegacyInputs?.[key];if(!o)return {status:'not-found'};if(current.summaryTasks?.operations?.[key])throw fail('SESSION_BUSY');o.status='not-applied';await this.write(current);return {operationId:key,status:o.status};});
 }
 async sendWithoutMemory(id,input){
  let s=await this.read(id);await this.requireRuntime(s);const a=await this.memoryAuthorizations(id),policy=memoryContextPolicy(s,a);
  if(a.memory.enabled)throw fail('MEMORY_AUTHORIZATION_CHANGED');
  if(s.revision!==input.expectedSessionRevision||s.memoryPalace.revision!==input.expectedMemoryRevision||policy.revision!==input.expectedContextPolicyRevision)throw fail('MEMORY_TASK_REVISION_CONFLICT');
  if(memoryTaskBlock(s))throw fail('MEMORY_TASK_UNCONFIRMED');
  if(summaryTaskBlock(s)){
   if(Object.values(s.summaryTasks.operations).some(o=>['pending','unknown'].includes(o.status)&&o.kind!=='background'))throw fail('SESSION_BUSY');
   await this.summaryTasks.wait(id);s=await this.read(id);if(canonical(await this.memoryAuthorizations(id))!==canonical(a))throw fail('MEMORY_AUTHORIZATION_CHANGED');
  }
  const mapped=await this.exclusive(id,async()=>{
   const current=await this.read(id);await this.requireSelectable(current);
   if(current.revision!==s.revision||memoryContextPolicy(current,a).revision!==memoryContextPolicy(s,a).revision||canonical(await this.memoryAuthorizations(id))!==canonical(a))throw fail('MEMORY_TASK_REVISION_CONFLICT');
   const blocked=current.memoryTasks.blocked&&current.memoryTasks.operations[current.memoryTasks.blocked];if(blocked?.blockedPurpose==='summary'&&a.summary.enabled)throw fail('MEMORY_UPDATE_REQUIRES_EXPLICIT_ACTION');
   const mapped={operationId:input.operationId,expectedSessionRevision:current.revision,expectedContextPolicyRevision:effectiveContextPolicy(current,a.summary,a.history).revision,input:input.input};
   current.memoryLegacyInputs={...current.memoryLegacyInputs,[input.operationId]:{input:structuredClone(input),hash:sha(canonical(input)),mapped,authorization:a,status:'prepared'}};await this.write(current);return mapped;
  });
  return this.summaryTasks.send(id,mapped);
 }
 async select(id,input,plugins=this.plugins){
  if((await this.read(id)).runtime?.format!==5)return super.select(id,input,plugins);
  if(!exact(input,['operationId','expectedSessionRevision','selectionRevision','selectionId','catalogRevision'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!revision(input.selectionRevision)||![input.selectionId,input.catalogRevision].every(v=>typeof v==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(v)))throw fail('MODEL_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id),m=requireModelState(s),hash=sha(canonical(input)),prior=Object.hasOwn(m.operations,input.operationId)?m.operations[input.operationId]:null;
   if(prior){if(prior.hash!==hash)throw fail('MODEL_OPERATION_COLLISION');return {session:s,operation:prior,replayed:true};}
   await this.requireSelectable(s);if(s.revision!==input.expectedSessionRevision||m.revision!==input.selectionRevision)throw fail('MODEL_SELECTION_CONFLICT');
   const ticket=await plugins.invoke({pluginId:MODEL_SELECTOR_ID,sessionId:id,capability:'session.model.select',input:{selectionId:input.selectionId,selectionRevision:input.selectionRevision}});
   const found=(await this.control.catalog({sessionId:id})).find(x=>x.available&&x.selectionId===input.selectionId&&x.selectionRevision===input.catalogRevision);if(!found)throw fail('MODEL_SELECTION_UNAVAILABLE');const selected=controlledChoice(found);if(!validChoice(selected))throw fail('MODEL_SELECTION_INVALID');
   m.revision++;m.current=selected;m.timeline.push({revision:m.revision,choice:structuredClone(selected)});const op=m.operations[input.operationId]={hash,revision:m.revision,selection:structuredClone(selected)};
   await plugins.commitResult(ticket,()=>this.write(s));return {session:s,operation:op,replayed:false};
  });
 }
 async createBranch(id,input,plugins=this.plugins){
  if((await this.read(id)).runtime?.format!==5)return super.createBranch(id,input,plugins);
  if(!exact(input,['operationId','expectedSessionRevision','turn','name'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!Number.isSafeInteger(input.turn)||input.turn<1||typeof input.name!=='string'||!input.name.trim()||input.name!==input.name.trim()||input.name.length>80)throw fail('BRANCH_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const operation={sessionId:id,...input},prior=await this.archive.find(operation);if(prior)return {session:await this.read(prior.session.id),snapshotHash:prior.snapshotHash,replayed:true};
   const s=await this.read(id);await this.requireSelectable(s);if(s.revision!==input.expectedSessionRevision)throw fail('BRANCH_SESSION_CONFLICT');
   const ticket=await plugins.invoke({sessionId:id,capability:'session.branch.prepare',input:{turn:input.turn,name:input.name}}),envelope=this.archive.prepare(s,operation);
   await this.archive.publish(envelope,publish=>plugins.commitResult(ticket,()=>this.fileOperation(id,publish)));return {session:envelope.session,snapshotHash:envelope.snapshotHash,replayed:false};
  });
 }
 async prepareHistoryRequest(id,input,plugins=this.plugins){
  if((await this.read(id)).runtime?.format!==5)return super.prepareHistoryRequest(id,input,plugins);
  if(!exact(input,['input','expectedContextPolicyRevision'])||typeof input.input!=='string'||!input.input.trim()||input.input.length>20000)throw fail('MEMORY_TASK_INPUT_INVALID',400);
  return this.exclusive(id,async()=>{const s=await this.read(id);await this.requireSelectable(s);const policy=memoryContextPolicy(s,await this.memoryAuthorizations(id));if(policy.revision!==input.expectedContextPolicyRevision)throw fail('MEMORY_TASK_REVISION_CONFLICT');return assembleMemoryRequest(s,input.input,policy);});
 }
}

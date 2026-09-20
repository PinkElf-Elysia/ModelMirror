import {reusableOverflow} from './summary-compression.mjs';
import {join} from 'node:path';
import {SummaryBranchArchive} from './summary-branch-archive.mjs';
import {assembleSummaryRequest,commitSummaryStory,requireContextEvidence} from './summary-assembly.mjs';
import {selectSummaryModel,createSummaryBranch} from './summary-operations.mjs';
import {summaryTaskBlock,requireTaskState,createSummaryTasks} from './summary-tasks.mjs';
import {randomUUID} from 'node:crypto';
import {HistorySessionStore} from './history-session-store.mjs';
import {summaryRuntime,bindSummaryRuntime,summaryRuntimeStatus} from './summary-runtime.mjs';
import {initialSummaryState,requireSummaryState,sealSummary,manualSummaryVersion,withSummaryVersion,effectiveContextPolicy,summaryUpdateSource} from './summary-state.mjs';
import {initialHistoryState,requireHistoryState,exact,revision,operationId} from './history-state.mjs';
import {initialModelState,fixedChoice,controlledChoice,validChoice,requireModelState} from './model-runtime.mjs';
import {completeHistory} from './branch-archive.mjs';
import {validateParameters} from '../card-replica/lib/assembly.mjs';
import {validSettings,validText} from '../plugins/rolling-summary.mjs';
import {models} from './provider.mjs';
import {ROLLING_SUMMARY_ID,sha,canonical,fail} from '../plugins/catalog.mjs';
const common=['operationId','expectedSessionRevision','expectedSummaryRevision'];
const validCommon=x=>operationId(x?.operationId)&&revision(x.expectedSessionRevision)&&revision(x.expectedSummaryRevision);
// HTTP wiring remains gated until B4. Only trusted host code connects the transport.
export class SummarySessionStore extends HistorySessionStore {
 constructor(...args){super(...args);this.archive=new SummaryBranchArchive(join(this.directory,'branches'));this.fileOperations=new Map();}
 // Windows cannot atomically replace a JSON file while our own read handle is open.
 // Serialize only each short file operation, never the whole generation or polling cycle.
 async fileOperation(id,action){const previous=this.fileOperations.get(id)||Promise.resolve();const next=previous.catch(()=>{}).then(action);this.fileOperations.set(id,next);try{return await next;}finally{if(this.fileOperations.get(id)===next)this.fileOperations.delete(id);}}
 async read(id){return this.fileOperation(id,()=>super.read(id));}
 async write(s){return this.fileOperation(s.id,()=>super.write(s));}
 connectSummary(plugins,transport){
  if(this.summaryTasks)throw fail('SUMMARY_HOST_ALREADY_CONNECTED');
  this.plugins=plugins;
  this.summaryTasks=createSummaryTasks({store:this,plugins,transport,admit:s=>{if(s.mode!=='real'&&!transport.supportsOffline)throw fail('SUMMARY_OFFLINE_TRANSPORT_REQUIRED');},buildStory:async(s,input,policy)=>{requireContextEvidence(s);return {...assembleSummaryRequest(s,input,policy),input};},commitStory:commitSummaryStory});
  return this.summaryTasks;
 }
 async requireSelectable(s){
  if(s.runtime?.format!==4)return super.requireSelectable(s);
  await this.requireSummarySession(s);
  if(summaryTaskBlock(s)||s.historyWindow.pending||s.rollingSummary.pending)throw fail('SUMMARY_OPERATION_UNCONFIRMED');
  if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('MODEL_UNCONFIRMED_REQUEST');
  if(!this.control)throw fail('CONTROL_DISABLED');
 }
 status(s){return summaryRuntimeStatus(s);}
 async requireRuntime(s){
  if(s.runtime?.format!==4)return super.requireRuntime(s);
  const status=this.status(s);if(!status.compatible)throw fail(status.code);
  completeHistory(s);requireModelState(s);requireHistoryState(s);requireSummaryState(s);requireTaskState(s);if(s.turns.some(t=>t.contextPolicy))requireContextEvidence(s);await summaryRuntime.verify();
 }
 async create(input){
  if(!input||Object.keys(input).some(k=>!['characterText','world','params','mode'].includes(k)))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const {characterText,world,mode}=input;
  if(mode!=='offline'&&mode!=='real')throw fail('PROVIDER_DISABLED',403);
  if(mode==='real'&&!this.realGenerate&&!(await this.control?.status?.())?.enabled)throw fail('PROVIDER_DISABLED',403);
  if(typeof characterText!=='string'||!characterText.trim()||characterText.length>200000||!['表世界','里世界'].includes(world))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const params=validateParameters({...input.params,max_tokens:input.params?.max_tokens??16384});
  if(Object.entries(models.earth.parameters).some(([k,v])=>params[k]!==v))throw fail('MODEL_PARAMETERS_INCOMPATIBLE');
  await summaryRuntime.verify();
  const s={id:randomUUID(),name:(characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'未命名角色').slice(0,100),created:new Date().toISOString(),characterText,world,params,mode,revision:0,history:[],turns:[],requests:{},modelState:initialModelState(fixedChoice()),historyWindow:initialHistoryState(),rollingSummary:initialSummaryState()};
  s.runtime=bindSummaryRuntime(s);await this.write(s);return s;
 }
 async requireSummarySession(s){if(s.runtime?.format!==4)throw fail('SUMMARY_RUNTIME_INCOMPATIBLE');await this.requireRuntime(s);return requireSummaryState(s);}
 async requireHistorySession(s){
  if(s.runtime?.format!==4)return super.requireHistorySession(s);
  const h=await this.requireSummarySession(s);if(h.pending||summaryTaskBlock(s))throw fail('SUMMARY_OPERATION_UNCONFIRMED');return requireHistoryState(s);
 }
 async pluginSession(id){
  const s=await this.read(id);if(s.runtime?.format!==4)return super.pluginSession(id);
  return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha(s.runtime.hash),historyWindowCompatible:this.status(s).compatible,rollingSummaryCompatible:this.status(s).compatible,busy:this.running.has(id),pending:Object.values(s.requests).some(r=>r.status==='pending')||!!s.historyWindow?.pending||!!s.rollingSummary?.pending||summaryTaskBlock(s)};
 }
 async summaryStatus(id,plugins){
  const s=await this.read(id);if(s.runtime?.format!==4)return {compatible:false,reason:'SUMMARY_RUNTIME_INCOMPATIBLE',enabled:false};
  const h=await this.requireSummarySession(s);if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  const auth=await plugins.summaryAuthorization(id),historyAuth=await plugins.historyAuthorization(id),taskState=requireTaskState(s);
  const policy=effectiveContextPolicy(s,auth,historyAuth),taskBlock=summaryTaskBlock(s)?'SUMMARY_TASK_UNCONFIRMED':taskState.blocked&&auth.enabled?'SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION':null;
  const updatePlan=auth.enabled&&h.config.model&&policy.needsUpdate&&!summaryTaskBlock(s)?(reusableOverflow(s,taskState,summaryUpdateSource(s))?{kind:'compression',maxCalls:1}:{kind:'summary',maxCalls:2}):null;
  return {updatePlan,compatible:true,enabled:auth.enabled,sessionRevision:s.revision,summaryRevision:h.revision,config:structuredClone(h.config),activeVersionId:h.activeVersionId,versions:structuredClone(h.versions),pendingOperationId:h.pending,taskState:structuredClone(taskState),effectivePolicy:taskBlock?{...policy,ready:false,reason:taskBlock}:policy};
 }
 async historyWindowStatus(id,plugins){
  const s=await this.read(id);if(s.runtime?.format!==4)return super.historyWindowStatus(id,plugins);
  const summary=await this.summaryStatus(id,plugins),h=requireHistoryState(s),m1=summary.effectivePolicy.historyPolicy;
  return {compatible:true,enabled:m1.enabled,sessionRevision:s.revision,configRevision:h.revision,config:structuredClone(h.config),pendingOperationId:h.pending,effectivePolicy:m1,historyPolicyRevision:m1.revision,temporarilyOverridden:summary.enabled,effectiveContextMode:summary.effectivePolicy.mode};
 }
 async configureSummary(id,input,plugins){
  if(!exact(input,[...common,'timing','selectionId','catalogRevision'])||!validCommon(input)||!validSettings({timing:input.timing,selectionId:input.selectionId,catalogRevision:input.catalogRevision}))throw fail('SUMMARY_OPERATION_INVALID',400);
  return this.mutateSummary(id,structuredClone(input),plugins,async(s,h,x)=>{
   if(!this.control)throw fail('CONTROL_DISABLED');
   const entries=await this.control.catalog({sessionId:id}),entry=entries.find(m=>m.selectionId===x.selectionId&&m.selectionRevision===x.catalogRevision&&m.available);
   if(!entry)throw fail('MODEL_SELECTION_UNAVAILABLE');const model=controlledChoice(entry);if(!validChoice(model))throw fail('MODEL_SELECTION_INVALID');
   return {capability:'session.summary.configure',proposal:{timing:x.timing,selectionId:x.selectionId,catalogRevision:x.catalogRevision},next:sealSummary({...h,revision:h.revision+1,config:{timing:x.timing,model}})};
  });
 }
 async saveSummaryForm(id,input,plugins){
  if(!exact(input,[...common,'timing','selectionId','catalogRevision','text'])||!validCommon(input)||!validSettings({timing:input.timing,selectionId:input.selectionId,catalogRevision:input.catalogRevision})||input.text!==null&&!validText(input.text))throw fail('SUMMARY_OPERATION_INVALID',400);
  return this.mutateSummary(id,structuredClone(input),plugins,async(s,h,x)=>{
   const entry=(await this.control.catalog({sessionId:id})).find(m=>m.selectionId===x.selectionId&&m.selectionRevision===x.catalogRevision&&m.available);
   if(!entry)throw fail('MODEL_SELECTION_UNAVAILABLE');const model=controlledChoice(entry);
   const next=x.text===null?h:withSummaryVersion(s,manualSummaryVersion(s,{text:x.text}));
   return {capability:'session.summary.configure',proposal:{timing:x.timing,selectionId:x.selectionId,catalogRevision:x.catalogRevision},next:sealSummary({...next,revision:h.revision+1,config:{timing:x.timing,model}})};
  });
 }
 async summaryCatalog(id,plugins){
  const s=await this.read(id);await this.requireSummarySession(s);
  const ticket=await plugins.invoke({pluginId:ROLLING_SUMMARY_ID,sessionId:id,capability:'ui.summary-action'});
  const list=await this.control.catalog({sessionId:id});await plugins.validateResult(ticket);return list;
 }
 async catalog(id,plugins){
  if((await this.read(id)).runtime?.format!==4)return super.catalog(id,plugins);
  const s=await this.read(id);await this.requireSelectable(s);
  const ticket=await plugins.invoke({pluginId:'rpg.model-selector',sessionId:id,capability:'model.catalog.read'});
  const list=await this.control.catalog({sessionId:id});await plugins.validateResult(ticket);return list;
 }
 async reviseSummary(id,input,plugins){
  const edit=exact(input,[...common,'text']),restore=exact(input,[...common,'restoreVersionId']);
  if(!validCommon(input)||!(edit&&validText(input.text)||restore&&typeof input.restoreVersionId==='string'&&/^[a-f0-9]{64}$/.test(input.restoreVersionId)))throw fail('SUMMARY_OPERATION_INVALID',400);
  return this.mutateSummary(id,structuredClone(input),plugins,async(s,h,x)=>{
   const v=manualSummaryVersion(s,edit?{text:x.text}:{restoreVersionId:x.restoreVersionId});
   return {capability:'session.summary.revise',proposal:{textHash:sha(v.effectiveText),characters:Array.from(v.effectiveText).length},next:withSummaryVersion(s,v)};
  });
 }
 async mutateSummary(id,input,plugins,prepare){
  return this.exclusive(id,async()=>{
   const s=await this.read(id),h=await this.requireSummarySession(s),operationHash=sha(canonical(input)),prior=Object.hasOwn(h.operations,input.operationId)?h.operations[input.operationId]:null;
   if(prior){if(prior.hash!==operationHash)throw fail('SUMMARY_OPERATION_COLLISION');if(prior.status==='pending'){prior.status='not-applied';h.pending=null;s.rollingSummary=sealSummary(h);await this.write(s);}return {session:s,operation:structuredClone(prior),replayed:true};}
   if(h.pending||s.historyWindow.pending||summaryTaskBlock(s))throw fail('SUMMARY_OPERATION_UNCONFIRMED');
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('SUMMARY_UNCONFIRMED_REQUEST');
   if(s.revision!==input.expectedSessionRevision||h.revision!==input.expectedSummaryRevision)throw fail('SUMMARY_CONFIGURATION_CONFLICT');
   if(Object.keys(h.operations).length>=10000)throw fail('SUMMARY_OPERATION_LIMIT');
   if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
   const auth=await plugins.summaryAuthorization(id);if(!auth.enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
   const prepared=await prepare(s,h,input);
   const ticket=await plugins.invoke({pluginId:ROLLING_SUMMARY_ID,sessionId:id,capability:prepared.capability,input:prepared.proposal});
   if((await plugins.summaryAuthorization(id)).revision!==auth.revision)throw fail('SUMMARY_AUTHORIZATION_CHANGED');
   const staged=structuredClone(s),op={hash:operationHash,input:structuredClone(input),status:'pending',summaryRevision:h.revision};
   staged.rollingSummary.pending=input.operationId;staged.rollingSummary.operations[input.operationId]=op;staged.rollingSummary=sealSummary(staged.rollingSummary);
   const next=prepared.next;next.operations[input.operationId]={...op,status:'complete',summaryRevision:next.revision};next.pending=null;s.rollingSummary=sealSummary(next);s.revision++;
   requireSummaryState(staged);requireSummaryState(s);
   await plugins.commitResult(ticket,async()=>{await this.write(staged);await this.publishSummary(s);});
   return {session:s,operation:structuredClone(s.rollingSummary.operations[input.operationId]),replayed:false};
  });
 }
 async publishSummary(s){return this.write(s);}
 async recoverSummaryOperation(id,key){
  if(!operationId(key))throw fail('SUMMARY_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id),h=await this.requireSummarySession(s),op=Object.hasOwn(h.operations,key)?h.operations[key]:null;
   if(!op)return {operationId:key,status:'not-found'};
   if(op.status==='pending'){op.status='not-applied';h.pending=null;s.rollingSummary=sealSummary(h);await this.write(s);}
   return {operationId:key,...structuredClone(op)};
  });
 }
 async send(id,input){
  if((await this.read(id)).runtime?.format!==4)return super.send(id,input);
  if(!this.summaryTasks)throw fail('SUMMARY_DISPATCH_NOT_CONNECTED');return this.summaryTasks.send(id,input);
 }
 async select(id,input,plugins=this.plugins){
  if((await this.read(id)).runtime?.format!==4)return super.select(id,input,plugins);
  if(!this.summaryTasks)throw fail('SUMMARY_MODEL_SWITCH_NOT_CONNECTED');return selectSummaryModel.call(this,id,input,plugins);
 }
 async createBranch(id,input,plugins=this.plugins){
  if((await this.read(id)).runtime?.format!==4)return super.createBranch(id,input,plugins);
  if(!this.summaryTasks)throw fail('SUMMARY_BRANCH_NOT_CONNECTED');return createSummaryBranch.call(this,id,input,plugins);
 }
 async prepareHistoryRequest(id,input,plugins=this.plugins){
  if((await this.read(id)).runtime?.format!==4)return super.prepareHistoryRequest(id,input,plugins);
  if(!this.summaryTasks)throw fail('SUMMARY_ASSEMBLY_NOT_CONNECTED');
  if(!exact(input,['input','expectedContextPolicyRevision'])||typeof input.input!=='string'||!input.input.trim()||input.input.length>20000)throw fail('SUMMARY_TASK_INPUT_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id);await this.requireSelectable(s);requireContextEvidence(s);
   const policy=effectiveContextPolicy(s,await plugins.summaryAuthorization(id),await plugins.historyAuthorization(id));
   if(policy.revision!==input.expectedContextPolicyRevision)throw fail('SUMMARY_TASK_REVISION_CONFLICT');
   return assembleSummaryRequest(s,input.input,policy);
  });
 }
}

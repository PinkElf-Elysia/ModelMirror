// Trusted six-card adapter. Author scripts and browser input cannot supply a transport.
import {withCardRequirements,emptyCardRequirements,forkCardRequirements,requirementsPolicy} from './card-requirements.mjs';
import {futureIntent,futureIds,futureReceipt,priorFuture,validateFutureConfigurations} from '../authoring/comparison-future.mjs';
import {createCardState} from './card-state.mjs';
import {sealSummary} from './summary-state.mjs';
import {join} from 'node:path';
import {canonical,sha,fail,GENERATION_PARAMETERS_ID as PARAMETER_ID} from '../plugins/catalog.mjs';
import {validateModelChoice,modelChoiceInput,PARAMETER_SELECTION_FORMAT,PARAMETER_CHOICE_FORMAT,modelCapacity} from '../authoring/model-control.mjs';
import {PARAMETER_PROFILE,PARAMETER_CONFIG} from '../authoring/generation-settings.mjs';
import {ControlledProvider} from '../authoring/controlled-provider.mjs';
import {controlledPayload} from '../authoring/controlled-receipt.mjs';
import {createMemoryBudget} from './memory-budget.mjs';
import {createMemoryStoreClass} from './memory-session-store.mjs';
const purposes=['story','summary','memory'],clone=structuredClone,digest=v=>sha(canonical(v));
const exact=(v,k)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===k.slice().sort().join(',');
const integer=v=>Number.isSafeInteger(v)&&v>=0,hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const purpose=p=>p==='compression'?'summary':p;
const seal=v=>({...v,hash:digest(v)});
export function emptyCardParameters(values={story:null,summary:null,memory:null}){return seal({format:'modelmirror.rpg.card-parameters/1',revision:0,values:clone(values),operations:{}});}
export function validateCardParameters(q){
 if(!exact(q,['format','revision','values','operations','hash'])||q.format!=='modelmirror.rpg.card-parameters/1'||!integer(q.revision)||q.revision>1000||!exact(q.values,purposes)||!q.operations||typeof q.operations!=='object')throw fail('PARAMETER_STATE_INVALID');
 const {hash:h,...body}=q;if(h!==digest(body))throw fail('PARAMETER_STATE_INVALID');
 for(const [p,c] of Object.entries(q.values))if(c!==null){validateModelChoice(c);if(c.format!==PARAMETER_CHOICE_FORMAT||c.parameterProfile.purpose!==p)throw fail('PARAMETER_STATE_INVALID');}
 if(Array.isArray(q.operations)||Object.keys(q.operations).length!==q.revision)throw fail('PARAMETER_STATE_INVALID');
 const seen=new Set(),last={};for(const [key,o] of Object.entries(q.operations)){if(!/^[A-Za-z0-9-]{1,80}$/.test(key)||!exact(o,['status','inputHash','configurationHash','revision','purpose','choice','receipt'])||o.status!=='applied'||!hash(o.inputHash)||!hash(o.configurationHash)||!integer(o.revision)||o.revision<1||o.revision>q.revision||seen.has(o.revision)||!purposes.includes(o.purpose))throw fail('PARAMETER_STATE_INVALID');seen.add(o.revision);if(o.choice!==null){validateModelChoice(o.choice);if(o.choice.parameterProfile.purpose!==o.purpose)throw fail('PARAMETER_STATE_INVALID');}if(!last[o.purpose]||last[o.purpose].revision<o.revision)last[o.purpose]=o;}
 for(const [p,o] of Object.entries(last))if(canonical(o.choice)!==canonical(q.values[p]))throw fail('PARAMETER_STATE_INVALID');
 return q;
}
export function wrapParameterChoice(c,context=null){validateModelChoice(c);return sealChoice({kind:'controlled',model:c.entry.modelId,selectionId:c.entry.selectionId,selectionRevision:c.entry.selectionRevision,parameters:clone(c.wireParameters),parameterChoice:clone(c),...(context?{parameterContext:clone(context)}:{})});}
const sealChoice=v=>({...v,configHash:digest(v)});
export function cardParameterPolicy(cardId,initial){
 const validChoice=c=>{try{const keys=['kind','model','selectionId','selectionRevision','parameters','parameterChoice','configHash',...(c?.parameterContext?['parameterContext']:[])];
  if(!exact(c,keys)||c.kind!=='controlled')return false;validateModelChoice(c.parameterChoice);const v=c.parameterChoice;
  if(v.format!==PARAMETER_CHOICE_FORMAT||v.entry.modelId!==c.model||v.entry.selectionId!==c.selectionId||v.entry.selectionRevision!==c.selectionRevision||canonical(c.parameters)!==canonical(v.wireParameters))return false;
  if(c.parameterContext){const x=c.parameterContext;if(!exact(x,['revision','authorization','values'])||!integer(x.revision)||!exact(x.authorization,['enabled','revision'])||typeof x.authorization.enabled!=='boolean'||!hash(x.authorization.revision))return false;validateCardParameters(emptyCardParameters(x.values));}
  const {configHash,...body}=c;return configHash===digest(body);
 }catch{return false;}};
 return Object.freeze({cardId,validChoice,controlledChoice:m=>{const c=wrapParameterChoice(m.parameterChoice);if(!validChoice(c))throw fail('MODEL_SELECTION_INVALID');return c;},fixedChoice:()=>wrapParameterChoice(initial)});
}
// A summary version is bound to the exact dispatched stage, while the session's
// default summary model remains unchanged by a per-purpose parameter override.
export function createCardParameterState(policy){
 const base=createCardState(policy);
 return Object.freeze({...base,memory:Object.freeze({...base.memory,memoryContextPolicy(s,a){
  const p=requirementsPolicy(s,base.memory.memoryContextPolicy(s,a));return {...p,baseRevision:requirementsPolicy(s,base.summary.effectiveContextPolicy(s,a.summary,a.history)).revision};
 }}),summary:Object.freeze({...base.summary,effectiveContextPolicy(s,...args){return requirementsPolicy(s,base.summary.effectiveContextPolicy(s,...args));},modelSummaryVersion(s,input){
  base.summary.requireSummaryState(s);
  const configured=s.rollingSummary.config.model,selected=input.model;
  const operations=[...Object.values(s.memoryTasks?.operations||{}),...Object.values(s.summaryTasks?.operations||{})];
  const frozen=operations.some(o=>canonical(o.generation?.choices?.summary)===canonical(selected)&&o.tasks.some(t=>['summary','compression'].includes(t.purpose)&&t.requestId===input.receipt?.requestId&&canonical(t.selection)===canonical(selected)));
  if(!configured||!policy.validChoice(selected)||!frozen||configured.model!==selected.model||configured.selectionId!==selected.selectionId||configured.selectionRevision!==selected.selectionRevision)throw fail('SUMMARY_RECEIPT_INVALID');
  const projected={...s,rollingSummary:sealSummary({...s.rollingSummary,config:{...s.rollingSummary.config,model:clone(selected)}})};
  return base.summary.modelSummaryVersion(projected,input);
 }})});
}
const baseChoice=(s,p)=>p==='story'?s.modelState.current:p==='summary'?s.rollingSummary.config.model:s.memoryTasks.config.model;
const expectedFor=(s,a)=>({parameterRevision:s.generationParameters.revision,authorizationRevision:a.revision});
const pending=s=>Object.values(s.memoryTasks?.operations||{}).some(x=>['pending','unknown'].includes(x.status))||Object.values(s.summaryTasks?.operations||{}).some(x=>['pending','unknown'].includes(x.status));
function intent(v){if(!exact(v,['operationId','expectedSessionRevision','expectedParameterRevision','baseHash','purpose','selection'])||typeof v.operationId!=='string'||!/^[A-Za-z0-9-]{1,80}$/.test(v.operationId)||!integer(v.expectedSessionRevision)||!integer(v.expectedParameterRevision)||!hash(v.baseHash)||!purposes.includes(v.purpose))throw fail('PARAMETER_INPUT_INVALID',400);return clone(v);}
export async function defaultParameterChoice(control,modelId,p='story'){
 const c=await control.parameterCatalog(),e=c.models.find(x=>x.modelId===modelId&&x.available);
 if(!e)throw fail('MODEL_SELECTION_UNAVAILABLE');
 return control.selectParameters({format:PARAMETER_SELECTION_FORMAT,catalogRevision:c.revision,selectionId:e.selectionId,selectionRevision:e.selectionRevision,capabilityRevision:e.capabilityRevision,
  profile:{format:PARAMETER_PROFILE,purpose:p,modelId,parameters:{format:PARAMETER_CONFIG,values:{}}}});
}
export function createParameterStore(profile){
 const Base=withCardRequirements(createMemoryStoreClass(profile));
 return class ParameterStore extends Base{
  expectations=new Map();
  parameterReads=Promise.resolve();
  async prepareImportedSession(source,child){child.generationParameters=emptyCardParameters(source.generationParameters.values);child.requirements=emptyCardRequirements(source.requirements.text);}
  async prepareOpeningFork(source,child){child.generationParameters=emptyCardParameters(source.turns[0].model.selection.parameterContext?.values);child.requirements=forkCardRequirements(source,0);}

  async requireRuntime(s){await super.requireRuntime(s);validateCardParameters(s.generationParameters);validateFutureConfigurations(s);}
  async pluginSession(id){const v=await super.pluginSession(id),s=await this.read(id);return {...v,comparisonCompatible:!!this.comparisonEnabled&&v.memoryPalaceCompatible,generationParametersCompatible:v.memoryPalaceCompatible&&!!s.generationParameters,parameterRevision:s.generationParameters?.revision};}
  async send(id,input){
   const {expectedGeneration,...base}=input,s=await this.read(id);await this.requireRuntime(s);
   if(!exact(expectedGeneration,['parameterRevision','authorizationRevision'])||!integer(expectedGeneration.parameterRevision)||!hash(expectedGeneration.authorizationRevision))throw fail('PARAMETER_SEND_SNAPSHOT_REQUIRED');
   const key=id+'/'+base.operationId,old=s.memoryTasks.operations[base.operationId]||s.summaryTasks?.operations?.[base.operationId];
   if(old?.comparison)throw fail('COMPARISON_RECOVERY_REQUIRED');
   if(old?.generation){if(canonical(old.generation.expected)!==canonical(expectedGeneration))throw fail('PARAMETER_OPERATION_COLLISION');}
   else if(canonical(expectedFor(s,await this.plugins.parameterAuthorization(id)))!==canonical(expectedGeneration))throw fail('PARAMETER_CONFIGURATION_CHANGED');
   if(!old){await this.control.freeze(s,base,['story',...((await this.plugins.summaryAuthorization(id)).enabled?['summary']:[]),...((await this.plugins.memoryAuthorization(id)).enabled?['memory']:[])]);}
   this.expectations.set(key,clone(expectedGeneration));try{return await super.send(id,base);}finally{this.expectations.delete(key);}
  }
  async parameterStatus(id){
   const s=await this.read(id);await this.requireRuntime(s);const a=await this.plugins.parameterAuthorization(id),q=s.generationParameters;
   return {sessionId:id,sessionRevision:s.revision,revision:q.revision,authorization:a,expected:expectedFor(s,a),values:clone(q.values),base:Object.fromEntries(purposes.map(p=>[p,baseChoice(s,p)])),busy:this.running.has(id)||pending(s)};
  }
  async parameterCatalog(id,p){
   if(!purposes.includes(p))throw fail('PARAMETER_PURPOSE_INVALID');
   // Read-only panels can overlap; each still passes the plugin's invocation
   // and result authorization checks, in order, without retrying a generation.
   const read=this.parameterReads.then(async()=>{const ticket=await this.plugins.invoke({pluginId:PARAMETER_ID,sessionId:id,capability:'model.parameters.read',input:{purpose:p}}),c=await this.parameterControl.parameterCatalog();await this.plugins.validateResult(ticket);return c;});
   this.parameterReads=read.catch(()=>{});return read;
  }
  async checkParameter(id,input){const i=intent(input),s=await this.read(id);await this.requireSelectable(s);await this.parameterCatalog(id,i.purpose);const base=baseChoice(s,i.purpose);
   if(!base||base.configHash!==i.baseHash)throw fail('PARAMETER_MODEL_CHANGED');
   if(i.selection===null)return {valid:true,choice:null,dispatched:false};
   const c=await this.parameterControl.selectParameters(i.selection);
   if(c.parameterProfile.purpose!==i.purpose||c.entry.modelId!==base.model||c.entry.selectionId!==base.selectionId)throw fail('PARAMETER_MODEL_MISMATCH');return {valid:true,choice:c,dispatched:false};
  }
  async configureParameter(id,input){const i=intent(input);return this.exclusive(id,async()=>{
   const s=await this.read(id);await this.requireRuntime(s);const q=s.generationParameters,prior=q.operations[i.operationId];if(prior){if(prior.inputHash!==digest(i))throw fail('PARAMETER_OPERATION_COLLISION');return clone(prior);}
   await this.requireSelectable(s);if(this.running.has(id)||pending(s))throw fail('SESSION_BUSY');
   if(i.expectedSessionRevision!==s.revision||i.expectedParameterRevision!==q.revision)throw fail('PARAMETER_REVISION_CONFLICT');
   if(q.revision>=1000)throw fail('PARAMETER_CONFIGURATION_LIMIT');
   if(i.purpose!=='story'&&!(await this.plugins[i.purpose==='summary'?'summaryAuthorization':'memoryAuthorization'](id)).enabled)throw fail('PARAMETER_PURPOSE_NOT_AUTHORIZED');
   const checked=await this.checkParameter(id,i),configurationHash=digest({intent:i,choice:checked.choice});
   const ticket=await this.plugins.invoke({pluginId:PARAMETER_ID,sessionId:id,capability:'session.parameters.configure',input:{purpose:i.purpose,parameterRevision:q.revision,configurationHash}});
   const operation={status:'applied',inputHash:digest(i),configurationHash,revision:q.revision+1,purpose:i.purpose,choice:checked.choice,receipt:ticket};
   const next=clone(s),body={format:q.format,revision:q.revision+1,values:{...q.values,[i.purpose]:checked.choice},operations:{...q.operations,[i.operationId]:operation}};next.generationParameters=seal(body);next.revision++;
   await this.plugins.commitResult(ticket,()=>this.write(next));return clone(operation);
  });}
  async comparisonFutureStatus(id){
   const s=await this.read(id);await this.requireRuntime(s);const permissions={model:await this.plugins.contextAuthorization(id,'rpg.model-selector'),parameters:await this.plugins.parameterAuthorization(id)};
   const allowed=permissions.model.enabled&&permissions.parameters.enabled;
   return {sessionId:id,expectedRevision:s.revision,expectedModelRevision:s.modelState.revision,expectedParameterRevision:s.generationParameters.revision,expectedAuthorizationRevision:digest(permissions),allowed,
    reason:allowed?null:'COMPARISON_FUTURE_AUTHORIZATION_REQUIRED',defaultChoice:permissions.parameters.enabled?s.generationParameters.values.story??s.modelState.current.parameterChoice:s.modelState.current.parameterChoice};
  }
  async comparisonFutureOperation(id,operationId){const s=await this.read(id);await this.requireRuntime(s);return clone((s.futureConfigurations??[]).find(p=>p.intent.operationId===operationId)??{status:'not_applied'});}
  configureFutureStory(value,source){const i=futureIntent(value);return this.exclusive(i.sessionId,async()=>{
   const s=await this.read(i.sessionId);await this.requireRuntime(s);const prior=priorFuture(s,i,source);if(prior)return prior;
   await this.requireSelectable(s);if(this.running.has(s.id)||pending(s))throw fail('SESSION_BUSY');
   const status=await this.comparisonFutureStatus(s.id);for(const key of ['expectedRevision','expectedModelRevision','expectedParameterRevision','expectedAuthorizationRevision'])if(status[key]!==i[key])throw fail('COMPARISON_FUTURE_CONFIGURATION_CHANGED');
   if(!status.allowed)throw fail(status.reason);if(s.generationParameters.revision>=1000)throw fail('PARAMETER_CONFIGURATION_LIMIT');
   await this.parameterControl.parameterCatalog();await this.parameterControl.validate(source);const choice=await this.parameterControl.selectParameters(modelChoiceInput(source));if(canonical(choice)!==canonical(source))throw fail('MODEL_CAPABILITY_CHANGED');
   const def=modelChoiceInput(choice);def.profile.parameters.values={};const base=wrapParameterChoice(await this.parameterControl.selectParameters(def)),ids=futureIds(i),q=s.generationParameters,m=s.modelState;
   if(m.operations[ids.model]||q.operations[ids.parameter]||s.memoryTasks.operations[i.operationId])throw fail('PARAMETER_OPERATION_COLLISION');
   const pi={operationId:ids.parameter,expectedSessionRevision:s.revision,expectedParameterRevision:q.revision,baseHash:base.configHash,purpose:'story',selection:modelChoiceInput(choice)},configurationHash=digest({intent:pi,choice});
   const mt=await this.plugins.invoke({pluginId:'rpg.model-selector',sessionId:s.id,capability:'session.model.select',input:{selectionId:base.selectionId,selectionRevision:m.revision}});
   const pt=await this.plugins.invoke({pluginId:PARAMETER_ID,sessionId:s.id,capability:'session.parameters.configure',input:{purpose:'story',parameterRevision:q.revision,configurationHash}});
   const next=clone(s);next.modelState.revision++;next.modelState.current=base;next.modelState.timeline.push({revision:next.modelState.revision,choice:clone(base)});
   next.modelState.operations[ids.model]={hash:digest(i),revision:next.modelState.revision,selection:clone(base)};
   const operation={status:'applied',inputHash:digest(pi),configurationHash,revision:q.revision+1,purpose:'story',choice,receipt:pt};
   next.generationParameters=seal({format:q.format,revision:q.revision+1,values:{...q.values,story:choice},operations:{...q.operations,[ids.parameter]:operation}});next.revision++;
   const receipt=futureReceipt(i,choice,next.revision,next.modelState.revision,q.revision+1);next.futureConfigurations??=[];next.futureConfigurations.push(receipt);
   await this.plugins.commitResults([mt,pt],async()=>{if((await this.comparisonFutureStatus(s.id)).expectedAuthorizationRevision!==i.expectedAuthorizationRevision)throw fail('COMPARISON_FUTURE_CONFIGURATION_CHANGED');await this.write(next);});return clone(receipt);
  });}
  async parameterOperation(id,operationId){const s=await this.read(id);await this.requireRuntime(s);return clone(s.generationParameters.operations[operationId]||{status:'not-applied'});}
 };
}
export async function createParameterTransport({store,control,provider,budget,plugins}){
 const validateCurrent=async choice=>{await control.parameterCatalog();return control.validate(choice);};
 const choices=async()=>{const c=await control.parameterCatalog(),out=[];for(const e of c.models){if(!e.available){out.push({model:e.modelId,name:e.name,selectionId:e.selectionId,selectionRevision:e.selectionRevision,available:false});continue;}
  try{const choice=await defaultParameterChoice(control,e.modelId);out.push({model:e.modelId,name:e.name,selectionId:e.selectionId,selectionRevision:e.selectionRevision,available:true,parameterChoice:choice});}catch{out.push({model:e.modelId,name:e.name,selectionId:e.selectionId,selectionRevision:e.selectionRevision,available:false});}}
  return out;
 };
 const request=task=>({id:task.requestId,operationId:task.requestId,sessionId:task.sessionId,kind:task.purpose,messages:clone(task.messages),messagesHash:digest(task.messages),modelChoice:clone(task.selection.parameterChoice),cardId:store.cardState.cardId,selectionHash:digest(task.selection)});
 const project=(task,r,dispatched)=>{
  if(!r)return {raw:'',finishReason:null,dispatched,evidence:null};
  const logicalRequestHash=sha(JSON.stringify({sessionId:task.sessionId,requestId:task.requestId,selectionId:task.selection.selectionId,selectionRevision:task.selection.selectionRevision,messages:task.messages,parameters:task.selection.parameters}));
  return {raw:r.result.text,finishReason:r.result.finishReason,dispatched:r.control.dispatched,failureKnown:r.control.status==='failed',failureCode:r.control.error,evidence:{kind:'typed-card-control',controlReceipt:clone(r),record:{status:r.result.status==='completed'?'complete':r.control.status,sessionId:task.sessionId,requestId:task.requestId,requestedModel:task.selection.model,actualModel:r.control.actualModel,parameters:clone(task.selection.parameters),rawHash:sha(r.result.text),requestHash:logicalRequestHash,controlRequestHash:r.control.requestHash,sourceRequestHash:r.requestHash,retries:r.control.retries}}};
 };
 return {budget,catalog:choices,status:async()=>({...await budget.status(),enabled:true}),
  async freeze(s,input,stagePurposes){
   validateCardParameters(s.generationParameters);const authorization=await plugins.parameterAuthorization(s.id),expected=expectedFor(s,authorization),admission=store.expectations.get(s.id+'/'+input.operationId);
   if(admission&&canonical(admission)!==canonical(expected))throw fail('PARAMETER_CONFIGURATION_CHANGED');
   const frozen={expected,revision:s.generationParameters.revision,authorization,values:clone(s.generationParameters.values),choices:{}};
   for(const p of new Set(stagePurposes.map(purpose))){const base=baseChoice(s,p);if(!base)throw fail('PARAMETER_MODEL_REQUIRED');
    const override=authorization.enabled?frozen.values[p]:null;let c;
    if(override){if(override.entry.modelId!==base.model||override.entry.selectionId!==base.selectionId)throw fail('PARAMETER_MODEL_MISMATCH');await validateCurrent(override);c=override;}
    else c=await defaultParameterChoice(control,base.model,p);
    frozen.choices[p]=wrapParameterChoice(c,{revision:frozen.revision,authorization,values:frozen.values});
   }
   return frozen;
  },
  async selectFrozen(op,p){const c=op.generation?.choices[purpose(p)];if(!c)throw fail('PARAMETER_FROZEN_SELECTION_MISSING');await validateCurrent(c.parameterChoice);return clone(c);},
  async parameterFence(s,op){const x=op.generation;if(!x)throw fail('PARAMETER_SNAPSHOT_MISSING');return {authorizationRevision:x.authorization.revision,sessionRevision:(await store.read(s.id)).revision,parameterRevision:x.revision};},
  async execute(task,signal,guard){
   const r=request(task);let result=null,failure=null;
   try{if(signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');await provider.ready();await validateCurrent(task.selection.parameterChoice);modelCapacity(task.selection.parameterChoice,task.messages);controlledPayload(r);
    const started=await guard(async()=>{if(signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');await budget.dispatched(task.lease,task.requestId,digest(r));return {response:provider.dispatch(r)};});result=await started.response;
   }catch(e){failure=e.code||'CONTROL_RESULT_UNKNOWN';}
   const dispatched=await budget.wasDispatched(task.lease);if(!result&&dispatched)try{result=await provider.lookup(r);}catch{}
   return {...project(task,result,dispatched),failure};
  },
  async recover(task){let result=null;try{result=await provider.lookup(request(task));}catch{}return project(task,result,await budget.wasDispatched(task.lease));}
 };
}
export async function openCardParameterTransport(options){
 const {directory,budgetDirectory,cardId,limit,origin,serviceToken,recoverDeadOwner=false}=options;
 if(!Number.isSafeInteger(limit)||limit<0||limit>100||typeof budgetDirectory!=='string')throw fail('PARAMETER_BUDGET_REQUIRED');
 const provider=await ControlledProvider.open({directory:join(directory,'controlled-requests'),origin,serviceToken,recoverDeadOwner});
 try{return {provider,budget:await createMemoryBudget({directory:budgetDirectory,cardId,limit}),close:()=>provider.close()};}catch(e){await provider.close();throw e;}
}

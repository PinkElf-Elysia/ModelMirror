import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {VersionedSessionStore} from './versioned-store.mjs';
import {atomicJson} from './branch-archive.mjs';
import {ModelBranchArchive} from './model-branch-archive.mjs';
import {assemble,validateParameters} from '../card-replica/lib/assembly.mjs';
import {models} from './provider.mjs';
import {modelRuntime,modelRuntimeStatus,bindModelRuntime,fixedChoice,controlledChoice,initialModelState,requireModelState,validChoice} from './model-runtime.mjs';
import {canonical,sha,fail,MODEL_SELECTOR_ID} from '../plugins/catalog.mjs';

const validId=x=>typeof x==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(x);
const revision=x=>Number.isSafeInteger(x)&&x>=0;
const exact=(x,keys)=>x&&typeof x==='object'&&!Array.isArray(x)&&Object.keys(x).sort().join(',')===keys.sort().join(',');
export class ModelSessionStore extends VersionedSessionStore {
 constructor(directory,generate,realGenerate=null,{control,evidence}={}){
  super(directory,generate,realGenerate);this.control=control;this.evidence=evidence;
  this.archive=new ModelBranchArchive(join(directory,'branches'));
 }
 status(s){return modelRuntimeStatus(s);}
 async requireRuntime(s){
  if(s.runtime?.format!==2)return super.requireRuntime(s);
  const state=this.status(s);if(!state.compatible)throw fail(state.code);
  requireModelState(s);await modelRuntime.verify();
 }
 async write(s){
  if(s.provenance)return this.archive.writeSession(s);
  return atomicJson(this.path(s.id),s);
 }
 async create(input){
  if(!input||Object.keys(input).some(k=>!['characterText','world','params','mode'].includes(k)))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const {characterText,world,mode}=input;
  if(mode!=='offline'&&mode!=='real')throw fail('PROVIDER_DISABLED',403);
  if(mode==='real'&&!this.realGenerate&&!(await this.control?.status?.())?.enabled)throw fail('PROVIDER_DISABLED',403);
  if(typeof characterText!=='string'||!characterText.trim()||characterText.length>200000||!['表世界','里世界'].includes(world))throw fail('SESSION_CONFIGURATION_INVALID',400);
  const params=validateParameters({...input.params,max_tokens:input.params?.max_tokens??16384});
  if(Object.entries(models.earth.parameters).some(([k,v])=>params[k]!==v))throw fail('MODEL_PARAMETERS_INCOMPATIBLE');
  await modelRuntime.verify();
  const s={id:randomUUID(),name:(characterText.match(/姓名[：:]([^\n]+)/)?.[1]||'未命名角色').slice(0,100),created:new Date().toISOString(),characterText,world,params,mode,revision:0,history:[],turns:[],requests:{},modelState:initialModelState(fixedChoice())};
  s.runtime=bindModelRuntime(s);await this.write(s);return s;
 }
 async catalog(id,plugins){
  const s=await this.read(id);await this.requireSelectable(s);
  if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  const ticket=await plugins.invoke({pluginId:MODEL_SELECTOR_ID,sessionId:id,capability:'model.catalog.read'});
  const result=await this.control.catalog();await plugins.validateResult(ticket);return result;
 }
 async requireSelectable(s){
  if(s.runtime?.format!==2)throw fail('MODEL_LEGACY_READ_ONLY');
  await this.requireRuntime(s);
  if(!this.control)throw fail('CONTROL_DISABLED');
  if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('MODEL_UNCONFIRMED_REQUEST');
 }
 async select(id,input,plugins){
  if(!exact(input,['operationId','expectedSessionRevision','selectionRevision','selectionId','catalogRevision'])||!validId(input.operationId)||!revision(input.expectedSessionRevision)||!revision(input.selectionRevision)||typeof input.selectionId!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(input.selectionId)||typeof input.catalogRevision!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(input.catalogRevision))throw fail('MODEL_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id);if(s.runtime?.format!==2)throw fail('MODEL_LEGACY_READ_ONLY');
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
 async send(id,input){
  const existing=await this.read(id);if(existing.runtime?.format!==2)return super.send(id,input);
  if(!exact(input,['input','requestId','revision','expectedSelectionRevision'])||typeof input.input!=='string'||!input.input.trim()||input.input.length>20000||!validId(input.requestId)||!revision(input.revision)||!revision(input.expectedSelectionRevision))throw fail('MODEL_SEND_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id);await this.requireRuntime(s);
   const state=requireModelState(s),inputHash=sha(canonical(input));
   if(s.provenance?.requestOrigins?.some(o=>o.requestId===input.requestId))throw fail('BRANCH_INHERITED_REQUEST_ID');
   const prior=s.requests[input.requestId];if(prior){if(prior.inputHash!==inputHash)throw fail('MODEL_REQUEST_COLLISION');return s;}
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('MODEL_UNCONFIRMED_REQUEST');
   if(s.revision!==input.revision||state.revision!==input.expectedSelectionRevision)throw fail('MODEL_SELECTION_CONFLICT');
   const selection=structuredClone(state.current);
   if(s.mode==='real'&&selection.kind==='fixed'&&!this.realGenerate)throw fail('PROVIDER_DISABLED');
   if(s.mode==='real'&&selection.kind==='controlled'&&!this.control)throw fail('CONTROL_DISABLED');
   const controller=new AbortController();this.running.set(id,controller);
   try{
    const assembled=assemble({characterText:s.characterText,input:input.input,history:s.history,world:s.world});
    const request=s.requests[input.requestId]={status:'pending',inputHash,assemblyHash:sha(canonical(assembled.messages)),selectionRevision:state.revision,selection,at:new Date().toISOString()};
    await this.write(s);
    let raw,outputEvidence=null;
    try{
     if(s.mode==='real'&&selection.kind==='controlled'){
      const list=await this.control.catalog({signal:controller.signal});
      if(!list.some(m=>m.available&&m.selectionId===selection.selectionId&&m.selectionRevision===selection.selectionRevision&&m.model===selection.model))throw fail('MODEL_SELECTION_UNAVAILABLE');
     }
     const args={messages:assembled.messages,params:selection.parameters,signal:controller.signal,sessionId:id,requestId:input.requestId};
     raw=s.mode==='offline'?await this.generate(args):selection.kind==='controlled'?await this.control.generate('earth',{...args,selection}):await this.realGenerate(args);
     if(s.mode==='real'){
      outputEvidence=await this.evidence?.(id,input.requestId);
      const record=outputEvidence?.record;
      if(!record||record.status!=='complete'||record.sessionId!==id||record.requestId!==input.requestId||record.requestedModel!==selection.model||record.rawHash!==sha(raw)||canonical(record.parameters)!==canonical(selection.parameters))throw fail('MODEL_RECEIPT_UNCONFIRMED');
     }
     request.evidence=outputEvidence;
     if(controller.signal.aborted)request.status='cancelled';
     else{
      if(typeof raw!=='string'||!raw.trim())throw fail('MODEL_EMPTY_OUTPUT');
      const model={selectionRevision:state.revision,selection,requestedModel:selection.model,actualModel:s.mode==='offline'?null:outputEvidence.record.actualModel??null,evidence:outputEvidence,mode:s.mode};
      s.history.push({role:'user',content:assembled.current},{role:'assistant',content:raw});
      s.turns.push({requestId:input.requestId,input:input.input,raw,rawHash:sha(raw),at:new Date().toISOString(),model});s.revision++;request.status='complete';
     }
    }catch(e){
     if(!outputEvidence&&s.mode==='real')outputEvidence=await this.evidence?.(id,input.requestId).catch(()=>null);
     request.evidence=outputEvidence??null;
     const known=['BUDGET_EXHAUSTED','CONTROL_DISABLED','PROVIDER_DISABLED','CANCELLED_BEFORE_DISPATCH','CONTROL_PARAMETERS_MISMATCH','MODEL_SELECTION_UNAVAILABLE'];
     request.status=controller.signal.aborted?'cancelled':s.mode==='offline'||known.includes(e.code)?'failed':'pending';
     request.error=request.status==='pending'?'MODEL_RESULT_UNCONFIRMED':request.status==='cancelled'?'CANCELLED':'MODEL_GENERATION_FAILED';
    }
    await this.write(s);return s;
   }finally{this.running.delete(id);}
  });
 }
}

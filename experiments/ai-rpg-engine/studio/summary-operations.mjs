import {atomicJson} from './branch-archive.mjs';
import {exact,operationId,revision,requireHistoryState} from './history-state.mjs';
import {requireModelState,controlledChoice,validChoice} from './model-runtime.mjs';
import {summaryTaskBlock} from './summary-tasks.mjs';
import {canonical,sha,fail,MODEL_SELECTOR_ID} from '../plugins/catalog.mjs';
export async function selectSummaryModel(id,input,plugins){
  if(!exact(input,['operationId','expectedSessionRevision','selectionRevision','selectionId','catalogRevision'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!revision(input.selectionRevision)||typeof input.selectionId!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(input.selectionId)||typeof input.catalogRevision!=='string'||! /^[A-Za-z0-9_-]{1,128}$/.test(input.catalogRevision))throw fail('MODEL_OPERATION_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id);if(s.runtime?.format!==4)throw fail('SUMMARY_RUNTIME_INCOMPATIBLE');
   const state=requireModelState(s),operationHash=sha(canonical(input));
   const prior=Object.hasOwn(state.operations,input.operationId)?state.operations[input.operationId]:null;
   if(prior){if(prior.hash!==operationHash)throw fail('MODEL_OPERATION_COLLISION');return {session:s,operation:prior,replayed:true};}
   await this.requireSelectable(s);
   if(s.revision!==input.expectedSessionRevision||state.revision!==input.selectionRevision)throw fail('MODEL_SELECTION_CONFLICT');
   if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
   const result=await plugins.invoke({pluginId:MODEL_SELECTOR_ID,sessionId:id,capability:'session.model.select',input:{selectionId:input.selectionId,selectionRevision:input.selectionRevision}});
   const options=await this.control.catalog({sessionId:id});
   const selected=options.find(m=>m.selectionId===input.selectionId&&m.selectionRevision===input.catalogRevision&&m.available);
   if(!selected)throw fail('MODEL_SELECTION_UNAVAILABLE');
   const next=controlledChoice(selected);if(!validChoice(next))throw fail('MODEL_SELECTION_INVALID');
   state.revision++;state.current=next;state.timeline.push({revision:state.revision,choice:structuredClone(next)});
   const operation={hash:operationHash,revision:state.revision,selection:structuredClone(next)};
   state.operations[input.operationId]=operation;
   const path=s.provenance?this.archive.path(s.id):this.path(s.id);
   const value=s.provenance?{...await this.archive.read(s.id),session:s}:s;
   await atomicJson(path,value,publish=>plugins.commitResult(result,async()=>{await this.requireRuntime(await this.read(id));return this.fileOperation(id,publish);}));
   return {session:s,operation,replayed:false};
  });
 }
export async function createSummaryBranch(id,input,plugins){
  if(!exact(input,['operationId','expectedSessionRevision','turn','name'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!Number.isSafeInteger(input.turn)||input.turn<1||typeof input.name!=='string'||!input.name.trim()||input.name!==input.name.trim()||input.name.length>80)throw fail('BRANCH_OPERATION_INVALID',400);
  if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  return this.exclusive(id,async()=>{
   const operation={sessionId:id,...input},prior=await this.archive.find(operation);
   if(prior)return {session:await this.read(prior.session.id),replayed:true,snapshotHash:prior.snapshotHash};
   const s=await this.read(id);await this.requireRuntime(s);
   if(requireHistoryState(s).pending||s.rollingSummary.pending||summaryTaskBlock(s))throw fail('SUMMARY_OPERATION_UNCONFIRMED');
   if(s.revision!==input.expectedSessionRevision)throw fail('BRANCH_SESSION_CONFLICT');
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('BRANCH_UNCONFIRMED_REQUEST');
   const ticket=await plugins.invoke({sessionId:id,capability:'session.branch.prepare',input:{turn:input.turn,name:input.name}});
   const envelope=this.archive.prepare(s,operation);
   await this.archive.publish(envelope,publish=>plugins.commitResult(ticket,async()=>{await this.requireRuntime(await this.read(id));return this.fileOperation(id,publish);}));
   return {session:envelope.session,replayed:false,snapshotHash:envelope.snapshotHash};
  });
 }

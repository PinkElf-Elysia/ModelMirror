import {canonical,sha,fail} from '../plugins/catalog.mjs';
import {requireModelState} from './model-runtime.mjs';
import {exact,operationId,revision,requireHistoryState,effectiveHistoryPolicy} from './history-state.mjs';
import {assembleHistoryRequest} from './history-assembly.mjs';
export async function sendHistory(store,id,input,plugins){
 if(!exact(input,['input','requestId','revision','expectedSelectionRevision','expectedHistoryPolicyRevision'])||typeof input.input!=='string'||!input.input.trim()||input.input.length>20000||!operationId(input.requestId)||!revision(input.revision)||!revision(input.expectedSelectionRevision)||! /^[a-f0-9]{64}$/.test(input.expectedHistoryPolicyRevision))throw fail('HISTORY_SEND_INVALID',400);
 input=structuredClone(input);
 return store.exclusive(id,async()=>{
  const s=await store.read(id);await store.requireRuntime(s);
  const model=requireModelState(s),h=requireHistoryState(s),inputHash=sha(canonical(input));
  if(s.provenance?.requestOrigins?.some(o=>o.requestId===input.requestId))throw fail('BRANCH_INHERITED_REQUEST_ID');
  const prior=s.requests[input.requestId];if(prior){if(prior.inputHash!==inputHash)throw fail('MODEL_REQUEST_COLLISION');return s;}
  if(h.pending)throw fail('HISTORY_OPERATION_UNCONFIRMED');
  if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('MODEL_UNCONFIRMED_REQUEST');
  if(s.revision!==input.revision||model.revision!==input.expectedSelectionRevision)throw fail('MODEL_SELECTION_CONFLICT');
  if(!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  let policy=effectiveHistoryPolicy(s,await plugins.historyAuthorization(id));
  if(policy.revision!==input.expectedHistoryPolicyRevision)throw fail('HISTORY_POLICY_CONFLICT');
  const selection=structuredClone(model.current);
  if(s.mode==='real'&&selection.kind==='fixed'&&!store.realGenerate)throw fail('PROVIDER_DISABLED');
  if(s.mode==='real'&&selection.kind==='controlled'&&!store.control)throw fail('CONTROL_DISABLED');
  const controller=new AbortController();store.running.set(id,controller);
  try{
   let assembled=assembleHistoryRequest(s,input.input,policy),evidence=null,guardFailure=null;
   const request=s.requests[input.requestId]={status:'pending',inputHash,assemblyHash:assembled.historyPolicy.requestHash,historyPolicy:structuredClone(assembled.historyPolicy),selectionRevision:model.revision,selection,at:new Date().toISOString()};
   await store.write(s);
   try{
    if(s.mode==='real'&&selection.kind==='controlled'){
     const list=await store.control.catalog({signal:controller.signal});
     if(!list.some(m=>m.available&&m.selectionId===selection.selectionId&&m.selectionRevision===selection.selectionRevision&&m.model===selection.model))throw fail('MODEL_SELECTION_UNAVAILABLE');
    }
    const fresh=effectiveHistoryPolicy(s,await plugins.historyAuthorization(id));
    if(fresh.revision!==policy.revision){
     if(fresh.enabled)throw fail('HISTORY_POLICY_CONFLICT');
     // Explicit revocation during admission restores full history before freezing wire bytes.
     policy=fresh;assembled=assembleHistoryRequest(s,input.input,policy);
     request.historyPolicy=structuredClone(assembled.historyPolicy);request.assemblyHash=assembled.historyPolicy.requestHash;await store.write(s);
    }
    const assertHistoryPolicy=async()=>{
     try{
      if(controller.signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');
      const disk=await store.read(id);await store.requireRuntime(disk);
      const now=effectiveHistoryPolicy(disk,await plugins.historyAuthorization(id));
      if(now.revision!==policy.revision)throw fail('HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH');
     }catch(e){guardFailure=e;throw e;}
    };
    await assertHistoryPolicy();
    const beginHistoryDispatch=async start=>{try{return await plugins.startHistoryDispatch(id,policy.authorizationRevision,start);}catch(e){guardFailure=e;throw e;}};
    const args={beginHistoryDispatch,messages:assembled.messages,params:selection.parameters,signal:controller.signal,sessionId:id,requestId:input.requestId,assertHistoryPolicy};
    const raw=s.mode==='offline'?await store.generate(args):selection.kind==='controlled'?await store.control.generate('earth',{...args,selection}):await store.realGenerate(args);
    if(s.mode==='real'){
     evidence=await store.evidence?.(id,input.requestId);const record=evidence?.record;
     if(!record||record.status!=='complete'||record.sessionId!==id||record.requestId!==input.requestId||record.requestedModel!==selection.model||record.rawHash!==sha(raw)||canonical(record.parameters)!==canonical(selection.parameters))throw fail('MODEL_RECEIPT_UNCONFIRMED');
    }
    request.evidence=evidence;
    if(controller.signal.aborted)request.status='cancelled';
    else{
     if(typeof raw!=='string'||!raw.trim())throw fail('MODEL_EMPTY_OUTPUT');
     const turnModel={selectionRevision:model.revision,selection,requestedModel:selection.model,actualModel:s.mode==='offline'?null:evidence.record.actualModel??null,evidence,mode:s.mode};
     s.history.push({role:'user',content:assembled.current},{role:'assistant',content:raw});
     s.turns.push({requestId:input.requestId,input:input.input,raw,rawHash:sha(raw),at:new Date().toISOString(),model:turnModel,historyPolicy:structuredClone(request.historyPolicy)});s.revision++;request.status='complete';
    }
   }catch(e){
    if(!evidence&&s.mode==='real')evidence=await store.evidence?.(id,input.requestId).catch(()=>null);
    request.evidence=evidence??null;
    const known=['BUDGET_EXHAUSTED','CONTROL_DISABLED','PROVIDER_DISABLED','CANCELLED_BEFORE_DISPATCH','CONTROL_PARAMETERS_MISMATCH','MODEL_SELECTION_UNAVAILABLE','HISTORY_POLICY_CONFLICT'];
    request.status=controller.signal.aborted?'cancelled':guardFailure||s.mode==='offline'||known.includes(e.code)?'failed':'pending';
    request.error=request.status==='pending'?'MODEL_RESULT_UNCONFIRMED':request.status==='cancelled'?'CANCELLED':guardFailure?'HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH':'MODEL_GENERATION_FAILED';
   }
   await store.write(s);return s;
  }finally{store.running.delete(id);}
 });
}

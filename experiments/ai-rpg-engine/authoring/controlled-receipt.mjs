import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {validateModelChoice,CONTROL_CHOICE_FORMAT,PARAMETER_CHOICE_FORMAT} from './model-control.mjs';
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),text=v=>typeof v==='string'&&v.isWellFormed();
export const CONTROLLED_MODE='control-plane-joint';
export const CONTROLLED_SESSION_FORMAT='modelmirror.rpg.controlled-session/1';
export const CONTROLLED_BRANCH_FORMAT='modelmirror.rpg.controlled-session/2';
export const isControlledSession=s=>[CONTROLLED_SESSION_FORMAT,CONTROLLED_BRANCH_FORMAT].includes(s?.format);
export function controlledPayload(request){
 canonical(request);const choice=request.modelChoice??request.modelConfiguration?.choice;validateModelChoice(choice);
 const v2=choice.format===PARAMETER_CHOICE_FORMAT,purpose=request.kind??'story';
 if(![CONTROL_CHOICE_FORMAT,PARAMETER_CHOICE_FORMAT].includes(choice.format)||choice.source!=='control-plane-cache'||!isId(request.id)||!isId(request.operationId)||!isId(request.sessionId)||
   !Array.isArray(request.messages)||request.messagesHash!==digest(request.messages)||!['story','summary','compression','memory'].includes(request.kind??'story'))fail('CONTROL_REQUEST_INVALID');
 const character=Object.hasOwn(request,'messageContract');
 if(character&&(request.messageContract!=='modelmirror.rpg.character-messages/1'||purpose!=='story'))fail('CONTROL_REQUEST_INVALID');
 if(v2&&choice.parameterProfile.purpose!==(purpose==='compression'?'summary':purpose))fail('CONTROL_REQUEST_INVALID');
 return {format:v2?'modelmirror.rpg.authoring-task/2':'modelmirror.rpg.authoring-task/1',sessionId:request.sessionId,requestId:request.id,operationId:request.operationId,purpose:request.kind??'story',
  ...(character?{messageContract:request.messageContract}:{}),
  sourceRequestHash:digest(request),catalogRevision:choice.catalogRevision,selectionId:choice.entry.selectionId,selectionRevision:choice.entry.selectionRevision,
  ...(v2?{capabilityRevision:choice.entry.capabilityRevision}:{}),parameters:structuredClone(v2?choice.parameterProfile.parameters:choice.parameters),messages:structuredClone(request.messages)};
}
export function controlEvidence(value,request){
 canonical(value);keys(value,['status','requestHash','raw','sse','receipt']);const p=controlledPayload(request),c=request.modelChoice??request.modelConfiguration.choice,r=value.receipt;
 const v2=c.format===PARAMETER_CHOICE_FORMAT;
 keys(r,['format','gateway','requestHash','sourceRequestHash','sessionId','requestId','operationId','purpose','selectionId','selectionRevision','catalogRevision','requestedModel','actualModel',
  'parameters','runId','attemptId','dispatched','status','finishReason','error','httpStatus','usage','retries','rawHash','sseHash','sha256',...(v2?['capabilityRevision','parameterResolution','providerRequestHash']:[])]);
 if(!['completed','failed','unknown'].includes(value.status)||value.status!==r.status||!isHash(value.requestHash)||value.requestHash!==r.requestHash||!text(value.raw)||!text(value.sse)||
   r.format!==(v2?'modelmirror.rpg.authoring-task-receipt/2':'modelmirror.rpg.authoring-task-receipt/1')||r.gateway!=='rpg_authoring'||!isHash(r.sha256)||r.sourceRequestHash!==p.sourceRequestHash||
   ['sessionId','requestId','operationId','purpose','selectionId','selectionRevision','catalogRevision'].some(k=>r[k]!==p[k])||
   ![null,c.entry.modelId].includes(r.requestedModel)||!(r.actualModel===null||text(r.actualModel))||
   !(r.parameters===null||same(r.parameters,c.wireParameters))||typeof r.dispatched!=='boolean'||r.retries!==0||r.rawHash!==sha256(value.raw)||r.sseHash!==sha256(value.sse)||
   ![r.runId,r.attemptId].every(v=>v===null||isId(v))||!(r.error===null||typeof r.error==='string'&&/^[a-z0-9_]{1,100}$/.test(r.error))||
   !(r.finishReason===null||typeof r.finishReason==='string'&&/^[a-z0-9_]{1,80}$/.test(r.finishReason))||
   !(r.httpStatus===null||Number.isInteger(r.httpStatus)&&r.httpStatus>=100&&r.httpStatus<=599))fail('CONTROL_RECEIPT_MISMATCH');
 if(v2&&(r.capabilityRevision!==c.entry.capabilityRevision||!(r.providerRequestHash===null||isHash(r.providerRequestHash))||
   !(r.parameterResolution===null||same(r.parameterResolution,c.parameterResolution))||r.dispatched&&(!isHash(r.providerRequestHash)||!same(r.parameterResolution,c.parameterResolution))))fail('CONTROL_RECEIPT_MISMATCH');
 if(r.usage!==null){keys(r.usage,Object.keys(r.usage));if(Object.entries(r.usage).some(([k,v])=>!['prompt_tokens','completion_tokens','total_tokens'].includes(k)||!Number.isSafeInteger(v)||v<0))fail('CONTROL_RECEIPT_MISMATCH');}
 if(r.dispatched&&(!r.runId||!r.attemptId||r.requestedModel!==c.entry.modelId||!same(r.parameters,c.wireParameters)))fail('CONTROL_RECEIPT_MISMATCH');
 if(r.status==='completed'&&(!r.dispatched||r.error!==null||!['stop','length'].includes(r.finishReason)||(r.actualModel!==null&&r.actualModel!==c.entry.modelId)))fail('CONTROL_RECEIPT_MISMATCH');
 if(r.status!=='completed'&&r.error===null)fail('CONTROL_RECEIPT_MISMATCH');
 // The service's JSON hash is opaque (Python number serialization). Local
 // persistence seals the complete evidence; bindings and raw bytes are checked.
 return value;
}
export function controlReceipt(value,request){
 controlEvidence(value,request);if(value.status==='unknown')return null;
 const body={kind:'control-plane',requestId:request.id,requestHash:digest(request),
  result:{status:value.status,text:value.raw,finishReason:value.status==='failed'?'control_failure':value.receipt.finishReason},
  control:structuredClone(value.receipt),sse:value.sse};
 return {...body,sha256:digest(body)};
}
export function validateControlledReceipt(receipt,request){
 canonical(receipt);if(Object.hasOwn(receipt,'comparison'))return validateComparisonReceipt(receipt,request);keys(receipt,['kind','requestId','requestHash','result','control','sse','sha256']);keys(receipt.result,['status','text','finishReason']);
 const {sha256:hash,...body}=receipt;
 if(receipt.kind!=='control-plane'||hash!==digest(body)||receipt.requestId!==request.id||receipt.requestHash!==digest(request))fail('CONTROL_RECEIPT_MISMATCH');
 const actual=controlReceipt({status:receipt.control.status,requestHash:receipt.control.requestHash,raw:receipt.result.text,sse:receipt.sse,receipt:receipt.control},request);
 if(actual===null||!same(actual,receipt))fail('CONTROL_RECEIPT_MISMATCH');return receipt;
}

// Adoption retains the original controlled request/receipt. The projection binds
// it to the host's frozen request without rewriting provider evidence or calling again.
export function comparisonReceipt(request,remoteRequest,result){
 const evidence=result?.receipt?.evidence;controlEvidence(evidence,remoteRequest);
 const original=controlReceipt(evidence,remoteRequest);if(!original||original.result.status!=='completed'||original.result.finishReason!=='stop')fail('COMPARISON_RESULT_NOT_ADOPTABLE');
 const body={kind:'control-plane',requestId:request.id,requestHash:digest(request),result:structuredClone(original.result),control:structuredClone(original.control),sse:original.sse,comparison:{request:structuredClone(remoteRequest)}};
 const receipt={...body,sha256:digest(body)};return validateComparisonReceipt(receipt,request);
}
function validateComparisonReceipt(receipt,native){
 keys(receipt,['kind','requestId','requestHash','result','control','sse','comparison','sha256']);keys(receipt.comparison,['request']);
 const r=receipt.comparison.request,choice=native.modelChoice??native.modelConfiguration?.choice,{sha256:hash,...body}=receipt;
 if(receipt.kind!=='control-plane'||hash!==digest(body)||receipt.requestId!==native.id||receipt.requestHash!==digest(native)||
  r.sessionId!==native.sessionId||r.kind!==(native.kind??'story')||!isId(r.batchId)||!same(r.messages,native.messages)||!same(r.modelChoice,choice)||!same(r.parameters,native.parameters)||r.messagesHash!==native.messagesHash||
  (r.kind!=='story'&&r.nativeRequestHash!==digest(native)))fail('COMPARISON_ADOPTION_BINDING_INVALID');
 const original=controlReceipt({status:receipt.control.status,requestHash:receipt.control.requestHash,raw:receipt.result.text,sse:receipt.sse,receipt:receipt.control},r);
 if(!original||!same(original.result,receipt.result)||original.result.status!=='completed'||original.result.finishReason!=='stop')fail('COMPARISON_RESULT_NOT_ADOPTABLE');
 return receipt;
}

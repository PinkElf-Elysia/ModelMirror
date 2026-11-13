import {canonical, keys, isId, isHash, sha256, fail} from './contracts.mjs';
import {validateModelChoice, modelCapacity} from './model-control.mjs';
export const COMPARISON_FORMAT='modelmirror.rpg.comparison/1';
export const COMPARISON_LIMITS=Object.freeze({candidates:4,parallel:2,batches:24,replyBytes:524288,contextBytes:4*1024*1024});
export const clone=structuredClone;
export const digest=v=>sha256(canonical(v));
export const same=(a,b)=>canonical(a)===canonical(b);
export const integer=v=>Number.isSafeInteger(v)&&v>=0;
export const terminal=s=>['completed','failed','cancelled','not_dispatched','released'].includes(s);
export const batchId=(sessionId,operationId)=>'compare-'+digest({sessionId,operationId});
export const stageId=(id,name)=>'cmp-'+digest({batchId:id,stage:name});
export function comparisonIntent(value){
 canonical(value);keys(value,['sessionId','operationId','expectedContextRevision','expectedAuthorizationRevision','input','candidates']);
 if(!isId(value.sessionId)||!isId(value.operationId)||!integer(value.expectedContextRevision)||!isHash(value.expectedAuthorizationRevision)||
  typeof value.input!=='string'||!value.input.isWellFormed()||!value.input.trim()||Buffer.byteLength(value.input)>131072||
  !Array.isArray(value.candidates)||value.candidates.length<2||value.candidates.length>4)fail('COMPARISON_INPUT_INVALID');
 const ids=new Set();for(const c of value.candidates){keys(c,['id','choice']);if(!isId(c.id)||ids.has(c.id))fail('COMPARISON_CANDIDATE_INVALID');ids.add(c.id);validateModelChoice(c.choice);
  if(c.choice.parameterProfile&&c.choice.parameterProfile.purpose!=='story')fail('COMPARISON_CANDIDATE_PURPOSE');}
 return clone(value);
}
// Inputs here are already resolved by a trusted host; no browser wire fields.
export function comparisonPlan(intent,state){
 comparisonIntent(intent);keys(state,['contextRevision','authorizationRevision','sourceHash','summaryBefore','summaryAfter','memoryAfter']);
 if(!integer(state.contextRevision)||!isHash(state.authorizationRevision)||!isHash(state.sourceHash)||
  ['summaryBefore','summaryAfter','memoryAfter'].some(k=>typeof state[k]!=='boolean'))fail('COMPARISON_CONTEXT_INVALID');
 if(state.contextRevision!==intent.expectedContextRevision)fail('COMPARISON_CONTEXT_CHANGED');
 if(state.authorizationRevision!==intent.expectedAuthorizationRevision)fail('COMPARISON_AUTHORIZATION_CHANGED');
 const id=batchId(intent.sessionId,intent.operationId),stages=[];
 const add=(name,purpose,phase,candidateId=null)=>stages.push({id:stageId(id,name),name,purpose,phase,candidateId});
 if(state.summaryBefore){add('summary-before','summary','before');add('compression-before','compression','before');}
 for(const c of intent.candidates)add('candidate-'+c.id,'story','candidate',c.id);
 if(state.summaryAfter){add('summary-after','summary','after');add('compression-after','compression','after');}
 if(state.memoryAfter)add('memory-after','memory','after');
 const body={format:COMPARISON_FORMAT,id,intent:clone(intent),state:clone(state),stages,maximumCalls:stages.length};return {...body,hash:digest(body)};
}
export function validatePlan(v){keys(v,['format','id','intent','state','stages','maximumCalls','hash']);if(!same(v,comparisonPlan(v.intent,v.state)))fail('COMPARISON_PLAN_INVALID');return v;}
export function freezeContext(value){
 keys(value,['contextRevision','source','messages']);keys(value.source,['input','history','requirements','variables','summary','memory','worldbook']);canonical(value);
 if(!integer(value.contextRevision)||typeof value.source.input!=='string'||!Array.isArray(value.source.history)||!Array.isArray(value.messages)||value.messages.length<2||value.messages.length>2002||
  value.messages[0].role!=='system'||value.messages.at(-1).role!=='user'||value.messages.filter(m=>m.role==='system').length!==1||Buffer.byteLength(canonical(value))>COMPARISON_LIMITS.contextBytes)fail('COMPARISON_CONTEXT_INVALID');
 for(const m of value.messages){keys(m,['role','content']);if(!['system','user','assistant'].includes(m.role)||typeof m.content!=='string'||!m.content.isWellFormed())fail('COMPARISON_CONTEXT_INVALID');}
 const body=clone(value);return {...body,sourceHash:digest(body.source),messagesHash:digest(body.messages),hash:digest(body)};
}
export function validateContext(v){const {sourceHash,messagesHash,hash,...body}=v;if(!same(v,freezeContext(body)))fail('COMPARISON_CONTEXT_INVALID');return v;}
export function candidateRequest(plan,stage,context){
 validatePlan(plan);validateContext(context);const c=plan.intent.candidates.find(x=>x.id===stage.candidateId);if(!c||stage.phase!=='candidate')fail('COMPARISON_STAGE_INVALID');
 const choice=c.choice;modelCapacity(choice,context.messages);
 return {id:stage.id,operationId:stage.id,sessionId:plan.intent.sessionId,kind:'story',batchId:plan.id,contextHash:context.hash,
  messages:clone(context.messages),messagesHash:context.messagesHash,modelChoice:clone(choice),parameters:clone(choice.wireParameters)};
}
export function validateRequest(r,plan,stage){
 canonical(r);if(!r||r.id!==stage.id||r.operationId!==stage.id||r.sessionId!==plan.intent.sessionId||r.kind!==stage.purpose||r.batchId!==plan.id||r.messagesHash!==digest(r.messages))fail('COMPARISON_REQUEST_INVALID');
 validateModelChoice(r.modelChoice);modelCapacity(r.modelChoice,r.messages);
 if(!same(r.parameters,r.modelChoice.wireParameters))fail('COMPARISON_REQUEST_INVALID');return r;
}
// Transport adapters must validate their own provider receipt before producing
// this envelope. Receipt data remains durable and is never interpreted as code.
export function resultEnvelope(request,{status,raw='',receipt}){
 if(!['completed','failed','cancelled','not_dispatched','unknown'].includes(status)||typeof raw!=='string'||!raw.isWellFormed()||Buffer.byteLength(raw)>COMPARISON_LIMITS.replyBytes||
  !receipt||receipt.requestId!==request.id||receipt.modelId!==request.modelChoice.entry.modelId||!same(receipt.parameters,request.parameters)||receipt.messagesHash!==request.messagesHash||typeof receipt.dispatched!=='boolean'||receipt.retries!==0||receipt.requestHash!==digest(request)||
  status==='completed'&&(!receipt.dispatched||!raw.trim())||status==='not_dispatched'&&receipt.dispatched)fail('COMPARISON_RECEIPT_INVALID');
 const v={requestId:request.id,requestHash:digest(request),status,raw,rawHash:sha256(raw),receipt:clone(receipt)};return {...v,hash:digest(v)};
}
export function validateResult(result,request){if(!same(result,resultEnvelope(request,result)))fail('COMPARISON_RECEIPT_INVALID');return result;}

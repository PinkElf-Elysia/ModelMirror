import {keys,isId,isHash,fail} from '../../authoring/contracts.mjs';
import {digest,idFor,text,player,reference,active,memberOf,projectMember} from '../conversation/contracts.mjs';
export {digest,idFor,active,memberOf};
export const FORMAT='modelmirror.rpg.character-group/1';
export const MESSAGE_CONTRACT='modelmirror.rpg.character-group-messages/1';
export const check=(ok,code)=>{if(!ok)fail('GROUP_'+code);};
export const integer=n=>Number.isSafeInteger(n)&&n>=0;
export const pending=s=>s.requests.find(r=>['prepared','dispatching','unknown'].includes(r.status));
export function limits(v){keys(v,['calls','microUsd']);check(integer(v.calls)&&integer(v.microUsd),'BUDGET_INVALID');}
export function model(v){
 keys(v,['id','label','choice','contextTokens','outputTokens','price']);text(v.id,128);text(v.label,200);
 check(integer(v.contextTokens)&&integer(v.outputTokens)&&v.outputTokens>0&&v.outputTokens<v.contextTokens,'MODEL_INVALID');
 if(v.price!==null){keys(v.price,['revision','inputMicroUsdPerMillion','outputMicroUsdPerMillion','overheadTokens']);text(v.price.revision,200);check([v.price.inputMicroUsdPerMillion,v.price.outputMicroUsdPerMillion,v.price.overheadTokens].every(integer),'PRICE_INVALID');}return v;
}
export function reservation(messages,m){
 model(m);check(m.price!==null,'PRICE_UNKNOWN');
 // Host-certified byte upper bound, including provider framing/hidden costs.
 const inputTokens=messages.reduce((n,x)=>n+Buffer.byteLength(x.content,'utf8')+32,0)+m.price.overheadTokens;
 check(inputTokens+m.outputTokens<=m.contextTokens,'CONTEXT_CAPACITY');
 const microUsd=Math.ceil((inputTokens*m.price.inputMicroUsdPerMillion+m.outputTokens*m.price.outputMicroUsdPerMillion)/1e6);
 check(integer(microUsd),'PRICE_INVALID');return {calls:1,microUsd,inputTokens};
}
function newMember(s,p,operationId,index){
 keys(p,['reference','configurationHash','name','model']);reference(p.reference);check(isHash(p.configurationHash),'BINDING_INVALID');text(p.name,120);model(p.model);
 return {id:idFor('member',s.id,operationId,index),kind:'character',name:p.name,identity:null,binding:{reference:p.reference,configurationHash:p.configurationHash,worldInstanceRef:null},model:p.model,intervals:[{from:s.messages.length+1,until:null}]};
}
function message(s,operationId,speakerId,content,source,addressedTo=[]){
 text(content);check(active(memberOf(s,speakerId)),'MEMBER_INACTIVE');
 s.messages.push({id:idFor('message',s.id,operationId),sequence:s.messages.length+1,speakerId,content,source,visibility:{kind:'public'},addressedTo});
}
export function replay(record){
 keys(record,['format','id','runtimeHash','events']);check(record.format===FORMAT&&isHash(record.runtimeHash),'FORMAT_INVALID');
 check(/^group-[a-f0-9]{64}$/.test(record.id)&&Array.isArray(record.events)&&record.events.length>0&&record.events.length<=3000,'RECORD_INVALID');
 let s=null;const seen=new Set();
 for(const [i,e] of record.events.entries()){
  keys(e,['operationId','expectedRevision','action','payload','intentHash']);check(isId(e.operationId)&&isHash(e.intentHash)&&!seen.has(e.operationId),'OPERATION_COLLISION');seen.add(e.operationId);check(e.expectedRevision===(i?i-1:null),'REVISION_CONFLICT');const p=e.payload;
  if(!i){
   check(e.action==='create'&&record.id===idFor('group',e.operationId),'CREATE_REQUIRED');keys(p,['player','scene','members','opening','budget']);player(p.player);text(p.scene,20000);limits(p.budget);check(Array.isArray(p.members)&&p.members.length>=2&&p.members.length<=3,'MEMBER_LIMIT');
   s={format:FORMAT,id:record.id,revision:0,scene:p.scene,members:[],messages:[],privateRecords:[],requests:[],budget:{...p.budget,usedCalls:0,usedMicroUsd:0},worldInstanceRef:null};
   s.members.push({id:idFor('member',s.id,e.operationId,'player'),kind:'player',name:p.player.name,identity:p.player,binding:null,intervals:[{from:1,until:null}]});
   p.members.forEach((m,n)=>s.members.push(newMember(s,m,e.operationId,n)));
   if(p.opening!==null){keys(p.opening,['memberIndex','kind','index','content']);check(integer(p.opening.memberIndex)&&p.opening.memberIndex<p.members.length&&['ordinary','group'].includes(p.opening.kind)&&integer(p.opening.index),'OPENING_INVALID');message(s,e.operationId,s.members[p.opening.memberIndex+1].id,p.opening.content,{kind:'opening',selection:{kind:p.opening.kind,index:p.opening.index}});}
  }else if(['join','leave','rejoin','model','private'].includes(e.action)){
   check(!pending(s),'REQUEST_PENDING');
   if(e.action==='join'){check(s.members.filter(m=>m.kind==='character').length<3,'MEMBER_LIMIT');s.members.push(newMember(s,p,e.operationId,0));}
   else if(e.action==='private'){keys(p,['ownerId','content']);check(active(memberOf(s,p.ownerId)),'MEMBER_INACTIVE');text(p.content);s.privateRecords.push({id:idFor('private',s.id,e.operationId),...p,source:{kind:'manual',reference:null}});}
   else {keys(p,e.action==='model'?['memberId','model']:['memberId']);const m=memberOf(s,p.memberId);check(m.kind==='character','PLAYER_FIXED');if(e.action==='model')m.model=model(p.model);else {check(active(m)===(e.action==='leave'),'PRESENCE_CONFLICT');if(e.action==='leave')m.intervals.at(-1).until=s.messages.length+1;else m.intervals.push({from:s.messages.length+1,until:null});}}
  }else if(e.action==='prepare'){
   check(!pending(s),'REQUEST_PENDING');keys(p,['targetId','input','request']);const m=memberOf(s,p.targetId);check(m.kind==='character'&&active(m),'MEMBER_INACTIVE');check(p.input===null||typeof p.input==='string','INPUT_INVALID');
   if(p.input!==null)message(s,e.operationId,s.members[0].id,p.input,{kind:'player-input',reference:null},[m.id]);
   const r=structuredClone(p.request);keys(r,['id','operationId','groupId','targetId','binding','model','messages','messagesHash','trace','reservation','sourceHash']);
   check(r.id===idFor('request',s.id,e.operationId)&&r.operationId===e.operationId&&r.groupId===s.id&&r.targetId===m.id&&digest(r.binding)===digest(m.binding)&&digest(r.model)===digest(m.model),'REQUEST_BINDING');
   check(r.messagesHash===digest(r.messages)&&r.sourceHash===digest({revision:i-1,scene:s.scene,messages:s.messages.map(x=>x.id),target:m.id}),'REQUEST_HASH');
   check(digest(r.reservation)===digest(reservation(r.messages,r.model)),'BUDGET_INVALID');
   check(s.budget.usedCalls+1<=s.budget.calls&&s.budget.usedMicroUsd+r.reservation.microUsd<=s.budget.microUsd,'BUDGET_EXHAUSTED');
   s.budget.usedCalls++;s.budget.usedMicroUsd+=r.reservation.microUsd;s.requests.push({...r,status:'prepared',evidence:null});
  }else if(['dispatch','unknown','settle','close-prepared'].includes(e.action)){
   keys(p,e.action==='settle'?['requestId','evidence']:['requestId']);const r=s.requests.find(r=>r.id===p.requestId);check(r&&pending(s)?.id===r.id,'REQUEST_STATE');
   if(e.action==='dispatch'){check(r.status==='prepared','REQUEST_STATE');r.status='dispatching';}
   if(e.action==='unknown'){check(r.status!=='prepared','REQUEST_STATE');r.status='unknown';}
   if(e.action==='close-prepared'){check(r.status==='prepared','REQUEST_STATE');r.status='failed';s.budget.usedCalls--;s.budget.usedMicroUsd-=r.reservation.microUsd;}
   if(e.action==='settle'){
    check(r.status!=='prepared','REQUEST_STATE');validateReceipt(p.evidence,r);r.evidence=structuredClone(p.evidence);r.status=p.evidence.status;
    if(r.status==='completed')message(s,r.id,r.targetId,p.evidence.content,{kind:'model-result',reference:r.id,modelId:p.evidence.actualModel});
    if(r.status!=='unknown'&&p.evidence.costMicroUsd!==null){check(p.evidence.costMicroUsd<=r.reservation.microUsd,'COST_BOUND_EXCEEDED');s.budget.usedMicroUsd-=r.reservation.microUsd-p.evidence.costMicroUsd;}
    if(r.status==='failed'&&!p.evidence.dispatched){s.budget.usedCalls--;s.budget.usedMicroUsd-=p.evidence.costMicroUsd===null?r.reservation.microUsd:p.evidence.costMicroUsd;}
   }
  }else check(false,'ACTION_INVALID');s.revision=i;
 }return s;
}
export function requestBody(r){const {status,evidence,...body}=r;return body;}
export function validateReceipt(e,r){
 keys(e,['format','requestId','requestHash','targetId','modelHash','status','dispatched','actualModel','content','partial','costMicroUsd','evidenceHash']);
 check(e.format==='modelmirror.rpg.group-receipt/1'&&e.requestId===r.id&&e.requestHash===digest(requestBody(r))&&e.targetId===r.targetId&&e.modelHash===digest(r.model),'RECEIPT_MISMATCH');
 check(['completed','failed','unknown'].includes(e.status)&&typeof e.dispatched==='boolean'&&typeof e.content==='string'&&typeof e.partial==='string'&&isHash(e.evidenceHash),'RECEIPT_INVALID');
 check(e.costMicroUsd===null||integer(e.costMicroUsd),'RECEIPT_INVALID');check(e.actualModel===null||typeof e.actualModel==='string','RECEIPT_INVALID');
 if(e.status==='completed'){text(e.content);check(e.dispatched&&e.actualModel!==null,'RECEIPT_INVALID');}else check(e.content==='','RECEIPT_INVALID');
 if(!e.dispatched)check(e.status==='failed'&&(e.costMicroUsd===0||e.costMicroUsd===null),'RECEIPT_INVALID');return e;
}
export function memberView(s,id){const v=projectMember(s,id);delete v.member.model;return v;}
export function browserView(s){const v=memberView(s,s.members[0].id);return {id:s.id,revision:s.revision,scene:s.scene,messages:v.messages,members:s.members.map(m=>({id:m.id,name:m.name,kind:m.kind,active:active(m),modelId:m.model?.id??null})),budget:s.budget,requests:s.requests.map(r=>({id:r.id,targetId:r.targetId,status:r.status,modelId:r.model.id,actualModel:r.evidence?.actualModel??null,partial:r.evidence?.partial??'',canRecover:['dispatching','unknown'].includes(r.status),canClose:r.status==='prepared'}))};}

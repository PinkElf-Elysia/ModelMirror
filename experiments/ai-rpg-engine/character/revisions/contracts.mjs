// Append-only revision contract. No provider or filesystem access.
import {canonical,sha256,keys,isId,fail} from '../../authoring/contracts.mjs';
import {active,participated} from '../conversation/contracts.mjs';
export const FORMAT='modelmirror.rpg.character-revisions/2';
export const digest=v=>sha256(canonical(v));
export const copy=structuredClone;
export const id=(kind,...v)=>kind+'-'+digest(v);
export const check=(ok,c)=>{if(!ok)fail('REVISION_'+c);};
export const limits=Object.freeze({events:2000,branches:64,depth:32,candidates:16,text:100000});
export const pending=f=>Object.values(f.requests).find(r=>['prepared','dispatching','unknown'].includes(r.status));
export function text(v,max=limits.text){check(typeof v==='string'&&v.isWellFormed()&&v.trim().length>0&&v.length<=max,'TEXT_INVALID');}
export function branch(f,b){const v=f.branches[b];check(v,'BRANCH_NOT_FOUND');return v;}
export function at(b,messageId){const n=b.messages.findIndex(m=>m.id===messageId);check(n>=0,'MESSAGE_NOT_FOUND');return n;}
export const prefixHash=messages=>digest(messages.map(m=>[m.id,m.revisionId]));
export const boundary=b=>b.current??b.messages.at(-1)?.boundary??b.initial;
export function visible(b,memberId){const state=boundary(b),m=state.members.find(m=>m.id===memberId);check(m&&active(m),'MEMBER_INACTIVE');return b.messages.filter((x,i)=>participated(m,i+1)&&(x.visibility.kind==='public'||x.visibility.memberIds.includes(memberId)));}
export function derivedView(b,memberId){
 const shown=visible(b,memberId),revisions=new Set(shown.map(m=>m.revisionId));
 return b.derived.filter(d=>d.ownerId===memberId&&(d.availableAfter??0)<=b.messages.length&&(d.availableRevision??0)<=(boundary(b).sourceRevision??0)).map(d=>{
  const valid=d.dependencies?.length&&d.dependencies.every(r=>revisions.has(r))&&(!d.prefixHash||d.prefixHash===prefixHash(shown));
  return {...copy(d),status:valid?'valid':d.dependencies?.length?'invalid':'needs-review'};
 });
}
function validateSource(s){
 keys(s,['kind','id','revision','runtimeHash','pending','messages','initial','current','derived','cards','evidence','budget','sourceHash']);
 check(['single','group'].includes(s.kind)&&isId(s.id)&&Number.isSafeInteger(s.revision)&&s.revision>=0&&!s.pending,'SOURCE_INVALID');
 const {sourceHash,...body}=s;check(digest(body)===sourceHash,'SOURCE_HASH');
 check(Array.isArray(s.messages)&&s.messages.length<=2001&&s.initial?.members?.length>=2,'SOURCE_INVALID');
 for(const m of s.messages){text(m.content);check(isId(m.id)&&isId(m.revisionId)&&m.boundary?.members?.some(x=>x.id===m.speakerId),'MESSAGE_INVALID');}
 check(new Set(s.messages.map(m=>m.id)).size===s.messages.length,'MESSAGE_DUPLICATE');
}
function child(f,b,messages,operationId,label){
 check(Object.keys(f.branches).length<limits.branches&&b.depth<limits.depth,'BRANCH_LIMIT');
 const childId=id('branch',f.id,operationId),last=messages.at(-1);
 const v={id:childId,parentId:b.id,depth:b.depth+1,title:label,initial:copy(b.initial),current:copy(last?.boundary??b.initial),messages:copy(messages),derived:copy(b.derived),fork:{messageId:last?.id??null,revisionId:last?.revisionId??null,sourcePrefix:prefixHash(messages)}};
 f.branches[childId]=v;return v;
}
export function apply(f,e){
 keys(e,['action','operationId','expectedRevision','payload']);check(isId(e.operationId),'OPERATION_INVALID');check(e.expectedRevision===(f?.revision??null),'REVISION_CONFLICT');const p=e.payload;
 if(!f){
  check(e.action==='create','CREATE_REQUIRED');keys(p,['source']);validateSource(p.source);const familyId=id('family',p.source.kind,p.source.id),rootId=id('branch',familyId,'root');
  return {format:FORMAT,id:familyId,revision:0,source:copy(p.source),rootId,branches:{[rootId]:{id:rootId,parentId:null,depth:0,title:'原始路径',initial:copy(p.source.initial),current:copy(p.source.current),messages:copy(p.source.messages),derived:copy(p.source.derived),fork:null}},candidates:{},requests:{},audit:[copy(e)],usedCalls:0,usedMicroUsd:0};
 }
 f=copy(f);check(!pending(f)||['dispatch','unknown','settle','close-prepared'].includes(e.action),'REQUEST_PENDING');
 if(e.action==='fork'||e.action==='revise'){
  keys(p,e.action==='fork'?['branchId','messageId','title']:['branchId','messageId','content','title']);text(p.title,120);
  const b=branch(f,p.branchId),n=at(b,p.messageId),messages=copy(b.messages.slice(0,n+1));
  if(e.action==='revise'){text(p.content);const m=messages[n];m.content=p.content;m.revisionId=id('revision',f.id,e.operationId);m.provenance={kind:'manual-edit',previousRevision:b.messages[n].revisionId,original:b.messages[n].provenance};m.generation=null;}
  child(f,b,messages,e.operationId,p.title);
 }else if(e.action==='restore'){
  keys(p,['branchId','messageId']);const b=branch(f,p.branchId),n=at(b,p.messageId),original=f.source.messages.find(m=>m.id===p.messageId);check(original,'ORIGINAL_NOT_FOUND');
  const originalIndex=f.source.messages.indexOf(original);check(prefixHash(b.messages.slice(0,n))===prefixHash(f.source.messages.slice(0,originalIndex)),'PREFIX_CHANGED');
  const next=child(f,b,[...b.messages.slice(0,n),copy(original)],e.operationId,'原回复续写');void next;
 }else if(e.action==='review'){
  keys(p,['branchId','recordId','content']);text(p.content);const b=branch(f,p.branchId),d=b.derived.find(d=>d.id===p.recordId);check(d,'DERIVED_NOT_FOUND');
  const history=visible(b,d.ownerId);check(history.length,'DERIVED_NO_HISTORY');b.derived.push({...copy(d),id:id('derived',f.id,e.operationId),content:p.content,dependencies:history.map(m=>m.revisionId),prefixHash:prefixHash(history),availableRevision:boundary(b).sourceRevision??0,reviewOf:d.id});
 }else if(e.action==='prepare'){
  keys(p,['branchId','request']);const b=branch(f,p.branchId),r=copy(p.request);
  keys(r,['sourceKind','sourceId',...(r.overrideModel?['overrideModel']:[]),'id','branchId','targetId','slotId','prefix','prefixHash','context','contextHash','model','reservation','kind','originalMessageId','inputMessage','boundary','hash']);
  const {hash,...body}=r;check(r.sourceKind===f.source.kind&&r.sourceId===f.source.id&&hash===digest(body)&&r.id===id('request',f.id,e.operationId)&&r.branchId===b.id,'REQUEST_HASH');
  check(r.contextHash===digest(r.context)&&r.prefixHash===prefixHash(r.prefix),'CONTEXT_HASH');
  check(['send','regenerate'].includes(r.kind)&&r.boundary.members.some(m=>m.id===r.targetId&&m.kind==='character'&&active(m)),'TARGET_INVALID');
  if(r.kind==='regenerate'){const n=at(b,r.originalMessageId),m=b.messages[n];check(m.generation&&m.speakerId===r.targetId&&prefixHash(b.messages.slice(0,n))===r.prefixHash,'REGENERATION_SOURCE');}
  else check(prefixHash([...b.messages,...(r.inputMessage?[r.inputMessage]:[])])===r.prefixHash,'PREFIX_CHANGED');
  check(Object.values(f.candidates).filter(c=>c.slotId===r.slotId).length<limits.candidates,'CANDIDATE_LIMIT');
  check(Number.isSafeInteger(r.reservation.microUsd)&&r.reservation.microUsd>=0&&r.reservation.calls===1,'BUDGET_INVALID');
  check(f.usedCalls+1<=f.source.budget.calls&&f.usedMicroUsd+r.reservation.microUsd<=f.source.budget.microUsd,'BUDGET_EXHAUSTED');
  // New player messages are durable before dispatch; regeneration never appends one.
  if(r.inputMessage)b.messages.push(copy(r.inputMessage));
  f.requests[r.id]={...r,status:'prepared',receipt:null};f.usedCalls++;f.usedMicroUsd+=r.reservation.microUsd;
 }else if(['dispatch','unknown','close-prepared','settle'].includes(e.action)){
  keys(p,e.action==='settle'?['requestId','receipt']:['requestId']);const r=f.requests[p.requestId];check(r&&pending(f)?.id===r.id,'REQUEST_STATE');
  if(e.action==='dispatch'){check(r.status==='prepared','REQUEST_STATE');r.status='dispatching';}
  if(e.action==='unknown'){check(r.status!=='prepared','REQUEST_STATE');r.status='unknown';}
  if(e.action==='close-prepared'){check(r.status==='prepared','REQUEST_STATE');r.status='failed';f.usedCalls--;f.usedMicroUsd-=r.reservation.microUsd;}
  if(e.action==='settle'){
   check(r.status!=='prepared','REQUEST_STATE');const c=p.receipt;keys(c,['requestId','requestHash','modelId','targetId','status','text','raw','dispatched','costMicroUsd','hash']);
   const {hash,...body}=c;check(digest(body)===hash&&c.requestId===r.id&&c.requestHash===r.hash&&c.modelId===r.model.id&&c.targetId===r.targetId,'RECEIPT_MISMATCH');
   check(['completed','failed'].includes(c.status)&&typeof c.dispatched==='boolean'&&typeof c.text==='string'&&c.raw,'RECEIPT_INVALID');
   check(c.costMicroUsd===null||Number.isSafeInteger(c.costMicroUsd)&&c.costMicroUsd>=0&&c.costMicroUsd<=r.reservation.microUsd,'COST_BOUND');
   if(c.status==='completed'){text(c.text);check(c.dispatched,'RECEIPT_INVALID');}
   if(!c.dispatched)check(c.status==='failed'&&c.costMicroUsd===0,'RECEIPT_INVALID');
   r.receipt=copy(c);r.status=c.status;
   if(c.costMicroUsd!==null)f.usedMicroUsd-=r.reservation.microUsd-c.costMicroUsd;
   if(!c.dispatched)f.usedCalls--;
   if(c.status==='completed'){const candidateId=id('candidate',r.id);f.candidates[candidateId]={id:candidateId,slotId:r.slotId,requestId:r.id,branchId:r.branchId,content:c.text,targetId:r.targetId,prefixHash:r.prefixHash,modelId:c.modelId};}
  }
 }else if(e.action==='select'){
  keys(p,['branchId','candidateId']);const b=branch(f,p.branchId),c=f.candidates[p.candidateId];check(c&&c.branchId===b.id,'CANDIDATE_SCOPE');const r=f.requests[c.requestId];
  const n=r.originalMessageId?at(b,r.originalMessageId):r.prefix.length;
  check(prefixHash(b.messages.slice(0,n))===c.prefixHash,'PREFIX_CHANGED');
  const existing=b.messages[n];check(!existing||existing.slotId===r.slotId||existing.id===r.originalMessageId,'SLOT_CHANGED');
  const target=(b.messages.length>n+1||digest({members:boundary(b).members,scene:boundary(b).scene})!==digest({members:r.boundary.members,scene:r.boundary.scene}))?child(f,b,b.messages.slice(0,n),e.operationId,'另一种回应'):b;
  const m={id:r.originalMessageId??id('message',r.slotId),revisionId:id('revision',c.id),speakerId:r.targetId,content:c.content,visibility:copy(existing?.visibility??{kind:'public'}),addressedTo:copy(existing?.addressedTo??[]),provenance:{kind:'model-result',requestId:r.id,candidateId:c.id},generation:{requestId:r.id,context:copy(r.context),model:copy(r.model)},slotId:r.slotId,boundary:copy(r.boundary)};
  target.messages=[...target.messages.slice(0,n),m];target.current=copy(r.boundary);
 }else check(false,'ACTION_INVALID');
 f.revision++;f.audit.push(copy(e));check(f.audit.length<=limits.events,'EVENT_LIMIT');return f;
}
export function replay(events){check(Array.isArray(events)&&events.length>0&&events.length<=limits.events,'EVENT_INVALID');let f=null;const seen=new Set();for(const e of events){check(!seen.has(e.operationId),'OPERATION_COLLISION');seen.add(e.operationId);f=apply(f,e);}return f;}
export function safeView(f,branchId=f.rootId){
 const b=branch(f,branchId);return {replySlots:replySlots(f,b),id:f.id,revision:f.revision,rootId:f.rootId,source:{kind:f.source.kind,id:f.source.id,revision:f.source.revision},branchId,branches:Object.values(f.branches).map(x=>({id:x.id,parentId:x.parentId,title:x.title})),messages:b.messages.map(m=>({id:m.id,revisionId:m.revisionId,speakerId:m.speakerId,content:m.content,slotId:m.slotId,modelId:m.generation?.model?.id??null,source:m.provenance.kind,canRegenerate:!!m.generation,canRestore:!!f.source.messages.find(o=>o.id===m.id&&o.revisionId!==m.revisionId)})),members:boundary(b).members.map(m=>({id:m.id,name:m.name,kind:m.kind,active:active(m)})),candidates:Object.values(f.candidates).filter(c=>c.branchId===branchId||b.messages.some(m=>m.provenance.candidateId===c.id)).map(c=>({...c,selected:b.messages.some(m=>m.provenance.candidateId===c.id),canSelect:c.branchId===branchId})),requests:Object.values(f.requests).map(r=>({id:r.id,status:r.status,modelId:r.model.id,partial:r.status==='failed'?r.receipt?.text??'':'',canClose:r.status==='prepared',canRecover:['dispatching','unknown'].includes(r.status)})),budget:{...f.source.budget,usedCalls:f.usedCalls,usedMicroUsd:f.usedMicroUsd},pending:!!pending(f),derived:boundary(b).members.filter(m=>active(m)).flatMap(m=>derivedView(b,m.id).map(d=>({id:d.id,ownerId:d.ownerId,content:d.content,status:d.status}))),player:copy(boundary(b).members[0].identity)};
}

export function replySlots(f,b){
 const ids=new Set([...b.messages.filter(m=>m.provenance.kind==='model-result'||f.source.messages.some(o=>o.id===m.id&&o.provenance.kind==='model-result')).map(m=>m.slotId),...Object.values(f.candidates).filter(c=>c.branchId===b.id).map(c=>c.slotId)]);
 return [...ids].map(slotId=>{const index=b.messages.findIndex(m=>m.slotId===slotId),current=b.messages[index],original=f.source.messages.find(m=>m.slotId===slotId),candidates=Object.values(f.candidates).filter(c=>c.slotId===slotId&&(c.branchId===b.id||current?.provenance.candidateId===c.id));
 return {slotId,messageId:current?.id??null,position:index<0?b.messages.length+1:index+1,targetId:current?.speakerId??candidates[0]?.targetId,items:[...(original?[{id:'original-'+original.id,kind:'original',content:original.content,modelId:original.generation?.model?.id??null,selected:current?.revisionId===original.revisionId,canSelect:!!current&&index>=0&&prefixHash(b.messages.slice(0,index))===prefixHash(f.source.messages.slice(0,f.source.messages.indexOf(original))),messageId:original.id,requestId:original.generation?.requestId??null}]:[]),...candidates.map(c=>{const r=f.requests[c.requestId],n=r.originalMessageId?b.messages.findIndex(m=>m.id===r.originalMessageId):r.prefix.length;return {...c,kind:'generated',selected:current?.provenance.candidateId===c.id,canSelect:c.branchId===b.id&&n>=0&&prefixHash(b.messages.slice(0,n))===c.prefixHash,createsBranch:b.messages.length>n+1};})]};});
}

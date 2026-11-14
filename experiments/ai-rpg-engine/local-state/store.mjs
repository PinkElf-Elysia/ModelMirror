import {randomUUID} from 'node:crypto';
import {SessionRecords} from '../authoring/session-storage.mjs';
import {clone,hash,need,id,text,keys,validateState} from './definition.mjs';
import {candidate} from './rules.mjs';
const FORMAT='modelmirror.rpg.local-state-session/1';
function validateRecord(r){
 need(r?.format===FORMAT&&id(r.id)&&Number.isSafeInteger(r.revision)&&Array.isArray(r.events)&&Array.isArray(r.turns),'STATE_RECORD');
 validateState(r.state);validateState(r.initial);
 need(!r.pending||r.operations[r.pending],'STATE_PENDING_REFERENCE');return r;
}
export class LocalStateStore{
 static async open({directory,binding,fault,recoverDeadOwner=false}){
  return new LocalStateStore(await SessionRecords.open(directory,{scope:'rpg-local-state',fault,recoverDeadOwner}),binding);
 }
 constructor(records,binding){this.records=records;this.binding=clone(binding);}
 close(){return this.records.close();}
 reconcile(id){return this.records.reconcile(id);}
 async envelope(key){need(id(key),'STATE_ID');const row=await this.records.get(key,validateRecord);need(row,'STATE_NOT_FOUND');need(hash(row.data.binding)===hash(this.binding),'STATE_BINDING_CHANGED');return row;}
 async read(key){return (await this.envelope(key)).data;}
 async create({operationId,initial,confirmed,source={kind:'example',id:'adult-fixture',history:[]},config={profiles:[],mods:[]}}){
  need(id(operationId)&&confirmed===true,'STATE_INITIAL_CONFIRMATION_REQUIRED');validateState(initial);
  need(source&&!source.pending&&id(source.id)&&['example','confirmed-import','new'].includes(source.kind)&&Array.isArray(source.history)&&source.history.length<=500,'STATE_SOURCE');
  need(source.history.every(t=>text(t.input,20000)&&text(t.body,200000)),'STATE_SOURCE_HISTORY');
  const key='local-'+hash([source.id,operationId]).slice(0,40),fingerprint=hash({initial,source,config,binding:this.binding});
  const old=await this.records.get(key,validateRecord);if(old){need(old.data.creationHash===fingerprint,'STATE_OPERATION_COLLISION');return old.data;}
  const r={format:FORMAT,id:key,revision:0,stateRevision:0,binding:clone(this.binding),creationHash:fingerprint,source:clone(source),config:clone(config),initial:clone(initial),state:clone(initial),enabled:true,pending:null,turns:[],events:[],operations:{},createdAt:new Date().toISOString()};
  await this.records.put(key,null,r);return r;
 }
 async update(key,fn){
  const row=await this.envelope(key),r=clone(row.data);const result=fn(r);
  r.revision++;validateRecord(r);await this.records.put(key,row.hash,r);return {record:r,result};
 }
 async startTurn(key,request,context){
  keys(request,['operationId','revision','input']);need(id(request.operationId)&&text(request.input,20000)&&request.input.trim(),'STATE_INPUT');
  const prior=await this.read(key),fingerprint=hash(request),old=prior.operations[request.operationId];
  if(old){need(old.requestHash===fingerprint,'STATE_OPERATION_COLLISION');return {record:prior,dispatch:false};}
  const {record}=await this.update(key,r=>{
   need(r.enabled,'STATE_DISABLED');need(r.revision===request.revision,'STATE_STALE');need(!r.pending,'STATE_TURN_PENDING');
   r.operations[request.operationId]={kind:'turn',requestHash:fingerprint,input:request.input,status:'pending',stateRevision:r.stateRevision,context:clone(context),receipt:null};
   r.pending=request.operationId;
  });return {record,dispatch:true};
 }
 async receive(key,operationId,receipt){
  return (await this.update(key,r=>{
   const op=r.operations[operationId];need(r.pending===operationId&&op?.kind==='turn','STATE_TURN_MISMATCH');
   if(op.receipt && op.receipt.status!=='unknown'){need(hash(op.receipt)===hash(receipt),'STATE_RECEIPT_COLLISION');return;}
   need(receipt&&['completed','failed','unknown'].includes(receipt.status)&&text(receipt.text??'',200000),'STATE_RECEIPT');
   op.receipt=clone(receipt);op.status=receipt.status==='unknown'?'unknown':receipt.status==='failed'?'failed':'received';
  })).record;
 }
 async markFailure(key,operationId,{definite=false,message='供应商结果未知'}={}){
  return (await this.update(key,r=>{need(r.pending===operationId,'STATE_TURN_MISMATCH');const op=r.operations[operationId];op.status=definite?'failed':'unknown';op.error=message;})).record;
 }
 async finish(key,operationId){
  const old=await this.read(key);if(old.operations[operationId]?.status==='completed')return old;
  return (await this.update(key,r=>{
   const op=r.operations[operationId];need(r.pending===operationId&&op?.receipt,'STATE_RESULT_UNKNOWN');
   need(op.receipt.status!=='unknown','STATE_RESULT_UNKNOWN');need(op.receipt.status==='completed','STATE_GENERATION_FAILED');
   need(op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
   let proposal;try{proposal=candidate(r.state,op.receipt,op.context.statePolicy);}catch(e){op.status='rejected';op.error=e.code||'STATE_INVALID';return;}
   const before=clone(r.state);r.state=proposal.state;r.stateRevision++;
   const event={id:operationId,kind:'turn',stateRevision:r.stateRevision,before,after:clone(r.state),changes:proposal.changes};
   r.events.push(event);r.turns.push({id:operationId,input:op.input,body:proposal.body,changes:proposal.changes,stateRevision:r.stateRevision});
   op.status='completed';op.eventId=operationId;r.pending=null;
  })).record;
 }
 async action(key,request,kind,mutate){
  need(id(request.operationId)&&Number.isSafeInteger(request.revision),'STATE_INPUT');const fingerprint=hash({kind,request}),old=await this.read(key),seen=old.operations[request.operationId];
  if(seen){need(seen.requestHash===fingerprint,'STATE_OPERATION_COLLISION');return old;}
  return (await this.update(key,r=>{need(r.revision===request.revision,'STATE_STALE');mutate(r);r.operations[request.operationId]={kind,requestHash:fingerprint,status:'completed'};})).record;
 }
 override(key,request){
  keys(request,['operationId','revision','state','reason']);validateState(request.state);need(text(request.reason,500)&&request.reason.trim(),'STATE_REASON_REQUIRED');
  return this.action(key,request,'override',r=>{
   need(!r.pending,'STATE_TURN_PENDING');const before=clone(r.state);r.state=clone(request.state);r.stateRevision++;
   r.events.push({id:request.operationId,kind:'override',stateRevision:r.stateRevision,before,after:clone(r.state),reason:request.reason,changes:[{path:'explicit-override',before:null,after:null,reason:request.reason,authority:'user-override'}]});
  });
 }
 enabled(key,request){
  keys(request,['operationId','revision','enabled']);need(typeof request.enabled==='boolean');
  return this.action(key,request,'enabled',r=>{need(!r.pending,'STATE_TURN_PENDING');r.enabled=request.enabled;});
 }
 discard(key,request){
  keys(request,['operationId','revision','pendingId','reason']);need(text(request.reason,500)&&request.reason.trim(),'STATE_REASON_REQUIRED');
  return this.action(key,request,'discard',r=>{
   need(r.pending===request.pendingId&&['failed','rejected'].includes(r.operations[r.pending]?.status),'STATE_UNKNOWN_CANNOT_DISCARD');
   r.operations[r.pending].status='discarded';r.operations[r.pending].discardReason=request.reason;r.pending=null;
  });
 }
 async previewCandidate(key,receipt){const r=await this.read(key);return {...candidate(r.state,receipt),stateRevision:r.stateRevision};}
 async fork(key,{operationId,revision,eventId}){
  need(id(operationId),'STATE_ID');const source=await this.read(key);
  const fingerprint=hash({key,operationId,eventId}),childId='local-'+fingerprint.slice(0,40),old=await this.records.get(childId,validateRecord);
  if(old){need(old.data.creationHash===fingerprint,'STATE_OPERATION_COLLISION');return old.data;}
  need(source.revision===revision&&!source.pending,'STATE_CHECKPOINT_PENDING');
  const index=eventId===null?-1:source.events.findIndex(e=>e.id===eventId);need(eventId===null||index>=0,'STATE_CHECKPOINT_NOT_FOUND');
  const e=source.events[index],r=clone(source);r.id=childId;r.revision=0;r.creationHash=fingerprint;
  r.source={kind:'checkpoint',id:key,eventId,history:clone(source.source.history||[])};
  r.state=clone(e?.after||source.initial);r.stateRevision=e?.stateRevision||0;r.events=source.events.slice(0,index+1);
  r.turns=source.turns.filter(t=>t.stateRevision<=r.stateRevision);r.operations={};r.pending=null;
  await this.records.put(childId,null,r);return r;
 }
}
export const operationId=()=> 'op-'+randomUUID();

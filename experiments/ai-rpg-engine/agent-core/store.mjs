import {SessionRecords} from '../authoring/session-storage.mjs';
import {clone,hash,need,id,text,keys,bounded,validateAdapter} from './contracts.mjs';
export const FORMAT='modelmirror.rpg.agent-core-session/3';
export class CoreStore{
 static async open({directory,adapter,fault,recoverDeadOwner=false}){
  validateAdapter(adapter);return new CoreStore(await SessionRecords.open(directory,{scope:'rpg-agent-core-r3',fault,recoverDeadOwner}),adapter);
 }
 constructor(records,adapter){this.records=records;this.adapter=adapter;this.binding=clone(adapter.binding);}
 close(){return this.records.close();}
 reconcile(key){return this.records.reconcile(key);}
 validate(r){
  bounded(r,{bytes:16*1024*1024,depth:40});need(r?.format===FORMAT&&id(r.id),'CORE_RECORD');
  need(Number.isSafeInteger(r.revision)&&r.revision>=0&&Number.isSafeInteger(r.stateRevision)&&r.stateRevision>=0,'CORE_REVISION');
  need(Array.isArray(r.events)&&Array.isArray(r.turns)&&r.operations&&typeof r.enabled==='boolean','CORE_RECORD');
  need(!r.pending||r.operations[r.pending]?.kind==='turn','CORE_PENDING');
  need(hash(r.binding)===hash(this.binding),'STATE_BINDING_CHANGED');
  bounded(r.state);bounded(r.initial);this.adapter.validateState(r.state);this.adapter.validateState(r.initial);return r;
 }
 async envelope(key){need(id(key),'STATE_ID');const row=await this.records.get(key,r=>this.validate(r));need(row,'STATE_NOT_FOUND');return row;}
 async read(key){return (await this.envelope(key)).data;}
 async create({operationId,initial,confirmed,source={kind:'example',id:'adult-fixture',history:[]},config={profiles:[],mods:[]}}){
  need(id(operationId)&&confirmed===true,'STATE_INITIAL_CONFIRMATION_REQUIRED');bounded(initial);this.adapter.validateState(initial);await this.adapter.validateConfig(config);
  keys(source,['kind','id','history']);need(['example','new'].includes(source.kind)&&id(source.id)&&Array.isArray(source.history)&&source.history.length===0,'CORE_NEW_SOURCE_ONLY');
  const key='core-'+hash([this.adapter.id,source.id,operationId]).slice(0,40),creationHash=hash({initial,source,config,binding:this.binding});
  const old=await this.records.get(key,r=>this.validate(r));if(old){need(old.data.creationHash===creationHash,'STATE_OPERATION_COLLISION');return old.data;}
  const r={format:FORMAT,id:key,revision:0,stateRevision:0,binding:clone(this.binding),creationHash,source:clone(source),config:clone(config),initial:clone(initial),state:clone(initial),enabled:true,pending:null,turns:[],events:[],operations:{},createdAt:new Date().toISOString()};
  this.validate(r);await this.records.put(key,null,r);return r;
 }
 async update(key,fn){
  const row=await this.envelope(key),r=clone(row.data);const result=fn(r);need(!result?.then,'CORE_SYNC_MUTATION');
  r.revision++;this.validate(r);await this.records.put(key,row.hash,r);return {record:r,result};
 }
 async start(key,request,context,options,adopted=null){
  keys(request,['operationId','revision','input']);need(id(request.operationId)&&text(request.input,20000)&&request.input.trim(),'STATE_INPUT');
  const fingerprint=hash({request,options}),prior=await this.read(key),old=prior.operations[request.operationId];
  if(old){need(old.requestHash===fingerprint,'STATE_OPERATION_COLLISION');return {record:prior,dispatch:false};}
  const {record}=await this.update(key,r=>{
   need(r.enabled,'STATE_DISABLED');need(r.revision===request.revision,'STATE_STALE');need(!r.pending,'STATE_TURN_PENDING');
   r.operations[request.operationId]={kind:'turn',requestHash:fingerprint,input:request.input,status:adopted?'received':'pending',stateRevision:r.stateRevision,
    context:clone(context),options:clone(options),frozenState:clone(r.state),stages:[],query:null,receipt:clone(adopted)};
   r.pending=request.operationId;
  });return {record,dispatch:true};
 }
 async mark(key,operationId,status,error=null){
  need(['failed','unknown','rejected'].includes(status),'CORE_STATUS');
  return (await this.update(key,r=>{need(r.pending===operationId,'STATE_TURN_MISMATCH');Object.assign(r.operations[operationId],{status,error});})).record;
 }
 async receive(key,operationId,receipt){
  return (await this.update(key,r=>{need(r.pending===operationId,'STATE_TURN_MISMATCH');const op=r.operations[operationId];
   need(receipt?.status==='completed','STATE_REPLY_INCOMPLETE');bounded(receipt);
   if(op.receipt)need(hash(op.receipt)===hash(receipt),'STATE_RECEIPT_COLLISION');
   op.receipt=clone(receipt);op.status='received';})).record;
 }
 async finish(key,operationId){
  const prior=await this.read(key);if(prior.operations[operationId]?.status==='completed')return prior;
  return (await this.update(key,r=>{
   const op=r.operations[operationId];need(r.pending===operationId&&op?.receipt,'STATE_RESULT_UNKNOWN');need(op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
   let proposal;try{proposal=this.adapter.candidate(r.state,op.receipt,op.context.statePolicy);bounded(proposal);this.adapter.validateState(proposal.state);}
   catch(e){op.status='rejected';op.error=e.code||'STATE_INVALID';return;}
   const before=clone(r.state);r.state=clone(proposal.state);r.stateRevision++;
   r.events.push({id:operationId,operationId,kind:'turn',stateRevision:r.stateRevision,before,after:clone(r.state),changes:clone(proposal.changes)});
   r.turns.push({id:operationId,eventId:operationId,input:op.input,body:proposal.body,changes:clone(proposal.changes),stateRevision:r.stateRevision});
   op.status='completed';op.eventId=operationId;r.pending=null;
  })).record;
 }
 async action(key,request,kind,mutate){
  need(id(request.operationId)&&Number.isSafeInteger(request.revision),'STATE_INPUT');
  const fingerprint=hash({kind,request}),old=await this.read(key),seen=old.operations[request.operationId];
  if(seen){need(seen.requestHash===fingerprint,'STATE_OPERATION_COLLISION');return old;}
  return (await this.update(key,r=>{need(r.revision===request.revision,'STATE_STALE');mutate(r);r.operations[request.operationId]={kind,requestHash:fingerprint,status:'completed'};})).record;
 }
 override(key,request){
  keys(request,['operationId','revision','state','reason']);bounded(request.state);this.adapter.validateState(request.state);need(text(request.reason,500)&&request.reason.trim(),'STATE_REASON_REQUIRED');
  return this.action(key,request,'override',r=>{need(!r.pending,'STATE_TURN_PENDING');const before=clone(r.state);r.state=clone(request.state);r.stateRevision++;
   r.events.push({id:request.operationId,operationId:request.operationId,kind:'override',stateRevision:r.stateRevision,before,after:clone(r.state),reason:request.reason,changes:[{path:'explicit-override',before,after:clone(r.state),reason:request.reason,authority:'user-override'}]});});
 }
 enabled(key,request){keys(request,['operationId','revision','enabled']);need(typeof request.enabled==='boolean');return this.action(key,request,'enabled',r=>{need(!r.pending,'STATE_TURN_PENDING');r.enabled=request.enabled;});}
 discard(key,request){
  keys(request,['operationId','revision','pendingId','reason']);need(text(request.reason,500)&&request.reason.trim(),'STATE_REASON_REQUIRED');
  return this.action(key,request,'discard',r=>{need(r.pending===request.pendingId&&['failed','rejected'].includes(r.operations[r.pending]?.status),'STATE_UNKNOWN_CANNOT_DISCARD');
   r.operations[r.pending].status='discarded';r.operations[r.pending].discardReason=request.reason;r.pending=null;});
 }
 async preview(key,receipt,policy){const r=await this.read(key);need(!r.pending,'STATE_TURN_PENDING');return {...this.adapter.candidate(r.state,receipt,policy),stateRevision:r.stateRevision};}
 async fork(key,{operationId,revision,eventId}){
  need(id(operationId),'STATE_ID');const source=await this.read(key);
  const creationHash=hash({key,operationId,revision,eventId}),childId='core-'+creationHash.slice(0,40),old=await this.records.get(childId,r=>this.validate(r));
  if(old){need(old.data.creationHash===creationHash,'STATE_OPERATION_COLLISION');return old.data;}
  need(source.revision===revision&&!source.pending,'STATE_CHECKPOINT_PENDING');
  const index=eventId===null?-1:source.events.findIndex(e=>e.id===eventId);need(eventId===null||index>=0,'STATE_CHECKPOINT_NOT_FOUND');
  const e=source.events[index],r=clone(source);r.id=childId;r.revision=0;r.creationHash=creationHash;
  r.source={kind:'checkpoint',id:key,eventId,history:clone(source.source.history)};r.state=clone(e?.after||source.initial);r.stateRevision=e?.stateRevision||0;
  r.events=source.events.slice(0,index+1);r.turns=source.turns.filter(t=>t.stateRevision<=r.stateRevision);r.operations={};r.pending=null;
  this.validate(r);await this.records.put(childId,null,r);return r;
 }
}

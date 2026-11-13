import {SessionRecords} from '../authoring/session-storage.mjs';
import {clone,hash,requireValue,id,integer,exact,canonical,fail} from './contracts.mjs';
import {validateRules,validateAssets,initialAssets,purchaseResult} from './shop-rules.mjs';
const FORMAT='modelmirror.rpg.shop-session/1';
const timestamp=()=>new Date().toISOString();
const unresolved=r=>r.active!==null;
const assets=r=>clone(r.assets);
function validateRecord(r){
 requireValue(r?.format===FORMAT&&id(r.id)&&integer(r.revision)&&Array.isArray(r.events)&&r.operations&&typeof r.enabled==='boolean','SHOP_RECORD_INVALID',409);
 validateRules(r.rules);validateAssets(r.assets,r.rules);
 requireValue(r.binding?.ruleHash===hash(r.rules)&&r.events.every(e=>id(e.id))&&(!r.active||r.events.some(e=>e.id===r.active)),'SHOP_RECORD_INVALID',409);
 return r;
}
export class ShopStore{
 constructor(records,{rules,binding}){this.records=records;this.rules=clone(validateRules(rules));this.binding=clone(binding);requireValue(binding.ruleHash===hash(rules),'SHOP_RULE_BINDING_INVALID');}
 static async open({directory,rules,binding,fault,recoverDeadOwner=false}){
  const records=await SessionRecords.open(directory,{scope:'rpg-shop',fault,recoverDeadOwner});
  return new ShopStore(records,{rules,binding});
 }
 async read(key){requireValue(id(key));const v=await this.records.get(key,validateRecord);if(!v)throw fail('SHOP_SESSION_NOT_FOUND',404);return v.data;}

 async close(){return this.records.close();}
 async reconcile(key){return this.records.reconcile(key);}
 async activate({operationId,source,initial,confirmed,sourceKind='user-confirmed'}){
  requireValue(id(operationId)&&source&&id(source.id)&&source.world===this.rules.world&&typeof source.characterText==='string'&&Array.isArray(source.history),'SHOP_SOURCE_INVALID');
  requireValue(confirmed===true&&['user-confirmed','verified-snapshot','example'].includes(sourceKind),'SHOP_INITIAL_CONFIRMATION_REQUIRED');
  requireValue(!source.pending,'SHOP_SOURCE_PENDING');
  validateAssets(initial,this.rules);
  const key='shop-'+hash([source.id,operationId]).slice(0,40);
  const activationHash=hash({operationId,source,initial,sourceKind,binding:this.binding});
  const prior=await this.records.get(key,validateRecord);
  if(prior){requireValue(prior.data.activationHash===activationHash,'SHOP_OPERATION_COLLISION',409);return prior.data;}
  const now=timestamp();
  const r={format:FORMAT,id:key,revision:0,createdAt:now,activationHash,activationInputHash:source.activationInputHash||null,binding:clone(this.binding),rules:clone(this.rules),
   source:{id:source.id,revision:source.revision??0,hash:hash(source),kind:sourceKind},
   session:{world:source.world,characterText:source.characterText,history:clone(source.history),enabledRuleIds:clone(source.enabledRuleIds||[]),options:clone(source.options||{}),context:clone(source.context||{})},
   memory:clone(source.memory||null),initial:clone(initial),assets:clone(initial),stateRevision:0,enabled:true,active:null,events:[],operations:{},
   stages:Object.fromEntries(['intent','narration','summary-small','summary-large','memory'].map(p=>[p,{primary:null,backup:null,cap:null}])),
   usage:{calls:0,reserved:0,settled:0,unknown:0},stageRuns:{},projection:[],notes:[]};
  await this.records.put(key,null,r);return r;
 }
 async change(key,request,kind,reduce){
  requireValue(id(request?.operationId)&&integer(request.revision),'SHOP_OPERATION_INVALID');
  const current=await this.records.get(key,validateRecord);if(!current)throw fail('SHOP_SESSION_NOT_FOUND',404);
  const r=current.data,requestHash=hash({kind,request}),prior=r.operations[request.operationId];
  if(prior){requireValue(prior.requestHash===requestHash,'SHOP_OPERATION_COLLISION',409);return {record:r,receipt:prior.receipt,replayed:true};}
  requireValue(!r.events.some(e=>e.id===request.operationId),'SHOP_OPERATION_COLLISION',409);
  requireValue(canonical(r.binding)===canonical(this.binding),'SHOP_ADAPTER_BINDING_CHANGED',409);
  requireValue(request.revision===r.revision,'SHOP_STATE_STALE',409);
  requireValue(Object.keys(r.operations).length<1000,'SHOP_OPERATION_LIMIT',409);
  const next=clone(r),receipt=await reduce(next);
  for(const e of next.events)if(!e.memoryCheckpoint)e.memoryCheckpoint=clone(next.memory);
  validateRecord(next);
  next.revision++;next.operations[request.operationId]={requestHash,kind,receipt:clone(receipt),at:timestamp()};
  await this.records.put(key,current.hash,next);return {record:next,receipt,replayed:false};
 }
 async purchase(key,request){
  requireValue(exact(request,['operationId','revision','ruleVersion','productId','quantity']),'SHOP_PURCHASE_INVALID');
  return this.change(key,request,'purchase',r=>{
   requireValue(r.enabled,'SHOP_DISABLED',409);requireValue(!unresolved(r),'SHOP_TURN_PENDING',409);
   requireValue(request.ruleVersion===r.rules.version,'SHOP_RULE_STALE',409);
   const before=assets(r),result=purchaseResult(before,r.rules,request);
   const event={id:request.operationId,kind:'purchase',...result,before,after:clone(result.assets),at:timestamp(),stateBefore:r.stateRevision,stateAfter:r.stateRevision+(result.outcome==='committed'?1:0),ruleVersion:r.rules.version,ruleHash:hash(r.rules),
    narration:{status:result.outcome==='committed'?'ready':'not-required',attempts:[],raw:null,error:null}};
   delete event.assets;
   if(result.outcome==='committed'){r.assets=result.assets;r.stateRevision++;r.active=event.id;}
   r.events.push(event);return clone(event);
  });
 }
 async override(key,request){
  requireValue(exact(request,['operationId','revision','assets','reason','relatedEventId'])&&typeof request.reason==='string'&&request.reason.trim().length>0&&request.reason.length<=2000,'SHOP_OVERRIDE_INVALID');
  return this.change(key,request,'override',r=>{
   requireValue(!unresolved(r),'SHOP_TURN_PENDING',409);
   requireValue(request.relatedEventId===null||r.events.some(e=>e.id===request.relatedEventId),'SHOP_EVENT_NOT_FOUND');
   validateAssets(request.assets,r.rules);
   const event={id:request.operationId,kind:'override',outcome:'committed',before:assets(r),after:clone(request.assets),reason:request.reason,relatedEventId:request.relatedEventId,at:timestamp(),stateBefore:r.stateRevision,stateAfter:r.stateRevision+1,ruleVersion:r.rules.version,ruleHash:hash(r.rules),narration:{status:'not-required',attempts:[],raw:null,error:null}};
   r.assets=clone(request.assets);r.stateRevision++;r.events.push(event);
   r.notes.push({eventId:event.id,kind:'correction',stateRevision:r.stateRevision,reason:request.reason});return event;
  });
 }
 async enabled(key,request){
  requireValue(exact(request,['operationId','revision','enabled'])&&typeof request.enabled==='boolean');
  return this.change(key,request,'enabled',r=>{r.enabled=request.enabled;return {enabled:r.enabled,assetsRetained:true};});
 }
 async endReceiptOnly(key,request){
  requireValue(exact(request,['operationId','revision','eventId']));
  return this.change(key,request,'receipt-only',r=>{
   const e=r.events.find(x=>x.id===request.eventId);
   requireValue(e&&r.active===e.id&&e.narration.status==='failed','SHOP_NARRATION_NOT_FAILED',409);
   e.narration.status=e.narration.raw?'complete':'receipt-only';e.backgroundSkipped=e.narration.raw?e.pendingStage:null;e.pendingStage=null;e.memoryCheckpoint=clone(r.memory);r.active=null;return {eventId:e.id,status:e.narration.status};
  });
 }
 async update(key,mutate){
  const current=await this.records.get(key,validateRecord);if(!current)throw fail('SHOP_SESSION_NOT_FOUND',404);
  requireValue(canonical(current.data.binding)===canonical(this.binding),'SHOP_ADAPTER_BINDING_CHANGED',409);
  const next=clone(current.data);await mutate(next);next.revision++;validateRecord(next);await this.records.put(key,current.hash,next);return next;
 }
 async fork(key,request){
  const {operationId,eventId,revision}=request;
  requireValue(id(operationId)&&id(eventId));const source=await this.read(key);
  requireValue(!source.active,'SHOP_TURN_PENDING',409);
  const index=source.events.findIndex(e=>e.id===eventId);requireValue(index>=0,'SHOP_CHECKPOINT_UNKNOWN');
  const checkpoint=source.events[index];requireValue(['complete','not-required','receipt-only'].includes(checkpoint.narration.status),'SHOP_CHECKPOINT_UNFINISHED',409);
  const childId='shop-'+hash([key,operationId]).slice(0,40),fingerprint=hash({source:key,operationId,eventId});
  const prior=await this.records.get(childId,validateRecord);
  if(prior){requireValue(prior.data.activationHash===fingerprint,'SHOP_OPERATION_COLLISION',409);return prior.data;}
  if(revision!==undefined)requireValue(revision===source.revision,'SHOP_STATE_STALE');
  const r=clone(source);r.id=childId;r.createdAt=timestamp();r.revision=0;r.activationHash=fingerprint;
  r.source={id:key,revision:source.revision,hash:hash(checkpoint),kind:'checkpoint',eventId};
  r.assets=clone(checkpoint.after);r.stateRevision=checkpoint.stateAfter;r.memory=clone(checkpoint.memoryCheckpoint);
  r.events=clone(source.events.slice(0,index+1));r.active=null;r.operations={};r.stageRuns={};r.usage={calls:0,reserved:0,settled:0,unknown:0};
  r.projection=r.projection.filter(x=>r.events.some(e=>e.id===x.eventId));
  r.notes=r.notes.filter(x=>r.events.some(e=>e.id===x.eventId));
  await this.records.put(childId,null,r);return r;
 }
}

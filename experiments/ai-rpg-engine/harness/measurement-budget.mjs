import {SessionRecords} from '../authoring/session-storage.mjs';
import {hash,clone,requireValue,integer} from './contracts.mjs';
export class MeasurementBudget{
 static async open({directory,plan,authorization,recoverDeadOwner=false}){
  requireValue(authorization?.confirmed===true&&authorization.planHash===hash(plan)&&authorization.maxCalls===plan.limits.maxCalls&&authorization.maxCostMicros===plan.limits.maxCostMicros,'SHOP_MEASUREMENT_NOT_AUTHORIZED');
  requireValue(plan.requests.length===plan.limits.maxCalls&&integer(plan.limits.maxCostMicros)&&plan.requests.every(r=>r.messagesHash&&integer(r.reserveMicros)),'SHOP_MEASUREMENT_PLAN_INVALID');
  const records=await SessionRecords.open(directory,{scope:'rpg-shop-measurement',recoverDeadOwner});
  const key='measurement-budget',prior=await records.get(key);
  try{if(prior)requireValue(prior.data.planHash===hash(plan),'SHOP_MEASUREMENT_PLAN_CHANGED');else await records.put(key,null,{planHash:hash(plan),entries:{},blocked:null});}
  catch(e){await records.close();throw e;}
  return new MeasurementBudget(records,plan,key);
 }
 constructor(records,plan,key){this.records=records;this.plan=clone(plan);this.key=key;this.queue=Promise.resolve();}
 serial(work){const task=this.queue.then(work);this.queue=task.catch(()=>{});return task;}
 async snapshot(){await this.records.reconcile(this.key);return (await this.records.get(this.key)).data;}
 reserve(testId,request){return this.serial(async()=>{
  const row=this.plan.requests.find(r=>r.id===testId);
  requireValue(row&&row.model===request.model.selection?.model&&row.purpose===request.purpose&&row.messagesHash===request.messagesHash&&hash(request.messages)===row.messagesHash&&hash(request.model.parameters)===row.parametersHash,'SHOP_MEASUREMENT_REQUEST_CHANGED');
  const current=await this.records.get(this.key),r=current.data,requestHash=hash(request),prior=r.entries[testId];
  if(prior){requireValue(prior.requestHash===requestHash,'SHOP_MEASUREMENT_ID_COLLISION');return {replayed:true};}
  requireValue(!r.blocked,'SHOP_MEASUREMENT_BLOCKED');
  const entries=Object.values(r.entries);requireValue(!entries.some(e=>e.status==='reserved'||e.status==='unknown'),'SHOP_MEASUREMENT_RESULT_PENDING');
  const spent=entries.reduce((n,e)=>n+(e.costMicros??e.reservedMicros),0);
  requireValue(entries.length<this.plan.limits.maxCalls&&spent+row.reserveMicros<=this.plan.limits.maxCostMicros,'SHOP_MEASUREMENT_LIMIT');
  r.entries[testId]={requestHash,requestId:request.id,sessionId:request.sessionId,reservedMicros:row.reserveMicros,costMicros:null,status:'reserved',measurement:null};
  await this.records.put(this.key,current.hash,r);return {replayed:false};
 });}
 settle(testId,request,result){return this.serial(async()=>{
  const current=await this.records.get(this.key),r=current.data,e=r.entries[testId];
  requireValue(e&&e.requestHash===hash(request),'SHOP_MEASUREMENT_ID_COLLISION');
  if(e.status==='succeeded'||e.status==='failed')return clone(e);
  e.status=['succeeded','failed'].includes(result.status)?result.status:'unknown';e.measurement=clone(result.measurement||null);
  if(integer(result.costMicros))e.costMicros=result.costMicros;
  if(e.costMicros>e.reservedMicros||result.usage?.issues?.length||result.usage?.inputTokens>this.plan.limits.inputAdmissionUnits||result.usage?.outputTokens>this.plan.parameters.max_tokens)r.blocked='USAGE_OR_COST_BOUND_EXCEEDED';
  await this.records.put(this.key,current.hash,r);return clone(e);
 });}
 async close(){await this.queue;await this.records.close();}
}

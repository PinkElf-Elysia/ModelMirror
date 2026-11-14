import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need,id,bounded} from '../contracts.mjs';
import {FORMAT,policy,totals} from './policy.mjs';
// This is the sole allowance owner for NEW R7 runs, not the platform billing ledger.
export class TaskLedger{
 static async open(directory,limits={calls:24,microUSD:1200000}){
  need(Number.isSafeInteger(limits.calls)&&limits.calls>=0&&limits.calls<=100&&Number.isSafeInteger(limits.microUSD)&&limits.microUSD>=0,'R7_LIMITS');
  const records=await SessionRecords.open(directory,{scope:'rpg-task-runtime-r7',recoverDeadOwner:true});
  try{await records.reconcile();const old=await records.get('allowance');
   if(old)need(old.data.format===FORMAT&&hash(old.data.limits)===hash(limits),'R7_ALLOWANCE_CHANGED');
   else await records.put('allowance',null,{format:FORMAT,revision:0,limits:clone(limits),tasks:{}});
   return new TaskLedger(records);
  }catch(e){await records.close();throw e;}
 }
 constructor(records){this.records=records;this.queue=Promise.resolve();}
 async read(){return clone((await this.records.get('allowance')).data);}
 change(fn){const p=this.queue.then(async()=>{const e=await this.records.get('allowance'),r=clone(e.data);const result=fn(r);r.revision++;bounded(r,{bytes:16*1024*1024,depth:60});await this.records.put('allowance',e.hash,r);return clone(result??r);});this.queue=p.catch(()=>{});return p;}
 async begin(spec,config){
  need(id(spec.id)&&['world-narration','world-dialogue','actor-response'].includes(spec.purpose)&&id(spec.session)&&typeof spec.sourceHash==='string'&&spec.ownerRef,'R7_TASK');
  const p=policy(config),signature=hash({spec,policy:p});
  return this.change(r=>{const old=r.tasks[spec.id];if(old){need(old.signature===signature,'R7_TASK_COLLISION');return old;}
   const t={...clone(spec),sequence:Object.keys(r.tasks).length+1,signature,policy:p,stages:[],adoption:'pending',receipt:null,error:null,fallbackUsed:false,pinnedModel:null};r.tasks[spec.id]=t;return t;});
 }
 async adopted(taskId,status,receipt=null,error=null){return this.change(r=>{const t=r.tasks[taskId];need(t,'R7_TASK_MISSING');
  if(t.adoption==='completed'){need(status==='completed'&&hash(t.receipt)===hash(receipt),'R7_ADOPTION_COLLISION');return;}
  t.adoption=status;t.receipt=clone(receipt);t.error=error;});}
 async stage(taskId,session,stageId,request,meta){return this.change(r=>{
  const t=r.tasks[taskId];need(t&&t.session===session,'R7_TASK_SCOPE');
  const requestHash=hash({request,meta}),old=t.stages.find(s=>s.id===stageId);
  if(old){need(old.requestHash===requestHash,'R7_REQUEST_COLLISION');return old;}
  need(t.stages.length<2&&!t.stages.some(s=>!s.result||s.result.status!=='completed'),'R7_STAGE_BLOCKED');
  const s={id:stageId,requestHash,request:clone(request),meta:clone(meta),attempts:[],result:null};t.stages.push(s);return s;
 });}
 async claim(taskId,stageId,model,reserveMicroUSD,fallback=false){return this.change(r=>{
  const t=r.tasks[taskId],s=t?.stages.find(s=>s.id===stageId);need(s&&!s.result,'R7_STAGE_DONE');
  need(Number.isSafeInteger(reserveMicroUSD)&&reserveMicroUSD>0,'R7_RESERVE');
  const attempts=t.stages.flatMap(s=>s.attempts),sum=totals(r);
  need(attempts.length<t.policy.maxCalls&&attempts.reduce((n,a)=>n+(a.chargeMicroUSD??a.reserveMicroUSD),0)+reserveMicroUSD<=t.policy.capMicroUSD,'R7_TASK_BUDGET');
  need(sum.calls<r.limits.calls&&sum.occupiedMicroUSD+reserveMicroUSD<=r.limits.microUSD,'R7_TOTAL_BUDGET');
  if(fallback){need(!t.fallbackUsed&&s.attempts.at(-1)?.result?.terminal===true&&s.attempts.at(-1)?.result?.status==='failed','R7_FALLBACK_BLOCKED');t.fallbackUsed=true;}
  else need(s.attempts.length===0,'R7_ALREADY_DISPATCHED');
  const a={id:'attempt-'+hash([taskId,stageId,s.attempts.length]).slice(0,40),model:clone(model),reserveMicroUSD,chargeMicroUSD:null,status:'dispatched',result:null,startedAt:Date.now()};
  s.attempts.push(a);return a;
 });}
 async settle(taskId,stageId,attemptId,result,timing){return this.change(r=>{
  const t=r.tasks[taskId],s=t.stages.find(s=>s.id===stageId),a=s.attempts.find(a=>a.id===attemptId);need(a,'R7_ATTEMPT_MISSING');
  if(['completed','failed'].includes(a.status)){need(hash(a.result)===hash(result),'R7_RECEIPT_COLLISION');return;}
  need(['completed','failed','unknown'].includes(result.status),'R7_RESULT');
  const cost=result.status!=='unknown'&&['simulated','provider','estimate'].includes(result.billing?.kind)?result.billing.microUSD:null;
  need(cost===null||Number.isSafeInteger(cost)&&cost>=0&&cost<=a.reserveMicroUSD,'R7_COST_BOUND');
  a.result=clone(result);a.status=result.status;a.chargeMicroUSD=cost;a.timing=clone(timing);a.finishedAt=Date.now();
  if(result.status==='completed'&&result.message?.tool_calls?.length)t.pinnedModel=clone(a.model);
 });}
 async finishStage(taskId,stageId,result){return this.change(r=>{r.tasks[taskId].stages.find(s=>s.id===stageId).result=clone(result);});}
 async find(session,stageId){const r=await this.read();for(const t of Object.values(r.tasks))if(t.session===session){const s=t.stages.find(s=>s.id===stageId);if(s)return {task:t,stage:s};}return null;}
 async close(){await this.queue;await this.records.close();}
}

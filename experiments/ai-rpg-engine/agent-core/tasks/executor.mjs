import {clone,hash,need} from '../contracts.mjs';
import {reserve} from '../actions/real/provider.mjs';
const unknown=error=>({status:'unknown',error});
// A provider facade shared by R5/R6 owners. Owners still parse tools and adopt text.
export class TaskExecutor{
 constructor(ledger,transport,{controlledReal=false}={}){need(transport.mode==='offline'||controlledReal===true&&['mock','real'].includes(transport.mode),'R7_REAL_DISABLED');Object.assign(this,{ledger,transport,mode:transport.mode,calls:0});this.active=null;this.queue=Promise.resolve();}
 options(o={}){need(!Object.keys(o).length,'R7_OPTIONS');return {fault:null};}
 async using(taskId,fn){const prior=this.queue;let release;this.queue=new Promise(ok=>{release=ok;});await prior;this.active=taskId;try{return await fn();}finally{this.active=null;release();}}
 async generate(session,stageId,request,meta){
  need(this.active,'R7_TASK_REQUIRED');const taskId=this.active;
  await this.ledger.stage(taskId,session,stageId,request,meta);
  return this.advance(taskId,stageId,false);
 }
 async lookup(session,stageId){const found=await this.ledger.find(session,stageId);if(!found)return null;return this.advance(found.task.id,stageId,true);}
 async advance(taskId,stageId,recover){
  for(let n=0;n<2;n++){
   const r=await this.ledger.read(),t=r.tasks[taskId],s=t.stages.find(s=>s.id===stageId);
   if(s.result&&s.result.status!=='unknown')return clone(s.result);
   let a=s.attempts.at(-1),result=a?.result;
   if(a&&['dispatched','unknown'].includes(a.status)){
    if(!recover)return unknown('R7_RECOVERY_REQUIRED');
    try{result=await this.transport.lookup(t.session,a.id);}catch{return unknown('R7_LOOKUP_UNKNOWN');}
    if(!result||result.status==='unknown')return unknown('R7_LOOKUP_UNKNOWN');
    await this.ledger.settle(taskId,stageId,a.id,result,{source:'offline-recovered',proseFirstMs:null});
   }
   const previousFailed=a&&result?.status==='failed';
   const nativeContinuation=s.request.messages.some(m=>m.role==='tool'||m.tool_calls?.length);
   const canFallback=previousFailed&&result.terminal===true&&t.policy.backup&&!t.fallbackUsed&&!nativeContinuation&&!t.pinnedModel;
   if(a&&!canFallback){await this.ledger.finishStage(taskId,stageId,result);return clone(result);}
   const model=canFallback?t.policy.backup:(t.pinnedModel??t.policy.model);
   let reservation;
   try{
    const budgetPayload=this.transport.budgetPayload?this.transport.budgetPayload({taskId,session:t.session,id:'attempt-'+hash([taskId,stageId,s.attempts.length]).slice(0,40),request:clone(s.request),model:clone(model)}):{...clone(s.request),parameters:model.parameters};
    reservation=reserve(budgetPayload,model.quote,{max:model.parameters.max_tokens,cap:t.policy.capMicroUSD/1e6});
    a=await this.ledger.claim(taskId,stageId,model,reservation.reserveMicros,!!canFallback);
   }catch(e){const blocked={status:'failed',terminal:true,dispatched:false,error:e.code||'R7_RESERVATION_FAILED'};await this.ledger.finishStage(taskId,stageId,blocked);return blocked;}
   this.calls++;const began=performance.now();
   try{result=await this.transport.generate({taskId:t.id,reserveMicroUSD:a.reserveMicroUSD,purpose:t.purpose,session:t.session,id:a.id,request:clone(s.request),meta:clone(s.meta),model:clone(model),fault:t.fault??null,attempt:s.attempts.length});}
   catch(e){result=unknown(e.code||'R7_TRANSPORT_UNKNOWN');}
   // A generic failed string is not proof of a terminated upstream request.
   if(result?.status==='failed'&&result.terminal!==true)result=unknown('R7_FAILURE_UNCONFIRMED');
   if(!result||!['completed','failed','unknown'].includes(result.status))result=unknown('R7_INVALID_RECEIPT');
   const elapsed=performance.now()-began;
   const timing={source:'offline-buffered-fixture',firstEffectiveMs:null,firstContentMs:result.message?.content?elapsed:null,
    proseFirstMs:result.finishReason==='stop'&&result.message?.content?elapsed:null,completedMs:elapsed};
   try{await this.ledger.settle(taskId,stageId,a.id,result,result.timing??timing);}
   catch{return unknown('R7_RECEIPT_SAVE_REQUIRED');}
   if(result.status==='failed'&&result.terminal&&t.policy.backup&&!t.fallbackUsed&&!canFallback&&!nativeContinuation&&!t.pinnedModel)continue;
   await this.ledger.finishStage(taskId,stageId,result);return clone(result);
  }
  return unknown('R7_ATTEMPT_LIMIT');
 }
 close(){} // Composition owns the shared allowance and transport.
}

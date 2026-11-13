import {hash,clone,requireValue,integer,exact,fail} from './contracts.mjs';
const now=()=>Date.now();
const terminal=s=>['succeeded','failed'].includes(s);
export class StageRunner{
 constructor({store,provider,assemble,validate=()=>null,onSuccess=()=>{}}){this.onSuccess=onSuccess;this.store=store;this.provider=provider;this.assemble=assemble;this.validate=validate;this.running=new Map();}
 async configure(id,request){
  requireValue(exact(request,['operationId','revision','purpose','primary','backup','cap'])&&['intent','narration','summary-small','summary-large','memory'].includes(request.purpose)&&integer(request.cap),'SHOP_STAGE_CONFIG_INVALID');
  const catalog=await this.provider.catalog();
  for(const key of ['primary','backup'])requireValue(key==='backup'&&request[key]===null||catalog.some(x=>x.id===request[key]&&x.available),'SHOP_MODEL_UNAVAILABLE');
  requireValue(request.primary!==request.backup,'SHOP_BACKUP_DUPLICATES_PRIMARY');
  return this.store.change(id,request,'stage-config',r=>{
   requireValue(!r.active,'SHOP_TURN_PENDING',409);r.stages[request.purpose]={primary:request.primary,backup:request.backup,cap:request.cap};return clone(r.stages[request.purpose]);
  });
 }
 async retry(id,request){
  requireValue(exact(request,['operationId','revision','eventId','purpose'])&&['intent','narration','summary-small','summary-large','memory'].includes(request.purpose),'SHOP_RETRY_INVALID');
  return this.store.change(id,request,'stage-retry',r=>{
   const run=r.stageRuns[request.eventId+'-'+request.purpose],e=r.events.find(e=>e.id===request.eventId);
   requireValue(r.active===request.eventId&&e?.narration.status==='failed','SHOP_RETRY_NOT_ALLOWED');
   if(!run){
    requireValue((e.pendingStage||(e.kind==='input'?'intent':'narration'))===request.purpose&&!Object.values(r.stageRuns).some(s=>s.eventId===e.id&&['pending','unknown'].includes(s.status)),'SHOP_RETRY_NOT_ALLOWED');
    requireValue((e.preflightRetries||0)<3,'SHOP_RETRY_LIMIT');e.preflightRetries=(e.preflightRetries||0)+1;e.narration.status='ready';e.narration.error=null;return {eventId:e.id,stage:request.purpose,preflightRetries:e.preflightRetries};
   }
   requireValue(run.status==='failed','SHOP_RETRY_NOT_ALLOWED');
   requireValue(!run.attempts.some(a=>['pending','unknown'].includes(a.status))&&run.error!=='SHOP_COST_BOUND_EXCEEDED','SHOP_RETRY_NOT_ALLOWED');
   requireValue((run.manualRetries||0)<3,'SHOP_RETRY_LIMIT');
   run.manualRetries=(run.manualRetries||0)+1;run.cycleStart=run.attempts.length;run.status='ready';run.error=null;run.completedAt=null;e.narration.status='ready';e.narration.error=null;
   return {eventId:e.id,stage:run.purpose,manualRetries:run.manualRetries};
  });
 }
 async run(id,eventId,purpose='narration',recover=false){
  const lock=id+'/'+purpose;if(this.running.has(lock))return this.running.get(lock);
  const pending=this.execute(id,eventId,purpose,recover);this.running.set(lock,pending);
  try{return await pending;}finally{this.running.delete(lock);}
 }
 async execute(id,eventId,purpose,recover){
  requireValue(['intent','narration','summary-small','summary-large','memory'].includes(purpose));
  let r=await this.store.read(id),e=r.events.find(x=>x.id===eventId);
  requireValue(e,'SHOP_EVENT_NOT_FOUND');
  const key=eventId+'-'+purpose;
  if(r.stageRuns[key]?.status==='succeeded')return r;
  if(r.stageRuns[key]?.status==='failed')return r;
  requireValue(r.active===eventId,'SHOP_EVENT_NOT_ACTIVE',409);
  let run=r.stageRuns[key];
  if(!run){
   const config=clone(r.stages[purpose]),catalog=await this.provider.catalog();
   const primary=catalog.find(x=>x.id===config.primary&&x.available),backup=config.backup?catalog.find(x=>x.id===config.backup&&x.available):null;
   requireValue(primary&&(!config.backup||backup),'SHOP_MODEL_REQUIRED',409);
   requireValue(integer(config.cap)&&integer(primary.upperBoundMicros)&&(!backup||integer(backup.upperBoundMicros)),'SHOP_BUDGET_REQUIRED',409);
   const assembled=await this.assemble(r,e,purpose);
   run={id:key,purpose,eventId,stateRevision:r.stateRevision,config,primary:clone(primary),backup:clone(backup||null),messages:assembled.messages,messagesHash:hash(assembled.messages),
    status:'ready',attempts:[],startedAt:now(),completedAt:null,result:null,provenance:this.provider.mode};
   r=await this.store.update(id,n=>{requireValue(n.active===eventId&&n.stateRevision===run.stateRevision,'SHOP_STATE_STALE');requireValue(!n.stageRuns[key],'SHOP_STAGE_COLLISION');n.stageRuns[key]=run;});
  }
  if(['pending','unknown'].includes(run.status)){
   if(!recover)return r;
   const a=run.attempts.at(-1);let result;
   try{result=await this.provider.lookup(clone(a.request));}catch{result={status:'unknown'};}
   r=await this.accept(id,key,result);run=r.stageRuns[key];
   if(!terminal(run.status)&&run.status!=='ready')return r;
  }
  if(run.status==='succeeded'||run.status==='failed')return r;
  while(true){
   r=await this.store.read(id);run=r.stageRuns[key];
   if(terminal(run.status)||run.status==='unknown'||run.status==='pending')return r;
   const cycleAttempts=run.attempts.length-(run.cycleStart||0);
   const selected=cycleAttempts===0?run.primary:run.backup;
   if(!selected||cycleAttempts>=2)return this.failure(id,key,'SHOP_BACKUP_UNAVAILABLE');
   const charged=run.attempts.reduce((s,a)=>s+(a.costMicros??a.reservation),0);
   if(!integer(charged+selected.upperBoundMicros)||charged+selected.upperBoundMicros>run.config.cap)return this.failure(id,key,'SHOP_COST_LIMIT');
   if(r.usage.calls>=100)return this.failure(id,key,'SHOP_CALL_LIMIT');
   try{await this.provider.validate(selected);}catch{return this.failure(id,key,'SHOP_MODEL_CONFIGURATION_INVALID');}
   const request={id:'call-'+hash([id,key,run.attempts.length]).slice(0,40),sessionId:id,eventId,purpose,model:clone(selected),messages:clone(run.messages),messagesHash:run.messagesHash};
   const attempt={request,status:'pending',reservation:selected.upperBoundMicros,costMicros:null,startedAt:now(),completedAt:null,receipt:null};
   r=await this.store.update(id,n=>{
    const current=n.stageRuns[key];requireValue(n.active===eventId&&n.stateRevision===run.stateRevision&&current.status==='ready'&&current.attempts.length===run.attempts.length,'SHOP_STAGE_STALE');
    current.attempts.push(attempt);current.status='pending';n.usage.calls++;n.usage.reserved+=attempt.reservation;
    if(purpose==='narration')n.events.find(x=>x.id===eventId).narration.status='pending';
   });
   let result;
   // Mark pending before dispatch. Throws and timeouts are unknown until receipt lookup.
   try{let timer;try{result=await Promise.race([this.provider.dispatch(clone(request)),new Promise(resolve=>{timer=setTimeout(()=>resolve({status:'unknown'}),this.provider.timeoutMs||30000);})]);}finally{clearTimeout(timer);}}catch{result={status:'unknown'};}
   r=await this.accept(id,key,result);run=r.stageRuns[key];
   if(run.status!=='ready')return r;
  }
 }
 async failure(id,key,code){return this.store.update(id,r=>{
  const run=r.stageRuns[key];run.status='failed';run.error=code;run.completedAt=now();
  const e=r.events.find(x=>x.id===run.eventId);e.narration.status='failed';e.narration.error=code;
 });}
 async accept(id,key,result){
  return this.store.update(id,r=>{
   const run=r.stageRuns[key],attempt=run.attempts.at(-1),e=r.events.find(x=>x.id===run.eventId);
   requireValue(attempt&&['pending','unknown'].includes(attempt.status),'SHOP_ATTEMPT_ALREADY_SETTLED');
   requireValue(r.active===e.id&&r.stateRevision===run.stateRevision,'SHOP_STAGE_STALE');
   const credible=result&&result.requestId===attempt.request.id&&result.messagesHash===run.messagesHash&&terminal(result.status)&&result.terminal===true&&result.actualModel===(attempt.request.model.selection?.model||attempt.request.model.id);
   if(!credible){if(attempt.status!=='unknown')r.usage.unknown++;attempt.status='unknown';run.status='unknown';e.narration.status='unknown';return;}
   if(attempt.status==='unknown')r.usage.unknown--;
   attempt.status=result.status;attempt.completedAt=now();attempt.receipt=clone(result);attempt.measurement=clone(result.measurement||null);
   // An unknown charge remains conservatively reserved; do not assume failed calls are free.
   if(integer(result.costMicros)){
    attempt.costMicros=result.costMicros;r.usage.reserved-=attempt.reservation;r.usage.settled+=result.costMicros;
   }
   if(integer(result.costMicros)&&result.costMicros>attempt.reservation){
    run.status='failed';run.error='SHOP_COST_BOUND_EXCEEDED';e.narration.status='failed';e.narration.error=run.error;return;
   }
   if(result.status==='failed'){
    run.status=run.attempts.length-(run.cycleStart||0)===1&&run.backup&&result.safeToRetry===true?'ready':'failed';
    run.error=result.code||'SHOP_MODEL_FAILED';e.narration.status=run.status==='ready'?'ready':'failed';e.narration.error=run.error;
    if(run.status==='failed')run.completedAt=now();return;
   }
   const issue=this.validate(result.raw,r,run.purpose,run.eventId);
   if(issue){run.status='failed';run.error=issue;run.result=clone(result);run.completedAt=now();e.narration.status='failed';e.narration.error=issue;return;}
   run.status='succeeded';run.result=clone(result);run.completedAt=now();
   if(run.purpose==='narration'){
    e.narration={status:'complete',raw:result.raw,error:null,attempts:run.attempts.map(a=>({requestId:a.request.id,model:a.request.model.id,status:a.status})),switched:run.attempts.some(a=>a.request.model.id!==run.primary.id)};
    if(!r.projection.some(p=>p.eventId===e.id))r.projection.push({eventId:e.id,input:e.input||'购买 '+e.quantity+' × '+r.rules.products.find(p=>p.id===e.productId)?.name,raw:result.raw,stateRevision:r.stateRevision});
    r.active=null;
   }
   this.onSuccess(r,run,result);
  });
 }
}

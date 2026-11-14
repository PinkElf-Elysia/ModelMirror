import {clone,hash,need,id,keys} from '../contracts.mjs';
import {greeting} from '../../character/context.mjs';
import {FORMAT} from './tasks.mjs';
import {query} from './observations.mjs';
import {assembleActor,finalText,BRIDGE_VERSION} from './context.mjs';
import {parseResult,continuation} from '../actions/protocol.mjs';

// A small coordinator over two owners: existing R5 ActionHost and a new
// world-linked character response adapter. Old character sessions are untouched.
export class BridgeHost{
 constructor({world,worldPort,works,records,provider,reference,runtimeHash,requestPolicy=request=>request}){Object.assign(this,{world,worldPort,works,records,provider,reference,runtimeHash,requestPolicy});this.locks=new Map();}
 async exclusive(key,fn){const prior=this.locks.get(key)||Promise.resolve(),job=prior.catch(()=>{}).then(fn);this.locks.set(key,job);
  try{return await job;}finally{if(this.locks.get(key)===job)this.locks.delete(key);}}
 async read(key){const r=await this.records.read(key);need(r.runtimeHash===this.runtimeHash,'BRIDGE_RUNTIME_CHANGED');
  const w=await this.world.store.read(r.worldId);need(hash(w.binding)===r.worldBindingHash,'BRIDGE_WORLD_BINDING');return r;}
 async create({operationId,inventory=false}){
  need(id(operationId)&&typeof inventory==='boolean','BRIDGE_INPUT');
  return this.exclusive('create',async()=>{
   const config=await this.works.verifyConfiguration(this.reference.workId,this.reference.versionId);
   const card=config.document.character.card,configurationHash=hash(config);
   const world=await this.world.create({operationId,initial:this.world.adapter.initial(),confirmed:true,source:{kind:'new',id:'r6-neutral-world',history:[]}});
   const key='bridge-'+hash([world.id,operationId]).slice(0,40);
   const make=(actorId,witness)=>({id:actorId,card:clone(card),player:'同行',opening:greeting(card,0,'同行'),
    binding:{version:BRIDGE_VERSION,worldId:world.id,branchId:world.id,joinedAtVersion:world.stateRevision,
     reference:clone(this.reference),configurationHash,openingIndex:0,instanceId:actorId,
     grant:{version:1,witness,inventory:witness&&inventory}},
    history:[],responses:[],observations:[],pending:null});
   return this.records.create({format:FORMAT,id:key,revision:0,runtimeHash:this.runtimeHash,
    creationHash:hash({world:world.id,configurationHash,inventory,runtime:this.runtimeHash}),worldId:world.id,worldBindingHash:hash(world.binding),
    actors:{witness:make('witness',true),outsider:make('outsider',false)},tasks:{}});
  });
 }
 async deliver(key){
  return this.exclusive(key,async()=>{
   const r=await this.read(key),w=await this.world.store.read(r.worldId);
   return this.records.update(key,row=>{for(const a of Object.values(row.actors)){
    for(const event of this.worldPort.project(w,a).events)if(!a.observations.some(e=>e.id===event.id))a.observations.push(event);
   }});
  });
 }
 async worldAction(key,{operationId,revision}){
  need(id(operationId)&&Number.isSafeInteger(revision),'BRIDGE_INPUT');
  return this.exclusive(key,async()=>{
   const r=await this.read(key),w=await this.world.store.read(r.worldId);
   const request={operationId,revision,action:clone(this.worldPort.confirmedAction)};
   const spec={id:operationId,purpose:'world-action',owner:'r5-action-host',worldId:r.worldId,branchId:r.worldId,actorId:'player'};
   const task=await this.records.begin(key,spec,{request});
   if(task.status==='completed')return this.read(key);
   // Never replay an uncertain owner operation here; explicit recover queries its record.
   need(task.status==='prepared','TASK_RECOVERY_REQUIRED');
   await this.records.update(key,row=>{row.tasks[operationId].status='running';});
   try{
    const done=await this.world.execute(w.id,request);
    await this.captureWorld(key,operationId,done);
   }catch(e){await this.records.update(key,row=>{const t=row.tasks[operationId];t.status='unknown';t.error=e.code||'WORLD_RESULT_UNKNOWN';});}
   return this.read(key);
  });
 }
 async captureWorld(key,taskId,w){
  const op=w.operations[taskId];need(op?.receipt?.status==='executed','WORLD_RECEIPT_REQUIRED');
  return this.records.update(key,row=>{Object.assign(row.tasks[taskId],{status:'completed',error:null,
   receipt:{owner:'r5-action-host',worldId:w.id,operationId:taskId,eventId:op.receipt.eventId,stateVersion:op.receipt.stateRevision,result:clone(op.receipt.result)}});});
 }
 async narrateWorld(key){
  return this.exclusive(key,async()=>{const r=await this.read(key),w=await this.world.store.read(r.worldId);if(!w.pending)return r;
   await this.world.narrate(w.id);return this.read(key);});
 }
 async respond(key,{operationId,actorId,input,fault=null}){
  need(id(operationId)&&['witness','outsider'].includes(actorId)&&typeof input==='string'&&input.trim()&&input.length<=2000,'BRIDGE_INPUT');
  need([null,'failed','unknown','write','foreign-tool','truncated'].includes(fault),'BRIDGE_FAULT');
  return this.exclusive(key,async()=>{
   const r=await this.read(key),actor=r.actors[actorId],prior=r.tasks[operationId];
   if(prior){need(prior.purpose==='actor-response'&&prior.actorId===actorId&&prior.input.userInput===input&&prior.input.fault===fault,'STATE_OPERATION_COLLISION');return r;}
   need(!actor.pending,'ACTOR_TASK_PENDING');
   const config=await this.works.verifyConfiguration(actor.binding.reference.workId,actor.binding.reference.versionId);
   need(hash(config)===actor.binding.configurationHash,'ACTOR_TEMPLATE_CHANGED');
   const w=await this.world.store.read(r.worldId),frame=this.worldPort.project(w,actor);
   frame.events=frame.events.filter(e=>actor.observations.some(o=>o.id===e.id));
   const assembled=assembleActor(actor,frame,input);
   assembled.request=this.requestPolicy(clone(assembled.request),{actorId});
   await this.records.begin(key,{id:operationId,purpose:'actor-response',owner:'world-linked-actor-adapter',
    worldId:w.id,branchId:w.id,actorId,stateVersion:w.stateRevision,sourceEvents:frame.events.map(e=>e.id),
    actorBindingHash:hash(actor.binding),runtimeVersion:BRIDGE_VERSION},
    {userInput:input,fault,frame,request:assembled.request,trace:assembled.trace});
   await this.records.update(key,row=>{row.actors[actorId].pending=operationId;});
   return this.advance(key,operationId,false);
  });
 }
 async advance(key,taskId,recover){
  for(let i=0;i<3;i++){
   let r=await this.read(key),task=r.tasks[taskId],actor=r.actors[task.actorId];
   if(['completed','failed','rejected','stale'].includes(task.status))return r;
   const current=await this.world.store.read(r.worldId);
   if(current.stateRevision!==task.stateVersion||hash(actor.binding)!==task.actorBindingHash)
    return this.end(key,taskId,'stale','SOURCE_VERSION_CHANGED');
   let stage=task.stages.at(-1);
   if(!stage||stage.status==='queried'){
    need(task.stages.length<2,'ACTOR_STAGE_LIMIT');
    const request=stage?continuation(task.input.request,stage.result.message,stage.toolResult,[]):clone(task.input.request);
    stage={id:'stage-'+hash([key,taskId,task.stages.length]).slice(0,40),status:'ready',request,result:null};
    await this.records.update(key,row=>{row.tasks[taskId].stages.push(stage);row.tasks[taskId].status='running';});
   }
   let result=stage.result;
   if(!result||result.status==='unknown'){
    if(['dispatched','unknown'].includes(stage.status)){
     if(!recover)return r;
     result=await this.provider.lookup(key,stage.id);
     if(!result||result.status==='unknown')return this.end(key,taskId,'unknown','ACTOR_RESULT_UNKNOWN');
    }else{
     need(stage.status==='ready','ACTOR_STAGE');
     await this.records.update(key,row=>{row.tasks[taskId].stages.at(-1).status='dispatched';});
     try{result=await this.provider.generate(key,stage.id,clone(stage.request),{
      frame:clone(task.input.frame),input:task.input.userInput,fault:task.input.fault});}
     catch{result={status:'unknown',error:'ACTOR_RESULT_UNKNOWN'};}
    }
    await this.records.update(key,row=>{const s=row.tasks[taskId].stages.at(-1);s.result=clone(result);s.status=result.status;});
   }
   if(result.status!=='completed')return this.end(key,taskId,result.status==='failed'?'failed':'unknown',result.error||'ACTOR_RESULT_UNKNOWN');
   if((await this.world.store.read(r.worldId)).stateRevision!==task.stateVersion)return this.end(key,taskId,'stale','SOURCE_VERSION_CHANGED');
   let parsed;
   try{parsed=parseResult(result,stage.request.tools);
    if(parsed.kind==='tool'){
     need(parsed.name==='query_local_state'&&task.stages.length<=1,'ACTOR_TOOL_FORBIDDEN');
     const output=query(task.input.frame,parsed.args);
     await this.records.update(key,row=>{const s=row.tasks[taskId].stages.at(-1);s.toolResult=output;s.status='queried';});
     continue;
    }
    const body=finalText(parsed);
    return this.records.update(key,row=>{const t=row.tasks[taskId],a=row.actors[t.actorId];t.status='completed';t.error=null;
     t.receipt={owner:'world-linked-actor-adapter',responseId:taskId,sourceEvents:t.sourceEvents,stateVersion:t.stateVersion,authority:'actor-response-only',body};
     a.history.push({role:'user',content:t.input.userInput},{role:'assistant',content:body});
     a.responses.push(clone(t.receipt));a.pending=null;
    });
   }catch(e){return this.end(key,taskId,'rejected',e.code||'ACTOR_INVALID_REPLY');}
  }
  return this.end(key,taskId,'rejected','ACTOR_STAGE_LIMIT');
 }
 async end(key,taskId,status,error){return this.records.update(key,row=>{const t=row.tasks[taskId];t.status=status;t.error=error;
  if(status!=='unknown')row.actors[t.actorId].pending=null;});}
 async recover(key,taskId){
  return this.exclusive(key,async()=>{
   await this.records.records.reconcile(key);const r=await this.read(key),task=r.tasks[taskId];need(task,'TASK_NOT_FOUND');
   if(task.status==='completed')return r;
   if(task.purpose==='world-action'){
    await this.world.store.reconcile(r.worldId);const w=await this.world.store.read(r.worldId);
    if(w.operations[taskId]?.receipt?.status==='executed')return this.captureWorld(key,taskId,w);
    return r; // No owner receipt: leave unresolved; never dispatch again.
   }
   return this.advance(key,taskId,true);
  });
 }
 async view(key){
  const r=await this.read(key),w=await this.world.store.read(r.worldId);
  return {id:key,mode:this.provider.mode==='real'?'controlled-real':'offline-fixture',revision:r.revision,world:{id:w.id,revision:w.revision,stateVersion:w.stateRevision,
   ...this.worldPort.summary(w.state),events:w.events.filter(x=>x.kind==='action').map(x=>({id:x.id,result:x.receipt.result})),
   narration:w.turns.map(t=>t.body),pending:w.pending},
   actors:Object.values(r.actors).map(a=>({id:a.id,name:a.card.data.name,binding:a.binding,opening:a.opening,history:a.history,
    observations:a.observations,pending:a.pending,responses:a.responses.map(x=>({...x,current:x.stateVersion===w.stateRevision}))})),
   tasks:Object.values(r.tasks).sort((a,b)=>a.sequence-b.sequence).map(t=>({id:t.id,purpose:t.purpose,owner:t.owner,actorId:t.actorId,status:t.status,error:t.error,
    sourceEvents:t.sourceEvents??[],stateVersion:t.stateVersion??t.receipt?.stateVersion,receipt:t.receipt,
    stages:t.stages.map(s=>({id:s.id,status:s.status,toolResult:s.toolResult??null}))})),
   metrics:{kind:this.provider.mode==='real'?'world-offline-actor-real':'offline-fixture',realCalls:this.provider.mode==='real'?this.provider.calls:0,costUSD:this.provider.mode==='real'?null:0,worldGenerations:this.world.provider.calls,actorGenerations:this.provider.calls}};
 }
}

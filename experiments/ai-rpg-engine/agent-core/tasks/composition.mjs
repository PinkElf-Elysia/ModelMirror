import {join} from 'node:path';
import {AuthoringStore} from '../../authoring/store.mjs';
import {actionAdapters} from '../actions/adapters.mjs';
import {ActionHost} from '../actions/host.mjs';
import {ActionSimulator} from '../actions/simulator.mjs';
import {ActorSimulator} from '../actors/simulator.mjs';
import {TaskRecords} from '../actors/tasks.mjs';
import {BridgeHost} from '../actors/host.mjs';
import {cultivationWorldPort} from '../actors/world-port.mjs';
import {ensureCharacter} from '../actors/fixtures.mjs';
import {runtimeManifest} from '../manifest.mjs';
import {clone,hash,need,id,keys} from '../contracts.mjs';
import {TaskLedger} from './ledger.mjs';
import {TaskExecutor} from './executor.mjs';
import {OfflineTransport} from './transport.mjs';
import {MODEL_A,MODEL_B,totals,policy} from './policy.mjs';
export const FAULTS=[null,'failed','backup-failed','unknown','unknown-sticky','continuation-failed','write','foreign-tool','truncated'];
export const STRATEGIES={same:{model:MODEL_A},split:{model:MODEL_B},backup:{model:MODEL_A,backup:MODEL_B}};
export async function openRuntime(directory,{offlinePreview=false,limits,delayMs=40}={}){
 need(offlinePreview===true,'R7_PREVIEW_DISABLED');
 const manifest=await runtimeManifest([new URL('./composition.mjs',import.meta.url)],[{url:new URL('../../package-lock.json',import.meta.url)}]);
 const owners=[];
 try{
  const ledger=await TaskLedger.open(join(directory,'tasks'),limits);owners.push(ledger);
  await ledger.change(r=>{if(r.runtimeHash)need(r.runtimeHash===manifest.hash,'R7_RUNTIME_CHANGED');else r.runtimeHash=manifest.hash;r.runs??={};});
  const works=await AuthoringStore.open(join(directory,'works'),{recoverDeadOwner:true});owners.push(works);const {reference}=await ensureCharacter(works);
  const actorSimulator=await ActorSimulator.open(join(directory,'actor-fixture'));owners.push(actorSimulator);
  const records=await TaskRecords.open(join(directory,'bridge'));owners.push(records);
  const worlds=new Map(),transports=new Map(),worldProviders=new Map(),actorProviders=new Map();
  for(const a of await actionAdapters()){
   a.binding.manifestHash=manifest.hash;
   const fixture=await ActionSimulator.open(join(directory,a.id,'world-fixture'),a);owners.push(fixture);
   const transport=await OfflineTransport.open(join(directory,a.id,'transport'),fixture,actorSimulator,{delayMs});owners.push(transport);transports.set(a.id,transport);
   const provider=new TaskExecutor(ledger,transport),actorProvider=new TaskExecutor(ledger,transport);worldProviders.set(a.id,provider);actorProviders.set(a.id,actorProvider);
   const world=await ActionHost.open({directory:join(directory,a.id,'world'),adapter:a,provider,offlinePreview:true});owners.push(world);worlds.set(a.id,world);
  }
  const bridge=new BridgeHost({world:worlds.get('cultivation'),worldPort:cultivationWorldPort,works,records,provider:actorProviders.get('cultivation'),reference,runtimeHash:manifest.hash});
  return new Runtime({ledger,worlds,transports,worldProviders,actorProviders,bridge,owners,manifest});
 }catch(e){for(const o of owners.reverse())await o.close().catch(()=>{});throw e;}
}
class Runtime{
 constructor(ports){Object.assign(this,ports);this.busy=new Set();this.activeJobs=0;this.maxJobs=0;this.waiters=[];}
 async exclusive(run,fn){need(!this.busy.has(run),'R7_RUN_BUSY');this.busy.add(run);try{return await fn();}finally{this.busy.delete(run);}}
 // Only two registered owner tasks; not a general workflow/DAG interpreter.
 async slot(fn){if(this.activeJobs>=2)await new Promise(ok=>this.waiters.push(ok));else this.activeJobs++;
  this.maxJobs=Math.max(this.maxJobs,this.activeJobs);try{return await fn();}finally{const next=this.waiters.shift();if(next)next();else this.activeJobs--;}}
 async create({operationId,adapterId='cultivation',inventory=true}){
  need(id(operationId)&&this.worlds.has(adapterId)&&typeof inventory==='boolean','R7_CREATE');
  return this.exclusive('create',async()=>{
   const key='run-'+hash([operationId,adapterId]).slice(0,40),creationHash=hash({operationId,adapterId,inventory}),old=(await this.ledger.read()).runs[key];
   if(old){need(old.creationHash===creationHash,'R7_CREATE_COLLISION');return this.view(key);}
   const w=this.worlds.get(adapterId);let worldId,bridgeId=null;
   if(adapterId==='cultivation'){const b=await this.bridge.create({operationId,inventory});worldId=b.worldId;bridgeId=b.id;}
   else worldId=(await w.create({operationId,initial:w.adapter.initial(),confirmed:true,source:{kind:'new',id:'r7-adult-fixture',history:[]}})).id;
   await this.ledger.change(r=>{r.runs[key]={id:key,adapterId,worldId,bridgeId,creationHash};});return this.view(key);
  });
 }
 async run(key){const run=(await this.ledger.read()).runs[key];need(run,'R7_RUN_NOT_FOUND');return run;}
 async spec(run,operationId,purpose,input,actorId,fault){
  const w=await this.worlds.get(run.adapterId).store.read(run.worldId),b=run.bridgeId?await this.bridge.read(run.bridgeId):null;
  return {id:'task-'+hash([run.id,purpose,operationId]).slice(0,40),purpose,session:purpose==='actor-response'?run.bridgeId:run.worldId,runId:run.id,
   sourceHash:hash({world:w.binding,stateVersion:w.stateRevision,actor:actorId?b.actors[actorId].binding:null,input}),
   source:{worldId:run.worldId,branchId:run.worldId,stateVersion:w.stateRevision,actorId,events:w.events.map(e=>e.id)},
   ownerRef:{owner:purpose==='actor-response'?'r6-actor':'r5-world',operationId,actorId},input,fault};
 }
 async task(run,{operationId,purpose,input='',actorId=null,fault=null,strategy='same'},recover=false){
  const provider=purpose==='actor-response'?this.actorProviders.get(run.adapterId):this.worldProviders.get(run.adapterId),world=this.worlds.get(run.adapterId);
  const config=this.taskPolicyFor?this.taskPolicyFor({run,operationId,purpose,actorId}):STRATEGIES[strategy];
  const taskId='task-'+hash([run.id,purpose,operationId]).slice(0,40),prior=(await this.ledger.read()).tasks[taskId];
  if(prior&&!recover){need(prior.input===input&&prior.fault===fault&&prior.ownerRef.actorId===actorId&&hash(prior.policy)===hash(policy(config)),'R7_TASK_COLLISION');return prior;}
  const task=prior??await this.ledger.begin(await this.spec(run,operationId,purpose,input,actorId,fault),config);
  if(['completed','failed','rejected','stale'].includes(task.adoption))return task;
  return this.slot(()=>provider.using(taskId,async()=>{
   let result,status,receipt,error;
   try{
    if(purpose==='actor-response'){
     result=recover?await this.bridge.recover(run.bridgeId,operationId):await this.bridge.respond(run.bridgeId,{operationId,actorId,input});
     const op=result.tasks[operationId];status=op.status;receipt=op.receipt;error=op.error;
    }else{
     result=recover?await world.recover(run.worldId):purpose==='world-narration'?await world.narrate(run.worldId):await world.turn(run.worldId,{operationId,revision:(await world.store.read(run.worldId)).revision,input});
     const op=result.operations[operationId];status=op?.status??'unknown';receipt=op?.receipt??null;error=op?.error??null;
    }
    await this.ledger.adopted(taskId,status,receipt,error);
   }catch(e){await this.ledger.adopted(taskId,'unknown',null,e.code||'R7_OWNER_UNKNOWN');}
   return (await this.ledger.read()).tasks[taskId];
  }));
 }
 async checkReady(run){
  const r=await this.ledger.read();need(!Object.values(r.tasks).some(t=>t.runId===run.id&&['pending','running','unknown'].includes(t.adoption)),'R7_RECOVERY_REQUIRED');
 }
 async execute(key,data){
  keys(data,['operationId','revision','parallel','strategy','fault']);const {operationId,revision,parallel=false,strategy='same',fault=null}=data;
  need(id(operationId)&&typeof parallel==='boolean'&&Object.hasOwn(STRATEGIES,strategy)&&FAULTS.includes(fault),'R7_EXECUTE');
  return this.exclusive(key,async()=>{
   const run=await this.run(key),world=this.worlds.get(run.adapterId),w=await world.store.read(run.worldId);
   if(w.operations[operationId])return this.view(key);await this.checkReady(run);
   if(run.bridgeId){await this.bridge.worldAction(run.bridgeId,{operationId,revision});await this.bridge.deliver(run.bridgeId);}
   else await world.execute(run.worldId,{operationId,revision,action:{actionId:world.adapter.rule.id,itemId:world.adapter.resources(w.state)[0].id,quantity:2}});
   const after=await world.store.read(run.worldId);need(after.operations[operationId]?.receipt?.status==='executed','R7_ACTION_UNCONFIRMED');
   const jobs=[()=>this.task(run,{operationId:after.pending,purpose:'world-narration',strategy:strategy==='split'?'same':strategy,fault})];
   if(parallel&&run.bridgeId)jobs.push(()=>this.task(run,{operationId:'actor-'+operationId,purpose:'actor-response',input:'刚才发生了什么？',actorId:'witness',strategy}));
   if(parallel)await Promise.allSettled(jobs.map(fn=>fn()));else for(const fn of jobs)await fn();
   return this.view(key);
  });
 }
 async respond(key,{operationId,actorId='witness',input,strategy='same',fault=null}){
  need(id(operationId)&&['witness','outsider'].includes(actorId)&&typeof input==='string'&&input.length<=2000&&Object.hasOwn(STRATEGIES,strategy)&&FAULTS.includes(fault),'R7_RESPONSE');
  return this.exclusive(key,async()=>{const run=await this.run(key);need(run.bridgeId,'R7_ACTOR_UNAVAILABLE');const taskId='task-'+hash([run.id,'actor-response',operationId]).slice(0,40);
   if(!(await this.ledger.read()).tasks[taskId])await this.checkReady(run);
   await this.task(run,{operationId,purpose:'actor-response',input,actorId,strategy,fault});return this.view(key);});
 }
 async dialogue(key,{operationId,input}){need(id(operationId)&&['聊聊今日安排','查看登记物资'].includes(input),'R7_DIALOGUE');return this.exclusive(key,async()=>{const run=await this.run(key);await this.checkReady(run);await this.task(run,{operationId,purpose:'world-dialogue',input});return this.view(key);});}
 async recover(key,taskId){return this.exclusive(key,async()=>{const run=await this.run(key),t=(await this.ledger.read()).tasks[taskId];need(t?.runId===key,'R7_TASK_SCOPE');
  await this.task(run,{operationId:t.ownerRef.operationId,purpose:t.purpose,input:t.input,actorId:t.ownerRef.actorId,fault:t.fault},true);return this.view(key);});}
 async resumeNarration(key){return this.exclusive(key,async()=>{
  const run=await this.run(key),world=this.worlds.get(run.adapterId),r=await world.store.read(run.worldId),op=r.operations[r.pending];
  need(op?.purpose==='action-narration'&&op.status==='pending'&&op.stages.length===0,'R7_NARRATION_ALREADY_STARTED');
  need(!Object.values((await this.ledger.read()).tasks).some(t=>t.runId===key&&t.ownerRef.operationId===r.pending),'R7_RECOVERY_REQUIRED');
  await this.checkReady(run);if(run.bridgeId)await this.bridge.deliver(run.bridgeId);
  // No model policy or dispatch existed. Explicitly start only the missing narration,
  // using the displayed default; never recreate optional actor jobs or the action.
  await this.task(run,{operationId:r.pending,purpose:'world-narration',strategy:'same'});return this.view(key);
 });}
 async finishReceipt(key,{operationId,reason}){need(id(operationId)&&typeof reason==='string'&&reason.trim(),'R7_FINISH_REASON');return this.exclusive(key,async()=>{const run=await this.run(key);await this.checkReady(run);const world=this.worlds.get(run.adapterId),r=await world.store.read(run.worldId);await world.action(run.worldId,'discard',{operationId,revision:r.revision,pendingId:r.pending,reason});return this.view(key);});}
 async view(key){
  const run=await this.run(key),world=await this.worlds.get(run.adapterId).store.read(run.worldId),all=await this.ledger.read(),b=run.bridgeId?await this.bridge.view(run.bridgeId):null;
  return {...run,mode:'offline-fixture',busy:this.busy.has(key),world:{revision:world.revision,stateVersion:world.stateRevision,resources:this.worlds.get(run.adapterId).adapter.resources(world.state),events:world.events,turns:world.turns,pending:world.pending},
   canResumeNarration:!!(world.pending&&world.operations[world.pending]?.purpose==='action-narration'&&world.operations[world.pending]?.status==='pending'&&world.operations[world.pending]?.stages.length===0&&!Object.values(all.tasks).some(t=>t.runId===key&&t.ownerRef.operationId===world.pending)),
   actors:b?.actors??[],tasks:Object.values(all.tasks).filter(t=>t.runId===key).sort((a,b)=>a.sequence-b.sequence),budget:totals(all),maxConcurrentTasks:this.maxJobs,
   maxConcurrentRequests:Math.max(...[...this.transports.values()].map(t=>t.maxActive))};
 }
 async list(){const r=await this.ledger.read();return {runs:Object.values(r.runs),budget:totals(r),strategies:Object.keys(STRATEGIES)};}
 async close(){need(this.busy.size===0,'R7_BUSY');for(const o of this.owners.slice().reverse())await o.close();}
}

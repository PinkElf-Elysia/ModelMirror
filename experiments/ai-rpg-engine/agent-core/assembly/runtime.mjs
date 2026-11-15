import {join} from 'node:path';
import {clone,hash,id,need,keys} from '../contracts.mjs';
import {ActionHost} from '../actions/host.mjs';
import {TaskLedger} from '../tasks/ledger.mjs';
import {TaskExecutor} from '../tasks/executor.mjs';
import {TaskScheduling} from '../tasks/scheduling.mjs';
import {totals} from '../tasks/policy.mjs';
import {AssemblyStore} from './store.mjs';
import {compileCatalog} from './catalog.mjs';

export const FAULTS=[null,'failed','unknown','unknown-sticky','continuation-failed','truncated','write','foreign-tool'];
const unresolved=t=>['pending','running','unknown'].includes(t.adoption);

// All executable ports come from trusted host code, never HTTP configuration or exports.
export async function openAssembly(directory,{catalog,transportFactory,manifestHash,offlinePreview=false,controlledReal=false,limits}={}){
 need((offlinePreview===true)!==(controlledReal===true)&&typeof transportFactory==='function'&&/^[a-f0-9]{64}$/.test(manifestHash),'ASSEMBLY_OFFLINE_ONLY');
 for(const w of catalog.worlds)w.adapter.binding.manifestHash=manifestHash;
 const compiled=compileCatalog(catalog),closers=[];
 try{
  const ledger=await TaskLedger.open(join(directory,'tasks'),limits,{version:'task-owners/8',purposes:[...compiled.owners.keys()]});closers.push(ledger);
  await ledger.change(r=>{if(r.assemblyHash)need(r.assemblyHash===compiled.hash,'ASSEMBLY_CHANGED');else r.assemblyHash=compiled.hash;r.runs??={};});
  const ports=new Map();
  for(const [adapterId,w] of compiled.worlds){
   const transport=await transportFactory(join(directory,adapterId,'transport'),w);closers.push(transport);need(controlledReal?['mock','real'].includes(transport.mode):transport.mode==='offline','ASSEMBLY_OFFLINE_ONLY');
   const providers=new Map([...compiled.owners.keys()].map(k=>[k,new TaskExecutor(ledger,transport,{controlledReal})]));
   const store=await AssemblyStore.open({directory:join(directory,adapterId,'sessions'),adapter:w.adapter});closers.push(store);
   // Each task gets its registered provider; hosts share one authoritative store.
   const hosts=new Map([...providers].map(([k,p])=>[k,new ActionHost(store,p)]));
   ports.set(adapterId,{...w,transport,providers,hosts,store});
  }
  return new AssemblyRuntime({ledger,compiled,ports,closers,mode:controlledReal?[...ports.values()][0].transport.mode:'offline-fixture'});
 }catch(e){for(const x of closers.reverse())await x.close().catch(()=>{});throw e;}
}

export class AssemblyRuntime extends TaskScheduling{
 constructor(p){super();Object.assign(this,p);}
 async run(key){const r=(await this.ledger.read()).runs[key];need(r&&r.binding===this.compiled.hash,'ASSEMBLY_RUN');return r;}
 port(run){const p=this.ports.get(run.adapterId);need(p,'ASSEMBLY_ADAPTER');return p;}
 async ready(run){const all=await this.ledger.read();need(!Object.values(all.tasks).some(t=>t.runId===run.id&&(unresolved(t)||t.adoption==='awaiting-adoption')),'ASSEMBLY_RECOVERY_REQUIRED');}
 async create({operationId,adapterId}){
  need(id(operationId)&&this.ports.has(adapterId),'ASSEMBLY_CREATE');return this.exclusive('create',async()=>{
   const key='run-'+hash([operationId,adapterId]).slice(0,40),old=(await this.ledger.read()).runs[key];if(old)return this.view(key);
   const p=this.ports.get(adapterId),world=await p.store.create({operationId,initial:p.adapter.initial(),confirmed:true,source:{kind:'new',id:'r8-adult-example',history:[]}});
   await this.ledger.change(r=>{r.runs[key]={id:key,adapterId,worldId:world.id,binding:this.compiled.hash,checkpoints:{},source:null};});return this.view(key);
  });
 }
 taskId(run,ownerId,operationId){return 'task-'+hash([run.id,ownerId,operationId]).slice(0,40);}
 async task(run,{operationId,ownerId,input='',strategy='same',fault=null},recover=false){
  const owner=this.compiled.owners.get(ownerId),p=this.port(run),provider=p.providers.get(ownerId),host=p.hosts.get(ownerId);
  need(id(operationId)&&owner&&typeof input==='string'&&input.length<=20000&&Object.hasOwn(this.compiled.policies,strategy)&&FAULTS.includes(fault),'ASSEMBLY_TASK');
  const taskId=this.taskId(run,ownerId,operationId),all=await this.ledger.read(),old=all.tasks[taskId],world=await p.store.read(run.worldId);
  const config=this.compiled.policies[strategy];
  if(old)need(old.input===input&&old.fault===fault&&old.strategy===strategy,'R7_TASK_COLLISION');
  if(old&&!recover)return old;
  if(old&&!unresolved(old))return old;
  if(!old)await this.ready(run);
  const observation=owner.authority==='observation'?owner.project(clone(world)):null;
  const spec={id:taskId,purpose:ownerId,session:run.worldId,runId:run.id,input,fault,strategy,
   sourceHash:hash({binding:world.binding,revision:world.revision,input,owner:owner.id}),
   source:{worldId:world.id,stateVersion:world.stateRevision,recordRevision:world.revision},
   ownerRef:{owner:owner.id,operationId},observation};
  const t=old??await this.ledger.begin(spec,config);
  return this.slot(()=>provider.using(taskId,async()=>{
   try{
    // Observation consumers get only their projection and a provider, never world/store ports.
    const ctx=owner.authority==='world'?{host,world,provider,task:t,recover}:{provider,task:t,recover};
    const out=await owner.execute(ctx);need(['completed','proposed','failed','rejected','unknown','awaiting-adoption'].includes(out.status),'ASSEMBLY_OWNER_RESULT');
    await this.ledger.adopted(taskId,out.status,out.receipt??null,out.error??null);
   }catch(e){await this.ledger.adopted(taskId,e.code?.startsWith('NATIVE')?'rejected':'unknown',null,e.code||'ASSEMBLY_OWNER_UNKNOWN');}
   return (await this.ledger.read()).tasks[taskId];
  }));
 }
 async submit(key,data){return this.exclusive(key,async()=>{const r=await this.run(key);
  need(data.ownerId!==this.compiled.entrypoints.narration,'R8_NARRATE_ENTRY');
  const prior=(await this.ledger.read()).tasks[this.taskId(r,data.ownerId,data.operationId)];
  if(!prior){await this.ready(r);need(!(await this.port(r).store.read(r.worldId)).pending,'STATE_TURN_PENDING');}
  await this.task(r,data);return this.view(key);});}
 async recover(key,taskId){return this.exclusive(key,async()=>{
  const r=await this.run(key),t=(await this.ledger.read()).tasks[taskId];need(t?.runId===key,'R7_TASK_SCOPE');
  await this.task(r,{operationId:t.ownerRef.operationId,ownerId:t.purpose,input:t.input,strategy:t.strategy,fault:t.fault},true);return this.view(key);
 });}
 async adopt(key,taskId){return this.exclusive(key,async()=>{
  const r=await this.run(key),t=(await this.ledger.read()).tasks[taskId];need(t?.runId===key&&this.compiled.owners.get(t.purpose)?.adoption==='explicit','ASSEMBLY_ADOPT');
  if(t.adoption==='completed')return this.view(key);need(t.adoption==='awaiting-adoption','ASSEMBLY_ADOPT');
  const w=await this.port(r).store.read(r.worldId);
  if(w.revision!==t.source.recordRevision)await this.ledger.adopted(taskId,'stale',null,'STATE_STALE_RESULT');
  else await this.ledger.adopted(taskId,'completed',t.receipt);
  return this.view(key);
 });}
 async confirm(key,data){return this.exclusive(key,async()=>{
  keys(data,['operationId','revision','proposalId','proposalHash']);need(id(data.operationId),'STATE_ID');
  const r=await this.run(key),p=this.port(r);await this.ready(r);
  const w=await p.store.read(r.worldId);need(w.operations[data.operationId]||this.allowed(w).some(n=>p.adapter.tools.entries.find(e=>e.name===n)?.effect==='proposal'),'CAPABILITY_DISABLED');
  await p.store.execute(r.worldId,data);return this.view(key);
 });}
 async narrate(key,{strategy='same',fault=null}={}){return this.exclusive(key,async()=>{
  const r=await this.run(key),p=this.port(r),w=await p.store.read(r.worldId);need(w.operations[w.pending]?.purpose==='action-narration','ACTION_NARRATION');
  await this.task(r,{operationId:w.pending,ownerId:this.compiled.entrypoints.narration,strategy,fault});return this.view(key);
 });}
 async finish(key,{operationId,reason}){return this.exclusive(key,async()=>{
  const r=await this.run(key);await this.ready(r);const p=this.port(r),w=await p.store.read(r.worldId);
  await p.store.discard(w.id,{operationId,revision:w.revision,pendingId:w.pending,reason});return this.view(key);
 });}
 allowed(w){return w.runtimeCapabilities??this.ports.get(w.binding.adapterId).adapter.tools.entries.map(e=>e.name);}
 async capabilities(key,{operationId,revision,names}){return this.exclusive(key,async()=>{
  const r=await this.run(key);await this.ready(r);const p=this.port(r);
  need(Array.isArray(names)&&new Set(names).size===names.length&&names.every(n=>p.adapter.tools.entries.some(e=>e.name===n)),'CAPABILITY_UNKNOWN');
  await p.store.action(r.worldId,{operationId,revision,names},'capabilities',w=>{need(!w.pending,'STATE_TURN_PENDING');w.runtimeCapabilities=clone(names);});return this.view(key);
 });}
 async checkpoint(key,{operationId}){return this.exclusive(key,async()=>{
  need(id(operationId),'STATE_ID');const r=await this.run(key);await this.ready(r);
  if(r.checkpoints[operationId])return this.view(key);
  const w=await this.port(r).store.read(r.worldId);need(!w.pending,'CHECKPOINT_PENDING');
  const c={id:operationId,hash:hash(w),snapshot:clone(w)};
  await this.ledger.change(x=>{x.runs[key].checkpoints[operationId]=c;});return this.view(key);
 });}
 async fork(key,{operationId,checkpointId}){return this.exclusive(key,async()=>{
  need(id(operationId),'STATE_ID');const r=await this.run(key),c=r.checkpoints[checkpointId];need(c,'CHECKPOINT_MISSING');await this.ready(r);
  need(!(await this.port(r).store.read(r.worldId)).pending,'CHECKPOINT_PENDING');
  const childId='run-'+hash([key,operationId]).slice(0,40),old=(await this.ledger.read()).runs[childId];
  if(old){need(old.source.checkpointId===checkpointId,'STATE_OPERATION_COLLISION');return this.view(childId);}
  const w=await this.port(r).store.forkCheckpoint(c,operationId);
  await this.ledger.change(x=>{x.runs[childId]={id:childId,adapterId:r.adapterId,worldId:w.id,binding:r.binding,checkpoints:{},source:{runId:key,checkpointId}};});
  return this.view(childId);
 });}
 async view(key){
  const r=await this.run(key),p=this.port(r),w=await p.store.read(r.worldId),ledger=await this.ledger.read();
  return {id:r.id,adapterId:r.adapterId,worldId:r.worldId,source:r.source,binding:r.binding,mode:this.mode,busy:this.busy.has(key),
   world:{revision:w.revision,stateRevision:w.stateRevision,state:clone(w.state),resources:p.adapter.resources(w.state),turns:w.turns,events:w.events,pending:w.pending,proposals:w.proposals??{},pendingStatus:w.operations[w.pending]?.status??null},
   tools:p.adapter.tools.entries.map(({name,effect,version})=>({name,effect,version,enabled:this.allowed(w).includes(name)})),
   queries:Object.values(w.operations).flatMap(o=>(o.toolResults??[]).map(x=>({operationId:Object.keys(w.operations).find(k=>w.operations[k]===o),...clone(x)}))),
   tasks:Object.values(ledger.tasks).filter(t=>t.runId===key).sort((a,b)=>a.sequence-b.sequence),checkpoints:Object.values(r.checkpoints).map(c=>({id:c.id,stateRevision:c.snapshot.stateRevision,turns:c.snapshot.turns.length,hash:c.hash})),
   budget:totals(ledger),maxConcurrentTasks:this.maxJobs};
 }
 async list(){const r=await this.ledger.read();return {runs:Object.values(r.runs).map(({id,adapterId,source})=>({id,adapterId,source})),
  worlds:[...this.compiled.worlds.values()].map(w=>({id:w.adapter.id,name:w.adapter.name,category:w.category})),owners:this.compiled.descriptor.owners,policies:Object.keys(this.compiled.policies),budget:totals(r),assembly:this.compiled.descriptor};}
 async close(){need(this.busy.size===0,'R7_BUSY');for(const c of this.closers.slice().reverse())await c.close();}
}

import {AssemblyRuntime} from '../assembly/runtime.mjs';
import {clone,hash,need,keys,id} from '../contracts.mjs';
import {VERSION} from './projection.mjs';
export class SceneRuntime extends AssemblyRuntime{
 constructor(core,participants){super(core);this.participants=participants;}
 async create(data){
  const view=await super.create(data);
  return this.exclusive(view.id,async()=>{
   const run=await this.run(view.id),store=this.port(run).store,w=await store.read(run.worldId);
   if(!w.scene)await store.update(w.id,r=>{r.scene={version:VERSION,participants:Object.fromEntries(this.participants.map(p=>[p.id,{reference:clone(p.reference),configurationHash:p.configurationHash,joinedAtVersion:r.stateRevision,grant:clone(p.grant)}])),entries:[]};});
   return this.view(view.id);
  });
 }
 async submit(key,data){
  keys(data,['operationId','ownerId','input','strategy','fault']);
  return super.submit(key,data);
 }
 async adopt(key,taskId){return this.exclusive(key,async()=>{
  const r=await this.run(key),t=(await this.ledger.read()).tasks[taskId],p=this.participants.find(p=>'participant-'+p.id===t?.purpose);
  need(t?.runId===key&&p,'SCENE_ADOPT_TARGET');
  const store=this.port(r).store,w=await store.read(r.worldId),existing=w.scene.entries.find(e=>e.taskId===taskId);
  if(existing){need(existing.receiptHash===hash(t.receipt),'SCENE_RECEIPT_COLLISION');await this.ledger.adopted(taskId,'completed',t.receipt);return this.view(key);}
  need(t.adoption==='awaiting-adoption'&&t.receipt?.text,'SCENE_ADOPT_PENDING');
  if(w.revision!==t.source.recordRevision){await this.ledger.adopted(taskId,'stale',t.receipt,'STATE_STALE_RESULT');return this.view(key);}
  await store.update(w.id,row=>{
   need(!row.pending&&row.revision===t.source.recordRevision,'STATE_STALE_RESULT');
   row.scene.entries.push({taskId,adoptionId:'adopt-'+taskId,participantId:p.id,name:p.name,input:t.input,body:t.receipt.text,receiptHash:hash(t.receipt),
    source:clone(t.source),originWorldId:w.id,configurationHash:p.configurationHash,kind:'adopted-speech-not-world-fact',afterTurnCount:row.turns.length});
  });
  // World-owned adoption is committed first. Retrying adopt only completes this ledger projection.
  await this.ledger.adopted(taskId,'completed',t.receipt);
  return this.view(key);
 });}
 async dismiss(key,taskId){return this.exclusive(key,async()=>{
  const t=(await this.ledger.read()).tasks[taskId];need(t?.runId===key&&this.participants.some(p=>'participant-'+p.id===t.purpose),'SCENE_ADOPT_TARGET');
  if(t.adoption==='discarded')return this.view(key);need(t.adoption==='awaiting-adoption','SCENE_DISCARD_PENDING');
  await this.ledger.adopted(taskId,'discarded',t.receipt);return this.view(key);
 });}
 async view(key){
  const v=await super.view(key),r=await this.run(key),world=await this.port(r).store.read(r.worldId),scene=world.scene;
  if(scene){
   need(scene.version===VERSION&&Array.isArray(scene.entries),'SCENE_FORMAT');
   for(const p of this.participants)need(scene.participants[p.id]?.configurationHash===p.configurationHash,'SCENE_BINDING');
  }
  v.scene={version:VERSION,participants:this.participants.map(p=>({id:p.id,name:p.name,instanceId:'actor-'+hash([v.worldId,p.id]).slice(0,32),
   reference:clone(p.reference),scope:p.grant.witness?'可查看本场景登记与已确认行动':'仅知道与自己的已采用对话'})),entries:clone(scene?.entries??[])};
  const blocks=[];for(let i=0;i<=world.turns.length;i++){
   if(i>0)blocks.push({...clone(world.turns[i-1]),kind:'world'});
   for(const e of scene?.entries??[])if(e.afterTurnCount===i)blocks.push({...clone(e),kind:'speech'});
  }v.timeline=blocks;
  v.tasks=v.tasks.map(t=>({id:t.id,purpose:t.purpose,operationId:t.ownerRef.operationId,input:t.input,source:t.source,adoption:t.adoption,error:t.error,body:t.receipt?.text??null,
   model:t.policy.model.id,stages:t.stages.map(s=>({id:s.id,status:s.result?.status??'pending',tools:s.request.tools.map(x=>x.function.name),
    queryResults:s.request.messages.filter(m=>m.role==='tool').map(m=>({callId:m.tool_call_id,result:JSON.parse(m.content)})),attempts:s.attempts.length}))}));
  return v;
 }
}

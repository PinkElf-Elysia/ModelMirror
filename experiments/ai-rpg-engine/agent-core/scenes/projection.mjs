import {clone,hash,need,keys} from '../contracts.mjs';
export const VERSION='scene-participants/9';
export const QUERY={type:'function',function:{name:'query_scene_state',description:'读取本参与者获准知道的场景事件或登记资源。没有执行权限；未知信息不推测。',parameters:{type:'object',additionalProperties:false,required:['domain'],properties:{domain:{type:'string',enum:['events','resources']}}}}};
export function frame(world,participant,adapter){
 const s=world.scene;need(s?.version===VERSION&&s.participants[participant.id]?.configurationHash===participant.configurationHash,'SCENE_BINDING');
 const binding=s.participants[participant.id];
 const events=binding.grant.witness?world.events.filter(e=>e.kind==='action'&&e.stateRevision>binding.joinedAtVersion).map(e=>({
  id:e.id,kind:'confirmed-action',stateVersion:e.stateRevision,description:e.receipt.result.label+' '+e.receipt.result.name+' × '+e.receipt.result.quantity
 })):[];
 return {version:VERSION,worldId:world.id,recordRevision:world.revision,stateRevision:world.stateRevision,participantId:participant.id,
  configurationHash:participant.configurationHash,grant:clone(binding.grant),
  facts:binding.grant.scene?clone(adapter.sceneFacts(world.state)):[],
  events,resources:binding.grant.resources?clone(adapter.resources(world.state)):[],
  coverage:{events:binding.grant.witness?'since-join-only':'not-authorized',resources:binding.grant.resources?'registered-action-resources-only':'not-authorized'},
  dialogue:s.entries.filter(e=>e.participantId===participant.id).map(e=>({taskId:e.taskId,input:e.input,body:e.body,kind:'adopted-speech'}))};
}
export function queryFrame(f,args){
 keys(args,['domain']);need(['events','resources'].includes(args.domain),'NATIVE_SCENE_QUERY');
 const allowed=f.coverage[args.domain]!=='not-authorized';
 return {source:'host-projection',stateRevision:f.stateRevision,frameHash:hash(f),status:allowed?'available':'unavailable',
  entries:allowed?clone(f[args.domain].slice(0,50)):[],coverage:allowed&&f[args.domain].length>50?'partial':f.coverage[args.domain],
  note:'登记范围不是全部世界事实；人物发言不自动证明其内容。'};
}
export function speechMaterial(world){
 return {kind:'adopted-speech-not-world-fact',entries:(world.scene?.entries??[]).slice(-12).map(e=>({
  participantId:e.participantId,name:e.name,input:e.input,body:e.body,taskId:e.taskId,originWorldId:e.originWorldId
 }))};
}

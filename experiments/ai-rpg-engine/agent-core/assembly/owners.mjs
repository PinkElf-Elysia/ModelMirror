import {clone,need,hash} from '../contracts.mjs';
import {parseResult} from '../actions/protocol.mjs';
export function worldOwner(purpose,method){
 return {id:purpose,version:'1',authority:'world',adoption:'host',
  async execute({host,world,task,recover}){
   const result=recover?await host.recover(world.id):method==='narrate'?await host.narrate(world.id):
    await host.turn(world.id,{operationId:task.ownerRef.operationId,revision:task.source.recordRevision,input:task.input});
   const op=result.operations[task.ownerRef.operationId];return {status:op?.status??'unknown',receipt:op?.receipt??null,error:['completed','proposed'].includes(op?.status)?null:op?.error??null};
  }};
}
// A test consumer, not a character instance or shared memory.
export function observationOwner(id='observation-note'){
 return {id,version:'1',authority:'observation',adoption:'explicit',
  project(world){return {session:world.id,stateRevision:world.stateRevision,events:world.events.map(e=>({id:e.id,kind:e.kind})),prose:world.turns.slice(-2).map(t=>({eventId:t.eventId,body:t.body}))};},
  async execute({provider,task,recover}){
   const stageId='note-'+hash(task.id).slice(0,32),request={messages:[
    {role:'system',content:'根据可见记录生成简短观察。未提供的信息回答未知，不调用工具、不改变状态。'},
    {role:'user',content:JSON.stringify({input:task.input,observation:task.observation})}],tools:[],tool_choice:'none'};
   const raw=recover?await provider.lookup(task.session,stageId):await provider.generate(task.session,stageId,request,{purpose:'observation',input:task.input});
   if(!raw||raw.status!=='completed')return {status:raw?.status??'unknown',receipt:null,error:raw?.error??'RESULT_UNKNOWN'};
   const parsed=parseResult(raw,[]);need(parsed.kind==='final','OBSERVATION_FINAL');
   return {status:'awaiting-adoption',receipt:clone(parsed.receipt),error:null};
  }};
}

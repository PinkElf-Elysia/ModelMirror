import {clone,need,keys,id} from '../contracts.mjs';

// Only this trusted projector receives the world owner's raw record.
// Neither the card assembler nor the actor tool executor receives it.
export function project(world,actor){
 const grant=actor.binding.grant;
 need(actor.binding.worldId===world.id,'ACTOR_WORLD_MISMATCH');
 const events=grant.witness?world.events.filter(e=>e.kind==='action'&&e.stateRevision>actor.binding.joinedAtVersion).map(e=>({
  id:e.id,source:'confirmed-world-event',stateVersion:e.stateRevision,
  action:e.receipt.result.actionId,item:{id:e.receipt.result.itemId,name:e.receipt.result.name},
  quantityUsed:e.receipt.result.quantity,
  description:'玩家'+e.receipt.result.label+e.receipt.result.quantity+'份'+e.receipt.result.name
 })):[];
 // Remaining quantities are an independent grant, never inferred from observation.
 const inventory=grant.inventory?world.state.items.filter(x=>x.kind==='丹药').map(x=>({
  id:x.id,name:x.name,kind:x.kind,quantity:x.quantity
 })):[];
 return {worldId:world.id,stateVersion:world.stateRevision,grantVersion:actor.binding.grant.version,
  events,inventory,coverage:{events:grant.witness?'since-join-only':'not-authorized',
   inventory:grant.inventory?'registered-medicine-only':'not-authorized'}};
}
export const QUERY_TOOL={type:'function',function:{name:'query_local_state',
 description:'只读取当前角色已获授权的观察或丹药登记；不能访问其他人物、世界全量状态或执行行动。',
 parameters:{type:'object',additionalProperties:false,required:['domain'],properties:{
  domain:{type:'string',enum:['observations','inventory']},id:{type:'string',maxLength:80},
  name:{type:'string',maxLength:100}}}}};
export function query(frame,args){
 keys(args,['domain','id','name']);
 need(['observations','inventory'].includes(args.domain),'ACTOR_QUERY_DOMAIN');
 need(args.id===undefined||id(args.id),'ACTOR_QUERY_ID');
 need(args.name===undefined||typeof args.name==='string'&&args.name.length<=100,'ACTOR_QUERY_NAME');
 const domain=args.domain==='observations'?'events':'inventory',allowed=frame.coverage[domain]!=='not-authorized';
 // Deliberately identical for absent and inaccessible identifiers.
 if(!allowed)return {stateVersion:frame.stateVersion,status:'unavailable',items:[],coverage:'not-authorized'};
 const all=frame[domain].filter(x=>(args.id===undefined||x.id===args.id)&&(args.name===undefined||(x.name??x.item?.name??'').includes(args.name)));
 return {stateVersion:frame.stateVersion,status:all.length?'matched':'no-match',items:clone(all.slice(0,50)),
  coverage:all.length>50?'partial':frame.coverage[domain],
  note:'只表示授权登记范围；未登记事实及未建模容器关系未知。'};
}

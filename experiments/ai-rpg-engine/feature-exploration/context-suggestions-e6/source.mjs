import {openPreview} from '../../agent-core/assembly/bootstrap.mjs';
import {exampleCatalog} from '../../agent-core/assembly/catalog.mjs';
import {clone,hash,need} from '../../agent-core/contracts.mjs';
import {empty,apply,save,exportPlan} from '../context-plans-e5/plan.mjs';
export function project(v){
 need(v.adapterId==='cultivation','E6_ADAPTER');
 return {source:{runId:v.id,worldId:v.worldId,binding:v.binding,revision:v.world.revision,stateRevision:v.world.stateRevision},
 ready:!v.busy&&!v.world.pending,
 facts:{resources:v.world.resources.map(x=>({id:x.id,name:x.name,quantity:x.quantity??null})),
 people:v.world.state.relations.filter(x=>x.id==='zhou').map(x=>({id:x.id,name:x.name}))},
 events:v.world.events.slice(-3).map(x=>({id:x.id,kind:x.kind,meaning:'已登记事件；未提供的细节未知'})),
 narrative:v.world.turns.slice(-2).map(x=>({eventId:x.eventId??null,text:String(x.body??'').slice(0,600),kind:'public-narrative'})),
 capabilities:v.tools.filter(x=>x.enabled).map(({name,effect,version})=>({name,effect,version})),
 coverage:'只提供本场景登记物资与周禾姓名；登记不代表在场、可联系或同意。公开正文不是状态事实。'};
}
export const stamp=f=>hash(f.source);
export async function openSource(directory){
 const catalog=await exampleCatalog(),runtime=await openPreview(directory,{offlinePreview:true,catalog,delayMs:0});
 try{
 const run=await runtime.create({operationId:'e6-neutral-start',adapterId:'cultivation'});
 const adapter=catalog.worlds.find(x=>x.adapter.id==='cultivation').adapter;
 return {reader:Object.freeze({read:async()=>project(await runtime.view(run.id))}),
 async audit(){const v=await runtime.view(run.id);return {worldHash:hash(v.world),taskCount:v.tasks.length,budget:clone(v.budget)};},
 async selectedPlan(s,expected){
 const v=await runtime.view(run.id),f=project(v);need(stamp(f)===expected&&f.ready,'E6_STALE');
 need(s.kind==='gift'&&s.origin==='player-selection','E6_SELECTION');
 const frame={adapterId:'cultivation',reference:f.source,ready:f.ready,resources:f.facts.resources,people:f.facts.people,
 capabilities:[{id:'gift',kind:'intent-only'}],record:{id:v.worldId,stateRevision:v.world.stateRevision,state:clone(v.world.state)}};
 const changes=[{field:'target',value:s.targetId,quote:s.text},{field:'item',value:s.itemId,quote:s.text},{field:'quantity',value:s.quantity,quote:s.text}];
 const p=apply(adapter,frame,empty(frame),{ops:[{type:'add',action:'gift',changes,condition:null,quote:s.text}],question:null,limit:null},
 {text:s.text,turnId:s.id,kind:'user-edit'});
 return exportPlan(adapter,frame,save(adapter,frame,p));
 },close:()=>runtime.close()};
 }catch(e){await runtime.close();throw e;}
}

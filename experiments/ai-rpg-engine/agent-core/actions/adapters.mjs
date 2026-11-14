import {cultivationContextAdapter,lighthouseContextAdapter} from '../context/adapters.mjs';
import {assemblePrepared,material} from '../context/assemble.mjs';
import {resourceRule} from './rules.mjs';
import {registry} from './registry.mjs';
import {clone,hash,need} from '../contracts.mjs';
const PLAN='【R5 行动职责】普通交流可直接生成正文和一个空 state_delta，未接管的定性变化沿用允许协议。受管资源数量只能由宿主执行动作，不能用增量改写或新增同名物品抵消消耗。需要行动时调用 prepare_action，返回的是预计影响，不代表执行；必要时先 query_local_state。缺信息先澄清，不猜对象、数量或人物同意。工具阶段不输出最终剧情；不要输出推理过程或完整面板。';
const NARRATE='【R5 已执行结果】以下行动回执已由宿主提交。只叙述该事件及人物反应，不调用工具、不输出 state_delta、面板或协议，不再次消耗物资。不要虚构药效数值、过去事件、人物同意或登记外的精确数量。';
export async function actionAdapters(){
 const bases=await Promise.all([cultivationContextAdapter(),lighthouseContextAdapter()]);
 return bases.map(base=>{
  const cultivation=base.id==='cultivation',entries=s=>cultivation?s.items:s.supplies;
  const accept=e=>cultivation?e.kind==='丹药':e.id==='lamp-oil';
  const rule=resourceRule({id:cultivation?'use_medicine':'fuel_lamp',label:cultivation?'服用':'添灯油',entries,accept,path:id=>(cultivation?'items.':'supplies.')+id+'.quantity',validateState:base.validateState});
  const a={...base,binding:{...base.binding,adapterVersion:'5',rulesVersion:base.binding.rulesVersion+'+actions/5'},rule,
   resources:s=>entries(s).filter(accept),owned:s=>entries(s).filter(accept).map(e=>[e.id,e.quantity]),
   name:base.name.replace('R4','R5'),scene:cultivation?'scene:qingyun':'scene:lighthouse'};
  a.ownership={...base.ownership,actionResources:'host action only; typed proposal or explicit user override'};
  a.tools=registry(a,rule);a.binding.effectiveContextHash=hash({prior:base.binding.effectiveContextHash,PLAN,NARRATE,tools:a.tools.hash});
  a.candidate=(state,receipt,policy)=>{const p=base.candidate(state,receipt,policy);const paths=new Set(a.resources(state).map(e=>(cultivation?'items.':'supplies.')+e.id+'.quantity'));need(!p.changes.some(c=>paths.has(c.path))&&hash(a.owned(state))===hash(a.owned(p.state)),'ACTION_QUANTITY_OWNED');return p;};
  a.assembleTask=async(r,input,purpose='plan',receipt=null)=>{
   const p=await base.prepare(r,input,{selection:{mode:'scoped',refs:[a.scene]}});
   const audit=[];
   p.materials=p.materials.filter(m=>{
    if(['generation','query-protocol'].includes(m.id)||(purpose==='narration'&&m.id==='delta-contract')){audit.push({id:m.id,change:'replace-output-contract',beforeHash:hash(m.text)});return false;}return true;
   }).map(m=>{
    const next=clone(m);if(['plugins','state-events'].includes(m.id))delete next.placement;
    if(m.id==='rules'&&!cultivation)next.text='你是灯塔值守的叙事者；守灯人为成年人。灯油仅由宿主行动维护，duty 为已接受的安排，转述或猜测不能冒充完成。';
    if(m.id==='delta-contract')next.text=m.text.split('\n').filter(l=>!l.startsWith('quantity {')).join('\n').replace('数量使用 quantity/balance 的整数 delta；','数量不得输出 quantity；');
    next.text=next.text.replace('宿主字段只由增量提交维护','受管资源数量由行动提交维护，其余字段沿既有增量规则');
    if(next.text!==m.text){audit.push({id:m.id,change:'r5-responsibility',beforeHash:hash(m.text),afterHash:hash(next.text)});next.source={...next.source,r5Transformation:true};}
    return next;
   });
   p.replacePrefix=p.messages[0].content;
   p.materials.push(material(p.scope,'r5-contract','task-contract','instruction',purpose==='narration'?NARRATE:PLAN,{kind:'action-adapter/5'},{required:true}));
   if(receipt)p.materials.push(material(p.scope,'execution-receipt','event','confirmed',JSON.stringify(receipt),{kind:'host-event',id:receipt.eventId},{required:true}));
   const missing=r.events.filter(e=>e.kind==='action'&&!r.turns.some(t=>t.eventId===e.id)).slice(-3).map(e=>e.receipt);
   if(purpose==='plan'&&missing.length)p.materials.push(material(p.scope,'unnarrated-actions','event','confirmed',JSON.stringify(missing),{kind:'host-action-receipts'},{required:true}));
   const out=assemblePrepared(p),tools=purpose==='narration'?[]:a.tools.tools();
   return {...out,tools,tool_choice:tools.length?'auto':'none',stateRevision:r.stateRevision,statePolicy:{},purpose,
    adaptation:audit,binding:clone(a.binding),hash:hash({messages:out.messages,tools,purpose})};
  };
  a.assemble=(r,input)=>a.assembleTask(r,input);return a;
 });
}

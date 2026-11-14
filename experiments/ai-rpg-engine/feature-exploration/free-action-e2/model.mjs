import {need,hash} from '../../agent-core/contracts.mjs';
import {intentIssues} from './intent.mjs';
import {sourceRef} from '../free-action/proposal.mjs';
export function validateProposal(world,request,raw,{id,revision,quantityOverride=null}){
 need(typeof raw==='string'&&raw.length<=16000&&!raw.includes('<state_delta>'),'PROPOSAL_OUTPUT');
 let v;try{v=JSON.parse(raw);}catch{need(false,'PROPOSAL_JSON');}
 const keys=['goal','steps','refs','requirements','questions','limitations'];
 need(v&&Object.keys(v).sort().join()===keys.sort().join(),'PROPOSAL_FIELDS');
 const words=x=>typeof x==='string'&&x.trim()&&x.length<=1000&&!/已经|已完成|已扣|已转交|对方已同意|执行成功/.test(x);
 need(words(v.goal)&&['steps','questions','limitations'].every(k=>Array.isArray(v[k])&&v[k].length<=8&&v[k].every(words)),'PROPOSAL_TEXT');
 need(Array.isArray(v.refs)&&v.refs.length<=8&&Array.isArray(v.requirements)&&v.requirements.length<=8,'PROPOSAL_LIST');
 const seen=new Set(),diagnostics=[],expected=[],basis=[];
 for(const r of v.refs){
  need(r&&Object.keys(r).sort().join()==='domain,id'&&['items','abilities','relations'].includes(r.domain),'PROPOSAL_REFERENCE');
  const e=world.state[r.domain].find(x=>x.id===r.id);need(e,'PROPOSAL_UNKNOWN_ID');
  const key=r.domain+':'+r.id;need(!seen.has(key),'PROPOSAL_DUPLICATE_ID');seen.add(key);
  basis.push({id:key,name:e.name,nature:'registered-state',stateRevision:world.version});
 }
 if(!v.questions.length)need(request.input.selection.every(x=>seen.has(x)),'PROPOSAL_SELECTION_IGNORED');
 const used=new Set();
 for(const r of v.requirements){
  need(r&&Object.keys(r).sort().join()==='itemId,quantity'&&Number.isSafeInteger(r.quantity)&&r.quantity>0,'PROPOSAL_QUANTITY');
  need(!used.has(r.itemId)&&seen.has('items:'+r.itemId),'PROPOSAL_REQUIREMENT');used.add(r.itemId);
  const item=world.state.items.find(x=>x.id===r.itemId);
  if(item.quantity===null)diagnostics.push(item.name+'数量未设定，需确认');
  else if(r.quantity>item.quantity)diagnostics.push(item.name+'不足，当前 '+item.quantity+'，拟使用 '+r.quantity);
  else expected.push({id:item.id,name:item.name,before:item.quantity,allocated:r.quantity,after:item.quantity-r.quantity});
 }
 const intentDiagnostics=intentIssues(world,request.input,v,quantityOverride);
 if(intentDiagnostics.length){
  // Preserve raw output in the receipt; withhold the ungrounded action as a whole.
  v={...v,goal:'行动信息待澄清',steps:[],refs:[],requirements:[],questions:[...new Set([...v.questions,...intentDiagnostics])]};
  expected.length=0;basis.length=0;diagnostics.length=0;
 }
 const p={format:'modelmirror.rpg.e2-proposal/1',id,revision,source:sourceRef(world),input:request.input,
  generator:'offline-simulated',goal:v.goal,steps:v.steps,refs:v.refs,requirements:v.requirements,
  questions:v.questions,limitations:[...v.limitations,'仅条件预估，实际状态未变；人物是否接受、行动是否获准均未裁定。'],
  intentDiagnostics,validationVersion:'e2-intent/2',status:diagnostics.length?'blocked':v.questions.length?'clarification':'review',diagnostics,basis,expected,
  requestHash:request.contextTrace.messagesHash,confirmation:null};
 need(p.status!=='review'||p.steps.length>0,'PROPOSAL_EMPTY');
 p.hash=hash(p);return p;
}
// A provider-shaped fixture, not language understanding. Unknown text never falls through to a guessed plan.
export class MockProposalProvider{
 calls=0;
 async generate(request){
  this.calls++;
  const text=request.input.text.trim(),v={goal:text,steps:[],refs:[],requirements:[],questions:[],limitations:[]};
  const state={queries:request.queries};
  const found=(domain,name)=>state.queries.flatMap(q=>q.results).find(r=>r.query.domain===domain)?.entries.find(e=>e.name===name)?.id;
  const med=found('items','养息丹'),person=found('relations','周禾');
  if(text==='想拿两份养息丹给周禾，先拟方案'){
   v.goal='拟将两份养息丹赠予周禾';v.steps=['拟先确认周禾是否在场及愿意接受，再决定是否交付。'];
   if(med&&person){v.refs=[{domain:'items',id:med},{domain:'relations',id:person}];v.requirements=[{itemId:med,quantity:2}];}
   else v.questions=['物资或人物没有匹配的登记，请先核对对象。'];
   v.limitations=['未发出赠送，也未取得对方同意。'];
  }else if(text==='给他一些丹药'){
   v.questions=['“他”指谁？准备使用哪一种丹药、多少份？'];v.goal='澄清拟赠送对象与数量';
  }else if(text==='先帮周禾扶稳竹架，再谈今日杂务，暂不服丹'){
   v.goal='拟帮忙后商谈杂务';v.steps=['拟先询问周禾，确认竹架当前情况及是否需要帮忙。','若条件允许，拟扶稳竹架，再谈今日杂务。'];
   if(person)v.refs=[{domain:'relations',id:person}];else v.questions=['没有找到周禾的登记，请核对人物。'];
   v.limitations=['竹架仅来自历史情境，不是库存条目；不预判关系或信任变化。','不使用丹药。'];
  }else{
   v.questions=['当前离线模拟器只支持页面列出的固定样例；这段自由输入需后续真实模型验证。'];
   v.limitations=['不保证未登记能力、永久效果或任意成功率。'];
  }
  return {status:'completed',raw:JSON.stringify(v),provider:'offline-simulated',usage:null,realCalls:0};
 }
}

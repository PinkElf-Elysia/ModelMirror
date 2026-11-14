import {hash,clone,need,bounded} from '../../agent-core/contracts.mjs';
import {sourceRef} from '../free-action/proposal.mjs';
export const FIELDS=['target','item','quantity','condition'];
export const LABELS={target:'赠送对象',item:'丹药种类',quantity:'拟赠数量',condition:'玩家条件'};
export function emptyIntent(){return Object.fromEntries(FIELDS.map(k=>[k,null]));}
function entry(w,field,value){return (field==='target'?w.state.relations:w.state.items).find(e=>e.id===value);}
export function describe(w,k,slot){if(!slot)return '未明确';return ['target','item'].includes(k)?entry(w,k,slot.value)?.name??'引用已失效':String(slot.value);}
export function derive(w,intent,extra=[]){
 const questions=['target','item','quantity'].filter(k=>!intent[k]).map(k=>({id:'need-'+k,field:k,text:{target:'你打算送给谁？',item:'你打算赠送哪一种丹药？',quantity:'你打算赠送多少份？'}[k]}));
 const diagnostics=[],expected=[];let steps=[];
 for(const k of ['target','item'])if(intent[k]&&!entry(w,k,intent[k].value))diagnostics.push(LABELS[k]+'已不在登记范围内，请重新选择。');
 if(!questions.length&&!diagnostics.length){
  const item=entry(w,'item',intent.item.value),q=intent.quantity.value;
  if(item.quantity===null)diagnostics.push('库存数量未设定，不能计算条件预估。');
  else if(item.quantity<q)diagnostics.push('库存不足；保留拟用量，请自行修订。');
  else expected.push({itemId:item.id,before:item.quantity,requested:q,conditionalAfter:item.quantity-q});
  steps=['拟先核实对象是否在场、是否愿意接受。','若条件成立，再考虑交付 '+q+' 份'+item.name+'；本页不会交付。'];
 }
 return {questions:[...questions,...extra.map((text,i)=>({id:'interpretation-'+i,field:null,text}))],diagnostics,expected,steps,
  worldConditions:[{id:'presence',status:'unknown',text:'赠送对象是否在场尚未核实。'},{id:'consent',status:'unknown',text:'对方是否愿意接受尚未核实。'}],
  status:diagnostics.length?'blocked':questions.length||extra.length?'clarification':'review'};
}
function validateValue(w,k,value){
 need(FIELDS.includes(k),'INTENT_FIELD');
 if(value===null)return;
 if(k==='target'||k==='item'){const e=entry(w,k,value);need(e,'INTENT_UNKNOWN_ID');if(k==='item')need(e.kind==='丹药','INTENT_ITEM_KIND');}
 if(k==='quantity')need(Number.isSafeInteger(value)&&value>0,'INTENT_QUANTITY');
 if(k==='condition')need(typeof value==='string'&&value.trim()&&value.length<=500,'INTENT_CONDITION');
}
export function applyChanges(w,previous,changes,{text='',turnId,kind='model-interpretation',questions=[],unsupported=false}){
 bounded(changes,{bytes:8000,depth:5});need(Array.isArray(changes)&&changes.length<=4,'INTENT_CHANGES');
 const intent=clone(previous?.intent??emptyIntent()),diff=[],seen=new Set();
 for(const change of changes){
  need(change&&Object.keys(change).sort().join()==='field,quote,value','INTENT_CHANGE_FIELDS');
  const {field:k,value,quote}=change;need(!seen.has(k),'INTENT_DUPLICATE');seen.add(k);validateValue(w,k,value);
  need(typeof quote==='string'&&quote.length<=2000,'INTENT_QUOTE');
  if(kind==='model-interpretation'){
   need(quote.trim()&&text.includes(quote),'INTENT_EVIDENCE');
   if(value!==null&&['target','item'].includes(k)){
    const e=entry(w,k,value);
    // A lone registered entity is not player intent. Pronouns may retain an already bound entity only.
    need(quote.includes(e.name)||intent[k]?.value===value,'INTENT_REFERENCE_NOT_EXPLICIT');
   }
   if(k==='quantity'&&value!==null){
    const m=quote.match(/^(\d+|一|二|两|三|四|五|六|七|八|九|十)\s*(?:份|颗|粒|枚|个)?$/);
    const n=m?Number(m[1])||({一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,十:10}[m[1]]):null;
    need(n===value,'INTENT_QUANTITY_NOT_EXPLICIT');
   }
   if(k==='condition'&&value!==null)need(text.includes(value),'INTENT_CONDITION_EVIDENCE');
  }
  const before=describe(w,k,intent[k]);
  intent[k]=value===null?null:{value,status:kind==='model-interpretation'?'candidate':'explicit',source:{turnId,kind,quote}};
  diff.push({field:k,before,after:describe(w,k,intent[k])});
 }
 const derived=derive(w,intent,questions);
 if(unsupported){derived.status='unsupported';derived.steps=[];derived.expected=[];}
 return {intent,...derived,diff,unsupported,confirmation:null};
}
export function proposal(w,previous,changes,options){
 const p={format:'modelmirror.rpg.e3-proposal/1',id:previous?.id??options.id,revision:(previous?.revision??0)+1,
  source:sourceRef(w),...applyChanges(w,previous,changes,options)};
 p.hash=hash(p);return p;
}
export function accept(p,w){
 need(p.status==='review','PROPOSAL_NOT_READY');need(hash(p.source)===hash(sourceRef(w)),'SOURCE_STALE');
 return {...clone(p),status:'accepted',confirmation:{proposalHash:p.hash,revision:p.revision,at:new Date().toISOString(),meaning:'accept-intent-only'}};
}
export function exportProposal(p,w){
 need(p?.status==='accepted'&&p.confirmation?.proposalHash===p.hash&&p.confirmation.revision===p.revision,'PROPOSAL_NOT_ACCEPTED');
 need(hash(p.source)===hash(sourceRef(w)),'SOURCE_STALE');
 const original={...clone(p),status:'review',confirmation:null};delete original.hash;need(hash(original)===p.hash,'PROPOSAL_HASH');
 return {format:'modelmirror.rpg.e3-handoff/1',executionStatus:'not_executed',requiresExecutionValidation:true,
 proposal:clone(p),notice:'接受意图不是执行授权；世界条件未核实；不是 R5 类型化动作或状态补丁。'};
}

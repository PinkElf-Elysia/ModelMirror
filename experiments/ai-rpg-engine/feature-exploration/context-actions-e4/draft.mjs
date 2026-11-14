import {clone,hash,need,keys,bounded} from '../../agent-core/contracts.mjs';
import {prepare,verifyProposal} from '../../agent-core/actions/rules.mjs';
import {applyChanges as giftChanges} from '../free-action-e3/intent.mjs';
import {giftWorld} from './source.mjs';
export const FORMAT='modelmirror.rpg.e4-draft/1';
const equal=(a,b)=>hash(a)===hash(b);
export function derive(adapter,frame,draft){
 const out={...clone(draft),confirmation:null,preflight:null,questions:[],diagnostics:[],worldConditions:[],giftEstimate:[]};
 const cap=frame.capabilities.find(c=>c.id===draft.action&&c.kind!=='read');
 if(!equal(draft.source,frame.reference)){out.status='stale';out.diagnostics=['来源已变化，草稿仍保留；请重新读取情境后再预检。'];return out;}
 if(!frame.ready){out.status='blocked';out.diagnostics=['来源停用或有待处理回合；目前仅可保留意图。'];return out;}
 if(draft.unsupported){out.status='unsupported';out.questions=draft.modelQuestions;return out;}
 if(!cap){out.status='clarification';out.questions=['你打算做哪一类行动？',...draft.modelQuestions];return out;}
 if(draft.action==='gift'){
  try{
   const derived=giftChanges(giftWorld(frame),{intent:draft.intent},[],{turnId:'derive',kind:'user-edit',questions:draft.modelQuestions});
   out.questions=derived.questions.map(q=>q.text);out.diagnostics=derived.diagnostics;out.worldConditions=derived.worldConditions;
   out.giftEstimate=derived.expected;out.status=derived.status==='review'?'intent-only':derived.status;
  }catch(e){out.status='blocked';out.diagnostics=[e.code||e.message];}
  return out;
 }
 const missing=['item','quantity'].filter(k=>!draft.intent[k]);
 out.questions=[...missing.map(k=>k==='item'?'你打算使用哪一种登记物资？':'你打算使用多少份？'),...draft.modelQuestions];
 if(out.questions.length){out.status='clarification';return out;}
 try{
  const p=prepare(frame.record,adapter.rule,{actionId:draft.action,itemId:draft.intent.item.value,quantity:draft.intent.quantity.value});
  out.preflight=p;out.status='rule-ready';
 }catch(e){out.status='blocked';out.diagnostics=[e.code||e.message];}
 return out;
}
export function revise(adapter,frame,previous,{action=null,changes=[],questions=[],unsupported=false,text='',kind='model-interpretation',turnId}){
 bounded({action,changes,questions,unsupported,text},{bytes:16000,depth:8});
 need(action===null||frame.capabilities.some(c=>c.id===action&&c.kind!=='read'),'E4_ACTION_UNSUPPORTED');
 need(typeof unsupported==='boolean'&&Array.isArray(questions)&&questions.length<=4&&questions.every(q=>typeof q==='string'&&q.trim()&&q.length<=300),'E4_QUESTIONS');
 const switched=action!==null&&action!==previous?.action;
 const next={format:FORMAT,revision:(previous?.revision??0)+1,source:clone(previous?.source??frame.reference),
  action:action??previous?.action??null,intent:switched?{}:clone(previous?.intent??{}),modelQuestions:questions,unsupported,
  actionSource:switched?{kind,quote:text,turnId}:clone(previous?.actionSource??null),confirmation:null};
 if(next.action==='gift'){
  const applied=giftChanges(giftWorld(frame),{intent:next.intent},changes,{turnId,kind,text,questions,unsupported});
  next.intent=applied.intent;
 }else{
  need(Array.isArray(changes)&&changes.length<=2,'E4_CHANGES');
  const seen=new Set();
  for(const c of changes){
   need(c&&Object.keys(c).sort().join()==='field,quote,value'&&['item','quantity'].includes(c.field)&&!seen.has(c.field),'E4_FIELD');seen.add(c.field);
   need(typeof c.quote==='string'&&c.quote.length<=2000,'E4_QUOTE');
   if(c.value!==null){
    if(c.field==='item')need(frame.resources.some(e=>e.id===c.value),'E4_ITEM');
    else need(Number.isSafeInteger(c.value)&&c.value>0,'E4_QUANTITY');
   }
   if(kind==='model-interpretation'){
    need(c.quote.trim()&&text.includes(c.quote),'E4_EVIDENCE');
    if(c.field==='item'&&c.value!==null){const item=frame.resources.find(e=>e.id===c.value);need(c.quote.includes(item.name)||next.intent.item?.value===c.value,'E4_REFERENCE');}
    if(c.field==='quantity'&&c.value!==null){
     const m=c.quote.match(/^(\d+|一|二|两|三|四|五|六|七|八|九|十)\s*(?:份|颗|粒|枚|个)?$/);
     const n=m?(Number(m[1])||({一:1,二:2,两:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,十:10}[m[1]])):null;
     need(n===c.value,'E4_QUANTITY_EVIDENCE');
    }
   }
   next.intent[c.field]=c.value===null?null:{value:c.value,status:kind==='model-interpretation'?'candidate':'explicit',source:{turnId,kind,quote:c.quote}};
  }
 }
 const before=previous?.intent??{};
 next.diff=[...(switched?[{field:'action',before:previous?.action??null,after:next.action}]:[]),
 ...new Set([...Object.keys(before),...Object.keys(next.intent)])].flatMap(x=>{
  if(typeof x==='object')return [x];
  return equal(before[x]??null,next.intent[x]??null)?[]:[{field:x,before:before[x]?.value??null,after:next.intent[x]?.value??null}];
 });
 return derive(adapter,frame,next);
}
export function refresh(adapter,frame,draft){need(draft,'E4_DRAFT_MISSING');return derive(adapter,frame,{...clone(draft),source:frame.reference,revision:draft.revision+1,diff:[{field:'source',before:draft.source.stateRevision,after:frame.reference.stateRevision}]});}
export function accept(adapter,frame,draft){
 need(draft&&['rule-ready','intent-only'].includes(draft.status),'E4_NOT_READY');
 need(equal(draft.source,frame.reference)&&frame.ready,'E4_SOURCE_STALE');
 const checked=derive(adapter,frame,draft);
 need(checked.status===draft.status&&equal(checked.preflight,draft.preflight),'E4_PREFLIGHT_CHANGED');
 const stamp=hash(draft);return {...clone(draft),status:'accepted',confirmation:{meaning:'accept-intent-only',hash:stamp,revision:draft.revision,priorStatus:draft.status}};
}
export function handoff(adapter,frame,draft){
 need(draft?.status==='accepted'&&draft.confirmation?.revision===draft.revision,'E4_NOT_ACCEPTED');
 const original={...clone(draft),status:draft.confirmation.priorStatus,confirmation:null};
 need(hash(original)===draft.confirmation.hash,'E4_DRAFT_HASH');
 need(equal(draft.source,frame.reference)&&frame.ready,'E4_SOURCE_STALE');
 if(draft.preflight)verifyProposal(frame.record,adapter.rule,draft.preflight);
 return {format:'modelmirror.rpg.e4-handoff/1',executionStatus:'not_executed',requiresExecutionValidation:true,
  intent:clone(draft),capability:draft.preflight?'rule-supported':'intent-only',
  notice:'仅接受意图。规则提案没有登记或执行；主线须重读当前世界、复验语义与授权并单独确认。玩家条件不是世界事实。'};
}

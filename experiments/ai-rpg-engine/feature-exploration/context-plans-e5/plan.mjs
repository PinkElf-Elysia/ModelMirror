import {clone,hash,need,keys,bounded} from '../../agent-core/contracts.mjs';
import {revise,derive} from '../context-actions-e4/draft.mjs';
export const FORMAT='modelmirror.rpg.e5-plan/1';
const value=(s,k)=>s.atomic.intent[k]?.value??null;
export function empty(frame){return {format:FORMAT,revision:0,nextId:1,source:clone(frame.reference),steps:[],question:null,limit:null,orderReview:false,saved:null};}
function condition(c,text){
 if(c===null)return null;
 keys(c,['kind','value','quote']);need(['prerequisite','player-claim'].includes(c.kind),'E5_CONDITION_KIND');
 need(typeof c.value==='string'&&c.value.trim()&&c.value.length<=500&&c.value===c.quote&&text.includes(c.value),'E5_CONDITION_LITERAL');
 return clone(c);
}
export function project(adapter,frame,plan){
 if(!plan)return null;
 const p=clone(plan),stale=hash(p.source)!==hash(frame.reference);p.stale=stale;
 p.steps=p.steps.map((s,i)=>{
  const result={...s,preflight:null,status:'waiting',questions:[],diagnostics:[]};
  const missing=['item','quantity',...(s.atomic.action==='gift'?['target']:[])].filter(k=>!value(s,k));
  result.questions=missing.map(k=>({item:'请明确物资。',quantity:'请明确数量。',target:'请明确赠送对象。'}[k]));
  if(stale){result.status='stale';return result;}
  if(!frame.ready){result.status='blocked';result.diagnostics=['来源停用或有未完成回合。'];return result;}
  if(p.orderReview){result.status='order-review';return result;}
  if(missing.length){result.status='clarification';return result;}
  if(s.condition){result.status='condition';result.diagnostics=['条件尚未核实，玩家陈述不等于世界事实。'];return result;}
  if(i>0||s.after!==null){result.status='waiting';return result;}
  const d=derive(adapter,frame,{...s.atomic,source:p.source});
  result.status=d.status;result.preflight=d.preflight;result.questions=d.questions;result.diagnostics=d.diagnostics;
  // Never expose E3 giftEstimate as a chained or executed balance.
  return result;
 });
 p.nextQuestion=p.question??(p.orderReview?'步骤已删除或改序，请确认剩余步骤的顺序与依赖。':p.steps.find(s=>s.questions.length)?.questions[0]??null);
 return p;
}
export function apply(adapter,frame,previous,result,{text,turnId,kind='model-interpretation'}={}){
 bounded(result,{bytes:20000,depth:9});keys(result,['ops','question','limit']);
 need(Array.isArray(result.ops)&&result.ops.length<=3,'E5_OPS');
 for(const k of ['question','limit'])need(result[k]===null||typeof result[k]==='string'&&result[k].trim()&&result[k].length<=500,'E5_MESSAGE');
 need(!result.limit||result.ops.length===0,'E5_PARTIAL_LIMIT');
 const p=clone(previous??empty(frame));need(p.format===FORMAT,'E5_FORMAT');
 p.revision++;p.saved=null;p.question=result.question;p.limit=result.limit;
 for(const op of result.ops){
  need(op&&['add','revise','remove','move','review-order'].includes(op.type),'E5_OP');
  keys(op,op.type==='add'?['type','action','changes','condition','quote']:op.type==='revise'?['type','stepId','action','changes','condition','quote']:op.type==='move'?['type','stepId','to','quote']:op.type==='remove'?['type','stepId','quote']:['type','quote']);
  need(typeof op.quote==='string'&&op.quote.trim()&&text.includes(op.quote),'E5_EVIDENCE');
  if(op.type==='review-order'){
   need(kind==='user-edit','E5_ORDER_CONFIRM');p.orderReview=false;p.steps.forEach((s,i)=>s.after=p.steps[i-1]?.id??null);continue;
  }
  if(op.type==='add'){
   need(p.steps.length<3,'E5_STEP_LIMIT');need(typeof op.action==='string','E5_ACTION');
   const atomic=revise(adapter,frame,null,{action:op.action,changes:op.changes,text,turnId,kind});
   const c=condition(op.condition,text);need(!atomic.intent.condition,'E5_USE_STEP_CONDITION');
   p.steps.push({id:'step-'+p.nextId++,after:p.steps.at(-1)?.id??null,quote:op.quote,origin:{turnId,text},condition:c,atomic:strip(atomic)});
  }else{
   const i=p.steps.findIndex(s=>s.id===op.stepId);need(i>=0,'E5_STEP_UNKNOWN');const s=p.steps[i];
   if(op.type==='remove'){p.steps.splice(i,1);p.orderReview=p.steps.length>0;}
   if(op.type==='move'){
    need(kind==='user-edit'&&Number.isInteger(op.to)&&op.to>=0&&op.to<p.steps.length,'E5_MOVE');
    p.steps.splice(i,1);p.steps.splice(op.to,0,s);p.orderReview=true;
   }
   if(op.type==='revise'){
    const atomic=revise(adapter,frame,s.atomic,{action:op.action,changes:op.changes,text,turnId,kind});
    need(!atomic.intent.condition,'E5_USE_STEP_CONDITION');s.atomic=strip(atomic);
    if(op.condition!==undefined)s.condition=condition(op.condition,text);
    s.lastRevision={turnId,quote:op.quote};
   }
  }
 }
 return p;
}
function strip(d){const {preflight,giftEstimate,questions,diagnostics,worldConditions,status,confirmation,diff,...base}=d;return base;}
export function refresh(frame,previous){need(previous,'E5_PLAN_MISSING');return {...clone(previous),source:clone(frame.reference),revision:previous.revision+1,saved:null};}
export function save(adapter,frame,previous){
 need(previous?.steps.length,'E5_PLAN_MISSING');const view=project(adapter,frame,previous);
 need(!view.stale&&frame.ready&&!previous.orderReview&&!previous.limit&&!previous.question,'E5_REVIEW_REQUIRED');
 need(!view.steps.some(s=>['clarification','blocked','stale','unsupported'].includes(s.status)),'E5_REVIEW_REQUIRED');
 return {...clone(previous),saved:{revision:previous.revision,hash:hash({...previous,saved:null}),meaning:'save-plan-only'}};
}
export function exportPlan(adapter,frame,p){
 need(p?.saved?.revision===p.revision&&hash({...p,saved:null})===p.saved.hash,'E5_NOT_SAVED');
 need(hash(p.source)===hash(frame.reference)&&frame.ready,'E5_SOURCE_STALE');
 return {format:FORMAT,executionStatus:'not_executed',requiresExecutionValidation:true,plan:project(adapter,frame,p),
  notice:'只读计划材料，未验证整条计划可执行性。不是 R5 动作或 R7 调度指令，条件不是世界事实。'};
}

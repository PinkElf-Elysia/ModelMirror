import {clone,hash,need,keys} from '../contracts.mjs';
import {integerDelta} from '../arithmetic.mjs';
export function resourceRule({id,label,entries,accept,path,validateState}){
 return {id,version:'1',label,effect:'consume-registered-resource',evaluate(state,args){
  keys(args,['actionId','itemId','quantity']);need(args.actionId===id,'ACTION_UNSUPPORTED');
  need(typeof args.itemId==='string'&&Number.isSafeInteger(args.quantity)&&args.quantity>0,'ACTION_ARGUMENTS');
  validateState(state);const next=clone(state),e=entries(next).find(e=>e.id===args.itemId);need(e&&accept(e),'ACTION_TARGET');
  const delta=integerDelta(e.quantity,-args.quantity);e.quantity=delta.after;validateState(next);
  return {state:next,changes:[{path:path(e.id),...delta,reason:label,authority:'rule-calculation'}],
   result:{actionId:id,itemId:e.id,name:e.name,quantity:args.quantity,before:delta.before,after:delta.after,label}};
 }};
}
export function prepare(r,rule,args){
 const out=rule.evaluate(r.state,args),p={format:'rpg-action-proposal/5',session:r.id,stateRevision:r.stateRevision,stateHash:hash(r.state),
 bindingHash:hash(r.binding),actor:'player',ruleVersion:rule.version,action:clone(args),expected:out.changes,status:'review',executionStatus:'not_executed'};
 return {...p,hash:hash(p)};
}
export function verifyProposal(r,rule,p){
 need(p?.format==='rpg-action-proposal/5','ACTION_PROPOSAL');const {hash:stamp,...body}=p;
 need(stamp===hash(body)&&p.status==='review'&&p.executionStatus==='not_executed','ACTION_PROPOSAL');
 need(p.session===r.id&&p.actor==='player'&&p.bindingHash===hash(r.binding),'ACTION_SCOPE');
 need(p.stateRevision===r.stateRevision&&p.stateHash===hash(r.state),'ACTION_STALE');need(p.ruleVersion===rule.version,'ACTION_RULE_VERSION');
 const out=rule.evaluate(r.state,p.action);need(hash(out.changes)===hash(p.expected),'ACTION_ESTIMATE_CHANGED');return out;
}

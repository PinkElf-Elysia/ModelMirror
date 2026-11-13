// Native cards share one explicit, host-owned persistent-requirements contract.
// Timeline entries apply to the next turn; replay selects by turn, never today's text.
import {canonical,sha,fail} from '../plugins/catalog.mjs';
const digest=v=>sha(canonical(v)),clone=structuredClone,format='modelmirror.rpg.card-requirements/1';
const exact=(v,k)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===k.slice().sort().join(',');
const integer=v=>Number.isSafeInteger(v)&&v>=0;
const text=v=>typeof v==='string'&&v.isWellFormed()&&[...v].length<=8000;
const seal=v=>({...v,hash:digest(v)});
const snapshot=v=>exact(v,['revision','text'])&&integer(v.revision)&&text(v.text);
export const emptyCardRequirements=(initialText='',history=[])=>seal({format,revision:0,initialText,text:initialText,history:clone(history),operations:{}});
export function validateCardRequirements(q){
 if(!exact(q,['format','revision','initialText','text','history','operations','hash'])||q.format!==format||!integer(q.revision)||q.revision>1000||!text(q.initialText)||!text(q.text)||!Array.isArray(q.history)||q.history.some(v=>!snapshot(v))||!q.operations||typeof q.operations!=='object'||Array.isArray(q.operations))throw fail('REQUIREMENTS_STATE_INVALID');
 const {hash,...body}=q;if(hash!==digest(body)||Object.keys(q.operations).length!==q.revision)throw fail('REQUIREMENTS_STATE_INVALID');
 let revision=0,fromTurn=q.history.length+1,lastText=q.initialText;
 for(const [id,o] of Object.entries(q.operations).sort((a,b)=>a[1].revision-b[1].revision)){
  if(!/^[A-Za-z0-9-]{1,80}$/.test(id)||!exact(o,['status','inputHash','revision','fromTurn','text'])||o.status!=='applied'||!/^[a-f0-9]{64}$/.test(o.inputHash)||o.revision!==++revision||!integer(o.fromTurn)||o.fromTurn<fromTurn||!text(o.text))throw fail('REQUIREMENTS_STATE_INVALID');
  fromTurn=o.fromTurn;lastText=o.text;
 }
 if(lastText!==q.text)throw fail('REQUIREMENTS_STATE_INVALID');return q;
}
export function cardRequirementsAt(s,turn=s.turns.length+1){
 const q=validateCardRequirements(s.requirements);if(!Number.isSafeInteger(turn)||turn<1)throw fail('REQUIREMENTS_TURN_INVALID');
 if(turn<=q.history.length)return clone(q.history[turn-1]);
 const found=Object.values(q.operations).filter(o=>o.fromTurn<=turn).sort((a,b)=>b.revision-a.revision)[0];
 return found?{revision:found.revision,text:found.text}:{revision:0,text:q.initialText};
}
export function forkCardRequirements(s,through){
 const history=Array.from({length:through},(_,i)=>cardRequirementsAt(s,i+1)),last=history.at(-1)??cardRequirementsAt(s,1);
 return emptyCardRequirements(last.text,history);
}
export function appendCardRequirements(s,assembled){
 if(!s.requirements)return assembled;
 const chosen=cardRequirementsAt(s);if(!chosen.text)return assembled;
 const current=assembled.current+'\n\n【持续要求】\n'+chosen.text+'\n【持续要求结束】';
 return {...assembled,current,messages:[...assembled.messages.slice(0,-1),{role:'user',content:current}]};
}
export function requirementsPolicy(s,p){
 if(!s.requirements)return p;
 const requirements=cardRequirementsAt(s);return {...p,requirementsRevision:requirements.revision,revision:digest({baseRevision:p.revision,requirements})};
}
export function withCardRequirements(Base){return class extends Base{
 async requireRuntime(s){await super.requireRuntime(s);const q=validateCardRequirements(s.requirements);if(q.history.length>s.turns.length||Object.values(q.operations).some(o=>o.fromTurn>s.turns.length+1))throw fail('REQUIREMENTS_STATE_INVALID');}
 async requirementsStatus(id){const s=await this.read(id);await this.requireRuntime(s);return {sessionRevision:s.revision,revision:s.requirements.revision,text:s.requirements.text};}
 async requirementsOperation(id,operationId){if(typeof operationId!=='string'||!/^[A-Za-z0-9-]{1,80}$/.test(operationId))throw fail('REQUIREMENTS_INPUT_INVALID',400);const s=await this.read(id);await this.requireRuntime(s);return clone(s.requirements.operations[operationId]??{status:'not-applied'});}
 async configureRequirements(id,value){
  if(!exact(value,['operationId','expectedSessionRevision','expectedRequirementsRevision','text'])||typeof value.operationId!=='string'||!/^[A-Za-z0-9-]{1,80}$/.test(value.operationId)||!integer(value.expectedSessionRevision)||!integer(value.expectedRequirementsRevision)||!text(value.text))throw fail('REQUIREMENTS_INPUT_INVALID',400);
  const i=clone(value);return this.exclusive(id,async()=>{
   const s=await this.read(id);await this.requireRuntime(s);const q=s.requirements,prior=q.operations[i.operationId];
   if(prior){if(prior.inputHash!==digest(i))throw fail('REQUIREMENTS_OPERATION_COLLISION');return clone(prior);}
   await this.requireSelectable(s);if(this.running.has(id))throw fail('SESSION_BUSY');
   if(s.revision!==i.expectedSessionRevision||q.revision!==i.expectedRequirementsRevision)throw fail('REQUIREMENTS_REVISION_CONFLICT');
   if(q.revision>=1000)throw fail('REQUIREMENTS_REVISION_LIMIT');
   if([s.requests,s.modelState.operations,s.generationParameters.operations,s.memoryTasks.operations,s.summaryTasks?.operations].some(v=>v&&Object.hasOwn(v,i.operationId)))throw fail('REQUIREMENTS_OPERATION_COLLISION');
   const operation={status:'applied',inputHash:digest(i),revision:q.revision+1,fromTurn:s.turns.length+1,text:i.text};
   const next=clone(s),{hash,...body}=q;next.requirements=seal({...body,revision:q.revision+1,text:i.text,operations:{...q.operations,[i.operationId]:operation}});next.revision++;
   validateCardRequirements(next.requirements);await this.write(next);return clone(operation);
  });
 }
};}

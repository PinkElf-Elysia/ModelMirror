// Explicit, atomic transfer of a candidate's model and parameters. No dispatch.
import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
const hash=v=>sha256(canonical(v)),integer=v=>Number.isSafeInteger(v)&&v>=0;
export function futureIntent(v){
 keys(v,['sessionId','batchId','operationId','candidateId','expectedRevision','expectedModelRevision','expectedParameterRevision','expectedAuthorizationRevision']);
 if(![v.sessionId,v.operationId,v.candidateId].every(isId)||!/^compare-[a-f0-9]{64}$/.test(v.batchId)||![v.expectedRevision,v.expectedModelRevision].every(integer)||
  !(v.expectedParameterRevision===null||integer(v.expectedParameterRevision))||!isHash(v.expectedAuthorizationRevision))fail('COMPARISON_FUTURE_INPUT_INVALID');
 return structuredClone(v);
}
export const futureIds=i=>({model:'future-model-'+hash(i).slice(0,48),parameter:'future-params-'+hash(i).slice(0,48)});
export function futureReceipt(intent,choice,revision,modelRevision,parameterRevision){
 return {status:'applied',intent:structuredClone(intent),choiceHash:hash(choice),inputHash:hash({intent,choice}),revision,modelRevision,parameterRevision};
}
export function priorFuture(s,i,c){
 const p=(s.futureConfigurations??[]).find(p=>p.intent.operationId===i.operationId);
 if(p&&p.inputHash!==hash({intent:i,choice:c}))fail('COMPARISON_FUTURE_OPERATION_COLLISION');
 return p?structuredClone(p):null;
}
export function validateFutureConfigurations(s){
 if(s.futureConfigurations!==undefined&&!Array.isArray(s.futureConfigurations))fail('COMPARISON_FUTURE_RECORD_INVALID');
 const seen=new Set();let previous=-1;
 for(const p of s.futureConfigurations??[]){
  keys(p,['status','intent','choiceHash','inputHash','revision','modelRevision','parameterRevision']);futureIntent(p.intent);
  if(p.status!=='applied'||p.intent.sessionId!==s.id||!isHash(p.choiceHash)||!isHash(p.inputHash)||!integer(p.revision)||p.revision!==p.intent.expectedRevision+1||p.revision>s.revision||p.revision<=previous||
   !integer(p.modelRevision)||p.modelRevision!==p.intent.expectedModelRevision+1||!(p.intent.expectedParameterRevision===null?p.parameterRevision===null:integer(p.parameterRevision)&&p.parameterRevision===p.intent.expectedParameterRevision+1)||seen.has(p.intent.operationId))fail('COMPARISON_FUTURE_RECORD_INVALID');
  previous=p.revision;seen.add(p.intent.operationId);
  if(s.models){const ids=futureIds(p.intent),model=s.models.changes.find(c=>c.intent.operationId===ids.model),param=s.generationParameters?.changes.find(c=>c.intent.operationId===ids.parameter);
   const choice=s.generationParameters?param?.choice:model?.choice;
   if(!choice||model?.revision!==p.modelRevision||model.intent.expectedRevision!==p.intent.expectedRevision||(s.generationParameters&&(param.revision!==p.parameterRevision||param.intent.expectedRevision!==model.intent.expectedRevision))||hash(choice)!==p.choiceHash||hash({intent:p.intent,choice})!==p.inputHash)fail('COMPARISON_FUTURE_RECORD_INVALID');
  }else if(s.modelState){
   const ids=futureIds(p.intent),model=s.modelState.operations[ids.model],param=s.generationParameters.operations[ids.parameter],choice=param?.choice;
   const pi={operationId:ids.parameter,expectedSessionRevision:p.intent.expectedRevision,expectedParameterRevision:p.intent.expectedParameterRevision,baseHash:model?.selection?.configHash,purpose:'story',selection:choice&&{format:'modelmirror.rpg.parameter-selection/2',catalogRevision:choice.catalogRevision,selectionId:choice.entry.selectionId,selectionRevision:choice.entry.selectionRevision,capabilityRevision:choice.entry.capabilityRevision,profile:choice.parameterProfile}};
   if(!choice||model?.revision!==p.modelRevision||model.hash!==hash(p.intent)||param.revision!==p.parameterRevision||hash(choice)!==p.choiceHash||hash({intent:p.intent,choice})!==p.inputHash||param.inputHash!==hash(pi)||param.configurationHash!==hash({intent:pi,choice}))fail('COMPARISON_FUTURE_RECORD_INVALID');
  }
 }
 if((s.futureConfigurations?.length??0)>1000)fail('COMPARISON_FUTURE_CAPACITY');
}
export function sameFutureChange(s,model,parameter){
 return (s.futureConfigurations??[]).some(p=>{const ids=futureIds(p.intent);return ids.model===model.intent.operationId&&ids.parameter===parameter.intent.operationId&&model.intent.expectedRevision===parameter.intent.expectedRevision;});
}

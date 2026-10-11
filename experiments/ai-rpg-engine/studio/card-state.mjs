import * as summary from './summary-state.mjs';
import * as memory from './memory-task-state.mjs';
import {requireModelState} from './model-runtime.mjs';
import {fail} from '../plugins/catalog.mjs';
// Trusted host dependency bundle, never reconstructed from JSON/session fields.
export function createCardState(modelPolicy){
 if(!modelPolicy||typeof modelPolicy.cardId!=='string'||typeof modelPolicy.validChoice!=='function'||typeof modelPolicy.controlledChoice!=='function')throw fail('CARD_STATE_POLICY_INVALID');
 const validateModel=modelPolicy.validChoice,cardId=modelPolicy.cardId;
 const guard=s=>{
  if(s?.runtime?.descriptor?.cardId!==cardId||(s.cardId!==undefined&&s.cardId!==cardId))throw fail('CARD_STATE_BINDING_INVALID');
 };
 const bind=fn=>(s,...args)=>{guard(s);return fn(s,...args,validateModel);};
 const summaryState=Object.freeze({...summary,
  requireSummaryState:bind(summary.requireSummaryState),modelSummaryVersion:bind(summary.modelSummaryVersion),
  manualSummaryVersion:bind(summary.manualSummaryVersion),withSummaryVersion:bind(summary.withSummaryVersion),
  summaryUpdateSource:bind(summary.summaryUpdateSource),effectiveContextPolicy:bind(summary.effectiveContextPolicy)});
 const memoryState=Object.freeze({...memory,requireMemoryTasks:bind(memory.requireMemoryTasks),
  memoryContextPolicy:bind(memory.memoryContextPolicy),
  memoryPlan:(s,a,kind='send')=>{guard(s);return memory.memoryPlan(s,a,kind,validateModel);}});
 return Object.freeze({cardId,guard,validateModel,models:modelPolicy,summary:summaryState,memory:memoryState,
  requireModelState:bind(requireModelState)});
}

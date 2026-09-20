import {assembleHistoryRequest} from './history-assembly.mjs';
import {completeHistory} from './branch-archive.mjs';
import {requireSummaryState,sealSummary,activeSummary,SUMMARY_WRAPPER} from './summary-state.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
export const summarySnapshot=s=>sealSummary({...requireSummaryState(s),pending:null,operations:{}});
// Only host-owned raw history and a frozen authorization-derived policy enter here.
export function assembleSummaryRequest(s,input,policy){
 completeHistory(s);const snapshot=summarySnapshot(s);
 if(policy.runtimeHash!==s.runtime.hash||policy.summaryRevision!==s.rollingSummary.revision||policy.modelRevision!==s.modelState.revision||!policy.ready)throw fail('SUMMARY_POLICY_NOT_READY');
 const m2=policy.mode==='rolling-summary';
 const original=assembleHistoryRequest(s,input,policy.historyPolicy);
 let messages=original.messages,selectedTurns=original.historyPolicy.selectedTurns;
 if(m2){
  const T=s.turns.length,active=activeSummary(snapshot);
  if((active?.coveredThrough||0)!==Math.max(0,T-1))throw fail('SUMMARY_COVERAGE_GAP');
  const system={...original.messages[0]};
  if(active)system.content+=SUMMARY_WRAPPER.replace('{summary}',()=>active.effectiveText);
  messages=[system,...structuredClone(s.history.slice(-2)),{role:'user',content:original.current}];
  selectedTurns=T?[T]:[];
 }
 const context={format:1,policy:structuredClone(policy),mode:policy.mode,totalTurns:s.turns.length,selectedTurns,selectedRequestIds:selectedTurns.map(n=>s.turns[n-1].requestId),summaryVersionId:m2?snapshot.activeVersionId:null,coveredThrough:m2?(activeSummary(snapshot)?.coveredThrough||0):0,summarySnapshot:snapshot,historyConfig:structuredClone(s.historyWindow.config),sourceHash:sha(canonical(s.history)),requestHash:sha(canonical(messages))};
 return {...original,messages,contextPolicy:{...context,hash:sha(canonical(context))}};
}
// This receipt permits reconstruction without consulting today's grants or summary.
export function requireContextEvidence(s){
 completeHistory(s);
 for(let i=0;i<s.turns.length;i++){
  const t=s.turns[i],c=t.contextPolicy,receipt=s.requests?.[t.requestId]||s.provenance?.requestOrigins?.find(o=>o.requestId===t.requestId)?.record||s.source?.requestOrigins?.find(o=>o.requestId===t.requestId)?.record;
  if(!c||!receipt||receipt.status!=='complete')throw fail('SUMMARY_TURN_BINDING_INVALID');
  const {hash,...body}=c;
  if(hash!==sha(canonical(body))||c.totalTurns!==i||c.sourceHash!==sha(canonical(s.history.slice(0,i*2)))||receipt.assemblyHash!==c.requestHash||canonical(receipt.contextPolicy)!==canonical(c)||canonical(t.historyPolicy?.config)!==canonical(c.historyConfig))throw fail('SUMMARY_TURN_BINDING_INVALID');
  const prefix={...s,history:s.history.slice(0,i*2),turns:s.turns.slice(0,i),rollingSummary:c.summarySnapshot,historyWindow:{...s.historyWindow,config:c.historyConfig},modelState:{...s.modelState,revision:c.policy.modelRevision}};
  const rebuilt=assembleSummaryRequest(prefix,t.input,c.policy);
  if(rebuilt.contextPolicy.hash!==c.hash||rebuilt.current!==s.history[i*2].content)throw fail('SUMMARY_TURN_BINDING_INVALID');
 }
 return true;
}
export async function commitSummaryStory(target,task,result){
 const s=structuredClone(target);
 const story=task.story;
 if(s.turns.some(t=>t.requestId===task.requestId))throw fail('SUMMARY_DUPLICATE_STORY');
 if(story.contextPolicy.sourceHash!==sha(canonical(s.history))||story.contextPolicy.summarySnapshot.stateHash!==summarySnapshot(s).stateHash||story.contextPolicy.requestHash!==task.messagesHash)throw fail('SUMMARY_STORY_SOURCE_CHANGED');
 const model={selectionRevision:s.modelState.revision,selection:structuredClone(task.selection),requestedModel:task.selection.model,actualModel:result.evidence.record.actualModel??null,evidence:structuredClone(result.evidence),mode:s.mode};
 const at=new Date().toISOString();
 s.requests[task.requestId]={status:'complete',selectionRevision:model.selectionRevision,selection:model.selection,assemblyHash:task.messagesHash,contextPolicy:structuredClone(story.contextPolicy),evidence:structuredClone(result.evidence),at};
 s.history.push({role:'user',content:story.current},{role:'assistant',content:result.raw});
 s.turns.push({requestId:task.requestId,input:story.input,raw:result.raw,rawHash:sha(result.raw),at,model,historyPolicy:{config:structuredClone(story.contextPolicy.historyConfig)},contextPolicy:structuredClone(story.contextPolicy)});s.revision++;
 requireContextEvidence(s);for(const key of ['history','turns','requests','revision'])target[key]=s[key];
}

import {canonical, keys, isHash, sha256, fail, LIMITS} from './contracts.mjs';
import {selectHistory, validConfig} from '../plugins/history-window.mjs';
import {validateAuthoringSummary, summaryUpdateSource, summaryBindingHash, summaryRawHash, requireSummaryAuthorization, SUMMARY_WRAPPER} from './summary-state.mjs';

export const SUMMARY_CONTEXT_FORMAT = 'modelmirror.rpg.authored-summary-context/1';
const digest=value=>sha256(canonical(value));
const same=(a,b)=>canonical(a)===canonical(b);
const clone=value=>structuredClone(value);
function grants(value) {
  keys(value,['history','summary','memory']);
  Object.values(value).forEach(requireSummaryAuthorization);
}
export function summaryContextPolicy(session,state,{authorization,historyConfig}) {
  validateAuthoringSummary(state,session);grants(authorization);
  if(!validConfig(historyConfig))fail('HISTORY_CONFIGURATION_INVALID');
  const source=summaryUpdateSource(session,state),m2=authorization.summary.enabled,version=state.versions.at(-1);
  const reason=authorization.memory.enabled?'AUTHORING_MEMORY_EXECUTION_UNAVAILABLE':
    m2&&!session.models.current.summary?'SUMMARY_MODEL_REQUIRED':m2&&source.needsUpdate?'SUMMARY_UPDATE_REQUIRED':null;
  const body={format:SUMMARY_CONTEXT_FORMAT,sessionId:session.id,bindingHash:summaryBindingHash(session),summaryRevision:state.revision,stateHash:state.sha256,
    authorization:clone(authorization),historyConfig:clone(historyConfig),summaryModelHash:session.models.current.summary?.sha256??null,
    mode:m2?'rolling-summary':authorization.history.enabled?'history-window':'full-history',
    activeVersionId:m2?state.activeVersionId:null,coveredThrough:m2?(version?.coveredThrough??0):null,
    canAssemble:reason===null,reason,runtimeReady:false,billedCalls:0};
  return {...body,revision:digest(body)};
}
export function assembleSummaryContext({session,state,authorization,historyConfig,base,expectedPolicyRevision}) {
  canonical(base);keys(base,['system','user']);
  if(![base.system,base.user].every(v=>typeof v==='string'&&v.isWellFormed()))fail('SUMMARY_BASE_INVALID');
  const policy=summaryContextPolicy(session,state,{authorization,historyConfig});
  if(!isHash(expectedPolicyRevision)||expectedPolicyRevision!==policy.revision)fail('SUMMARY_POLICY_CONFLICT');
  if(!policy.canAssemble)fail(policy.reason);
  if(!Array.isArray(session.examples)||session.examples.length>200||session.examples.length%2)fail('SUMMARY_EXAMPLES_INVALID');
  session.examples.forEach((m,i)=>{keys(m,['role','content']);if(m.role!==(i%2?'assistant':'user')||typeof m.content!=='string'||!m.content.isWellFormed())fail('SUMMARY_EXAMPLES_INVALID');});
  const total=session.turns.length,m2=policy.mode==='rolling-summary',version=state.versions.at(-1);
  let system=base.system,history,selection;
  if(m2) {
    if(version)system+=SUMMARY_WRAPPER.replace('{summary}',()=>version.effectiveText);
    history=session.turns.slice(-1).flatMap(t=>[{role:'user',content:t.userMessage},{role:'assistant',content:t.assistant}]);
    selection={totalTurns:total,selectedTurns:total?[total]:[],initialSnapshotAdded:false};
  } else {
    const result=selectHistory({history:session.turns.flatMap(t=>[{role:'user',content:t.userMessage},{role:'assistant',content:t.assistant}]),
      characterText:session.binding.openingText,enabled:authorization.history.enabled,config:historyConfig});
    history=result.history;selection=result.record;
  }
  const messages=[{role:'system',content:system},...clone(session.examples),...history,{role:'user',content:base.user}];
  if(messages.reduce((n,m)=>n+[...m.content].length,0)>2000000)fail('SUMMARY_CONTEXT_CAPACITY');
  const body={format:SUMMARY_CONTEXT_FORMAT,sessionId:session.id,bindingHash:summaryBindingHash(session),sessionRevision:session.revision,
    completedTurns:total,sourceHash:summaryRawHash(session.turns),models:clone(session.models),summarySnapshot:clone(state),policy,selection,
    selectedRequestIds:selection.selectedTurns.map(n=>session.turns[n-1].requestId),baseHash:digest(base),messagesHash:digest(messages)};
  const evidence={...body,sha256:digest(body)};
  if(Buffer.byteLength(canonical(evidence))>LIMITS.recordBytes)fail('SUMMARY_CONTEXT_CAPACITY');
  return {messages,evidence};
}
// Reconstruct an earlier request using its immutable summary/model/grant snapshot.
// Today's grants, new summary edits and later turns cannot rewrite that evidence.
export function replaySummaryContext(session,base,evidence) {
  canonical(evidence);
  keys(evidence,['format','sessionId','bindingHash','sessionRevision','completedTurns','sourceHash','models','summarySnapshot','policy','selection','selectedRequestIds','baseHash','messagesHash','sha256']);
  const {sha256:hash,...body}=evidence;
  if(evidence.format!==SUMMARY_CONTEXT_FORMAT||hash!==digest(body)||evidence.sessionId!==session.id||evidence.bindingHash!==summaryBindingHash(session)||
    !Number.isSafeInteger(evidence.completedTurns)||evidence.completedTurns<0||evidence.completedTurns>session.turns.length||
    evidence.baseHash!==digest(base))fail('SUMMARY_CONTEXT_EVIDENCE_INVALID');
  const prior={...clone(session),revision:evidence.sessionRevision,models:clone(evidence.models),turns:clone(session.turns.slice(0,evidence.completedTurns))};
  const rebuilt=assembleSummaryContext({session:prior,state:evidence.summarySnapshot,authorization:evidence.policy.authorization,
    historyConfig:evidence.policy.historyConfig,base,expectedPolicyRevision:evidence.policy.revision});
  if(!same(rebuilt.evidence,evidence))fail('SUMMARY_CONTEXT_EVIDENCE_INVALID');
  return rebuilt.messages;
}

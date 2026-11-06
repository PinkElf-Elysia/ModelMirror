import {largeMessages,pendingBatch,smallMessages,summaryContext} from '../plugins/auto-summary-v2.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
import {activeSummary} from './summary-state.mjs';

const seal=body=>({...body,hash:sha(canonical(body))});
export const validSummaryText=text=>typeof text==='string'&&!!text.trim()&&Array.from(text).length<=10000;

export function summaryV2State(s){
 const saved=s.autoSummaryV2;
 if(!saved){
  const old=activeSummary(s.rollingSummary),inherited=old?.effectiveText||'',through=old?.coveredThrough||0;
  return seal({inherited,through,large:inherited,covered:through,turns:[]});
 }
 const {hash,...body}=saved;
 if(hash!==sha(canonical(body))||![saved.through,saved.covered].every(n=>Number.isSafeInteger(n)&&n>=0&&n<=s.turns.length)||
    saved.through>saved.covered||![saved.inherited,saved.large].every(t=>t===''||validSummaryText(t))||
    !Array.isArray(saved.turns)||saved.turns.some((t,i)=>!Number.isSafeInteger(t.id)||t.id<=saved.through||t.id<=(saved.turns[i-1]?.id||0)||
     s.turns[t.id-1]?.requestId!==t.requestId||!validSummaryText(t.small)))throw fail('SUMMARY_V2_STATE_INVALID');
 return saved;
}

export function summaryV2Policy(s,policy){
 const state=summaryV2State(s);
 const baseRevision=sha(canonical({version:2,policy:policy.baseRevision,state:state.hash}));
 const blocked=['SUMMARY_OPERATION_UNCONFIRMED','SUMMARY_UNCONFIRMED_REQUEST'].includes(policy.reason);
 return {...policy,summaryVersion:2,baseRevision,revision:sha(canonical({baseRevision,memory:policy.revision})),
  activeVersionId:null,coveredThrough:state.covered,needsUpdate:pendingBatch(state).length===20,ready:!blocked,reason:blocked?policy.reason:null};
}

export function summaryV2Story(s,original){
 const state=summaryV2State(s),summarized=new Set(state.turns.map(t=>t.id));
 const selectedTurns=original.historyPolicy.selectedTurns.filter(turn=>turn>state.through&&!summarized.has(turn));
 const {messages}=original,rawOffset=messages.length-1-original.historyPolicy.selectedTurns.length*2;
 const raw=original.historyPolicy.selectedTurns.flatMap((turn,i)=>selectedTurns.includes(turn)?messages.slice(rawOffset+2*i,rawOffset+2*i+2):[]);
 const opening=s.characterText&&s.turns.length&&!selectedTurns.includes(1)?'\n\n【开局档案 · 资料】\n'+JSON.stringify(s.characterText):'';
 const system={...messages[0],content:messages[0].content+opening+summaryContext(state)};
 return {messages:[system,...messages.slice(1,rawOffset),...raw,messages.at(-1)],selectedTurns,state};
}

export const isV2SummaryStage=stageId=>stageId.startsWith('round-summary-');
export function summaryV2Plan(s,a,kind,turn){
 const plan=[],add=(stageId,purpose)=>plan.push({stageId,purpose});
 if(kind==='regenerate')add(turn?'round-summary-small':'round-summary-large','summary');
 if(kind==='send'){
  if(pendingBatch(summaryV2State(s)).length===20)add('round-summary-merge','summary');
  add('story','story');add('round-summary-small','summary');
 }
 if(a.memory.enabled&&(kind==='send'||kind==='update'&&s.memoryPalace.processedThrough<s.turns.length))add('memory-after','memory');
 return plan;
}

export function summaryV2Source(s,stageId,turn){
 const state=summaryV2State(s);
 if(stageId==='round-summary-small'){
  const id=turn??s.turns.length,source=s.turns[id-1];
  if(!source||id<=state.through||(turn&&!summaryV2Recent(s,state).some(t=>t.id===turn)))throw fail('SUMMARY_V2_TURN_UNAVAILABLE');
  return {binding:{hash:state.hash,id,requestId:source.requestId},messages:smallMessages(source)};
 }
 const covered=state.turns.filter(t=>t.id<=state.covered),pending=pendingBatch(state);
 const merge=stageId==='round-summary-merge'||!covered.length;
 const batch=merge?pending:covered;
 if(merge?batch.length!==20:batch.length===0)throw fail('SUMMARY_V2_LARGE_UNAVAILABLE');
 return {binding:{hash:state.hash,covered:batch.at(-1).id},messages:largeMessages(merge?state.large:state.inherited,batch)};
}

function replaceText(state,turn,text){
 const {hash,...body}=state;
 if(!turn)return seal({...body,large:text});
 return seal({...body,turns:body.turns.map(t=>t.id===turn?{...t,small:text}:t)});
}

export function reviseSummaryV2(s,turn,text){
 const state=summaryV2State(s);
 if(!validSummaryText(text)||!(turn?summaryV2Recent(s,state).some(t=>t.id===turn&&t.small):state.large))throw fail('SUMMARY_V2_EDIT_INVALID',400);
 return replaceText(state,turn,text);
}

export function publishSummaryV2(s,task,result,turn){
 const {binding}=summaryV2Source(s,task.stageId,turn);
 if(canonical(binding)!==canonical(task.source)||result.finishReason!=='stop'||!validSummaryText(result.raw))throw fail('SUMMARY_V2_OUTPUT_REJECTED');
 const {hash,...state}=summaryV2State(s);
 if(binding.id){
  const turns=state.turns.filter(t=>t.id!==binding.id);
  turns.push({id:binding.id,requestId:binding.requestId,small:result.raw});
  s.autoSummaryV2=seal({...state,turns:turns.sort((a,b)=>a.id-b.id)});
 }else s.autoSummaryV2=seal({...state,large:result.raw,covered:binding.covered});
 s.revision++;
}

function summaryV2Recent(s,state){
 const rows=state.turns.map(({id,small})=>({id,small}));
 const operations=s.memoryTasks?.operations||{};
 const blocked=operations[s.memoryTasks?.blocked];
 const failedSmall=blocked?.tasks?.find(t=>t.stageId==='round-summary-small'&&t.status!=='complete');
 const missing=failedSmall?.source?.id??(blocked?.blockedPurpose==='summary'&&blocked.tasks.some(t=>t.stageId==='story'&&t.status==='complete')?s.turns.length:0);
 if(missing>state.through&&!rows.some(t=>t.id===missing))rows.push({id:missing,small:''});
 return rows.sort((a,b)=>a.id-b.id).slice(-5);
}

export function projectSummaryV2(s){
 const state=summaryV2State(s);
 return {large:state.large,covered:state.covered,turns:summaryV2Recent(s,state),
  canRegenerateLarge:state.turns.some(t=>t.id<=state.covered)||pendingBatch(state).length===20};
}
